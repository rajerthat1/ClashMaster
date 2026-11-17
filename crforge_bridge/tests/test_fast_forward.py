"""The in-process ``ever_server`` port: staging, state table, autocast, verify.

The CUDA-specific halves (graph capture, pinned staging, fp16) cannot run on
this host's CPU-only torch build, so they are exercised through the same code
path with ``cuda_graphs=False`` and fp32/bf16 autocast.  What the tests pin is
what a GPU box would otherwise only discover by measuring: the staged batch is
the host batch, a second decision sees the first decision's state, a forward
does not advance the carry, and verification compares both the sampled actions
and the log-probs against eager fp32.
"""

from __future__ import annotations

import pytest
import torch

from crforge_bridge.fast_forward import FastForwardV4, resolve_precision
from native_runner.tests.test_training_v4_model import _batch, _model


def _setup(rows: int = 2) -> tuple[torch.nn.Module, object, object, torch.Tensor]:
    torch.manual_seed(7)
    model = _model().eval()
    batch = _batch(rows)
    state = model.initial_state(rows, device="cpu")
    starts = torch.ones(rows, dtype=torch.bool)
    return model, batch, state, starts


def test_resolve_precision_refuses_fp16_without_cuda() -> None:
    with pytest.raises(ValueError, match="needs CUDA"):
        resolve_precision("fp16", torch.device("cpu"))
    assert resolve_precision("auto", torch.device("cpu")) == "fp32"
    assert resolve_precision("bf16", torch.device("cpu")) == "bf16"
    with pytest.raises(ValueError, match="must be one of"):
        resolve_precision("int8", torch.device("cpu"))


def test_staged_batch_keeps_the_valid_prefix() -> None:
    model, batch, _state, starts = _setup()
    fast = FastForwardV4(model, device="cpu", rows=2, precision="fp32", cuda_graphs=False)
    stage = fast._stage(batch)
    stage.fill(batch, starts)
    staged = stage.batch
    assert staged.batch_size == batch.batch_size
    assert torch.equal(staged.match_scalars, batch.match_scalars)
    assert torch.equal(staged.towers.mask, batch.towers.mask)
    # Padded axes hold the valid prefix and stay masked beyond it.
    assert int(staged.relation_edges.mask.shape[1]) == fast.relation_capacity
    assert torch.equal(staged.relation_edges.mask[:, : batch.relation_edges.mask.shape[1]], batch.relation_edges.mask)
    assert not staged.relation_edges.mask[:, batch.relation_edges.mask.shape[1] :].any()


def test_sampled_decision_matches_eager_fp32() -> None:
    model, batch, state, starts = _setup()
    fast = FastForwardV4(model, device="cpu", rows=2, precision="fp32", cuda_graphs=False)
    rng = torch.get_rng_state()
    eager = model.sample_for_ppo_rollout(batch, state, episode_start=starts, validate=False)
    torch.set_rng_state(rng)
    actual = fast.sample(batch, state, episode_start=starts)
    for field in ("gate", "micro_action_count", "candidate_index", "candidate_uid", "target_cell", "delay_offset_bin"):
        assert torch.equal(getattr(actual.actions, field), getattr(eager.actions, field)), field
    assert torch.allclose(actual.log_prob, eager.log_prob)
    assert torch.allclose(actual.value, eager.value)
    assert actual.next_state is fast.state


def test_state_table_carries_the_second_decision() -> None:
    model, batch, state, starts = _setup()
    fast = FastForwardV4(model, device="cpu", rows=2, precision="fp32", cuda_graphs=False)
    rng = torch.get_rng_state()

    first_eager = model.sample_for_ppo_rollout(batch, state, episode_start=starts, validate=False)
    second_eager = model.sample_for_ppo_rollout(
        batch, first_eager.next_state, episode_start=torch.zeros(2, dtype=torch.bool), validate=False
    )
    torch.set_rng_state(rng)

    first = fast.sample(batch, fast.state, episode_start=starts)
    second = fast.sample(batch, fast.state, episode_start=torch.zeros(2, dtype=torch.bool))
    assert torch.equal(first.actions.gate, first_eager.actions.gate)
    assert torch.equal(second.actions.gate, second_eager.actions.gate)
    assert torch.allclose(second.log_prob, second_eager.log_prob)
    assert torch.equal(fast.state.hidden, second.next_state.hidden)


def test_forward_does_not_advance_the_carry() -> None:
    model, batch, _state, starts = _setup()
    fast = FastForwardV4(model, device="cpu", rows=2, precision="fp32", cuda_graphs=False)
    before = fast.state.hidden.clone()
    context = fast.forward(batch, fast.state, episode_start=starts)
    assert context.value.shape == (2,)
    assert torch.equal(fast.state.hidden, before)


def test_rebind_replaces_the_table() -> None:
    model, batch, _state, starts = _setup()
    fast = FastForwardV4(model, device="cpu", rows=2, precision="fp32", cuda_graphs=False)
    carried = fast.sample(batch, fast.state, episode_start=starts).next_state.hidden.clone()
    fast.rebind(model.initial_state(2, device="cpu"))
    assert not torch.equal(fast.state.hidden, carried)
    assert not fast.state.hidden.any()


def test_verify_passes_for_fp32_and_restores_state_and_rng() -> None:
    model, batch, _state, starts = _setup()
    fast = FastForwardV4(model, device="cpu", rows=2, precision="fp32", cuda_graphs=False)
    torch.manual_seed(11)
    before = fast.state.hidden.clone()
    rng_before = torch.get_rng_state()
    report = fast.verify(batch, fast.state, episode_start=starts)
    assert report["actions_equal"] is True
    assert report["passed"] is True
    assert report["max_abs_delta"]["log_prob"] == 0.0
    assert torch.equal(fast.state.hidden, before)
    # Verification rewound the generator: the next draw is the same as it would
    # have been without the check.
    torch.set_rng_state(rng_before)
    expected = torch.rand(3)
    torch.set_rng_state(rng_before)
    fast.verify(batch, fast.state, episode_start=starts)
    assert torch.equal(torch.rand(3), expected)


def test_verify_reports_bf16_against_fp32() -> None:
    model, batch, _state, starts = _setup()
    greedy = FastForwardV4(model, device="cpu", rows=2, precision="bf16", cuda_graphs=False)
    report = greedy.verify(batch, greedy.state, episode_start=starts)
    assert report["precision"] == "bf16"
    assert report["passed"] == (
        report["actions_equal"] and max(report["max_abs_delta"].values()) <= report["tolerance"]
    )
    if not report["actions_equal"]:
        assert any(report["action_mismatches"].values())
    else:
        assert report["max_abs_delta"]["log_prob"] >= 0.0


def test_rows_mismatch_is_refused() -> None:
    model, _batch2, _state, _starts = _setup()
    fast = FastForwardV4(model, device="cpu", rows=2, precision="fp32", cuda_graphs=False)
    smaller = _batch(1)
    with pytest.raises(ValueError, match="built for 2 rows"):
        fast.sample(smaller, model.initial_state(1, device="cpu"), episode_start=torch.ones(1, dtype=torch.bool))


def test_describe_names_the_disabled_graph_path() -> None:
    model = _model().eval()
    fast = FastForwardV4(model, device="cpu", rows=2, precision="auto")
    described = fast.describe()
    assert described["precision"] == "fp32"
    assert described["cuda_graphs"] is False
    assert described["full_cuda_graph"] is False
    assert described["graph_covers"] == "nothing"
    assert described["captured_shapes"] == []
    assert described["captured_full_shapes"] == []
    assert described["sampled_graph_rng_gate"] is None


def test_full_cuda_graph_is_refused_without_cuda() -> None:
    """A sampled graph needs capture: this host's CPU-only torch must say so."""

    model = _model().eval()
    with pytest.raises(ValueError, match="needs CUDA"):
        FastForwardV4(model, device="cpu", rows=2, precision="fp32", full_cuda_graph=True)


def test_full_cuda_graph_refuses_the_eager_path() -> None:
    """The full-call graph is the captured path; --no-cuda-graphs would be a lie."""

    model = _model().eval()
    with pytest.raises(ValueError, match="do not combine"):
        FastForwardV4(model, device="cpu", rows=2, precision="fp32", cuda_graphs=False, full_cuda_graph=True)


def test_the_sampled_graph_rng_gate_catches_a_frozen_stream() -> None:
    """Two replays that return the same log-probabilities are a frozen RNG."""

    from types import SimpleNamespace

    model, batch, _state, _starts = _setup()
    fast = FastForwardV4(model, device="cpu", rows=2, precision="fp32", cuda_graphs=False)

    class FrozenSlot:
        def run(self, host_batch: object, state: object, episode_start: object) -> tuple[None, object]:
            return None, SimpleNamespace(log_prob=torch.zeros(2))

    with pytest.raises(RuntimeError, match="same random draws"):
        fast._assert_graph_rng_advances(FrozenSlot(), batch)
    assert fast.rng_gate is not None
    assert fast.rng_gate["passed"] is False
    assert fast.rng_gate["max_log_prob_delta_between_replays"] == 0.0

    class AdvancingSlot:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, host_batch: object, state: object, episode_start: object) -> tuple[None, object]:
            self.calls += 1
            return None, SimpleNamespace(log_prob=torch.full((2,), float(self.calls)))

    fast._assert_graph_rng_advances(AdvancingSlot(), batch)
    assert fast.rng_gate is not None
    assert fast.rng_gate["passed"] is True
    assert fast.rng_gate["max_log_prob_delta_between_replays"] == 1.0
    # The probe must not leave the table advanced.
    assert not fast.state.hidden.any()


def test_the_rng_gate_accepts_a_peaked_policy_when_the_probe_advances() -> None:
    """Two replays can agree on the action while the RNG stream is healthy."""

    from types import SimpleNamespace

    model, batch, _state, _starts = _setup()
    fast = FastForwardV4(model, device="cpu", rows=2, precision="fp32", cuda_graphs=False)

    class PeakedSlot:
        def __init__(self) -> None:
            self.rng_probe = torch.zeros(1)

        def run(self, host_batch: object, state: object, episode_start: object) -> tuple[None, object]:
            self.rng_probe.add_(1.0)
            return None, SimpleNamespace(log_prob=torch.zeros(2))

    fast._assert_graph_rng_advances(PeakedSlot(), batch)
    assert fast.rng_gate is not None
    assert fast.rng_gate["passed"] is True
    assert fast.rng_gate["random_probe_delta_between_replays"] == 1.0
    # The sampled log-probs were identical; only the probe separated the cases.
    assert fast.rng_gate["max_log_prob_delta_between_replays"] == 0.0
