"""Run N crforge producers at once, each with its own engine, one forward shared.

The C4 producer is one process = one core + one forward.  The scale-out question
is not whether more processes add throughput — they do until the host's cores or
the card run out — but *where* the aggregate stops growing, and whether the
shared decision server is what buys the next step.  This tool measures that
directly:

    # one producer alone (the baseline), then four through one batched server
    .venv/bin/python tools/crforge/multiproc.py \
      --procs 4 --lanes 8 --segments 3 --decisions-per-segment 32 \
      --server auto --output runs/crforge-c4/multiproc-server.json

    # the same four with each producer owning its forward (the multiproc floor)
    .venv/bin/python tools/crforge/multiproc.py \
      --procs 4 --lanes 8 --segments 3 --decisions-per-segment 32 \
      --server none --output runs/crforge-c4/multiproc-local.json

On a GPU box the producers stay ``--device cpu`` and the server takes the card
with ``--server-device cuda`` (plus the server's ``--inference-precision`` /
``--full-cuda-graph``); ``--server auto`` refuses a non-cpu producer device
because the producer no longer owns a forward.

The **in-process** arm (``--server none``) owns its forward, so it passes
``--fast-forward`` and the same ``--inference-precision`` / graph flags the
server would get.  Both arms then differ in batching, not in precision or
capture; an eager fp32 floor against an fp16 graph server would price the
precision change and call it the architecture.  On the card, also pass
``--no-validate``: per-decision contract validation syncs the device on every
value it inspects and measures Python, not the forward (the native notes).

The aggregate is the sum of the producers' own collection rates, exactly as
``cloud_capacity.py --sweep-procs`` computes it: each producer keeps its own
``collection_wall_s``, so no cross-process clock is needed, and a shared
bottleneck shows up as every process's decision wall inflating at once.  The
artifact keeps the per-process forward/CPU split, the client's request
round-trip, and the server's rounds/requests/rows per round, so a CPU contention
slowdown is not misread as a GPU one.

The baseline runs in the **same** forwarding mode as the concurrent run: a
one-producer server run prices the server's serial forward, and the speedup is
against that, not against the in-process path.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / ".resource" / "crforge" / "python"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from crforge_bridge.decision_server import DecisionClient

PRODUCER = _REPO_ROOT / "tools" / "crforge" / "self_play.py"
SERVER = _REPO_ROOT / "tools" / "crforge" / "decision_server.py"


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def producer_command(args: argparse.Namespace, *, output: Path, address: str | None) -> list[str]:
    command = [
        sys.executable,
        str(PRODUCER),
        "--count",
        str(args.lanes),
        "--segments",
        str(args.segments),
        "--decisions-per-segment",
        str(args.decisions_per_segment),
        "--seats",
        "0",
        "1",
        "--device",
        str(args.device),
        "--seed",
        str(args.seed),
        "--checkpoint",
        str(args.checkpoint),
        "--output",
        str(output),
    ]
    if not args.rollout:
        command.append("--no-rollout")
    if args.no_validate:
        command.append("--no-validate")
    if args.no_warmup_forward:
        command.append("--no-warmup-forward")
    for value in args.jvm_arg:
        command.append(f"--jvm-arg={value}")
    if args.fast_forward:
        # The in-process arm owns its forward here: give it the same fp16/graph
        # forward the server uses, so the two arms differ in batching, not in
        # precision or capture.
        command.append("--fast-forward")
        if args.inference_precision is not None:
            command.extend(("--inference-precision", str(args.inference_precision)))
        if args.no_cuda_graphs:
            command.append("--no-cuda-graphs")
        if args.full_cuda_graph:
            command.append("--full-cuda-graph")
    if address is not None:
        command.extend(("--decision-server", address))
        if args.verify_decision_server:
            command.append("--verify-decision-server")
    return command


def server_command(args: argparse.Namespace, *, address: str, ready: Path, output: Path) -> list[str]:
    command = [
        sys.executable,
        str(SERVER),
        "--address",
        address,
        "--checkpoint",
        str(args.checkpoint),
        "--device",
        str(args.server_device),
        "--max-requests",
        str(args.max_requests),
        "--wait-ms",
        str(args.wait_ms),
        "--ready-file",
        str(ready),
        "--output",
        str(output),
    ]
    if args.inference_precision is not None:
        command.extend(("--inference-precision", str(args.inference_precision)))
    if args.no_cuda_graphs:
        command.append("--no-cuda-graphs")
    if args.full_cuda_graph:
        command.append("--full-cuda-graph")
    return command


def _start(
    command: list[str], log_path: Path, env: dict[str, str] | None = None
) -> tuple[subprocess.Popen[bytes], Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command, cwd=_REPO_ROOT, stdout=handle, stderr=subprocess.STDOUT, env=dict(os.environ if env is None else env)
    )
    return process, handle


def _env(args: argparse.Namespace) -> dict[str, str]:
    """Every process in the run gets the same thread pinning.

    Each producer's CPU half is one thread of NumPy; a forward with the default
    ``OMP_NUM_THREADS`` (one thread per core) then oversubscribes the host by
    ``procs + 1`` and the *forward* is what inflates.  Pinning keeps the
    comparison about the architecture, not about thread-pool contention.
    """

    environment = dict(os.environ)
    environment["OMP_NUM_THREADS"] = str(args.omp_threads)
    environment["MKL_NUM_THREADS"] = str(args.omp_threads)
    return environment


def _load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _segment(artifact: dict[str, Any], *, name: str, returncode: int) -> dict[str, Any]:
    totals = artifact.get("totals") or {}
    segments = artifact.get("segments") or []
    steady = segments[-1] if segments else {}
    cpu_half = steady.get("cpu_half") or {}
    whole = cpu_half.get("whole_ms_mean") or {}
    server_block = (artifact.get("inference") or {}).get("decision_server") or {}
    requests = (server_block.get("request") or {}).get("round_trip_ms_mean")
    return {
        "artifact": name,
        "producer_returncode": returncode,
        "producer_gate_pass": bool((artifact.get("gate") or {}).get("pass")),
        "device": (artifact.get("shape") or {}).get("device"),
        "lanes": (artifact.get("shape") or {}).get("count"),
        "seats": len((artifact.get("shape") or {}).get("seats") or []),
        "collection_wall_s": float(steady.get("collection_wall_s") or 0.0),
        "lane_decisions": int(steady.get("lane_decisions") or 0),
        "lane_decisions_per_s": float(steady.get("collection_lane_decisions_per_s") or 0.0),
        "matches_per_s_1214": (
            float(steady.get("collection_lane_decisions_per_s") or 0.0)
            / max(len((artifact.get("shape") or {}).get("seats") or [0]) or 1, 1)
            / 1_214.0
        ),
        "decision_ms_mean": (
            sum(float(value) for value in (steady.get("ms_mean") or {}).values()) if steady.get("ms_mean") else None
        ),
        "forward_ms": (steady.get("ms_mean") or {}).get("inference_ms"),
        "cpu_ms": sum(float(value) for value in whole.values()) if whole else None,
        "cpu_half_ms": (cpu_half.get("breakdown_ms_mean") or {}),
        "decision_server": {
            "address": server_block.get("address"),
            "request_round_trip_ms_mean": requests,
            "request_pack_ms_mean": (server_block.get("request") or {}).get("pack_ms_mean"),
            "request_queue_wait_ms_mean": (server_block.get("request") or {}).get("queue_wait_ms_mean"),
            "sampling": server_block.get("sampling"),
            "verification": server_block.get("verification"),
        }
        if server_block
        else None,
        "totals": {
            "wall_s": totals.get("wall_s"),
            "lane_decisions": totals.get("lane_decisions"),
            "lane_decisions_per_s": totals.get("collection_lane_decisions_per_s"),
        },
    }


def run_level(args: argparse.Namespace, *, level: int, address: str | None, tag: str) -> dict[str, Any]:
    outputs: list[Path] = []
    launched: list[subprocess.Popen[bytes]] = []
    handles: list[Any] = []
    codes: list[int] = []
    started = time.perf_counter()
    try:
        for index in range(level):
            output = args.run_dir / f"{tag}-p{index}.json"
            command = producer_command(args, output=output, address=address)
            process, handle = _start(command, args.run_dir / f"{tag}-p{index}.log", _env(args))
            handles.append(handle)
            outputs.append(output)
            launched.append(process)
        for process in launched:
            try:
                codes.append(process.wait(timeout=args.timeout))
            except subprocess.TimeoutExpired:
                process.kill()
                codes.append(-9)
    finally:
        for process in launched:
            if process.poll() is None:
                process.kill()
        for handle in handles:
            handle.close()
    window_s = time.perf_counter() - started

    segments: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, (code, output) in enumerate(zip(codes, outputs, strict=False)):
        artifact = _load(output)
        if artifact is None:
            failures.append({"process": index, "returncode": code, "artifact": str(output)})
            continue
        segment = _segment(artifact, name=output.name, returncode=code)
        segment["process"] = index
        segments.append(segment)

    aggregate = sum(float(segment["lane_decisions_per_s"]) for segment in segments)
    rows: dict[str, Any] = {
        "processes": level,
        "launch_window_s": window_s,
        "completed": len(segments),
        "per_process_lane_decisions_s_mean": _mean([float(segment["lane_decisions_per_s"]) for segment in segments]),
        "aggregate_lane_decisions_s": aggregate,
        "aggregate_matches_s_1214": (aggregate / 2.0 / 1_214.0) if segments else 0.0,
        "decision_ms_mean": _mean(
            [float(segment["decision_ms_mean"]) for segment in segments if segment["decision_ms_mean"]]
        ),
        "forward_ms_mean": _mean([float(segment["forward_ms"]) for segment in segments if segment["forward_ms"]]),
        "cpu_ms_mean": _mean([float(segment["cpu_ms"]) for segment in segments if segment["cpu_ms"]]),
        "request_round_trip_ms_mean": _mean(
            [
                float(segment["decision_server"]["request_round_trip_ms_mean"])
                for segment in segments
                if segment["decision_server"] and segment["decision_server"]["request_round_trip_ms_mean"]
            ]
        ),
        "producer_gate_pass": all(bool(segment["producer_gate_pass"]) for segment in segments) if segments else False,
        "producers": segments,
        "failures": failures,
    }
    return rows


def server_summary(artifact: dict[str, Any] | None) -> dict[str, Any] | None:
    if artifact is None:
        return None
    server = artifact.get("server") or {}
    return {
        "wall_s": artifact.get("wall_s"),
        "device": server.get("device"),
        "precision": server.get("precision"),
        "cuda_graphs": server.get("cuda_graphs"),
        "full_cuda_graph": server.get("full_cuda_graph"),
        "wait_ms": server.get("wait_ms"),
        "max_requests": server.get("max_requests"),
        "rounds": server.get("rounds"),
        "requests": server.get("requests"),
        "rows": server.get("rows"),
        "requests_per_round": server.get("requests_per_round"),
        "rows_per_round": server.get("rows_per_round"),
        "max_requests_per_round": server.get("max_requests_per_round"),
        "forward_ms_mean": server.get("forward_ms_mean"),
        "forward_ms_max": server.get("forward_ms_max"),
        "forward_ms_per_request": server.get("forward_ms_per_request"),
        "assemble_ms_mean": server.get("assemble_ms_mean"),
        "row_buckets": server.get("row_buckets"),
        "shape_buckets": server.get("shape_buckets"),
        "captured_row_counts": server.get("captured_row_counts"),
        "modes": server.get("modes"),
    }


def server_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """The counters the concurrent arm added, not the server's whole life.

    The baseline arm shares the server process, so its rounds would otherwise be
    averaged into the arm the artifact is about.
    """

    def total_forward(stats: dict[str, Any]) -> float:
        return float(stats.get("forward_ms_mean") or 0.0) * int(stats.get("rounds") or 0)

    rounds = int(after.get("rounds") or 0) - int(before.get("rounds") or 0)
    requests = int(after.get("requests") or 0) - int(before.get("requests") or 0)
    rows = int(after.get("rows") or 0) - int(before.get("rows") or 0)
    forward_ms = total_forward(after) - total_forward(before)
    return {
        "rounds": rounds,
        "requests": requests,
        "rows": rows,
        "failed_rounds": int(after.get("failed_rounds") or 0) - int(before.get("failed_rounds") or 0),
        "requests_per_round": (requests / rounds) if rounds else None,
        "rows_per_round": (rows / rounds) if rounds else None,
        "forward_ms_per_round": (forward_ms / rounds) if rounds else None,
        "forward_ms_per_request": (forward_ms / requests) if requests else None,
        "max_requests_per_round": after.get("max_requests_per_round"),
        "row_buckets": after.get("row_buckets"),
        "shape_buckets": after.get("shape_buckets"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--procs", type=int, default=2)
    parser.add_argument("--lanes", type=int, default=8)
    parser.add_argument("--segments", type=int, default=3)
    parser.add_argument("--decisions-per-segment", type=int, default=32)
    parser.add_argument("--server", choices=("auto", "none"), default="auto")
    parser.add_argument("--address", default=None, help="server address (default: a socket under the run dir)")
    parser.add_argument("--max-requests", type=int, default=8)
    parser.add_argument("--wait-ms", type=float, default=2.0)
    parser.add_argument("--inference-precision", default=None, choices=("auto", "fp32", "fp16", "bf16"))
    parser.add_argument(
        "--fast-forward",
        action="store_true",
        help=(
            "the in-process arm's producers own their forward and use the ported forward "
            "(--fast-forward), with the same precision/graph flags as the server; refused "
            "with --server auto, where the forward is the server's"
        ),
    )
    parser.add_argument("--no-cuda-graphs", action="store_true")
    parser.add_argument("--full-cuda-graph", action="store_true")
    parser.add_argument("--verify-decision-server", action="store_true")
    parser.add_argument("--device", default="cpu", help="the producers' device; cpu when a server owns the forward")
    parser.add_argument(
        "--server-device",
        default="cpu",
        help="the decision server's device (the forward's device); the producers stay on the cpu half",
    )
    parser.add_argument("--checkpoint", type=Path, default=Path(".resource/RoyaleFish/models/B-general-v275.pt"))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--rollout", action="store_true", help="store each producer's rollout (default: probe shape)")
    parser.add_argument("--no-validate", action="store_true")
    parser.add_argument("--no-warmup-forward", action="store_true")
    parser.add_argument(
        "--jvm-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="extra option for every producer's engine JVM (repeatable)",
    )
    parser.add_argument("--baseline", dest="baseline", action="store_true", default=True)
    parser.add_argument("--no-baseline", dest="baseline", action="store_false")
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument(
        "--omp-threads",
        type=int,
        default=1,
        help=(
            "OMP_NUM_THREADS for every process in the run (default 1: each producer's CPU half is "
            "single-threaded and an unpinned forward oversubscribes the host)"
        ),
    )
    parser.add_argument("--run-dir", type=Path, default=Path("runs/crforge-c4/multiproc"))
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c4/multiproc.json"))
    args = parser.parse_args(argv)

    if args.procs < 1:
        raise SystemExit("--procs must be positive")
    if args.server == "auto" and str(args.device) != "cpu":
        raise SystemExit(
            "--server auto keeps the producers on the cpu half: pass --device cpu (the forward is "
            "the server's) and --server-device for the card"
        )
    if args.fast_forward and args.server == "auto":
        raise SystemExit(
            "--fast-forward is the producer's own forward; with --server auto the server owns it "
            "(pass --inference-precision for the server instead)"
        )
    if args.procs > (os.cpu_count() or 1):
        print(f"note: {args.procs} producers on {os.cpu_count()} CPUs; the CPU half will queue", file=sys.stderr)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    address: str | None = None
    server: subprocess.Popen[bytes] | None = None
    server_handle: Any = None
    server_output = args.run_dir / "decision-server.json"
    try:
        if args.server == "auto":
            address = args.address or f"unix:{args.run_dir / 'decision.sock'}"
            ready = args.run_dir / "decision.ready.json"
            if ready.exists():
                ready.unlink()
            command = server_command(args, address=address, ready=ready, output=server_output)
            server, server_handle = _start(command, args.run_dir / "decision-server.log", _env(args))
            deadline = time.perf_counter() + args.timeout
            while not ready.is_file():
                if server.poll() is not None:
                    raise SystemExit(
                        f"the decision server exited with {server.returncode}; see {args.run_dir / 'decision-server.log'}"
                    )
                if time.perf_counter() > deadline:
                    raise SystemExit("timed out waiting for the decision server to bind")
                time.sleep(0.05)
            ready_info = json.loads(ready.read_text(encoding="utf-8"))
            print(json.dumps({"event": "server_ready", **ready_info}), flush=True)
            # The supervisor's identity is read off the server's own ready file,
            # so it can snapshot the counters without loading the model.
            supervisor_identity = {
                "checkpoint_sha256": ready_info.get("checkpoint_sha256"),
                "weights_digest": ready_info.get("weights_digest"),
            }
        elif args.address is not None:
            raise SystemExit("--address names a server this tool starts; drop it with --server none")

        baseline: dict[str, Any] | None = None
        if args.baseline:
            print(json.dumps({"event": "baseline", "processes": 1}), flush=True)
            baseline = run_level(args, level=1, address=address, tag="baseline")
        before: dict[str, Any] | None = None
        if address is not None:
            with DecisionClient(address, identity=supervisor_identity, timeout=60.0) as supervisor:
                before = supervisor.server_stats()
        print(json.dumps({"event": "concurrent", "processes": args.procs}), flush=True)
        concurrent = run_level(args, level=args.procs, address=address, tag=f"k{args.procs}")
        after: dict[str, Any] | None = None
        if address is not None:
            with DecisionClient(address, identity=supervisor_identity, timeout=60.0) as supervisor:
                after = supervisor.server_stats()
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=60)
            except subprocess.TimeoutExpired:
                server.kill()
        if server_handle is not None:
            server_handle.close()

    server_artifact = _load(server_output) if args.server == "auto" else None
    server_block = server_summary(server_artifact)
    server_arm = server_delta(before, after) if before is not None and after is not None else None

    baseline_rate = (
        float(baseline["aggregate_lane_decisions_s"]) if baseline and baseline["producer_gate_pass"] else None
    )
    ideal = baseline_rate * args.procs if baseline_rate else None
    aggregate = float(concurrent["aggregate_lane_decisions_s"])
    artifact = {
        "schema": "crforge-multiproc.v1",
        "measured": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "shape": {
            "lanes": args.lanes,
            "segments": args.segments,
            "decisions_per_segment": args.decisions_per_segment,
            "procs": args.procs,
            "device": args.device,
            "server_device": args.server_device,
            "seed": args.seed,
            "checkpoint": str(args.checkpoint),
        },
        "forwarding": {
            "mode": "decision-server" if args.server == "auto" else "in-process",
            "address": address,
            "verify": bool(args.verify_decision_server),
            "max_requests": args.max_requests,
            "wait_ms": args.wait_ms,
            "inference_precision": args.inference_precision,
            "cuda_graphs": not args.no_cuda_graphs,
            "full_cuda_graph": args.full_cuda_graph,
        },
        "host": {"cpu_count": os.cpu_count(), "python": sys.version.split()[0]},
        "baseline": baseline,
        "concurrent": concurrent,
        "server": server_block,
        "server_arm": server_arm,
        "aggregate": {
            "baseline_lane_decisions_s": baseline_rate,
            "ideal_lane_decisions_s": ideal,
            "measured_lane_decisions_s": aggregate,
            "retention_vs_ideal": (aggregate / ideal) if ideal else None,
            "speedup_vs_baseline": (aggregate / baseline_rate) if baseline_rate else None,
            "per_process_efficiency": (
                float(concurrent["per_process_lane_decisions_s_mean"] or 0.0) / baseline_rate if baseline_rate else None
            ),
            "matches_per_s_1214": concurrent["aggregate_matches_s_1214"],
        },
        "gate": {
            "producers_pass": bool(concurrent["producer_gate_pass"]),
            "completed_all": len(concurrent["producers"]) == args.procs,
            "server_rounds": None if server_arm is None else server_arm["rounds"],
            "server_requests": None if server_arm is None else server_arm["requests"],
            "server_requests_per_round": None if server_arm is None else server_arm["requests_per_round"],
            "server_forward_ms_per_request": None if server_arm is None else server_arm["forward_ms_per_request"],
            "server_failed_rounds": None if server_arm is None else server_arm["failed_rounds"],
            "pass": bool(concurrent["producer_gate_pass"]) and len(concurrent["producers"]) == args.procs,
        },
        "totals": {
            "producers": len(concurrent["producers"]),
            "requests_served": None if server_arm is None else server_arm["requests"],
            "rows_served": None if server_arm is None else server_arm["rows"],
            "request_round_trip_ms_mean": concurrent["request_round_trip_ms_mean"],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "pass": artifact["gate"]["pass"],
                "forwarding": artifact["forwarding"]["mode"],
                "aggregate": artifact["aggregate"],
                "server": server_block,
                "server_arm": server_arm,
                "output": str(args.output),
            },
            indent=2,
        )
    )
    return 0 if artifact["gate"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
