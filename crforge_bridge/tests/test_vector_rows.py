"""The batched group row build must equal the per-lane tensorizer oracle."""

from __future__ import annotations

import numpy as np
import pytest

from crforge_bridge.catalog import Catalog
from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
from crforge_bridge.vector_observation import VectorObservationEncoder
from crforge_bridge.vector_rows import VectorEntityTables, VectorGroupBuilder, compare_group_section, reference_entities
from native_runner.competitive_release import load_release
from native_runner.training.v4.config import ModelConfigV4
from native_runner.training.v4.factory import deck_roles_from_form_availability, production_semantic_bundle
from native_runner.training.v4.tensorizer import UniversalObservationTensorizerV4

# The locked X-Bow learner deck (HANDOFF.md 2026-11-15).  Slot flags are the
# native form mask: 1 is evolved, 2 is hero, 0 is base.
LEARNER_DECK = (27000008, 27000006, 26000001, 26000000, 26000010, 26000084, 28000000, 28000011)
LEARNER_FORMS = (0, 1, 1, 2, 0, 0, 0, 0)
LEARNER_NAMES = ("Xbow", "Tesla", "Archer", "Knight", "Skeletons", "ElectroSpirit", "Fireball", "Log")
RELEASE = "16.635.1"


def release_config() -> ModelConfigV4:
    """The 16.635 catalog capacities; the default config is the 15.535 shape."""

    identity = load_release(RELEASE).identity
    return ModelConfigV4(
        ability_vocab_size=identity.ability_vocab_size, child_archetype_count=identity.child_archetype_count
    )


def _tensorizer(config: ModelConfigV4 | None = None) -> UniversalObservationTensorizerV4:
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
        config=config or release_config(),
        reject_unknown_public_semantics=True,
    )


def _vector(count: int = 4):
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


def _step(vector, seed: int) -> None:
    rng = np.random.default_rng(seed)
    actions = np.zeros((vector.count, 2), dtype=np.int32)
    first_ability = int(vector.schema.first_ability)
    for lane in range(vector.count):
        for seat in (0, 1):
            if rng.random() >= 0.5:
                continue
            legal = np.flatnonzero(vector.masks[lane, seat, 1:first_ability])
            if legal.size:
                actions[lane, seat] = int(legal[rng.integers(legal.size)]) + 1
    vector.step(actions)


def _builder(tensorizer, vector):
    catalog = Catalog.load()
    tables = VectorEntityTables.build(tensorizer=tensorizer, vocabulary=list(vector.vocabulary), bridge_catalog=catalog)
    return VectorGroupBuilder(tensorizer, tables), tables


def test_batched_groups_match_the_per_lane_oracle(crforge_runtime: None) -> None:
    tensorizer = _tensorizer()
    with _vector(count=4) as vector:
        builder, tables = _builder(tensorizer, vector)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        for step in range(50):
            mismatches = compare_group_section(builder.build(batch), tensorizer, batch, tables=tables)
            assert mismatches == [], f"step {step}: {mismatches}"
            _step(vector, step + 1)
            encoder.encode(batch)


def test_group_capacity_overflow_is_exact(crforge_runtime: None) -> None:
    """A two-group / three-child cap must drop the same rows as the oracle."""

    from dataclasses import replace

    tensorizer = _tensorizer(replace(release_config(), max_battle_groups=2, max_children_total=3))
    with _vector(count=3) as vector:
        builder, tables = _builder(tensorizer, vector)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        saw_overflow = False
        for step in range(60):
            section = builder.build(batch)
            saw_overflow = saw_overflow or bool((section.group_overflow > 0).any())
            mismatches = compare_group_section(section, tensorizer, batch, tables=tables)
            assert mismatches == [], f"step {step}: {mismatches}"
            _step(vector, step + 7)
            encoder.encode(batch)
        assert saw_overflow, "the fixture never exceeded the two-group cap"


def test_empty_board_masks_every_row(crforge_runtime: None) -> None:
    tensorizer = _tensorizer()
    with _vector(count=2) as vector:
        builder, tables = _builder(tensorizer, vector)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        assert not (batch.entity_valid & ~batch.entity_is_tower).any(), "the reset scene should have towers only"
        section = builder.build(batch)
        assert not bool(section.groups.mask.any())
        assert not bool(section.groups.child_mask.any())
        # ``_groups`` pre-fills owner_type with neutral before the loop.
        assert section.groups.owner_type.tolist() == [[2] * builder.max_groups] * 2
        assert compare_group_section(section, tensorizer, batch, tables=tables) == []


def test_reference_entities_carry_the_frame_facts(crforge_runtime: None) -> None:
    """The oracle adapter is a fact projection, not a second interpretation."""

    tensorizer = _tensorizer()
    with _vector(count=2) as vector:
        _, tables = _builder(tensorizer, vector)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        for step in range(20):
            _step(vector, 100 + step)
            encoder.encode(batch)
        for lane in range(batch.count):
            entities = reference_entities(batch, lane, tables=tables, tensorizer=tensorizer)
            slots = np.flatnonzero(batch.entity_valid[lane] & ~batch.entity_is_tower[lane])
            assert len(entities) == len(slots)
            for entity, slot in zip(entities, slots, strict=True):
                assert entity.entity_id == int(batch.entity_id[lane, slot])
                assert entity.position == (float(batch.entity_x[lane, slot]), float(batch.entity_y[lane, slot]))
                assert entity.hitpoints == (
                    None if batch.entity_hp[lane, slot] < 0 else float(batch.entity_hp[lane, slot])
                )
                if batch.entity_velocity_valid[lane, slot]:
                    assert entity.velocity is not None
                else:
                    assert entity.velocity is None


def test_reference_entities_refuse_a_nonzero_seat(crforge_runtime: None) -> None:
    tensorizer = _tensorizer()
    with _vector(count=1) as vector:
        _, tables = _builder(tensorizer, vector)
        batch = VectorObservationEncoder(vector, Catalog.load()).encode()
        with pytest.raises(ValueError, match="seat 0"):
            reference_entities(batch, 0, tables=tables, tensorizer=tensorizer, seat=1)
