"""Pure decision-loop helpers shared by the live match tools.

These tests do not touch a device: they pin the host-side validation contract
used by ``tools/native_analysis/multi_decision_match.py`` and
``entity_hp_probe.py`` so a regression in the checks cannot silently turn a
free or mispriced card into a passing run.
"""

from __future__ import annotations

import sys
from pathlib import Path

from native_runner.paths import REPOSITORY_ROOT

TOOLS = REPOSITORY_ROOT / "tools" / "native_analysis"
sys.path.insert(0, str(TOOLS))

import multi_decision_match as mdm  # type: ignore[import-not-found]


def player(owner: int, elixir: float, hand: list[tuple[int, int, int]], next_card: int) -> dict:
    return {
        "owner": owner,
        "elixir": elixir,
        "hand": [
            {"handIndex": hand_index, "cardId": card_id, "cost": cost}
            for hand_index, card_id, cost in hand
        ],
        "nextCard": {"deckSlot": 0, "cardId": next_card},
    }


def observation(players: list[dict], tick: int = 0, count: int = 6) -> dict:
    return {"tick": tick, "count": count, "queuedCommands": 0, "players": players}


def test_choose_actions_selects_the_first_affordable_slot_per_owner() -> None:
    before = observation(
        [
            player(0, 4.0, [(0, 26000000, 5), (1, 26000001, 3), (2, 26000005, 2)], 26000014),
            player(1, 1.0, [(0, 26000000, 5), (1, 26000001, 3)], 26000014),
        ]
    )
    actions = mdm.choose_actions(before, (0, 1), 9000, 10000)
    assert [(a.owner, a.hand_index, a.x, a.y) for a in actions] == [(0, 1, 9000, 10000)]


def test_validate_decision_accepts_a_paid_play_with_rotation() -> None:
    before = observation([player(0, 6.0, [(0, 26000000, 3), (1, 26000001, 3)], 26000014)])
    after = observation([player(0, 3.5, [(0, 26000014, 4), (1, 26000001, 3)], 26000005)], tick=22, count=7)
    actions = [mdm.HandAction(0, 0, 9000, 10000)]
    assert mdm.validate_decision(before, after, actions, 22) == []


def test_validate_decision_flags_a_free_play() -> None:
    before = observation([player(0, 6.0, [(0, 26000000, 3), (1, 26000001, 3)], 26000014)])
    after = observation([player(0, 6.5, [(0, 26000014, 4), (1, 26000001, 3)], 26000005)], tick=22, count=7)
    actions = [mdm.HandAction(0, 0, 9000, 10000)]
    problems = mdm.validate_decision(before, after, actions, 22)
    assert any("did not pay 3" in problem for problem in problems)


def test_validate_decision_flags_overpayment_and_wrong_rotation() -> None:
    before = observation([player(0, 6.0, [(0, 26000000, 3), (1, 26000001, 3)], 26000014)])
    after = observation([player(0, 1.0, [(0, 26000018, 4), (1, 26000001, 3)], 26000005)], tick=22, count=7)
    actions = [mdm.HandAction(0, 0, 9000, 10000)]
    problems = mdm.validate_decision(before, after, actions, 22)
    assert any("paid more than the 3" in problem for problem in problems)
    assert any("expected next card 26000014" in problem for problem in problems)


def test_validate_decision_flags_a_queued_command_and_lost_slot() -> None:
    before = observation([player(0, 6.0, [(0, 26000000, 3)], 26000014)])
    after = observation([player(0, 3.0, [], 26000014)], tick=22, count=7)
    after["queuedCommands"] = 1
    problems = mdm.validate_decision(before, after, [mdm.HandAction(0, 0, 9000, 10000)], 22)
    assert any("queuedCommands=1" in problem for problem in problems)
    assert any("lost hand slot 0" in problem for problem in problems)
