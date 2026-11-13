#!/usr/bin/env python3
"""Verify the live entity/HP path on a native probe.

``observe`` already returns the engine object graph: every live object with its
owner, card id (``-1`` for towers), arena position, and the hitpoint component
values (``hp``/``maxHp``). This tool drives one fresh match, deploys a card for
each owner, and samples that graph so the entity/HP path has a live golden:

* towers are present with plausible positive HP at fixed native tower cells,
* a deployed unit appears with its own hp/maxHp and position,
* sampled hp values evolve (damage) without becoming corrupt,
* the object vector stays within the probe's bound.

It does not invent field meanings: it reports exactly what ``observe`` returns.
Forward the control port first::

    adb forward tcp:26789 tcp:26789
    .venv/bin/python tools/native_analysis/entity_hp_probe.py \
        --output /tmp/opencode/entity_hp_probe.json

The static layout behind these fields is the 16.635.1 hitpoint accessor
``0x0108b474``, byte-identical to the 15.535.13 accessor ``0x00f2db90``
(components[2], current HP at component+0x10, max HP at +0x14).
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

from multi_decision_match import choose_actions  # noqa: E402
from native_runner.cr_native_env import NativeClashEnv, RunnerError  # noqa: E402
from native_runner.match_factory import MatchConfig  # noqa: E402

TOWER_CARD_ID = -1
# The four fixed native 1v1 tower cells (king + two princesses per owner).
TOWER_CELLS = {
    (9000, 3000),
    (9000, 29000),
    (3500, 6500),
    (14500, 6500),
    (3500, 25500),
    (14500, 25500),
}


def summarize_objects(observation: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "slot": item.get("slot"),
            "nativeObjectId": item.get("nativeObjectId"),
            "owner": item.get("owner"),
            "cardId": item.get("cardId"),
            "x": item.get("x"),
            "y": item.get("y"),
            "hp": item.get("hp"),
            "maxHp": item.get("maxHp"),
            "objectIndex": item.get("objectIndex"),
            "secondaryIndex": item.get("secondaryIndex"),
        }
        for item in observation.get("objects", [])
        if not item.get("null")
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=26789)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--sample-ticks", type=int, default=40)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    env = NativeClashEnv(args.host, args.port, timeout=args.timeout)
    status = env.status()
    if not status.get("coldReady"):
        print("error: native content runtime is not cold-ready on the device", file=sys.stderr)
        return 2

    report: dict[str, Any] = {
        "clientVersion": status.get("clientVersion"),
        "mode": status.get("mode"),
        "samples": [],
        "problems": [],
    }

    observation = env.create_match(MatchConfig())
    report["start"] = summarize_objects(observation)

    # Reach the first legal command tick, then deploy one affordable card per
    # owner so a fresh unit with its own hitpoints enters the object graph.
    env.transition((), advance_ticks=90)
    before = env.observe()
    actions = choose_actions(before, (0, 1), 9000, 10000)
    env.transition(actions, advance_ticks=22)
    report["deployed"] = [{"owner": action.owner, "handIndex": action.hand_index} for action in actions]

    started = time.monotonic()
    hp_history: dict[tuple[int, int], list[int]] = {}
    for index in range(args.samples):
        env.transition((), advance_ticks=args.sample_ticks)
        observation = env.observe()
        objects = summarize_objects(observation)
        sample = {
            "index": index,
            "tick": observation.get("tick"),
            "count": observation.get("count"),
            "returned": observation.get("returned"),
            "truncated": observation.get("truncated"),
            "crowns": observation.get("crownsRaw"),
            "objects": objects,
        }
        report["samples"].append(sample)
        towers = [
            item
            for item in objects
            if item["cardId"] == TOWER_CARD_ID and (item["x"], item["y"]) in TOWER_CELLS
        ]
        ownerless = [
            item
            for item in objects
            if item["cardId"] == TOWER_CARD_ID and (item["x"], item["y"]) not in TOWER_CELLS
        ]
        units = [item for item in objects if item["cardId"] != TOWER_CARD_ID]
        tower_hp = [item["hp"] for item in towers]
        unit_hp = [item["hp"] for item in units]
        print(
            f"sample {index} tick {sample['tick']:5} objects {len(objects):3} "
            f"towers {len(towers)} hp {tower_hp} units {[(u['cardId'], u['hp'], u['maxHp']) for u in units]} "
            f"other {len(ownerless)}"
        )
        if observation.get("truncated"):
            report["problems"].append(f"sample {index}: object graph was truncated")
        if len(towers) < 6:
            report["problems"].append(f"sample {index}: only {len(towers)} of 6 native tower cells are present")
        for item in towers + units:
            if item["hp"] is None:
                continue
            if item["hp"] < 0:
                report["problems"].append(f"sample {index}: slot {item['slot']} has negative hp")
            if item["maxHp"] is not None and item["hp"] > item["maxHp"]:
                report["problems"].append(
                    f"sample {index}: slot {item['slot']} hp {item['hp']} exceeds maxHp {item['maxHp']}"
                )
            hp_history.setdefault((item["nativeObjectId"], item["cardId"]), []).append(item["hp"])

    damaged = [hp for hp in hp_history.values() if len({value for value in hp}) > 1]
    if not damaged:
        report["problems"].append("no sampled entity changed hitpoints; the HP path may be static")
    report["damagedEntities"] = len(damaged)

    report["seconds"] = round(time.monotonic() - started, 3)
    print(f"\n{len(report['samples'])} samples in {report['seconds']} s, {len(report['problems'])} problems")
    for problem in report["problems"]:
        print(f"  PROBLEM {problem}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"report: {args.output}")
    return 0 if not report["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
