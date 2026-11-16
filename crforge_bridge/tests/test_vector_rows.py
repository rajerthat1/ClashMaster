"""The batched group row build must equal the per-lane tensorizer oracle."""

from __future__ import annotations

import numpy as np
import pytest

from crforge_bridge.catalog import Catalog
from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
from crforge_bridge.vector_observation import VectorObservationEncoder
from crforge_bridge.vector_rows import (
    VectorCandidateBuilder,
    VectorCardBuilder,
    VectorEmptySectionBuilder,
    VectorEntityTables,
    VectorGroupBuilder,
    VectorRelationBuilder,
    VectorScalarBuilder,
    VectorSpatialPlaneBuilder,
    VectorTowerBuilder,
    candidate_geometry,
    compare_candidate_section,
    compare_card_section,
    compare_empty_section,
    compare_group_section,
    compare_relation_section,
    compare_scalar_section,
    compare_spatial_plane_section,
    compare_tower_section,
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


def _tensorizer(
    config: ModelConfigV4 | None = None, *, horizontal_mirror: bool = False
) -> UniversalObservationTensorizerV4:
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
        horizontal_mirror=horizontal_mirror,
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


def test_batched_candidates_match_the_per_lane_oracle(crforge_runtime: None) -> None:
    """Hand order, runtime cost/form, placement grid and building geometry."""

    tensorizer = _tensorizer()
    with _vector(count=4) as vector:
        _, tables = _builder(tensorizer, vector)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        candidates_builder = VectorCandidateBuilder(tensorizer)
        saw_pending = False
        saw_building = False
        for step in range(50):
            section = candidates_builder.build(batch)
            section.candidates.validate(
                tensorizer.config,
                batch_size=batch.count,
                card_vocab_size=tensorizer.catalog.vocab_size,
                ability_vocab_size=tensorizer.ability_catalog.vocab_size,
                own_card_count=tensorizer.config.max_own_cards,
                group_count=tensorizer.config.max_battle_groups,
                child_count=tensorizer.config.max_children_total,
            )
            saw_pending = saw_pending or bool((batch.hand_pending[:, 0] > 0).any())
            saw_building = saw_building or bool(section.candidates.is_building.any())
            mismatches = compare_candidate_section(section, tensorizer, batch, tables=tables)
            assert mismatches == [], f"step {step}: {mismatches}"
            _step(vector, 400 + step)
            encoder.encode(batch)
        assert saw_pending, "the fixture never queued a play, so the pending-slot rule is untested"
        assert saw_building, "the fixture never dealt a building candidate"


def test_candidate_slot_rule_closes_pending_unaffordable_and_blocked_slots(crforge_runtime: None) -> None:
    """The mask is the environment's rule, not the native tile block alone."""

    tensorizer = _tensorizer()
    with _vector(count=2) as vector:
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        # Let the start cooldown pass with no play queued, so every slot is open.
        wait = np.zeros((vector.count, 2), dtype=np.int32)
        for _ in range(60):
            vector.step(wait)
        batch = encoder.encode()
        builder = VectorCandidateBuilder(tensorizer)
        legal, _elixir = builder.hand_slots(batch)
        assert legal.all(), "an open hand is fully affordable and placeable"

        # A queued play closes its slot, and only its slot.
        batch.hand_pending[0, 0, 1] = 1
        legal, _elixir = builder.hand_slots(batch)
        assert legal[0].tolist() == [True, False, True, True]

        # No elixir closes the lane.
        batch.elixir_raw[1, 0] = 0
        batch.elixir_reserved_raw[1, 0] = 0
        legal, _elixir = builder.hand_slots(batch)
        assert not legal[1].any(), "a card costs at least one elixir"

        # The header's promise accounting is what is spendable, not the raw bar.
        batch.elixir_raw[1, 0] = 100_000
        batch.elixir_reserved_raw[1, 0] = 100_000
        legal, _elixir = builder.hand_slots(batch)
        assert not legal[1].any(), "a fully promised balance leaves nothing to spend"

        # An empty native tile block is ``no_legal_placement`` in the env.
        block = batch.hand_slots * vector.schema.cols * vector.schema.rows
        batch.masks[:, :, 1 : 1 + block] = 0
        legal, _elixir = builder.hand_slots(batch)
        assert not bool(legal.any())


def test_candidate_building_geometry_is_the_native_lattice(crforge_runtime: None) -> None:
    """A 2x2 Tesla carries the half-tile subcell offset; a 3x3 X-Bow does not."""

    tesla = candidate_geometry(_tensorizer(), 27000006)
    assert tesla.is_building
    assert tesla.half_width == pytest.approx(1.0)
    assert tesla.half_height == pytest.approx(1.0)
    assert (tesla.offset_x, tesla.offset_y) == pytest.approx((0.5, 0.5))

    xbow = candidate_geometry(_tensorizer(), 27000008)
    assert xbow.is_building
    assert xbow.half_width == pytest.approx(1.5)
    assert (xbow.offset_x, xbow.offset_y) == pytest.approx((0.0, 0.0))

    # A troop has no building footprint, so the decoder reads it as centered.
    knight = candidate_geometry(_tensorizer(), 26000000)
    assert not knight.is_building
    assert knight.half_width == 0.0

    # The rows carry the same values for the card they name.
    tensorizer = _tensorizer()
    with _vector(count=2) as vector:
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        builder = VectorCandidateBuilder(tensorizer)
        saw_building = False
        for step in range(40):
            section = builder.build(batch)
            candidates = section.candidates
            for row in np.flatnonzero(candidates.mask[0] & candidates.is_building[0]):
                card_id = int(candidates.native_visible_card_id[0, row])
                expected = candidate_geometry(tensorizer, card_id)
                assert float(candidates.building_half_width[0, row]) == pytest.approx(expected.half_width)
                assert float(candidates.building_offset_x[0, row]) == pytest.approx(expected.offset_x)
                saw_building = True
            _step(vector, 700 + step)
            encoder.encode(batch)
        assert saw_building, "the fixture never dealt a building candidate"


def test_candidate_builder_refuses_mirror(crforge_runtime: None) -> None:
    """Mirror needs the actor-private runtime contract this frame does not carry."""

    from native_runner.training.v4.native_actions import MIRROR_CARD_ID

    tensorizer = _tensorizer()
    original = tensorizer.deck
    roles = dict(tensorizer.deck_roles)
    roles[MIRROR_CARD_ID] = (False, False)
    tensorizer.deck = (MIRROR_CARD_ID,)
    tensorizer.deck_roles = roles
    try:
        with pytest.raises(ValueError, match="Mirror"):
            VectorCandidateBuilder(tensorizer)
    finally:
        tensorizer.deck = original
        tensorizer.deck_roles = roles


TOWER_TROOP = 159000000


def _tower_fields() -> tuple[int, int, int, int]:
    from native_runner.training.v4.config import TOWER_FEATURE_NAMES

    return (
        TOWER_FEATURE_NAMES.index("hitpoints_ratio"),
        TOWER_FEATURE_NAMES.index("active"),
        TOWER_FEATURE_NAMES.index("hitpoints_over_5000"),
        TOWER_FEATURE_NAMES.index("max_hitpoints_over_5000"),
    )


def test_batched_towers_match_the_per_lane_oracle(crforge_runtime: None) -> None:
    """Every tower tensor field and row index, lane by lane."""

    tensorizer = _tensorizer()
    with _vector(count=4) as vector:
        _, tables = _builder(tensorizer, vector)
        builder = VectorTowerBuilder(tensorizer, tower_troop_id=TOWER_TROOP)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        for step in range(50):
            section = builder.build(batch)
            assert int(section.towers.mask.sum()) == batch.count * 6
            mismatches = compare_tower_section(
                section, tensorizer, batch, tables=tables, tower_troop_id=TOWER_TROOP
            )
            assert mismatches == [], f"step {step}: {mismatches}"
            _step(vector, 500 + step)
            encoder.encode(batch)


def test_tower_order_and_types_follow_the_model_view_under_mirror(crforge_runtime: None) -> None:
    """A horizontal flip swaps the princess kinds, which reorders the rows."""

    tensorizer = _tensorizer(horizontal_mirror=True)
    with _vector(count=2) as vector:
        _, tables = _builder(tensorizer, vector)
        builder = VectorTowerBuilder(tensorizer, tower_troop_id=TOWER_TROOP)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        section = builder.build(batch)
        # Actor first, then enemy; the model's left princess is the native right
        # tower (kind code 2), so each side is king, right, left.
        assert section.towers.tower_type[0].tolist() == [0, 1, 2, 0, 1, 2]
        assert section.towers.owner_type[0].tolist() == [0, 0, 0, 1, 1, 1]
        assert int(section.tower_index[0, 2]) == 1
        assert int(section.tower_index[0, 1]) == 2
        assert compare_tower_section(
            section, tensorizer, batch, tables=tables, tower_troop_id=TOWER_TROOP
        ) == []


def test_tower_troop_is_a_static_build_input(crforge_runtime: None) -> None:
    """The frame carries no tower troop, so the builder pins it per match."""

    tensorizer = _tensorizer()
    with _vector(count=1) as vector:
        batch = VectorObservationEncoder(vector, Catalog.load()).encode()
        pinned = VectorTowerBuilder(tensorizer, tower_troop_id=TOWER_TROOP).build(batch)
        assert pinned.towers.tower_troop_type.tolist() == [[1] * 6]
        bare = VectorTowerBuilder(tensorizer).build(batch)
        assert bare.towers.tower_troop_type.tolist() == [[0] * 6]
        with pytest.raises(ValueError, match="not a competitive Tower Troop"):
            VectorTowerBuilder(tensorizer, tower_troop_id=123)
        tensorizer.actor_owner = 1
        try:
            with pytest.raises(ValueError, match="seat 0"):
                VectorTowerBuilder(tensorizer)
        finally:
            tensorizer.actor_owner = 0


def test_destroyed_and_missing_towers_keep_the_oracle_shape(crforge_runtime: None) -> None:
    """A dead tower is a zero-HP row; an absent one is padding, not a phantom."""

    hp_ratio, active, hp, max_hp = _tower_fields()
    tensorizer = _tensorizer()
    builder = VectorTowerBuilder(tensorizer, tower_troop_id=TOWER_TROOP)
    with _vector(count=2) as vector:
        _, tables = _builder(tensorizer, vector)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()

        # Destroy the enemy's right princess (tower id 5).  The row survives
        # with zero HP and the original maximum, so the ratio reads 0.
        batch.tower_hp[:, 5] = 0
        section = builder.build(batch)
        row = int(section.tower_index[0, 5])
        assert row == 5
        assert bool(section.towers.mask[0, row])
        assert float(section.towers.features[0, row, active]) == 0.0
        assert float(section.towers.features[0, row, hp_ratio]) == 0.0
        max_hitpoints = int(batch.tower_max_hp[0, 5])
        assert float(section.towers.features[0, row, max_hp]) == pytest.approx(max_hitpoints / 5000.0)
        assert float(section.towers.features[0, row, hp]) == 0.0
        assert compare_tower_section(
            section, tensorizer, batch, tables=tables, tower_troop_id=TOWER_TROOP
        ) == []

        # A tower row that leaves the shared scene drops out of the section.
        lanes, columns = np.nonzero(batch.entity_valid & batch.entity_is_tower & (batch.entity_id == 4))
        batch.entity_valid[lanes[0], columns[0]] = False
        batch.entity_is_tower[lanes[0], columns[0]] = False
        section = builder.build(batch)
        assert int(section.tower_index[lanes[0], 4]) == -1
        assert section.towers.mask[lanes[0]].tolist() == [True] * 5 + [False]
        assert compare_tower_section(
            section, tensorizer, batch, tables=tables, tower_troop_id=TOWER_TROOP
        ) == []



def _play_card(vector, encoder, card_id: int, *, lane: int = 0, seat: int = 0, attempts: int = 80) -> bool:
    """Play one specific card as soon as it is in hand and affordable."""

    wait = np.zeros((vector.count, 2), dtype=np.int32)
    rows, cols = int(vector.schema.rows), int(vector.schema.cols)
    for _ in range(attempts):
        batch = encoder.encode()
        elixir = int(batch.elixir_raw[lane, seat]) - int(batch.elixir_reserved_raw[lane, seat])
        for slot in range(int(vector.schema.hand_slots)):
            if int(batch.hand_card_id[lane, seat, slot]) != int(card_id):
                continue
            if elixir < int(batch.hand_cost[lane, seat, slot]) * 10_000:
                continue
            cells = np.argwhere(batch.placement_rows(seat)[lane, slot])
            if not cells.size:
                continue
            y, x = (int(value) for value in cells[len(cells) // 2])
            actions = wait.copy()
            actions[lane, seat] = 1 + slot * rows * cols + y * cols + x
            vector.step(actions)
            return True
        vector.step(wait)
    return False


def test_batched_spatial_planes_match_the_per_lane_oracle(crforge_runtime: None) -> None:
    """Every explicit plane, lane by lane, including the building footprint fill."""

    tensorizer = _tensorizer()
    with _vector(count=4) as vector:
        _, tables = _builder(tensorizer, vector)
        builder = VectorSpatialPlaneBuilder(tensorizer, tables)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()

        # A Tesla (2x2) pins the building-category footprint fill.  A queued
        # play runs a few ticks later, so wait for the building row to appear.
        assert _play_card(vector, encoder, 27000006), "the fixture could not deploy a Tesla"
        wait = np.zeros((vector.count, 2), dtype=np.int32)
        section = builder.build(encoder.encode(batch=batch))
        for _ in range(8):
            if bool((section.planes[:, 2] > 0).any()):
                break
            vector.step(wait)
            section = builder.build(encoder.encode(batch=batch))
        assert bool((section.planes[:, 2] > 0).any()), "the forced Tesla produced no building plane"
        assert compare_spatial_plane_section(section, tensorizer, batch, tables=tables) == []

        saw_ground = False
        for step in range(50):
            section = builder.build(batch)
            assert tuple(section.planes.shape) == (batch.count, 14, 32, 18)
            saw_ground = saw_ground or bool((section.planes[:, 0] > 0).any())
            mismatches = compare_spatial_plane_section(section, tensorizer, batch, tables=tables)
            assert mismatches == [], f"step {step}: {mismatches}"
            _step(vector, 600 + step)
            encoder.encode(batch)
        assert saw_ground, "the fixture never fielded a ground troop"


def test_spatial_plane_tables_project_the_archetype_metadata(crforge_runtime: None) -> None:
    """Airborne, threat and the persistent-root rule come from the same metadata."""

    tensorizer = _tensorizer()
    with _vector(count=1) as vector:
        _, tables = _builder(tensorizer, vector)
        archetypes = tensorizer.entity_archetype_catalog
        for index in range(tables.vocab_size):
            info = archetypes.metadata_for_vocab_id(int(tables.archetype_id[index]))
            expected_airborne = bool(info.is_airborne) if info.is_airborne_known else False
            assert bool(tables.is_airborne[index]) == expected_airborne
            expected_damage = (
                float(info.static_damage_basis)
                if info.static_damage_basis_known and info.static_damage_basis is not None
                else 0.0
            )
            assert float(tables.static_damage[index]) == expected_damage
            assert bool(tables.is_persistent_area_root[index]) == (
                tables.child_kind[index] in {"area", "effect"}
            )


def test_spatial_planes_skip_towers_and_neutral_rows(crforge_runtime: None) -> None:
    """Towers are a separate section, and an ownerless row is not a plane entity."""

    tensorizer = _tensorizer()
    with _vector(count=2) as vector:
        _, tables = _builder(tensorizer, vector)
        builder = VectorSpatialPlaneBuilder(tensorizer, tables)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()

        # A reset scene is towers only, which this section never draws.
        section = builder.build(batch)
        assert not bool(section.planes.any())
        assert compare_spatial_plane_section(section, tensorizer, batch, tables=tables) == []

        row = None
        for step in range(80):
            _step(vector, 1000 + step)
            encoder.encode(batch)
            hits = np.argwhere(batch.entity_valid & ~batch.entity_is_tower)
            if hits.size:
                row = (int(hits[0][0]), int(hits[0][1]))
                break
        assert row is not None, "the fixture never fielded a troop"
        lane, slot = row
        original_side = int(batch.entity_side[lane, slot])

        # A neutral row contributes nothing: the neutral build equals the build
        # with the row removed, on both the batched and the oracle path.
        batch.entity_side[lane, slot] = 2
        neutral = builder.build(batch).planes.numpy()
        assert compare_spatial_plane_section(
            builder.build(batch), tensorizer, batch, tables=tables
        ) == []
        batch.entity_valid[lane, slot] = False
        batch.entity_side[lane, slot] = original_side
        removed = builder.build(batch).planes.numpy()
        assert np.array_equal(neutral, removed)


STANDARD_GAME_MODE = 72_000_006  # ``BattleEnvV1.STANDARD_GAME_MODE``


def _timeline():
    from native_runner.timeline import load_game_mode_timeline

    return load_game_mode_timeline(STANDARD_GAME_MODE).timeline


def test_batched_scalars_match_the_per_lane_oracle(crforge_runtime: None) -> None:
    """Clock, phase, elixirs, crowns and every overflow ratio, lane by lane."""

    tensorizer = _tensorizer()
    with _vector(count=4) as vector:
        _, tables = _builder(tensorizer, vector)
        groups_builder = VectorGroupBuilder(tensorizer, tables)
        candidates_builder = VectorCandidateBuilder(tensorizer)
        scalars_builder = VectorScalarBuilder(tensorizer, timeline=_timeline())
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        saw_elixir = False
        saw_candidate = False
        for step in range(50):
            groups = groups_builder.build(batch)
            candidates = candidates_builder.build(batch)
            section = scalars_builder.build(batch, groups=groups, candidates=candidates)
            assert tuple(section.scalars.shape) == (batch.count, 18)
            saw_elixir = saw_elixir or bool((batch.elixir_raw[:, 0] > 0).any())
            saw_candidate = saw_candidate or bool((candidates.count > 0).any())
            mismatches = compare_scalar_section(
                section, tensorizer, batch, tables=tables, timeline=_timeline()
            )
            assert mismatches == [], f"step {step}: {mismatches}"
            _step(vector, 1100 + step)
            encoder.encode(batch)
        assert saw_elixir, "the fixture never generated elixir, so the clock columns are untested"
        assert saw_candidate, "the fixture never produced a candidate, so the count column is untested"


def test_scalar_timeline_phases_follow_the_standard_schedule(crforge_runtime: None) -> None:
    """Normal / double / overtime / tiebreak come from the mode timeline."""

    tensorizer = _tensorizer()
    timeline = _timeline()
    with _vector(count=2) as vector:
        _, tables = _builder(tensorizer, vector)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        groups = VectorGroupBuilder(tensorizer, tables).build(batch)
        candidates = VectorCandidateBuilder(tensorizer).build(batch)
        builder = VectorScalarBuilder(tensorizer, timeline=timeline)

        expected = {
            0: (1.0, 1.0 / 3.0, 0.0, 0.0),
            2400: (0.6, 2.0 / 3.0, 0.0, 0.0),
            3600: (0.4, 2.0 / 3.0, 1.0, 0.0),
            4800: (0.2, 1.0, 1.0, 0.0),
            6000: (0.0, 1.0, 0.0, 1.0),
        }
        for tick, columns in expected.items():
            batch.tick[:, 0] = tick
            scalars = builder.build(batch, groups=groups, candidates=candidates).scalars[0]
            assert float(scalars[0]) == pytest.approx(columns[0])
            assert float(scalars[1]) == pytest.approx(columns[1])
            assert float(scalars[2]) == pytest.approx(columns[2])
            assert float(scalars[3]) == pytest.approx(columns[3])
            assert float(scalars[17]) == 1.0
            assert compare_scalar_section(
                builder.build(batch, groups=groups, candidates=candidates),
                tensorizer,
                batch,
                tables=tables,
                timeline=timeline,
            ) == []


def test_scalar_builder_refuses_a_tracker(crforge_runtime: None) -> None:
    """A tracker would turn the sound public bound into a guessed belief."""

    tensorizer = _tensorizer()
    tensorizer.tracker = object()
    try:
        with pytest.raises(ValueError, match="no-event envelope"):
            VectorScalarBuilder(tensorizer, timeline=_timeline())
    finally:
        tensorizer.tracker = None


def test_batched_empty_effects_and_events_match_the_per_lane_oracle(crforge_runtime: None) -> None:
    """The no-effect/no-event envelope widens the stock empty records exactly."""

    tensorizer = _tensorizer()
    config = tensorizer.config
    token_count = (
        int(config.max_towers) + int(config.max_own_cards) + int(config.max_opponent_cards) + int(config.max_battle_groups)
    )
    with _vector(count=2) as vector:
        _, tables = _builder(tensorizer, vector)
        builder = VectorEmptySectionBuilder(tensorizer)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        for step in range(20):
            section = builder.build(batch)
            effects, events, count, overflow = section.lane(0)
            assert tuple(effects.mask.shape) == (1, 0)
            assert tuple(events.mask.shape) == (1, int(config.max_recent_events))
            assert not bool(events.mask.any())
            assert int(events.owner_type.min()) == 2
            assert int(events.source_group_index.max()) == -1
            assert int(events.target_token_index.max()) == -1
            assert count == 0 and overflow == 0
            effects.validate(
                config,
                batch_size=1,
                effect_vocab_size=tensorizer.effect_catalog.vocab_size,
                child_count=int(config.max_children_total),
                tower_count=int(config.max_towers),
            )
            events.validate(
                config,
                batch_size=1,
                vocab_size=tensorizer.catalog.vocab_size,
                group_count=int(config.max_battle_groups),
                token_count=token_count,
            )
            assert compare_empty_section(section, tensorizer, batch, tables=tables) == []
            _step(vector, 1200 + step)
            encoder.encode(batch)


def test_empty_section_builder_refuses_a_nonzero_seat(crforge_runtime: None) -> None:
    tensorizer = _tensorizer()
    tensorizer.actor_owner = 1
    try:
        with pytest.raises(ValueError, match="seat 0"):
            VectorEmptySectionBuilder(tensorizer)
    finally:
        tensorizer.actor_owner = 0
