#!/usr/bin/env python3
"""Drive a two-sided decision loop against the native probe on a real device.

This is the Phase 0 multi-decision gate (HANDOFF "NEXT ACTION" item 1). It
configures one fresh :class:`MatchConfig` match, then repeatedly observes both
engine hands, plays one affordable card per owner, and advances across the
native command-consumption boundary. Every decision is validated against the
engine state:

* the played hand slot is replaced by the deck's next card,
* the owner's elixir drops by at least the card cost minus one tick of regen,
* no command is left queued and no step reports an error.

It is a diagnostic harness, not a training API. Forward the probe control port
first::

    adb forward tcp:26789 tcp:26789
    .venv/bin/python tools/native_analysis/multi_decision_match.py \
        --min-ticks 600 --output /tmp/opencode/multi_decision_match.json

Check the device crash buffer before and after the run::

    adb logcat -b crash -d
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

from native_runner.cr_native_env import HandAction, NativeClashEnv, RunnerError  # noqa: E402
from native_runner.match_factory import MatchConfig  # noqa: E402

# Elixir regenerates continuously; one decision advances 22 ticks (~1.1 s), so
# the post-play balance can only rise from the pre-play balance by the regen
# added over the advance. Measured on 16.635.1 at 20 Hz: single elixir
# ~0.018/tick (2.8 s per elixir) for ticks 0..2400, double elixir ~0.036/tick
# for the last minute, and overtime would be triple (~0.054/tick). Any net gain
# beyond this bound means the play was free.
MAX_REGEN_PER_TICK = 0.06
MAX_OVERPAY = 0.05


def observe_summary(observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "tick": observation.get("tick"),
        "count": observation.get("count"),
        "ended": observation.get("ended"),
        "crowns": observation.get("crownsRaw"),
        "players": [
            {
                "owner": player.get("owner"),
                "elixir": player.get("elixir"),
                "hand": [
                    {
                        "handIndex": card.get("handIndex"),
                        "deckSlot": card.get("deckSlot"),
                        "cardId": card.get("cardId"),
                        "cost": card.get("cost"),
                    }
                    for card in player.get("hand", [])
                ],
                "nextCard": player.get("nextCard"),
            }
            for player in observation.get("players", [])
        ],
    }


def parse_owners(text: str) -> tuple[int, ...]:
    owners = tuple(int(part) for part in text.split(",") if part.strip())
    if not owners or any(owner not in (0, 1) for owner in owners):
        raise argparse.ArgumentTypeError("owners must be a comma list drawn from 0,1")
    return owners


def choose_actions(before: dict[str, Any], owners: tuple[int, ...], x: int, y: int) -> list[HandAction]:
    """Pick the first affordable hand slot for each requested owner."""

    actions: list[HandAction] = []
    for player in before.get("players", []):
        owner = int(player["owner"])
        if owner not in owners:
            continue
        for card in player.get("hand", []):
            if float(player["elixir"]) >= float(card["cost"]):
                actions.append(HandAction(owner, int(card["handIndex"]), x, y))
                break
    return actions


def validate_decision(
    before: dict[str, Any],
    after: dict[str, Any],
    actions: list[HandAction],
    ticks: int,
) -> list[str]:
    """Return human-readable violations for one played decision."""

    problems: list[str] = []
    if int(after.get("queuedCommands", 0)) > 0:
        problems.append(f"queuedCommands={after.get('queuedCommands')} after the decision")
    for action in actions:
        before_player = next((p for p in before["players"] if int(p["owner"]) == action.owner), None)
        after_player = next((p for p in after["players"] if int(p["owner"]) == action.owner), None)
        if before_player is None or after_player is None:
            problems.append(f"owner {action.owner} missing from an observation")
            continue
        played = next(
            (card for card in before_player["hand"] if int(card["handIndex"]) == action.hand_index), None
        )
        if played is None:
            problems.append(f"owner {action.owner} hand slot {action.hand_index} was not observed")
            continue
        replacement = next(
            (card for card in after_player["hand"] if int(card["handIndex"]) == action.hand_index), None
        )
        if replacement is None:
            problems.append(f"owner {action.owner} lost hand slot {action.hand_index}")
        else:
            expected = before_player.get("nextCard") or {}
            if int(replacement["cardId"]) != int(expected.get("cardId", -1)):
                problems.append(
                    f"owner {action.owner} slot {action.hand_index} replaced by {replacement['cardId']}, "
                    f"expected next card {expected.get('cardId')}"
                )
        cost = float(played["cost"])
        delta = float(after_player["elixir"]) - float(before_player["elixir"])
        if delta > -cost + MAX_REGEN_PER_TICK * ticks:
            problems.append(
                f"owner {action.owner} elixir {before_player['elixir']} -> {after_player['elixir']} "
                f"did not pay {cost} within regen tolerance"
            )
        if delta < -cost - MAX_OVERPAY:
            problems.append(
                f"owner {action.owner} elixir {before_player['elixir']} -> {after_player['elixir']} "
                f"paid more than the {cost} card cost"
            )
        if float(after_player["elixir"]) < 0:
            problems.append(f"owner {action.owner} elixir went negative")
    return problems


def run(args: argparse.Namespace) -> dict[str, Any]:
    env = NativeClashEnv(args.host, args.port, timeout=args.timeout)
    status = env.status()
    report: dict[str, Any] = {
        "clientVersion": status.get("clientVersion"),
        "mode": status.get("mode"),
        "minTicks": args.min_ticks,
        "advanceTicks": args.advance,
        "owners": list(args.owners),
        "decisions": [],
        "problems": [],
    }
    if not status.get("coldReady"):
        raise RunnerError("native content runtime is not cold-ready on the device")

    started = time.monotonic()
    observation = env.create_match(MatchConfig())
    report["start"] = observe_summary(observation)
    report["configureSeconds"] = round(time.monotonic() - started, 3)

    total_actions = 0
    decisions_with_actions = 0
    while True:
        before = env.observe()
        if before.get("ended"):
            report["endedAtTick"] = before.get("tick")
            break
        actions = choose_actions(before, args.owners, args.x, args.y)
        decision_started = time.monotonic()
        result = env.transition(actions, advance_ticks=args.advance)
        after = env.observe()
        entry = {
            "decision": len(report["decisions"]),
            "before": observe_summary(before),
            "after": observe_summary(after),
            "actions": [
                {"owner": a.owner, "handIndex": a.hand_index, "x": a.x, "y": a.y} for a in actions
            ],
            "transitionOk": bool(result.get("ok")),
            "seconds": round(time.monotonic() - decision_started, 3),
        }
        problems = validate_decision(before, after, actions, int(after["tick"]) - int(before["tick"]))
        entry["problems"] = problems
        report["decisions"].append(entry)
        report["problems"].extend(f"decision {entry['decision']}: {problem}" for problem in problems)
        if actions:
            decisions_with_actions += 1
            total_actions += len(actions)
        print(
            f"decision {entry['decision']:3d} tick {before.get('tick'):5} -> {after.get('tick'):5} "
            f"count {before.get('count'):4} -> {after.get('count'):4} actions {len(actions)} "
            f"problems {len(problems)}"
        )
        if int(after.get("tick", 0)) >= args.min_ticks and entry["decision"] + 1 >= args.min_decisions:
            break
        if entry["decision"] + 1 >= args.max_decisions:
            break
        if not actions and args.stop_when_blocked:
            break

    report["seconds"] = round(time.monotonic() - started, 3)
    report["totalActions"] = total_actions
    report["decisionsWithActions"] = decisions_with_actions
    report["final"] = observe_summary(env.observe())
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=26789)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--min-ticks", type=int, default=600, help="keep deciding until the match reaches this tick")
    parser.add_argument("--min-decisions", type=int, default=1)
    parser.add_argument("--max-decisions", type=int, default=400)
    parser.add_argument("--advance", type=int, default=22, help="ticks advanced per decision")
    parser.add_argument("--owners", type=parse_owners, default=(0, 1))
    parser.add_argument("--x", type=int, default=9000, help="owner-local deploy x")
    parser.add_argument("--y", type=int, default=10000, help="owner-local deploy y")
    parser.add_argument("--stop-when-blocked", action="store_true", help="stop when no owner can afford a card")
    parser.add_argument("--output", type=Path, help="write the full JSON report here")
    args = parser.parse_args(argv)

    try:
        report = run(args)
    except RunnerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(
        f"\nclient {report['clientVersion']} mode {report['mode']}: "
        f"{len(report['decisions'])} decisions, {report['totalActions']} actions, "
        f"{report['seconds']} s, {len(report['problems'])} problems"
    )
    print(f"start tick {report['start']['tick']} -> final tick {report['final']['tick']}")
    for problem in report["problems"]:
        print(f"  PROBLEM {problem}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"report: {args.output}")
    return 0 if not report["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
