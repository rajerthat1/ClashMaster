"""C3-B gate: batched deploy candidates against the per-lane tensorizer.

C3 attributed 0.30 of the 0.992 CPU ms/decision packet to ``_candidates``: the
actor's legal hand slots, each with its runtime cost and form, the deck row it
addresses and a 32x18 native placement grid.  ``crforge_bridge/vector_rows.py``
builds those rows for every lane from the vectorized facts layer in one pass.
This tool is their gate: it steps real 256-lane learner-deck battles, compares
every candidate tensor against the stock per-lane method on the same facts, and
times both paths paired (alternating samples, because this host's absolute
timings move with load).

The oracle is the stock ``_candidates`` called with an observation projected
from the same ``VectorObservationBatch`` — entities, players, pending actions and
an ``action_mask`` whose hand slots, placement entries and effective elixir come
from the same facts.  The section's own validation runs on the batch too, so a
shape or vocabulary regression fails here rather than at the model.

    .venv/bin/python tools/crforge/batched_candidates.py \
        --count 256 --steps 40 --verify-lanes 32 \
        --output runs/crforge-c3/batched-candidates.json
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
from crforge_bridge.vector_rows import (
    VectorCandidateBuilder,
    VectorEntityTables,
    compare_candidate_section,
    oracle_groups,
    reference_action_mask,
    reference_observation,
)
from native_runner.training.v4.config import ModelConfigV4
from native_runner.training.v4.factory import deck_roles_from_form_availability, production_semantic_bundle
from native_runner.training.v4.policy_session import config_from_contract
from native_runner.training.v4.tensorizer import UniversalObservationTensorizerV4

LEARNER_DECK = (27000008, 27000006, 26000001, 26000000, 26000010, 26000084, 28000000, 28000011)
LEARNER_FORMS = (0, 1, 1, 2, 0, 0, 0, 0)
LEARNER_NAMES = ("Xbow", "Tesla", "Archer", "Knight", "Skeletons", "ElectroSpirit", "Fireball", "Log")
TOWER_TROOP = 159000000
RELEASE = "16.635.1"
# The C3 profile's ``_candidates`` share of the 0.992 ms/decision packet.
C3_SHARE_MS = 0.300


def _checkpoint_config(checkpoint: Path) -> ModelConfigV4:
    """The checkpoint's own model config (21 card columns for B-general-v275)."""

    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    contract = payload.get("contract") if isinstance(payload, dict) else None
    if contract is None:
        raise ValueError(f"{checkpoint} has no V4 contract")
    return config_from_contract(contract)


def _tensorizer(config: ModelConfigV4) -> UniversalObservationTensorizerV4:
    bundle = production_semantic_bundle(RELEASE)
    return UniversalObservationTensorizerV4(
        actor_owner=0,
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


def _vector(count: int):
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=256)
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--timing-repeats", type=int, default=3)
    parser.add_argument("--timing-samples", type=int, default=10)
    parser.add_argument("--verify-lanes", type=int, default=32, help="lanes compared every step after step 0")
    parser.add_argument(
        "--warmup-steps",
        type=int,
        default=60,
        help="WAIT-only decisions before the loop, past the start cooldown",
    )
    parser.add_argument("--play-rate", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--checkpoint", type=Path, default=Path(".resource/RoyaleFish/models/B-general-v275.pt"))
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c3/batched-candidates.json"))
    args = parser.parse_args(argv)

    start_jvm(str(DEFAULT_INSTALL))
    from crforge_env import _jvm as env_jvm

    env_jvm._started_with = Path(DEFAULT_INSTALL).expanduser().resolve()
    catalog = Catalog.load()
    config = _checkpoint_config(args.checkpoint)
    tensorizer = _tensorizer(config)
    vector = _vector(args.count)
    encoder = VectorObservationEncoder(vector, catalog)
    encoder.reset()
    batch = encoder.encode()
    tables = VectorEntityTables.build(tensorizer=tensorizer, vocabulary=list(vector.vocabulary), bridge_catalog=catalog)
    candidates_builder = VectorCandidateBuilder(tensorizer)
    rng = np.random.default_rng(args.seed)
    # The match refuses card plays during its start cooldown, so a cold frame has
    # an empty candidate set on both paths — an equivalence that proves nothing.
    # Wait it out before measuring.
    for _ in range(args.warmup_steps):
        _step(vector, rng, 0.0)
    encoder.encode(batch)

    mismatches: list[str] = []
    counts: list[int] = []
    pending_slots = 0
    building_rows = 0
    empty_lanes = 0
    steps_run = 0
    try:
        for step in range(args.steps):
            lanes = list(range(args.count)) if step == 0 else list(range(min(args.verify_lanes, args.count)))
            section = candidates_builder.build(batch)
            section.candidates.validate(
                tensorizer.config,
                batch_size=int(batch.count),
                card_vocab_size=tensorizer.catalog.vocab_size,
                ability_vocab_size=tensorizer.ability_catalog.vocab_size,
                own_card_count=int(tensorizer.config.max_own_cards),
                group_count=int(tensorizer.config.max_battle_groups),
                child_count=int(tensorizer.config.max_children_total),
            )
            found = compare_candidate_section(section, tensorizer, batch, tables=tables, lanes=lanes)
            if found:
                mismatches.extend(f"step={step} {value}" for value in found)
                steps_run = step
                break
            counts.append(int(section.count.max(initial=0)))
            empty_lanes += int((section.count == 0).sum())
            pending_slots += int((batch.hand_pending[:, 0] > 0).sum())
            building_rows += int(section.candidates.is_building.sum())
            steps_run = step + 1
            _step(vector, rng, args.play_rate)
            encoder.encode(batch)

        # ---- paired timing on the final frame -------------------------------
        references = [
            reference_observation(
                batch,
                lane,
                tables=tables,
                tensorizer=tensorizer,
                action_mask=reference_action_mask(batch, lane, tensorizer=tensorizer),
            )
            for lane in range(args.count)
        ]
        oracle_inputs = []
        for observation in references:
            tensorizer._frame_players.clear()
            _own, _opponent, card_to_row = tensorizer._card_sets(observation)
            _groups, group_index, child_index, _group_overflow, _child_overflow = oracle_groups(
                tensorizer, observation.entities
            )
            oracle_inputs.append((card_to_row, group_index, child_index))

        def batched() -> None:
            candidates_builder.build(batch)

        def oracle() -> None:
            for observation, (card_to_row, group_index, child_index) in zip(
                references, oracle_inputs, strict=True
            ):
                tensorizer._candidates(
                    observation,
                    card_to_row=card_to_row,
                    group_index_by_entity=group_index,
                    child_index_by_entity=child_index,
                )

        batched_samples = [_timed(batched, args.timing_repeats) for _ in range(args.timing_samples)]
        oracle_samples = [_timed(oracle, args.timing_repeats) for _ in range(args.timing_samples)]
        batched_median = statistics.median(batched_samples)
        oracle_median = statistics.median(oracle_samples)
        batched_per_lane = batched_median / args.count

        artifact = {
            "schema": "crforge-batched-candidates.v1",
            "measured": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "shape": {
                "count": args.count,
                "steps": args.steps,
                "warmup_steps": args.warmup_steps,
                "verify_lanes_step0": args.count,
                "verify_lanes_after": args.verify_lanes,
                "play_rate": args.play_rate,
                "seed": args.seed,
            },
            "substrate": {
                "release": RELEASE,
                "tables": str(DEFAULT_TABLES),
                "install": str(DEFAULT_INSTALL),
                "checkpoint": str(args.checkpoint),
                "card_runtime_feature_dim": int(config.card_runtime_feature_dim),
                "card_runtime_schema": ("21-column" if int(config.card_runtime_feature_dim) == 21 else "16-column"),
                "max_action_candidates": int(config.max_action_candidates),
                "tower_troop": TOWER_TROOP,
            },
            "equivalence": {
                "steps": steps_run,
                "lanes_checked_first_step": args.count,
                "lanes_checked_after": args.verify_lanes,
                "mismatches": mismatches[:40],
                "pass": not mismatches,
            },
            "counts": {
                "max_candidates_per_lane": max(counts) if counts else 0,
                "lanes_with_no_candidate": empty_lanes,
                "queued_play_slots_seen": pending_slots,
                "building_candidate_rows_seen": building_rows,
            },
            "timing": {
                "lanes": args.count,
                "batched_ms": batched_median,
                "oracle_ms": oracle_median,
                "batched_ms_per_lane": batched_per_lane,
                "oracle_ms_per_lane": oracle_median / args.count,
                "speedup": (oracle_median / batched_median) if batched_median else 0.0,
                "c3_profile_share_ms": C3_SHARE_MS,
                "projected_packet_ms": 0.992 - C3_SHARE_MS + batched_per_lane,
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "pass": artifact["equivalence"]["pass"],
                    "steps": steps_run,
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
