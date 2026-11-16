"""The batched group row build must equal the per-lane tensorizer oracle."""

from __future__ import annotations

import numpy as np
import pytest

from crforge_bridge.catalog import Catalog
from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
from crforge_bridge.vector_observation import VectorObservationEncoder
from crforge_bridge.vector_rows import (
    VectorCardBuilder,
    VectorEntityTables,
    VectorGroupBuilder,
    VectorRelationBuilder,
    compare_card_section,
    compare_group_section,
    compare_relation_section,
    reference_entities,
    reference_pending_actions,
)
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


def _v2_config() -> ModelConfigV4:
    """The 16.635 21-column card runtime schema (B-general-v275's contract)."""

    from dataclasses import replace

    return replace(release_config(), card_runtime_feature_dim=21, neutralize_pending_ticks_remaining=True)


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


def test_batched_cards_match_the_per_lane_oracle(crforge_runtime: None) -> None:
    """Own/opponent card sets, forms, roles and all 21 runtime columns."""

    tensorizer = _tensorizer(_v2_config())
    with _vector(count=4) as vector:
        _, tables = _builder(tensorizer, vector)
        cards_builder = VectorCardBuilder(tensorizer)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        saw_pending = False
        for step in range(50):
            section = cards_builder.build(batch)
            saw_pending = saw_pending or bool((section.own.runtime_features[..., 16] > 0).any())
            mismatches = compare_card_section(section, tensorizer, batch, tables=tables)
            assert mismatches == [], f"step {step}: {mismatches}"
            _step(vector, 200 + step)
            encoder.encode(batch)
        assert saw_pending, "the fixture never produced a pending play, so the 16.x columns are untested"


def test_batched_relations_match_the_per_lane_oracle(crforge_runtime: None) -> None:
    """Target, reverse-target and card->group edges, row for row."""

    tensorizer = _tensorizer()
    with _vector(count=4) as vector:
        group_builder, tables = _builder(tensorizer, vector)
        cards_builder = VectorCardBuilder(tensorizer)
        relations_builder = VectorRelationBuilder(tensorizer)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        saw_edges = False
        for step in range(50):
            groups = group_builder.build(batch)
            cards = cards_builder.build(batch)
            relations = relations_builder.build(batch, cards, groups)
            saw_edges = saw_edges or bool(relations.mask.any())
            mismatches = compare_relation_section(relations, tensorizer, batch, tables=tables)
            assert mismatches == [], f"step {step}: {mismatches}"
            _step(vector, 300 + step)
            encoder.encode(batch)
        assert saw_edges, "the fixture produced no relation edges"


def test_pending_card_cell_is_the_seat_frame_mirror(crforge_runtime: None) -> None:
    """A pending play's seat-frame point maps to the model cell exactly once."""

    tensorizer = _tensorizer(_v2_config())
    with _vector(count=2) as vector:
        _, tables = _builder(tensorizer, vector)
        cards_builder = VectorCardBuilder(tensorizer)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        card_id = int(cards_builder.own_order[0])
        batch.pending_count[:, 0] = 1
        batch.pending_card_id[:, 0, 0] = card_id
        batch.pending_x[:, 0, 0] = 2500  # seat frame: native x 15500
        batch.pending_y[:, 0, 0] = 12500
        batch.pending_ticks[:, 0, 0] = 8
        section = cards_builder.build(batch)
        row = cards_builder.own_order.index(card_id)
        assert float(section.own.runtime_features[0, row, 16]) == 1.0
        # ``neutralize_pending_ticks_remaining`` zeroes the inert tick column.
        assert float(section.own.runtime_features[0, row, 17]) == 0.0
        assert float(section.own.runtime_features[0, row, 18]) == pytest.approx(15 / 17)
        assert float(section.own.runtime_features[0, row, 19]) == pytest.approx(12 / 31)
        reference = reference_pending_actions(batch, 0, tensorizer=tensorizer)
        assert len(reference) == 1
        assert reference[0].action.target_grid == (15, 12)
        assert compare_card_section(section, tensorizer, batch, tables=tables) == []

        # An even-sized building's command point sits half a tile past the
        # cell center: Tesla (2x2) at cell (15, 12) is world (16000, 13000),
        # i.e. seat-frame (2000, 13000), and must still read as (15, 12).
        tesla = 27000006
        tesla_row = cards_builder.own_order.index(tesla)
        batch.pending_card_id[:, 0, 0] = tesla
        batch.pending_x[:, 0, 0] = 2000
        batch.pending_y[:, 0, 0] = 13000
        section = cards_builder.build(batch)
        assert float(section.own.runtime_features[0, tesla_row, 18]) == pytest.approx(15 / 17)
        assert float(section.own.runtime_features[0, tesla_row, 19]) == pytest.approx(12 / 31)
        reference = reference_pending_actions(batch, 0, tensorizer=tensorizer)
        assert reference[0].action.target_grid == (15, 12)
        assert compare_card_section(section, tensorizer, batch, tables=tables) == []


def test_waiting_ability_sets_the_ability_pending_column(crforge_runtime: None) -> None:
    """A champion ability in flight belongs to its card's runtime row."""

    tensorizer = _tensorizer(_v2_config())
    with _vector(count=2) as vector:
        _, tables = _builder(tensorizer, vector)
        cards_builder = VectorCardBuilder(tensorizer)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        knight = 26000000
        assert tensorizer.ability_ids_by_card.get(knight), "the fixture needs the Knight hero ability"
        batch.ability_champion_id[:, 0, 0] = knight
        batch.ability_unit[:, 0, 0] = 42
        batch.ability_ticks[:, 0, 0] = 9
        section = cards_builder.build(batch)
        row = cards_builder.own_order.index(knight)
        assert float(section.own.runtime_features[0, row, 20]) == 1.0
        pending = reference_pending_actions(batch, 0, tensorizer=tensorizer)
        assert [item.action.kind.value for item in pending] == ["activate_ability"]
        assert compare_card_section(section, tensorizer, batch, tables=tables) == []


def test_card_builder_refuses_a_tracker(crforge_runtime: None) -> None:
    """The bridge envelope carries no events, so a tracker would invent reveals."""

    tensorizer = _tensorizer()
    tensorizer.tracker = object()
    try:
        with pytest.raises(ValueError, match="no-event envelope"):
            VectorCardBuilder(tensorizer)
    finally:
        tensorizer.tracker = None
