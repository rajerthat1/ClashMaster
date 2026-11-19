#!/usr/bin/env python3
"""C4 cloud capacity: is the provider's ceiling the CPU processes or the GPU?

The provider sweep turns on one question: at the shape the C4 producer actually
runs, is throughput capped by the cores the Python packet needs, or by the GPU
that serves the recurrent forward?  The project's capacity equation is::

    matches/s ≈ 0.211 × min(N_proc, G × E / g) / T

with, at the 128-lane, two-seat gate shape:

* ``0.211`` — matches per decision round: 128 lanes × 2 seats = 256
  lane-decisions, and the repo's ``matches/s`` convention uses 1,214
  lane-decisions per match (6,068 ticks ÷ 5).  ``256 / 1,214 = 0.2109``.  Keep
  the shape fixed when comparing providers; this tool recomputes the constant
  from the artifact's lane and seat counts, so a 32-lane run is compared
  honestly against a 128-lane one in the *rate*, never in the verdict.
* ``N_proc`` — producer processes the host can run.  One process owns one
  single-threaded ``VectorBattle`` and one decision forward and is CPU-bound on
  one core, so this is the usable vCPU count (``--cpus``); it is *not* the lane
  count.
* ``G`` — GPUs on the instance.  A partitioned card is ``G < 1``.
* ``E`` — the fraction of those GPUs the workload may use (1.0 for a dedicated
  card, lower for MIG, preemption or another tenant).
* ``g`` — GPU time one process consumes per decision round, divided by the
  decision wall: the producer's median ``inference_ms / decision_ms``.  At the
  recorded A6000 gate shape this is ``157.4 / 646.8 = 0.243``, i.e. **4.1
  processes fill one A6000** — the same "run ~4 without changes" result in
  the crforge plan
* ``T`` — steady-state seconds per decision round: ``collection_wall_s /
  decisions`` on the last warm segment.

The verdict is the comparison of the two process ceilings, ``N_proc`` against
``G × E / g``.  If the GPU serves fewer processes than the host has cores, the
host is **GPU-bound**: vCPUs past that point only queue on the card.  If it
serves more, the host is **CPU-bound** and the lever is more or faster cores, or
a thinner Python packet.  The rate uses the equation; the verdict does not
depend on ``0.211`` or ``T``.

Two rate conventions coexist in the repo and differ by 2×.  This tool's primary
``matches/s`` is the **1,214** convention (both actor seats' lane-decisions are
counted as match decisions), which is what the equation and
the crforge plan use.  Every report also carries the **2,428**
convention (a physical match: one seat), which is what the producer artifact's
own ``matches_per_s`` field uses.  Quote the convention with the number.

Usage on each provider (the producer itself is the benchmark)::

    # live measurement on the box; 128 lanes x 3 x 32 ~= 90 s at the A6000 rate
    .venv/bin/python tools/crforge/cloud_capacity.py --run \\
        --provider thunder-a6000 --price-per-hour 0.35 \\
        --output runs/crforge-c4/cloud-thunder-a6000.json

    # the corrected 8-128-lane scaling table from session 34's open item
    .venv/bin/python tools/crforge/cloud_capacity.py --run \\
        --lanes 8 32 64 128 --provider thunder-a6000 --price-per-hour 0.35

    # validate the process ceiling: run k producers concurrently and see
    # whether the aggregate stops growing where G*E/g says it should
    .venv/bin/python tools/crforge/cloud_capacity.py --run \\
        --sweep-procs 1 2 4 6 --provider thunder-a6000 --price-per-hour 0.35

    # the forwarded loop, with the sampled decode in one graph (the A6000's
    # fastest forward; the producer refuses it if the random probe is frozen)
    .venv/bin/python tools/crforge/cloud_capacity.py --run \\
        --fast-forward --full-cuda-graph --sweep-procs 1 2 4 6 \\
        --provider thunder-a6000 --price-per-hour 0.35

    # score a provider from an artifact already on disk (a recorded run carries
    # no vCPU/GPU count, so both must be stated)
    .venv/bin/python tools/crforge/cloud_capacity.py \\
        --artifact runs/crforge-c4/gpu-forward-scaling-128-long.json \\
        --provider a6000 --cpus 6 --gpus 1 --price-per-hour 0.35

    # compare the saved provider reports
    .venv/bin/python tools/crforge/cloud_capacity.py \\
        --compare runs/crforge-c4/cloud-*.json

The tool does **not** use rollout storage: it is a throughput probe
(``--no-rollout``) and defaults to ``--no-validate`` because validation syncs
the device on every decision and is not the production path.  The artifact is
``crforge-c4-cloud-capacity.v1``.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCER = REPO_ROOT / "tools" / "crforge" / "self_play.py"
SCHEMA = "crforge-c4-cloud-capacity.v1"
MATCH_DECISIONS = 1_214
BASELINE_LANES = 128
BASELINE_SEATS = 2
BASELINE_MATCHES_PER_DECISION = BASELINE_LANES * BASELINE_SEATS / MATCH_DECISIONS
# Inside this band the two ceilings are close enough that either side can move
# the rate; outside it, the named side is the one to rent.
BALANCE_BAND = 0.15


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def matches_per_decision(lanes: int, seats: int) -> float:
    """Matches per decision round under the repo's 1,214-lane-decision convention."""

    return lanes * seats / MATCH_DECISIONS


def segment_row(artifact: dict[str, Any], *, index: int | None = None, name: str = "") -> dict[str, Any]:
    """The steady-state decision wall and its forward share for one segment.

    The last segment is the steady state: the producer warms the sampled and
    decoded path before segment 0 by default, and a final segment has no
    segment-boundary flush behind it.  ``collection_wall_s`` excludes the update
    wall, the JVM startup and the model load, so it is the loop's own clock.
    """

    shape = artifact.get("shape") or {}
    segments = artifact.get("segments") or []
    usable = [position for position, segment in enumerate(segments) if int(segment.get("decisions") or 0) > 0]
    if not usable:
        raise ValueError(f"{name or 'artifact'} has no segment with a decision count")
    selected = usable[-1] if index is None else index
    segment = segments[selected]
    decisions = int(segment["decisions"])
    lanes = int(shape.get("count") or 0)
    seats = len(shape.get("seats") or [0, 1])

    collection_s = segment.get("collection_wall_s")
    if collection_s is None:
        collection_s = float(segment.get("wall_s") or 0.0) - float(segment.get("update_wall_s") or 0.0)
    collection_s = float(collection_s)
    if collection_s <= 0.0:
        raise ValueError(f"{name or 'artifact'} segment {selected} has no collection wall")

    decision_s = collection_s / decisions
    lane_decisions = int(segment.get("lane_decisions") or decisions * lanes * seats)
    per_decision = lane_decisions / decisions / MATCH_DECISIONS

    buckets = segment.get("ms") or {}
    means = segment.get("ms_mean") or {}
    forward_median = _optional_float(buckets.get("inference_ms"))
    forward_mean = _optional_float(means.get("inference_ms"))
    # The median is the steady-state forward.  A mean over a cold segment would
    # fold the one-time 5 s setup into "GPU demand" and overstate g.
    if forward_median is not None and forward_median > 0.0:
        forward_ms = forward_median
        forward_source = "ms_median"
    elif forward_mean is not None and forward_mean > 0.0:
        forward_ms = forward_mean
        forward_source = "ms_mean"
    else:
        forward_ms = None
        forward_source = "missing"

    decision_ms = decision_s * 1000.0
    first_forward = _optional_float((segment.get("ms_first") or {}).get("inference_ms"))
    warnings: list[str] = []
    if decisions < 16:
        warnings.append(
            f"{decisions} decisions: the one-time first decision is a large share of this wall; "
            "use --segments >= 3 or the producer's default warmup before quoting the rate"
        )
    if shape.get("warmup_forward") is False:
        warnings.append(
            "produced with --no-warmup-forward: T includes the cold first kernel/allocator cost; "
            "re-run with the default warmup for a steady-state rate"
        )
    if first_forward is not None and forward_ms is not None and first_forward > 2.0 * forward_ms:
        warnings.append(
            f"first forward {first_forward:.0f} ms vs median {forward_ms:.0f} ms: "
            "the cold decision is in the mean wall, not in the median forward"
        )
    return {
        "artifact": name,
        "segment": int(selected),
        "lanes": lanes,
        "seats": seats,
        "decisions": decisions,
        "lane_decisions": lane_decisions,
        "collection_wall_s": collection_s,
        "decision_s": decision_s,
        "decision_ms": decision_ms,
        "forward_ms": forward_ms,
        "forward_ms_median": forward_median,
        "forward_ms_mean": forward_mean,
        "first_forward_ms": first_forward,
        "forward_source": forward_source,
        "warnings": warnings,
        "cpu_ms": None if forward_ms is None else decision_ms - forward_ms,
        "forward_fraction": None if forward_ms is None else forward_ms / decision_ms,
        "matches_per_decision": per_decision,
        "matches_per_s_1214_per_process": per_decision / decision_s,
        "matches_per_s_2428_per_process": per_decision / decision_s / 2.0,
        "artifact_matches_per_s": _optional_float(segment.get("matches_per_s")),
        "device": shape.get("device"),
        "warmup_forward": shape.get("warmup_forward"),
        "validated_every_decision": shape.get("validated_every_decision"),
        "checkpoint": (artifact.get("substrate") or {}).get("checkpoint_id"),
    }


def capacity_report(
    host: dict[str, Any], row: dict[str, Any], *, gpu_efficiency: float = 1.0, price_per_hour: float | None = None
) -> dict[str, Any]:
    """Apply ``matches/s ≈ 0.211 × min(N_proc, G × E / g) / T`` to one segment.

    ``N_proc`` is the process ceiling; ``G × E / g`` is the GPU's process
    capacity.  The binding side is the smaller one, and the rate is the
    equation evaluated there.  A host with no GPU is CPU-bound by construction:
    its "forward" is CPU work and the GPU term does not exist.
    """

    processes = float(host["processes"])
    gpus = float(host["gpus"])
    per_decision = float(row["matches_per_decision"])
    decision_s = float(row["decision_s"])
    per_process = per_decision / decision_s if decision_s > 0.0 else 0.0
    cpu_ceiling = per_process * processes
    forward_fraction = row.get("forward_fraction")

    gpu_slots: float | None = None
    gpu_ceiling: float | None = None
    if gpus > 0.0 and forward_fraction is not None and forward_fraction > 0.0:
        gpu_slots = gpus * gpu_efficiency / forward_fraction
        gpu_ceiling = per_process * gpu_slots
        ratio = processes / gpu_slots
        if ratio > 1.0 + BALANCE_BAND:
            binding = "gpu"
        elif ratio < 1.0 - BALANCE_BAND:
            binding = "cpu"
        else:
            binding = "balanced"
    else:
        binding = "cpu"

    capacity = processes if gpu_slots is None else min(processes, gpu_slots)
    predicted = cpu_ceiling if gpu_ceiling is None else min(cpu_ceiling, gpu_ceiling)
    gpu_busy = None
    if gpu_slots is not None and gpus > 0.0:
        gpu_busy = capacity * float(forward_fraction) / (gpus * gpu_efficiency)

    if binding == "gpu":
        why = (
            f"GPU term binds: G*E/g = {gpus:g}*{gpu_efficiency:g}/{float(forward_fraction):.3f} "
            f"= {gpu_slots:.2f} processes, below N_proc = {processes:g}."
        )
        adjust = (
            f"More vCPUs past ~{gpu_slots:.1f} add little and can slow every process; the sweep is "
            "the check. Add GPUs, raise E (NVIDIA MPS or one batched decision server), or cut g "
            "(CUDA graphs, fp16, fewer rows)."
        )
    elif gpu_slots is None:
        why = "No GPU declared: the producer's forward is CPU work, so N_proc is the only ceiling."
        share = row.get("forward_fraction")
        if share is not None and share >= 0.5:
            adjust = (
                f"The forward is {share * 100:.0f} % of the decision and runs on the CPU; a GPU can move "
                "that share, after which the CPU packet is the next ceiling. Re-measure g on the GPU."
            )
        elif share is not None:
            adjust = (
                f"The forward is only {share * 100:.0f} % of the decision; a GPU would move little. "
                "More or faster vCPUs is the lever."
            )
        else:
            adjust = (
                "Rent a GPU only when the forward share is large enough to move off the CPU; "
                "then re-measure g. Until then, more or faster vCPUs is the lever."
            )
    elif binding == "cpu":
        cpu_ms = row.get("cpu_ms")
        decision_ms = row.get("decision_ms")
        why = f"CPU term binds: N_proc = {processes:g}, below G*E/g = {gpu_slots:.2f} processes."
        if cpu_ms is not None and decision_ms is not None:
            adjust = (
                f"Add vCPUs/processes, or cut the CPU half of T ({cpu_ms:.0f} ms of {decision_ms:.0f} ms); "
                "a bigger GPU stays idle until N_proc exceeds the GPU capacity."
            )
        else:
            adjust = "Add vCPUs/processes; the GPU has headroom."
    else:
        why = f"Balanced: N_proc = {processes:g} and G*E/g = {gpu_slots:.2f} are within {BALANCE_BAND * 100:.0f} %."
        adjust = (
            "Either side can move the ceiling. More vCPUs and a second GPU are both "
            "defensible; choose by $/match, not by utilisation."
        )

    cost = None
    if price_per_hour is not None and predicted > 0.0:
        cost = {
            "price_per_hour": float(price_per_hour),
            "usd_per_million_matches_1214": float(price_per_hour) / (predicted * 3600.0) * 1e6,
            "usd_per_million_matches_2428": float(price_per_hour) / (predicted / 2.0 * 3600.0) * 1e6,
            "matches_per_day_1214": predicted * 86_400.0,
            "matches_per_day_2428": predicted / 2.0 * 86_400.0,
        }

    return {
        "binding": binding,
        "why": why,
        "adjust": adjust,
        "per_process_matches_s_1214": per_process,
        "cpu_ceiling_matches_s_1214": cpu_ceiling,
        "gpu_process_capacity": gpu_slots,
        "gpu_ceiling_matches_s_1214": gpu_ceiling,
        "capacity_processes": capacity,
        "predicted_matches_s_1214": predicted,
        "predicted_matches_s_2428": predicted / 2.0,
        "gpu_busy_fraction_at_predicted": gpu_busy,
        "cpu_busy_fraction_at_predicted": capacity / processes if processes > 0.0 else None,
        "cost": cost,
        "equation": {
            "form": "matches/s = matches_per_decision * min(N_proc, G*E/g) / T",
            "matches_per_decision": per_decision,
            "constant_at_128_lanes_two_seats": BASELINE_MATCHES_PER_DECISION,
            "N_proc": processes,
            "G": gpus,
            "E": float(gpu_efficiency),
            "g": forward_fraction if gpus > 0.0 else None,
            "T_s": decision_s,
            "gpu_process_capacity": gpu_slots,
            "capacity_processes": capacity,
            "matches_per_s_1214": predicted,
        },
    }


def _read_cpu_model() -> str | None:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def _read_memory_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return round(float(line.split()[1]) / 1024.0 / 1024.0, 1)
    except (OSError, ValueError):
        pass
    return None


def _nvidia_smi_inventory() -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if completed.returncode == 0 and lines:
            names = []
            memory = []
            for line in lines:
                parts = [part.strip() for part in line.split(",")]
                names.append(parts[0])
                if len(parts) > 1:
                    try:
                        memory.append(round(float(parts[1].split()[0]) / 1024.0, 1))
                    except (ValueError, IndexError):
                        pass
            return {
                "gpus": len(lines),
                "names": names,
                "memory_gb": memory,
                "source": "nvidia-smi",
                "torch": None,
                "cuda": None,
            }
    except (OSError, subprocess.SubprocessError):
        pass
    return {"gpus": 0, "names": [], "memory_gb": [], "source": "none", "torch": None, "cuda": None}


def _detect_gpus() -> dict[str, Any]:
    try:
        import torch

        if torch.cuda.is_available():
            count = int(torch.cuda.device_count())
            names: list[str] = []
            memory: list[float] = []
            for index in range(count):
                properties = torch.cuda.get_device_properties(index)
                names.append(str(properties.name))
                memory.append(round(float(properties.total_memory) / 1024.0**3, 1))
            return {
                "gpus": count,
                "names": names,
                "memory_gb": memory,
                "source": "torch",
                "torch": str(torch.__version__),
                "cuda": None if torch.version.cuda is None else str(torch.version.cuda),
            }
    except (ImportError, OSError, RuntimeError):
        # torch is optional; a CPU-only box (or a broken CUDA install) still inventories.
        return _nvidia_smi_inventory()
    return _nvidia_smi_inventory()


def host_inventory(*, processes: float | None = None, gpus: float | None = None) -> dict[str, Any]:
    """The host as the equation sees it: N_proc, G, and the machine identity."""

    affinity = None
    if hasattr(os, "sched_getaffinity"):
        try:
            affinity = len(os.sched_getaffinity(0))
        except OSError:
            affinity = None
    detected = _detect_gpus()
    if processes is not None:
        resolved_processes = float(processes)
        source = "argument"
    else:
        resolved_processes = float(affinity or os.cpu_count() or 1)
        source = "sched_getaffinity" if affinity else "cpu_count"
    return {
        "hostname": socket.gethostname(),
        "cpu_model": _read_cpu_model(),
        "cpu_count": os.cpu_count(),
        "cpu_affinity": affinity,
        "memory_gb": _read_memory_gb(),
        "processes": resolved_processes,
        "processes_source": source,
        "gpus": float(detected["gpus"] if gpus is None else gpus),
        "gpus_detected": detected["gpus"],
        "gpu_names": detected["names"],
        "gpu_memory_gb": detected["memory_gb"],
        "gpu_source": detected["source"],
        "torch": detected["torch"],
        "cuda": detected["cuda"],
    }


def producer_command(args: argparse.Namespace, *, output: Path, lanes: int, segments: int, decisions: int) -> list[str]:
    command = [
        sys.executable,
        str(PRODUCER),
        "--count",
        str(lanes),
        "--segments",
        str(segments),
        "--decisions-per-segment",
        str(decisions),
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
        "--no-rollout",
    ]
    if not args.validate:
        command.append("--no-validate")
    if args.no_warmup_forward:
        command.append("--no-warmup-forward")
    if args.fast_forward:
        command.append("--fast-forward")
        if args.inference_precision is not None:
            command.extend(("--inference-precision", str(args.inference_precision)))
        if getattr(args, "full_cuda_graph", False):
            command.append("--full-cuda-graph")
        if args.no_cuda_graphs:
            command.append("--no-cuda-graphs")
    return command


def _run(command: list[str], log_path: Path, timeout: float) -> tuple[float, int]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as handle:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
            env=dict(os.environ),
            timeout=timeout,
        )
    return time.perf_counter() - started, int(completed.returncode)


def run_shape(args: argparse.Namespace, *, lanes: int, tag: str) -> dict[str, Any]:
    output = args.run_dir / f"{tag}.json"
    log_path = args.run_dir / f"{tag}.log"
    print(f"{tag}: {lanes} lanes x {args.segments} x {args.decisions_per_segment}", file=sys.stderr)
    try:
        wall, returncode = _run(
            producer_command(
                args, output=output, lanes=lanes, segments=args.segments, decisions=args.decisions_per_segment
            ),
            log_path,
            args.timeout,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(f"producer timed out after {args.timeout:g} s; see {log_path}") from None
    # The producer's exit code reports its *gate* (ACT rate, rejects, rollouts),
    # not whether it timed a loop.  A provider probe must read the throughput of
    # a short shape even when that shape never acts; only a missing artifact is
    # fatal here.
    if not output.is_file():
        raise SystemExit(f"producer wrote no artifact (exit {returncode}); see {log_path}")
    artifact = json.loads(output.read_text(encoding="utf-8"))
    row = segment_row(artifact, name=output.name)
    row["run_wall_s"] = wall
    row["producer_returncode"] = returncode
    row["producer_gate_pass"] = bool((artifact.get("gate") or {}).get("pass"))
    row["artifact_path"] = str(output)
    row["log_path"] = str(log_path)
    return row


def run_sweep(
    args: argparse.Namespace, *, host: dict[str, Any], lanes: int, levels: list[int], baseline: dict[str, Any]
) -> list[dict[str, Any]]:
    """Run k producers concurrently and measure the per-process rate at k.

    Concurrent producers keep their own ``collection_wall_s``, so no cross-
    process clock alignment is needed: if the GPU saturates, *each* process's
    decision wall inflates and the aggregate ``sum(matches_per_s)`` stops
    growing.  The forward and CPU halves are recorded separately so a CPU
    contention slowdown is not misread as a GPU one.
    """

    rows: list[dict[str, Any]] = []
    for level in levels:
        print(f"sweep: {level} concurrent producer(s) at {lanes} lanes", file=sys.stderr)
        tag = f"sweep-k{level}"
        outputs: list[Path] = []
        launched: list[subprocess.Popen[bytes]] = []
        handles: list[Any] = []
        codes: list[int] = []
        try:
            for index in range(level):
                output = args.run_dir / f"{tag}-p{index}.json"
                log_path = args.run_dir / f"{tag}-p{index}.log"
                command = producer_command(
                    args, output=output, lanes=lanes, segments=args.sweep_segments, decisions=args.sweep_decisions
                )
                handle = log_path.open("w", encoding="utf-8")
                handles.append(handle)
                outputs.append(output)
                launched.append(
                    subprocess.Popen(
                        command, cwd=REPO_ROOT, stdout=handle, stderr=subprocess.STDOUT, env=dict(os.environ)
                    )
                )
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

        segments: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        nonzero: list[dict[str, Any]] = []
        for index, (code, output) in enumerate(zip(codes, outputs, strict=False)):
            if not output.is_file():
                failures.append({"process": index, "returncode": code, "artifact": str(output)})
                continue
            try:
                artifact = json.loads(output.read_text(encoding="utf-8"))
                segment = segment_row(artifact, name=output.name)
            except (ValueError, json.JSONDecodeError) as error:
                failures.append({"process": index, "returncode": code, "error": str(error)})
                continue
            segment["producer_returncode"] = code
            segment["producer_gate_pass"] = bool((artifact.get("gate") or {}).get("pass"))
            segments.append(segment)
            if code != 0:
                nonzero.append({"process": index, "returncode": code, "artifact": str(output)})

        if not segments:
            rows.append({"processes": level, "failures": failures, "measured": False})
            continue

        aggregate = sum(float(segment["matches_per_s_1214_per_process"]) for segment in segments)
        mean_decision_s = _mean([float(segment["decision_s"]) for segment in segments])
        forward_values = [float(segment["forward_ms"]) for segment in segments if segment.get("forward_ms")]
        cpu_values = [float(segment["cpu_ms"]) for segment in segments if segment.get("cpu_ms") is not None]
        fraction_values = [
            float(segment["forward_fraction"]) for segment in segments if segment.get("forward_fraction") is not None
        ]
        baseline_rate = float(baseline["matches_per_s_1214_per_process"])
        gpu_slots = None
        if float(host["gpus"]) > 0.0 and fraction_values:
            gpu_slots = float(host["gpus"]) * args.gpu_efficiency / _mean(fraction_values)
        predicted_aggregate = baseline_rate * (level if gpu_slots is None else min(level, gpu_slots))
        rows.append(
            {
                "processes": level,
                "measured": True,
                "processes_completed": len(segments),
                "decision_s_mean": mean_decision_s,
                "forward_ms_mean": _mean(forward_values) if forward_values else None,
                "cpu_ms_mean": _mean(cpu_values) if cpu_values else None,
                "forward_fraction_mean": _mean(fraction_values) if fraction_values else None,
                "per_process_matches_s_1214_mean": _mean(
                    [float(segment["matches_per_s_1214_per_process"]) for segment in segments]
                ),
                "aggregate_matches_s_1214": aggregate,
                "aggregate_matches_s_2428": aggregate / 2.0,
                "predicted_aggregate_matches_s_1214": predicted_aggregate,
                "retention_vs_ideal_scaling": (
                    aggregate / (level * baseline_rate) if level > 0 and baseline_rate > 0.0 else None
                ),
                "per_process_slowdown_vs_baseline": (
                    mean_decision_s / float(baseline["decision_s"]) if baseline["decision_s"] > 0.0 else None
                ),
                "forward_slowdown_vs_baseline": (
                    _mean(forward_values) / float(baseline["forward_ms"])
                    if forward_values and baseline.get("forward_ms")
                    else None
                ),
                "cpu_slowdown_vs_baseline": (
                    _mean(cpu_values) / float(baseline["cpu_ms"]) if cpu_values and baseline.get("cpu_ms") else None
                ),
                "gpu_busy_fraction": (
                    level * _mean(fraction_values) / (float(host["gpus"]) * args.gpu_efficiency)
                    if fraction_values and float(host["gpus"]) > 0.0
                    else None
                ),
                "gpu_process_capacity_from_sweep": gpu_slots,
                "failures": failures,
                "nonzero_returncodes": nonzero,
            }
        )
    return rows


def sweep_verdict(
    sweep: list[dict[str, Any]], *, gpu_slots: float | None, gpu_ceiling_matches_s: float | None = None
) -> dict[str, Any]:
    """Did the aggregate stop growing where the equation said it would?

    Two claims are checked separately.  The *process* claim is ``G × E / g``;
    the *rate* claim is the GPU ceiling, ``matches_per_decision × slots / T``.  A
    host can fail the first and pass the second: when the CPU half and the
    forward half slow together, the aggregate keeps climbing for a process or
    two past ``slots`` but still approaches the GPU ceiling, and the ceiling is
    the number the provider decision turns on.
    """

    measured = [row for row in sweep if row.get("measured")]
    if not measured:
        return {"measured": False, "confirms_equation": None, "note": "no sweep level completed"}
    saturated_at = None
    for row in sorted(measured, key=lambda item: int(item["processes"])):
        retention = row.get("retention_vs_ideal_scaling")
        if retention is not None and retention < 0.8:
            saturated_at = int(row["processes"])
            break
    by_level = {
        int(row["processes"]): {
            "aggregate_matches_s_1214": row["aggregate_matches_s_1214"],
            "retention": row.get("retention_vs_ideal_scaling"),
            "per_process_slowdown": row.get("per_process_slowdown_vs_baseline"),
            "forward_slowdown": row.get("forward_slowdown_vs_baseline"),
            "cpu_slowdown": row.get("cpu_slowdown_vs_baseline"),
        }
        for row in measured
    }
    reference = None
    for row in measured:
        if saturated_at is not None and int(row["processes"]) == saturated_at:
            reference = row
    if reference is None:
        reference = max(measured, key=lambda item: int(item["processes"]))
    forward_slowdown = reference.get("forward_slowdown_vs_baseline")
    cpu_slowdown = reference.get("cpu_slowdown_vs_baseline")
    if forward_slowdown is None or cpu_slowdown is None:
        bound_by = None
    elif (
        forward_slowdown > 1.15
        and cpu_slowdown > 1.15
        and abs(forward_slowdown - cpu_slowdown) <= 0.25 * max(forward_slowdown, cpu_slowdown)
    ):
        bound_by = "cpu" if gpu_slots is None else "shared"
    elif forward_slowdown > cpu_slowdown * 1.15:
        bound_by = "gpu"
    elif cpu_slowdown > forward_slowdown * 1.15:
        bound_by = "cpu"
    else:
        bound_by = "mixed"

    largest_row = max(measured, key=lambda item: int(item["processes"]))
    measured_aggregate = float(largest_row["aggregate_matches_s_1214"])
    rate_ratio = (
        measured_aggregate / float(gpu_ceiling_matches_s)
        if gpu_ceiling_matches_s is not None and gpu_ceiling_matches_s > 0.0
        else None
    )
    rate_confirmed = rate_ratio is not None and 0.8 <= rate_ratio <= 1.2
    confirms = None
    if gpu_slots is not None:
        largest = int(largest_row["processes"])
        if saturated_at is not None:
            confirms = abs(saturated_at - gpu_slots) <= 1.0 or rate_confirmed
        else:
            confirms = gpu_slots >= largest or rate_confirmed
    return {
        "measured": True,
        "predicted_gpu_capacity_processes": gpu_slots,
        "predicted_gpu_ceiling_matches_s": gpu_ceiling_matches_s,
        "measured_aggregate_at_largest_matches_s": measured_aggregate,
        "measured_vs_gpu_ceiling": rate_ratio,
        "saturated_at_processes": saturated_at,
        "bound_by": bound_by,
        "confirms_equation": confirms,
        "by_level": by_level,
        "note": (
            "retention is aggregate / (k * one-process rate); < 0.8 is saturation. "
            "A per-process slowdown whose forward half moves and CPU half holds is the GPU; "
            "the reverse is CPU contention; both moving together is CPU contention on a "
            "CPU-only host (or shared memory bandwidth on a GPU host)."
        ),
    }


def _format_cost(cost: dict[str, Any] | None, *, key: str = "usd_per_million_matches_1214") -> str:
    if not cost:
        return "-"
    return f"{float(cost[key]):.1f}"


def _print_report(artifact: dict[str, Any]) -> None:
    host = artifact["host"]
    print(
        f"provider: {artifact['provider']}   host: {host['processes']:g} processes, "
        f"{host['gpus']:g} GPU(s), E={artifact['settings']['gpu_efficiency']:g}"
    )
    if host.get("gpu_names"):
        print(f"gpu: {', '.join(host['gpu_names'])}")
    print(
        f"{'shape':<28} {'T(s)':>8} {'g':>7} {'proc m/s':>9} {'CPU ceil':>9} "
        f"{'GPU slots':>10} {'GPU ceil':>9} {'binding':<9} {'predict':>8} {'$/M':>7}"
    )
    gpus = float(host["gpus"])
    for run in artifact["runs"]:
        segment = run["segment"]
        capacity = run["capacity"]
        slots = capacity["gpu_process_capacity"]
        gpu_ceiling = capacity["gpu_ceiling_matches_s_1214"]
        g_text = (
            f"{segment['forward_fraction']:.4f}" if gpus > 0.0 and segment["forward_fraction"] is not None else "n/a"
        )
        slots_text = f"{slots:.2f}" if slots is not None else "-"
        gpu_ceiling_text = f"{gpu_ceiling:.3f}" if gpu_ceiling is not None else "-"
        print(
            f"{run['label']:<28} {segment['decision_s']:>8.4f} {g_text:>7} "
            f"{capacity['per_process_matches_s_1214']:>9.3f} "
            f"{capacity['cpu_ceiling_matches_s_1214']:>9.3f} "
            f"{slots_text:>10} {gpu_ceiling_text:>9} "
            f"{capacity['binding']:<9} {capacity['predicted_matches_s_1214']:>8.3f} "
            f"{_format_cost(capacity['cost']):>7}"
        )
    for run in artifact["runs"]:
        for warning in run["segment"].get("warnings") or []:
            print(f"warning ({run['label']}): {warning}")
    primary = artifact["primary"]["capacity"]
    equation = primary["equation"]
    print()
    print(f"verdict: {primary['binding'].upper()}-bound — {primary['why']}")
    print(f"adjust:  {primary['adjust']}")
    if equation["g"] is None:
        print(
            f"equation: {equation['matches_per_decision']:.4f} x N_proc({equation['N_proc']:g}) / "
            f"{equation['T_s']:.4f} = {equation['matches_per_s_1214']:.3f} matches/s "
            "(1,214 convention; no GPU term)"
        )
    else:
        print(
            f"equation: {equation['matches_per_decision']:.4f} x min({equation['N_proc']:g}, "
            f"{equation['G']:g} x {equation['E']:g} / {float(equation['g']):.4f}) / {equation['T_s']:.4f} "
            f"= {equation['matches_per_s_1214']:.3f} matches/s (1,214 convention)"
        )
    print(
        f"physical: {primary['predicted_matches_s_2428']:.3f} matches/s (2,428 convention, "
        "the producer artifact's own matches_per_s)"
    )
    if primary["cost"]:
        cost = primary["cost"]
        print(
            f"cost: ${cost['price_per_hour']:.2f}/h -> "
            f"${cost['usd_per_million_matches_1214']:.1f}/1M matches (1,214) or "
            f"${cost['usd_per_million_matches_2428']:.1f}/1M (2,428)"
        )
    sweep = artifact.get("sweep") or []
    if sweep:
        print()
        print(
            f"{'procs':>5} {'T mean':>7} {'proc m/s':>9} {'aggregate':>10} {'retention':>9} {'fwd x':>7} {'cpu x':>7}"
        )
        for row in sweep:
            if not row.get("measured"):
                print(f"{row['processes']:>5}   failed")
                continue
            print(
                f"{row['processes']:>5} {row['decision_s_mean']:>7.4f} "
                f"{row['per_process_matches_s_1214_mean']:>9.3f} "
                f"{row['aggregate_matches_s_1214']:>10.3f} "
                f"{(row['retention_vs_ideal_scaling'] or 0.0):>9.3f} "
                f"{(row['forward_slowdown_vs_baseline'] or 0.0):>7.2f} "
                f"{(row['cpu_slowdown_vs_baseline'] or 0.0):>7.2f}"
            )
        verdict = artifact.get("sweep_verdict") or {}
        if verdict.get("measured"):
            predicted = verdict.get("predicted_gpu_capacity_processes")
            predicted_text = f"{float(predicted):.2f}" if predicted is not None else "no GPU term"
            ratio = verdict.get("measured_vs_gpu_ceiling")
            ratio_text = f"{float(ratio):.2f}x" if ratio is not None else "n/a"
            confirms = verdict.get("confirms_equation")
            confirms_text = "n/a" if confirms is None else str(confirms)
            print(
                f"sweep verdict: saturated at {verdict['saturated_at_processes']} processes; "
                f"predicted capacity {predicted_text}; measured {ratio_text} the GPU ceiling; "
                f"bound by {verdict.get('bound_by') or 'n/a'}; confirms equation: {confirms_text}"
            )
            price = artifact.get("settings", {}).get("price_per_hour")
            measured_aggregate = verdict.get("measured_aggregate_at_largest_matches_s")
            levels = [int(row["processes"]) for row in (artifact.get("sweep") or []) if row.get("measured")]
            largest = max(levels) if levels else 0
            if price and measured_aggregate:
                print(
                    f"measured cost at {largest} producers: "
                    f"${float(price) / (float(measured_aggregate) * 3600.0) * 1e6:.1f}/1M (1,214) or "
                    f"${float(price) / (float(measured_aggregate) / 2.0 * 3600.0) * 1e6:.1f}/1M (2,428)"
                )


def _load_entry(path: Path) -> dict[str, Any]:
    entry = json.loads(path.read_text(encoding="utf-8"))
    if entry.get("schema") != SCHEMA:
        raise SystemExit(f"{path} is {entry.get('schema')!r}, not {SCHEMA!r}")
    return entry


def _compare(entries: list[dict[str, Any]]) -> None:
    print(
        f"{'provider':<22} {'procs':>5} {'GPUs':>5} {'shape':<28} {'T(s)':>7} "
        f"{'g':>6} {'slots':>6} {'binding':<9} {'pred m/s':>8} {'pred $/M':>8} "
        f"{'meas m/s':>8} {'meas $/M':>8}"
    )
    for entry in entries:
        host = entry["host"]
        primary = entry["primary"]
        capacity = primary["capacity"]
        segment = primary["segment"]
        slots = capacity["gpu_process_capacity"]
        verdict = entry.get("sweep_verdict") or {}
        measured = verdict.get("measured_aggregate_at_largest_matches_s")
        price = entry.get("settings", {}).get("price_per_hour")
        measured_cost = None
        if price is not None and measured:
            measured_cost = {"usd_per_million_matches_1214": float(price) / (float(measured) * 3600.0) * 1e6}
        print(
            f"{entry['provider']!s:<22} {host['processes']:>5g} {host['gpus']:>5g} "
            f"{primary['label']:<28} {segment['decision_s']:>7.4f} "
            f"{(segment['forward_fraction'] if segment['forward_fraction'] is not None else 0.0):>6.3f} "
            f"{(slots if slots is not None else 0.0):>6.2f} {capacity['binding']:<9} "
            f"{capacity['predicted_matches_s_1214']:>8.3f} {_format_cost(capacity['cost']):>8} "
            f"{(f'{float(measured):.3f}' if measured else '-'):>8} {_format_cost(measured_cost):>8}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run", action="store_true", help="run the producer on this host")
    source.add_argument(
        "--artifact",
        type=Path,
        action="append",
        help="score an existing producer artifact (needs --cpus and --gpus); repeatable",
    )
    source.add_argument("--compare", type=Path, nargs="+", help="compare saved cloud-capacity artifacts")

    parser.add_argument("--provider", default=None, help="provider/instance label for this report")
    parser.add_argument("--lanes", type=int, nargs="+", default=[BASELINE_LANES], help="lane counts to measure")
    parser.add_argument("--segments", type=int, default=3, help="segments per lane count")
    parser.add_argument("--decisions-per-segment", type=int, default=32)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--device", default=None, help="cpu or cuda; default: cuda when available")
    parser.add_argument("--checkpoint", type=Path, default=Path(".resource/RoyaleFish/models/B-general-v275.pt"))
    parser.add_argument("--cpus", type=float, default=None, help="N_proc: usable vCPUs (default: affinity count)")
    parser.add_argument("--gpus", type=float, default=None, help="G: GPUs, fractional for a partition")
    parser.add_argument(
        "--gpu-efficiency", type=float, default=1.0, help="E: fraction of each GPU this workload may use"
    )
    parser.add_argument("--price-per-hour", type=float, default=None)
    parser.add_argument("--omp-threads", type=int, default=1, help="OMP_NUM_THREADS for producer runs")
    parser.add_argument("--validate", action="store_true", help="keep per-decision record validation")
    parser.add_argument("--no-warmup-forward", action="store_true", help="reproduce the old cold timings")
    parser.add_argument(
        "--fast-forward",
        action="store_true",
        help=(
            "run the producer's in-process forward port (fp16 autocast, one graph per row count, "
            "state table, padded staging); score the forwarded packet instead of the eager one"
        ),
    )
    parser.add_argument(
        "--inference-precision",
        choices=("auto", "fp32", "fp16", "bf16"),
        default=None,
        help="--fast-forward precision (default: the producer's 'auto')",
    )
    parser.add_argument(
        "--no-cuda-graphs",
        action="store_true",
        help="with --fast-forward: isolate the precision gain from the graph gain",
    )
    parser.add_argument(
        "--full-cuda-graph",
        action="store_true",
        help=(
            "with --fast-forward: capture the sampled decode in the same graph (the A6000's "
            "fastest forward; the producer refuses it if the random probe does not advance)"
        ),
    )
    parser.add_argument("--timeout", type=float, default=1800.0, help="per-producer timeout in seconds")
    parser.add_argument("--sweep-procs", type=int, nargs="+", default=None, help="concurrent process counts to test")
    parser.add_argument("--sweep-lanes", type=int, default=None, help="lanes for the process sweep (default: primary)")
    parser.add_argument("--sweep-segments", type=int, default=2)
    parser.add_argument("--sweep-decisions", type=int, default=32)
    parser.add_argument("--primary-lanes", type=int, default=None, help="shape whose verdict is primary")
    parser.add_argument("--run-dir", type=Path, default=Path("runs/crforge-c4/cloud"))
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c4/cloud-capacity.json"))
    args = parser.parse_args(argv)

    if args.compare:
        _compare([_load_entry(path) for path in args.compare])
        return 0

    if args.artifact and (args.cpus is None or args.gpus is None):
        raise SystemExit(
            "a recorded producer artifact does not carry the host's vCPU or GPU count; "
            "pass --cpus and --gpus for the host it ran on"
        )
    if args.sweep_procs and not args.run:
        raise SystemExit("--sweep-procs needs --run")
    if any(lanes < 1 for lanes in args.lanes):
        raise SystemExit("--lanes must be positive")
    if args.no_warmup_forward and args.fast_forward:
        raise SystemExit("--fast-forward needs the warmup that keeps the first decision out of the rate")
    if args.no_cuda_graphs and not args.fast_forward:
        raise SystemExit("--no-cuda-graphs is a --fast-forward setting")
    if args.full_cuda_graph and not args.fast_forward:
        raise SystemExit("--full-cuda-graph is a --fast-forward setting")
    if args.full_cuda_graph and args.no_cuda_graphs:
        raise SystemExit("--full-cuda-graph is the captured path; drop --no-cuda-graphs")
    if args.inference_precision is not None and not args.fast_forward:
        raise SystemExit("--inference-precision is a --fast-forward setting")
    if args.gpu_efficiency <= 0.0 or args.gpu_efficiency > 1.0:
        raise SystemExit("--gpu-efficiency must be in (0, 1]")
    if args.omp_threads < 1:
        raise SystemExit("--omp-threads must be positive")

    if args.device is None:
        try:
            import torch

            args.device = "cuda" if torch.cuda.is_available() else "cpu"
        except (ImportError, OSError, RuntimeError):
            args.device = "cpu"

    host = host_inventory(processes=args.cpus, gpus=args.gpus)
    provider = args.provider or host["hostname"]
    os.environ["OMP_NUM_THREADS"] = str(args.omp_threads)
    args.run_dir.mkdir(parents=True, exist_ok=True)

    runs: list[dict[str, Any]] = []
    sweep: list[dict[str, Any]] = []
    sweep_summary: dict[str, Any] | None = None
    if args.artifact:
        for path in args.artifact:
            artifact = json.loads(path.read_text(encoding="utf-8"))
            row = segment_row(artifact, name=path.name)
            runs.append(
                {
                    "label": path.stem,
                    "artifact_path": str(path),
                    "segment": row,
                    "capacity": capacity_report(
                        host, row, gpu_efficiency=args.gpu_efficiency, price_per_hour=args.price_per_hour
                    ),
                }
            )
    else:
        for lanes in args.lanes:
            row = run_shape(args, lanes=lanes, tag=f"lanes-{lanes}")
            runs.append(
                {
                    "label": f"{lanes}x2@{args.decisions_per_segment}",
                    "artifact_path": row.get("artifact_path"),
                    "segment": row,
                    "capacity": capacity_report(
                        host, row, gpu_efficiency=args.gpu_efficiency, price_per_hour=args.price_per_hour
                    ),
                }
            )
        primary_lanes = args.primary_lanes or max(args.lanes)
        primary_run = next(run for run in runs if int(run["segment"]["lanes"]) == primary_lanes)
        if args.sweep_procs:
            sweep_lanes = args.sweep_lanes or int(primary_run["segment"]["lanes"])
            sweep = run_sweep(
                args, host=host, lanes=sweep_lanes, levels=args.sweep_procs, baseline=primary_run["segment"]
            )
            sweep_summary = sweep_verdict(
                sweep,
                gpu_slots=primary_run["capacity"]["gpu_process_capacity"],
                gpu_ceiling_matches_s=primary_run["capacity"]["gpu_ceiling_matches_s_1214"],
            )

    primary_lanes = args.primary_lanes or max(int(run["segment"]["lanes"]) for run in runs)
    primary_run = next(run for run in runs if int(run["segment"]["lanes"]) == primary_lanes)
    measured_lanes = sorted({int(run["segment"]["lanes"]) for run in runs})
    artifact = {
        "schema": SCHEMA,
        "measured": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": provider,
        "host": host,
        "settings": {
            "mode": "artifact" if args.artifact else "run",
            "lanes": measured_lanes,
            "segments": args.segments if args.run else None,
            "decisions_per_segment": primary_run["segment"]["decisions"],
            "seed": args.seed if args.run else None,
            "device": (
                args.device
                if args.run
                else next((run["segment"].get("device") for run in runs if run["segment"].get("device")), None)
            ),
            "gpu_efficiency": args.gpu_efficiency,
            "price_per_hour": args.price_per_hour,
            "omp_threads": args.omp_threads if args.run else None,
            "validated_every_decision": (
                args.validate if args.run else primary_run["segment"].get("validated_every_decision")
            ),
            "warmup_forward": (
                not args.no_warmup_forward if args.run else primary_run["segment"].get("warmup_forward")
            ),
            "fast_forward": args.fast_forward if args.run else None,
            "full_cuda_graph": bool(getattr(args, "full_cuda_graph", False)) if args.run else None,
        },
        "runs": runs,
        "primary": {
            "label": primary_run["label"],
            "lane_count": primary_run["segment"]["lanes"],
            "segment": primary_run["segment"],
            "capacity": primary_run["capacity"],
        },
        "sweep": sweep,
        "sweep_verdict": sweep_summary,
        "notes": [
            (
                "matches/s is the 1,214-lane-decision convention used by the equation and "
                "the crforge plan; the 2,428 convention (one seat per match) is half and is "
                "what the producer artifact's own matches_per_s field uses"
            ),
            (
                "N_proc defaults to the process's sched_getaffinity count, which can exceed a "
                "container's vCPU quota; pass --cpus with the rented vCPU count"
            ),
            (
                "g is the median inference_ms over the decision wall, so a cold first decision "
                "is not counted as GPU demand; the producer is warmed before segment 0 by default"
            ),
            (
                "the ceilings assume each process keeps its measured single-process T at N_proc; "
                "--sweep-procs is the check, and a retention below 0.8 at the predicted capacity "
                "means the host has a second limit"
            ),
            (
                "this models collection throughput; VRAM for the PPO update is a separate gate - "
                "the A6000 OOMs on a 2,048-frame update (§3.62), so a card with less memory may "
                "not train even when its collection capacity is high"
            ),
        ],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    _print_report(artifact)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
