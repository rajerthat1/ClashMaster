"""The facts-only tensorize must be bit-identical to the per-lane tensorizer."""

from __future__ import annotations

import numpy as np
import pytest
import torch

from crforge_bridge.catalog import Catalog
from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
from crforge_bridge.vector_observation import VectorObservationEncoder
from crforge_bridge.vector_rows import reference_action_mask, reference_scalar_observation
from crforge_bridge.vector_tensorize import (
    VectorTensorizer,
    batch_digest,
    compare_semantic_batches,
    lane_batch,
    lane_sequence,
)
from native_runner.competitive_release import load_release
from native_runner.timeline import load_game_mode_timeline
from native_runner.training.v4.config import ModelConfigV4
from native_runner.training.v4.factory import deck_roles_from_form_availability, production_semantic_bundle
from native_runner.training.v4.tensorizer import UniversalObservationTensorizerV4
from native_runner.training.v4.tensors import GATE_ACT, GATE_WAIT, ActionSequenceV4

# The locked X-Bow learner deck (HANDOFF.md 2026-11-15).
LEARNER_DECK = (27000008, 27000006, 26000001, 26000000, 26000010, 26000084, 28000000, 28000011)
LEARNER_FORMS = (0, 1, 1, 2, 0, 0, 0, 0)
LEARNER_NAMES = ("Xbow", "Tesla", "Archer", "Knight", "Skeletons", "ElectroSpirit", "Fireball", "Log")
TOWER_TROOP = 159000000
RELEASE = "16.635.1"
STANDARD_GAME_MODE = 72_000_006


def _tensorizer(seat: int = 0) -> UniversalObservationTensorizerV4:
    bundle = production_semantic_bundle(RELEASE)
    identity = load_release(RELEASE).identity
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
        config=ModelConfigV4(
            ability_vocab_size=identity.ability_vocab_size,
            child_archetype_count=identity.child_archetype_count,
        ),
        reject_unknown_public_semantics=True,
    )


def _vector(count: int = 2):
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


def _step(vector, seed: int, play_rate: float) -> None:
    rng = np.random.default_rng(seed)
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


def _rows(vector, tensorizer):
    catalog = Catalog.load()
    encoder = VectorObservationEncoder(vector, catalog)
    encoder.reset()
    batch = encoder.encode()
    rows = VectorTensorizer(
        tensorizer,
        vocabulary=list(vector.vocabulary),
        bridge_catalog=catalog,
        timeline=load_game_mode_timeline(STANDARD_GAME_MODE).timeline,
        tower_troop_id=TOWER_TROOP,
    )
    rows.reset(batch)
    return encoder, batch, rows


def _reference(stock, rows, batch, lane: int, seat: int = 0):
    return reference_scalar_observation(
        batch,
        lane,
        tables=rows.tables,
        tensorizer=stock,
        timeline=rows.scalar_builder.timeline,
        tower_troop_id=TOWER_TROOP,
        seat=int(seat),
        action_mask=reference_action_mask(batch, lane, tensorizer=stock, seat=int(seat), model_frame=False),
        episode_id=f"lane{lane}",
    )


def _action_sequence(candidates, config: ModelConfigV4) -> ActionSequenceV4:
    lanes = int(candidates.mask.shape[0])
    steps = int(config.max_micro_actions)
    gate = torch.zeros(lanes, dtype=torch.long)
    count = torch.zeros(lanes, dtype=torch.long)
    index = torch.full((lanes, steps), -1, dtype=torch.long)
    uid = torch.full((lanes, steps), -1, dtype=torch.long)
    target = torch.full((lanes, steps), -1, dtype=torch.long)
    delay = torch.full((lanes, steps), -1, dtype=torch.long)
    mask = candidates.mask.numpy()
    placement = candidates.placement.numpy()
    for lane in range(lanes):
        rows = np.flatnonzero(mask[lane])
        if rows.size == 0:
            continue
        row = int(rows[0])
        gate[lane] = GATE_ACT
        count[lane] = 1
        index[lane, 0] = row
        uid[lane, 0] = candidates.uid[lane, row]
        cells = np.argwhere(placement[lane, row])
        if cells.size:
            target[lane, 0] = int(cells[0, 0]) * int(config.board_width) + int(cells[0, 1])
        delay[lane, 0] = 0
    return ActionSequenceV4(
        gate=gate,
        micro_action_count=count,
        candidate_index=index,
        candidate_uid=uid,
        target_cell=target,
        delay_offset_bin=delay,
    )


@pytest.mark.parametrize("seat", [0, 1])
def test_facts_tensorize_is_digest_identical_over_a_played_sequence(
    crforge_runtime: None, seat: int
) -> None:
    """Every lane of the stacked record equals the actor's stock tensorizer, row for row."""

    tensorizer = _tensorizer(seat)
    config = tensorizer.config
    with _vector(count=2) as vector:
        encoder, batch, rows = _rows(vector, tensorizer)
        stock_by_lane = {}
        for lane in range(2):
            stock = _tensorizer(seat)
            stock.start_episode(_reference(stock, rows, batch, lane, seat))
            stock_by_lane[lane] = stock
        for _ in range(60):
            _step(vector, 1, 0.0)
        encoder.encode(batch)

        recorded = 0
        for step in range(10):
            stacked = rows.tensorize(batch, validate=(step == 0))
            for lane in range(2):
                stock = stock_by_lane[lane]
                expected = stock.tensorize(_reference(stock, rows, batch, lane, seat), validate=False)
                actual = lane_batch(stacked, lane)
                assert compare_semantic_batches(actual, expected) == []
                assert batch_digest(actual) == batch_digest(expected)
            sequence = _action_sequence(stacked.candidates, config)
            rows.record_action(sequence, stacked, validate=True)
            for lane in range(2):
                stock_by_lane[lane].record_action(
                    lane_sequence(sequence, lane),
                    stock_by_lane[lane].tensorize(
                        _reference(stock_by_lane[lane], rows, batch, lane, seat), validate=False
                    ),
                    row=0,
                    validate=True,
                )
            recorded += int(sequence.micro_action_count.sum())
            _step(vector, 100 + step, 1.0)
            encoder.encode(batch)
        assert recorded > 0


def test_lane_batch_trims_only_the_variable_length_relation_set(crforge_runtime: None) -> None:
    """Fixed sections keep V4 capacity; the relation record keeps its live rows."""

    tensorizer = _tensorizer()
    config = tensorizer.config
    with _vector(count=3) as vector:
        encoder, batch, rows = _rows(vector, tensorizer)
        for step in range(6):
            _step(vector, 200 + step, 1.0)
            encoder.encode(batch)
        stacked = rows.tensorize(batch, validate=True)
        for lane in range(3):
            slotted = lane_batch(stacked, lane)
            assert slotted.batch_size == 1
            assert tuple(slotted.candidates.mask.shape) == (1, int(config.max_action_candidates))
            assert tuple(slotted.active_effects.mask.shape) == (1, 0)
            assert tuple(slotted.events.mask.shape) == (1, int(config.max_recent_events))
            count = max(1, int(stacked.relation_edges.mask[lane].sum()))
            assert tuple(slotted.relation_edges.mask.shape) == (1, count)
            assert torch.equal(
                slotted.relation_edges.source,
                stacked.relation_edges.source[lane : lane + 1, :count],
            )
            assert torch.equal(slotted.match_scalars, stacked.match_scalars[lane : lane + 1])


def test_record_action_resolves_each_lane_against_its_own_candidates(crforge_runtime: None) -> None:
    """One stacked sequence updates every lane's previous-action record."""

    tensorizer = _tensorizer()
    config = tensorizer.config
    with _vector(count=4) as vector:
        encoder, batch, rows = _rows(vector, tensorizer)
        for _ in range(60):
            _step(vector, 300, 0.0)
        encoder.encode(batch)
        stacked = rows.tensorize(batch)
        sequence = _action_sequence(stacked.candidates, config)
        rows.record_action(sequence, stacked, validate=True)
        previous = rows.previous_action
        assert previous is not None
        for lane in range(4):
            expected_gate = int(sequence.gate[lane])
            assert int(previous.gate[lane]) == expected_gate
            assert int(previous.micro_action_count[lane]) == int(sequence.micro_action_count[lane])
            if expected_gate == GATE_WAIT:
                assert not bool(previous.action_mask[lane].any())
                continue
            row = int(sequence.candidate_index[lane, 0])
            assert int(previous.variant[lane, 0]) == int(stacked.candidates.variant[lane, row])
            assert int(previous.effective_card_vocab_id[lane, 0]) == int(
                stacked.candidates.effective_card_vocab_id[lane, row]
            )
            assert int(previous.target_cell[lane, 0]) == int(sequence.target_cell[lane, 0])
            assert bool(previous.action_mask[lane, 0])
            assert not bool(previous.source_mask[lane, 0])


def test_stacked_record_validation_rejects_a_broken_lane(crforge_runtime: None) -> None:
    """The stacked record runs the model's own padding/shape contract."""

    tensorizer = _tensorizer()
    with _vector(count=2) as vector:
        _encoder, batch, rows = _rows(vector, tensorizer)
        stacked = rows.tensorize(batch, validate=True)
        broken = lane_batch(stacked, 0)
        broken.match_scalars[0, 0] = float("nan")
        assert "match_scalars.values" in compare_semantic_batches(broken, lane_batch(stacked, 0))


def test_reset_rows_clears_only_the_lanes_that_started_a_new_episode(crforge_runtime: None) -> None:
    """A vector autoreset is per lane, so the previous action must reset per lane."""

    tensorizer = _tensorizer()
    config = tensorizer.config
    with _vector(count=3) as vector:
        encoder, batch, rows = _rows(vector, tensorizer)
        for _ in range(60):
            _step(vector, 500, 0.0)
        encoder.encode(batch)
        stacked = rows.tensorize(batch)
        sequence = _action_sequence(stacked.candidates, config)
        if int(sequence.micro_action_count.sum()) == 0:
            pytest.skip("this seed opened no candidate")
        rows.record_action(sequence, stacked, validate=True)
        previous = rows.previous_action
        assert previous is not None
        kept = int(sequence.gate[1])
        kept_count = int(sequence.micro_action_count[1])
        before_gate = previous.gate.clone()
        before_target = previous.target_cell.clone()

        rows.reset_rows(batch, np.array([True, False, False]))
        reset = rows.previous_action
        assert reset is not None
        assert int(reset.gate[0]) == GATE_WAIT
        assert int(reset.micro_action_count[0]) == 0
        assert not bool(reset.action_mask[0].any())
        assert int(reset.gate[1]) == kept
        assert int(reset.micro_action_count[1]) == kept_count
        assert torch.equal(reset.gate[1:], before_gate[1:])
        assert torch.equal(reset.target_cell[1:], before_target[1:])

        with pytest.raises(ValueError):
            rows.reset_rows(batch, np.array([True, False]))


def test_recorded_action_from_inference_mode_can_be_reset_outside_it(crforge_runtime: None) -> None:
    """The previous-action record must be a normal tensor: collection samples in inference mode."""

    tensorizer = _tensorizer()
    config = tensorizer.config
    with _vector(count=2) as vector:
        encoder, batch, rows = _rows(vector, tensorizer)
        for _ in range(60):
            _step(vector, 700, 0.0)
        encoder.encode(batch)
        stacked = rows.tensorize(batch)
        sequence = _action_sequence(stacked.candidates, config)
        if int(sequence.micro_action_count.sum()) == 0:
            pytest.skip("this seed opened no candidate")
        with torch.inference_mode():
            rows.record_action(sequence, stacked, validate=True)
        previous = rows.previous_action
        assert previous is not None
        assert not previous.gate.is_inference()
        assert not previous.micro_action_count.is_inference()

        # The producer's per-lane autoreset happens outside inference mode.
        rows.reset_rows(batch, np.array([True, False]))

        # A second sampling step must not resurrect an inference tensor either.
        with torch.inference_mode():
            rows.record_action(sequence, stacked, validate=False)
        assert not rows.previous_action.gate.is_inference()
        rows.reset_rows(batch, np.array([False, True]))
