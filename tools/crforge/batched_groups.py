"""C3-B gate: the batched group section against the per-lane tensorizer.

C3 attributed ~0.54 of a ~1.0 CPU ms/decision packet to ``_groups`` + children.
``crforge_bridge/vector_rows.py`` builds that section for every lane of a
``VectorBattle`` from the vectorized facts layer in one pass.  This tool is the
gate: it steps real vector lanes with the locked learner deck, compares every
group/child tensor against the per-lane ``_groups`` oracle on the same facts,
and times both paths paired (alternating runs, because this host's absolute
timings move with load).

The oracle is the stock tensorizer called with ``EntityStateV1`` rows projected
from the same ``VectorObservationBatch``.  It resolves archetype, child kind,
extent, radius and runtime form through its own helper methods, so equality is
not a shared shortcut.

    .venv/bin/python tools/crforge/batched_groups.py \
        --count 256 --steps 40 --repeats 3 --verify-lanes 32 \
        --output runs/crforge-c3/batched-groups.json
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

from crforge_bridge.catalog import Catalog
from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
from crforge_bridge.jvm import start as start_jvm
from crforge_bridge.vector_observation import VectorObservationEncoder
from crforge_bridge.vector_rows import (
    VectorEntityTables,
    VectorGroupBuilder,
    compare_group_section,
    oracle_groups,
    reference_entities,
)
from native_runner.competitive_release import load_release
from native_runner.training.v4.config import ModelConfigV4
from native_runner.training.v4.factory import deck_roles_from_form_availability, production_semantic_bundle
from native_runner.training.v4.tensorizer import UniversalObservationTensorizerV4

LEARNER_DECK = (27000008, 27000006, 26000001, 26000000, 26000010, 26000084, 28000000, 28000011)
LEARNER_FORMS = (0, 1, 1, 2, 0, 0, 0, 0)
LEARNER_NAMES = ("Xbow", "Tesla", "Archer", "Knight", "Skeletons", "ElectroSpirit", "Fireball", "Log")
RELEASE = "16.635.1"


def _tensorizer() -> UniversalObservationTensorizerV4:
    identity = load_release(RELEASE).identity
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
        config=ModelConfigV4(
            ability_vocab_size=identity.ability_vocab_size, child_archetype_count=identity.child_archetype_count
        ),
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
    parser.add_argument("--repeats", type=int, default=3, help="paired timing repeats per sample")
    parser.add_argument("--timing-repeats", type=int, default=5)
    parser.add_argument("--timing-samples", type=int, default=10)
    parser.add_argument("--verify-lanes", type=int, default=32, help="lanes compared every step after step 0")
    parser.add_argument("--play-rate", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c3/batched-groups.json"))
    args = parser.parse_args(argv)

    start_jvm(str(DEFAULT_INSTALL))
    # ``crforge_bridge.jvm`` and ``crforge_env._jvm`` share one JVM but each
    # remembers the folder it started with; give the vector package the same.
    from crforge_env import _jvm as env_jvm

    env_jvm._started_with = Path(DEFAULT_INSTALL).expanduser().resolve()
    catalog = Catalog.load()
    tensorizer = _tensorizer()
    vector = _vector(args.count)
    encoder = VectorObservationEncoder(vector, catalog)
    encoder.reset()
    batch = encoder.encode()
    tables = VectorEntityTables.build(tensorizer=tensorizer, vocabulary=list(vector.vocabulary), bridge_catalog=catalog)
    builder = VectorGroupBuilder(tensorizer, tables)
    rng = np.random.default_rng(args.seed)

    mismatches: list[str] = []
    verify = list(range(args.count))
    entity_counts: list[int] = []
    group_counts: list[int] = []
    child_counts: list[int] = []
    overflow_max = 0
    steps_run = 0
    try:
        for step in range(args.steps):
            section = builder.build(batch)
            lanes = verify if step == 0 else verify[: args.verify_lanes]
            found = compare_group_section(section, tensorizer, batch, tables=tables, lanes=lanes)
            if found:
                mismatches.extend(f"step={step} {value}" for value in found)
                steps_run = step
                break
            entity_counts.append(int((batch.entity_valid & ~batch.entity_is_tower).sum(axis=1).max()))
            group_counts.append(int(section.groups.mask.sum(axis=1).max()))
            child_counts.append(int(section.groups.child_mask.sum(axis=1).max()))
            overflow_max = max(overflow_max, int(section.group_overflow.max()), int(section.child_overflow.max()))
            steps_run = step + 1
            _step(vector, rng, args.play_rate)
            encoder.encode(batch)

        # ---- paired timing on the final frame -------------------------------
        timed_lanes = list(range(args.count))
        entities = [reference_entities(batch, lane, tables=tables, tensorizer=tensorizer) for lane in timed_lanes]

        def batched() -> None:
            builder.build(batch)

        def oracle() -> None:
            for lane_entities in entities:
                oracle_groups(tensorizer, lane_entities)

        batched_ms: list[float] = []
        oracle_ms: list[float] = []
        for _ in range(args.timing_samples):
            batched_ms.append(_timed(batched, args.timing_repeats))
            oracle_ms.append(_timed(oracle, args.timing_repeats))
        batched_median = statistics.median(batched_ms)
        oracle_median = statistics.median(oracle_ms)
        per_lane_oracle = oracle_median / args.count
        per_lane_batched = batched_median / args.count

        artifact = {
            "schema": "crforge-batched-groups.v1",
            "measured": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "shape": {
                "count": args.count,
                "steps": args.steps,
                "verify_lanes_step0": args.count,
                "verify_lanes_after": args.verify_lanes,
                "play_rate": args.play_rate,
                "seed": args.seed,
            },
            "substrate": {"release": RELEASE, "tables": str(DEFAULT_TABLES), "install": str(DEFAULT_INSTALL)},
            "equivalence": {
                "steps": steps_run,
                "lanes_checked_first_step": args.count,
                "lanes_checked_after": args.verify_lanes,
                "mismatches": mismatches[:40],
                "pass": not mismatches,
            },
            "entity_counts": {
                "max_per_lane": max(entity_counts) if entity_counts else 0,
                "mean_max_per_step": statistics.mean(entity_counts) if entity_counts else 0.0,
                "max_groups_per_lane": max(group_counts) if group_counts else 0,
                "max_children_per_lane": max(child_counts) if child_counts else 0,
                "max_overflow_observed": overflow_max,
            },
            "timing": {
                "timed_lanes": args.count,
                "batched_ms": batched_median,
                "oracle_ms": oracle_median,
                "batched_ms_per_lane": per_lane_batched,
                "oracle_ms_per_lane": per_lane_oracle,
                "speedup": per_lane_oracle / per_lane_batched if per_lane_batched else 0.0,
                "batched_samples_ms": batched_ms,
                "oracle_samples_ms": oracle_ms,
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "pass": artifact["equivalence"]["pass"],
                    "steps": steps_run,
                    "entities": artifact["entity_counts"],
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
