"""Batched decode: a stacked V4 action row -> the vector engine's action index.

C3-B ends at the stacked :class:`UniversalSemanticBatchV4`: the model can read
256 lanes of crforge facts, but nothing yet turns its sampled
:class:`ActionSequenceV4` back into something a ``VectorBattle`` can step.  This
module is that half of the C4 producer.

Three frames meet here, and they are easy to confuse:

* the **model** frame is what the tensorizer emits.  Seat 0 reads the game frame
  unchanged; seat 1 reads it rotated 180 degrees, which is why the stock
  ``PerspectiveTransformV1`` sets ``flip_x``/``flip_y`` for owner 1;
* the **game** frame is the native probe's frame (x grows to the right from seat
  0's view, y grows towards seat 0's own tower).  The facts layer writes it;
* the **engine seat** frame is the vector action index.  crforge mirrors x for
  seat 0 and y for seat 1 (``Schema.toSeatX/toSeatY``).

The net model -> engine mapping is therefore a **single x flip for both seats**
under the default perspective.  It is derived from the tensorizer's own
``perspective`` flags rather than hard-coded, so a non-default
``horizontal_mirror`` still decodes correctly: ``model -> game`` flips with
``perspective.flip_x``/``flip_y``; ``game -> engine`` flips x for seat 0 and y
for seat 1.  Both conversions are applied once per lane in one NumPy pass.

What the decode produces is the engine's flat action:

* ``0`` = wait;
* ``1 + slot * rows * cols + row * cols + col`` = play hand slot ``slot`` on the
  seat's ``(col, row)`` tile, exactly the encoding ``VectorBattle.actions`` and
  ``Schema.play`` use;
* ``schema.first_ability + champion_slot`` = an ability.

The bridge envelope emits **deploy candidates only** (no ability sources, no
Mirror runtime), so an ability or non-grid candidate raises instead of guessing.
Every micro-action of the V4 decision is decoded: slot ``k`` carries its engine
action and the tick offset at which the native path would execute it
(``base_latency_ticks + delay_offset_ms // tick_ms``), so a producer can step
one tick at a time and honour the second micro-action instead of dropping it.

The vector engine's mask stays authoritative.  ``decode`` reports, per micro
action, whether the chosen index is open in ``VectorBattle.masks``; a closed
index is a producer bug, not something to silently repair.

The gate is ``tools/crforge/vector_decode.py``: on the same facts it compares
every decoded index and offset with the stock per-lane
``decode_action_sequence_v4`` path, lane by lane, for both actor seats.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import torch

from native_runner.contracts import DEFAULT_TICK_MS as TICK_MS
from native_runner.contracts import ActionKind, TargetKind
from native_runner.training.v4.tensors import (
    CANDIDATE_ABILITY,
    CANDIDATE_DEPLOY,
    GATE_ACT,
    GATE_WAIT,
    TARGET_GRID,
    ActionSequenceV4,
)

# ``VectorBattle.actions`` reserves index 0 for "no play this tick".
ENGINE_WAIT = 0


@dataclass(frozen=True)
class VectorActionPlan:
    """One actor seat's decoded plan for every lane of one decision.

    ``actions[lane, step]`` is the engine action index for micro-action ``step``
    of that lane, ``-1`` where the lane has no such micro-action.  A waiting
    lane carries ``ENGINE_WAIT`` at step 0 with ``offsets`` -1.
    """

    seat: int
    actions: np.ndarray
    offsets: np.ndarray
    candidate_rows: np.ndarray
    masked: np.ndarray
    steps: np.ndarray

    @property
    def act(self) -> np.ndarray:
        """Lanes that carried at least one micro-action, ``(lanes,)``."""

        return np.asarray(self.steps > 0)


def engine_action_index(*, slot: int, col: int, row: int, cols: int, rows: int) -> int:
    """The vector engine's flat play index for one hand slot and seat tile."""

    if not 0 <= int(slot) < 4:
        raise ValueError(f"hand slot {slot} is outside 0..3")
    if not 0 <= int(col) < int(cols) or not 0 <= int(row) < int(rows):
        raise ValueError(f"seat tile ({col}, {row}) is outside {cols}x{rows}")
    return 1 + int(slot) * int(rows) * int(cols) + int(row) * int(cols) + int(col)


def engine_cell_from_game(col: int, row: int, *, seat: int, cols: int, rows: int) -> tuple[int, int]:
    """Game cell -> the engine's seat tile (``Schema.toSeatX/toSeatY``)."""

    if seat not in (0, 1):
        raise ValueError(f"seat {seat} is not a seat")
    engine_col = cols - 1 - int(col) if int(seat) == 0 else int(col)
    engine_row = rows - 1 - int(row) if int(seat) == 1 else int(row)
    return engine_col, engine_row


def engine_action_from_native(action: Any, *, cols: int, rows: int, seat: int) -> int:
    """The engine index of one stock ``ActionV1`` from the per-lane decoder.

    This is the oracle half of the gate.  The stock decoder emits ``ActionV1``
    grid targets in the **game** frame (``cell_to_world`` feeds the native
    command directly), while the vector action index names the **seat** tile, so
    the game -> seat conversion is applied here exactly as the engine does it.
    """

    kind = getattr(action, "kind", None)
    if kind == ActionKind.WAIT:
        return ENGINE_WAIT
    if kind != ActionKind.PLAY_CARD:
        raise ValueError(f"crforge vector actions do not model {kind}")
    if action.target_kind != TargetKind.GRID or action.target_grid is None:
        raise ValueError("crforge vector actions need a grid target")
    x, y = (int(value) for value in action.target_grid)
    col, row = engine_cell_from_game(x, y, seat=int(seat), cols=cols, rows=rows)
    return engine_action_index(slot=int(action.hand_slot), col=col, row=row, cols=cols, rows=rows)


def _candidate_record(section: Any) -> Any:
    """Accept a ``VectorCandidateSection`` or an ``ActionCandidatesV4`` record."""

    return getattr(section, "candidates", section)


def _numpy(value: Any) -> np.ndarray:
    if isinstance(value, torch.Tensor):
        return value.detach().cpu().numpy()
    return np.asarray(value)


class VectorActionDecoder:
    """Decode a stacked ``ActionSequenceV4`` into engine action indices, per seat."""

    def __init__(self, tensorizer: Any, *, schema: Any | None = None) -> None:
        self.tensorizer = tensorizer
        self.config = tensorizer.config
        self.perspective = tensorizer.perspective
        self.seat = int(tensorizer.actor_owner)
        self.cols = int(self.config.board_width)
        self.rows = int(self.config.board_height)
        self.first_ability = None if schema is None else int(schema.first_ability)

    def _engine_flip(self, seat: int) -> tuple[bool, bool]:
        """Whether the model -> engine mapping mirrors x / y for this seat.

        model -> game uses the tensorizer's perspective; game -> engine mirrors x
        for seat 0 and y for seat 1.  Composing them keeps one source of truth
        for the seat-1 rotation instead of a hard-coded table here.
        """

        if seat not in (0, 1):
            raise ValueError(f"fact seat {seat} is not a seat")
        flip_x = bool(self.perspective.flip_x) ^ (int(seat) == 0)
        flip_y = bool(self.perspective.flip_y) ^ (int(seat) == 1)
        return flip_x, flip_y

    def decode(
        self,
        batch: Any,
        sequence: ActionSequenceV4,
        candidates: Any,
        *,
        seat: int | None = None,
    ) -> VectorActionPlan:
        """Decode one stacked sequence for one actor seat.

        ``batch`` is the :class:`VectorObservationBatch` the sequence was built
        from: it supplies the lane count and the engine mask the indices are
        checked against.  ``candidates`` is that decision's
        ``VectorCandidateSection`` (or its ``ActionCandidatesV4`` record).
        """

        seat = self.seat if seat is None else int(seat)
        lanes = int(batch.count)
        if int(sequence.gate.shape[0]) != lanes:
            raise ValueError("action and observation batch sizes differ")
        steps = int(sequence.candidate_uid.shape[1])
        if steps != int(self.config.max_micro_actions):
            raise ValueError("action sequence micro-action width disagrees with the model config")

        gate = _numpy(sequence.gate)
        counts = _numpy(sequence.micro_action_count)
        cell_source = _numpy(sequence.target_cell)
        delay_source = _numpy(sequence.delay_offset_bin)
        uid_source = _numpy(sequence.candidate_uid)

        record = _candidate_record(candidates)
        mask = _numpy(record.mask)
        uid_table = _numpy(record.uid)
        variant_table = _numpy(record.variant)
        target_table = _numpy(record.target_mode)
        slot_table = _numpy(record.native_hand_slot)
        if mask.shape[0] != lanes:
            raise ValueError("candidate and observation batch sizes differ")

        if not np.isin(gate, (GATE_WAIT, GATE_ACT)).all():
            raise ValueError("action gate must be WAIT=0 or ACT=1")
        if np.any(counts < 0) or np.any(counts > steps):
            raise ValueError("action micro-action count is outside the sequence width")
        if np.any((gate == GATE_WAIT) & (counts != 0)):
            raise ValueError("a WAIT row carries micro-actions")

        flip_x, flip_y = self._engine_flip(seat)
        actions = np.full((lanes, steps), -1, dtype=np.int32)
        offsets = np.full((lanes, steps), -1, dtype=np.int32)
        rows_found = np.full((lanes, steps), -1, dtype=np.int64)
        actions[gate == GATE_WAIT, 0] = ENGINE_WAIT

        delay_bins = np.asarray(self.config.delay_offset_ms, dtype=np.int64)
        hand_slots = int(batch.hand_slots)
        for step in range(steps):
            active = (gate == GATE_ACT) & (counts > step)
            if not active.any():
                continue
            active_lanes = np.flatnonzero(active)
            uids = uid_source[active, step].astype(np.int64)
            matches = mask[active] & (uid_table[active] == uids[:, None])
            matches_per_row = matches.sum(axis=1)
            if np.any(matches_per_row != 1):
                bad = int(np.flatnonzero(matches_per_row != 1)[0])
                raise ValueError(
                    "previous action candidate UID is missing or duplicated: "
                    f"lane={int(active_lanes[bad])} step={step}"
                )
            chosen = matches.argmax(axis=1)
            rows_found[active_lanes, step] = chosen

            variants = variant_table[active_lanes, chosen]
            if np.any(variants != CANDIDATE_DEPLOY):
                bad = int(np.flatnonzero(variants != CANDIDATE_DEPLOY)[0])
                raise ValueError(
                    "crforge vector actions model deploy candidates only: "
                    f"lane={int(active_lanes[bad])} step={step} variant={int(variants[bad])} "
                    f"(ability={CANDIDATE_ABILITY})"
                )
            targets = target_table[active_lanes, chosen]
            if np.any(targets != TARGET_GRID):
                bad = int(np.flatnonzero(targets != TARGET_GRID)[0])
                raise ValueError(
                    f"crforge vector actions need a GRID target: lane={int(active_lanes[bad])} step={step}"
                )
            slots = slot_table[active_lanes, chosen].astype(np.int64)
            if np.any((slots < 0) | (slots >= hand_slots)):
                bad = int(np.flatnonzero((slots < 0) | (slots >= hand_slots))[0])
                raise ValueError(f"candidate has no native hand slot: lane={int(active_lanes[bad])} step={step}")

            cells = cell_source[active, step].astype(np.int64)
            outside = (cells < 0) | (cells >= self.cols * self.rows)
            if np.any(outside):
                bad = int(np.flatnonzero(outside)[0])
                raise ValueError(
                    f"target cell is outside the model grid: lane={int(active_lanes[bad])} step={step}"
                )
            model_col = cells % self.cols
            model_row = cells // self.cols
            engine_col = self.cols - 1 - model_col if flip_x else model_col
            engine_row = self.rows - 1 - model_row if flip_y else model_row

            bins = delay_source[active, step].astype(np.int64)
            bad_bin = (bins < 0) | (bins >= delay_bins.size)
            if np.any(bad_bin):
                bad = int(np.flatnonzero(bad_bin)[0])
                raise ValueError(
                    f"delay offset bin is outside the model config: lane={int(active_lanes[bad])} step={step}"
                )

            index = 1 + slots * self.rows * self.cols + engine_row * self.cols + engine_col
            if self.first_ability is not None and np.any(index >= self.first_ability):
                raise ValueError("decoded play index overlaps the engine ability block")
            actions[active_lanes, step] = index.astype(np.int32)
            # The native decoder's ``execute_offset_ticks``: one tick of base
            # latency plus the selected delay bin, in native ticks.
            offsets[active_lanes, step] = (1 + delay_bins[bins] // TICK_MS).astype(np.int32)

        played = actions > ENGINE_WAIT
        masked = np.zeros_like(played)
        if played.any():
            lane_rows, step_rows = np.nonzero(played)
            masked[lane_rows, step_rows] = (
                batch.masks[lane_rows, seat, actions[lane_rows, step_rows]] != 0
            )

        return VectorActionPlan(
            seat=seat,
            actions=actions,
            offsets=offsets,
            candidate_rows=rows_found,
            masked=masked,
            steps=counts.astype(np.int32),
        )


__all__ = [
    "ENGINE_WAIT",
    "VectorActionDecoder",
    "VectorActionPlan",
    "engine_action_from_native",
    "engine_action_index",
]
