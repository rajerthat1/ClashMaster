"""Attribute a refused crforge action to the lane, seat, tick and card that gave it.

The producer's C4 gate counts refusals (``illegal_peak``), but a count cannot be
debugged: the 50-update league run reported **one** illegal lane-action in
409,600 lane-decisions and named neither the lane nor the card.  The engine
already publishes the facts, per seat, in its observation header:

* ``illegal`` -- the seat's actions refused as illegal **in the episode so far**
  (cumulative, so a difference over one engine tick is the refusal count of that
  tick, and a negative difference is a lane autoreset, not a refusal);
* ``last_refusal`` -- why the seat's action of the last decision was refused, a
  refusal code (``Schema.refusals`` names it) or 0 for a play given / a wait;
* ``last_run_refusal`` -- the code of the seat's play refused at its run, or
  ``RUN_NOT_PLACED`` for a play the placement refused.

So one tick of the producer's window is enough to bind a refusal to the exact
action it gave: this module owns that binding (a pure function) plus the small
per-run trace the producer folds it into.  The producer reads the three headers
after every ``VectorBattle.step`` and calls :meth:`IllegalActionTrace.observe`;
an example is a JSON-ready dict that names the segment, decision, window tick,
lane, seat, engine action, micro-action step, hand slot, seat and game cell,
candidate card, and both refusal codes.

Why the header and not ``info["illegal_0"]``: ``VectorBattle``'s info record
reports ``illegalNow``, which is **this step's** refusal and is cleared on the
next step.  Session 53's producer read it once per decision, after the last tick
of a multi-tick window, so a refusal at an earlier tick was invisible.  The
header's cumulative count has no such blind spot.

Ported refusal codes: ``Schema.java`` @ ``fa5959`` (https://github.com/voonhous/crforge,
``H_LAST_REFUSAL``/``H_LAST_RUN_REFUSAL``/``H_ILLEGAL``, ``REFUSAL_MASKED``,
``REFUSAL_NOT_MODELLED``, ``RUN_NOT_PLACED``).  The engine action index itself is
``vector_actions.engine_action_index``'s encoding: ``0`` waits, ``1 + slot *
rows * cols + row * cols + col`` plays a hand slot, and the indices past
``first_ability`` are abilities.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

#: ``VectorBattle.actions`` index 0: no play this tick.
ENGINE_WAIT = 0
#: ``Schema.REFUSAL_NONE``.
REFUSAL_NONE = 0
#: ``Schema.REFUSAL_MASKED``: no giving makes this one.
REFUSAL_MASKED = 100
#: ``Schema.REFUSAL_NOT_MODELLED``: no giving makes this one.
REFUSAL_NOT_MODELLED = 101
#: ``Schema.RUN_NOT_PLACED``: a play whose placement found no place.
RUN_NOT_PLACED = 1000

_BUILTIN_REFUSALS = {
    REFUSAL_NONE: "none",
    REFUSAL_MASKED: "MASKED",
    REFUSAL_NOT_MODELLED: "NOT_MODELLED",
    RUN_NOT_PLACED: "RUN_NOT_PLACED",
}


def refusal_name(code: int, refusals: Mapping[int, str] | None = None) -> str:
    """The name of one refusal code (``Schema.refusals`` plus the built-ins)."""

    code = int(code)
    if refusals is not None and code in refusals:
        return str(refusals[code])
    return _BUILTIN_REFUSALS.get(code, str(code))


def decode_engine_play(
    index: int, *, cols: int, rows: int, hand_slots: int = 4
) -> tuple[int, int, int] | None:
    """Engine action index -> ``(hand_slot, seat_col, seat_row)``, or ``None``.

    ``None`` covers a wait and an ability (an ability index is past every play).
    ``seat_col``/``seat_row`` are the engine's own seat tile, the frame
    ``VectorActionPlan.actions`` is built in; :func:`game_cell` undoes the
    engine's mirror.
    """

    index = int(index)
    if index <= ENGINE_WAIT:
        return None
    width = int(rows) * int(cols)
    if width < 1:
        raise ValueError("a play needs a positive board")
    slot, cell = divmod(index - 1, width)
    if slot >= int(hand_slots):
        return None
    row, col = divmod(cell, int(cols))
    return slot, col, row


def game_cell(col: int, row: int, *, seat: int, cols: int, rows: int) -> tuple[int, int]:
    """Engine seat tile -> the game cell (the inverse of the engine's mirror).

    ``Schema.toSeatX``/``toSeatY``: seat 0 mirrors x, seat 1 mirrors y.  Both are
    involutions, so the call is its own inverse.
    """

    if seat not in (0, 1):
        raise ValueError(f"seat {seat} is not a seat")
    game_col = int(cols) - 1 - int(col) if int(seat) == 0 else int(col)
    game_row = int(rows) - 1 - int(row) if int(seat) == 1 else int(row)
    return game_col, game_row


def _candidate_int(tables: Any | None, name: str, lane: int, row: int) -> int | None:
    if tables is None or row < 0:
        return None
    values = getattr(tables, name, None)
    if values is None:
        return None
    array = np.asarray(values)
    if array.ndim != 2 or lane >= array.shape[0] or row >= array.shape[1]:
        return None
    return int(array[lane, row])


def attribute_illegal_action(
    *,
    segment: int,
    decision: int,
    window_tick: int,
    lane: int,
    seat: int,
    refused: int,
    action: int,
    micro_step: int,
    plan: Any | None,
    tables: Any | None,
    refusal_code: int,
    run_refusal_code: int,
    refusals: Mapping[int, str] | None,
    cols: int,
    rows: int,
) -> dict[str, Any]:
    """One refusal, named end to end.

    ``plan`` is the seat's :class:`crforge_bridge.vector_actions.VectorActionPlan`
    for this decision and ``tables`` its
    :class:`crforge_bridge.vector_actions.CandidateTables`; together they map the
    micro-action back to the candidate card the policy chose.  A missing plan or
    a step with no candidate leaves those fields ``None`` instead of guessing.
    """

    play = decode_engine_play(action, cols=cols, rows=rows)
    candidate_row = -1
    if plan is not None and micro_step >= 0:
        rows_found = np.asarray(plan.candidate_rows)
        if rows_found.ndim == 2 and lane < rows_found.shape[0] and micro_step < rows_found.shape[1]:
            candidate_row = int(rows_found[lane, micro_step])
    mask_open: bool | None = None
    if plan is not None and micro_step >= 0:
        masked = np.asarray(plan.masked)
        if masked.ndim == 2 and lane < masked.shape[0] and micro_step < masked.shape[1]:
            mask_open = bool(masked[lane, micro_step])
    event: dict[str, Any] = {
        "segment": int(segment),
        "decision": int(decision),
        "window_tick": int(window_tick),
        "lane": int(lane),
        "seat": int(seat),
        "refused": int(refused),
        "action": int(action),
        "micro_step": int(micro_step),
        "hand_slot": None if play is None else int(play[0]),
        "seat_cell": None if play is None else [int(play[1]), int(play[2])],
        "game_cell": (
            None if play is None else list(game_cell(play[1], play[2], seat=seat, cols=cols, rows=rows))
        ),
        "card_vocab_id": _candidate_int(tables, "effective_card_vocab_id", lane, candidate_row),
        "candidate_uid": _candidate_int(tables, "uid", lane, candidate_row),
        "hand_slot_reported": _candidate_int(tables, "native_hand_slot", lane, candidate_row),
        "engine_mask_open": mask_open,
        "refusal_code": int(refusal_code),
        "refusal": refusal_name(refusal_code, refusals),
        "run_refusal_code": int(run_refusal_code),
        "run_refusal": refusal_name(run_refusal_code, refusals),
    }
    return event


@dataclass
class IllegalActionTrace:
    """Fold each engine tick's header diff into bounded, attributable examples.

    ``count`` is the run's number of refusals (the sum of the header's positive
    differences, so a lane autoreset -- a drop to zero -- is not a refusal);
    ``examples`` keeps the first ``limit`` of them so an artifact stays small.
    ``start`` establishes the baseline once per decision; ``observe`` is called
    after every ``VectorBattle.step`` with that tick's headers and the actions
    the producer gave.
    """

    cols: int
    rows: int
    refusals: Mapping[int, str] | None = None
    limit: int = 8
    count: int = 0
    examples: list[dict[str, Any]] = field(default_factory=list)
    _previous: np.ndarray | None = None

    def start(self, counters: Any) -> None:
        """Set the baseline the next tick's ``illegal`` counts are compared with."""

        self._previous = np.array(counters, dtype=np.int64, copy=True)

    def observe(
        self,
        *,
        segment: int,
        decision: int,
        window_tick: int,
        counters: Any,
        refusal_codes: Any,
        run_refusal_codes: Any,
        given: Mapping[int, Any],
        given_step: Mapping[int, Any],
        plans: Mapping[int, Any],
        tables: Mapping[int, Any],
    ) -> list[dict[str, Any]]:
        """Differences this tick's headers against the last snapshot.

        ``given``/``given_step`` hold the engine action and micro-action step the
        producer actually gave this tick, per seat (``0``/``-1`` for a wait).
        The first call after ``start`` only establishes the snapshot.
        """

        current = np.asarray(counters, dtype=np.int64)
        previous = self._previous
        if previous is None or previous.shape != current.shape:
            self._previous = current.copy()
            return []
        delta = current - previous
        self._previous = current.copy()
        lanes, seats = np.nonzero(delta > 0)
        if lanes.size == 0:
            return []
        events: list[dict[str, Any]] = []
        for lane, seat in zip(lanes.tolist(), seats.tolist()):
            seat = int(seat)
            action = ENGINE_WAIT
            if seat in given:
                values = np.asarray(given[seat])
                if lane < values.shape[0]:
                    action = int(values[lane])
            step = -1
            if seat in given_step:
                steps = np.asarray(given_step[seat])
                if lane < steps.shape[0]:
                    step = int(steps[lane])
            events.append(
                attribute_illegal_action(
                    segment=segment,
                    decision=decision,
                    window_tick=window_tick,
                    lane=int(lane),
                    seat=seat,
                    refused=int(delta[lane, seat]),
                    action=action,
                    micro_step=step,
                    plan=plans.get(seat),
                    tables=tables.get(seat),
                    refusal_code=int(np.asarray(refusal_codes)[lane, seat]),
                    run_refusal_code=int(np.asarray(run_refusal_codes)[lane, seat]),
                    refusals=self.refusals,
                    cols=self.cols,
                    rows=self.rows,
                )
            )
        self.count += int(delta[delta > 0].sum())
        room = int(self.limit) - len(self.examples)
        if room > 0:
            self.examples.extend(events[:room])
        return events
