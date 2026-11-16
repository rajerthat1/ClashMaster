"""C3-B gate: batched card sets and relation edges against the per-lane tensorizer.

C3 attributed 0.39/0.33 CPU ms/decision to ``_card_sets`` and 0.14 to
``_relations``.  ``crforge_bridge/vector_rows.py`` builds both sections for every
lane of a ``VectorBattle`` from the vectorized facts layer in one pass.  This tool
is their gate: it steps real 256-lane learner-deck battles, compares every card
and relation tensor against the stock per-lane methods on the same facts, and
times both paths paired (alternating samples, because this host's absolute
timings move with load).

The oracle is the stock tensorizer called with players, towers, entities and
pending actions projected from the same ``VectorObservationBatch`` — the same
pattern the group gate uses.  The checkpoint's own ``model_config`` is honoured,
so the 16.635 21-column card runtime schema (including the pending-execution
columns) is what gets measured.

    .venv/bin/python tools/crforge/batched_cards.py \
        --count 256 --steps 40 --verify-lanes 32 \
        --output runs/crforge-c3/batched-cards.json
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
    VectorCardBuilder,
    VectorEntityTables,
    VectorGroupBuilder,
    VectorRelationBuilder,
    compare_card_section,
    compare_relation_section,
    oracle_groups,
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
    parser.add_argument("--play-rate", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--checkpoint", type=Path, default=Path(".resource/RoyaleFish/models/B-general-v275.pt"))
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c3/batched-cards.json"))
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
    groups_builder = VectorGroupBuilder(tensorizer, tables)
    cards_builder = VectorCardBuilder(tensorizer)
    relations_builder = VectorRelationBuilder(tensorizer)
    rng = np.random.default_rng(args.seed)

    card_mismatches: list[str] = []
    relation_mismatches: list[str] = []
    entity_counts: list[int] = []
    pending_max = 0
    edge_max = 0
    steps_run = 0
    try:
        for step in range(args.steps):
            lanes = list(range(args.count)) if step == 0 else list(range(min(args.verify_lanes, args.count)))
            groups = groups_builder.build(batch)
            cards = cards_builder.build(batch)
            relations = relations_builder.build(batch, cards, groups)
            mismatch_sets = [("cards", compare_card_section(cards, tensorizer, batch, tables=tables, lanes=lanes))]
            if step == 0 or step % 4 == 0:
                mismatch_sets.append(
                    (
                        "relations",
                        compare_relation_section(
                            relations, tensorizer, batch, tables=tables, tower_troop_id=TOWER_TROOP, lanes=lanes
                        ),
                    )
                )
            for name, found in mismatch_sets:
                if found:
                    target = card_mismatches if name == "cards" else relation_mismatches
                    target.extend(f"step={step} {value}" for value in found)
            if card_mismatches or relation_mismatches:
                steps_run = step
                break
            entity_counts.append(int((batch.entity_valid & ~batch.entity_is_tower).sum(axis=1).max()))
            pending_max = max(pending_max, int(batch.pending_count[:, 0].max()))
            edge_max = max(edge_max, int(relations.mask.sum(axis=1).max()))
            steps_run = step + 1
            _step(vector, rng, args.play_rate)
            encoder.encode(batch)

        # ---- paired timing on the final frame -------------------------------
        references = [
            reference_observation(batch, lane, tables=tables, tensorizer=tensorizer, tower_troop_id=TOWER_TROOP)
            for lane in range(args.count)
        ]
        oracle_inputs = []
        for observation in references:
            own, opponent, _card_to_row = tensorizer._card_sets(observation)
            _towers, tower_index = tensorizer._towers(observation)
            groups, group_index, _child_index, _group_overflow, _child_overflow = oracle_groups(
                tensorizer, observation.entities
            )
            oracle_inputs.append((own, opponent, tower_index, groups, group_index))

        frame_cards = cards_builder.build(batch)
        frame_groups = groups_builder.build(batch)

        def batched_cards() -> None:
            cards_builder.build(batch)

        def oracle_cards() -> None:
            for observation in references:
                tensorizer._card_sets(observation)

        def batched_relations() -> None:
            relations_builder.build(batch, frame_cards, frame_groups)

        def oracle_relations() -> None:
            for observation, (own, opponent, tower_index, groups, group_index) in zip(
                references, oracle_inputs, strict=True
            ):
                tensorizer._relations(
                    observation,
                    group_index_by_entity=group_index,
                    tower_token_by_entity=tower_index,
                    own_cards=own,
                    opponent_cards=opponent,
                    groups=groups,
                )

        timings: dict[str, float] = {}
        samples: dict[str, list[float]] = {}
        for name, batched, oracle in (
            ("cards", batched_cards, oracle_cards),
            ("relations", batched_relations, oracle_relations),
        ):
            batched_samples = [_timed(batched, args.timing_repeats) for _ in range(args.timing_samples)]
            oracle_samples = [_timed(oracle, args.timing_repeats) for _ in range(args.timing_samples)]
            batched_median = statistics.median(batched_samples)
            oracle_median = statistics.median(oracle_samples)
            timings[name] = batched_median / args.count
            samples[name] = [batched_median, oracle_median]

        artifact = {
            "schema": "crforge-batched-cards.v1",
            "measured": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "shape": {
                "count": args.count,
                "steps": args.steps,
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
                "tower_troop": TOWER_TROOP,
            },
            "equivalence": {
                "steps": steps_run,
                "lanes_checked_first_step": args.count,
                "lanes_checked_after": args.verify_lanes,
                "card_mismatches": card_mismatches[:40],
                "relation_mismatches": relation_mismatches[:40],
                "pass": not card_mismatches and not relation_mismatches,
            },
            "counts": {
                "max_entities_per_lane": max(entity_counts) if entity_counts else 0,
                "max_pending_rows_per_lane": pending_max,
                "max_relation_edges_per_lane": edge_max,
            },
            "timing": {
                "lanes": args.count,
                "cards": {
                    "batched_ms": samples["cards"][0],
                    "oracle_ms": samples["cards"][1],
                    "batched_ms_per_lane": timings["cards"],
                    "oracle_ms_per_lane": samples["cards"][1] / args.count,
                    "speedup": (samples["cards"][1] / samples["cards"][0]) if samples["cards"][0] else 0.0,
                },
                "relations": {
                    "batched_ms": samples["relations"][0],
                    "oracle_ms": samples["relations"][1],
                    "batched_ms_per_lane": timings["relations"],
                    "oracle_ms_per_lane": samples["relations"][1] / args.count,
                    "speedup": (samples["relations"][1] / samples["relations"][0]) if samples["relations"][0] else 0.0,
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
                    "counts": artifact["counts"],
                    "timing": artifact["timing"],
                    "output": str(args.output),
                },
                indent=2,
            )
        )
    finally:
        vector.close()
    return 0 if not card_mismatches and not relation_mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
