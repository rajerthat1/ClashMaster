"""C3-B gate: the batched match scalars against the per-lane tensorizer.

``_scalars`` is the smallest named section of the C3 packet (0.0140 CPU
ms/decision in the shipped profile): 18 match-wide numbers per decision — clock,
phase, elixir multiplier, both elixirs and the reserved balance, crowns, the
visible-entity count, the group/child/candidate/event ratios and the static actor
identity.  ``crforge_bridge/vector_rows.py`` builds them for every lane from the
vectorized facts and the same mode timeline ``BattleEnvV1`` uses.

This tool steps real 256-lane learner-deck battles, compares every scalar
against the stock per-lane method (whose group/child overflow and candidate
count come from its own sections), then times both paths paired.  A 40-decision
window stays in the normal phase, so the overtime/tiebreak columns are pinned by
the unit test's synthetic ticks rather than here; the artifact records which
phases were actually seen.

    .venv/bin/python tools/crforge/batched_scalars.py \
        --count 256 --steps 40 --verify-lanes 32 \
        --output runs/crforge-c3/batched-scalars.json
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
    VectorGroupBuilder,
    VectorScalarBuilder,
    compare_scalar_section,
    oracle_groups,
    reference_action_mask,
    reference_scalar_observation,
)
from native_runner.timeline import load_game_mode_timeline
from native_runner.training.v4.config import ModelConfigV4
from native_runner.training.v4.factory import deck_roles_from_form_availability, production_semantic_bundle
from native_runner.training.v4.policy_session import config_from_contract
from native_runner.training.v4.tensorizer import UniversalObservationTensorizerV4

LEARNER_DECK = (27000008, 27000006, 26000001, 26000000, 26000010, 26000084, 28000000, 28000011)
LEARNER_FORMS = (0, 1, 1, 2, 0, 0, 0, 0)
LEARNER_NAMES = ("Xbow", "Tesla", "Archer", "Knight", "Skeletons", "ElectroSpirit", "Fireball", "Log")
TOWER_TROOP = 159000000
RELEASE = "16.635.1"
STANDARD_GAME_MODE = 72_000_006  # ``BattleEnvV1.STANDARD_GAME_MODE``
# The shipped C3 profile's ``_scalars`` share of the 0.992 ms/decision packet.
C3_SHARE_MS = 0.0140


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
    parser.add_argument("--timing-repeats", type=int, default=5)
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
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c3/batched-scalars.json"))
    args = parser.parse_args(argv)

    start_jvm(str(DEFAULT_INSTALL))
    from crforge_env import _jvm as env_jvm

    env_jvm._started_with = Path(DEFAULT_INSTALL).expanduser().resolve()
    catalog = Catalog.load()
    config = _checkpoint_config(args.checkpoint)
    tensorizer = _tensorizer(config)
    timeline = load_game_mode_timeline(STANDARD_GAME_MODE).timeline
    vector = _vector(args.count)
    encoder = VectorObservationEncoder(vector, catalog)
    encoder.reset()
    batch = encoder.encode()
    tables = VectorEntityTables.build(tensorizer=tensorizer, vocabulary=list(vector.vocabulary), bridge_catalog=catalog)
    groups_builder = VectorGroupBuilder(tensorizer, tables)
    candidates_builder = VectorCandidateBuilder(tensorizer)
    builder = VectorScalarBuilder(tensorizer, timeline=timeline)
    rng = np.random.default_rng(args.seed)
    for _ in range(args.warmup_steps):
        _step(vector, rng, 0.0)
    encoder.encode(batch)

    mismatches: list[str] = []
    phases_seen: set[str] = set()
    max_elixir = 0.0
    max_candidates = 0
    max_entities = 0
    steps_run = 0
    try:
        for step in range(args.steps):
            lanes = list(range(args.count)) if step == 0 else list(range(min(args.verify_lanes, args.count)))
            groups = groups_builder.build(batch)
            candidates = candidates_builder.build(batch)
            section = builder.build(batch, groups=groups, candidates=candidates)
            if tuple(section.scalars.shape) != (batch.count, int(config.generic_scalar_dim)):
                mismatches.append(f"step={step} scalars.shape")
            if not bool(torch.isfinite(section.scalars).all()):
                mismatches.append(f"step={step} scalars.nonfinite")
            found = compare_scalar_section(
                section, tensorizer, batch, tables=tables, timeline=timeline, lanes=lanes
            )
            if found:
                mismatches.extend(f"step={step} {value}" for value in found)
                steps_run = step
                break
            scalars = section.scalars.numpy()
            phases_seen.update(
                "tiebreak" if row[3] else ("overtime" if row[2] else "normal") for row in scalars
            )
            max_elixir = max(max_elixir, float(scalars[:, 4].max(initial=0.0)) * 10.0)
            max_candidates = max(
                max_candidates, round(float(scalars[:, 13].max(initial=0.0)) * config.max_action_candidates)
            )
            max_entities = max(
                max_entities, round(float(scalars[:, 7].max(initial=0.0)) * config.max_children_total)
            )
            steps_run = step + 1
            _step(vector, rng, args.play_rate)
            encoder.encode(batch)

        # ---- paired timing on the final frame -------------------------------
        references = []
        for lane in range(args.count):
            observation = reference_scalar_observation(
                batch,
                lane,
                tables=tables,
                tensorizer=tensorizer,
                timeline=timeline,
                action_mask=reference_action_mask(batch, lane, tensorizer=tensorizer),
            )
            tensorizer._frame_players.clear()
            _own, _opponent, card_to_row = tensorizer._card_sets(observation)
            _groups, group_index, child_index, group_overflow, child_overflow = oracle_groups(
                tensorizer, observation.entities
            )
            _candidates, candidate_count = tensorizer._candidates(
                observation,
                card_to_row=card_to_row,
                group_index_by_entity=group_index,
                child_index_by_entity=child_index,
            )
            references.append((observation, group_overflow, child_overflow, candidate_count))

        groups = groups_builder.build(batch)
        candidates = candidates_builder.build(batch)

        def batched() -> None:
            builder.build(batch, groups=groups, candidates=candidates)

        def oracle() -> None:
            for observation, group_overflow, child_overflow, candidate_count in references:
                tensorizer._scalars(
                    observation,
                    group_overflow=group_overflow,
                    child_overflow=child_overflow,
                    event_count=0,
                    event_overflow=0,
                    candidate_count=candidate_count,
                )

        batched_samples = [_timed(batched, args.timing_repeats) for _ in range(args.timing_samples)]
        oracle_samples = [_timed(oracle, args.timing_repeats) for _ in range(args.timing_samples)]
        batched_median = statistics.median(batched_samples)
        oracle_median = statistics.median(oracle_samples)
        batched_per_lane = batched_median / args.count

        artifact = {
            "schema": "crforge-batched-scalars.v1",
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
                "generic_scalar_dim": int(config.generic_scalar_dim),
                "timeline": timeline.name,
                "game_mode": STANDARD_GAME_MODE,
                "native_tick_offset": 0,
                "scopes": "no tracker (public [0,10] bound), no events (both event columns zero)",
            },
            "equivalence": {
                "steps": steps_run,
                "lanes_checked_first_step": args.count,
                "lanes_checked_after": args.verify_lanes,
                "mismatches": mismatches[:40],
                "pass": not mismatches,
            },
            "counts": {
                "phases_seen": sorted(phases_seen),
                "max_own_elixir": max_elixir,
                "max_candidates_per_lane": max_candidates,
                "max_visible_entities": max_entities,
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
