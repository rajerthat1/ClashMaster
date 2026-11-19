"""Opponent league for the crforge C4 producer.

The native V4 trainer already has ``PolicyLeagueV4``: PFSP weighting, history
snapshots and a frozen IL anchor.  This module is the small piece crforge needs
around it — load/save the same JSON, sample one opponent for a segment, and
record the learner's results so the next sample is weighted by what the learner
still loses to.

A league file is the native ``v4-ppo-policy-league.v1`` JSON, so a league built
by the native trainer can be used as a crforge opponent pool without conversion.
crforge adds one optional top-level ``crforge`` block (each entry's deck); a
native ``PolicyLeagueV4.save`` does not preserve it, so write the file back
through :class:`CrforgeOpponentLeague` once decks are attached.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from native_runner.training.v4.deck_pool import DeckIdentityV4, DeckPoolManifestV4
from native_runner.training.v4.league import LeagueEntryV4, PolicyLeagueV4

LEAGUE_SCHEMA = "crforge-opponent-league.v1"


@dataclass(frozen=True, slots=True)
class OpponentDeck:
    """One opponent's deck, in the deck-pool identity the native trainer uses."""

    identity: DeckIdentityV4

    @property
    def deck_id(self) -> str:
        return self.identity.deck_id

    @property
    def card_ids(self) -> tuple[int, ...]:
        return self.identity.card_ids

    @property
    def forms(self) -> tuple[int, ...]:
        return self.identity.form_availability

    @property
    def tower_troop_id(self) -> int:
        return self.identity.tower_troop_id

    def as_dict(self) -> dict[str, Any]:
        return self.identity.to_dict()

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> OpponentDeck:
        return cls(DeckIdentityV4.from_dict(value))

    @classmethod
    def from_pool(cls, pool: str | Path, deck_id: str) -> OpponentDeck:
        manifest = DeckPoolManifestV4.load(pool)
        for deck in manifest.decks:
            if deck.deck_id == str(deck_id):
                return cls(deck)
        raise ValueError(f"deck {deck_id} is not in {Path(pool)}")



@dataclass(frozen=True, slots=True)
class OpponentPlan:
    """The frozen checkpoint one segment's opponent seat plays."""

    checkpoint_id: str
    checkpoint_path: Path
    update_step: int

    @classmethod
    def from_entry(cls, entry: LeagueEntryV4) -> OpponentPlan:
        return cls(
            checkpoint_id=entry.checkpoint_id,
            checkpoint_path=Path(entry.checkpoint_path),
            update_step=int(entry.update_step),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_path": str(self.checkpoint_path),
            "update_step": self.update_step,
        }


def checkpoint_identity(path: str | Path) -> tuple[str, int]:
    """Read a V4 checkpoint's ID and update step without building the model."""

    import torch

    checkpoint_path = Path(path).expanduser().resolve()
    payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    if not isinstance(payload, dict):
        raise TypeError(f"{checkpoint_path} is not a V4 checkpoint")
    checkpoint_id = str(payload.get("checkpoint_id", ""))
    if not checkpoint_id:
        raise ValueError(f"{checkpoint_path} carries no checkpoint_id")
    update_step = int(payload.get("update_step", 0))
    if update_step < 0:
        raise ValueError(f"{checkpoint_path} carries a negative update_step")
    return checkpoint_id, update_step


class CrforgeOpponentLeague:
    """The sample/record half of ``PolicyLeagueV4`` used by the producer.

    ``seed`` makes a sampled sequence reproducible; each producer owns its own
    league state and writes it back at a segment boundary.
    """

    def __init__(
        self,
        league: PolicyLeagueV4,
        *,
        path: str | Path | None = None,
        seed: int = 0,
        decks: Mapping[str, OpponentDeck] | None = None,
    ) -> None:
        if seed < 0:
            raise ValueError("league seed must be non-negative")
        self.league = league
        self.path = None if path is None else Path(path).expanduser().resolve()
        self.rng = random.Random(int(seed))
        self.decks = {str(key): value for key, value in (decks or {}).items()}
        known = {entry.checkpoint_id for entry in league.entries} | {league.anchor_checkpoint_id}
        unknown = set(self.decks) - known
        if unknown:
            raise ValueError(f"league decks name unknown checkpoints: {sorted(unknown)}")

    @classmethod
    def load(cls, path: str | Path, *, seed: int = 0) -> CrforgeOpponentLeague:
        source = Path(path).expanduser().resolve()
        raw = json.loads(source.read_text(encoding="utf-8"))
        block = raw.get("crforge") if isinstance(raw, Mapping) else None
        rows = block.get("decks") if isinstance(block, Mapping) else None
        decks = (
            {str(key): OpponentDeck.from_mapping(value) for key, value in rows.items()}
            if isinstance(rows, Mapping)
            else {}
        )
        return cls(PolicyLeagueV4.load(source), path=source, seed=seed, decks=decks)

    @property
    def has_opponents(self) -> bool:
        return self.league.has_history

    @property
    def entries(self) -> tuple[LeagueEntryV4, ...]:
        return tuple(self.league.entries)

    def deck_for(self, checkpoint_id: str) -> OpponentDeck | None:
        return self.decks.get(str(checkpoint_id))

    def set_deck(self, checkpoint_id: str, deck: OpponentDeck) -> None:
        known = {entry.checkpoint_id for entry in self.league.entries} | {self.league.anchor_checkpoint_id}
        if checkpoint_id not in known:
            raise ValueError(f"league has no checkpoint {checkpoint_id}")
        self.decks[str(checkpoint_id)] = deck

    def sample(self) -> OpponentPlan:
        if not self.has_opponents:
            raise ValueError("the opponent league has no history entries")
        return OpponentPlan.from_entry(self.league.sample_history(self.rng))

    def record(self, checkpoint_id: str, learner_result: float) -> None:
        """Record one game's result (1 win, 0 draw, -1 loss) against an entry."""

        self.league.record_result(checkpoint_id, float(learner_result))

    def record_segment(self, checkpoint_id: str, results: Iterable[float]) -> int:
        """Record every finished game in a segment; returns the count."""

        count = 0
        for result in results:
            value = float(result)
            if value not in (-1.0, 0.0, 1.0):
                raise ValueError(f"learner result must be -1, 0 or 1 (got {value})")
            self.league.record_result(checkpoint_id, value)
            count += 1
        return count

    def save(self, path: str | Path | None = None) -> Path:
        destination = Path(path) if path is not None else self.path
        if destination is None:
            raise ValueError("a league loaded without a path needs an explicit save destination")
        destination = destination.expanduser().resolve()
        payload = self.league.to_dict()
        payload["crforge"] = {"decks": {key: deck.as_dict() for key, deck in self.decks.items()}}
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(destination)
        self.path = destination
        return destination

    def summary(self) -> dict[str, Any]:
        return {
            "schema": LEAGUE_SCHEMA,
            "path": None if self.path is None else str(self.path),
            "anchor_checkpoint_id": self.league.anchor_checkpoint_id,
            "anchor_checkpoint_path": self.league.anchor_checkpoint_path,
            "entries": [entry.to_dict() for entry in self.league.entries],
            "decks": {key: deck.as_dict() for key, deck in self.decks.items()},
        }


def add_checkpoint(
    path: str | Path,
    checkpoint: str | Path,
    *,
    update_step: int | None = None,
    deck: OpponentDeck | None = None,
) -> CrforgeOpponentLeague:
    """Load a league, add one checkpoint as a frozen opponent, and save it."""

    league = CrforgeOpponentLeague.load(path)
    checkpoint_id, recorded_step = checkpoint_identity(checkpoint)
    league.league.add_snapshot(
        checkpoint_id,
        checkpoint,
        update_step=recorded_step if update_step is None else int(update_step),
    )
    if deck is not None:
        league.set_deck(checkpoint_id, deck)
    league.save(path)
    return league


def create_league(
    path: str | Path,
    *,
    anchor: str | Path,
    anchor_id: str | None = None,
) -> CrforgeOpponentLeague:
    """Create an empty league whose anchor is a real V4 checkpoint."""

    if anchor_id is None:
        anchor_id, _ = checkpoint_identity(anchor)
    league = PolicyLeagueV4(anchor_checkpoint_id=anchor_id, anchor_checkpoint_path=anchor)
    destination = Path(path).expanduser().resolve()
    league.save(destination)
    return CrforgeOpponentLeague(league, path=destination)


__all__ = [
    "LEAGUE_SCHEMA",
    "CrforgeOpponentLeague",
    "OpponentDeck",
    "OpponentPlan",
    "add_checkpoint",
    "checkpoint_identity",
    "create_league",
]
