"""The C4 training loop: one persistent optimizer and an explicit state policy.

These tests use the small fixture model from ``test_training_v4_model``: they are
about the loop's bookkeeping (when the optimizer steps, which behaviour identity a
rollout binds, what the boundary does to the recurrent state, and the two-update
gate), not about PPO's numerics, which ``test_training_v4_learning`` covers.
"""

from __future__ import annotations

import torch

from crforge_bridge.training import CrforgeTrainer, CrforgeTrainingConfig, policy_state_id, weights_digest
from native_runner.tests.test_training_v4_learning import _wait_actions
from native_runner.tests.test_training_v4_model import _batch, _model
from native_runner.training.v4.learning import evaluate_recurrent_sequence
from native_runner.training.v4.ppo import StoredRolloutV4
from native_runner.training.v4.tensors import RecurrentPolicyStateV4


def _rollout(model: torch.nn.Module, *, checkpoint_id: str = "fixture", rows: int = 2) -> StoredRolloutV4:
    """One stored decision sampled by the model's current weights."""

    batch = _batch(batch_size=rows)
    actions = _wait_actions(rows)
    starts = torch.tensor([[True] * rows])
    initial_state = model.initial_state(rows)
    with torch.no_grad():
        behavior = evaluate_recurrent_sequence(model, (batch,), (actions,), starts, initial_state=initial_state)
    return StoredRolloutV4(
        observations=(batch,),
        actions=(actions,),
        episode_start=starts,
        valid_mask=torch.ones(1, rows, dtype=torch.bool),
        behavior_log_prob=behavior.log_prob,
        behavior_value=behavior.value,
        next_value=torch.zeros(1, rows),
        rewards=torch.ones(1, rows),
        terminated=torch.ones(1, rows, dtype=torch.bool),
        truncated=torch.zeros(1, rows, dtype=torch.bool),
        initial_state=initial_state,
        gate_temperature=1.0,
        action_temperature=1.0,
        continue_temperature=1.0,
        policy_state_id=policy_state_id(model, checkpoint_id),
    )


def _trainer(model: torch.nn.Module, **overrides: object) -> CrforgeTrainer:
    config = CrforgeTrainingConfig(sequence_chunk_steps=1, entropy_coefficient=0.0, **overrides)
    return CrforgeTrainer(model, config=config, checkpoint_id="fixture")


def test_training_config_rejects_an_implicit_wave_or_unknown_policy() -> None:
    assert CrforgeTrainingConfig(state_policy="carry").resets_recurrence is False
    assert CrforgeTrainingConfig().resets_recurrence is True
    assert CrforgeTrainingConfig(state_policy="wave", segments_per_wave=3).collects_waves is True
    assert CrforgeTrainingConfig(state_policy="wave", segments_per_wave=3).as_dict()["segments_per_wave"] == 3

    for kwargs in (
        {"state_policy": "sometimes"},
        {"state_policy": "wave", "segments_per_wave": 1},
        {"state_policy": "carry", "segments_per_wave": 2},
        {"state_policy": "reset", "segments_per_wave": 0},
    ):
        try:
            CrforgeTrainingConfig(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"training config accepted {kwargs}")


def test_two_consecutive_updates_keep_one_optimizer_and_advance_the_policy() -> None:
    torch.manual_seed(7)
    model = _model().eval()
    trainer = _trainer(model)
    optimizer = trainer.optimizer
    first = _rollout(model)

    assert trainer.enqueue(first, segment=0, expected_policy_state_id=first.policy_state_id) is not None
    assert trainer.updates == 1
    assert trainer.optimizer is optimizer
    assert trainer.optimizer.state, "the persistent optimizer must hold Adam moments"
    digest_after_first = weights_digest(model)

    # A real loop samples the next segment with the post-update weights.
    second = _rollout(model)
    updates = trainer.enqueue(second, segment=1, expected_policy_state_id=second.policy_state_id)

    assert updates is not None and len(updates) == 1
    assert trainer.updates == 2
    assert trainer.optimizer is optimizer
    assert weights_digest(model) != digest_after_first

    gate = trainer.gate()
    assert gate["two_consecutive_updates"] is True
    assert gate["trace_finite"] is True
    assert gate["reward_trace_finite"] is True
    assert gate["return_trace_finite"] is True
    assert gate["pass"] is True
    assert [row["update_index"] for row in trainer.trace] == [1, 2]
    assert trainer.trace[0]["policy_state_id_after"] == trainer.trace[1]["policy_state_id_before"]
    assert trainer.distinct_behaviour_ids == 2


def test_one_update_is_not_a_training_loop() -> None:
    model = _model().eval()
    trainer = _trainer(model)
    rollout = _rollout(model)

    trainer.enqueue(rollout, segment=0, expected_policy_state_id=rollout.policy_state_id)

    gate = trainer.gate()
    assert gate["updates"] == 1
    assert gate["two_consecutive_updates"] is False
    assert gate["pass"] is False


def test_wave_freezes_the_weights_until_both_segments_are_collected() -> None:
    torch.manual_seed(11)
    model = _model().eval()
    trainer = _trainer(model, state_policy="wave", segments_per_wave=2)
    frozen_digest = weights_digest(model)

    first = _rollout(model)
    assert trainer.enqueue(first, segment=0, expected_policy_state_id=first.policy_state_id) is None
    assert trainer.updates == 0
    assert weights_digest(model) == frozen_digest
    assert trainer.pending

    # The second segment is sampled by the same frozen weights, so it binds the
    # same behaviour identity the first one did.
    second = _rollout(model)
    assert second.policy_state_id == first.policy_state_id
    updates = trainer.enqueue(second, segment=1, expected_policy_state_id=second.policy_state_id)

    assert updates is not None and [entry["segment"] for entry in updates] == [0, 1]
    assert [entry["update_index"] for entry in updates] == [1, 2]
    assert trainer.updates == 2
    assert not trainer.pending
    assert weights_digest(model) != frozen_digest
    assert trainer.gate()["pass"] is True


def test_a_partial_wave_still_trains() -> None:
    model = _model().eval()
    trainer = _trainer(model, state_policy="wave", segments_per_wave=3)
    for segment in range(2):
        rollout = _rollout(model)
        assert trainer.enqueue(rollout, segment=segment, expected_policy_state_id=rollout.policy_state_id) is None

    flushed = trainer.flush()

    assert [entry["segment"] for entry in flushed] == [0, 1]
    assert trainer.updates == 2
    assert trainer.gate()["pass"] is True


def test_state_policy_carry_keeps_the_recurrence_and_reset_zeroes_it() -> None:
    model = _model().eval()
    rows = 2
    hidden = torch.ones(rows, model.config.lstm_hidden_dim)
    cell = torch.full((rows, model.config.lstm_hidden_dim), 0.5)
    state = RecurrentPolicyStateV4(hidden=hidden.clone(), cell=cell.clone())

    carried = _trainer(model, state_policy="carry").boundary_state(state, rows=rows)
    assert torch.equal(carried.hidden, hidden) and torch.equal(carried.cell, cell)

    for policy in ("reset", "wave"):
        trainer = _trainer(model, state_policy=policy, segments_per_wave=2 if policy == "wave" else 1)
        reset = trainer.boundary_state(state, rows=rows)
        assert torch.equal(reset.hidden, torch.zeros_like(reset.hidden))
        assert torch.equal(reset.cell, torch.zeros_like(reset.cell))


def test_weights_digest_is_stable_and_tracks_the_weights() -> None:
    model = _model().eval()
    digest = weights_digest(model)
    assert digest == weights_digest(model)
    assert policy_state_id(model, "a") != policy_state_id(model, "b")

    with torch.no_grad():
        next(model.parameters()).add_(0.01)
    assert weights_digest(model) != digest
