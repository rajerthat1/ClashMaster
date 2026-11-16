"""C4 gate: the batched engine action decode against the per-lane decoder.

`crforge_bridge/vector_actions.py` turns a stacked ``ActionSequenceV4`` into the
flat action index a ``VectorBattle`` steps: ``0`` waits, ``1 + slot*rows*cols +
row*cols + col`` plays a hand slot on a seat tile.  The stock per-lane path
(`decode_action_sequence_v4`) emits ``ActionV1`` bodies instead, so this tool is
the equivalence gate between them, on the same facts:

* it steps real learner-deck lanes (WAIT past the start cooldown, then sampled
  play so candidates and played entities exist);
* every decision it builds a sampled ``ActionSequenceV4`` per actor seat -- one
  micro action for most lanes, two for lanes that opened more than one candidate
  (the second at a delayed bin, so slot 1 is exercised too);
* the batched decode produces one plan per seat; each lane is decoded again by
  the stock path through a ``reference_scalar_observation`` projected from the
  same facts, mapped game-cell -> engine index, and compared index by index,
  offset by offset, candidate row by candidate row;
* the decoded indices are then **stepped into the engine**, so the gate also
  proves the engine accepts what the decode emits: a refusal or an illegal
  counter increment fails the run;
* paired timing: one batched decode for every lane and seat vs the per-lane
  stock decode loop, which is the half the stacked batch replaces.

    .venv/bin/python tools/crforge/vector_decode.py \
        --count 256 --steps 40 --verify-lanes 32 --seats 0 1 \
        --output runs/crforge-c4/vector-decode.json
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
from crforge_bridge.vector_actions import (
    ENGINE_WAIT,
    VectorActionDecoder,
    engine_action_from_native,
)
from crforge_bridge.vector_observation import VectorObservationEncoder
from crforge_bridge.vector_rows import reference_action_mask, reference_scalar_observation
from crforge_bridge.vector_tensorize import VectorTensorizer, lane_batch, lane_sequence
from native_runner.timeline import load_game_mode_timeline
from native_runner.training.v4.config import ModelConfigV4
from native_runner.training.v4.decoding import decode_action_sequence_v4
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
# The second micro action's delay bin: 100 ms, i.e. two ticks past the base one.
SECOND_DELAY_BIN = 2


def _checkpoint_config(checkpoint: Path) -> ModelConfigV4:
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
    """A random non-spell play per seat with ``play_rate``, else WAIT."""

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
    """A sampled sequence: the first legal candidate, plus a delayed second.

    The first micro action uses the first open candidate row at its first legal
    tile.  A lane that opened at least two candidates gets a second micro action
    on the next row at the delayed bin, so slot 1 of the plan is exercised; the
    stock oracle may still refuse the pair, which the gate records rather than
    treats as a mismatch.
    """

    lanes = int(candidates.mask.shape[0])
    steps = int(config.max_micro_actions)
    width = int(config.board_width)
    gate = torch.zeros(lanes, dtype=torch.long)
    count = torch.zeros(lanes, dtype=torch.long)
    index = torch.full((lanes, steps), -1, dtype=torch.long)
    uid = torch.full((lanes, steps), -1, dtype=torch.long)
    target = torch.full((lanes, steps), -1, dtype=torch.long)
    delay = torch.full((lanes, steps), -1, dtype=torch.long)
    mask = candidates.mask.numpy()
    placement = candidates.placement.numpy()
    for lane in range(lanes):
        open_rows = np.flatnonzero(mask[lane])
        if open_rows.size == 0:
            continue
        gate[lane] = GATE_ACT
        wanted = min(int(open_rows.size), steps)
        count[lane] = wanted
        for step in range(wanted):
            row = int(open_rows[step])
            index[lane, step] = row
            uid[lane, step] = int(candidates.uid[lane, row])
            cells = np.argwhere(placement[lane, row])
            if cells.size:
                target[lane, step] = int(cells[0, 0]) * width + int(cells[0, 1])
            delay[lane, step] = 0 if step == 0 else SECOND_DELAY_BIN
    return ActionSequenceV4(
        gate=gate,
        micro_action_count=count,
        candidate_index=index,
        candidate_uid=uid,
        target_cell=target,
        delay_offset_bin=delay,
    )


def _reference(stock: Any, tables: Any, timeline: Any, batch: Any, lane: int, seat: int) -> Any:
    return reference_scalar_observation(
        batch,
        lane,
        tables=tables,
        tensorizer=stock,
        timeline=timeline,
        tower_troop_id=TOWER_TROOP,
        seat=int(seat),
        action_mask=reference_action_mask(batch, lane, tensorizer=stock, seat=int(seat), model_frame=False),
        episode_id=f"lane{lane}",
    )


def _oracle(
    stock: Any,
    tables: Any,
    timeline: Any,
    batch: Any,
    stacked: Any,
    sequence: ActionSequenceV4,
    lane: int,
    seat: int,
) -> tuple[list[int], list[int]]:
    """The stock per-lane decode of one lane, as engine indices and offsets."""

    view = _reference(stock, tables, timeline, batch, lane, seat)
    decoded = decode_action_sequence_v4(
        lane_sequence(sequence, lane),
        lane_batch(stacked, lane).candidates,
        row=0,
        observation=view,
        catalog=stock.catalog,
        deck=stock.deck,
        card_costs=stock.card_costs,
        ability_id_by_vocab_id=stock.ability_id_by_vocab_id,
        horizontal_mirror=stock.perspective.horizontal_mirror,
        hand_slot_permutation=stock.perspective.hand_slot_permutation,
        config=stock.config,
        base_latency_ticks=1,
        validate=False,
    )
    actions = [engine_action_from_native(item, cols=18, rows=32, seat=seat) for item in decoded.actions]
    offsets = [
        int(item.metadata["execute_offset_ticks"]) if index > ENGINE_WAIT else -1
        for index, item in zip(actions, decoded.actions, strict=True)
    ]
    return actions, offsets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=256)
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--timing-repeats", type=int, default=3)
    parser.add_argument("--timing-samples", type=int, default=8)
    parser.add_argument("--verify-lanes", type=int, default=32, help="lanes compared every step after step 0")
    parser.add_argument(
        "--warmup-steps", type=int, default=60, help="WAIT-only decisions before the loop, past the start cooldown"
    )
    parser.add_argument("--play-rate", type=float, default=0.5)
    parser.add_argument(
        "--seats",
        type=int,
        nargs="+",
        default=[0, 1],
        choices=(0, 1),
        help="actor seats to decode (0 1 gates both owners of every lane)",
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--checkpoint", type=Path, default=Path(".resource/RoyaleFish/models/B-general-v275.pt"))
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c4/vector-decode.json"))
    args = parser.parse_args(argv)

    start_jvm(str(DEFAULT_INSTALL))
    from crforge_env import _jvm as env_jvm

    env_jvm._started_with = Path(DEFAULT_INSTALL).expanduser().resolve()
    catalog = Catalog.load()
    config = _checkpoint_config(args.checkpoint)
    seats = tuple(dict.fromkeys(int(seat) for seat in args.seats))
    if not seats or any(seat not in (0, 1) for seat in seats):
        raise SystemExit(f"--seats must be a subset of 0 1, got {args.seats}")
    stocks = {seat: _tensorizer(config, seat) for seat in seats}
    timeline = load_game_mode_timeline(STANDARD_GAME_MODE).timeline
    vector = _vector(args.count)
    encoder = VectorObservationEncoder(vector, catalog)
    encoder.reset()
    batch = encoder.encode()
    rows_by_seat = {
        seat: VectorTensorizer(
            stocks[seat],
            vocabulary=list(vector.vocabulary),
            bridge_catalog=catalog,
            timeline=timeline,
            tower_troop_id=TOWER_TROOP,
        )
        for seat in seats
    }
    for rows in rows_by_seat.values():
        rows.reset(batch)
    decoders = {seat: VectorActionDecoder(stocks[seat], schema=vector.schema) for seat in seats}
    rng = np.random.default_rng(args.seed)
    for _ in range(args.warmup_steps):
        _step(vector, rng, 0.0)
    encoder.encode(batch)

    mismatches: list[str] = []
    by_seat = {
        seat: {
            "lane_decisions": 0,
            "act_rows": 0,
            "deploy_rows": 0,
            "two_micro_rows": 0,
            "oracle_two_micro_rows": 0,
            "oracle_refusals": 0,
            "mask_closed": 0,
            "max_candidates_per_lane": 0,
        }
        for seat in seats
    }
    illegal_before = int(batch.illegal.sum())
    refusal_before = int(batch.last_refusal.sum())
    steps_run = 0
    try:
        for step in range(args.steps):
            lanes = list(range(args.count)) if step == 0 else list(range(min(args.verify_lanes, args.count)))
            plans = {}
            sequences = {}
            stacked_by_seat = {}
            for seat in seats:
                rows = rows_by_seat[seat]
                stacked = rows.tensorize(batch)
                stacked_by_seat[seat] = stacked
                sequence = _action_sequence(stacked.candidates, config)
                sequences[seat] = sequence
                plan = decoders[seat].decode(batch, sequence, stacked.candidates, seat=seat)
                plans[seat] = plan

                for lane in lanes:
                    stats = by_seat[seat]
                    stats["lane_decisions"] += 1
                    planned = int(plan.steps[lane])
                    if planned:
                        stats["act_rows"] += 1
                    if planned > 1:
                        stats["two_micro_rows"] += 1
                    stats["deploy_rows"] += int((plan.actions[lane] > ENGINE_WAIT).sum())
                    stats["mask_closed"] += int((plan.actions[lane] > ENGINE_WAIT).sum() - plan.masked[lane].sum())
                    stats["max_candidates_per_lane"] = max(
                        stats["max_candidates_per_lane"], int(stacked.candidates.mask[lane].sum())
                    )
                    try:
                        actions, offsets = _oracle(
                            stocks[seat], rows.tables, timeline, batch, stacked, sequence, lane, seat
                        )
                    except ValueError as error:
                        if "illegal under V4 shadow state" in str(error):
                            stats["oracle_refusals"] += 1
                            actions, offsets = None, None
                        else:
                            raise
                    if actions is None:
                        continue
                    if len(actions) > 1:
                        stats["oracle_two_micro_rows"] += 1
                    for micro, (expected, expected_offset) in enumerate(zip(actions, offsets, strict=True)):
                        actual = int(plan.actions[lane, micro])
                        actual_offset = int(plan.offsets[lane, micro])
                        if actual != expected:
                            mismatches.append(
                                f"step={step} seat={seat} lane={lane} micro={micro} "
                                f"action {actual} != {expected}"
                            )
                        elif actual_offset != expected_offset:
                            mismatches.append(
                                f"step={step} seat={seat} lane={lane} micro={micro} "
                                f"offset {actual_offset} != {expected_offset}"
                            )
                        elif expected > ENGINE_WAIT and not bool(plan.masked[lane, micro]):
                            mismatches.append(
                                f"step={step} seat={seat} lane={lane} micro={micro} index {expected} is not open"
                            )
                rows.record_action(sequence, stacked, validate=False)
            if mismatches:
                steps_run = step
                break

            # Apply the decoded first micro action for both seats.  The engine is
            # the final authority: a refused or illegal play invalidates the run.
            engine_actions = np.zeros((args.count, 2), dtype=np.int32)
            for seat in seats:
                engine_actions[:, seat] = plans[seat].actions[:, 0]
            vector.step(engine_actions)
            steps_run = step + 1
            encoder.encode(batch)

        illegal_after = int(batch.illegal.sum())
        refusal_after = int(batch.last_refusal.sum())
        if illegal_after > illegal_before:
            mismatches.append(f"engine counted {illegal_after - illegal_before} illegal actions")
        if refusal_after > refusal_before:
            mismatches.append(f"engine raised {refusal_after - refusal_before} refusals")

        # ---- paired timing on the final frame ------------------------------
        timing_stacked = {seat: rows_by_seat[seat].tensorize(batch) for seat in seats}
        timing_sequences = {seat: _action_sequence(timing_stacked[seat].candidates, config) for seat in seats}

        def batched() -> None:
            for seat in seats:
                decoders[seat].decode(batch, timing_sequences[seat], timing_stacked[seat].candidates, seat=seat)

        def oracle() -> None:
            for seat in seats:
                for lane in range(args.count):
                    try:
                        _oracle(
                            stocks[seat],
                            rows_by_seat[seat].tables,
                            timeline,
                            batch,
                            timing_stacked[seat],
                            timing_sequences[seat],
                            lane,
                            seat,
                        )
                    except ValueError as error:
                        # A sampled two-micro-action pair the live shadow state
                        # refuses is the engine's answer, not a decode failure.
                        if "illegal under V4 shadow state" not in str(error):
                            raise

        def _seat_batched(seat: int) -> None:
            decoders[seat].decode(batch, timing_sequences[seat], timing_stacked[seat].candidates, seat=seat)

        batched_median = statistics.median(
            [_timed(batched, args.timing_repeats) for _ in range(args.timing_samples)]
        )
        oracle_median = statistics.median(
            [_timed(oracle, args.timing_repeats) for _ in range(args.timing_samples)]
        )
        per_seat_batched = {
            seat: statistics.median(
                [
                    _timed(lambda seat=seat: _seat_batched(seat), args.timing_repeats)
                    for _ in range(args.timing_samples)
                ]
            )
            for seat in seats
        }
        lane_decisions_timed = args.count * len(seats)
        totals = {name: sum(stats[name] for stats in by_seat.values()) for name in next(iter(by_seat.values()))}

        artifact = {
            "schema": "crforge-vector-decode.v1",
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
                "second_delay_bin": SECOND_DELAY_BIN,
            },
            "substrate": {
                "release": RELEASE,
                "tables": str(DEFAULT_TABLES),
                "install": str(DEFAULT_INSTALL),
                "checkpoint": str(args.checkpoint),
                "timeline": timeline.name,
                "game_mode": STANDARD_GAME_MODE,
                "tower_troop": TOWER_TROOP,
                "frames": (
                    "model -> game follows the tensorizer perspective; game -> engine mirrors x for seat 0 "
                    "and y for seat 1 (Schema.toSeatX/toSeatY)"
                ),
                "scopes": (
                    "deploy candidates only (the bridge envelope emits no ability or Mirror candidate); "
                    "every micro action is decoded with its execute offset"
                ),
            },
            "equivalence": {
                "steps": steps_run,
                "lanes_checked_first_step": args.count,
                "lanes_checked_after": args.verify_lanes,
                "lane_decisions": totals["lane_decisions"],
                "act_rows": totals["act_rows"],
                "deploy_rows": totals["deploy_rows"],
                "two_micro_rows": totals["two_micro_rows"],
                "oracle_two_micro_rows": totals["oracle_two_micro_rows"],
                "oracle_refusals": totals["oracle_refusals"],
                "mask_closed": totals["mask_closed"],
                "illegal": illegal_after - illegal_before,
                "refusals": refusal_after - refusal_before,
                "mismatches": mismatches[:40],
                "pass": not mismatches,
                "by_seat": {str(seat): by_seat[seat] for seat in seats},
            },
            "timing": {
                "lanes": args.count,
                "seats": list(seats),
                "lane_decisions": lane_decisions_timed,
                "batched_ms": batched_median,
                "batched_ms_per_lane": batched_median / lane_decisions_timed,
                "oracle_ms": oracle_median,
                "oracle_ms_per_lane": oracle_median / lane_decisions_timed,
                "speedup": (oracle_median / batched_median) if batched_median else 0.0,
                "by_seat": {
                    str(seat): {
                        "batched_ms": per_seat_batched[seat],
                        "batched_ms_per_lane": per_seat_batched[seat] / args.count,
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
                    "equivalence": artifact["equivalence"],
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
