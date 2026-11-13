#!/usr/bin/env python3
"""Measure native headless match throughput on a connected probe.

Phase 0 asks for aggregate matches/s and decisions/s on the target hardware.
This tool runs N sequential fresh matches against one probe process in one of
three modes and reports wall-clock throughput:

``raw``
    one ``step`` call per match: the engine-bound ceiling.
``batch``
    repeated ``step`` calls of ``--batch-ticks``: the cost of control round
    trips without policy work.
``decision``
    the two-sided decision loop from :mod:`multi_decision_match` (observe,
    play one affordable card per owner, advance one command boundary): the
    shape a training harness actually runs.

One connection drives one match, so a single-slot number. Resident slots share
the same process and multiply throughput only as far as device CPU allows;
measure that with the multi-slot probe, not by extrapolating this number.

Forward the control port first::

    adb forward tcp:26789 tcp:26789
    .venv/bin/python tools/native_analysis/native_match_throughput.py \
        --matches 5 --mode raw --output /tmp/opencode/throughput_raw.json
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from multi_decision_match import choose_actions, validate_decision  # noqa: E402
from native_runner.cr_native_env import NativeClashEnv, RunnerError  # noqa: E402
from native_runner.match_factory import MatchConfig  # noqa: E402


def run_one(env: NativeClashEnv, mode: str, batch_ticks: int, advance: int) -> dict[str, Any]:
    configure_started = time.monotonic()
    env.create_match(MatchConfig())
    configure_seconds = time.monotonic() - configure_started

    started = time.monotonic()
    decisions = 0
    actions = 0
    problems: list[str] = []
    step_calls = 0
    if mode == "decision":
        while True:
            before = env.observe()
            if before.get("ended"):
                break
            chosen = choose_actions(before, (0, 1), 9000, 10000)
            env.transition(chosen, advance_ticks=advance)
            after = env.observe()
            decisions += 1
            actions += len(chosen)
            problems.extend(validate_decision(before, after, chosen, int(after["tick"]) - int(before["tick"])))
            if decisions >= 1000:
                break
    else:
        while True:
            result = env.step(batch_ticks if mode == "batch" else 1_000_000)
            step_calls += 1
            if result.get("ended"):
                break
            if step_calls >= 100000:
                raise RunnerError("match did not end")
    elapsed = time.monotonic() - started
    final = env.observe()
    return {
        "configureSeconds": round(configure_seconds, 3),
        "seconds": round(elapsed, 3),
        "finalTick": final.get("tick"),
        "ended": final.get("ended"),
        "crowns": final.get("crownsRaw"),
        "decisions": decisions,
        "actions": actions,
        "stepCalls": step_calls,
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=26789)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--matches", type=int, default=5)
    parser.add_argument("--mode", choices=("raw", "batch", "decision"), default="raw")
    parser.add_argument("--batch-ticks", type=int, default=20)
    parser.add_argument("--advance", type=int, default=22)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    env = NativeClashEnv(args.host, args.port, timeout=args.timeout)
    status = env.status()
    if not status.get("coldReady"):
        print("error: native content runtime is not cold-ready on the device", file=sys.stderr)
        return 2
    print(f"client {status.get('clientVersion')} mode {status.get('mode')} ({args.mode}, {args.matches} matches)")

    matches: list[dict[str, Any]] = []
    try:
        for index in range(args.matches):
            record = run_one(env, args.mode, args.batch_ticks, args.advance)
            record["match"] = index
            matches.append(record)
            print(
                f"match {index}: {record['seconds']:7.3f} s  tick {record['finalTick']}  "
                f"crowns {record['crowns']}  decisions {record['decisions']}  "
                f"actions {record['actions']}  problems {len(record['problems'])}"
            )
    except RunnerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    total_seconds = sum(record["seconds"] for record in matches)
    total_actions = sum(record["actions"] for record in matches)
    total_decisions = sum(record["decisions"] for record in matches)
    summary = {
        "clientVersion": status.get("clientVersion"),
        "mode": args.mode,
        "matches": len(matches),
        "totalSeconds": round(total_seconds, 3),
        "secondsPerMatch": round(total_seconds / len(matches), 3),
        "matchesPerSecond": round(len(matches) / total_seconds, 3) if total_seconds else None,
        "decisionsPerSecond": round(total_decisions / total_seconds, 3) if total_seconds else None,
        "actionsPerSecond": round(total_actions / total_seconds, 3) if total_seconds else None,
        "problems": [problem for record in matches for problem in record["problems"]],
        "runs": matches,
    }
    print(
        f"\n{summary['matches']} matches in {summary['totalSeconds']} s: "
        f"{summary['secondsPerMatch']} s/match, {summary['matchesPerSecond']} matches/s, "
        f"{summary['decisionsPerSecond']} decisions/s, {summary['actionsPerSecond']} actions/s"
    )
    if summary["problems"]:
        for problem in summary["problems"]:
            print(f"  PROBLEM {problem}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=1), encoding="utf-8")
        print(f"report: {args.output}")
    return 0 if not summary["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
