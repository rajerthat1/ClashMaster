"""The cloud capacity equation must reproduce the recorded A6000 rate.

``matches/s ≈ 0.211 × min(N_proc, G × E / g) / T`` is only useful to a provider
decision if its terms come from the producer's own artifact: the decision wall,
the forward's share of it, and the host's process/GPU counts.  These tests pin
the arithmetic against the 2026-11-19 A6000 128-lane run (0.33 matches/s at one
process, ~4 processes per card) and the boundary cases a provider report must
not confuse.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

_TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "crforge"
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import cloud_capacity


def _artifact(*, lanes: int = 128, seats: list[int] | None = None, segments: list[dict] | None = None) -> dict:
    return {
        "shape": {"count": lanes, "seats": seats or [0, 1], "device": "cuda"},
        "substrate": {"checkpoint_id": "RoyaleFish-B-general-v275-16635"},
        "segments": segments
        or [
            {
                "segment": 0,
                "decisions": 32,
                "collection_wall_s": 20.698336126999948,
                "lane_decisions": lanes * len(seats or [0, 1]) * 32,
                "matches_per_s": 0.16300683906483576,
                "ms": {
                    "tensorize_ms": 80.99998499994854,
                    "inference_ms": 157.43159149997155,
                    "decode_ms": 97.1106700000064,
                    "step_ms": 75.15699850000601,
                    "facts_ms": 9.007278000012775,
                },
            }
        ],
    }


def _host(*, processes: float = 6.0, gpus: float = 1.0) -> dict:
    return {"processes": processes, "gpus": gpus, "hostname": "test"}


def _row(**kwargs) -> dict:
    return cloud_capacity.segment_row(_artifact(**kwargs), name="a6000.json")


def test_the_baseline_constant_is_256_lane_decisions_over_1214() -> None:
    assert cloud_capacity.matches_per_decision(128, 2) == pytest.approx(256 / 1214)
    assert cloud_capacity.matches_per_decision(128, 2) == pytest.approx(0.2109, abs=5e-5)


def test_the_recorded_a6000_run_is_0_326_matches_per_s_at_one_process() -> None:
    row = _row()

    assert row["decision_s"] == pytest.approx(0.646823, abs=1e-6)
    assert row["forward_ms"] == pytest.approx(157.43159)
    assert row["forward_fraction"] == pytest.approx(0.243392, abs=1e-5)
    assert row["matches_per_s_1214_per_process"] == pytest.approx(0.3260, abs=1e-3)
    # The producer's own field uses the 2,428 convention; it must be exactly half.
    assert row["matches_per_s_2428_per_process"] == pytest.approx(0.1630, abs=5e-4)
    assert row["artifact_matches_per_s"] == pytest.approx(0.1630, abs=5e-4)


def test_six_vcpus_and_one_a6000_are_gpu_bound_at_1_34_matches_per_s() -> None:
    report = cloud_capacity.capacity_report(_host(processes=6, gpus=1), _row(), price_per_hour=0.35)

    assert report["binding"] == "gpu"
    assert report["gpu_process_capacity"] == pytest.approx(4.1088, abs=1e-3)
    assert report["cpu_ceiling_matches_s_1214"] == pytest.approx(1.956, abs=1e-3)
    assert report["gpu_ceiling_matches_s_1214"] == pytest.approx(1.3396, abs=1e-3)
    assert report["predicted_matches_s_1214"] == pytest.approx(1.3396, abs=1e-3)
    assert report["capacity_processes"] == pytest.approx(4.1088, abs=1e-3)
    assert report["equation"]["N_proc"] == 6.0
    assert report["equation"]["g"] == pytest.approx(0.243392, abs=1e-5)
    assert report["cost"]["usd_per_million_matches_1214"] == pytest.approx(72.6, abs=0.1)
    # The physical (one-seat) convention costs twice as much per real match.
    assert report["cost"]["usd_per_million_matches_2428"] == pytest.approx(145.2, abs=0.2)


def test_the_last_segment_is_the_steady_state_and_a_cold_mean_is_not_demand() -> None:
    cold = {
        "segment": 0,
        "decisions": 8,
        "collection_wall_s": 6.0,
        "lane_decisions": 2048,
        "ms": {"inference_ms": 88.5},
        "ms_mean": {"inference_ms": 5000.0},
    }
    warm = {
        "segment": 1,
        "decisions": 32,
        "collection_wall_s": 20.0,
        "lane_decisions": 8192,
        "ms": {"inference_ms": 157.0},
        "ms_mean": {"inference_ms": 157.0},
    }

    row = cloud_capacity.segment_row(_artifact(segments=[cold, warm]), name="two.json")

    assert row["segment"] == 1
    assert row["decision_s"] == pytest.approx(20.0 / 32)
    assert row["forward_ms"] == pytest.approx(157.0)
    assert row["forward_source"] == "ms_median"


def test_a_cpu_only_host_is_cpu_bound_by_construction() -> None:
    report = cloud_capacity.capacity_report(_host(processes=8, gpus=0), _row())

    assert report["binding"] == "cpu"
    assert report["gpu_process_capacity"] is None
    assert report["gpu_ceiling_matches_s_1214"] is None
    assert report["capacity_processes"] == 8.0
    assert report["predicted_matches_s_1214"] == pytest.approx(0.3260 * 8, abs=1e-2)
    assert "No GPU declared" in report["why"]


def test_a_partitioned_card_halves_the_process_capacity() -> None:
    full = cloud_capacity.capacity_report(_host(processes=6, gpus=1), _row())
    half = cloud_capacity.capacity_report(_host(processes=6, gpus=0.5), _row())

    assert half["gpu_process_capacity"] == pytest.approx(full["gpu_process_capacity"] / 2)
    assert half["predicted_matches_s_1214"] == pytest.approx(full["predicted_matches_s_1214"] / 2)
    assert half["binding"] == "gpu"


def test_the_balance_band_names_neither_side_near_the_crossover() -> None:
    row = _row()
    slots = 1.0 / row["forward_fraction"]
    report = cloud_capacity.capacity_report(_host(processes=slots * 0.95, gpus=1), row)

    assert report["binding"] == "balanced"
    assert report["predicted_matches_s_1214"] == pytest.approx(
        row["matches_per_s_1214_per_process"] * slots * 0.95, abs=1e-3
    )


def test_a_larger_forward_share_fills_the_card_sooner() -> None:
    cheap = cloud_capacity.capacity_report(_host(processes=6, gpus=1), _row())
    expensive = cloud_capacity.capacity_report(_host(processes=6, gpus=1), dict(_row(), forward_fraction=0.5))

    assert expensive["gpu_process_capacity"] == pytest.approx(2.0)
    assert expensive["binding"] == "gpu"
    assert expensive["predicted_matches_s_1214"] < cheap["predicted_matches_s_1214"]


def test_a_missing_forward_bucket_falls_back_to_the_cpu_ceiling() -> None:
    segment = {"segment": 0, "decisions": 32, "collection_wall_s": 20.0, "lane_decisions": 8192, "ms": {}}

    row = cloud_capacity.segment_row(_artifact(segments=[segment]), name="no-forward.json")
    report = cloud_capacity.capacity_report(_host(processes=6, gpus=1), row)

    assert row["forward_ms"] is None
    assert row["forward_source"] == "missing"
    assert report["binding"] == "cpu"
    assert report["gpu_process_capacity"] is None


def test_the_two_rate_conventions_differ_by_exactly_two() -> None:
    report = cloud_capacity.capacity_report(_host(processes=6, gpus=1), _row())

    assert report["predicted_matches_s_2428"] == pytest.approx(report["predicted_matches_s_1214"] / 2)
    assert report["cost"] is None


def _sweep_row(processes: int, retention: float) -> dict:
    return {
        "processes": processes,
        "measured": True,
        "aggregate_matches_s_1214": 0.326 * processes * retention,
        "retention_vs_ideal_scaling": retention,
        "per_process_slowdown_vs_baseline": 1.0 / retention,
        "forward_slowdown_vs_baseline": 1.0 / retention,
        "cpu_slowdown_vs_baseline": 1.0,
    }


def test_the_sweep_confirms_saturation_where_the_gpu_term_predicts_it() -> None:
    sweep = [_sweep_row(1, 0.99), _sweep_row(2, 0.96), _sweep_row(4, 0.72), _sweep_row(6, 0.51)]

    verdict = cloud_capacity.sweep_verdict(sweep, gpu_slots=4.11)

    assert verdict["saturated_at_processes"] == 4
    assert verdict["confirms_equation"] is True
    assert verdict["by_level"][6]["forward_slowdown"] == pytest.approx(1.0 / 0.51)


def test_a_soft_crossover_confirms_the_gpu_rate_not_the_process_count() -> None:
    sweep = [_sweep_row(1, 0.96), _sweep_row(4, 0.52), _sweep_row(6, 0.41)]
    sweep[1]["cpu_slowdown_vs_baseline"] = 1.92
    sweep[-1]["cpu_slowdown_vs_baseline"] = 2.44
    sweep[-1]["aggregate_matches_s_1214"] = 1.215

    verdict = cloud_capacity.sweep_verdict(sweep, gpu_slots=2.71, gpu_ceiling_matches_s=1.331)

    assert verdict["saturated_at_processes"] == 4
    assert verdict["measured_vs_gpu_ceiling"] == pytest.approx(0.913, abs=1e-3)
    assert verdict["confirms_equation"] is True
    assert verdict["bound_by"] == "shared"


def test_a_sweep_that_keeps_scaling_does_not_confirm_a_gpu_ceiling() -> None:
    sweep = [_sweep_row(1, 1.0), _sweep_row(2, 1.0), _sweep_row(4, 1.0), _sweep_row(8, 0.99)]

    verdict = cloud_capacity.sweep_verdict(sweep, gpu_slots=4.11)

    assert verdict["saturated_at_processes"] is None
    assert verdict["confirms_equation"] is False


def test_a_cold_short_artifact_is_flagged_instead_of_ranked() -> None:
    artifact = _artifact(
        segments=[
            {
                "segment": 0,
                "decisions": 8,
                "collection_wall_s": 6.0,
                "lane_decisions": 2048,
                "ms": {"inference_ms": 88.5},
                "ms_first": {"inference_ms": 5000.0},
            }
        ]
    )
    artifact["shape"]["warmup_forward"] = False

    row = cloud_capacity.segment_row(artifact, name="cold.json")

    assert len(row["warnings"]) == 3
    assert any("one-time first decision" in warning for warning in row["warnings"])
    assert any("--no-warmup-forward" in warning for warning in row["warnings"])
    assert any("first forward" in warning for warning in row["warnings"])


def test_the_steady_default_shape_has_no_warning() -> None:
    assert _row()["warnings"] == []


def test_a_cpu_only_sweep_names_cpu_contention_not_a_gpu_cap() -> None:
    sweep = [_sweep_row(1, 1.0)]
    contended = _sweep_row(2, 0.69)
    contended["forward_slowdown_vs_baseline"] = 1.46
    contended["cpu_slowdown_vs_baseline"] = 1.53
    sweep.append(contended)

    verdict = cloud_capacity.sweep_verdict(sweep, gpu_slots=None)

    assert verdict["saturated_at_processes"] == 2
    assert verdict["bound_by"] == "cpu"
    assert verdict["confirms_equation"] is None


def test_a_gpu_sweep_separates_a_forward_slowdown_from_a_cpu_one() -> None:
    stalled = _sweep_row(4, 0.6)
    stalled["forward_slowdown_vs_baseline"] = 1.8
    stalled["cpu_slowdown_vs_baseline"] = 1.02

    verdict = cloud_capacity.sweep_verdict([_sweep_row(1, 1.0), stalled], gpu_slots=4.11)

    assert verdict["bound_by"] == "gpu"
    assert verdict["confirms_equation"] is True


def test_the_probe_is_throughput_only_and_validates_only_on_request() -> None:
    args = argparse.Namespace(
        device="cuda",
        seed=1,
        checkpoint=Path("checkpoint.pt"),
        validate=False,
        no_warmup_forward=False,
        fast_forward=False,
        inference_precision=None,
        no_cuda_graphs=False,
    )

    command = cloud_capacity.producer_command(args, output=Path("out.json"), lanes=128, segments=3, decisions=32)

    assert "--no-rollout" in command
    assert "--no-validate" in command
    assert "--no-warmup-forward" not in command
    assert command[command.index("--count") + 1] == "128"


def test_the_probe_forwards_the_in_process_forward_flags() -> None:
    args = argparse.Namespace(
        device="cuda",
        seed=1,
        checkpoint=Path("checkpoint.pt"),
        validate=False,
        no_warmup_forward=False,
        fast_forward=True,
        inference_precision="fp32",
        no_cuda_graphs=True,
        full_cuda_graph=False,
    )

    command = cloud_capacity.producer_command(args, output=Path("out.json"), lanes=128, segments=3, decisions=32)

    assert "--fast-forward" in command
    assert command[command.index("--inference-precision") + 1] == "fp32"
    assert "--no-cuda-graphs" in command
    assert "--full-cuda-graph" not in command
    # The full-call graph is the other captured path; it is forwarded too.
    args.no_cuda_graphs = False
    args.full_cuda_graph = True
    full = cloud_capacity.producer_command(args, output=Path("out.json"), lanes=128, segments=3, decisions=32)
    assert "--full-cuda-graph" in full
    assert "--no-cuda-graphs" not in full
    # Without --fast-forward the precision flags are not meaningful and are not sent.
    args.fast_forward = False
    plain = cloud_capacity.producer_command(args, output=Path("out.json"), lanes=128, segments=3, decisions=32)
    assert "--fast-forward" not in plain
    assert "--inference-precision" not in plain
