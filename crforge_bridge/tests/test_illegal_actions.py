"""The producer names an illegal action, not just counts it.

The gate's ``illegal_peak`` is one number per run; the attribution has to say
which lane, seat, tick and card gave the refused action, and it must not mistake
a lane autoreset (the counter dropping to zero) for a refusal.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from crforge_bridge.illegal_actions import (
    REFUSAL_MASKED,
    REFUSAL_NONE,
    REFUSAL_NOT_MODELLED,
    RUN_NOT_PLACED,
    IllegalActionTrace,
    decode_engine_play,
    game_cell,
    refusal_name,
)
from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
from crforge_bridge.vector_actions import engine_action_index

COLS = 18
ROWS = 32


def test_a_play_decodes_to_its_hand_slot_and_seat_tile() -> None:
    for slot in range(4):
        for col, row in ((0, 0), (3, 4), (COLS - 1, ROWS - 1)):
            index = engine_action_index(slot=slot, col=col, row=row, cols=COLS, rows=ROWS)
            assert decode_engine_play(index, cols=COLS, rows=ROWS) == (slot, col, row)


def test_wait_and_ability_indices_are_not_plays() -> None:
    assert decode_engine_play(0, cols=COLS, rows=ROWS) is None
    first_ability = 1 + 4 * COLS * ROWS
    assert decode_engine_play(first_ability, cols=COLS, rows=ROWS) is None


def test_game_cell_undoes_the_engine_mirror() -> None:
    # Seat 0 mirrors x, seat 1 mirrors y (Schema.toSeatX/toSeatY).
    assert game_cell(3, 4, seat=0, cols=COLS, rows=ROWS) == (COLS - 1 - 3, 4)
    assert game_cell(3, 4, seat=1, cols=COLS, rows=ROWS) == (3, ROWS - 1 - 4)


def test_refusal_names_cover_the_builtin_codes() -> None:
    assert refusal_name(REFUSAL_NONE) == "none"
    assert refusal_name(REFUSAL_MASKED) == "MASKED"
    assert refusal_name(REFUSAL_NOT_MODELLED) == "NOT_MODELLED"
    assert refusal_name(RUN_NOT_PLACED) == "RUN_NOT_PLACED"
    # The player commands' own codes come from the env's refusal table.
    assert refusal_name(5, {5: "NOT_ENOUGH_ELIXIR"}) == "NOT_ENOUGH_ELIXIR"
    assert refusal_name(7) == "7"


def _trace(limit: int = 8) -> IllegalActionTrace:
    return IllegalActionTrace(cols=COLS, rows=ROWS, refusals={5: "NOT_ENOUGH_ELIXIR"}, limit=limit)


def _plan(*, candidate_row: int = 3, mask_open: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        candidate_rows=np.array([[candidate_row]], dtype=np.int64),
        masked=np.array([[mask_open]], dtype=bool),
    )


def _tables(*, candidates: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        effective_card_vocab_id=np.arange(candidates, dtype=np.int32)[None, :] + 40,
        uid=(np.arange(candidates, dtype=np.int64)[None, :] + 900),
        native_hand_slot=(np.arange(candidates, dtype=np.int32)[None, :] % 4),
    )


def test_a_positive_delta_names_the_action_the_card_and_the_reason() -> None:
    trace = _trace()
    action = engine_action_index(slot=2, col=3, row=4, cols=COLS, rows=ROWS)
    trace.start(np.zeros((1, 2), dtype=np.int64))
    events = trace.observe(
        segment=4,
        decision=17,
        window_tick=3,
        counters=np.array([[1, 0]], dtype=np.int64),
        refusal_codes=np.array([[5, 0]], dtype=np.int64),
        run_refusal_codes=np.array([[0, 0]], dtype=np.int64),
        given={0: np.array([action], dtype=np.int32)},
        given_step={0: np.array([0], dtype=np.int32)},
        plans={0: _plan()},
        tables={0: _tables()},
    )
    assert trace.count == 1
    assert len(events) == 1
    event = events[0]
    assert event["segment"] == 4
    assert event["decision"] == 17
    assert event["window_tick"] == 3
    assert event["lane"] == 0
    assert event["seat"] == 0
    assert event["hand_slot"] == 2
    assert event["seat_cell"] == [3, 4]
    assert event["game_cell"] == [COLS - 1 - 3, 4]
    assert event["card_vocab_id"] == 43
    assert event["candidate_uid"] == 903
    assert event["hand_slot_reported"] == 3
    assert event["engine_mask_open"] is True
    assert event["refusal"] == "NOT_ENOUGH_ELIXIR"
    assert event["run_refusal"] == "none"


def test_a_lane_reset_is_not_a_refusal() -> None:
    trace = _trace()
    trace.start(np.array([[2, 1]], dtype=np.int64))
    events = trace.observe(
        segment=0,
        decision=0,
        window_tick=0,
        counters=np.zeros((1, 2), dtype=np.int64),
        refusal_codes=np.zeros((1, 2), dtype=np.int64),
        run_refusal_codes=np.zeros((1, 2), dtype=np.int64),
        given={0: np.zeros(1, dtype=np.int32)},
        given_step={0: np.full(1, -1, dtype=np.int32)},
        plans={},
        tables={},
    )
    assert events == []
    assert trace.count == 0


def test_an_unstarted_or_reshaped_trace_only_establishes_a_snapshot() -> None:
    trace = _trace()

    def observe(counters: np.ndarray) -> list[dict]:
        return trace.observe(
            segment=0,
            decision=0,
            window_tick=0,
            counters=counters,
            refusal_codes=np.zeros((counters.shape[0], 2), dtype=np.int64),
            run_refusal_codes=np.zeros((counters.shape[0], 2), dtype=np.int64),
            given={},
            given_step={},
            plans={},
            tables={},
        )

    # No baseline yet: this observation is the snapshot, not a delta.
    assert observe(np.array([[1, 0]], dtype=np.int64)) == []
    # A changed shape (another lane count) is a new baseline too.
    assert observe(np.array([[1, 0], [0, 0]], dtype=np.int64)) == []
    # With a stable shape, a movement is a refusal.
    assert len(observe(np.array([[2, 0], [0, 0]], dtype=np.int64))) == 1
    assert trace.count == 1


def test_every_refusal_is_counted_but_examples_are_bounded() -> None:
    trace = _trace(limit=2)
    trace.start(np.zeros((3, 2), dtype=np.int64))
    for tick, lane in enumerate((0, 1, 2)):
        counters = np.zeros((3, 2), dtype=np.int64)
        counters[lane, 0] = 1
        trace.observe(
            segment=0,
            decision=tick,
            window_tick=tick,
            counters=counters,
            refusal_codes=np.zeros((3, 2), dtype=np.int64),
            run_refusal_codes=np.zeros((3, 2), dtype=np.int64),
            given={0: np.zeros(3, dtype=np.int32)},
            given_step={0: np.full(3, -1, dtype=np.int32)},
            plans={0: None},
            tables={0: None},
        )
    assert trace.count == 3
    assert [event["lane"] for event in trace.examples] == [0, 1]


def test_a_play_without_its_plan_still_names_the_slot_and_reason() -> None:
    trace = _trace()
    action = engine_action_index(slot=1, col=0, row=0, cols=COLS, rows=ROWS)
    trace.start(np.zeros((1, 2), dtype=np.int64))
    event = trace.observe(
        segment=0,
        decision=0,
        window_tick=0,
        counters=np.array([[0, 1]], dtype=np.int64),
        refusal_codes=np.array([[0, 0]], dtype=np.int64),
        run_refusal_codes=np.array([[0, RUN_NOT_PLACED]], dtype=np.int64),
        given={1: np.array([action], dtype=np.int32)},
        given_step={1: np.array([1], dtype=np.int32)},
        plans={},
        tables={},
    )[0]
    assert event["seat"] == 1
    assert event["hand_slot"] == 1
    assert event["card_vocab_id"] is None
    assert event["engine_mask_open"] is None
    assert event["run_refusal"] == "RUN_NOT_PLACED"


def test_the_real_engine_header_attributes_a_refused_play(crforge_runtime: None) -> None:
    """The header diff, against ``VectorBattle`` itself, not a synthetic counter.

    At reset the match is in its start cooldown, so every play is closed and a
    play given now is refused as ``MASKED``.  The engine's cumulative ``illegal``
    header moves from 0 to 1 on that step and ``last_refusal`` names the reason;
    the trace has to bind both to the seat, tile and hand slot that were given.
    """

    from crforge_env.vector import LadderConfig, SideSetup, VectorBattle

    names = ("Xbow", "Tesla", "Archer", "Knight", "Skeletons", "ElectroSpirit", "Fireball", "Log")
    forms = (0, 1, 1, 2, 0, 0, 0, 0)
    setup = SideSetup(towers="King_PrincessTowers", king_level=16, tower_level=16)
    vector = VectorBattle(
        1,
        1,
        install=str(DEFAULT_INSTALL),
        tables=str(DEFAULT_TABLES),
        config=LadderConfig(decision_ticks=5, level=16, tower_level=16),
    )
    vector.reset([1], list(names), list(names), list(forms), list(forms), setup0=setup, setup1=setup)
    header = vector.schema.header
    cols, board_rows = int(vector.schema.cols), int(vector.schema.rows)
    assert int(vector.observations[0, 0, header["illegal"][0]]) == 0

    trace = IllegalActionTrace(cols=cols, rows=board_rows, refusals=vector.schema.refusals)
    trace.start(vector.observations[:, :, header["illegal"][0]])
    action = engine_action_index(slot=0, col=5, row=5, cols=cols, rows=board_rows)
    actions = np.zeros((1, 2), dtype=np.int32)
    actions[0, 0] = action
    given = {0: np.array([action], dtype=np.int32), 1: np.zeros(1, dtype=np.int32)}
    given_step = {0: np.array([0], dtype=np.int32), 1: np.full(1, -1, dtype=np.int32)}
    vector.step(actions)
    events = trace.observe(
        segment=0,
        decision=0,
        window_tick=0,
        counters=vector.observations[:, :, header["illegal"][0]],
        refusal_codes=vector.observations[:, :, header["last_refusal"][0]],
        run_refusal_codes=vector.observations[:, :, header["last_run_refusal"][0]],
        given=given,
        given_step=given_step,
        plans={},
        tables={},
    )
    assert trace.count == 1
    assert len(events) == 1
    event = events[0]
    assert event["lane"] == 0
    assert event["seat"] == 0
    assert event["refused"] == 1
    assert event["hand_slot"] == 0
    assert event["seat_cell"] == [5, 5]
    assert event["game_cell"] == [cols - 1 - 5, 5]
    assert event["refusal"] == "MASKED"
    assert event["run_refusal"] == "NONE"
