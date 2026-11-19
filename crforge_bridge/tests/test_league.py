"""The crforge opponent league: same JSON as the native league, PFSP sampling."""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from crforge_bridge.league import (
    CrforgeOpponentLeague,
    add_checkpoint,
    checkpoint_identity,
    create_league,
)
from native_runner.training.v4.league import PolicyLeagueV4


def _checkpoint(path: Path, checkpoint_id: str, update_step: int = 0) -> Path:
    torch.save({"checkpoint_id": checkpoint_id, "update_step": update_step}, path)
    return path


def _league(tmp_path: Path) -> tuple[Path, Path, Path]:
    anchor = _checkpoint(tmp_path / "anchor.pt", "anchor-id")
    first = _checkpoint(tmp_path / "first.pt", "first-id", update_step=10)
    second = _checkpoint(tmp_path / "second.pt", "second-id", update_step=20)
    path = tmp_path / "league.json"
    league = PolicyLeagueV4(anchor_checkpoint_id="anchor-id", anchor_checkpoint_path=anchor)
    league.add_snapshot("first-id", first, update_step=10)
    league.add_snapshot("second-id", second, update_step=20)
    league.save(path)
    return path, first, second


def test_load_samples_a_real_entry_and_is_seed_reproducible(tmp_path: Path) -> None:
    path, first, second = _league(tmp_path)
    a = CrforgeOpponentLeague.load(path, seed=7)
    b = CrforgeOpponentLeague.load(path, seed=7)
    assert a.has_opponents
    assert {entry.checkpoint_id for entry in a.entries} == {"first-id", "second-id"}

    plans_a = [a.sample() for _ in range(20)]
    plans_b = [b.sample() for _ in range(20)]
    assert [plan.checkpoint_id for plan in plans_a] == [plan.checkpoint_id for plan in plans_b]
    assert {plan.checkpoint_id for plan in plans_a} == {"first-id", "second-id"}
    assert {plan.checkpoint_path for plan in plans_a} == {first.resolve(), second.resolve()}


def test_record_updates_the_learner_win_rate(tmp_path: Path) -> None:
    path, _, _ = _league(tmp_path)
    league = CrforgeOpponentLeague.load(path, seed=1)
    entry = league.entries[0]
    assert entry.learner_win_rate == pytest.approx(0.5)

    league.record_segment(entry.checkpoint_id, [1.0, -1.0, 0.0])
    updated = next(item for item in league.entries if item.checkpoint_id == entry.checkpoint_id)
    assert (updated.wins_against, updated.losses_against, updated.draws_against) == (1, 1, 1)
    assert updated.learner_win_rate == pytest.approx(0.5)


def test_record_rejects_a_non_game_result(tmp_path: Path) -> None:
    path, _, _ = _league(tmp_path)
    league = CrforgeOpponentLeague.load(path)
    with pytest.raises(ValueError, match="learner result"):
        league.record_segment(league.entries[0].checkpoint_id, [0.25])


def test_an_empty_league_has_no_opponent(tmp_path: Path) -> None:
    anchor = _checkpoint(tmp_path / "anchor.pt", "anchor-id")
    path = tmp_path / "league.json"
    create_league(path, anchor=anchor)
    league = CrforgeOpponentLeague.load(path)
    assert not league.has_opponents
    with pytest.raises(ValueError, match="no history"):
        league.sample()


def test_save_round_trips_the_recorded_results(tmp_path: Path) -> None:
    path, _, _ = _league(tmp_path)
    league = CrforgeOpponentLeague.load(path)
    entry = league.entries[0]
    league.record(entry.checkpoint_id, -1.0)
    saved = league.save()
    assert saved == path.resolve()

    reloaded = CrforgeOpponentLeague.load(saved)
    updated = next(item for item in reloaded.entries if item.checkpoint_id == entry.checkpoint_id)
    assert updated.losses_against == 1


def test_add_checkpoint_reads_the_id_and_step_from_the_payload(tmp_path: Path) -> None:
    anchor = _checkpoint(tmp_path / "anchor.pt", "anchor-id")
    path = tmp_path / "league.json"
    create_league(path, anchor=anchor)
    extra = _checkpoint(tmp_path / "extra.pt", "extra-id", update_step=42)

    league = add_checkpoint(path, extra)
    entry = league.entries[0]
    assert entry.checkpoint_id == "extra-id"
    assert entry.update_step == 42
    assert checkpoint_identity(extra) == ("extra-id", 42)


def test_checkpoint_identity_rejects_a_payload_without_an_id(tmp_path: Path) -> None:
    path = tmp_path / "bad.pt"
    torch.save({"weights": torch.zeros(1)}, path)
    with pytest.raises(ValueError, match="checkpoint_id"):
        checkpoint_identity(path)


def _deck_spec(deck_id: str = "hog-2.6") -> dict:
    return {
        "deck_id": deck_id,
        "card_keys": [f"card{index}" for index in range(8)],
        "card_ids": [26000000 + index for index in range(8)],
        "form_availability": [0, 1, 0, 2, 0, 0, 1, 0],
        "tower_key": "tower-princess",
        "tower_troop_id": 159000000,
        "uses": 10,
        "focus_rank": None,
    }


def test_a_league_deck_survives_save_and_record(tmp_path: Path) -> None:
    from crforge_bridge.league import OpponentDeck

    path, _, _ = _league(tmp_path)
    league = CrforgeOpponentLeague.load(path)
    entry = league.entries[0]
    deck = OpponentDeck.from_mapping(_deck_spec())
    league.set_deck(entry.checkpoint_id, deck)
    league.record(entry.checkpoint_id, 1.0)
    league.save()

    reloaded = CrforgeOpponentLeague.load(path)
    assert reloaded.deck_for(entry.checkpoint_id) == deck
    assert reloaded.summary()["decks"][entry.checkpoint_id]["deck_id"] == "hog-2.6"
    updated = next(item for item in reloaded.entries if item.checkpoint_id == entry.checkpoint_id)
    assert updated.wins_against == 1


def test_set_deck_refuses_an_unknown_checkpoint(tmp_path: Path) -> None:
    from crforge_bridge.league import OpponentDeck

    path, _, _ = _league(tmp_path)
    league = CrforgeOpponentLeague.load(path)
    with pytest.raises(ValueError, match="no checkpoint"):
        league.set_deck("not-in-the-league", OpponentDeck.from_mapping(_deck_spec()))


def test_add_checkpoint_can_attach_a_deck(tmp_path: Path) -> None:
    from crforge_bridge.league import OpponentDeck

    anchor = _checkpoint(tmp_path / "anchor.pt", "anchor-id")
    path = tmp_path / "league.json"
    create_league(path, anchor=anchor)
    extra = _checkpoint(tmp_path / "extra.pt", "extra-id")
    deck = OpponentDeck.from_mapping(_deck_spec("extra-deck"))

    league = add_checkpoint(path, extra, deck=deck)
    assert league.deck_for("extra-id") == deck
