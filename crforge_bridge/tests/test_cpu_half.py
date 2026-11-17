"""The CPU half's shared conversions: one table, one interleaved write.

These are the pieces ``tools/crforge/self_play.py`` uses to keep the per-decision
Python work off the critical path (``ROADMAP_CRFORGE.md`` §4): the candidate
columns are converted once per decision, the action record is written in one
NumPy pass instead of one tensor op per lane, and the two actor seats are
interleaved straight into the destination.  None of them needs an engine, so the
tests build the records directly.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from types import SimpleNamespace

import numpy as np
import torch

from crforge_bridge.vector_actions import (
    CANDIDATE_DEPLOY,
    CandidateTables,
    VectorActionDecoder,
    candidate_tables,
)
from crforge_bridge.vector_tensorize import VectorTensorizer, interleave_rows
from native_runner.training.v4.config import ModelConfigV4
from native_runner.training.v4.tensors import TARGET_GRID, ActionCandidatesV4, ActionSequenceV4

CONFIG = ModelConfigV4()


def _candidates(lanes: int, width: int = 4, **overrides: object) -> ActionCandidatesV4:
    values: dict[str, torch.Tensor] = {
        "mask": torch.zeros((lanes, width), dtype=torch.bool),
        "elixir": torch.zeros(lanes, dtype=torch.float32),
        "uid": torch.zeros((lanes, width), dtype=torch.long),
        "variant": torch.zeros((lanes, width), dtype=torch.long),
        "visible_card_vocab_id": torch.zeros((lanes, width), dtype=torch.long),
        "effective_card_vocab_id": torch.zeros((lanes, width), dtype=torch.long),
        "effective_form": torch.zeros((lanes, width), dtype=torch.long),
        "own_card_row": torch.full((lanes, width), -1, dtype=torch.long),
        "source_group_index": torch.full((lanes, width), -1, dtype=torch.long),
        "source_child_index": torch.full((lanes, width), -1, dtype=torch.long),
        "ability_vocab_id": torch.zeros((lanes, width), dtype=torch.long),
        "cost": torch.zeros((lanes, width), dtype=torch.float32),
        "runtime_features": torch.zeros((lanes, width, CONFIG.candidate_runtime_feature_dim)),
        "target_mode": torch.zeros((lanes, width), dtype=torch.long),
        "placement": torch.zeros((lanes, width, CONFIG.board_height, CONFIG.board_width), dtype=torch.bool),
        "is_building": torch.zeros((lanes, width), dtype=torch.bool),
        "building_half_width": torch.zeros((lanes, width), dtype=torch.float32),
        "building_half_height": torch.zeros((lanes, width), dtype=torch.float32),
        "building_offset_x": torch.zeros((lanes, width), dtype=torch.float32),
        "building_offset_y": torch.zeros((lanes, width), dtype=torch.float32),
        "exclusion_group_id": torch.full((lanes, width), -1, dtype=torch.long),
        "native_hand_slot": torch.full((lanes, width), -1, dtype=torch.long),
        "native_source_entity": torch.full((lanes, width), -1, dtype=torch.long),
        "native_visible_card_id": torch.full((lanes, width), -1, dtype=torch.long),
    }
    values.update({name: value for name, value in overrides.items()})  # type: ignore[arg-type]
    return ActionCandidatesV4(**values)


def _play_candidate(record: ActionCandidatesV4, lane: int, row: int, *, uid: int) -> None:
    record.mask[lane, row] = True
    record.uid[lane, row] = uid
    record.variant[lane, row] = CANDIDATE_DEPLOY
    record.target_mode[lane, row] = TARGET_GRID
    record.native_hand_slot[lane, row] = row
    record.effective_card_vocab_id[lane, row] = 100 + row
    record.effective_form[lane, row] = row


def _batch(lanes: int, record: ActionCandidatesV4) -> SimpleNamespace:
    return SimpleNamespace(
        count=lanes,
        batch_size=lanes,
        hand_slots=4,
        masks=np.zeros((lanes, 2, 4 * CONFIG.board_height * CONFIG.board_width + 1), dtype=np.uint8),
        candidates=record,
        groups=SimpleNamespace(
            position=torch.zeros((lanes, 2, 2), dtype=torch.float32),
            child_position=torch.zeros((lanes, 2, 2), dtype=torch.float32),
        ),
    )


def _sequence(lanes: int, record: ActionCandidatesV4, *, rows: np.ndarray, gates: np.ndarray) -> ActionSequenceV4:
    steps = int(CONFIG.max_micro_actions)
    count = np.zeros((lanes,), dtype=np.int64)
    uid = np.full((lanes, steps), -1, dtype=np.int64)
    cell = np.full((lanes, steps), -1, dtype=np.int64)
    delay = np.full((lanes, steps), -1, dtype=np.int64)
    for lane in range(lanes):
        if not gates[lane]:
            continue
        count[lane] = 1
        uid[lane, 0] = int(record.uid[lane, rows[lane]])
        cell[lane, 0] = 0
        delay[lane, 0] = 0
    return ActionSequenceV4(
        gate=torch.from_numpy(gates.astype(np.int64)),
        micro_action_count=torch.from_numpy(count),
        candidate_index=torch.from_numpy(rows.astype(np.int64).reshape(lanes, 1).repeat(steps, axis=1)),
        candidate_uid=torch.from_numpy(uid),
        target_cell=torch.from_numpy(cell),
        delay_offset_bin=torch.from_numpy(delay),
    )


def _records_tensorizer() -> VectorTensorizer:
    """A tensorizer without an engine: ``record_action`` only needs the config."""

    tensorizer = VectorTensorizer.__new__(VectorTensorizer)
    tensorizer.config = CONFIG
    tensorizer._previous_action = None
    return tensorizer


def test_candidate_tables_are_the_records_own_columns() -> None:
    record = _candidates(3)
    _play_candidate(record, 1, 2, uid=77)
    tables = candidate_tables(record)

    assert isinstance(tables, CandidateTables)
    assert tables.lanes == 3
    for name in (
        "mask",
        "uid",
        "variant",
        "target_mode",
        "native_hand_slot",
        "visible_card_vocab_id",
        "effective_card_vocab_id",
        "effective_form",
        "ability_vocab_id",
        "source_child_index",
        "source_group_index",
    ):
        assert np.array_equal(getattr(tables, name), getattr(record, name).numpy()), name
    assert bool(tables.mask[1, 2])
    assert int(tables.uid[1, 2]) == 77


def test_the_shared_tables_decode_is_the_converted_decode() -> None:
    """Passing the tables a caller already converted cannot change the plan."""

    lanes = 3
    record = _candidates(lanes)
    _play_candidate(record, 0, 1, uid=11)
    _play_candidate(record, 2, 3, uid=33)
    batch = _batch(lanes, record)
    tensorizer = SimpleNamespace(
        config=CONFIG,
        perspective=SimpleNamespace(flip_x=False, flip_y=False),
        actor_owner=0,
    )
    decoder = VectorActionDecoder(tensorizer)
    rows = np.array([1, 0, 3])
    sequence = _sequence(lanes, record, rows=rows, gates=np.array([True, False, True]))

    fresh = decoder.decode(batch, sequence, record, seat=0)
    shared = decoder.decode(batch, sequence, record, seat=0, tables=candidate_tables(record))

    for name in ("actions", "offsets", "candidate_rows", "masked", "steps"):
        assert np.array_equal(getattr(fresh, name), getattr(shared, name)), name
    assert fresh.actions[:, 0].tolist()[0] > 0
    assert fresh.actions[1, 0] == 0  # WAIT


def _reference_record_action(
    tensorizer: VectorTensorizer, sequence: ActionSequenceV4, batch: SimpleNamespace
) -> object:
    """The pre-optimisation per-lane loop, kept as the oracle for this test."""

    from crforge_bridge.vector_tensorize import VectorTensorizer as _Rows

    lanes = int(batch.count)
    previous = _Rows._empty_previous_action(tensorizer, lanes)
    previous.gate = sequence.gate.detach().cpu().clone()
    previous.micro_action_count = sequence.micro_action_count.detach().cpu().clone()
    mask = batch.candidates.mask.numpy()
    uid = batch.candidates.uid.numpy()
    for lane in range(lanes):
        count = int(sequence.micro_action_count[lane].item())
        for step in range(count):
            candidate_uid = int(sequence.candidate_uid[lane, step].item())
            matches = np.flatnonzero(mask[lane] & (uid[lane] == candidate_uid))
            assert matches.size == 1
            row = int(matches[0])
            for name in (
                "variant",
                "visible_card_vocab_id",
                "effective_card_vocab_id",
                "effective_form",
                "ability_vocab_id",
            ):
                getattr(previous, name)[lane, step] = getattr(batch.candidates, name)[lane, row].cpu()
            previous.target_cell[lane, step] = sequence.target_cell[lane, step].cpu()
            previous.delay_offset_bin[lane, step] = sequence.delay_offset_bin[lane, step].cpu()
            previous.action_mask[lane, step] = True
    return previous


def test_the_vectorised_record_is_the_per_lane_record_field_for_field() -> None:
    lanes = 4
    record = _candidates(lanes)
    for lane in range(lanes):
        _play_candidate(record, lane, lane, uid=1000 + lane)
    batch = _batch(lanes, record)
    rows = np.arange(lanes)
    sequence = _sequence(lanes, record, rows=rows, gates=np.array([True, True, False, True]))

    expected = _reference_record_action(_records_tensorizer(), sequence, batch)
    tensorizer = _records_tensorizer()
    tensorizer.record_action(sequence, batch, validate=False, tables=candidate_tables(record))
    actual = tensorizer.previous_action

    assert actual is not None
    for item in fields(actual):
        assert torch.equal(getattr(actual, item.name), getattr(expected, item.name)), item.name
    # Row 2 waited: its record must stay empty.
    assert not bool(actual.action_mask[2].any())
    assert int(actual.effective_card_vocab_id[1, 0]) == 101


def test_the_record_resolves_an_ability_candidates_source_position() -> None:
    """The ability path is not the producer's envelope, but it must not rot."""

    from native_runner.training.v4.tensors import CANDIDATE_ABILITY

    lanes = 2
    record = _candidates(lanes)
    record.mask[0, 0] = True
    record.uid[0, 0] = 4711
    record.variant[0, 0] = CANDIDATE_ABILITY
    record.native_hand_slot[0, 0] = 0
    record.source_child_index[0, 0] = 1
    batch = _batch(lanes, record)
    batch.groups.child_position[0, 1] = torch.tensor([0.25, 0.75])

    sequence = _sequence(lanes, record, rows=np.array([0, 0]), gates=np.array([True, False]))
    assert int(sequence.micro_action_count[0]) == 1
    tensorizer = _records_tensorizer()
    tensorizer.record_action(sequence, batch, validate=False)
    previous = tensorizer.previous_action

    assert previous is not None
    assert bool(previous.source_mask[0, 0])
    assert torch.allclose(previous.source_position[0, 0], torch.tensor([0.25, 0.75]))
    assert not bool(previous.source_mask[1].any())


@dataclass
class _NestedRecord:
    mask: torch.Tensor


@dataclass
class _SeatRecord:
    values: torch.Tensor
    nested: _NestedRecord


@dataclass
class _EdgeRecord:
    edges: torch.Tensor
    mask: torch.Tensor


def test_interleave_writes_lane_seat_row_order() -> None:
    seat0 = _SeatRecord(
        values=torch.arange(6, dtype=torch.float32).reshape(3, 2),
        nested=_NestedRecord(mask=torch.ones((3,), dtype=torch.bool)),
    )
    seat1 = _SeatRecord(
        values=torch.arange(6, 12, dtype=torch.float32).reshape(3, 2),
        nested=_NestedRecord(mask=torch.zeros((3,), dtype=torch.bool)),
    )
    stacked = interleave_rows([seat0, seat1])

    assert tuple(stacked.values.shape) == (6, 2)
    assert stacked.values[:, 0].tolist() == [0, 6, 2, 8, 4, 10]
    assert stacked.nested.mask.tolist() == [True, False, True, False, True, False]


def test_interleave_pads_a_shorter_variable_section_and_leaves_it_masked() -> None:
    long = _EdgeRecord(edges=torch.ones((2, 3), dtype=torch.long), mask=torch.ones((2, 3), dtype=torch.bool))
    short = _EdgeRecord(edges=torch.full((2, 1), 7, dtype=torch.long), mask=torch.ones((2, 1), dtype=torch.bool))
    stacked = interleave_rows([short, long], fill=0)

    assert tuple(stacked.edges.shape) == (4, 3)
    # Row 0 is the short seat's lane 0; the tail is padding, not data.
    assert stacked.edges[0].tolist() == [7, 0, 0]
    assert stacked.edges[1].tolist() == [1, 1, 1]
    assert stacked.edges[2].tolist() == [7, 0, 0]
