"""C4 rollout storage and the native-shape reward for crforge self-play.

The producer loop (``tools/crforge/self_play.py``) sampled clean, greedy-free
decisions but stored nothing, so it could not be a trainer: a PPO update needs
the observation batch, the sampled action, the behaviour log-probability and
value, a reward, and the episode boundary that tells the recurrence to reset.
This module owns both halves of that packet, and they are deliberately separate:

* :class:`PotentialShapingReward` reproduces ``BattleEnvV1._rewards`` from the
  vectorized facts.  The engine's own reward is the sparse terminal result
  (``LadderEnv.reward``: +1 / 0 / -1), while the native PPO path adds
  potential-based shaping over crowns and tower damage
  (``native_runner.battle_env``, ``shaping_beta=0.05``,
  ``shaping_tau_ticks`` derived from ``gamma_per_decision=0.9997`` at five-tick
  decisions).  A crforge rollout must not silently train on a different reward
  function, so the shaping term is rebuilt here from the same quantities the
  native env reads, and the unit test compares it against
  ``BattleEnvV1._potential`` / ``_rewards`` on the same towers.
* :class:`CrforgeRolloutStore` accumulates one decision per call and
  :meth:`CrforgeRolloutStore.finalize` returns the shared
  :class:`StoredRolloutV4` record, so the C4 producer feeds the same rollout
  contract the native path uses (and the same GAE implementation in
  ``native_runner.training.v4.learning``).

Two semantics are recorded rather than guessed:

* **A terminal zeroes the potential.**  ``_rewards`` sets ``current = 0`` when
  the match ended, which is what makes the shaping policy-invariant; the
  terminal decision therefore carries the terminal result plus the closing
  shaping term.
* **A time-limit cut carries no shaping term.**  ``max_ticks`` ends the episode
  and the vector autoresets the lane on the next step, so the producer cannot
  read the potential of the cut state.  The decision is stored as
  ``truncated`` with the terminal result (0) and a zero shaping term instead of
  manufacturing a drop from the old potential to the new episode's zero.

The rollout itself is per decision window: rows are ``lane * seats + seat``,
exactly the order the producer interleaves its two actor seats in.  A lane may
contain more than one episode (the vector autoresets it mid-rollout); the
``episode_start`` mask carries that boundary into the recurrence and the GAE
never propagates across it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, fields, is_dataclass

import numpy as np
import torch
from torch import Tensor

from native_runner.training.v4.ppo import StoredRolloutV4
from native_runner.training.v4.tensors import ActionSequenceV4, RecurrentPolicyStateV4, UniversalSemanticBatchV4

# ``ppo_runtime.ppo_environment_config_v4`` defaults: the native PPO reward.
DEFAULT_GAMMA_PER_DECISION = 0.9997
DEFAULT_SHAPING_BETA = 0.05
DEFAULT_DECISION_TICKS = 5


@dataclass(frozen=True)
class CrforgeRewardConfig:
    """The native PPO reward's two knobs, as ``EnvironmentConfigV1`` names them."""

    shaping_beta: float = DEFAULT_SHAPING_BETA
    gamma_per_decision: float = DEFAULT_GAMMA_PER_DECISION
    decision_ticks: int = DEFAULT_DECISION_TICKS

    def __post_init__(self) -> None:
        if not math.isfinite(self.shaping_beta) or self.shaping_beta < 0.0:
            raise ValueError("crforge shaping beta must be finite and non-negative")
        if not math.isfinite(self.gamma_per_decision) or not 0.0 < self.gamma_per_decision < 1.0:
            raise ValueError("crforge gamma per decision must be finite and in (0,1)")
        if self.decision_ticks < 1:
            raise ValueError("crforge decision ticks must be positive")

    @property
    def tau_ticks(self) -> float:
        """The decay the native env derives so ``exp(-ticks/tau) == gamma``."""

        return self.decision_ticks / -math.log(self.gamma_per_decision)

    @property
    def shaping_gamma(self) -> float:
        """Potential decay over one policy decision (native ``delta_ticks``)."""

        return math.exp(-self.decision_ticks / self.tau_ticks)

    def as_dict(self) -> dict[str, float]:
        return {
            "shaping_beta": self.shaping_beta,
            "gamma_per_decision": self.gamma_per_decision,
            "decision_ticks": self.decision_ticks,
            "tau_ticks": self.tau_ticks,
            "shaping_gamma": self.shaping_gamma,
        }


# A princess tower counts one; the king counts three (``BattleEnvV1._potential``).
_TOWER_WEIGHTS = np.array([3.0, 1.0, 1.0], dtype=np.float64)


class PotentialShapingReward:
    """``BattleEnvV1._rewards`` over the vectorized facts, per decision window.

    ``own_tower_hp[:, seat, i]`` is the seat's own towers in layout order
    ``(king, left, right)``; ``enemy_tower_*`` is the same list for its opponent.
    Only seat 0's potential is evaluated and mirrored, which is how the native
    env keeps the value exactly antisymmetric.
    """

    def __init__(self, count: int, *, config: CrforgeRewardConfig | None = None) -> None:
        if int(count) < 1:
            raise ValueError("a reward tracker needs at least one lane")
        self.config = config or CrforgeRewardConfig()
        self._last = np.zeros((int(count), 2), dtype=np.float64)
        self.decisions = 0
        self.terminated_decisions = 0
        self.truncated_decisions = 0
        self.reward_sum = 0.0
        self.reward_abs_sum = 0.0
        self.nonzero_rows = 0

    @property
    def last_potential(self) -> np.ndarray:
        """The per-seat potential the next decision's shaping starts from."""

        return self._last

    def reset(self, lanes: np.ndarray | None = None) -> None:
        if lanes is None:
            self._last.fill(0.0)
            return
        self._last[np.asarray(lanes, dtype=bool)] = 0.0

    def potential(self, batch: object) -> np.ndarray:
        """Per-seat potential, ``(lanes, 2)``, exactly antisymmetric."""

        own = np.asarray(batch.own_tower_hp, dtype=np.float64)
        own_max = np.asarray(batch.own_tower_max_hp, dtype=np.float64)
        enemy = np.asarray(batch.enemy_tower_hp, dtype=np.float64)
        enemy_max = np.asarray(batch.enemy_tower_max_hp, dtype=np.float64)
        if own.ndim != 3 or own.shape[2] != 3 or enemy.shape != own.shape:
            raise ValueError("a lane carries three own and three enemy tower rows")
        # ``_potential``: ``loss = 1 - hitpoints / max(1, initial)``, no clamping.
        own_loss = _TOWER_WEIGHTS * (1.0 - own / np.maximum(1.0, own_max))
        enemy_loss = _TOWER_WEIGHTS * (1.0 - enemy / np.maximum(1.0, enemy_max))
        crowns = np.asarray(batch.crowns, dtype=np.float64) - np.asarray(batch.crowns_enemy, dtype=np.float64)
        value = crowns + enemy_loss.sum(axis=2) - own_loss.sum(axis=2)
        # The two seats must describe the same board; average them so the pair
        # is antisymmetric even if one header column is rounded differently.
        symmetric = 0.5 * (value[:, 0] - value[:, 1])
        return np.stack((symmetric, -symmetric), axis=1)

    def advance(
        self,
        batch: object,
        *,
        terminal_reward: np.ndarray,
        terminated: np.ndarray,
        truncated: np.ndarray,
        reset: np.ndarray | None = None,
    ) -> np.ndarray:
        """One decision's reward from the post-window facts, ``(lanes, 2)``.

        ``terminal_reward`` is the engine's terminal result per seat in
        ``{-1, 0, 1}`` (0 while running and on a draw), ``terminated`` /
        ``truncated`` are the per-lane episode ends the producer observed, and
        ``reset`` optionally names lanes whose potential must start from zero.
        """

        reward = np.asarray(terminal_reward, dtype=np.float64)
        ended = np.asarray(terminated, dtype=bool)
        cut = np.asarray(truncated, dtype=bool)
        if reward.ndim != 2 or reward.shape[1] != 2:
            raise ValueError("terminal reward must be (lanes, 2)")
        lanes = reward.shape[0]
        if ended.shape != (lanes,) or cut.shape != (lanes,):
            raise ValueError("episode-end masks must have one entry per lane")
        if np.any(ended & cut):
            raise ValueError("a decision cannot terminate and truncate")
        if self._last.shape != (lanes, 2):
            raise ValueError("reward tracker was built for a different lane count")

        current = self.potential(batch)
        # Zero terminal potential is what makes the shaping policy-invariant.
        current[ended] = 0.0
        shaping = self.config.shaping_beta * (self.config.shaping_gamma * current - self._last)
        # A time-limit cut already autoreset the lane: its post-window facts are
        # the new episode, so there is no honest potential to shape from.
        shaping[cut] = 0.0
        raw = reward + shaping
        symmetric = 0.5 * (raw[:, 0] - raw[:, 1])
        result = np.stack((symmetric, -symmetric), axis=1)

        self._last = current
        self._last[cut] = 0.0
        if reset is not None:
            self._last[np.asarray(reset, dtype=bool)] = 0.0

        self.decisions += 1
        self.terminated_decisions += int(ended.sum())
        self.truncated_decisions += int(cut.sum())
        self.reward_sum += float(result.sum())
        self.reward_abs_sum += float(np.abs(result).sum())
        self.nonzero_rows += int(np.count_nonzero(result))
        return result


def rollout_next_values(
    values: Tensor, terminated: Tensor, truncated: Tensor, *, boundary_value: Tensor | None
) -> tuple[Tensor, Tensor]:
    """Next-step values and the segment-boundary truncation mask.

    A real terminal never bootstraps.  A time-limit truncation inside the
    rollout (``max_ticks``) also bootstraps from zero, because the engine has
    already started a fresh episode on that lane.  Only the **last** transition
    of the segment is a rollout cut: it bootstraps from the value of the final
    observation, which the producer computes with one extra forward.
    """

    if values.ndim != 2:
        raise ValueError("rollout values must be [T,B]")
    steps, rows = values.shape
    if terminated.shape != (steps, rows) or truncated.shape != (steps, rows):
        raise ValueError("rollout episode masks must be [T,B]")
    if torch.any(terminated & truncated):
        raise ValueError("a transition cannot terminate and truncate")
    zeros = torch.zeros_like(values)
    next_values = torch.zeros_like(values)
    if steps > 1:
        stopped = terminated[:-1] | truncated[:-1]
        next_values[:-1] = torch.where(stopped, zeros[:-1], values[1:])
    boundary = ~terminated[-1]
    if boundary_value is None:
        next_values[-1] = zeros[-1]
    else:
        next_values[-1] = torch.where(boundary, boundary_value.to(values.device, values.dtype), zeros[-1])
    # A cut inside the rollout stays truncated; the segment end becomes one too,
    # so GAE never propagates a trace across a real episode boundary.
    final_truncated = truncated.clone()
    final_truncated[-1] = truncated[-1] | boundary
    return next_values, final_truncated


@dataclass(slots=True)
class RolloutSummary:
    """What one segment's store retained, for the run artifact."""

    decisions: int
    rows: int
    episode_starts: int
    terminated: int
    truncated: int
    act_rows: int
    rewards: Tensor
    values: Tensor
    log_prob: Tensor

    def as_dict(self) -> dict[str, object]:
        reward = self.rewards
        values = self.values
        log_prob = self.log_prob
        return {
            "decisions": self.decisions,
            "rows": self.rows,
            "episode_starts": self.episode_starts,
            "terminated": self.terminated,
            "truncated": self.truncated,
            "act_rows": self.act_rows,
            "reward_sum": float(reward.sum()),
            "reward_abs_sum": float(reward.abs().sum()),
            "reward_nonzero_rows": int(torch.count_nonzero(reward)),
            "reward_min": float(reward.min()) if reward.numel() else 0.0,
            "reward_max": float(reward.max()) if reward.numel() else 0.0,
            "value_min": float(values.min()) if values.numel() else 0.0,
            "value_max": float(values.max()) if values.numel() else 0.0,
            "log_prob_min": float(log_prob.min()) if log_prob.numel() else 0.0,
            "log_prob_max": float(log_prob.max()) if log_prob.numel() else 0.0,
        }


def _rollout_float(value: object, *, label: str) -> Tensor:
    """A finite float32 actor-row vector that autograd can save.

    The producer samples under ``inference_mode``; ``detach``/``reshape`` keep
    that tensor kind, and a PPO backward then refuses it.  Cloning outside
    inference mode is what ``TensorRecordV4.to_storage`` does for the same
    reason.
    """

    tensor = torch.as_tensor(value, dtype=torch.float32).detach().reshape(-1)
    if torch.is_inference(tensor):
        with torch.inference_mode(False):
            tensor = tensor.clone()
    if not torch.isfinite(tensor).all():
        raise ValueError(f"rollout {label} must be finite")
    return tensor


class CrforgeRolloutStore:
    """Accumulate one decision window per :meth:`append`; finalize to PPO data."""

    def __init__(
        self,
        *,
        lanes: int,
        seats: int,
        initial_state: RecurrentPolicyStateV4,
        policy_state_id: str,
        gate_temperature: float,
        action_temperature: float,
        continue_temperature: float,
        device: str | torch.device = "cpu",
        float_dtype: torch.dtype | None = torch.float16,
        train_rows: Sequence[bool] | np.ndarray | None = None,
    ) -> None:
        if lanes < 1 or seats < 1:
            raise ValueError("a rollout store needs at least one lane and seat")
        if initial_state.hidden.shape[0] != lanes * seats:
            raise ValueError("the initial recurrent state must carry one row per actor")
        if not policy_state_id:
            raise ValueError("a rollout store must bind the behaviour policy state")
        self.lanes = int(lanes)
        self.seats = int(seats)
        self.rows = self.lanes * self.seats
        self.train_rows = self._train_row_mask(train_rows)
        self.device = torch.device(device)
        self.float_dtype = float_dtype
        self.initial_state = initial_state
        self.policy_state_id = str(policy_state_id)
        self.gate_temperature = float(gate_temperature)
        self.action_temperature = float(action_temperature)
        self.continue_temperature = float(continue_temperature)
        self.observations: list[UniversalSemanticBatchV4] = []
        self.actions: list[ActionSequenceV4] = []
        self._rewards: list[Tensor] = []
        self._values: list[Tensor] = []
        self._log_prob: list[Tensor] = []
        self._episode_start: list[Tensor] = []
        self._terminated: list[Tensor] = []
        self._truncated: list[Tensor] = []
        self.stats = {"episode_starts": 0, "terminated": 0, "truncated": 0, "act_rows": 0, "storage_bytes": 0}

    # -- collection -----------------------------------------------------------

    def _train_row_mask(self, value: Sequence[bool] | np.ndarray | None) -> Tensor:
        if value is None:
            return torch.ones(self.rows, dtype=torch.bool)
        mask = torch.as_tensor(value, dtype=torch.bool).reshape(-1)
        if mask.numel() != self.rows:
            raise ValueError(f"train_rows carries {mask.numel()} rows, the store expects {self.rows}")
        if not bool(mask.any()):
            raise ValueError("at least one actor row must be trainable")
        return mask

    @staticmethod
    def _row_mask(mask: np.ndarray, rows: int, *, label: str, device: torch.device | None = None) -> Tensor:
        value = np.asarray(mask)
        if value.ndim != 1:
            raise ValueError(f"{label} must be a per-lane mask")
        if rows % value.shape[0]:
            raise ValueError(f"{label} lane count does not divide the actor rows")
        repeated = np.repeat(value.astype(bool), rows // value.shape[0])
        return torch.from_numpy(repeated).to(torch.bool).to(device)

    def append(
        self,
        *,
        observation: UniversalSemanticBatchV4,
        actions: ActionSequenceV4,
        log_prob: Tensor,
        value: Tensor,
        rewards: np.ndarray,
        episode_start: np.ndarray,
        terminated: np.ndarray,
        truncated: np.ndarray,
    ) -> None:
        """Store one decision window for every actor row."""

        if int(observation.batch_size) != self.rows:
            raise ValueError(f"observation carries {observation.batch_size} rows, the store expects {self.rows}")
        if int(actions.gate.shape[0]) != self.rows:
            raise ValueError("action rows and observation rows differ")
        reward = np.asarray(rewards, dtype=np.float32)
        if reward.shape != (self.lanes, self.seats):
            raise ValueError("rewards must be (lanes, seats)")
        log_prob = _rollout_float(log_prob, label="log-probability").to(self.device)
        value = _rollout_float(value, label="value").to(self.device)
        if log_prob.numel() != self.rows or value.numel() != self.rows:
            raise ValueError("log-probability and value need one entry per actor row")
        episode_start_rows = self._row_mask(episode_start, self.rows, label="episode_start", device=self.device)
        terminated_rows = self._row_mask(terminated, self.rows, label="terminated", device=self.device)
        truncated_rows = self._row_mask(truncated, self.rows, label="truncated", device=self.device)
        if bool((terminated_rows & truncated_rows).any()):
            raise ValueError("a decision cannot terminate and truncate")

        stored_observation = observation.to_storage(self.device, float_dtype=self.float_dtype)
        stored_actions = actions.to_storage(self.device, float_dtype=self.float_dtype)
        self.observations.append(stored_observation)
        self.actions.append(stored_actions)
        self._rewards.append(torch.from_numpy(reward.reshape(-1).copy()).to(self.device))
        self._values.append(value)
        self._log_prob.append(log_prob)
        self._episode_start.append(episode_start_rows)
        self._terminated.append(terminated_rows)
        self._truncated.append(truncated_rows)

        self.stats["episode_starts"] += int(episode_start_rows.sum())
        self.stats["terminated"] += int(terminated_rows.sum())
        self.stats["truncated"] += int(truncated_rows.sum())
        self.stats["act_rows"] += int((torch.as_tensor(actions.gate).reshape(-1) != 0).sum())
        self.stats["storage_bytes"] += _record_bytes(stored_observation) + _record_bytes(stored_actions)

    # -- finalization ---------------------------------------------------------

    def summary(self) -> RolloutSummary:
        return RolloutSummary(
            decisions=len(self.observations),
            rows=self.rows,
            episode_starts=int(self.stats["episode_starts"]),
            terminated=int(self.stats["terminated"]),
            truncated=int(self.stats["truncated"]),
            act_rows=int(self.stats["act_rows"]),
            rewards=torch.stack(self._rewards) if self._rewards else torch.zeros((0, self.rows)),
            values=torch.stack(self._values) if self._values else torch.zeros((0, self.rows)),
            log_prob=torch.stack(self._log_prob) if self._log_prob else torch.zeros((0, self.rows)),
        )

    def finalize(self, *, boundary_value: Tensor | None = None) -> StoredRolloutV4:
        """Return the PPO record for the accumulated decisions.

        ``boundary_value`` is the value of the observation after the last stored
        decision (one extra forward).  It is the time-limit bootstrap for every
        lane still running at the segment boundary; without it those lanes are
        cut from the return with a zero bootstrap, which the artifact records.
        """

        if not self.observations:
            raise RuntimeError("a rollout store needs at least one decision")
        rewards = torch.stack(self._rewards)
        values = torch.stack(self._values)
        terminated = torch.stack(self._terminated)
        truncated = torch.stack(self._truncated)
        next_values, truncated = rollout_next_values(values, terminated, truncated, boundary_value=boundary_value)
        rollout = StoredRolloutV4(
            observations=tuple(self.observations),
            actions=tuple(self.actions),
            episode_start=torch.stack(self._episode_start),
            # The mask is an index in the PPO update, so it has to live beside the
            # tensors it masks: a CPU mask over CUDA rows made the league's
            # learner-only update index a CUDA tensor with a CPU index.
            valid_mask=self.train_rows.to(self.device).unsqueeze(0).expand_as(terminated).clone(),
            behavior_log_prob=torch.stack(self._log_prob),
            behavior_value=values,
            next_value=next_values,
            rewards=rewards,
            terminated=terminated,
            truncated=truncated,
            initial_state=self.initial_state,
            gate_temperature=self.gate_temperature,
            action_temperature=self.action_temperature,
            continue_temperature=self.continue_temperature,
            policy_state_id=self.policy_state_id,
        )
        rollout.validate()
        return rollout

    def storage_bytes(self) -> int:
        return int(self.stats["storage_bytes"])


def rollout_statistics(rollout: StoredRolloutV4, *, gamma: float, gae_lambda: float) -> dict[str, object]:
    """Advantages/returns plus the checks the C4 gate asks for."""

    advantages, returns = rollout.advantages_and_returns(gamma=gamma, gae_lambda=gae_lambda)
    finite = bool(torch.isfinite(advantages).all() and torch.isfinite(returns).all())

    def moments(value: Tensor) -> dict[str, float]:
        return {
            "mean": float(value.mean()),
            "std": float(value.std(unbiased=False)),
            "min": float(value.min()),
            "max": float(value.max()),
        }

    reward = rollout.rewards
    zero_sum = (
        float((reward[:, ::2] + reward[:, 1::2]).abs().max()) if reward.shape[1] % 2 == 0 and reward.numel() else 0.0
    )
    return {
        "time_steps": rollout.time_steps,
        "batch_size": rollout.batch_size,
        "finite": finite,
        "advantages": moments(advantages),
        "returns": moments(returns),
        "reward_zero_sum_max_deviation": zero_sum,
        "episode_starts": int(rollout.episode_start.sum()),
        "mid_rollout_episode_starts": int(rollout.episode_start[1:].sum()) if rollout.time_steps > 1 else 0,
        "terminated": int(rollout.terminated.sum()),
        "truncated": int(rollout.truncated.sum()),
        "valid_rows": int(rollout.valid_mask.sum()),
        "initial_state_rows": int(rollout.initial_state.hidden.shape[0]),
        "policy_state_id": rollout.policy_state_id,
    }


def _record_bytes(record: object) -> int:
    """Bytes one stored V4 record occupies, recursively."""

    if isinstance(record, torch.Tensor):
        return int(record.numel() * record.element_size())
    if is_dataclass(record):
        return sum(_record_bytes(getattr(record, item.name)) for item in fields(record))
    if isinstance(record, (tuple, list)):
        return sum(_record_bytes(item) for item in record)
    return 0


__all__ = [
    "DEFAULT_DECISION_TICKS",
    "DEFAULT_GAMMA_PER_DECISION",
    "DEFAULT_SHAPING_BETA",
    "CrforgeRewardConfig",
    "CrforgeRolloutStore",
    "PotentialShapingReward",
    "RolloutSummary",
    "rollout_next_values",
    "rollout_statistics",
]
