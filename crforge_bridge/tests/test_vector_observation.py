"""The vectorized observation facts must equal the bridge's per-lane decoders."""

from __future__ import annotations

import numpy as np

from crforge_bridge.catalog import Catalog
from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
from crforge_bridge.vector_observation import (
    TOWER_LAYOUT,
    VectorObservationEncoder,
    compare_lane_facts,
)

DECK = ("Xbow", "Tesla", "Archer", "Knight", "Skeletons", "ElectroSpirit", "Fireball", "Log")
DECK_SLOTS = (0, 1, 1, 2, 0, 0, 0, 0)
TOWERS = "King_PrincessTowers"


def _vector(count: int = 4):
    from crforge_env.vector import LadderConfig, SideSetup, VectorBattle

    setup = SideSetup(towers=TOWERS, king_level=16, tower_level=16)
    vector = VectorBattle(
        count,
        1,
        install=str(DEFAULT_INSTALL),
        tables=str(DEFAULT_TABLES),
        config=LadderConfig(decision_ticks=5, level=16, tower_level=16),
    )
    vector.reset(
        list(range(1, count + 1)),
        list(DECK),
        list(DECK),
        list(DECK_SLOTS),
        list(DECK_SLOTS),
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
            if rng.random() >= 0.3:
                continue
            legal = np.flatnonzero(vector.masks[lane, seat, 1:first_ability])
            if legal.size:
                actions[lane, seat] = int(legal[0]) + 1
    vector.step(actions)


def test_vectorized_facts_match_the_bridge_decoders(crforge_runtime: None) -> None:
    catalog = Catalog.load()
    with _vector() as vector:
        encoder = VectorObservationEncoder(vector, catalog)
        batch = encoder.batch()
        encoder.reset()
        encoder.encode(batch)
        assert compare_lane_facts(encoder, batch, [0, 1, 2, 3]) == []
        for step in range(12):
            _step(vector, step + 1)
            encoder.encode(batch)
        mismatches = compare_lane_facts(encoder, batch, [0, 1, 2, 3])
        assert mismatches == [], mismatches


def test_placement_rows_view_the_native_mask(crforge_runtime: None) -> None:
    catalog = Catalog.load()
    with _vector() as vector:
        encoder = VectorObservationEncoder(vector, catalog)
        batch = encoder.encode()
        for seat in (0, 1):
            rows = batch.placement_rows(seat)
            assert rows.shape == (vector.count, 4, vector.schema.rows, vector.schema.cols)
            # A view, not a copy: a later encode must refresh it in place.
            assert np.shares_memory(rows, batch.masks)
            expected = batch.masks[:, seat, 1 : 1 + 4 * vector.schema.cols * vector.schema.rows].reshape(
                vector.count, 4, vector.schema.rows, vector.schema.cols
            )
            assert np.array_equal(rows, expected)
        before = int(batch.placement_rows(0).sum())
        _step(vector, 3)
        encoder.encode(batch)
        after = int(batch.placement_rows(0).sum())
        assert isinstance(before, int) and isinstance(after, int)


def test_tower_rows_follow_the_fixed_layout(crforge_runtime: None) -> None:
    catalog = Catalog.load()
    with _vector() as vector:
        encoder = VectorObservationEncoder(vector, catalog)
        batch = encoder.encode()
        assert batch.tower_valid.all()
        kinds = [0 if kind == "king" else (1 if kind == "princess_left" else 2) for _, _, kind in TOWER_LAYOUT]
        assert batch.tower_kind[0].tolist() == kinds
        assert batch.tower_owner[0].tolist() == [owner for _, owner, _ in TOWER_LAYOUT]
        assert batch.tower_entity_id[0].tolist() == [entity_id for entity_id, _, _ in TOWER_LAYOUT]
        # King, left, right for each owner, matching that owner's own header.
        for owner in (0, 1):
            for index, tower_index in enumerate((0, 1, 2)):
                entity_id = owner * 3 + tower_index
                assert int(batch.tower_hp[0, entity_id]) == int(batch.own_tower_hp[0, owner, tower_index])


def test_ability_facts_mirror_the_native_header(crforge_runtime: None) -> None:
    """Both seats' champion slots come straight from the seat header."""

    catalog = Catalog.load()
    with _vector(count=2) as vector:
        encoder = VectorObservationEncoder(vector, catalog)
        batch = encoder.encode()
        header = vector.schema.header
        for seat in (0, 1):
            for slot in range(vector.schema.ability_slots):
                observation = vector.observations[0, seat]
                assert int(batch.ability_champion_vocab[0, seat, slot]) == int(
                    observation[header["ability_champion"][slot]]
                )
                assert int(batch.ability_unit[0, seat, slot]) == int(
                    observation[header["ability_unit"][slot]]
                )
                assert int(batch.ability_state[0, seat, slot]) == int(
                    observation[header["ability_state"][slot]]
                )
                assert int(batch.ability_ticks[0, seat, slot]) == int(
                    observation[header["ability_command_ticks"][slot]]
                )


def test_derived_age_and_velocity_follow_the_frame(crforge_runtime: None) -> None:
    catalog = Catalog.load()
    with _vector(count=2) as vector:
        encoder = VectorObservationEncoder(vector, catalog)
        first = encoder.encode()
        first_tick = first.tick[:, 0].copy()
        first_valid = first.entity_valid.copy()
        first_ids = first.entity_id.copy()
        first_x = first.entity_x.copy()
        first_y = first.entity_y.copy()
        # The initial scene is born on the first encoded tick.
        assert int(first.entity_age_ms[first_valid].max()) == 0

        for step in range(8):
            _step(vector, 40 + step)
        second = encoder.encode()
        raw_delta = second.tick[:, 0] - first_tick
        for lane in range(vector.count):
            for slot in np.flatnonzero(second.entity_valid[lane]):
                if not first_valid[lane, slot] or first_ids[lane, slot] != second.entity_id[lane, slot]:
                    assert not bool(second.entity_velocity_valid[lane, slot])
                    continue
                delta = int(raw_delta[lane])
                assert int(second.entity_age_ms[lane, slot]) == delta * 50
                if delta:
                    expected_x = (int(second.entity_x[lane, slot]) - int(first_x[lane, slot])) / delta
                    expected_y = (int(second.entity_y[lane, slot]) - int(first_y[lane, slot])) / delta
                    assert bool(second.entity_velocity_valid[lane, slot])
                    assert np.isclose(float(second.entity_velocity[lane, slot, 0]), expected_x)
                    assert np.isclose(float(second.entity_velocity[lane, slot, 1]), expected_y)


def test_one_lane_autoreset_restarts_its_clock_and_derived_state(crforge_runtime: None) -> None:
    """A single-lane reset must not move the other lanes' tick or entity ages."""

    catalog = Catalog.load()
    with _vector(count=2) as vector:
        encoder = VectorObservationEncoder(vector, catalog)
        first = encoder.encode()
        assert first.tick[:, 0].tolist() == [0, 0]
        for step in range(12):
            _step(vector, 60 + step)
        before = encoder.encode()
        before_ticks = before.tick[:, 0].copy()
        assert before_ticks.min() > 0
        before_lane1_entities = before.entity_id[1].copy()

        vector.reset_env(0, 9_999)
        after = encoder.encode()
        # The reset lane restarts at tick zero; the other lane keeps its clock.
        assert int(after.tick[0, 0]) == 0
        assert int(after.tick[1, 0]) >= int(before_ticks[1])
        # Derived state restarted with the episode: no inherited age or velocity.
        valid = after.entity_valid[0]
        assert int(after.entity_age_ms[0][valid].max()) == 0
        assert not bool(after.entity_velocity_valid[0].any())
        # The surviving lane's scene is untouched by the other lane's reset.
        assert after.entity_valid[1].sum() == before.entity_valid[1].sum()
        assert after.entity_id[1][before.entity_valid[1]].tolist() == before_lane1_entities[
            before.entity_valid[1]
        ].tolist()

        # The per-lane clock also survives another encode of the reset episode.
        for _ in range(6):
            _step(vector, 70)
        later = encoder.encode()
        assert int(later.tick[0, 0]) >= 0
        assert int(later.tick[1, 0]) > int(after.tick[1, 0])
