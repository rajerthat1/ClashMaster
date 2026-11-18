"""The multiproc harness: the commands it builds and the rows it aggregates.

The comparison is only a measurement if the two arms differ in one thing — who
owns the forward — so these tests pin the producer command's forwarding flags,
the server command's binding, and the artifact rows the aggregate is summed
from (each producer's own ``collection_lane_decisions_per_s``, never a
cross-process clock).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

_TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "crforge"
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import multiproc


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "lanes": 8,
        "segments": 3,
        "decisions_per_segment": 32,
        "device": "cpu",
        "server_device": "cpu",
        "seed": 1,
        "checkpoint": Path("checkpoint.pt"),
        "rollout": False,
        "no_validate": False,
        "no_warmup_forward": False,
        "verify_decision_server": False,
        "max_requests": 8,
        "wait_ms": 2.0,
        "inference_precision": None,
        "fast_forward": False,
        "no_cuda_graphs": False,
        "full_cuda_graph": False,
        "omp_threads": 1,
        "jvm_arg": [],
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_the_producer_command_differs_only_in_the_forward() -> None:
    local = multiproc.producer_command(_args(), output=Path("out.json"), address=None)
    remote = multiproc.producer_command(
        _args(verify_decision_server=True), output=Path("out.json"), address="unix:/tmp/s.sock"
    )
    assert "--decision-server" not in local
    assert "--verify-decision-server" not in local
    assert remote[remote.index("--decision-server") + 1] == "unix:/tmp/s.sock"
    assert "--verify-decision-server" in remote
    # The probe shape is the default; storing the rollout is opt-in.
    assert "--no-rollout" in local and "--no-rollout" in remote
    assert "--no-rollout" not in multiproc.producer_command(_args(rollout=True), output=Path("out.json"), address=None)
    # Everything else is identical, including the shape.
    stripped_local = [item for item in local if item != "--no-rollout"]
    stripped_remote = [
        item
        for item in remote
        if item not in {"--no-rollout", "--decision-server", "unix:/tmp/s.sock", "--verify-decision-server"}
    ]
    assert stripped_local == stripped_remote


def test_the_producer_command_forwards_validation_flags() -> None:
    command = multiproc.producer_command(
        _args(no_validate=True, no_warmup_forward=True), output=Path("o"), address=None
    )
    assert "--no-validate" in command
    assert "--no-warmup-forward" in command


def test_the_producer_command_forwards_jvm_options() -> None:
    """A host whose JVM miscompiles the engine needs the flag on every producer."""

    command = multiproc.producer_command(
        _args(jvm_arg=["-XX:TieredStopAtLevel=1", "-Xmx4g"]), output=Path("o"), address=None
    )
    assert "--jvm-arg=-XX:TieredStopAtLevel=1" in command
    assert "--jvm-arg=-Xmx4g" in command
    assert not any(item == "--jvm-arg" for item in command)
    assert "--jvm-arg" not in multiproc.producer_command(_args(), output=Path("o"), address=None)


def test_the_in_process_arm_carries_the_same_forward_settings() -> None:
    """The floor must differ from the server arm in batching, not in precision."""

    command = multiproc.producer_command(
        _args(fast_forward=True, inference_precision="fp16", no_cuda_graphs=True),
        output=Path("o"),
        address=None,
    )
    assert "--fast-forward" in command
    assert command[command.index("--inference-precision") + 1] == "fp16"
    assert "--no-cuda-graphs" in command
    assert "--fast-forward" not in multiproc.producer_command(_args(), output=Path("o"), address=None)


def test_the_server_command_binds_the_address_and_the_forward_settings() -> None:
    command = multiproc.server_command(
        _args(inference_precision="bf16", no_cuda_graphs=True),
        address="unix:/tmp/s.sock",
        ready=Path("ready.json"),
        output=Path("server.json"),
    )
    assert command[command.index("--address") + 1] == "unix:/tmp/s.sock"
    assert command[command.index("--ready-file") + 1] == "ready.json"
    assert command[command.index("--wait-ms") + 1] == "2.0"
    assert command[command.index("--max-requests") + 1] == "8"
    assert command[command.index("--inference-precision") + 1] == "bf16"
    assert "--no-cuda-graphs" in command
    # The server's device is the forward's; the producers stay on the cpu half.
    assert command[command.index("--device") + 1] == "cpu"
    gpu = multiproc.server_command(
        _args(server_device="cuda"), address="unix:/tmp/s.sock", ready=Path("r"), output=Path("s")
    )
    assert gpu[gpu.index("--device") + 1] == "cuda"


def test_every_process_shares_the_thread_pinning() -> None:
    environment = multiproc._env(_args(omp_threads=2))
    assert environment["OMP_NUM_THREADS"] == "2"
    assert environment["MKL_NUM_THREADS"] == "2"


def _producer_artifact(*, rate: float = 57.0, requests: int = 64) -> dict:
    return {
        "shape": {"count": 4, "seats": [0, 1], "device": "cpu"},
        "gate": {"pass": True},
        "inference": {
            "decision_server": {
                "address": "unix:/tmp/s.sock",
                "sampling": "server-global",
                "server": {"rounds": requests, "requests": requests},
                "request": {
                    "requests": requests,
                    "pack_ms_mean": 1.0,
                    "round_trip_ms_mean": 108.0,
                    "unpack_ms_mean": 0.4,
                    "queue_wait_ms_mean": 2.1,
                },
                "verification": {"passed": True},
            }
        },
        "segments": [
            {
                "segment": 0,
                "collection_wall_s": 2.0,
                "lane_decisions": 8 * int(rate),
                "collection_lane_decisions_per_s": rate,
                "ms": {"inference_ms": {"mean": 100.0}},
                "ms_mean": {"tensorize_ms": 28.0, "inference_ms": 108.0, "decode_ms": 2.0},
                "cpu_half": {"whole_ms_mean": {"tensorize": 28.0, "decode": 2.0}},
            }
        ],
        "totals": {"wall_s": 2.0, "lane_decisions": 8 * int(rate), "collection_lane_decisions_per_s": rate},
    }


def test_a_segment_row_reads_the_producer_rate_and_the_wire() -> None:
    row = multiproc._segment(_producer_artifact(rate=57.0), name="p0.json", returncode=0)
    assert row["producer_gate_pass"] is True
    assert row["lane_decisions_per_s"] == pytest.approx(57.0)
    assert row["matches_per_s_1214"] == pytest.approx(57.0 / 2.0 / 1214.0)
    assert row["decision_server"]["request_round_trip_ms_mean"] == pytest.approx(108.0)
    assert row["forward_ms"] == pytest.approx(108.0)  # ms_mean.inference_ms
    # The CPU half and the decision wall are bucket sums, not bucket means.
    assert row["cpu_ms"] == pytest.approx(30.0)
    assert row["decision_ms_mean"] == pytest.approx(138.0)


def test_a_local_producer_has_no_server_block() -> None:
    artifact = _producer_artifact()
    artifact["inference"] = {"fast_forward": {"enabled": False}}
    row = multiproc._segment(artifact, name="p0.json", returncode=0)
    assert row["decision_server"] is None


def test_the_server_arm_is_a_delta_not_the_whole_life() -> None:
    before = {"rounds": 10, "requests": 10, "rows": 80, "forward_ms_mean": 100.0, "failed_rounds": 0}
    after = {
        "rounds": 40,
        "requests": 90,
        "rows": 760,
        "forward_ms_mean": 110.0,
        "failed_rounds": 1,
        "max_requests_per_round": 2,
        "row_buckets": {"16": 30},
        "shape_buckets": {"4/4/16/0": 30},
    }
    arm = multiproc.server_delta(before, after)
    assert arm["rounds"] == 30
    assert arm["requests"] == 80
    assert arm["requests_per_round"] == pytest.approx(80.0 / 30.0)
    assert arm["rows_per_round"] == pytest.approx(680.0 / 30.0)
    # (110 × 40) − (100 × 10) = 3,400 ms over 30 rounds.
    assert arm["forward_ms_per_round"] == pytest.approx(3400.0 / 30.0)
    assert arm["forward_ms_per_request"] == pytest.approx(3400.0 / 80.0)
    assert arm["failed_rounds"] == 1
    empty = multiproc.server_delta(before, before)
    assert empty["rounds"] == 0 and empty["requests_per_round"] is None


def test_the_server_summary_keeps_the_round_shape() -> None:
    summary = multiproc.server_summary(
        {
            "wall_s": 52.0,
            "server": {
                "device": "cpu",
        "server_device": "cpu",
                "precision": "fp32",
                "rounds": 102,
                "requests": 204,
                "rows": 1632,
                "requests_per_round": 2.0,
                "rows_per_round": 16.0,
                "forward_ms_mean": 110.0,
                "forward_ms_per_request": 55.0,
                "assemble_ms_mean": 4.8,
                "row_buckets": {"16": 102},
                "shape_buckets": {"4/4/16/0": 102},
                "captured_row_counts": [16],
                "modes": {"sample": 102},
            },
        }
    )
    assert summary is not None
    assert summary["requests_per_round"] == 2.0
    assert summary["forward_ms_per_request"] == 55.0
    assert summary["row_buckets"] == {"16": 102}
    assert multiproc.server_summary(None) is None
