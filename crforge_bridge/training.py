"""The C4 training loop: one persistent optimizer over the stored rollouts.

The C4 producer (``tools/crforge/self_play.py``) finalizes one
``StoredRolloutV4`` per segment.  Session 28 proved the update mechanics with a
**fresh AdamW per segment**, which is a correctness gate, not a trainer: the
momentum buffers did not span a segment boundary and the loop had no stated
policy for the recurrent state when the weights changed underneath it.

This module owns both decisions, so the producer only collects and steps:

* **One optimizer for the whole run.**  It is created once, steps in place, and
  its step counter and moment buffers survive every segment.  The pinned
  ``B-general-v275`` seed carries no optimizer state, so a fresh AdamW *is* the
  honest continuation point; a run resumed from a crforge checkpoint should load
  the saved optimizer state with the weights.
* **An explicit recurrence policy at an update boundary** (``state_policy``):

  - ``carry`` — the recurrent state survives the update.  This is the deployed
    inference shape and what session 28 measured, but the state was produced by
    the pre-update weights.
  - ``reset`` — the recurrent state is zeroed after every update, so the LSTM
    never holds state computed by weights it no longer has.  The game continues:
    only the model's memory is cut, not the episode.
  - ``wave`` — the behaviour weights stay frozen for ``segments_per_wave``
    segments, their updates are applied at the wave end, and then the recurrence
    resets.  This is the native trainer's collect-then-update cadence and the
    closest crforge shape to "one rollout per update".

Every update records a **reward/return trace** row: reward sum, return moments,
value explained variance, KL, clip fraction, gradient norm, the optimizer step
index, and the behaviour-weight hash before and after.  A training run passes
only if at least two consecutive updates happened and every trace row is finite,
because one update cannot show that a loop works.

The update still runs per stored segment even in ``wave`` mode.  Each segment
ends in a truncation with its own boundary bootstrap, so GAE must not be
computed across two segments; the wave changes *when* the optimizer steps, not
what one update sees.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from native_runner.training.v4.distributed_ppo import distributed_ppo_update_v4
from native_runner.training.v4.ppo import PPOConfigV4, StoredRolloutV4
from native_runner.training.v4.tensors import RecurrentPolicyStateV4

STATE_POLICIES = ("carry", "reset", "wave")


def _weight_bytes(model: nn.Module) -> list[tuple[str, bytes]]:
    """Named parameter/buffer bytes in a stable order, for identity hashes."""

    chunks: list[tuple[str, bytes]] = []
    for group, tensors in (("parameter", model.named_parameters()), ("buffer", model.named_buffers())):
        for name, tensor in sorted(tensors):
            value = tensor.detach().to(device="cpu")
            if value.is_floating_point():
                value = value.to(torch.float32)
            chunks.append((f"{group}:{name}", value.contiguous().numpy().tobytes()))
    return chunks


def weights_digest(model: nn.Module) -> str:
    """Hash the parameter and buffer bytes, the behaviour's real identity."""

    digest = hashlib.sha256()
    for name, payload in _weight_bytes(model):
        digest.update(name.encode("utf-8"))
        digest.update(payload)
    return digest.hexdigest()[:32]


def policy_state_id(model: nn.Module, checkpoint_id: str) -> str:
    """Identity of the sampling weights *and* the checkpoint they came from."""

    return hashlib.sha256(f"{checkpoint_id}:{weights_digest(model)}".encode()).hexdigest()[:32]


def weights_finite(model: nn.Module) -> bool:
    """True when every parameter and floating buffer is finite."""

    for tensor in (*model.parameters(), *model.buffers()):
        if tensor.is_floating_point() and not bool(torch.isfinite(tensor).all()):
            return False
    return True


@dataclass(frozen=True)
class CrforgeTrainingConfig:
    """Persistent-optimizer PPO settings, recorded on the run artifact."""

    state_policy: str = "reset"
    segments_per_wave: int = 1
    learning_rate: float = 1e-5
    epochs: int = 1
    lane_minibatch_size: int = 0
    sequence_chunk_steps: int = 32
    clip_range: float = 0.1
    value_coefficient: float = 0.5
    entropy_coefficient: float = 0.0
    max_grad_norm: float | None = 1.0
    adam_eps: float = 1e-5
    weight_decay: float = 0.0
    gamma: float = 0.9997
    gae_lambda: float = 0.98
    validate: bool = True

    def __post_init__(self) -> None:
        if self.state_policy not in STATE_POLICIES:
            raise ValueError(f"unknown update state policy {self.state_policy!r}")
        if self.segments_per_wave < 1:
            raise ValueError("segments per wave must be positive")
        if self.state_policy == "wave" and self.segments_per_wave < 2:
            raise ValueError("the wave state policy needs at least two segments per wave")
        if self.state_policy != "wave" and self.segments_per_wave != 1:
            raise ValueError("segments per wave only applies to the wave state policy")
        if not math.isfinite(self.learning_rate) or self.learning_rate <= 0.0:
            raise ValueError("PPO learning rate must be finite and positive")
        if self.epochs < 1 or self.sequence_chunk_steps < 1 or self.lane_minibatch_size < 0:
            raise ValueError("PPO epochs, chunk length and lane minibatch are invalid")
        if not math.isfinite(self.clip_range) or self.clip_range <= 0.0:
            raise ValueError("PPO clip range must be finite and positive")
        if not math.isfinite(self.value_coefficient) or self.value_coefficient < 0.0:
            raise ValueError("PPO value coefficient must be finite and non-negative")
        if not math.isfinite(self.entropy_coefficient) or self.entropy_coefficient < 0.0:
            raise ValueError("PPO entropy coefficient must be finite and non-negative")
        if self.max_grad_norm is not None and (not math.isfinite(self.max_grad_norm) or self.max_grad_norm <= 0.0):
            raise ValueError("PPO max gradient norm must be finite and positive, or None")
        if not math.isfinite(self.adam_eps) or self.adam_eps <= 0.0:
            raise ValueError("AdamW epsilon must be finite and positive")
        if not math.isfinite(self.weight_decay) or self.weight_decay < 0.0:
            raise ValueError("AdamW weight decay must be finite and non-negative")
        for value, label in ((self.gamma, "gamma"), (self.gae_lambda, "gae_lambda")):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"PPO {label} must be in [0,1]")

    @property
    def collects_waves(self) -> bool:
        return self.state_policy == "wave"

    @property
    def resets_recurrence(self) -> bool:
        """Whether the recurrent state is zeroed after an update boundary."""

        return self.state_policy in ("reset", "wave")

    def as_dict(self) -> dict[str, Any]:
        return {
            "state_policy": self.state_policy,
            "segments_per_wave": self.segments_per_wave,
            "recurrence_reset_at_update_boundary": self.resets_recurrence,
            "collects_whole_waves_with_frozen_weights": self.collects_waves,
            "learning_rate": self.learning_rate,
            "epochs": self.epochs,
            "lane_minibatch_size": self.lane_minibatch_size,
            "sequence_chunk_steps": self.sequence_chunk_steps,
            "clip_range": self.clip_range,
            "value_coefficient": self.value_coefficient,
            "entropy_coefficient": self.entropy_coefficient,
            "max_grad_norm": self.max_grad_norm,
            "adam": {"name": "AdamW", "eps": self.adam_eps, "weight_decay": self.weight_decay},
            "gamma": self.gamma,
            "gae_lambda": self.gae_lambda,
            "validate": self.validate,
        }

    def ppo_config(self) -> PPOConfigV4:
        return PPOConfigV4(
            gamma=self.gamma,
            gae_lambda=self.gae_lambda,
            clip_range=self.clip_range,
            value_clip_range=self.clip_range,
            value_coefficient=self.value_coefficient,
            entropy_coefficient=self.entropy_coefficient,
            max_grad_norm=self.max_grad_norm,
        )


def _metric_mean(rows: list[dict[str, float]], key: str) -> float:
    values = [float(row[key]) for row in rows if key in row]
    return sum(values) / len(values) if values else 0.0


def _metric_last(rows: list[dict[str, float]], key: str) -> float:
    values = [float(row[key]) for row in rows if key in row]
    return values[-1] if values else 0.0


class CrforgeTrainer:
    """Persistent-optimizer PPO over the producer's per-segment rollouts."""

    def __init__(
        self,
        model: nn.Module,
        *,
        config: CrforgeTrainingConfig,
        checkpoint_id: str,
        device: str | torch.device = "cpu",
        base_update_step: int = 0,
    ) -> None:
        if not checkpoint_id:
            raise ValueError("a trainer must bind the checkpoint it was initialized from")
        self.model = model
        self.config = config
        self.device = torch.device(device)
        self.checkpoint_id = str(checkpoint_id)
        self.base_update_step = int(base_update_step)
        # One optimizer for the whole run: momentum spans every segment/wave.
        self.optimizer = torch.optim.AdamW(
            model.parameters(), lr=config.learning_rate, eps=config.adam_eps, weight_decay=config.weight_decay
        )
        self.updates = 0
        self.optimizer_steps = 0
        self.pending: list[tuple[int, StoredRolloutV4, str]] = []
        self.trace: list[dict[str, Any]] = []
        self.update_entries: list[dict[str, Any]] = []

    # -- the boundary policy --------------------------------------------------

    def boundary_state(self, state: RecurrentPolicyStateV4, *, rows: int) -> RecurrentPolicyStateV4:
        """The recurrent state the next segment starts from after an update."""

        if not self.config.resets_recurrence:
            return state
        return self.model.initial_state(rows, device=self.device, dtype=state.hidden.dtype)  # type: ignore[attr-defined]

    # -- update scheduling ----------------------------------------------------

    def enqueue(
        self, rollout: StoredRolloutV4, *, segment: int, expected_policy_state_id: str
    ) -> list[dict[str, Any]] | None:
        """Queue one finalized segment; run its update when the wave closes.

        Returns the update entries when this segment closed a wave, otherwise
        ``None``: in ``carry``/``reset`` mode every segment closes its own wave.
        """

        if not expected_policy_state_id:
            raise ValueError("a stored rollout must name the weights that sampled it")
        self.pending.append((int(segment), rollout, str(expected_policy_state_id)))
        if len(self.pending) < self.config.segments_per_wave:
            return None
        return self.flush()

    def flush(self) -> list[dict[str, Any]]:
        """Run the pending segments' updates (a partial wave at the run end)."""

        updates: list[dict[str, Any]] = []
        for segment, rollout, expected in self.pending:
            updates.append(self._update(segment, rollout, expected))
        self.pending.clear()
        return updates

    def _update(self, segment: int, rollout: StoredRolloutV4, expected_policy_state_id: str) -> dict[str, Any]:
        rows = int(rollout.batch_size)
        minibatch = int(self.config.lane_minibatch_size) or rows
        minibatch = min(minibatch, rows)
        optimizer_step = self.optimizer_steps
        started = time.perf_counter()
        metrics = distributed_ppo_update_v4(
            self.model,
            self.optimizer,
            rollout,
            epochs=int(self.config.epochs),
            lane_minibatch_size=minibatch,
            sequence_chunk_steps=int(self.config.sequence_chunk_steps),
            config=self.config.ppo_config(),
            expected_policy_state_id=expected_policy_state_id,
            autocast_dtype=None,
            validate=bool(self.config.validate),
        )
        wall = time.perf_counter() - started
        rows_with_metrics = [row for row in metrics if isinstance(row, dict)]
        valid = sum(float(row.get("valid_count", 0.0)) for row in rows_with_metrics)
        finite = bool(rows_with_metrics) and all(
            math.isfinite(float(value))
            for row in rows_with_metrics
            for value in row.values()
            if isinstance(value, (int, float))
        )
        weights_ok = weights_finite(self.model)
        if finite and weights_ok:
            # Only a finite update advances the loop's counters; a poisoned
            # update must not look like a successful step in the trace.
            self.updates += 1
            self.optimizer_steps = optimizer_step + len(rows_with_metrics)

        reward = rollout.rewards[rollout.valid_mask]
        reward_sum = float(reward.sum()) if reward.numel() else 0.0
        entry = {
            "segment": int(segment),
            "update_index": self.updates,
            "optimizer_step": optimizer_step,
            "optimizer_steps": len(rows_with_metrics),
            "epochs": int(self.config.epochs),
            "lane_minibatch_size": minibatch,
            "sequence_chunk_steps": int(self.config.sequence_chunk_steps),
            "time_steps": int(rollout.time_steps),
            "batch_size": rows,
            "valid_count": valid,
            "wall_s": wall,
            "finite": finite and weights_ok,
            "metrics_finite": finite,
            "weights_finite": weights_ok,
            "policy_state_id_before": str(expected_policy_state_id),
            "policy_state_id_after": policy_state_id(self.model, self.checkpoint_id),
            "metrics": metrics,
            "pass": finite and weights_ok and valid > 0.0,
        }
        self.update_entries.append(entry)
        self.trace.append(
            {
                "update_index": self.updates,
                "segment": int(segment),
                "optimizer_step": optimizer_step,
                "optimizer_steps": len(rows_with_metrics),
                "time_steps": int(rollout.time_steps),
                "valid_count": valid,
                "reward_sum": reward_sum,
                "reward_mean": _metric_last(rows_with_metrics, "reward_mean"),
                "return_mean": _metric_last(rows_with_metrics, "return_mean"),
                "return_std": _metric_last(rows_with_metrics, "return_std"),
                "value_explained_variance": _metric_last(rows_with_metrics, "value_explained_variance"),
                "approx_kl": _metric_mean(rows_with_metrics, "approx_kl"),
                "clip_fraction": _metric_mean(rows_with_metrics, "clip_fraction"),
                "gradient_norm": _metric_last(rows_with_metrics, "gradient_norm"),
                "policy_state_id_before": str(expected_policy_state_id),
                "policy_state_id_after": entry["policy_state_id_after"],
                "finite": entry["finite"],
            }
        )
        return entry

    # -- the gate -------------------------------------------------------------

    @property
    def distinct_behaviour_ids(self) -> int:
        return len({str(entry["policy_state_id_after"]) for entry in self.update_entries})

    def gate(self) -> dict[str, Any]:
        """The training gate: two consecutive updates and a finite trace."""

        trace_finite = all(bool(row["finite"]) for row in self.trace) if self.trace else False
        returns = [float(row["return_mean"]) for row in self.trace]
        rewards = [float(row["reward_sum"]) for row in self.trace]
        two_updates = self.updates >= 2
        passed = (
            bool(self.trace)
            and trace_finite
            and two_updates
            and all(bool(entry["pass"]) for entry in self.update_entries)
        )
        return {
            "updates": self.updates,
            "optimizer_steps": self.optimizer_steps,
            "segments_updated": len(self.update_entries),
            "distinct_behaviour_ids": self.distinct_behaviour_ids,
            "two_consecutive_updates": two_updates,
            "trace_finite": trace_finite,
            "reward_trace": rewards,
            "return_trace": returns,
            "reward_trace_finite": all(math.isfinite(value) for value in rewards),
            "return_trace_finite": all(math.isfinite(value) for value in returns),
            "pass": passed,
        }

    def checkpoint_state(self) -> dict[str, Any]:
        """The loop counters a saved checkpoint should carry."""

        return {
            "updates": self.updates,
            "optimizer_steps": self.optimizer_steps,
            "base_update_step": self.base_update_step,
            "state_policy": self.config.state_policy,
            "segments_per_wave": self.config.segments_per_wave,
            "distinct_behaviour_ids": self.distinct_behaviour_ids,
        }


__all__ = [
    "STATE_POLICIES",
    "CrforgeTrainer",
    "CrforgeTrainingConfig",
    "policy_state_id",
    "weights_digest",
    "weights_finite",
]
