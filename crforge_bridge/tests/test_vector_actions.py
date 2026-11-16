"""The batched decode must agree with the stock per-lane decoder, seat by seat."""

from __future__ import annotations

import numpy as np
import pytest
import torch

from crforge_bridge.catalog import Catalog
from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
from crforge_bridge.vector_actions import (
    ENGINE_WAIT,
    VectorActionDecoder,
    engine_action_from_native,
    engine_action_index,
    engine_cell_from_game,
)
from crforge_bridge.vector_observation import VectorObservationEncoder
from crforge_bridge.vector_rows import reference_action_mask, reference_scalar_observation
from crforge_bridge.vector_tensorize import VectorTensorizer, lane_batch, lane_sequence
from native_runner.competitive_release import load_release
from native_runner.timeline import load_game_mode_timeline
from native_runner.training.v4.config import ModelConfigV4
from native_runner.training.v4.decoding import decode_action_sequence_v4
from native_runner.training.v4.factory import deck_roles_from_form_availability, production_semantic_bundle
from native_runner.training.v4.tensorizer import UniversalObservationTensorizerV4
from native_runner.training.v4.tensors import GATE_ACT, ActionSequenceV4

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


def _play_then_settle(vector, *, seed: int) -> None:
    """Open the game, play a few cards, then let pending plays run and elixir refill."""

    for step in range(60):
        _step(vector, seed, 0.0)
    for step in range(12):
        _step(vector, seed + 1 + step, 0.5)
    for step in range(12):
        _step(vector, seed + 100 + step, 0.0)


def _rows(vector, stock, seat: int):
    catalog = Catalog.load()
    encoder = VectorObservationEncoder(vector, catalog)
    encoder.reset()
    batch = encoder.encode()
    rows = VectorTensorizer(
        stock,
        vocabulary=list(vector.vocabulary),
        bridge_catalog=catalog,
        timeline=load_game_mode_timeline(STANDARD_GAME_MODE).timeline,
        tower_troop_id=TOWER_TROOP,
    )
    rows.reset(batch)
    return encoder, batch, rows


def _reference(stock, rows, batch, lane: int, seat: int):
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


def _sequence(candidates, config: ModelConfigV4, *, steps: int = 1, delay_bin: int = 0) -> ActionSequenceV4:
    """One sampled micro action per lane: the first legal candidate, first legal tile."""

    lanes = int(candidates.mask.shape[0])
    width = int(config.board_width)
    micro = int(config.max_micro_actions)
    gate = torch.zeros(lanes, dtype=torch.long)
    count = torch.zeros(lanes, dtype=torch.long)
    index = torch.full((lanes, micro), -1, dtype=torch.long)
    uid = torch.full((lanes, micro), -1, dtype=torch.long)
    target = torch.full((lanes, micro), -1, dtype=torch.long)
    delay = torch.full((lanes, micro), -1, dtype=torch.long)
    mask = candidates.mask.numpy()
    placement = candidates.placement.numpy()
    for lane in range(lanes):
        open_rows = np.flatnonzero(mask[lane])
        if open_rows.size < steps:
            continue
        gate[lane] = GATE_ACT
        count[lane] = steps
        for step in range(steps):
            row = int(open_rows[step])
            index[lane, step] = row
            uid[lane, step] = int(candidates.uid[lane, row])
            cells = np.argwhere(placement[lane, row])
            if cells.size:
                target[lane, step] = int(cells[0, 0]) * width + int(cells[0, 1])
            delay[lane, step] = 0 if step == 0 else delay_bin
    return ActionSequenceV4(
        gate=gate,
        micro_action_count=count,
        candidate_index=index,
        candidate_uid=uid,
        target_cell=target,
        delay_offset_bin=delay,
    )


def test_engine_cell_pins_the_seat_frame() -> None:
    """Game cell ``(6, 10)`` is seat ``(11, 10)`` for seat 0 and ``(6, 21)`` for seat 1."""

    assert engine_cell_from_game(6, 10, seat=0, cols=18, rows=32) == (11, 10)
    assert engine_cell_from_game(6, 10, seat=1, cols=18, rows=32) == (6, 21)
    assert engine_action_index(slot=0, col=11, row=10, cols=18, rows=32) == 1 + 10 * 18 + 11
    assert engine_action_index(slot=2, col=0, row=0, cols=18, rows=32) == 1 + 2 * 32 * 18
    with pytest.raises(ValueError):
        engine_cell_from_game(6, 10, seat=2, cols=18, rows=32)


@pytest.mark.parametrize("seat", [0, 1])
def test_decoded_engine_actions_match_the_stock_decoder(crforge_runtime: None, seat: int) -> None:
    """Every decoded index, offset and candidate row equals the per-lane decoder's."""

    stock = _tensorizer(seat)
    with _vector(count=2) as vector:
        encoder, batch, rows = _rows(vector, stock, seat)
        decoder = VectorActionDecoder(stock, schema=vector.schema)
        for _ in range(60):
            _step(vector, 1, 0.0)
        for warmup in range(15):
            _step(vector, 40 + warmup, 0.6)
        encoder.encode(batch)
        decoded_rows = 0
        for step in range(12):
            stacked = rows.tensorize(batch)
            sequence = _sequence(stacked.candidates, stock.config)
            plan = decoder.decode(batch, sequence, stacked.candidates, seat=seat)
            decoded_rows += int(plan.steps.sum())
            for lane in range(2):
                view = _reference(stock, rows, batch, lane, seat)
                decoded = decode_action_sequence_v4(
                    lane_sequence(sequence, lane),
                    lane_batch(stacked, lane).candidates,
                    row=0,
                    observation=view,
                    catalog=stock.catalog,
                    deck=stock.deck,
                    card_costs=stock.card_costs,
                    ability_id_by_vocab_id=stock.ability_id_by_vocab_id,
                    horizontal_mirror=stock.perspective.horizontal_mirror,
                    hand_slot_permutation=stock.perspective.hand_slot_permutation,
                    config=stock.config,
                    base_latency_ticks=1,
                    validate=False,
                )
                action = decoded.actions[0]
                expected = engine_action_from_native(action, cols=18, rows=32, seat=seat)
                assert int(plan.actions[lane, 0]) == expected
                if expected == ENGINE_WAIT:
                    assert int(plan.offsets[lane, 0]) == -1
                else:
                    assert int(plan.candidate_rows[lane, 0]) == int(sequence.candidate_index[lane, 0])
                    assert int(plan.offsets[lane, 0]) == int(action.metadata["execute_offset_ticks"])
                if expected > ENGINE_WAIT:
                    assert bool(plan.masked[lane, 0])
            for lane in range(2):
                rows.record_action(sequence, stacked, validate=False)
            _step(vector, 60 + step, 0.6)
            encoder.encode(batch)
        assert decoded_rows > 0


def test_second_micro_action_decodes_with_its_delay_offset(crforge_runtime: None) -> None:
    """Both slots carry an engine index; the second one carries its execute offset."""

    stock = _tensorizer(0)
    config = stock.config
    with _vector(count=1) as vector:
        encoder, batch, rows = _rows(vector, stock, 0)
        decoder = VectorActionDecoder(stock, schema=vector.schema)
        for _ in range(60):
            _step(vector, 7, 0.0)
        _play_then_settle(vector, seed=8)
        encoder.encode(batch)
        stacked = rows.tensorize(batch)
        candidates = stacked.candidates
        if int(candidates.mask[0].sum()) < 2:
            pytest.skip("this seed did not open two candidate rows for lane 0")
        sequence = _sequence(candidates, config, steps=2, delay_bin=2)
        if int(sequence.micro_action_count[0]) != 2:
            pytest.skip("lane 0 did not open two candidate rows")
        plan = decoder.decode(batch, sequence, candidates, seat=0)
        assert int(plan.steps[0]) == 2
        assert int(plan.actions[0, 0]) > ENGINE_WAIT
        assert int(plan.actions[0, 1]) > ENGINE_WAIT
        assert plan.actions[0, 0] != plan.actions[0, 1] or config.max_action_candidates > 1
        # delay_offset_ms[2] == 100 ms == two ticks; base latency is one tick.
        assert int(plan.offsets[0, 0]) == 1
        assert int(plan.offsets[0, 1]) == 1 + int(config.delay_offset_ms[2]) // 50
        assert int(plan.candidate_rows[0, 1]) == int(sequence.candidate_index[0, 1])


def test_wait_rows_decode_to_the_engine_wait(crforge_runtime: None) -> None:
    stock = _tensorizer(0)
    with _vector(count=3) as vector:
        encoder, batch, rows = _rows(vector, stock, 0)
        decoder = VectorActionDecoder(stock, schema=vector.schema)
        for _ in range(60):
            _step(vector, 11, 0.0)
        encoder.encode(batch)
        stacked = rows.tensorize(batch)
        sequence = ActionSequenceV4(
            gate=torch.zeros(3, dtype=torch.long),
            micro_action_count=torch.zeros(3, dtype=torch.long),
            candidate_index=torch.full((3, 2), -1, dtype=torch.long),
            candidate_uid=torch.full((3, 2), -1, dtype=torch.long),
            target_cell=torch.full((3, 2), -1, dtype=torch.long),
            delay_offset_bin=torch.full((3, 2), -1, dtype=torch.long),
        )
        plan = decoder.decode(batch, sequence, stacked.candidates, seat=0)
        assert plan.act.tolist() == [False, False, False]
        assert plan.actions[:, 0].tolist() == [ENGINE_WAIT] * 3
        assert plan.offsets[:, 0].tolist() == [-1] * 3
        assert not plan.masked.any()


def test_unknown_or_duplicated_uid_raises(crforge_runtime: None) -> None:
    stock = _tensorizer(0)
    with _vector(count=1) as vector:
        encoder, batch, rows = _rows(vector, stock, 0)
        decoder = VectorActionDecoder(stock, schema=vector.schema)
        for _ in range(60):
            _step(vector, 13, 0.0)
        _play_then_settle(vector, seed=14)
        encoder.encode(batch)
        stacked = rows.tensorize(batch)
        if not bool(stacked.candidates.mask.any()):
            pytest.skip("this seed opened no candidate for lane 0")
        sequence = _sequence(stacked.candidates, stock.config)
        sequence.candidate_uid[0, 0] = 123456789
        with pytest.raises(ValueError, match="missing or duplicated"):
            decoder.decode(batch, sequence, stacked.candidates, seat=0)


def test_a_closed_engine_mask_is_reported_not_repaired(crforge_runtime: None) -> None:
    """The decode returns the index the model chose and flags it; the caller decides."""

    stock = _tensorizer(0)
    with _vector(count=1) as vector:
        encoder, batch, rows = _rows(vector, stock, 0)
        decoder = VectorActionDecoder(stock, schema=vector.schema)
        for _ in range(60):
            _step(vector, 17, 0.0)
        _play_then_settle(vector, seed=18)
        encoder.encode(batch)
        stacked = rows.tensorize(batch)
        if not bool(stacked.candidates.mask.any()):
            pytest.skip("this seed opened no candidate for lane 0")
        sequence = _sequence(stacked.candidates, stock.config)
        plan = decoder.decode(batch, sequence, stacked.candidates, seat=0)
        index = int(plan.actions[0, 0])
        assert index > ENGINE_WAIT and bool(plan.masked[0, 0])
        saved = vector.masks[0, 0, index]
        try:
            vector.masks[0, 0, index] = 0
            closed = decoder.decode(batch, sequence, stacked.candidates, seat=0)
        finally:
            vector.masks[0, 0, index] = saved
        assert int(closed.actions[0, 0]) == index
        assert not bool(closed.masked[0, 0])
