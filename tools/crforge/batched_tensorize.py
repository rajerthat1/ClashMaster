"""C3-B gate: the facts-only tensorize against the per-lane tensorizer.

``crforge_bridge/vector_rows.py`` builds every V4 row section for all lanes from
the vectorized facts; ``crforge_bridge/vector_tensorize.py`` assembles them into
one ``UniversalSemanticBatchV4`` with the lane count as the batch dimension and
carries the previous-action record across decisions.  This tool is the end-to-end
gate the earlier section gates could not run:

* it steps real 256-lane learner-deck battles (WAIT warmup past the start
  cooldown, then sampled play so candidates and played entities exist);
* for every checked lane it drives the stock
  ``UniversalObservationTensorizerV4.tensorize`` for each requested actor seat
  (``--seats 0 1`` gates both owners) with a ``reference_observation`` projected
  from the same facts, records the same sampled micro-action on both paths, and
  compares the **whole record** field by field plus
  ``batch_digest``-for-``batch_digest``;
* it times the stacked build, the stock per-lane build and the observation
  facts layer paired on the same final frame, so the ≤0.5 ms/lane claim includes
  the facts layer rather than only the row build.  With two seats one lane is
  two lane-decisions (one per actor), and the combined cost is reported both
  per seat and per lane-decision.

    .venv/bin/python tools/crforge/batched_tensorize.py \
        --count 256 --steps 40 --verify-lanes 32 --seats 0 1 \
        --output runs/crforge-c3/batched-tensorize.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / ".resource" / "crforge" / "python"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import numpy as np
import torch

from crforge_bridge.catalog import Catalog
from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
from crforge_bridge.jvm import start as start_jvm
from crforge_bridge.vector_observation import VectorObservationEncoder
from crforge_bridge.vector_rows import reference_action_mask, reference_scalar_observation
from crforge_bridge.vector_tensorize import (
    VectorTensorizer,
    batch_digest,
    compare_semantic_batches,
    lane_batch,
    lane_sequence,
    tensorize_seats,
)
from native_runner.timeline import load_game_mode_timeline
from native_runner.training.v4.config import ModelConfigV4
from native_runner.training.v4.factory import deck_roles_from_form_availability, production_semantic_bundle
from native_runner.training.v4.policy_session import config_from_contract
from native_runner.training.v4.tensorizer import UniversalObservationTensorizerV4
from native_runner.training.v4.tensors import GATE_ACT, ActionSequenceV4

LEARNER_DECK = (27000008, 27000006, 26000001, 26000000, 26000010, 26000084, 28000000, 28000011)
LEARNER_FORMS = (0, 1, 1, 2, 0, 0, 0, 0)
LEARNER_NAMES = ("Xbow", "Tesla", "Archer", "Knight", "Skeletons", "ElectroSpirit", "Fireball", "Log")
TOWER_TROOP = 159000000
RELEASE = "16.635.1"
STANDARD_GAME_MODE = 72_000_006  # ``BattleEnvV1.STANDARD_GAME_MODE``
# The shipped C3 profile's whole tensorize + record_action packet.
C3_PACKET_MS = 0.992
# C1's end-to-end target: ≤0.5 ms/lane for facts + row build.
GATE_MS_PER_LANE = 0.5


def _checkpoint_config(checkpoint: Path) -> ModelConfigV4:
    """The checkpoint's own model config (21 card columns for B-general-v275)."""

    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    contract = payload.get("contract") if isinstance(payload, dict) else None
    if contract is None:
        raise ValueError(f"{checkpoint} has no V4 contract")
    return config_from_contract(contract)


def _tensorizer(config: ModelConfigV4, seat: int = 0) -> UniversalObservationTensorizerV4:
    bundle = production_semantic_bundle(RELEASE)
    return UniversalObservationTensorizerV4(
        actor_owner=int(seat),
        card_catalog=bundle.card_catalog,
        ability_catalog=bundle.ability_catalog,
        entity_archetype_catalog=bundle.entity_archetype_catalog,
        effect_catalog=bundle.effect_catalog,
        card_specs=bundle.card_specs,
        deck=LEARNER_DECK,
        tracker=None,
        deck_roles=deck_roles_from_form_availability(LEARNER_DECK, LEARNER_FORMS),
        config=config,
        reject_unknown_public_semantics=True,
    )


def _vector(count: int) -> Any:
    from crforge_env.vector import LadderConfig, SideSetup, VectorBattle

    setup = SideSetup(towers="King_PrincessTowers", king_level=16, tower_level=16)
    vector = VectorBattle(
        count,
        1,
        install=str(DEFAULT_INSTALL),
        tables=str(DEFAULT_TABLES),
        config=LadderConfig(decision_ticks=5, level=16, tower_level=16),
    )
    vector.reset(
        list(range(1, count + 1)),
        list(LEARNER_NAMES),
        list(LEARNER_NAMES),
        list(LEARNER_FORMS),
        list(LEARNER_FORMS),
        setup0=setup,
        setup1=setup,
    )
    return vector


def _step(vector: Any, rng: np.random.Generator, play_rate: float) -> None:
    actions = np.zeros((vector.count, 2), dtype=np.int32)
    first_ability = int(vector.schema.first_ability)
    for lane in range(vector.count):
        for seat in (0, 1):
            if rng.random() >= play_rate:
                continue
            legal = np.flatnonzero(vector.masks[lane, seat, 1:first_ability])
            if legal.size:
                actions[lane, seat] = int(legal[rng.integers(legal.size)]) + 1
    vector.step(actions)


def _timed(callable_: Any, repeats: int) -> float:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        callable_()
        samples.append((time.perf_counter() - started) * 1_000.0)
    return statistics.median(samples)


def _action_sequence(candidates: Any, config: ModelConfigV4) -> ActionSequenceV4:
    """One sampled micro-action per lane: each lane's first legal candidate.

    A gate that never plays would leave ``previous_action`` empty on both paths
    and prove less than the digest suggests, so this records a real candidate.
    """

    lanes = int(candidates.mask.shape[0])
    steps = int(config.max_micro_actions)
    gate = torch.zeros(lanes, dtype=torch.long)
    count = torch.zeros(lanes, dtype=torch.long)
    index = torch.full((lanes, steps), -1, dtype=torch.long)
    uid = torch.full((lanes, steps), -1, dtype=torch.long)
    target = torch.full((lanes, steps), -1, dtype=torch.long)
    delay = torch.full((lanes, steps), -1, dtype=torch.long)
    mask = candidates.mask.numpy()
    placement = candidates.placement.numpy()
    for lane in range(lanes):
        rows = np.flatnonzero(mask[lane])
        if rows.size == 0:
            continue
        row = int(rows[0])
        gate[lane] = GATE_ACT
        count[lane] = 1
        index[lane, 0] = row
        uid[lane, 0] = candidates.uid[lane, row]
        cells = np.argwhere(placement[lane, row])
        if cells.size:
            target[lane, 0] = int(cells[0, 0]) * int(config.board_width) + int(cells[0, 1])
        delay[lane, 0] = 0
    return ActionSequenceV4(
        gate=gate,
        micro_action_count=count,
        candidate_index=index,
        candidate_uid=uid,
        target_cell=target,
        delay_offset_bin=delay,
    )


def _reference(
    stock: Any, tables: Any, timeline: Any, batch: Any, lane: int, episode_id: str, *, seat: int = 0
) -> Any:
    """The per-lane oracle observation for one actor seat.

    ``model_frame=False`` leaves the native placement grid intact: the stock
    ``tensorize`` applies the actor perspective itself, so a pre-flipped grid
    would be transformed twice.
    """

    return reference_scalar_observation(
        batch,
        lane,
        tables=tables,
        tensorizer=stock,
        timeline=timeline,
        tower_troop_id=TOWER_TROOP,
        seat=int(seat),
        action_mask=reference_action_mask(batch, lane, tensorizer=stock, seat=int(seat), model_frame=False),
        episode_id=episode_id,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=256)
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--timing-repeats", type=int, default=3)
    parser.add_argument("--timing-samples", type=int, default=8)
    parser.add_argument("--verify-lanes", type=int, default=32, help="lanes compared every step after step 0")
    parser.add_argument(
        "--warmup-steps",
        type=int,
        default=60,
        help="WAIT-only decisions before the loop, past the start cooldown",
    )
    parser.add_argument("--play-rate", type=float, default=0.5)
    parser.add_argument(
        "--seats",
        type=int,
        nargs="+",
        default=[0],
        choices=(0, 1),
        help="actor seats to tensorize (0 1 gates both owners of every lane)",
    )
    parser.add_argument(
        "--no-pair",
        action="store_true",
        help="tensorize each seat separately instead of the producer's two-seat group pass",
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--checkpoint", type=Path, default=Path(".resource/RoyaleFish/models/B-general-v275.pt"))
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c3/batched-tensorize.json"))
    args = parser.parse_args(argv)

    start_jvm(str(DEFAULT_INSTALL))
    from crforge_env import _jvm as env_jvm

    env_jvm._started_with = Path(DEFAULT_INSTALL).expanduser().resolve()
    catalog = Catalog.load()
    config = _checkpoint_config(args.checkpoint)
    seats = tuple(dict.fromkeys(int(seat) for seat in args.seats))
    if not seats or any(seat not in (0, 1) for seat in seats):
        raise SystemExit(f"--seats must be a subset of 0 1, got {args.seats}")
    tensorizers = {seat: _tensorizer(config, seat) for seat in seats}
    timeline = load_game_mode_timeline(STANDARD_GAME_MODE).timeline
    vector = _vector(args.count)
    encoder = VectorObservationEncoder(vector, catalog)
    encoder.reset()
    batch = encoder.encode()
    rows_by_seat = {
        seat: VectorTensorizer(
            tensorizers[seat],
            vocabulary=list(vector.vocabulary),
            bridge_catalog=catalog,
            timeline=timeline,
            tower_troop_id=TOWER_TROOP,
        )
        for seat in seats
    }
    for rows in rows_by_seat.values():
        rows.reset(batch)
    # Two seats go through the producer path by default: one group build for
    # both perspectives.  ``--no-pair`` keeps the per-seat path measurable.
    pair = len(seats) == 2 and not args.no_pair

    def tensorize_batch(*, validate: bool = False) -> dict[int, Any]:
        if pair:
            return tensorize_seats(rows_by_seat, batch, validate=validate)
        return {seat: rows_by_seat[seat].tensorize(batch, validate=validate) for seat in seats}

    rng = np.random.default_rng(args.seed)
    for _ in range(args.warmup_steps):
        _step(vector, rng, 0.0)
    encoder.encode(batch)

    mismatches: list[str] = []
    stock_by_lane: dict[tuple[int, int], Any] = {}
    stock_batches: dict[tuple[int, int], Any] = {}
    # Lanes that are checked on every step need their own previous-action
    # history.  Step 0 checks every lane against an empty previous action, so a
    # single instance can serve the one-shot lanes after ``start_episode``.
    persistent_lanes = set(range(min(args.verify_lanes, args.count)))
    cold_stock = {seat: _tensorizer(config, seat) for seat in seats}
    digest_matches_by_seat = {seat: 0 for seat in seats}
    lane_decisions_by_seat = {seat: 0 for seat in seats}
    candidates_by_seat = {seat: 0 for seat in seats}
    max_candidates_by_seat = {seat: 0 for seat in seats}
    max_relations_by_seat = {seat: 0 for seat in seats}
    max_entities_by_seat = {seat: 0 for seat in seats}
    digest_matches = 0
    lane_decisions = 0
    steps_run = 0
    decisions_with_candidates = 0
    micro_actions_recorded = 0
    max_candidates = 0
    max_relations = 0
    max_entities = 0
    try:
        for step in range(args.steps):
            lanes = list(range(args.count)) if step == 0 else list(range(min(args.verify_lanes, args.count)))
            stacked_by_seat = tensorize_batch(validate=(step == 0))
            for seat in seats:
                rows = rows_by_seat[seat]
                stacked = stacked_by_seat[seat]
                if tuple(stacked.match_scalars.shape) != (args.count, int(config.generic_scalar_dim)):
                    mismatches.append(f"step={step} seat={seat} stacked shape")
                for lane in lanes:
                    key = (seat, lane)
                    stock = stock_by_lane.get(key)
                    if stock is None and lane in persistent_lanes:
                        stock = _tensorizer(config, seat)
                        stock_by_lane[key] = stock
                        stock.start_episode(
                            _reference(stock, rows.tables, timeline, batch, lane, f"lane{lane}", seat=seat)
                        )
                    if stock is None:
                        stock = cold_stock[seat]
                        stock.start_episode(
                            _reference(stock, rows.tables, timeline, batch, lane, f"lane{lane}", seat=seat)
                        )
                    view = _reference(stock, rows.tables, timeline, batch, lane, f"lane{lane}", seat=seat)
                    expected = stock.tensorize(view, validate=False)
                    if lane in persistent_lanes:
                        stock_batches[key] = expected
                    actual = lane_batch(
                        stacked,
                        lane,
                        group_capacity=int(config.max_battle_groups),
                        child_capacity=int(config.max_children_total),
                    )
                    found = compare_semantic_batches(actual, expected)
                    if found:
                        mismatches.extend(f"step={step} seat={seat} lane={lane} {value}" for value in found)
                    elif batch_digest(actual) == batch_digest(expected):
                        digest_matches += 1
                        digest_matches_by_seat[seat] += 1
                    else:
                        mismatches.append(f"step={step} seat={seat} lane={lane} digest")
                    lane_decisions += 1
                    lane_decisions_by_seat[seat] += 1
                    lane_candidates = int(actual.candidates.mask.sum())
                    lane_relations = int(actual.relation_edges.mask.sum())
                    lane_entities = int(actual.groups.mask.sum())
                    max_candidates = max(max_candidates, lane_candidates)
                    max_relations = max(max_relations, lane_relations)
                    max_entities = max(max_entities, lane_entities)
                    max_candidates_by_seat[seat] = max(max_candidates_by_seat[seat], lane_candidates)
                    max_relations_by_seat[seat] = max(max_relations_by_seat[seat], lane_relations)
                    max_entities_by_seat[seat] = max(max_entities_by_seat[seat], lane_entities)
                    if lane_candidates > 0:
                        decisions_with_candidates += 1
                        candidates_by_seat[seat] += 1
            if mismatches:
                steps_run = step
                break

            for seat in seats:
                sequence = _action_sequence(stacked_by_seat[seat].candidates, config)
                rows_by_seat[seat].record_action(sequence, stacked_by_seat[seat], validate=True)
                for lane in persistent_lanes:
                    if lane in lanes:
                        stock_by_lane[(seat, lane)].record_action(
                            lane_sequence(sequence, lane),
                            stock_batches[(seat, lane)],
                            row=0,
                            validate=True,
                        )
                micro_actions_recorded += int(sequence.micro_action_count.sum())

            steps_run = step + 1
            _step(vector, rng, args.play_rate)
            encoder.encode(batch)

        # ---- paired timing on the final frame ------------------------------
        timing_stock = {seat: _tensorizer(config, seat) for seat in seats}
        references = {
            seat: [
                _reference(timing_stock[seat], rows_by_seat[seat].tables, timeline, batch, lane, "timing", seat=seat)
                for lane in range(args.count)
            ]
            for seat in seats
        }
        for seat in seats:
            timing_stock[seat].start_episode(references[seat][0])
        stacked_by_seat = tensorize_batch()
        sequences = {seat: _action_sequence(stacked_by_seat[seat].candidates, config) for seat in seats}

        def facts() -> None:
            encoder.encode(batch)

        def batched() -> None:
            if pair:
                tensorize_seats(rows_by_seat, batch)
                return
            for seat in seats:
                rows_by_seat[seat].tensorize(batch)

        def oracle() -> None:
            for seat in seats:
                stock = timing_stock[seat]
                for view in references[seat]:
                    stock.tensorize(view, validate=False)

        def record() -> None:
            for seat in seats:
                rows_by_seat[seat].record_action(sequences[seat], stacked_by_seat[seat], validate=False)

        def _seat_batched(seat: int) -> None:
            rows_by_seat[seat].tensorize(batch)

        def _seat_oracle(seat: int) -> None:
            stock = timing_stock[seat]
            for view in references[seat]:
                stock.tensorize(view, validate=False)

        def _seat_record(seat: int) -> None:
            rows_by_seat[seat].record_action(sequences[seat], stacked_by_seat[seat], validate=False)

        facts_median = statistics.median([_timed(facts, args.timing_repeats) for _ in range(args.timing_samples)])
        per_seat_batched = {
            seat: statistics.median(
                [_timed(lambda seat=seat: _seat_batched(seat), args.timing_repeats) for _ in range(args.timing_samples)]
            )
            for seat in seats
        }
        per_seat_oracle = {
            seat: statistics.median(
                [_timed(lambda seat=seat: _seat_oracle(seat), args.timing_repeats) for _ in range(args.timing_samples)]
            )
            for seat in seats
        }
        per_seat_record = {
            seat: statistics.median(
                [_timed(lambda seat=seat: _seat_record(seat), args.timing_repeats) for _ in range(args.timing_samples)]
            )
            for seat in seats
        }
        batched_median = statistics.median(
            [_timed(batched, args.timing_repeats) for _ in range(args.timing_samples)]
        )
        oracle_median = statistics.median([_timed(oracle, args.timing_repeats) for _ in range(args.timing_samples)])
        record_median = statistics.median([_timed(record, args.timing_repeats) for _ in range(args.timing_samples)])
        # One decision per actor per lane: a two-seat run is ``count * seats``
        # lane-decisions, so the per-lane cost stays comparable to the
        # single-seat C3-B artifact.
        lane_decisions_timed = args.count * len(seats)
        batched_per_lane = batched_median / lane_decisions_timed
        facts_per_lane = facts_median / args.count
        record_per_lane = record_median / lane_decisions_timed
        end_to_end_per_lane = (facts_median + batched_median + record_median) / lane_decisions_timed

        artifact = {
            "schema": "crforge-batched-tensorize.v2",
            "measured": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "shape": {
                "count": args.count,
                "steps": args.steps,
                "warmup_steps": args.warmup_steps,
                "verify_lanes_step0": args.count,
                "verify_lanes_after": args.verify_lanes,
                "play_rate": args.play_rate,
                "seed": args.seed,
                "seats": list(seats),
                "group_pair": pair,
            },
            "substrate": {
                "release": RELEASE,
                "tables": str(DEFAULT_TABLES),
                "install": str(DEFAULT_INSTALL),
                "checkpoint": str(args.checkpoint),
                "card_runtime_feature_dim": int(config.card_runtime_feature_dim),
                "timeline": timeline.name,
                "game_mode": STANDARD_GAME_MODE,
                "tower_troop": TOWER_TROOP,
                "scopes": (
                    "both actor seats (game-frame facts, per-seat model perspective); no tracker, events, "
                    "reveals, effects, ability runtime or tower status"
                ),
            },
            "equivalence": {
                "steps": steps_run,
                "lanes_checked_first_step": args.count,
                "lanes_checked_after": args.verify_lanes,
                "lane_decisions": lane_decisions,
                "digest_matches": digest_matches,
                "decisions_with_candidates": decisions_with_candidates,
                "micro_actions_recorded": micro_actions_recorded,
                "mismatches": mismatches[:40],
                "pass": not mismatches,
                "by_seat": {
                    str(seat): {
                        "lane_decisions": lane_decisions_by_seat[seat],
                        "digest_matches": digest_matches_by_seat[seat],
                        "decisions_with_candidates": candidates_by_seat[seat],
                        "max_candidates_per_lane": max_candidates_by_seat[seat],
                        "max_relation_edges_per_lane": max_relations_by_seat[seat],
                        "max_active_groups_per_lane": max_entities_by_seat[seat],
                    }
                    for seat in seats
                },
            },
            "counts": {
                "max_candidates_per_lane": max_candidates,
                "max_relation_edges_per_lane": max_relations,
                "max_active_groups_per_lane": max_entities,
            },
            "timing": {
                "lanes": args.count,
                "seats": list(seats),
                "lane_decisions": lane_decisions_timed,
                "facts_ms": facts_median,
                "facts_ms_per_lane": facts_per_lane,
                "batched_ms": batched_median,
                "batched_ms_per_lane": batched_per_lane,
                "oracle_ms": oracle_median,
                "oracle_ms_per_lane": oracle_median / lane_decisions_timed,
                "record_actions_ms": record_median,
                "record_actions_ms_per_lane": record_per_lane,
                "speedup": (oracle_median / batched_median) if batched_median else 0.0,
                "end_to_end_ms_per_lane": end_to_end_per_lane,
                "c3_packet_ms": C3_PACKET_MS,
                "gate_ms_per_lane": GATE_MS_PER_LANE,
                "gate_pass": end_to_end_per_lane <= GATE_MS_PER_LANE,
                "by_seat": {
                    str(seat): {
                        "batched_ms": per_seat_batched[seat],
                        "batched_ms_per_lane": per_seat_batched[seat] / args.count,
                        "oracle_ms": per_seat_oracle[seat],
                        "oracle_ms_per_lane": per_seat_oracle[seat] / args.count,
                        "record_actions_ms": per_seat_record[seat],
                        "record_actions_ms_per_lane": per_seat_record[seat] / args.count,
                        "speedup": (
                            per_seat_oracle[seat] / per_seat_batched[seat] if per_seat_batched[seat] else 0.0
                        ),
                    }
                    for seat in seats
                },
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "pass": artifact["equivalence"]["pass"],
                    "steps": steps_run,
                    "digest_matches": digest_matches,
                    "counts": artifact["counts"],
                    "timing": artifact["timing"],
                    "output": str(args.output),
                },
                indent=2,
            )
        )
    finally:
        vector.close()
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
