"""Drive a V4 checkpoint from live memory frames and ADB taps.

This is the only module that imports torch.  The reader runs in its own
process; the runner consumes its JSON lines, builds the FAIR observation,
runs one recurrent forward per policy turn, and sends the decoded card + tile
as Android touches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import torch

from ..contracts import ActionV1, EpisodeConfigV1, ObservationTier, ObservationV1
from ..training.v4.expert import FIRST_POLICY_DECISION_TICK
from ..training.v4.factory import build_episode_tensorizer_v4
from ..training.v4.policy_session import LoadedPolicyV4, PolicySessionV4
from .frames import LiveFrame, normalize_frame
from .observation import LiveObservationConfig, build_live_observation
from .taps import AdbTapper, ScreenLayout, taps_for_actions


@dataclass(frozen=True, slots=True)
class LiveDecision:
    tick: int
    actions: tuple[ActionV1, ...]
    taps: tuple[tuple[int, int], ...]
    inference_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "actions": [
                {
                    "kind": action.kind.value,
                    "hand_slot": action.hand_slot,
                    "card_id": action.card_id,
                    "target_grid": action.target_grid,
                }
                for action in self.actions
            ],
            "taps": [list(point) for point in self.taps],
            "inference_ms": self.inference_ms,
        }


class LivePolicyRunner:
    """One actor seat, one episode at a time."""

    def __init__(
        self,
        loaded: LoadedPolicyV4,
        observation_config: LiveObservationConfig,
        *,
        actor_owner: int = 0,
        device: str | torch.device = "cpu",
        sample: bool = True,
        layout: ScreenLayout | None = None,
        tapper: AdbTapper | None = None,
        dry_run: bool = False,
    ) -> None:
        if actor_owner not in (0, 1):
            raise ValueError("actor_owner must be 0 or 1")
        self.loaded = loaded
        self.observation_config = observation_config
        self.actor_owner = int(actor_owner)
        self.device = torch.device(device)
        self.sample = bool(sample)
        self.layout = layout
        self.tapper = tapper
        self.dry_run = bool(dry_run)
        self._session: PolicySessionV4 | None = None
        self._episode: EpisodeConfigV1 | None = None

    def episode_config(self) -> EpisodeConfigV1:
        if self._episode is None:
            decks = self.observation_config.decks
            self._episode = EpisodeConfigV1(
                ruleset_id=self.observation_config.ruleset_id,
                deck0=tuple(int(card) for card in decks[0]),
                deck1=tuple(int(card) for card in decks[1]),
                seed=0,
                observation_tier=ObservationTier.FAIR,
                game_mode=int(self.observation_config.game_mode),
                arena=int(self.observation_config.arena),
                tags={
                    "deck0_form_availability": (0,) * 8,
                    "deck1_form_availability": (0,) * 8,
                    "live": True,
                },
            )
        return self._episode

    def _build_session(self, observation: ObservationV1) -> PolicySessionV4:
        model = self.loaded.model
        tensorizer = build_episode_tensorizer_v4(
            self.episode_config(),
            actor_owner=self.actor_owner,
            bundle=getattr(model, "semantic_bundle", None),
            config=getattr(model, "config", None),
        )
        session = PolicySessionV4(model, tensorizer, device=self.device, sample=self.sample)
        session.start_episode(
            observation,
            initial_elixir={
                self.actor_owner: float(observation.players[0].elixir_exact or 6.0),
                1 - self.actor_owner: 6.0,
            },
        )
        return session

    def end(self) -> None:
        if self._session is not None:
            self._session.end_episode()
        self._session = None

    def step(self, frame: LiveFrame) -> LiveDecision | None:
        if not frame.active or frame.terminal:
            self.end()
            return None
        observation = build_live_observation(
            frame,
            actor_owner=self.actor_owner,
            config=self.observation_config,
            bundle=getattr(self.loaded.model, "semantic_bundle", None),
        )
        if self._session is None:
            self._session = self._build_session(observation)
        assert self._session is not None
        if observation.tick < FIRST_POLICY_DECISION_TICK:
            # Warm the tracker and recurrent path exactly as serve_policy does.
            self._session.tensorizer.tensorize(observation, validate=False)
            return None
        decision = self._session.decide(observation)
        actions = tuple(decision.decoded.actions)
        taps: tuple[tuple[int, int], ...] = ()
        if self.layout is not None:
            taps = taps_for_actions(actions, self.layout)
        if taps and self.tapper is not None and not self.dry_run:
            self.tapper.send(taps)
        return LiveDecision(
            tick=int(observation.tick),
            actions=actions,
            taps=taps,
            inference_ms=float(decision.inference_ms),
        )

    def run_frames(self, frames: Iterable[LiveFrame]) -> Iterable[LiveDecision]:
        for frame in frames:
            decision = self.step(frame)
            if decision is not None:
                yield decision


def normalize(frame: LiveFrame, config: LiveObservationConfig) -> LiveFrame:
    return normalize_frame(frame, decks=config.decks)
