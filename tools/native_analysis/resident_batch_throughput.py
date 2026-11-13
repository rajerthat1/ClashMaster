#!/usr/bin/env python3
"""Measure resident batch throughput on a connected probe.

The single-connection control API pays one TCP/adb round trip per command
(~19 ms measured, FINDINGS §3.18). The resident batch channel (`batch-v1`)
amortizes that: one framed request advances every resident slot a fixed number
of ticks, which is how the training harness is meant to run.

This tool configures N resident slots, opens one batch channel, and steps them
for a fixed number of batches. It reports engine ticks/s, per-slot matches/s,
and the channel's own send/ack/receive profile.

It deliberately issues no policy work: this is the transport + engine ceiling.
A batch step carries the full ordinary/atomic capture per slot, so real
training adds observation decode and policy latency on top.

Forward the control port first::

    adb forward tcp:26789 tcp:26789
    .venv/bin/python tools/native_analysis/resident_batch_throughput.py \
        --slots 8 --batches 20 --ticks 100 \
        --output /tmp/opencode/resident_batch_8.json

The probe control server is single-client: the batch channel owns the lane
until it is closed. Run only one instance of this tool at a time and do not
issue ordinary control commands while it runs.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from native_runner.cr_native_env import NativeClashEnv, ResidentNativeClashEnv, RunnerError  # noqa: E402
from native_runner.match_factory import MatchConfig  # noqa: E402
from native_runner.resident_batch_channel import ResidentBatchChannelV1  # noqa: E402

# One configured regulation match without actions ends at this tick.
MATCH_TICKS = 6068


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=26789)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--slots", type=int, default=8, help="resident slots to configure (1..16)")
    parser.add_argument("--batches", type=int, default=20)
    parser.add_argument("--ticks", type=int, default=100, help="ticks advanced per batch")
    parser.add_argument("--training-capture", action="store_true", help="use the slower attested capture envelope")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if not 1 <= args.slots <= 16:
        print("error: slots must be in 1..16", file=sys.stderr)
        return 2
    if args.batches < 1 or args.ticks < 1:
        print("error: batches and ticks must be positive", file=sys.stderr)
        return 2
    if args.ticks * args.batches > MATCH_TICKS:
        print(
            f"error: {args.ticks * args.batches} ticks per slot exceeds the {MATCH_TICKS}-tick match; "
            "reduce --batches or --ticks",
            file=sys.stderr,
        )
        return 2

    control = NativeClashEnv(args.host, args.port, timeout=args.timeout)
    try:
        control.stop_resident_mode()
    except RunnerError:
        pass

    report: dict[str, Any] = {"slots": args.slots, "batches": args.batches, "ticks": args.ticks}
    slots = [ResidentNativeClashEnv(env_id, args.host, args.port, timeout=args.timeout) for env_id in range(args.slots)]
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
        if int(status.get("occupied", 0)) != args.slots:
            raise RunnerError(f"expected {args.slots} resident slots, probe reports {status.get('occupied')}")

        channel = ResidentBatchChannelV1(
            args.host, args.port, timeout=args.timeout, training_capture=args.training_capture
        )
        batch_seconds: list[float] = []
        try:
            for index in range(args.batches):
                started = time.monotonic()
                result = channel.step_partitioned({slot.env_id: () for slot in slots}, ticks=args.ticks)
                batch_seconds.append(time.monotonic() - started)
                failures = [outcome for outcome in result.values() if type(outcome).__name__ == "BatchStepFailureV1"]
                if failures:
                    raise RunnerError(f"batch {index} reported {len(failures)} slot failures")
            report["profile"] = channel.profile()
        finally:
            channel.close()
        # Let the single-client server observe the batch-session EOF before
        # issuing ordinary control commands again.
        time.sleep(0.25)
        report["residentStatus"] = control._request("multi-status")
    except RunnerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
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
    slot_ticks = args.slots * args.ticks * args.batches
    ticks_per_second = slot_ticks / total_seconds if total_seconds else 0.0
    report.update(
        {
            "batchSeconds": [round(value, 4) for value in batch_seconds],
            "totalBatchSeconds": round(total_seconds, 3),
            "medianBatchSeconds": round(sorted(batch_seconds)[len(batch_seconds) // 2], 4),
            "engineTicksPerSecond": round(ticks_per_second, 1),
            "slotMatchesPerSecond": round(ticks_per_second / MATCH_TICKS, 3),
        }
    )
    print(
        f"{args.slots} slots x {args.ticks} ticks x {args.batches} batches in {total_seconds:.3f} s "
        f"(median {report['medianBatchSeconds']:.4f} s/batch)\n"
        f"{ticks_per_second:,.0f} slot-ticks/s = {report['slotMatchesPerSecond']} matches/s"
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
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
