#!/usr/bin/env python3
"""Verify the live entity/HP path on a native probe.

``observe`` already returns the engine object graph: every live object with its
owner, card id (``-1`` for towers), arena position, and the hitpoint component
values (``hp``/``maxHp``). This tool drives one fresh match, deploys cards for
each owner, and samples that graph so the entity/HP path has a live golden:

* towers are present with plausible positive HP at fixed native tower cells,
* a deployed unit appears with its own hp/maxHp and position,
* sampled hp values evolve (damage) without becoming corrupt,
* the object vector stays within the probe's bound.

It does not invent field meanings: it reports exactly what ``observe`` returns.

The per-card ``maxHp`` table this tool emits is the level-normalized unit-HP
parity input: run it against A51/16.635.1 and the local 15.535.29 worker with
the same ``--level``/``--deck`` and compare ``unitMaxHp``. Forward the control
port first::

    adb forward tcp:26789 tcp:26789
    .venv/bin/python tools/native_analysis/entity_hp_probe.py \
        --level 11 --deck xbow --decisions 60 \
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
from native_runner.cr_native_env import HandAction, NativeClashEnv  # noqa: E402
from native_runner.match_factory import STANDARD_DECK, MatchConfig  # noqa: E402
from scripted_xbow_match import CARD_NAMES, HERO_TO_BASE, XBOW_DECK, XBOW_FORMS  # noqa: E402

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
# Default unit-HP parity targets: the X-Bow deck's non-spell cards.
XBOW_UNITS = tuple(card_id for card_id in XBOW_DECK if CARD_NAMES.get(card_id) not in {"Fireball", "Log"})


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


def choose_target_actions(
    before: dict[str, Any], owners: tuple[int, ...], x: int, y: int, targets: tuple[int, ...]
) -> list[HandAction]:
    """Play the first affordable hand card whose base id is a requested target."""

    actions: list[HandAction] = []
    for player in before.get("players", []):
        owner = int(player["owner"])
        if owner not in owners:
            continue
        for card in player.get("hand", []):
            base = HERO_TO_BASE.get(int(card["cardId"]), int(card["cardId"]))
            if targets and base not in targets:
                continue
            if float(player["elixir"]) >= float(card["cost"]):
                actions.append(HandAction(owner, int(card["handIndex"]), x, y))
                break
    return actions


def build_config(args: argparse.Namespace) -> MatchConfig:
    if args.deck == "xbow":
        deck = XBOW_DECK
        forms = XBOW_FORMS
    else:
        deck = STANDARD_DECK
        forms = (0,) * 8
    level = max(0, int(args.level))
    return MatchConfig(
        deck0=deck,
        deck1=deck,
        deck0_form_availability=forms,
        deck1_form_availability=forms,
        seed=int(args.seed),
        level_cap=level,
        minimum_card_level=level,
        king_tower_level=level or None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=26789)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--decisions", type=int, default=1, help="deployment rounds (one observe/play per round)")
    parser.add_argument("--samples", type=int, default=8, help="passive samples after the deployment rounds")
    parser.add_argument("--sample-ticks", type=int, default=40)
    parser.add_argument("--level", type=int, default=0, help="normalize cards and King Towers to this level (0 = template)")
    parser.add_argument("--deck", choices=("standard", "xbow"), default="standard")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--deploy-cards",
        default=None,
        help="comma list of base card ids to deploy; omit to deploy any affordable card",
    )
    parser.add_argument(
        "--owners",
        default="0,1",
        help="comma list drawn from 0,1 (default both owners)",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    owners = tuple(int(part) for part in args.owners.split(",") if part.strip())
    if not owners or any(owner not in (0, 1) for owner in owners):
        parser.error("--owners must be a comma list drawn from 0,1")
    if args.deploy_cards is None:
        targets = XBOW_UNITS if args.deck == "xbow" else ()
    else:
        targets = tuple(int(part) for part in args.deploy_cards.split(",") if part.strip())

    env = NativeClashEnv(args.host, args.port, timeout=args.timeout)
    status = env.status()
    if not status.get("coldReady"):
        print("error: native content runtime is not cold-ready on the device", file=sys.stderr)
        return 2

    config = build_config(args)
    report: dict[str, Any] = {
        "clientVersion": status.get("clientVersion"),
        "mode": status.get("mode"),
        "level": int(args.level),
        "deck": list(config.deck0),
        "forms": list(config.deck0_form_availability),
        "seed": int(args.seed),
        "targets": list(targets),
        "samples": [],
        "deployments": [],
        "problems": [],
    }

    observation = env.create_match(config)
    report["start"] = summarize_objects(observation)

    # Deployment rounds: reach the first legal command tick, then play a target
    # card per owner whenever one is in hand and affordable.
    env.transition((), advance_ticks=90)
    started = time.monotonic()
    hp_history: dict[tuple[int, int], list[int]] = {}
    unit_max_hp: dict[str, dict[str, Any]] = {}

    def record_sample(index: int, observation: dict[str, Any]) -> list[dict[str, Any]]:
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
        for item in units:
            card_id = int(item["cardId"])
            base = int(HERO_TO_BASE.get(card_id, card_id))
            entry = unit_max_hp.setdefault(
                str(card_id),
                {
                    "cardId": card_id,
                    "baseCardId": base,
                    "name": CARD_NAMES.get(base, str(base)),
                    "maxHp": None,
                    "firstSeenIndex": index,
                    "firstSeenTick": sample["tick"],
                    "owners": [],
                    "count": 0,
                },
            )
            if item["maxHp"] is not None:
                entry["maxHp"] = max(int(item["maxHp"]), int(entry["maxHp"] or 0))
            entry["count"] += 1
            if item["owner"] is not None and int(item["owner"]) not in entry["owners"]:
                entry["owners"].append(int(item["owner"]))
        return units

    for index in range(args.decisions):
        before = env.observe()
        if before.get("ended"):
            break
        actions = choose_target_actions(before, owners, 9000, 10000, targets)
        if actions:
            for action in actions:
                player_state = next(
                    (p for p in before["players"] if int(p.get("owner", -1)) == action.owner),
                    {"hand": []},
                )
                card = next(
                    (c for c in player_state["hand"] if int(c["handIndex"]) == action.hand_index),
                    None,
                )
                report["deployments"].append(
                    {
                        "index": index,
                        "tick": before.get("tick"),
                        "owner": action.owner,
                        "handIndex": action.hand_index,
                        "cardId": int(card["cardId"]) if card else None,
                    }
                )
            env.transition(actions, advance_ticks=2)
        env.transition((), advance_ticks=args.sample_ticks)
        record_sample(len(report["samples"]), env.observe())

    # Passive samples let deployed units take damage so the HP path is proven
    # live (and, at A51 tick parity, comparable damage can be checked).
    for _ in range(args.samples):
        env.transition((), advance_ticks=args.sample_ticks)
        record_sample(len(report["samples"]), env.observe())

    damaged = [hp for hp in hp_history.values() if len({value for value in hp}) > 1]
    if not damaged:
        report["problems"].append("no sampled entity changed hitpoints; the HP path may be static")
    report["damagedEntities"] = len(damaged)
    report["unitMaxHp"] = unit_max_hp

    report["seconds"] = round(time.monotonic() - started, 3)
    print(f"\n{len(report['samples'])} samples in {report['seconds']} s, {len(report['problems'])} problems")
    print("unit max hp:")
    for row in sorted(unit_max_hp.values(), key=lambda entry: str(entry["cardId"])):
        print(
            f"  {row['name']:14} cardId {row['cardId']:>10} base {row['baseCardId']:>9} "
            f"maxHp {row['maxHp']} owners {row['owners']} first tick {row['firstSeenTick']}"
        )
    for problem in report["problems"]:
        print(f"  PROBLEM {problem}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"report: {args.output}")
    return 0 if not report["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
