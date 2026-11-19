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


def test_vector_uses_per_side_decks_slots_and_towers(monkeypatch) -> None:
    import crforge_env.vector as vector_module

    seen: dict[str, object] = {}

    class FakeVectorBattle:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def reset(self, *args: object, **kwargs: object) -> None:
            seen["args"] = args
            seen["kwargs"] = kwargs

    monkeypatch.setattr(vector_module, "VectorBattle", FakeVectorBattle)
    self_play._vector(
        2,
        max_ticks=0,
        decks=(("Xbow",) * 8, ("HogRider",) * 8),
        slots=((0,) * 8, (1, 0, 0, 0, 0, 2, 1, 0)),
        towers=("King_PrincessTowers", "King_CannonTowers"),
    )
    args = seen["args"]
    assert args[1] == ["Xbow"] * 8
    assert args[2] == ["HogRider"] * 8
    assert args[3] == [0] * 8
    assert args[4] == [1, 0, 0, 0, 0, 2, 1, 0]
    assert seen["kwargs"]["setup1"].towers == "King_CannonTowers"


def _deck_spec(deck_id: str) -> dict:
    return {
        "deck_id": deck_id,
        "card_keys": [f"card{index}" for index in range(8)],
        "card_ids": [26000000 + index for index in range(8)],
        "form_availability": [0, 1, 0, 2, 0, 0, 1, 0],
        "tower_key": "tower-princess",
        "tower_troop_id": 159000000,
        "uses": 1,
        "focus_rank": None,
    }


def test_resolve_opponent_deck_accepts_one_shared_deck(tmp_path) -> None:
    import argparse

    import torch

    from crforge_bridge.league import CrforgeOpponentLeague, OpponentDeck, add_checkpoint, create_league

    def checkpoint(name: str, checkpoint_id: str):
        path = tmp_path / f"{name}.pt"
        torch.save({"checkpoint_id": checkpoint_id, "update_step": 1}, path)
        return path

    league_path = tmp_path / "league.json"
    create_league(league_path, anchor=checkpoint("anchor", "anchor-id"))
    deck = OpponentDeck.from_mapping(_deck_spec("shared"))
    add_checkpoint(league_path, checkpoint("one", "one-id"), deck=deck)
    add_checkpoint(league_path, checkpoint("two", "two-id"), deck=deck)
    args = argparse.Namespace(opponent_deck_id=None, opponent_deck_json=None, deck_pool=tmp_path)
    assert self_play._resolve_opponent_deck(args, CrforgeOpponentLeague.load(league_path)) == deck


def test_resolve_opponent_deck_refuses_mixed_decks_without_a_choice(tmp_path) -> None:
    import argparse

    import pytest
    import torch

    from crforge_bridge.league import CrforgeOpponentLeague, OpponentDeck, add_checkpoint, create_league

    def checkpoint(name: str, checkpoint_id: str):
        path = tmp_path / f"{name}.pt"
        torch.save({"checkpoint_id": checkpoint_id, "update_step": 1}, path)
        return path

    league_path = tmp_path / "league.json"
    create_league(league_path, anchor=checkpoint("anchor", "anchor-id"))
    add_checkpoint(
        league_path, checkpoint("one", "one-id"), deck=OpponentDeck.from_mapping(_deck_spec("one-deck"))
    )
    add_checkpoint(
        league_path, checkpoint("two", "two-id"), deck=OpponentDeck.from_mapping(_deck_spec("two-deck"))
    )
    args = argparse.Namespace(opponent_deck_id=None, opponent_deck_json=None, deck_pool=tmp_path)
    with pytest.raises(SystemExit, match="share one deck"):
        self_play._resolve_opponent_deck(args, CrforgeOpponentLeague.load(league_path))
