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
