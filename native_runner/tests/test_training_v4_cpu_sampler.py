from __future__ import annotations

import torch

from native_runner.training.v4.async_cluster_self_play import (
    _EagerRolloutSamplerV4,
    _inference_batch_wait_seconds,
    _persistent_rollout_graph_v4,
)
from native_runner.training.v4.factory import build_production_model_v4


def test_cpu_host_uses_the_eager_rollout_sampler() -> None:
    """A non-CUDA device must not need a CUDA graph to run the collector."""

    model = build_production_model_v4(device="cpu")
    device = torch.device("cpu")
    sampler = _persistent_rollout_graph_v4(model, device, 32)
    assert isinstance(sampler, _EagerRolloutSamplerV4)
    # The cache keeps one sampler per shape, matching the graph path.
    assert _persistent_rollout_graph_v4(model, device, 32) is sampler


def test_eager_sampler_reports_its_device() -> None:
    sampler = _EagerRolloutSamplerV4(torch.device("cpu"))
    assert sampler.device.type == "cpu"
    assert sampler.autocast_dtype is None


def test_inference_batch_wait_override(monkeypatch) -> None:
    monkeypatch.delenv("CR_PPO_INFERENCE_BATCH_WAIT_MS", raising=False)
    assert _inference_batch_wait_seconds() == 0.002
    monkeypatch.setenv("CR_PPO_INFERENCE_BATCH_WAIT_MS", "40")
    assert _inference_batch_wait_seconds() == 0.04
    monkeypatch.setenv("CR_PPO_INFERENCE_BATCH_WAIT_MS", "  ")
    assert _inference_batch_wait_seconds() == 0.002
    for invalid in ("-1", "1001", "nan", "soon"):
        monkeypatch.setenv("CR_PPO_INFERENCE_BATCH_WAIT_MS", invalid)
        try:
            _inference_batch_wait_seconds()
        except ValueError:
            continue
        raise AssertionError(f"{invalid!r} should be rejected")
