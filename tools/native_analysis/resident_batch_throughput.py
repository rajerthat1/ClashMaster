#!/usr/bin/env python3
"""Measure resident batch throughput on one or more connected probe engines.

The single-connection control API pays one TCP/adb round trip per command
(~19 ms measured, FINDINGS §3.18). The resident batch channel (`batch-v1`)
amortizes that: one framed request advances every resident slot a fixed number
of ticks, which is how the training harness is meant to run.

This tool configures N resident slots per engine, opens one batch channel per
engine, and steps them for a fixed number of batches. With several `--ports` it
runs the lanes concurrently behind a start barrier and reports the aggregate
matches/s, which is the number the training-host decision rule uses.

It deliberately issues no policy work: this is the transport + engine ceiling.
A batch step carries the full ordinary/atomic capture per slot, so real
training adds observation decode and policy latency on top.

Forward the control port(s) first::

    adb forward tcp:26789 tcp:26789
    adb forward tcp:26790 tcp:26790
    .venv/bin/python tools/native_analysis/resident_batch_throughput.py \
        --slots 8 --batches 20 --ticks 100 \
        --output /tmp/opencode/resident_batch_8.json
    .venv/bin/python tools/native_analysis/resident_batch_throughput.py \
        --ports 26789,26790 --slots 8 --batches 5 --ticks 600 --actions \
        --output /tmp/opencode/multi_engine_2x8.json

Each probe control server is single-client: the batch channel owns its lane
until it is closed. Use one port per engine process.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from native_runner.cr_native_env import NativeClashEnv, ResidentNativeClashEnv, RunnerError  # noqa: E402
from native_runner.match_factory import MatchConfig  # noqa: E402
from native_runner.resident_batch_channel import (  # noqa: E402
    BatchPlayActionV1,
    ResidentBatchChannelV1,
)

# One configured regulation match without actions ends at this tick.
MATCH_TICKS = 6068


def measure_lane(
    host: str,
    port: int,
    slots_count: int,
    batches: int,
    ticks: int,
    timeout: float,
    actions: bool,
    action_every: int,
    training_capture: bool,
    barrier: threading.Barrier | None,
) -> dict[str, Any]:
    """Configure one engine's resident slots and step them, returning a report."""
    control = NativeClashEnv(host, port, timeout=timeout)
    try:
        control.stop_resident_mode()
    except RunnerError:
        pass

    report: dict[str, Any] = {
        "port": port,
        "slots": slots_count,
        "batches": batches,
        "ticks": ticks,
    }
    slots = [ResidentNativeClashEnv(env_id, host, port, timeout=timeout) for env_id in range(slots_count)]
    payload = MatchConfig().to_json()
    configure_started = time.monotonic()
    try:
        for slot in slots:
            receipt = slot.configure_replay(payload)
            if not receipt.get("ok"):
                raise RunnerError(f"resident slot {slot.env_id} did not configure")
        report["configureSeconds"] = round(time.monotonic() - configure_started, 3)
        status = control._request("multi-status")
        report["clientVersion"] = control.status().get("clientVersion")
        if int(status.get("occupied", 0)) != slots_count:
            raise RunnerError(f"expected {slots_count} resident slots, probe reports {status.get('occupied')}")

        channel = ResidentBatchChannelV1(host, port, timeout=timeout, training_capture=training_capture)
        batch_seconds: list[float] = []
        captures: dict[int, Any] = {}
        total_actions = 0
        action_batches = 0
        sample_receipts: list[dict[str, Any]] = []
        if barrier is not None:
            # Start every engine's clock together so the aggregate window is fair.
            try:
                barrier.wait(timeout=min(timeout, 60.0))
            except threading.BrokenBarrierError as error:
                raise RunnerError("another engine lane failed before the start barrier") from error
        lane_started = time.monotonic()
        try:
            for index in range(batches):
                actions_by_env: dict[int, tuple[BatchPlayActionV1, ...]] = {
                    slot.env_id: () for slot in slots
                }
                if actions and index % action_every == 0:
                    for slot in slots:
                        capture = captures.get(slot.env_id)
                        if not capture or not isinstance(capture, dict):
                            continue
                        ordinary = capture.get("ordinary", capture)
                        if not isinstance(ordinary, dict):
                            continue
                        env_actions: list[BatchPlayActionV1] = []
                        for player in ordinary.get("players", []):
                            elixir = float(player.get("elixir", 0.0))
                            for card in player.get("hand", []):
                                if float(card.get("cost", 0.0)) <= elixir:
                                    env_actions.append(
                                        BatchPlayActionV1(
                                            owner=int(player["owner"]),
                                            hand_index=int(card["handIndex"]),
                                            x=9000,
                                            y=10000,
                                        )
                                    )
                                    break
                        actions_by_env[slot.env_id] = tuple(env_actions)
                    action_batches += 1
                started = time.monotonic()
                result = channel.step_partitioned(actions_by_env, ticks=ticks)
                batch_seconds.append(time.monotonic() - started)
                failures = [outcome for outcome in result.values() if type(outcome).__name__ == "BatchStepFailureV1"]
                if failures:
                    raise RunnerError(f"batch {index} reported {len(failures)} slot failures")
                for env_id, outcome in result.items():
                    if type(outcome).__name__ == "BatchStepResultV1":
                        total_actions += len(outcome.receipts)
                        captures[env_id] = outcome.capture
                        if len(sample_receipts) < 6:
                            sample_receipts.extend(
                                {
                                    "env": env_id,
                                    "owner": receipt.owner,
                                    "cardId": receipt.card_id,
                                    "cost": receipt.cost,
                                    "executeTick": receipt.execute_tick,
                                }
                                for receipt in outcome.receipts
                            )
            report["profile"] = channel.profile()
        finally:
            lane_ended = time.monotonic()
            channel.close()
        report["laneSeconds"] = round(lane_ended - lane_started, 4)
        # Let the single-client server observe the batch-session EOF before
        # issuing ordinary control commands again.
        time.sleep(0.25)
        report["residentStatus"] = control._request("multi-status")
    except RunnerError as error:
        report["error"] = str(error)
        return report
    finally:
        for slot in slots:
            try:
                slot.close()
            except RunnerError:
                pass
        try:
            control.stop_resident_mode()
        except RunnerError:
            pass

    total_seconds = sum(batch_seconds)
    slot_ticks = slots_count * ticks * batches
    ticks_per_second = slot_ticks / total_seconds if total_seconds else 0.0
    report["ok"] = True
    report.update(
        {
            "batchSeconds": [round(value, 4) for value in batch_seconds],
            "totalBatchSeconds": round(total_seconds, 3),
            "medianBatchSeconds": round(sorted(batch_seconds)[len(batch_seconds) // 2], 4),
            "engineTicksPerSecond": round(ticks_per_second, 1),
            "slotMatchesPerSecond": round(ticks_per_second / MATCH_TICKS, 3),
            "actionsEnabled": actions,
            "actionEveryBatches": action_every,
            "actionBatches": action_batches,
            "totalActions": total_actions,
            "actionsPerSecond": round(total_actions / total_seconds, 3) if total_seconds else 0.0,
            "sampleReceipts": sample_receipts,
        }
    )
    return report


def print_lane(report: dict[str, Any]) -> None:
    label = f"port {report['port']}"
    print(
        f"{label}: {report['slots']} slots x {report['ticks']} ticks x {report['batches']} batches "
        f"in {report['totalBatchSeconds']:.3f} s (median {report['medianBatchSeconds']:.4f} s/batch)\n"
        f"{report['engineTicksPerSecond']:,.0f} slot-ticks/s = {report['slotMatchesPerSecond']} matches/s"
    )
    if report.get("actionsEnabled"):
        print(
            f"actions: {report['totalActions']} receipts over {report['actionBatches']} action rounds "
            f"= {report['actionsPerSecond']} actions/s"
        )
    profile = report.get("profile", {})
    if profile:
        print(
            "channel profile: "
            f"send {profile.get('send_ns', 0) / 1e6:.2f} ms, "
            f"ack {profile.get('request_ack_ns', 0) / 1e6:.2f} ms, "
            f"receive {profile.get('receive_ns', 0) / 1e6:.2f} ms, "
            f"decode {profile.get('decode_ns', 0) / 1e6:.2f} ms"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=26789)
    parser.add_argument(
        "--ports",
        help="comma-separated control ports for concurrent engine lanes (overrides --port)",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--slots", type=int, default=8, help="resident slots per engine (1..16)")
    parser.add_argument("--batches", type=int, default=20)
    parser.add_argument("--ticks", type=int, default=100, help="ticks advanced per batch")
    parser.add_argument("--training-capture", action="store_true", help="use the slower attested capture envelope")
    parser.add_argument(
        "--actions",
        action="store_true",
        help="play one affordable card per owner every --action-every batches (real action receipts)",
    )
    parser.add_argument(
        "--action-every",
        type=int,
        default=3,
        help="batches between action rounds (default 3 = ~15 s of game time at 100 ticks/batch)",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.ports:
        try:
            ports = [int(value) for value in args.ports.split(",") if value.strip()]
        except ValueError:
            print("error: --ports must be comma-separated integers", file=sys.stderr)
            return 2
        if not ports:
            print("error: --ports is empty", file=sys.stderr)
            return 2
    else:
        ports = [args.port]
    if len(set(ports)) != len(ports):
        print("error: --ports contains duplicates", file=sys.stderr)
        return 2
    if not 1 <= args.slots <= 16:
        print("error: slots must be in 1..16", file=sys.stderr)
        return 2
    if args.batches < 1 or args.ticks < 1:
        print("error: batches and ticks must be positive", file=sys.stderr)
        return 2
    if args.action_every < 1:
        print("error: --action-every must be positive", file=sys.stderr)
        return 2
    if args.ticks * args.batches > MATCH_TICKS:
        print(
            f"error: {args.ticks * args.batches} ticks per slot exceeds the {MATCH_TICKS}-tick match; "
            "reduce --batches or --ticks",
            file=sys.stderr,
        )
        return 2

    barrier = threading.Barrier(len(ports)) if len(ports) > 1 else None

    def run(port: int) -> dict[str, Any]:
        try:
            return measure_lane(
                args.host,
                port,
                args.slots,
                args.batches,
                args.ticks,
                args.timeout,
                args.actions,
                args.action_every,
                args.training_capture,
                barrier,
            )
        except Exception as error:  # keep one broken lane from aborting the aggregate
            return {"port": port, "ok": False, "error": f"{type(error).__name__}: {error}"}

    started = time.monotonic()
    if len(ports) == 1:
        lanes = [run(ports[0])]
    else:
        with ThreadPoolExecutor(max_workers=len(ports)) as executor:
            lanes = list(executor.map(run, ports))
    wall_seconds = time.monotonic() - started

    failures = [lane for lane in lanes if not lane.get("ok")]
    for lane in lanes:
        if lane.get("ok"):
            print_lane(lane)
        else:
            print(f"port {lane['port']}: error: {lane.get('error')}", file=sys.stderr)

    report: dict[str, Any] = {
        "engineCount": len(ports),
        "ports": ports,
        "slots": args.slots,
        "batches": args.batches,
        "ticks": args.ticks,
        "wallSeconds": round(wall_seconds, 3),
        "lanes": lanes,
    }
    if not failures:
        # Sum the lane windows rather than the whole configure wall time: the
        # aggregate is the parallel stepping window, and the barrier aligns it.
        window_seconds = max(lane["laneSeconds"] for lane in lanes)
        total_slot_ticks = sum(lane["slots"] * args.ticks * args.batches for lane in lanes)
        total_actions = sum(lane["totalActions"] for lane in lanes)
        ticks_per_second = total_slot_ticks / window_seconds if window_seconds else 0.0
        report.update(
            {
                "aggregateWindowSeconds": round(window_seconds, 4),
                "aggregateSlotTicksPerSecond": round(ticks_per_second, 1),
                "aggregateSlotMatchesPerSecond": round(ticks_per_second / MATCH_TICKS, 3),
                "totalActions": total_actions,
                "aggregateActionsPerSecond": round(total_actions / window_seconds, 3) if window_seconds else 0.0,
                "battleCapacity": len(ports) * args.slots,
            }
        )
        print(
            f"aggregate: {len(ports)} engines x {args.slots} slots in {window_seconds:.3f} s window "
            f"= {ticks_per_second:,.0f} slot-ticks/s = "
            f"{report['aggregateSlotMatchesPerSecond']} matches/s over {report['battleCapacity']} lanes"
        )
        if args.actions:
            print(f"aggregate actions: {report['aggregateActionsPerSecond']} actions/s")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"report: {args.output}")
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
