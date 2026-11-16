"""The C4 rollout store and the native-shape reward it records.

The reward half is a differential test against ``BattleEnvV1._potential`` /
``_rewards`` on the same towers: crforge must not train on a different reward
function than the native path.  The storage half pins the PPO record's shape,
the episode boundary the recurrence resets on, and that GAE never propagates a
trace across a terminal.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from crforge_bridge.rollout import (
    CrforgeRewardConfig,
    CrforgeRolloutStore,
    PotentialShapingReward,
    rollout_next_values,
    rollout_statistics,
)
from native_runner.battle_env import BattleEnvV1
from native_runner.contracts import TerminalV1, TowerStateV1
from native_runner.training.v4.tensors import GATE_ACT, GATE_WAIT, ActionSequenceV4, RecurrentPolicyStateV4

_TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "crforge"
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import self_play


class _NativeRewardProbe:
    """The smallest object ``BattleEnvV1._rewards`` runs on."""

    def __init__(self, towers: tuple[TowerStateV1, ...], environment) -> None:
        self.possible_agents = (0, 1)
        self.shaping_beta = environment.shaping_beta
        self.shaping_tau_ticks = environment.shaping_tau_ticks
        self._initial_tower_hp = {item.entity_id: item.max_hitpoints for item in towers}
        self._last_potential = {0: 0.0, 1: 0.0}
        self._crowns = BattleEnvV1._crowns

    def _potential(self, owner: int, towers) -> float:
        return BattleEnvV1._potential(self, owner, towers)

    def rewards(self, before, after, terminal: TerminalV1, delta_ticks: int) -> dict[int, float]:
        return BattleEnvV1._rewards(self, before, after, terminal, delta_ticks)


def _towers(
    own_hp: tuple[int, int, int] = (4000, 3000, 3000), enemy_hp: tuple[int, int, int] = (4000, 3000, 3000)
) -> tuple[TowerStateV1, ...]:
    rows = []
    for owner, hp in ((0, own_hp), (1, enemy_hp)):
        for index, (kind, entity_id) in enumerate(
            (
                ("king", 0 if owner == 0 else 3),
                ("princess_left", 1 if owner == 0 else 4),
                ("princess_right", 2 if owner == 0 else 5),
            )
        ):
            rows.append(
                TowerStateV1(
                    entity_id=entity_id,
                    owner=owner,
                    tower_kind=kind,
                    position=(0.0, 0.0),
                    hitpoints=float(hp[index]),
                    max_hitpoints=4000.0 if kind == "king" else 3000.0,
                    active=hp[index] > 0,
                    tower_troop_id=None if kind == "king" else 159000000,
                )
            )
    return tuple(rows)


def _facts(towers: tuple[TowerStateV1, ...], *, crowns: tuple[int, int] = (0, 0)) -> SimpleNamespace:
    """A facts batch carrying only what the reward reads."""

    own_hp = np.zeros((1, 2, 3), dtype=np.int32)
    own_max = np.zeros((1, 2, 3), dtype=np.int32)
    enemy_hp = np.zeros((1, 2, 3), dtype=np.int32)
    enemy_max = np.zeros((1, 2, 3), dtype=np.int32)
    for index, tower in enumerate(towers):
        slot = index % 3
        if tower.owner == 0:
            own_hp[0, 0, slot] = int(tower.hitpoints)
            own_max[0, 0, slot] = int(tower.max_hitpoints)
            enemy_hp[0, 1, slot] = int(tower.hitpoints)
            enemy_max[0, 1, slot] = int(tower.max_hitpoints)
        else:
            own_hp[0, 1, slot] = int(tower.hitpoints)
            own_max[0, 1, slot] = int(tower.max_hitpoints)
            enemy_hp[0, 0, slot] = int(tower.hitpoints)
            enemy_max[0, 0, slot] = int(tower.max_hitpoints)
    return SimpleNamespace(
        own_tower_hp=own_hp,
        own_tower_max_hp=own_max,
        enemy_tower_hp=enemy_hp,
        enemy_tower_max_hp=enemy_max,
        crowns=np.array([crowns], dtype=np.int32),
        crowns_enemy=np.array([[crowns[1], crowns[0]]], dtype=np.int32),
    )


def test_potential_shaping_reproduces_the_native_reward() -> None:
    # The native PPO environment derives tau from gamma; the probe uses that,
    # not the reward config's own property.
    from native_runner.training.v4.ppo_runtime import ppo_environment_config_v4

    environment = ppo_environment_config_v4(gamma_per_decision=0.9997, shaping_beta=0.05)
    config = CrforgeRewardConfig(shaping_beta=0.05)
    reward = PotentialShapingReward(1, config=config)
    before = _towers()
    after = _towers(own_hp=(2800, 3000, 900), enemy_hp=(4000, 3000, 1200))
    probe = _NativeRewardProbe(before, environment)

    # A non-terminal decision: both sides lose one princess and damage the king.
    assert reward.potential(_facts(before))[0, 0] == pytest.approx(0.0, abs=1e-12)
    expected = probe.rewards(before, after, TerminalV1(), config.decision_ticks)
    stored = reward.advance(
        _facts(after),
        terminal_reward=np.zeros((1, 2)),
        terminated=np.zeros(1, dtype=bool),
        truncated=np.zeros(1, dtype=bool),
    )
    assert stored[0, 0] == pytest.approx(expected[0], abs=1e-12)
    assert stored[0, 1] == pytest.approx(expected[1], abs=1e-12)
    assert stored[0, 0] == pytest.approx(-stored[0, 1], abs=1e-12)

    # A terminal zeroes the potential, so the closing shaping term is counted.
    terminal = TerminalV1(ended=True, winner=0, result_by_owner=(1.0, -1.0), terminal_tick=600)
    expected = probe.rewards(after, after, terminal, config.decision_ticks)
    stored = reward.advance(
        _facts(after),
        terminal_reward=np.array([[1.0, -1.0]]),
        terminated=np.ones(1, dtype=bool),
        truncated=np.zeros(1, dtype=bool),
    )
    assert stored[0, 0] == pytest.approx(expected[0], abs=1e-12)
    assert stored[0, 1] == pytest.approx(expected[1], abs=1e-12)
    assert reward.last_potential[0, 0] == pytest.approx(0.0, abs=1e-12)


def test_a_time_limit_cut_does_not_shape_from_the_new_episode() -> None:
    config = CrforgeRewardConfig(shaping_beta=0.05)
    reward = PotentialShapingReward(1, config=config)
    reward.advance(
        _facts(_towers(own_hp=(2000, 3000, 3000))),
        terminal_reward=np.zeros((1, 2)),
        terminated=np.zeros(1, dtype=bool),
        truncated=np.zeros(1, dtype=bool),
    )
    assert reward.last_potential[0, 0] < 0.0
    # The engine has already autoreset the lane: the cut state is unknown, so the
    # decision stores the terminal result only and restarts from zero potential.
    stored = reward.advance(
        _facts(_towers()),
        terminal_reward=np.zeros((1, 2)),
        terminated=np.zeros(1, dtype=bool),
        truncated=np.ones(1, dtype=bool),
    )
    assert stored[0, 0] == pytest.approx(0.0)
    assert stored[0, 1] == pytest.approx(0.0)
    assert reward.last_potential[0, 0] == pytest.approx(0.0)


def test_rollout_next_values_stop_at_a_terminal_and_bootstrap_the_segment_end() -> None:
    values = torch.tensor([[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]])
    terminated = torch.tensor([[False, False], [True, False], [False, False]])
    truncated = torch.zeros(3, 2, dtype=torch.bool)
    next_values, final_truncated = rollout_next_values(
        values, terminated, truncated, boundary_value=torch.tensor([9.0, 9.0])
    )
    # Lane 0 ended on step 1: its next value is zero even though step 2 is the
    # next episode's value.
    assert next_values[1, 0] == pytest.approx(0.0)
    assert next_values[0, 0] == pytest.approx(0.3)
    assert next_values[1, 1] == pytest.approx(0.6)
    # Every lane still running at the segment end bootstraps from the boundary.
    assert next_values[2, 0] == pytest.approx(9.0)
    assert next_values[2, 1] == pytest.approx(9.0)
    assert final_truncated[2].all()
    assert not final_truncated[1, 1]
    assert not final_truncated[1, 0]


def test_gae_does_not_leak_across_the_terminal() -> None:
    from native_runner.training.v4.learning import generalized_advantage_estimate

    values = torch.zeros(4, 1)
    terminated = torch.tensor([[False], [True], [False], [False]])
    rewards = torch.tensor([[0.0], [1.0], [50.0], [50.0]])
    next_values, _ = rollout_next_values(
        values, terminated, torch.zeros(4, 1, dtype=torch.bool), boundary_value=torch.zeros(1)
    )
    advantages, returns = generalized_advantage_estimate(
        rewards, values, next_values, terminated, torch.zeros(4, 1, dtype=torch.bool), gamma=0.9997, gae_lambda=0.98
    )
    # The 50s after the terminal belong to the next episode: the trace stopped at
    # step 1, so the pre-terminal advantage is just the terminal reward.
    assert advantages[0, 0] == pytest.approx(0.9997 * 0.98 * 1.0)
    assert advantages[1, 0] == pytest.approx(1.0)
    assert returns[1, 0] == pytest.approx(1.0)
    assert advantages[2, 0] == pytest.approx(50.0 + 0.9997 * 0.98 * 50.0)


def _sequence(rows: int, steps: int = 2) -> ActionSequenceV4:
    gate = torch.zeros(rows, dtype=torch.long)
    count = torch.zeros(rows, dtype=torch.long)
    index = torch.full((rows, steps), -1, dtype=torch.long)
    uid = torch.full((rows, steps), -1, dtype=torch.long)
    cell = torch.full((rows, steps), -1, dtype=torch.long)
    delay = torch.full((rows, steps), -1, dtype=torch.long)
    for row in range(rows):
        # Seat 0 of every lane acts; seat 1 waits.
        gate[row] = GATE_ACT if row % 2 == 0 else GATE_WAIT
        if gate[row] == GATE_ACT:
            count[row] = 1
            index[row, 0] = row
            uid[row, 0] = 1000 + row
            cell[row, 0] = 16 * 10 + 6
            delay[row, 0] = 0
    return ActionSequenceV4(
        gate=gate,
        micro_action_count=count,
        candidate_index=index,
        candidate_uid=uid,
        target_cell=cell,
        delay_offset_bin=delay,
    )


def _state(rows: int) -> RecurrentPolicyStateV4:
    return RecurrentPolicyStateV4(hidden=torch.zeros(rows, 4), cell=torch.zeros(rows, 4))


class _FakeObservation:
    """Stand-in for a stacked record: the store only needs its batch dimension."""

    def __init__(self, rows: int) -> None:
        self.rows = rows

    @property
    def batch_size(self):
        return self.rows

    def to_storage(self, device, *, float_dtype=None):
        return self

    def index_select(self, indices):
        return _FakeObservation(int(indices.numel()))


def test_store_finalizes_a_checked_ppo_record() -> None:
    lanes, seats = 2, 2
    store = CrforgeRolloutStore(
        lanes=lanes,
        seats=seats,
        initial_state=_state(lanes * seats),
        policy_state_id="test-checkpoint",
        gate_temperature=1.0,
        action_temperature=1.0,
        continue_temperature=1.0,
    )
    for step in range(3):
        store.append(
            observation=_FakeObservation(lanes * seats),
            actions=_sequence(lanes * seats),
            log_prob=torch.zeros(lanes * seats),
            value=torch.full((lanes * seats,), 0.5),
            rewards=np.array([[0.0, 0.0], [0.25, -0.25]], dtype=np.float32),
            episode_start=np.full(lanes, step == 0, dtype=bool),
            terminated=np.array([step == 2, False]),
            truncated=np.zeros(lanes, dtype=bool),
        )
    rollout = store.finalize(boundary_value=torch.tensor([1.0, -1.0, 2.0, -2.0]))
    assert rollout.time_steps == 3
    assert rollout.batch_size == lanes * seats
    assert bool(rollout.episode_start[0].all())
    assert bool(rollout.episode_start[1:].sum() == 0)
    # Lane 0 terminated on the last step: no boundary bootstrap for it; lane 1
    # was still running and takes the boundary value.
    assert float(rollout.next_value[-1, 0]) == pytest.approx(0.0)
    assert float(rollout.next_value[-1, 2]) == pytest.approx(2.0)
    assert bool(rollout.truncated[-1, 0]) is False
    assert bool(rollout.truncated[-1, 2]) is True

    statistics = rollout_statistics(rollout, gamma=0.9997, gae_lambda=0.98)
    assert statistics["finite"] is True
    assert statistics["episode_starts"] == lanes * seats
    assert statistics["terminated"] == seats
    assert statistics["truncated"] == seats
    assert statistics["reward_zero_sum_max_deviation"] == pytest.approx(0.0)
    assert store.summary().act_rows == 2 * 3  # one ACT row per lane, three decisions
    assert store.storage_bytes() > 0  # the action tensors are retained


def test_store_escapes_inference_mode_for_the_ppo_update() -> None:
    """The producer samples under ``inference_mode``; PPO must still backprop.

    ``detach``/``reshape`` alone preserve the inference-tensor kind, and
    autograd then refuses to save the stored log-probability or value.
    """

    lanes, seats = 1, 2
    store = CrforgeRolloutStore(
        lanes=lanes,
        seats=seats,
        initial_state=_state(lanes * seats),
        policy_state_id="test-inference",
        gate_temperature=1.0,
        action_temperature=1.0,
        continue_temperature=1.0,
    )
    with torch.inference_mode():
        for step in range(2):
            store.append(
                observation=_FakeObservation(lanes * seats),
                actions=_sequence(lanes * seats),
                log_prob=torch.full((lanes * seats,), -0.5),
                value=torch.full((lanes * seats,), 0.25),
                rewards=np.zeros((lanes, seats), dtype=np.float32),
                episode_start=np.full(lanes, step == 0, dtype=bool),
                terminated=np.zeros(lanes, dtype=bool),
                truncated=np.zeros(lanes, dtype=bool),
            )
        boundary = torch.zeros(lanes * seats)
    rollout = store.finalize(boundary_value=boundary)
    assert not torch.is_inference(rollout.behavior_log_prob)
    assert not torch.is_inference(rollout.behavior_value)
    probe = torch.zeros((), requires_grad=True)
    (rollout.behavior_log_prob.sum() * probe + rollout.next_value.sum() * probe).backward()
    assert probe.grad is not None


def test_store_rejects_rows_that_disagree() -> None:
    store = CrforgeRolloutStore(
        lanes=2,
        seats=2,
        initial_state=_state(4),
        policy_state_id="test",
        gate_temperature=1.0,
        action_temperature=1.0,
        continue_temperature=1.0,
    )
    with pytest.raises(ValueError):
        store.append(
            observation=_FakeObservation(4),
            actions=_sequence(3),
            log_prob=torch.zeros(4),
            value=torch.zeros(4),
            rewards=np.zeros((2, 2), dtype=np.float32),
            episode_start=np.zeros(2, dtype=bool),
            terminated=np.zeros(2, dtype=bool),
            truncated=np.zeros(2, dtype=bool),
        )
    with pytest.raises(ValueError):
        store.append(
            observation=_FakeObservation(4),
            actions=_sequence(2),
            log_prob=torch.zeros(4),
            value=torch.zeros(4),
            rewards=np.zeros((2, 2), dtype=np.float32),
            episode_start=np.zeros(2, dtype=bool),
            terminated=np.ones(2, dtype=bool),
            truncated=np.ones(2, dtype=bool),
        )


def test_live_tensorized_record_is_stored_as_training_data(crforge_runtime: None) -> None:
    """A record tensorized under ``inference_mode`` must become owned training data.

    Session 26's defect was a previous-action record that inherited
    ``inference_mode``; stored rollout data has to survive a later PPO write.
    """

    from crforge_env.vector import LadderConfig, SideSetup, VectorBattle

    from crforge_bridge.catalog import Catalog
    from crforge_bridge.jvm import DEFAULT_TABLES
    from crforge_bridge.vector_observation import VectorObservationEncoder
    from crforge_bridge.vector_tensorize import VectorTensorizer
    from native_runner.competitive_release import load_release
    from native_runner.timeline import load_game_mode_timeline
    from native_runner.training.v4.config import ModelConfigV4
    from native_runner.training.v4.factory import production_semantic_bundle

    bundle = production_semantic_bundle("16.635.1")
    identity = load_release("16.635.1").identity
    config = ModelConfigV4(
        ability_vocab_size=identity.ability_vocab_size, child_archetype_count=identity.child_archetype_count
    )
    setup = SideSetup(towers="King_PrincessTowers", king_level=16, tower_level=16)
    vector = VectorBattle(
        2,
        1,
        install=str(self_play.DEFAULT_INSTALL),
        tables=str(DEFAULT_TABLES),
        config=LadderConfig(decision_ticks=5, level=16, tower_level=16),
    )
    vector.reset(
        [1, 2],
        list(self_play.LEARNER_NAMES),
        list(self_play.LEARNER_NAMES),
        list(self_play.LEARNER_FORMS),
        list(self_play.LEARNER_FORMS),
        setup0=setup,
        setup1=setup,
    )
    try:
        catalog = Catalog.load()
        encoder = VectorObservationEncoder(vector, catalog)
        encoder.reset()
        batch = encoder.encode()
        rows = {
            seat: VectorTensorizer(
                self_play._stock_tensorizer(bundle, config, seat),
                vocabulary=list(vector.vocabulary),
                bridge_catalog=catalog,
                timeline=load_game_mode_timeline(self_play.STANDARD_GAME_MODE).timeline,
                tower_troop_id=self_play.TOWER_TROOP,
            )
            for seat in (0, 1)
        }
        for item in rows.values():
            item.reset(batch)
        with torch.inference_mode():
            stacks = {seat: rows[seat].tensorize(batch) for seat in (0, 1)}
        stacked = self_play._interleave([stacks[0], stacks[1]])
        sequence = self_play._interleave([_sequence(2), _sequence(2)])
        store = CrforgeRolloutStore(
            lanes=2,
            seats=2,
            initial_state=_state(4),
            policy_state_id="live-checkpoint",
            gate_temperature=1.0,
            action_temperature=1.0,
            continue_temperature=1.0,
        )
        store.append(
            observation=stacked,
            actions=sequence,
            log_prob=torch.zeros(4),
            value=torch.zeros(4),
            rewards=np.zeros((2, 2), dtype=np.float32),
            episode_start=np.ones(2, dtype=bool),
            terminated=np.zeros(2, dtype=bool),
            truncated=np.zeros(2, dtype=bool),
        )
        rollout = store.finalize(boundary_value=torch.zeros(4))
        stored = rollout.observations[0].match_scalars
        assert not torch.is_inference(stored)
        stored.add_(0.0)  # a PPO write must not raise
        assert store.storage_bytes() > 0
        assert run_statistics(rollout)["finite"] is True
    finally:
        vector.close()


def run_statistics(rollout) -> dict[str, object]:
    return rollout_statistics(rollout, gamma=0.9997, gae_lambda=0.98)
