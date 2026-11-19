"""The producer's JVM escape hatch: ``--jvm-arg`` reaches the engine.

A rented host's HotSpot C2 compiler miscompiled ``SeatIds.of`` while the PPO
update kept the JVM idle between segments and segfaulted the producer at the
next segment; ``-XX:TieredStopAtLevel=1`` (C1 only) passes.  The flag is the
producer's, so it has to reach ``VectorBattle`` unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "crforge"
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import self_play


def test_vector_forwards_the_jvm_options(monkeypatch) -> None:
    import crforge_env.vector as vector_module

    seen: dict[str, object] = {}

    class FakeVectorBattle:
        def __init__(self, *args: object, **kwargs: object) -> None:
            seen.update(kwargs)

        def reset(self, *args: object, **kwargs: object) -> None:
            pass

    monkeypatch.setattr(vector_module, "VectorBattle", FakeVectorBattle)
    self_play._vector(2, max_ticks=0, jvm_args=("-XX:TieredStopAtLevel=1",))
    assert seen["jvm_args"] == ("-XX:TieredStopAtLevel=1",)


def test_the_default_is_no_extra_option(monkeypatch) -> None:
    import crforge_env.vector as vector_module

    seen: dict[str, object] = {}

    class FakeVectorBattle:
        def __init__(self, *args: object, **kwargs: object) -> None:
            seen.update(kwargs)

        def reset(self, *args: object, **kwargs: object) -> None:
            pass

    monkeypatch.setattr(vector_module, "VectorBattle", FakeVectorBattle)
    self_play._vector(1, max_ticks=0)
    assert seen["jvm_args"] == ()


def _actions(gates: list[int]):
    import torch

    from native_runner.training.v4.tensors import ActionSequenceV4

    rows = len(gates)
    return ActionSequenceV4(
        gate=torch.tensor(gates, dtype=torch.long),
        micro_action_count=torch.tensor(gates, dtype=torch.long),
        candidate_index=torch.full((rows, 1), -1, dtype=torch.long),
        candidate_uid=torch.full((rows, 1), -1, dtype=torch.long),
        target_cell=torch.full((rows, 1), -1, dtype=torch.long),
        delay_offset_bin=torch.full((rows, 1), -1, dtype=torch.long),
    )


def test_learner_train_rows_select_only_the_learner_seat() -> None:
    assert self_play._learner_train_rows(3, (0, 1), 0).tolist() == [True, False] * 3
    assert self_play._learner_train_rows(3, (0, 1), 1).tolist() == [False, True] * 3
    assert self_play._learner_train_rows(2, (1, 0), 1).tolist() == [True, False] * 2


def test_splice_opponent_actions_keeps_the_interleaved_row_order() -> None:
    learner = _actions([1, 0, 1, 0])
    opponent = _actions([0, 1])
    spliced = self_play._splice_opponent_actions(
        learner, opponent, seats=(0, 1), opponent_seat=1
    )
    # lane 0 seat 0, lane 0 seat 1 (opponent), lane 1 seat 0, lane 1 seat 1 (opponent)
    assert spliced.gate.tolist() == [1, 0, 1, 1]


def test_splice_opponent_actions_supports_learner_seat_one() -> None:
    learner = _actions([1, 0, 1, 0])
    opponent = _actions([0, 1])
    spliced = self_play._splice_opponent_actions(
        learner, opponent, seats=(0, 1), opponent_seat=0
    )
    assert spliced.gate.tolist() == [0, 0, 1, 0]
