"""Opponent league for the crforge C4 producer.

The native V4 trainer already has ``PolicyLeagueV4``: PFSP weighting, history
snapshots and a frozen IL anchor.  This module is the small piece crforge needs
around it — load/save the same JSON, sample one opponent for a segment, and
record the learner's results so the next sample is weighted by what the learner
still loses to.

A league file is the native ``v4-ppo-policy-league.v1`` JSON, so a league built
by the native trainer can be used as a crforge opponent pool without conversion.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from native_runner.training.v4.league import LeagueEntryV4, PolicyLeagueV4

LEAGUE_SCHEMA = "crforge-opponent-league.v1"


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

    def __init__(self, league: PolicyLeagueV4, *, path: str | Path | None = None, seed: int = 0) -> None:
        if seed < 0:
            raise ValueError("league seed must be non-negative")
        self.league = league
        self.path = None if path is None else Path(path).expanduser().resolve()
        self.rng = random.Random(int(seed))

    @classmethod
    def load(cls, path: str | Path, *, seed: int = 0) -> CrforgeOpponentLeague:
        source = Path(path).expanduser().resolve()
        return cls(PolicyLeagueV4.load(source), path=source, seed=seed)

    @property
    def has_opponents(self) -> bool:
        return self.league.has_history

    @property
    def entries(self) -> tuple[LeagueEntryV4, ...]:
        return tuple(self.league.entries)

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
        self.league.save(destination)
        self.path = destination
        return destination

    def summary(self) -> dict[str, Any]:
        return {
            "schema": LEAGUE_SCHEMA,
            "path": None if self.path is None else str(self.path),
            "anchor_checkpoint_id": self.league.anchor_checkpoint_id,
            "anchor_checkpoint_path": self.league.anchor_checkpoint_path,
            "entries": [entry.to_dict() for entry in self.league.entries],
        }


def add_checkpoint(
    path: str | Path,
    checkpoint: str | Path,
    *,
    update_step: int | None = None,
) -> CrforgeOpponentLeague:
    """Load a league, add one checkpoint as a frozen opponent, and save it."""

    league = CrforgeOpponentLeague.load(path)
    checkpoint_id, recorded_step = checkpoint_identity(checkpoint)
    league.league.add_snapshot(
        checkpoint_id,
        checkpoint,
        update_step=recorded_step if update_step is None else int(update_step),
    )
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
    "OpponentPlan",
    "add_checkpoint",
    "checkpoint_identity",
    "create_league",
]
