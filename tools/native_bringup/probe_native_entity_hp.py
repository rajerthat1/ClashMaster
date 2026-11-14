#!/usr/bin/env python3
"""Capture per-card max HP from the native 15.535.29 runtime.

This is the native counterpart of ``tools/native_analysis/entity_hp_probe.py``
(which speaks the A51 bridge protocol). It drives a short X-Bow match on a
local rootless worker through :class:`NativeRoyaleEnv`, forces a chosen set of
deck cards onto the arena, and folds ``max_hp`` from every observed state.

``max_hp`` is constant for a live entity, so the maximum over the match is the
level-normalized unit HP for that card. The replay's ``avatar*.expLevel`` (11
in ``examples/xbow-3.0.json``) is the level contract; use the same replay for
the A51 side.

Usage (worker already listening)::

    tools/native_bringup/probe_native_entity_hp.py \
        --port 39031 --targets 27000008 \
        --output builds/native/xbow-entity-hp-15.535.29-xbow-seed1.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SANDBOX = PROJECT_ROOT / ".resource" / "cr-native-sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from native_core.env import NativeRoyaleEnv  # noqa: E402
from training.deck_spec import XBOW_DECK  # noqa: E402

ARENA_COLUMNS = 18
ARENA_ROWS = 32
CARD_COSTS = {int(card.card_id): int(XBOW_DECK.cost(card.card_id)) for card in XBOW_DECK.cards}
CARD_LABELS = {
    27000008: "X-Bow",
    26000000: "Knight",
    26000001: "Archers",
    27000006: "Tesla",
    26000010: "Skeletons",
    26000084: "Electro Spirit",
    28000000: "Fireball",
    28000011: "The Log",
    203000000: "Hero Knight (form)",
    13000001: "Evo Archers (form)",
    13000102: "Evo Tesla (form)",
}
DEFAULT_TARGETS = (27000008, 26000000, 26000001, 27000006, 26000010, 26000084)


def absolute_cell(position: int) -> tuple[int, int]:
    row, column = divmod(int(position), ARENA_COLUMNS)
    return column * 1000 + 500, row * 1000 + 500


def choose_position(rows: list[str], *, side: int) -> tuple[int, int] | None:
    """Pick the legal cell closest to a forward mid-arena point for the side."""
    target_x, target_y = 9000, 12000 if side == 0 else 20000
    best: tuple[float, int] | None = None
    for row in range(ARENA_ROWS):
        line = rows[row]
        for column in range(ARENA_COLUMNS):
            if line[column] != "1":
                continue
            position = row * ARENA_COLUMNS + column
            x, y = absolute_cell(position)
            distance = (x - target_x) ** 2 + (y - target_y) ** 2
            if best is None or distance < best[0]:
                best = (distance, position)
    return absolute_cell(best[1]) if best is not None else None


def merge_entity_hp(state: Mapping[str, Any], aggregate: dict[str, dict[str, Any]]) -> None:
    tick = int(state.get("tick", 0))
    for entity in state.get("entities", []):
        side = int(entity.get("side", -1))
        if side not in (0, 1):
            continue
        card_id = int(entity.get("card_id", -1))
        if card_id == -1:
            continue
        key = str(card_id)
        row = aggregate.get(key)
        if row is None:
            row = aggregate[key] = {
                "cardId": card_id,
                "baseCardId": int(entity.get("base_card_id", card_id)),
                "name": CARD_LABELS.get(card_id, str(card_id)),
                "maxHp": None,
                "firstSeenTick": tick,
                "sides": [],
                "abilityStateNames": {},
                "abilityCharges": [],
                "abilityManaCost": None,
                "abilityAvailable": False,
            }
        max_hp = entity.get("max_hp")
        if max_hp is not None:
            row["maxHp"] = max(int(max_hp), int(row["maxHp"] or 0))
        if side not in row["sides"]:
            row["sides"].append(side)
        state_code = entity.get("ability_state_code")
        if state_code is not None and int(state_code) >= 0:
            name = str(entity.get("ability_state_name", state_code))
            row["abilityStateNames"][name] = row["abilityStateNames"].get(name, 0) + 1
        charges = entity.get("ability_charges_remaining")
        if charges is not None and int(charges) >= 0 and int(charges) not in row["abilityCharges"]:
            row["abilityCharges"].append(int(charges))
        mana_cost = entity.get("ability_mana_cost")
        if mana_cost is not None and int(mana_cost) >= 0:
            row["abilityManaCost"] = int(mana_cost)
        if entity.get("ability_available"):
            row["abilityAvailable"] = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=39031)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-ticks", type=int, default=4000)
    parser.add_argument("--decision-period", type=int, default=20)
    parser.add_argument(
        "--replay",
        type=Path,
        default=SANDBOX / "examples" / "xbow-3.0.json",
    )
    parser.add_argument(
        "--targets",
        default=",".join(str(card) for card in DEFAULT_TARGETS),
        help="base card ids to force onto the arena",
    )
    parser.add_argument(
        "--cycle-elixir",
        type=int,
        default=9,
        help="when no target is in hand, spend down to this elixir to cycle the hand (0 disables)",
    )
    parser.add_argument(
        "--activate-ability",
        action="store_true",
        help="press the Hero Knight ability once per entity and record the state change",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    targets = tuple(int(part) for part in args.targets.split(",") if part.strip())
    replay = json.loads(args.replay.read_text(encoding="utf-8"))
    replay["rndSeed"] = int(args.seed)

    env = NativeRoyaleEnv(host=args.host, port=args.port, timeout=args.timeout)
    started = time.perf_counter()
    state = env.reset(replay, warmup_steps=100)
    entity_hp: dict[str, dict[str, Any]] = {}
    merge_entity_hp(state, entity_hp)
    plays: dict[str, int] = {str(card): 0 for card in targets}
    rejected: list[dict[str, Any]] = []
    ability_activations: list[dict[str, Any]] = []
    activated: set[int] = set()

    def maybe_activate(state: Mapping[str, Any]) -> None:
        if not args.activate_ability:
            return
        for entity in state.get("entities", []):
            entity_id = int(entity.get("entity_id", -1))
            if entity_id < 0 or entity_id in activated:
                continue
            if not entity.get("ability_available"):
                continue
            if int(entity.get("ability_charges_remaining", 0) or 0) <= 0:
                continue
            side = int(entity.get("side", -1))
            before = {
                "tick": int(state.get("tick", 0)),
                "cardId": int(entity.get("card_id", -1)),
                "abilityState": entity.get("ability_state_name"),
                "abilityStateCode": entity.get("ability_state_code"),
                "charges": entity.get("ability_charges_remaining"),
                "manaCost": entity.get("ability_mana_cost"),
            }
            activated.add(entity_id)
            try:
                receipt = env.use_ability(side=side, entity_id=entity_id)
            except Exception as error:  # noqa: BLE001 - record, do not abort the run
                ability_activations.append({**before, "error": f"{type(error).__name__}: {error}"})
                continue
            ability_activations.append({**before, "side": side, "entityId": entity_id, "receipt": receipt})

    while int(state["tick"]) < args.max_ticks:
        actions: list[dict[str, Any]] = []
        if int(state["tick"]) % args.decision_period == 0:
            for player in state.get("players", []):
                side = int(player["side"])
                elixir = int(player["elixir"])
                hand = [
                    int(deck_index) for deck_index in player.get("hand_deck_indices", [])
                    if 0 <= int(deck_index) < len(XBOW_DECK.cards)
                ]
                # Prefer an explicit target card; the X-Bow needs a banked bar.
                choice = next(
                    (
                        (deck_index, int(XBOW_DECK.cards[deck_index].card_id))
                        for deck_index in hand
                        if int(XBOW_DECK.cards[deck_index].card_id) in targets
                        and elixir >= CARD_COSTS.get(int(XBOW_DECK.cards[deck_index].card_id), 99)
                    ),
                    None,
                )
                if choice is None and args.cycle_elixir > 0 and elixir >= args.cycle_elixir:
                    # Cycle the static hand so targets can rotate in, while
                    # keeping the bank above the X-Bow's six elixir.
                    affordable = [
                        (deck_index, int(XBOW_DECK.cards[deck_index].card_id))
                        for deck_index in hand
                        if elixir >= CARD_COSTS.get(int(XBOW_DECK.cards[deck_index].card_id), 99)
                    ]
                    if affordable:
                        choice = min(affordable, key=lambda row: CARD_COSTS.get(row[1], 99))
                if choice is None:
                    continue
                deck_index, _card_id = choice
                rows = [str(row) for row in env.probe_grid(side=side, deck_index=deck_index)["rows"]]
                cell = choose_position(rows, side=side)
                if cell is None:
                    continue
                actions.append({"side": side, "deck_index": deck_index, "x": cell[0], "y": cell[1]})
        transition = env.joint_training_transition(actions, steps=1)
        for item in transition["joint_action"]["actions"]:
            result = item["result"]
            if result.get("accepted"):
                deck_index = int(result.get("deck_index", item.get("deck_index", -1)))
                if 0 <= deck_index < len(XBOW_DECK.cards):
                    card_id = int(XBOW_DECK.cards[deck_index].card_id)
                    if str(card_id) in plays:
                        plays[str(card_id)] += 1
            else:
                rejected.append({
                    "tick": int(state["tick"]),
                    "side": int(item["side"]),
                    "result_code": int(result.get("result_code", -1)),
                    "result_reason": result.get("result_reason"),
                })
        episode = transition["step"]["episode"]
        if bool(episode.get("terminated") or episode.get("truncated")):
            state = transition.get("state", state)
            merge_entity_hp(state, entity_hp)
            maybe_activate(state)
            break
        state = transition["state"]
        merge_entity_hp(state, entity_hp)
        maybe_activate(state)

    entity_hp = {
        key: {**row, "name": CARD_LABELS.get(int(row["cardId"]), row["name"])}
        for key, row in entity_hp.items()
    }
    report = {
        "schema": "xbow-native-entity-hp-v1",
        "build": "15.535.29",
        "abi": "x86_64",
        "seed": int(args.seed),
        "deck": XBOW_DECK.to_dict(),
        "targets": list(targets),
        "terminal_tick": int(state.get("tick", 0)),
        "plays": plays,
        "rejected": rejected[:50],
        "abilityActivations": ability_activations,
        "entity_hp": sorted(entity_hp.values(), key=lambda row: str(row["cardId"])),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    print(f"terminal tick {report['terminal_tick']} plays {plays} rejected {len(rejected)}")
    if ability_activations:
        print(f"ability activations: {len(ability_activations)}")
        for item in ability_activations:
            print(
                f"  tick {item.get('tick')} side {item.get('side')} card {item.get('cardId')} "
                f"{item.get('abilityState')} charges {item.get('charges')} "
                f"mana {item.get('manaCost')} receipt {item.get('receipt', item.get('error'))}"
            )
    for row in report["entity_hp"]:
        print(
            f"  {row['name']:22} cardId {row['cardId']:>10} base {row['baseCardId']:>9} "
            f"maxHp {row['maxHp']} firstTick {row['firstSeenTick']} "
            f"ability {row['abilityStateNames']} charges {row['abilityCharges']} "
            f"mana {row['abilityManaCost']} available {row['abilityAvailable']}"
        )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
