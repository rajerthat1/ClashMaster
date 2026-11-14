#!/usr/bin/env python3
"""Phase 2 bottleneck profiler for the native Linux runtime.

Measures where the training loop's wall time goes, in three separable phases:

``transport``
    Drive native ticks with no policy work (empty joint actions) and record the
    client RPC split (serialize/send/receive/parse) plus the worker-side
    ``timing_v1`` split (request parse, joint action, step JNI, step JSON parse,
    observe JNI, observe JSON parse, pre-response).
``loop``
    Run full native self-play matches with the sandbox recurrent policy on N
    workers, and attribute the Python thread to mask probes, encoding, mask
    build, inference, reward and trajectory bookkeeping.
``model``
    Microbenchmark the sandbox recurrent policy and the R0 elite-v3 forward
    pass, so model cost can be separated from observation and transport cost.

Workers are started from the rootless install unless ``--skip-start`` is set.
Evidence is written as JSON under ``builds/native/phase2-<utc>/``.

Examples::

    # 8 workers, start/stop them around the run
    tools/native_bringup/profile_native_loop.py --workers 8

    # attach to workers that are already listening
    tools/native_bringup/profile_native_loop.py --ports 39031-39034 --skip-start

    # only re-run the transport phase, keep workers up
    tools/native_bringup/profile_native_loop.py --ports 39031-39034 \
        --skip-start --phases transport --keep-workers
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping, Sequence

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SANDBOX = PROJECT_ROOT / ".resource" / "cr-native-sandbox"
DEFAULT_SYSTEM_ROOT = PROJECT_ROOT / "builds" / "rootless" / "system-root"
DEFAULT_WORKER_SCRIPT = (
    PROJECT_ROOT / "tools" / "native_bringup" / "run_rootless_worker.sh"
)
DEFAULT_REPLAY = SANDBOX / "examples" / "xbow-3.0.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "builds" / "native"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from native_core.client import JsonLineClient  # noqa: E402
from native_core.env import NativeRoyaleEnv  # noqa: E402
from training.deck_spec import XBOW_DECK  # noqa: E402
from training.model import RecurrentPolicyValueNet  # noqa: E402
from training.vector_rollout import VectorNativeSelfPlayCollector  # noqa: E402

NATIVE_TIMING_KEYS = (
    "request_parse_ns",
    "joint_action_ns",
    "step_jni_ns",
    "step_json_parse_ns",
    "observe_jni_ns",
    "observe_json_parse_ns",
    "transition_pre_response_ns",
    "response_json_serialize_probe_ns",
)
CLIENT_TIMING_KEYS = (
    "rpc_serialize_seconds",
    "rpc_send_seconds",
    "rpc_receive_seconds",
    "rpc_parse_seconds",
    "rpc_total_seconds",
)
LOOP_STAGE_KEYS = (
    "mask_probe_seconds",
    "encoding_seconds",
    "mask_build_seconds",
    "transition_seconds",
    "reward_seconds",
    "trajectory_seconds",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_ports(value: str) -> list[int]:
    ports: list[int] = []
    seen: set[int] = set()
    for token in value.replace("\n", ",").split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            values: Sequence[int] = range(int(left), int(right) + 1)
        else:
            values = (int(token),)
        for port in values:
            if not 0 < port < 65536:
                raise ValueError(f"invalid port: {port}")
            if port in seen:
                raise ValueError(f"duplicate port: {port}")
            seen.add(port)
            ports.append(port)
    if not ports:
        raise ValueError("at least one port is required")
    return ports


def listener_pid(port: int) -> int | None:
    try:
        output = subprocess.check_output(
            ["ss", "-ltnp", f"sport = :{port}"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.search(r"pid=(\d+)", output)
    return int(match.group(1)) if match else None


def process_cpu_seconds(pid: int | None) -> float | None:
    """Return cumulative utime+stime for a host PID, in seconds."""
    if pid is None:
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = raw[raw.rfind(")") + 2:].split()
    # After the comm field, field 14 (utime) is index 11 and 15 (stime) index 12.
    try:
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    except (IndexError, ValueError):
        return None


def read_cpu_times(pids: Sequence[int]) -> dict[str, float]:
    values: dict[str, float] = {}
    for pid in pids:
        seconds = process_cpu_seconds(pid)
        if seconds is not None:
            values[str(pid)] = seconds
    return values


def cpu_delta(
    before: Mapping[str, float], after: Mapping[str, float]
) -> dict[str, Any]:
    deltas = {
        pid: after[pid] - before.get(pid, after[pid])
        for pid in after
        if pid in before
    }
    return {
        "cpu_seconds_total": sum(deltas.values()),
        "cpu_seconds_by_pid": deltas,
    }


def _summary(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    return {
        "count": float(len(ordered)),
        "mean": float(statistics.fmean(ordered)),
        "p50": float(ordered[len(ordered) // 2]),
        "p95": float(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]),
        "max": float(ordered[-1]),
    }


def instrument_ops(env: NativeRoyaleEnv) -> dict[str, dict[str, float]]:
    """Count and time every RPC op on one env, by op name."""
    stats: dict[str, dict[str, float]] = defaultdict(
        lambda: {"calls": 0.0, "seconds": 0.0}
    )
    original: Callable[[dict[str, Any]], dict[str, Any]] = env.client.request

    def wrapped(payload: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            return original(payload)
        finally:
            op = str(payload.get("op", "unknown"))
            stats[op]["calls"] += 1.0
            stats[op]["seconds"] += time.perf_counter() - started

    env.client.request = wrapped  # type: ignore[method-assign]
    return stats


def absorb_native_timing(
    transition: Mapping[str, Any], target: dict[str, float]
) -> None:
    timing = transition.get("timing_v1")
    if not isinstance(timing, Mapping):
        return
    for key, raw in timing.items():
        if isinstance(raw, int) and not isinstance(raw, bool):
            target[str(key)] = target.get(str(key), 0.0) + float(raw)


def seeded_replay(replay: Mapping[str, Any], seed: int) -> dict[str, Any]:
    value = json.loads(json.dumps(replay))
    value["rndSeed"] = seed
    return value


def wait_for_ports(ports: Sequence[int], *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    pending = set(ports)
    while pending and time.monotonic() < deadline:
        pending = {port for port in pending if listener_pid(port) is None}
        if pending:
            time.sleep(0.5)
    if pending:
        raise RuntimeError(f"workers did not start listening: {sorted(pending)}")


def ensure_slots(workers: int) -> None:
    missing = [
        slot for slot in range(workers)
        if not (
            DEFAULT_SYSTEM_ROOT / "data" / "local" / "tmp"
            / f"cr-native-direct-{slot}"
        ).is_dir()
    ]
    if not missing:
        return
    bundle = (
        PROJECT_ROOT / "builds" / "bundles"
        / "cr-native-linux-bionic-runtime-150535029-local-overlay"
    )
    manifest = bundle / "BUNDLE-MANIFEST.json"
    if not bundle.is_dir() or not manifest.is_file():
        raise RuntimeError(
            f"worker slots {missing} are missing and the local overlay bundle "
            f"was not found at {bundle}"
        )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(
        PROJECT_ROOT / ".resource" / "cr-native-linux-bionic"
    )
    command = [
        sys.executable, "-m", "cr_native_bionic", "install",
        "--bundle-dir", str(bundle),
        "--manifest", str(manifest),
        "--system-root", str(DEFAULT_SYSTEM_ROOT),
        "--runtime-root", str(PROJECT_ROOT / "builds" / "rootless" / "runtime"),
        "--workers", str(workers),
        "--apply",
    ]
    subprocess.run(command, check=True, env=environment)


def start_workers(ports: Sequence[int]) -> list[subprocess.Popen[bytes]]:
    processes: list[subprocess.Popen[bytes]] = []
    for index, port in enumerate(ports):
        environment = dict(os.environ)
        environment.update({
            "SLOT": str(index),
            "PORT": str(port),
            "SYSTEM_ROOT": str(DEFAULT_SYSTEM_ROOT),
            "LOG": f"/tmp/opencode/rootless-worker-{index}.log",
        })
        processes.append(subprocess.Popen(
            [str(DEFAULT_WORKER_SCRIPT)],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ))
    wait_for_ports(ports, timeout=120.0)
    return processes


def stop_workers(processes: Sequence[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass
    for process in processes:
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Phase: transport (native tick + RPC + JSON, no policy)
# ---------------------------------------------------------------------------

def transport_episode(
    port: int,
    replay: Mapping[str, Any],
    *,
    seed: int,
    warmup_seconds: float,
    measure_seconds: float,
    timeout: float,
    transition_mode: str,
    profile_native: bool,
    steps: int,
) -> dict[str, Any]:
    env = NativeRoyaleEnv(
        port=port,
        timeout=timeout,
        profile_native=profile_native,
        transition_mode=transition_mode,
    )
    ops = instrument_ops(env)
    try:
        state = env.reset(seeded_replay(replay, seed), warmup_steps=100)
        started = time.perf_counter()
        transitions = 0
        ticks = 0
        native_timing: dict[str, float] = {}
        latencies: list[float] = []
        while time.perf_counter() - started < warmup_seconds:
            transition = env.joint_training_transition([], steps=steps)
            if transition["step"]["episode"].get("terminated") or \
                    transition["step"]["episode"].get("truncated"):
                state = env.reset(seeded_replay(replay, seed + 1), warmup_steps=100)
            else:
                state = transition["state"]
        env.reset_rpc_profile()
        for value in ops.values():
            value["calls"] = 0.0
            value["seconds"] = 0.0
        started = time.perf_counter()
        while time.perf_counter() - started < measure_seconds:
            previous_tick = int(state["tick"])
            transition_started = time.perf_counter()
            try:
                transition = env.joint_training_transition([], steps=steps)
            except Exception as error:  # noqa: BLE001 - reported in evidence
                return {
                    "port": port,
                    "status": "failed",
                    "error": f"{type(error).__name__}: {error}",
                }
            latencies.append(time.perf_counter() - transition_started)
            transitions += 1
            absorb_native_timing(transition, native_timing)
            episode = transition["step"]["episode"]
            if episode.get("terminated") or episode.get("truncated"):
                current_tick = int(episode.get("terminal_tick", previous_tick))
                state = env.reset(seeded_replay(replay, seed + 1), warmup_steps=100)
            else:
                state = transition["state"]
                current_tick = int(state["tick"])
            ticks += max(0, current_tick - previous_tick)
        wall = time.perf_counter() - started
        profile = dict(env.rpc_profile)
        samples = env.rpc_latency_samples()
        return {
            "port": port,
            "status": "complete",
            "transition_mode": transition_mode,
            "profile_native": profile_native,
            "wall_seconds": wall,
            "transitions": transitions,
            "ticks": ticks,
            "ticks_per_second": ticks / wall if wall else 0.0,
            "client_timing": {
                key: profile.get(key, 0.0) for key in CLIENT_TIMING_KEYS
            },
            "native_timing_seconds": {
                key: native_timing.get(key, 0.0) / 1e9
                for key in NATIVE_TIMING_KEYS
            },
            "rpc_latency_ms": JsonLineClient.latency_summary(
                samples.get("total", []),
                samples.get("receive", []),
                attempts=profile.get("rpc_attempts", 0.0),
                failures=profile.get("rpc_failures", 0.0),
            ),
            "request_bytes": profile.get("rpc_request_bytes", 0.0),
            "response_bytes": profile.get("rpc_response_bytes", 0.0),
            "ops": dict(ops),
            "ticks_advanced": ticks,
        }
    finally:
        env.close()


def run_transport(
    ports: Sequence[int],
    replay: Mapping[str, Any],
    *,
    seed: int,
    warmup_seconds: float,
    measure_seconds: float,
    timeout: float,
    workers: int,
    transition_mode: str,
    profile_native: bool,
    steps: int,
    match_ticks: float,
) -> dict[str, Any]:
    pids = [pid for pid in (listener_pid(port) for port in ports[:workers]) if pid]
    before = read_cpu_times(pids)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        episodes = list(executor.map(
            lambda item: transport_episode(
                item[1], replay,
                seed=seed + item[0],
                warmup_seconds=warmup_seconds,
                measure_seconds=measure_seconds,
                timeout=timeout,
                transition_mode=transition_mode,
                profile_native=profile_native,
                steps=steps,
            ),
            enumerate(ports[:workers]),
        ))
    wall = time.perf_counter() - started
    cpu = cpu_delta(before, read_cpu_times(pids))
    complete = [row for row in episodes if row.get("status") == "complete"]
    total_transitions = sum(row["transitions"] for row in complete)
    total_ticks = sum(row["ticks"] for row in complete)
    result: dict[str, Any] = {
        "workers": workers,
        "transition_mode": transition_mode,
        "profile_native": profile_native,
        "steps_per_transition": steps,
        "wall_seconds": wall,
        "host_cores": os.cpu_count(),
        "cpu_seconds_total": cpu["cpu_seconds_total"],
        "cpu_core_fraction": (
            cpu["cpu_seconds_total"] / (wall * (os.cpu_count() or 1))
            if wall else 0.0
        ),
        "episodes": episodes,
        "transitions": total_transitions,
        "aggregate_ticks_per_second": total_ticks / wall if wall else 0.0,
        "match_ticks_for_equivalence": match_ticks,
        "equivalent_matches_per_second": (
            (total_ticks / wall) / match_ticks
            if wall and match_ticks else 0.0
        ),
        "per_worker_p50_transition_ms": float(statistics.median(
            row["rpc_latency_ms"]["rpc_latency_p50_ms"] for row in complete
        )) if complete else 0.0,
    }
    result["aggregate"] = _aggregate_transport(complete)
    return result


def _aggregate_transport(episodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not episodes:
        return {}
    transitions = sum(row["transitions"] for row in episodes)
    client = {
        key: sum(row["client_timing"].get(key, 0.0) for row in episodes)
        for key in CLIENT_TIMING_KEYS
    }
    native = {
        key: sum(row["native_timing_seconds"].get(key, 0.0) for row in episodes)
        for key in NATIVE_TIMING_KEYS
    }
    response_bytes = sum(row["response_bytes"] for row in episodes)
    request_bytes = sum(row["request_bytes"] for row in episodes)
    # Client-side per-transition means, in milliseconds.
    client_ms = {
        key.replace("_seconds", ""): 1000.0 * value / transitions
        for key, value in client.items()
    }
    native_ms = {key.replace("_ns", ""): 1000.0 * value / transitions
                 for key, value in native.items()}
    addressed = (
        client["rpc_serialize_seconds"] + client["rpc_send_seconds"]
        + client["rpc_receive_seconds"] + client["rpc_parse_seconds"]
    )
    residual_ms = 1000.0 * (client["rpc_total_seconds"] - addressed) / transitions
    return {
        "transitions": transitions,
        "client_ms_per_transition": client_ms,
        "native_ms_per_transition": native_ms,
        "client_residual_ms_per_transition": residual_ms,
        "request_bytes_per_transition": request_bytes / transitions,
        "response_bytes_per_transition": response_bytes / transitions,
        "ops": _aggregate_ops(episodes),
    }


def _aggregate_ops(
    episodes: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {"calls": 0.0, "seconds": 0.0}
    )
    for row in episodes:
        for op, values in row.get("ops", {}).items():
            totals[op]["calls"] += values["calls"]
            totals[op]["seconds"] += values["seconds"]
    return {
        op: {
            "calls": values["calls"],
            "seconds": values["seconds"],
            "ms_per_call": (
                1000.0 * values["seconds"] / values["calls"]
                if values["calls"] else 0.0
            ),
        }
        for op, values in sorted(totals.items())
    }


# ---------------------------------------------------------------------------
# Phase: full self-play loop with the sandbox recurrent policy
# ---------------------------------------------------------------------------

def model_forward_benchmark(
    *,
    batch_size: int,
    iterations: int,
    torch_threads: int,
) -> dict[str, Any]:
    import torch

    torch.set_num_threads(torch_threads)
    torch.manual_seed(1701)
    policy = RecurrentPolicyValueNet().eval().cpu()
    grid = torch.zeros((batch_size, 10, 32, 18))
    scalars = torch.zeros((batch_size, 64))
    privileged = torch.zeros((batch_size, 33))
    card_mask = torch.ones((batch_size, 5), dtype=torch.bool)
    position_masks = torch.ones((batch_size, 4, 32 * 18), dtype=torch.bool)
    hidden = policy.initial_hidden(batch_size, device=torch.device("cpu"))
    timings: list[float] = []
    with torch.no_grad():
        for _ in range(max(1, iterations // 5)):
            policy.sample_batch(
                grid, scalars, privileged, card_mask, position_masks, hidden
            )
        for _ in range(iterations):
            started = time.perf_counter()
            policy.sample_batch(
                grid, scalars, privileged, card_mask, position_masks, hidden
            )
            timings.append(time.perf_counter() - started)
    return {
        "model": "sandbox-recurrent-v1",
        "batch_size": batch_size,
        "parameters": sum(p.numel() for p in policy.parameters()),
        "milliseconds": _summary([value * 1000.0 for value in timings]),
    }


def r0_forward_benchmark(
    *,
    batch_size: int,
    iterations: int,
    torch_threads: int,
) -> dict[str, Any]:
    import torch

    from training_r0.catalog import CardVocabulary
    from training_r0.config import ModelConfig
    from training_r0.model import R0Policy
    from training_r0.observation import ObservationBuilder
    from training_r0.synthetic import public_frame

    torch.set_num_threads(torch_threads)
    torch.manual_seed(1701)
    vocabulary = CardVocabulary.from_native()
    config = ModelConfig(allow_incomplete_capture=True)
    policy = R0Policy(vocabulary, config).eval().cpu()
    builder = ObservationBuilder(vocabulary, config)
    frames = [
        public_frame(builder, tick=100 + index, side=index % 2)
        for index in range(batch_size)
    ]
    batch = builder.batch(frames)
    state = policy.initial_state(batch_size)
    timings: list[float] = []
    with torch.no_grad():
        for _ in range(max(1, iterations // 5)):
            policy(batch, state)
        for _ in range(iterations):
            started = time.perf_counter()
            policy(batch, state)
            timings.append(time.perf_counter() - started)
    return {
        "model": "r0-elite-v3",
        "batch_size": batch_size,
        "parameters": sum(p.numel() for p in policy.parameters()),
        "synthetic_fixture": True,
        "milliseconds": _summary([value * 1000.0 for value in timings]),
    }


def run_model_benchmarks(
    *, batch_size: int, iterations: int, torch_threads: int
) -> dict[str, Any]:
    return {
        "batch_size": batch_size,
        "iterations": iterations,
        "torch_threads": torch_threads,
        "sandbox_recurrent": model_forward_benchmark(
            batch_size=batch_size,
            iterations=iterations,
            torch_threads=torch_threads,
        ),
        "r0_elite_v3": r0_forward_benchmark(
            batch_size=batch_size,
            iterations=iterations,
            torch_threads=torch_threads,
        ),
    }


def run_loop(
    ports: Sequence[int],
    replay: Mapping[str, Any],
    *,
    workers: int,
    seed: int,
    rounds: int,
    max_ticks: int,
    timeout: float,
    torch_threads: int,
    reward_mode: str,
    transition_mode: str,
    profile_native: bool,
    loop_steps: int,
) -> dict[str, Any]:
    import torch

    torch.set_num_threads(torch_threads)
    chosen = list(ports[:workers])
    pids = [pid for pid in (listener_pid(port) for port in chosen) if pid]
    envs = [
        NativeRoyaleEnv(
            port=port,
            timeout=timeout,
            profile_native=profile_native,
            transition_mode=transition_mode,
        )
        for port in chosen
    ]
    ops = [instrument_ops(env) for env in envs]
    policy = RecurrentPolicyValueNet().eval().cpu()
    collector = VectorNativeSelfPlayCollector(
        envs, policy, replay,
        device=torch.device("cpu"),
        reward_mode=reward_mode,
        max_ticks=max_ticks,
        deck=XBOW_DECK,
        decision_ticks=loop_steps,
    )
    before = read_cpu_times(pids)
    started = time.perf_counter()
    results = []
    round_walls: list[float] = []
    try:
        for round_index in range(rounds):
            seeds = [seed + round_index * workers + index
                     for index in range(workers)]
            round_started = time.perf_counter()
            results.extend(collector.collect(seeds))
            round_walls.append(time.perf_counter() - round_started)
    finally:
        for env in envs:
            env.close()
    wall = time.perf_counter() - started
    cpu = cpu_delta(before, read_cpu_times(pids))
    matches = len(results)
    ticks = sum(result.tick for result in results)
    profile: dict[str, float] = defaultdict(float)
    for result in results:
        for key, value in result.profile.items():
            profile[key] += float(value)
    stages = {
        key: profile.get(key, 0.0) / matches
        for key in LOOP_STAGE_KEYS
        if matches
    }
    native = {
        key.replace("host_", "").replace("_seconds", ""):
            profile.get(f"host_{key[:-3]}_seconds", 0.0) / matches
        for key in NATIVE_TIMING_KEYS
        if matches
    }
    rpc = {
        "calls_per_match": profile.get("rpc_calls", 0.0) / matches
        if matches else 0.0,
        **{
            key.replace("_seconds", "") + "_seconds_per_match":
                profile.get(key, 0.0) / matches
            for key in CLIENT_TIMING_KEYS
            if matches
        },
    }
    main_thread = sum(
        value for key, value in stages.items() if key != "transition_seconds"
    ) + profile.get("vector_inference_seconds", 0.0) / matches
    decision_ticks = sum(
        int(result.tick) for result in results
    ) / max(1, int(profile.get("decision_steps", 0.0)))
    round_wall_total = sum(round_walls)
    inference_total = profile.get("vector_inference_seconds", 0.0)
    transition_wall_total = profile.get("vector_transition_wall_seconds", 0.0)
    python_stage_total = sum(
        value
        for key, value in stages.items()
        if key != "transition_seconds"
    ) * matches
    round_attribution = {
        "round_wall_seconds": round_wall_total,
        "inference_seconds": inference_total,
        "transition_wall_seconds": transition_wall_total,
        "python_stage_seconds": max(0.0, python_stage_total),
        "inference_share": (
            inference_total / round_wall_total if round_wall_total else 0.0
        ),
        "transition_wait_share": (
            transition_wall_total / round_wall_total if round_wall_total else 0.0
        ),
        "python_stage_share": (
            max(0.0, python_stage_total) / round_wall_total
            if round_wall_total else 0.0
        ),
    }
    return {
        "workers": workers,
        "transition_mode": transition_mode,
        "profile_native": profile_native,
        "loop_steps": loop_steps,
        "matches": matches,
        "ticks": ticks,
        "rounds": rounds,
        "round_wall_seconds": round_walls,
        "wall_seconds": wall,
        "matches_per_second": matches / wall if wall else 0.0,
        "ticks_per_second": ticks / wall if wall else 0.0,
        "seconds_per_match": wall / matches if matches else 0.0,
        "ticks_per_match": ticks / matches if matches else 0.0,
        "decisions_per_match": profile.get("decision_steps", 0.0) / matches
        if matches else 0.0,
        "ticks_per_decision": decision_ticks,
        "cpu_seconds_total": cpu["cpu_seconds_total"],
        "cpu_core_fraction": (
            cpu["cpu_seconds_total"] / (wall * (os.cpu_count() or 1))
            if wall else 0.0
        ),
        "stages_seconds_per_match": stages,
        "main_thread_seconds_per_match": main_thread,
        "main_thread_share_of_wall": (
            main_thread * matches / wall if wall else 0.0
        ),
        "round_attribution": round_attribution,
        "stage_share_of_main_thread": {
            key: (value / main_thread if main_thread else 0.0)
            for key, value in stages.items()
            if key != "transition_seconds"
        },
        "vector_inference_seconds_per_match": (
            profile.get("vector_inference_seconds", 0.0) / matches
            if matches else 0.0
        ),
        "native_seconds_per_match": native,
        "rpc_per_match": rpc,
        "barrier": {
            key: value for key, value in profile.items()
            if key.startswith("worker_transition_") or key.startswith("vector_")
        },
        "ops": _aggregate_ops([
            {"ops": values} for values in ops
        ]),
        "episodes": [
            {
                "seed": result.seed,
                "tick": result.tick,
                "outcome": result.outcome,
                "actions": result.actions,
                "wall_seconds": result.wall_seconds,
            }
            for result in results
        ],
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(report: Mapping[str, Any]) -> None:
    print()
    print("Phase 2 native loop profile")
    print("=" * 78)
    for name in ("transport", "loop"):
        phase = report.get(name)
        if not isinstance(phase, Mapping):
            continue
        print()
        print(f"[{name}] workers={phase.get('workers')} "
              f"mode={phase.get('transition_mode')} "
              f"profiled={phase.get('profile_native')} "
              f"steps={phase.get('loop_steps', phase.get('steps_per_transition', 1))} "
              f"wall={phase.get('wall_seconds', 0.0):.2f}s")
        if name == "transport":
            print(f"  steps per RPC          : "
                  f"{phase.get('steps_per_transition', 1)}")
            print(f"  aggregate ticks/s      : "
                  f"{phase.get('aggregate_ticks_per_second', 0.0):.1f}")
            print(f"  aggregate RPC/s        : "
                  f"{phase.get('transitions', 0) / phase.get('wall_seconds', 1.0):.1f}")
            print(f"  equivalent matches/s   : "
                  f"{phase.get('equivalent_matches_per_second', 0.0):.2f} "
                  f"(at {phase.get('match_ticks_for_equivalence', 0):.0f} ticks/match)")
            aggregate = phase.get("aggregate", {})
            client = aggregate.get("client_ms_per_transition", {})
            native = aggregate.get("native_ms_per_transition", {})
            print("  client ms/transition   : "
                  + ", ".join(f"{k}={v:.3f}" for k, v in client.items()))
            nonzero_native = {k: v for k, v in native.items() if v > 0}
            if nonzero_native:
                print("  native ms/transition   : "
                      + ", ".join(
                          f"{k}={v:.3f}" for k, v in nonzero_native.items()
                      ))
            print(f"  response bytes/tick    : "
                  f"{aggregate.get('response_bytes_per_transition', 0.0):.0f}")
        else:
            print(f"  matches/s              : "
                  f"{phase.get('matches_per_second', 0.0):.3f}")
            print(f"  ticks/s                : "
                  f"{phase.get('ticks_per_second', 0.0):.1f}")
            print(f"  seconds/match          : "
                  f"{phase.get('seconds_per_match', 0.0):.2f}")
            print(f"  main-thread s/match    : "
                  f"{phase.get('main_thread_seconds_per_match', 0.0):.3f}")
            for key, value in phase.get("stages_seconds_per_match", {}).items():
                print(f"    {key:24s}: {value:.4f} s/match")
            print(f"    vector_inference        : "
                  f"{phase.get('vector_inference_seconds_per_match', 0.0):.4f}"
                  " s/match")
            nonzero_native = {
                k: v for k, v in
                phase.get("native_seconds_per_match", {}).items() if v > 0
            }
            if nonzero_native:
                print("  native s/match         : "
                      + ", ".join(
                          f"{k}={v:.3f}" for k, v in nonzero_native.items()
                      ))
            print(f"  worker CPU / host      : "
                  f"{phase.get('cpu_core_fraction', 0.0):.2f}")
            attribution = phase.get("round_attribution", {})
            if attribution:
                print("  round attribution      : "
                      f"inference={attribution.get('inference_share', 0.0):.1%}, "
                      f"transition_wait="
                      f"{attribution.get('transition_wait_share', 0.0):.1%}, "
                      f"python="
                      f"{attribution.get('python_stage_share', 0.0):.1%}")
    model = report.get("model")
    if isinstance(model, Mapping):
        print()
        print(f"[model] batch={model.get('batch_size')} "
              f"threads={model.get('torch_threads')}")
        for key in ("sandbox_recurrent", "r0_elite_v3"):
            row = model.get(key, {})
            milliseconds = row.get("milliseconds", {})
            print(f"  {row.get('model')}: p50={milliseconds.get('p50', 0.0):.2f} ms "
                  f"p95={milliseconds.get('p95', 0.0):.2f} ms")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ports", help="comma/range list, e.g. 39031-39034")
    source.add_argument("--workers", type=int,
                        help="start N rootless workers on 39031..39030+N")
    parser.add_argument("--skip-start", action="store_true",
                        help="attach to already-listening workers")
    parser.add_argument("--keep-workers", action="store_true",
                        help="leave started workers running after the run")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port-base", type=int, default=39031)
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--output-dir", type=Path,
                        default=DEFAULT_OUTPUT_ROOT / f"phase2-{utc_stamp()}")
    parser.add_argument(
        "--phases", default="transport,loop,model",
        help="comma list of transport,loop,model",
    )
    parser.add_argument("--transport-warmup-seconds", type=float, default=3.0)
    parser.add_argument("--transport-measure-seconds", type=float, default=20.0)
    parser.add_argument("--transport-steps", type=int, default=1,
                        help="native ticks per transport RPC (decision interval)")
    parser.add_argument("--match-ticks", type=float, default=3790.0,
                        help="ticks per match used only for matches/s equivalence")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--loop-steps", type=int, default=1,
                        help="native ticks advanced per policy decision")
    parser.add_argument("--max-ticks", type=int, default=7200)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--reward", default="terminal",
                        choices=("terminal", "potential", "tower_hp_potential_v1"))
    parser.add_argument("--torch-threads", type=int, default=2)
    parser.add_argument("--model-batch-size", type=int, default=8)
    parser.add_argument("--model-iterations", type=int, default=50)
    parser.add_argument("--transition-mode", default="legacy",
                        choices=("legacy", "fast-json"))
    parser.add_argument("--profile-native", action="store_true",
                        help="inline worker timing_v1 (legacy mode only; "
                             "adds one extra response serialization pass)")
    parser.add_argument("--label", default="",
                        help="suffix for the evidence file, e.g. fast-json")
    args = parser.parse_args(argv)

    phases = [value.strip() for value in args.phases.split(",") if value.strip()]
    unknown = set(phases) - {"transport", "loop", "model"}
    if unknown:
        raise ValueError(f"unknown phases: {sorted(unknown)}")

    if args.profile_native and args.transition_mode == "fast-json":
        raise ValueError(
            "fast-json does not support inline native profiling; "
            "run it without --profile-native"
        )
    evidence_name = (
        f"profile-{args.label}.json" if args.label else "profile.json"
    )
    replay = json.loads(args.replay.read_text(encoding="utf-8-sig"))
    if args.workers is not None:
        workers = args.workers
        ports = [args.port_base + index for index in range(workers)]
    else:
        ports = parse_ports(args.ports)
        workers = len(ports)
    if workers <= 0:
        raise ValueError("workers must be positive")

    processes: list[subprocess.Popen[bytes]] = []
    if not args.skip_start:
        ensure_slots(workers)
        processes = start_workers(ports)
    else:
        missing = [port for port in ports if listener_pid(port) is None]
        if missing:
            raise RuntimeError(f"no worker is listening on: {missing}")

    report: dict[str, Any] = {
        "schema": "native-phase2-profile-v1",
        "generated_utc": utc_now(),
        "status": "running",
        "config": {
            "ports": ports,
            "workers": workers,
            "phases": phases,
            "replay": str(args.replay.resolve()),
            "rounds": args.rounds,
            "max_ticks": args.max_ticks,
            "reward": args.reward,
            "torch_threads": args.torch_threads,
            "host_cores": os.cpu_count(),
            "transition_mode": args.transition_mode,
            "profile_native": args.profile_native,
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = args.output_dir / evidence_name
    atomic_json(evidence_path, report)
    try:
        if "transport" in phases:
            print("transport phase: native tick + RPC only", flush=True)
            report["transport"] = run_transport(
                ports, replay,
                seed=args.seed,
                warmup_seconds=args.transport_warmup_seconds,
                measure_seconds=args.transport_measure_seconds,
                timeout=args.timeout,
                workers=workers,
                transition_mode=args.transition_mode,
                profile_native=args.profile_native,
                steps=args.transport_steps,
                match_ticks=args.match_ticks,
            )
            atomic_json(evidence_path, report)
        if "loop" in phases:
            print("loop phase: full native self-play", flush=True)
            report["loop"] = run_loop(
                ports, replay,
                workers=workers,
                seed=args.seed,
                rounds=args.rounds,
                max_ticks=args.max_ticks,
                timeout=args.timeout,
                torch_threads=args.torch_threads,
                reward_mode=args.reward,
                transition_mode=args.transition_mode,
                profile_native=args.profile_native,
                loop_steps=args.loop_steps,
            )
            atomic_json(evidence_path, report)
        if "model" in phases:
            print("model phase: forward-pass microbenchmarks", flush=True)
            report["model"] = run_model_benchmarks(
                batch_size=args.model_batch_size,
                iterations=args.model_iterations,
                torch_threads=args.torch_threads,
            )
            atomic_json(evidence_path, report)
        report["status"] = "complete"
        report["finished_utc"] = utc_now()
    except Exception as error:  # noqa: BLE001 - always leave evidence
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        report["finished_utc"] = utc_now()
        atomic_json(evidence_path, report)
        raise
    finally:
        if processes and not args.keep_workers:
            stop_workers(processes)
        atomic_json(evidence_path, report)
    print_report(report)
    print(f"evidence: {evidence_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
