#!/usr/bin/env python3
"""Profile the V4 PPO collection loop with a scripted WAIT policy, without a GPU.

The GPU trainer's collection loop is CUDA-only, so its per-packet cost cannot be
profiled locally.  This harness speaks the exact ``remote_ppo_worker_server``
protocol the trainer speaks, but answers every frame with an all-WAIT packed
action batch: no observations are decoded, no model runs, no tensors are
stored.  Whatever remains is the transport + worker + engine cost, i.e. the
floor the real loop sits on.

    # one local x86_64 engine on 26789 (tools/native_analysis/crx_engine_host.py)
    .venv/bin/python tools/native_analysis/profile_cluster_collection.py \
        --deck-pool runs/deck-pool-a51.json --lanes 8 --steps 160

    # emulate the A51 shard: 4 engines on 26789..26792
    ... --engines 4 --engine-base-port 26789

Reported per engine: packets/s (one packet = one decision step for every lane),
lane-decisions/s, and the split between waiting for a packet, decoding it, and
answering.  A packet that costs ~80 ms means the loop is engine-bound; anything
much larger is trainer-side.
"""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_futures
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time

import torch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from native_runner.training.v4.async_cluster_self_play import (  # noqa: E402
    AsyncResidentEngineSpecV4,
)
from native_runner.training.v4.cluster_self_play import ClusterMatchAssignmentV4  # noqa: E402
from native_runner.training.v4.deck_pool import DeckPoolManifestV4  # noqa: E402
from native_runner.training.v4.matchmaking import (  # noqa: E402
    MATCH_TAIL_TAIL,
    POLICY_CURRENT,
    ScheduledMatchupV4,
)
from native_runner.training.v4.ppo_runtime import ppo_environment_config_v4  # noqa: E402
from native_runner.training.v4.remote_worker_transport import RemoteWorkerConnection  # noqa: E402
from native_runner.training.v4.tensors import GATE_WAIT  # noqa: E402


def _wait_matrix(rows: int, *, micro_actions: int = 2) -> torch.Tensor:
    """A packed all-WAIT action batch: gate WAIT, no micro-actions."""

    width = 2 + 4 * micro_actions
    packed = torch.zeros((rows, width), dtype=torch.long)
    packed[:, 0] = GATE_WAIT
    packed[:, 1] = 0
    packed[:, 2:] = -1
    packed[:, 2 + 3 * micro_actions :] = 0
    return packed


def _assignments(
    deck_pool: Path,
    *,
    engine_index: int,
    lanes: int,
    battle_level: int,
    base_sequence: int,
    deck_offset: int = 0,
    deck_source: str = "pool",
    card_keys: str | None = None,
) -> tuple[ClusterMatchAssignmentV4, ...]:
    manifest = DeckPoolManifestV4.load(deck_pool)
    decks = manifest.decks
    if card_keys:
        from native_runner.training.v4.deck_pool import deck_identity_from_keys

        pair = tuple(part.strip() for part in card_keys.split(",") if part.strip())
        fixed = deck_identity_from_keys(pair, "tower-princess", uses=1)
        return tuple(
            ClusterMatchAssignmentV4(
                matchup=ScheduledMatchupV4(
                    sequence=base_sequence + engine_index * lanes + lane,
                    category=MATCH_TAIL_TAIL,
                    policy_matchup=POLICY_CURRENT,
                    deck0=fixed,
                    deck1=fixed,
                    current_owners=(0, 1),
                    battle_level=battle_level,
                    seed=1_000_003 + lane,
                ),
                opponent=None,
            )
            for lane in range(lanes)
        )
    if deck_source == "focus":
        focus = manifest.focus_decks
        if len(focus) < 2:
            raise ValueError("deck pool has no focus decks")
        decks = focus
    if len(decks) < 2:
        raise ValueError("deck pool needs at least two decks")
    rows: list[ClusterMatchAssignmentV4] = []
    for lane in range(lanes):
        index = (deck_offset + engine_index * lanes + lane) % (len(decks) - 1)
        matchup = ScheduledMatchupV4(
            sequence=base_sequence + engine_index * lanes + lane,
            category=MATCH_TAIL_TAIL,
            policy_matchup=POLICY_CURRENT,
            deck0=decks[index],
            deck1=decks[index + 1],
            current_owners=(0, 1),
            battle_level=battle_level,
            seed=1_000_003 + lane,
        )
        rows.append(ClusterMatchAssignmentV4(matchup=matchup, opponent=None))
    return tuple(rows)


def _start_worker_server(args: argparse.Namespace, log_path: Path) -> subprocess.Popen[str]:
    command = [
        sys.executable,
        "-m",
        "native_runner.training.v4.remote_ppo_worker_server",
        "--bind-host",
        "127.0.0.1",
        "--port",
        str(args.worker_port),
        "--global-start",
        "0",
        "--engine-count",
        str(args.engines),
        "--engine-base-port",
        str(args.engine_base_port),
        # Keep every worker on its own CPU so the measurement matches the
        # production topology on a small host.
        "--worker-affinity-width",
        str(args.affinity_width),
    ]
    if args.allow_cpu_sharing:
        command.append("--allow-worker-cpu-sharing")
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, text=True)
    deadline = time.monotonic() + args.startup_timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"worker server exited early; see {log_path}")
        if "remote_ppo_worker_server_ready" in log_path.read_text(encoding="utf-8", errors="replace"):
            return process
        time.sleep(0.2)
    process.terminate()
    raise RuntimeError(f"worker server did not become ready; see {log_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deck-pool", type=Path, default=REPO_ROOT / "runs" / "deck-pool-a51.json")
    parser.add_argument("--engines", type=int, default=1)
    parser.add_argument("--engine-base-port", type=int, default=26_789)
    parser.add_argument("--lanes", type=int, default=8)
    parser.add_argument("--steps", type=int, default=160, help="decision steps per lane, mirrors segment seconds * 4")
    parser.add_argument("--battle-level", type=int, default=11)
    parser.add_argument("--deck-offset", type=int, default=0)
    parser.add_argument("--deck-source", choices=("pool", "focus"), default="pool")
    parser.add_argument("--card-keys", help="8 comma-separated card keys; pins both decks")
    parser.add_argument("--worker-port", type=int, default=29_950)
    parser.add_argument("--affinity-width", type=int, choices=(1, 2, 4), default=1)
    parser.add_argument("--allow-cpu-sharing", action="store_true")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--model-ms", type=float, default=0.0, help="artificial per-batch model cost")
    parser.add_argument(
        "--diagnostic-timing",
        action="store_true",
        help="ask each worker to report per-round decode/step/tensorize/channel time",
    )
    parser.add_argument("--no-server", action="store_true", help="reuse an already running worker server")
    parser.add_argument("--log", type=Path, default=Path("/tmp/opencode/profile-collection-worker.log"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    environment = ppo_environment_config_v4()
    max_decision_steps = math.ceil(environment.max_battle_ticks / 5) + 4
    specs = tuple(
        AsyncResidentEngineSpecV4(
            engine_index=index,
            cpu=index,
            host="127.0.0.1",
            base_port=args.engine_base_port,
            lanes=args.lanes,
            timeout=args.timeout,
            capture_mode="compact",
            worker_host="127.0.0.1",
            worker_port=args.worker_port,
            segment_decision_steps=args.steps * 4,
            diagnostic_timing=args.diagnostic_timing,
        )
        for index in range(args.engines)
    )

    server: subprocess.Popen[str] | None = None
    if not args.no_server:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        server = _start_worker_server(args, args.log)
        print(json.dumps({"event": "worker_server_ready", "port": args.worker_port}), flush=True)

    parents: dict[int, RemoteWorkerConnection] = {}
    try:
        for spec in specs:
            connection = RemoteWorkerConnection.connect("127.0.0.1", args.worker_port, timeout=args.startup_timeout)
            connection.send(
                {
                    "kind": "start",
                    "spec": spec,
                    "assignments": _assignments(
                        args.deck_pool,
                        engine_index=spec.engine_index,
                        lanes=args.lanes,
                        battle_level=args.battle_level,
                        base_sequence=1,
                        deck_offset=args.deck_offset,
                        deck_source=args.deck_source,
                        card_keys=args.card_keys,
                    ),
                    "gamma_per_decision": 0.9997,
                    "shaping_beta": 0.05,
                    "fireball_king_activation_penalty": 0.0,
                    "hog_deploy_reward": 0.0,
                    "hog_deploy_reward_episode_cap": 0.1,
                    "hog_deploy_opening_reward": None,
                    "first_hog_timing_reward_max": 0.0,
                    "first_hog_timing_start_seconds": 10.0,
                    "first_hog_timing_deadline_seconds": 30.0,
                    "first_hog_missed_deadline_penalty": 0.0,
                    "first_hog_deferral_penalty": 0.0,
                    "first_hog_deferral_penalty_episode_cap": 0.0,
                    "mutual_elixir_overflow_penalty": 0.0,
                    "mutual_elixir_overflow_grace": 0.0,
                    "unilateral_elixir_overflow_penalty": 0.0,
                    "elixir_overflow_step_penalty_cap": 0.1,
                    "policy_state_id": "profile-scripted-wait",
                    "validate_tensors": False,
                    "max_decision_steps": max_decision_steps,
                }
            )
            parents[spec.engine_index] = connection

        executor = ThreadPoolExecutor(max_workers=len(specs), thread_name_prefix="profile-receive")
        futures = {}

        def receive(connection: RemoteWorkerConnection) -> tuple[RemoteWorkerConnection, object, float]:
            started = time.perf_counter()
            return connection, connection.recv(), time.perf_counter() - started

        for connection in parents.values():
            futures[executor.submit(receive, connection)] = connection

        target_packets = args.steps
        packets_by_engine = {spec.engine_index: 0 for spec in specs}
        wait_seconds = 0.0
        receive_seconds = 0.0
        answer_seconds = 0.0
        worker_timing: dict[str, float] = {}
        channel_timing: dict[str, float] = {}
        packet_intervals: list[float] = []
        started = time.perf_counter()
        last_packet_at: float | None = None
        total_packets = 0

        while futures and any(count < target_packets for count in packets_by_engine.values()):
            wait_started = time.perf_counter()
            ready, _ = wait_futures(tuple(futures), return_when=FIRST_COMPLETED)
            wait_seconds += time.perf_counter() - wait_started
            now = time.perf_counter()
            if last_packet_at is not None:
                packet_intervals.append(now - last_packet_at)
            last_packet_at = now
            for future in ready:
                connection, packet, duration = future.result()
                engine_index = next(index for index, value in parents.items() if value is connection)
                del futures[future]
                receive_seconds += duration
                if not isinstance(packet, dict):
                    raise RuntimeError("worker packet changed type")
                kind = packet.get("kind")
                if kind == "error":
                    raise RuntimeError(f"worker error: {packet.get('error')}")
                if kind != "frame":
                    raise RuntimeError(f"unexpected worker packet kind: {kind!r}")
                actor_rows = tuple(int(row) for row in packet["actor_rows"])
                for name, value in (packet.get("worker_round_timing") or {}).items():
                    worker_timing[name] = worker_timing.get(name, 0.0) + float(value)
                for name, value in (packet.get("native_channel_timing") or {}).items():
                    channel_timing[name] = channel_timing.get(name, 0.0) + float(value)
                answer_started = time.perf_counter()
                if args.model_ms > 0.0:
                    time.sleep(args.model_ms / 1000.0)
                connection.send(
                    {
                        "kind": "act_packed",
                        "actor_rows": actor_rows,
                        "packed_actions": _wait_matrix(len(actor_rows)),
                        "forced_act_actor_rows": (),
                    }
                )
                answer_seconds += time.perf_counter() - answer_started
                packets_by_engine[engine_index] += 1
                total_packets += 1
                if packets_by_engine[engine_index] < target_packets:
                    futures[executor.submit(receive, connection)] = connection
        elapsed = time.perf_counter() - started
        for connection in parents.values():
            try:
                connection.send({"kind": "stop"})
            except OSError:
                pass
        executor.shutdown(wait=False, cancel_futures=True)
    finally:
        for connection in parents.values():
            connection.close()
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()

    lane_decisions = total_packets * args.lanes
    summary = {
        "engines": args.engines,
        "lanes": args.lanes,
        "steps": args.steps,
        "packets": total_packets,
        "elapsed_seconds": elapsed,
        "packets_per_second": total_packets / elapsed,
        "packets_per_second_per_engine": total_packets / elapsed / args.engines,
        "lane_decisions_per_second": lane_decisions / elapsed,
        "matches_per_second": (lane_decisions / elapsed) / (3600 / 5),
        "wait_seconds": wait_seconds,
        "receive_seconds": receive_seconds,
        "answer_seconds": answer_seconds,
        "model_ms": args.model_ms,
        "packet_interval_median_ms": (statistics.median(packet_intervals) * 1000.0) if packet_intervals else 0.0,
        "packet_interval_p90_ms": (
            statistics.quantiles(packet_intervals, n=10)[8] * 1000.0 if len(packet_intervals) > 9 else 0.0
        ),
        "worker_mean_ms": {
            name: (1000.0 * value / max(total_packets, 1)) for name, value in sorted(worker_timing.items())
        },
        "channel_mean_ms": {
            name: (1000.0 * value / max(total_packets, 1)) for name, value in sorted(channel_timing.items())
        },
    }
    print(json.dumps(summary, sort_keys=True))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
