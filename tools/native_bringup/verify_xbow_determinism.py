#!/usr/bin/env python3
"""X-Bow determinism + legality harness for the native Linux runtime.

For every match seed the harness runs the same replay twice on one worker with
the same deterministic action script and compares a per-tick observation hash.
It also enforces the explicit X-Bow deck/token contract, the form flags the
engine reports, and the consistency between the action mask and engine
refusals.

Usage (worker already listening):

    tools/native_bringup/verify_xbow_determinism.py --port 39031 --matches 20

The exit status is non-zero if any match is non-deterministic, reports an
illegal action, or disagrees with the deck contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys
import time
from typing import Any, Mapping

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SANDBOX = PROJECT_ROOT / ".resource" / "cr-native-sandbox"
for path in (SANDBOX,):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from native_core.env import NativeRoyaleEnv  # noqa: E402
from training.deck_spec import XBOW_DECK  # noqa: E402
from training.schema import (  # noqa: E402
    ActionMaskCache,
    ObservationEncoder,
    build_action_masks,
)


EXPECTED_DECK = (27000008, 26000000, 26000001, 27000006,
                 26000010, 26000084, 28000000, 28000011)
EXPECTED_FORMS = (0, 2, 1, 1, 0, 0, 0, 0)
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
GATE_RESULT_CODES = frozenset({3, 4})
POINTER_KEYS = frozenset({"id", "target"})
DECK_ROWS = [
    {"card_id": card.card_id} for card in XBOW_DECK.cards
]


def _canonical(value: Any) -> Any:
    """Drop volatile process pointers and make the state JSON-canonical."""
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
            if str(key) not in POINTER_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (np.generic,)):
        return value.item()
    return value


def observation_digest(
    state: Mapping[str, Any],
    encoder: ObservationEncoder,
) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(
        _canonical(state), sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))
    for side in (0, 1):
        grid, scalars = encoder.encode(state, side=side)
        digest.update(np.ascontiguousarray(grid, dtype=np.float32).tobytes())
        digest.update(np.ascontiguousarray(scalars, dtype=np.float32).tobytes())
    return digest.hexdigest()


def _absolute_cell(position: int) -> tuple[int, int]:
    row, column = divmod(int(position), 18)
    return column * 1000 + 500, row * 1000 + 500


def _check_deck_contract(state: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for player in state.get("players", []):
        side = int(player["side"])
        slots = [int(value) for value in player["hand_deck_indices"]]
        hand_items = player.get("hand", [])
        order = [int(item["hand_index"]) for item in hand_items]
        if order != list(range(len(hand_items))):
            errors.append(
                f"side {side} hand is not ordered by hand_index: {order}"
            )
        hand = {
            int(item["hand_index"]): item for item in hand_items
        }
        for hand_index, deck_index in enumerate(slots):
            if deck_index < 0:
                continue
            item = hand.get(hand_index)
            if item is None:
                errors.append(f"side {side} hand {hand_index} missing")
                continue
            expected = XBOW_DECK.cards[deck_index]
            if int(item["card_id"]) != expected.card_id:
                errors.append(
                    f"side {side} slot {hand_index}: hand card "
                    f"{item['card_id']} != deck {expected.card_id}"
                )
            if int(item["form_flags"]) != expected.form_flags:
                errors.append(
                    f"side {side} card {expected.card_id}: form flags "
                    f"{item['form_flags']} != {expected.form_flags}"
                )
            if int(item["deck_index"]) != deck_index:
                errors.append(
                    f"side {side} slot {hand_index}: deck index "
                    f"{item['deck_index']} != {deck_index}"
                )
            if bool(item.get("has_evolution", False)) != bool(
                expected.form_flags & 1
            ):
                errors.append(
                    f"side {side} card {expected.card_id}: "
                    "has_evolution mismatch"
                )
            if bool(item.get("has_hero", False)) != bool(
                expected.form_flags & 2
            ):
                errors.append(
                    f"side {side} card {expected.card_id}: has_hero mismatch"
                )
    return errors


def _merge_entity_hp(state: Mapping[str, Any], aggregate: dict[str, dict[str, Any]]) -> None:
    """Accumulate per-card max HP from every observed state.

    ``max_hp`` is constant for an entity, so the maximum over the match is the
    level-normalized unit HP for that card. Keying by runtime card id preserves
    hero/evolution form ids (e.g. Hero Knight ``203000000``) separately from
    their base card.
    """
    tick = int(state.get("tick", 0))
    for entity in state.get("entities", []):
        side = int(entity.get("side", -1))
        if side not in (0, 1):
            continue
        card_id = int(entity.get("card_id", -1))
        if card_id == -1:
            continue
        base_id = int(entity.get("base_card_id", card_id))
        max_hp = entity.get("max_hp")
        key = str(card_id)
        row = aggregate.get(key)
        if row is None:
            row = aggregate[key] = {
                "cardId": card_id,
                "baseCardId": base_id,
                "maxHp": None,
                "firstSeenTick": tick,
                "sides": [],
                "abilityStateNames": {},
                "abilityCharges": [],
                "abilityManaCost": None,
                "abilityAvailable": False,
            }
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


def _golden_frame(state: Mapping[str, Any]) -> dict[str, Any]:
    """Small, build-comparable frame for A51/16.635.1 parity work."""
    players = []
    for player in state.get("players", []):
        players.append({
            "side": int(player["side"]),
            "elixir": int(player.get("elixir", 0)),
            "hand_deck_indices": [int(value) for value in player.get("hand_deck_indices", [])],
            "hand": [
                {
                    "hand_index": int(item["hand_index"]),
                    "deck_index": int(item["deck_index"]),
                    "card_id": int(item["card_id"]),
                    "form_flags": int(item.get("form_flags", 0)),
                }
                for item in player.get("hand", [])
            ],
        })
    towers = [
        {
            "side": int(tower.get("side", -1)),
            "type": tower.get("type"),
            "x": int(tower.get("x", 0)),
            "y": int(tower.get("y", 0)),
            "hp": int(tower.get("hp", 0)),
            "max_hp": int(tower.get("max_hp", 0)),
        }
        for tower in state.get("episode", {}).get("crown_towers", [])
    ]
    entities = [
        {
            "card_id": int(entity.get("card_id", -1)),
            "base_card_id": int(entity.get("base_card_id",
                                           entity.get("card_id", -1))),
            "side": int(entity.get("side", -1)),
            "x": int(entity.get("x", 0)),
            "y": int(entity.get("y", 0)),
            "hp": int(entity.get("hp", 0)),
            "max_hp": int(entity.get("max_hp", 0)),
            "behavior_state": int(entity.get("behavior_state", 0)),
            "ability_state_code": int(entity.get("ability_state_code", -1)),
        }
        for entity in state.get("entities", [])
        if int(entity.get("side", -1)) in (0, 1)
    ]
    episode = state.get("episode", {})
    return {
        "tick": int(state["tick"]),
        "players": players,
        "crown_towers": towers,
        "crowns": [int(value) for value in episode.get("crowns", [0, 0])],
        "entities": sorted(
            entities,
            key=lambda row: (
                row["side"], row["card_id"], row["x"], row["y"], row["hp"]
            ),
        ),
    }


def _choose_actions(
    state: Mapping[str, Any],
    *,
    masks: dict[tuple[int, int], list[str]],
    cache: ActionMaskCache,
    tick: int,
    decision_period: int,
) -> tuple[list[dict[str, int]], dict[int, int]]:
    if tick % decision_period:
        return [], {}
    actions: list[dict[str, int]] = []
    slots: dict[int, int] = {}
    for side in (0, 1):
        card_mask, position_masks, hand = build_action_masks(
            state,
            side=side,
            native_masks=masks,
            decks=[DECK_ROWS, DECK_ROWS],
            cache=cache,
            deck=XBOW_DECK,
        )
        legal_slots = [index for index in range(4) if bool(card_mask[index + 1])]
        if not legal_slots:
            continue
        slot = legal_slots[(tick // decision_period + side) % len(legal_slots)]
        positions = np.flatnonzero(position_masks[slot])
        if positions.size == 0:
            continue
        x, y = _absolute_cell(int(positions[0]))
        actions.append({
            "side": side,
            "deck_index": int(hand[slot]),
            "x": x,
            "y": y,
        })
        slots[side] = slot
    return actions, slots


def run_match(
    env: NativeRoyaleEnv,
    replay: Mapping[str, Any],
    *,
    seed: int,
    max_ticks: int,
    decision_period: int,
    capture_frames: bool = False,
) -> dict[str, Any]:
    encoder = ObservationEncoder(XBOW_DECK)
    cache = ActionMaskCache()
    native_masks: dict[tuple[int, int], list[str]] = {}
    chain = hashlib.sha256(b"xbow-native-trace-v1")
    trace_ticks = 0
    trace_samples: list[dict[str, Any]] = []
    illegal: list[dict[str, Any]] = []
    gate_refusals: list[dict[str, Any]] = []
    deck_errors: list[str] = []
    plays = {str(card_id): 0 for card_id in EXPECTED_DECK}
    golden_frames: list[dict[str, Any]] = []
    entity_hp: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()

    def record(tick: int, digest: str) -> None:
        nonlocal trace_ticks
        chain.update(tick.to_bytes(8, "little", signed=False))
        chain.update(bytes.fromhex(digest) if len(digest) == 64 else digest.encode())
        trace_ticks += 1
        if trace_ticks == 1 or tick % 500 == 0:
            trace_samples.append({"tick": tick, "digest": digest[:16]})

    value = json.loads(json.dumps(replay))
    value["rndSeed"] = int(seed)
    state = env.reset(value, warmup_steps=100)
    deck_errors.extend(_check_deck_contract(state))
    _merge_entity_hp(state, entity_hp)
    record(int(state["tick"]), observation_digest(state, encoder))
    if capture_frames:
        golden_frames.append(_golden_frame(state))
    while int(state["tick"]) < max_ticks:
        for player in state.get("players", []):
            side = int(player["side"])
            for raw_index in player["hand_deck_indices"]:
                deck_index = int(raw_index)
                if deck_index < 0 or (side, deck_index) in native_masks:
                    continue
                result = env.probe_grid(side=side, deck_index=deck_index)
                native_masks[(side, deck_index)] = [
                    str(row) for row in result["rows"]
                ]
        actions, slots = _choose_actions(
            state,
            masks=native_masks,
            cache=cache,
            tick=int(state["tick"]),
            decision_period=decision_period,
        )
        transition = env.joint_training_transition(actions, steps=1)
        for item in transition["joint_action"]["actions"]:
            side = int(item["side"])
            result = item["result"]
            code = int(result.get("result_code", -1))
            accepted = bool(result.get("accepted", False))
            if accepted:
                deck_index = int(result.get("deck_index", item.get("deck_index", -1)))
                card_id = int(XBOW_DECK.cards[deck_index].card_id)
                plays[str(card_id)] += 1
                continue
            record = {
                "tick": int(state["tick"]),
                "side": side,
                "slot": slots.get(side),
                "result_code": code,
                "result_reason": result.get("result_reason"),
            }
            if code in GATE_RESULT_CODES:
                gate_refusals.append(record)
            else:
                illegal.append(record)
        episode = transition["step"]["episode"]
        done = bool(episode.get("terminated") or episode.get("truncated"))
        if done:
            terminal_tick = int(episode.get("terminal_tick", state["tick"]))
            result_chain = hashlib.sha256(
                b"xbow-native-terminal-v1"
                + chain.digest()
                + json.dumps(_canonical(episode), sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            break
        state = transition["state"]
        _merge_entity_hp(state, entity_hp)
        record(int(state["tick"]), observation_digest(state, encoder))
        if capture_frames and int(state["tick"]) % 500 == 0:
            golden_frames.append(_golden_frame(state))
        if trace_ticks % 128 == 0:
            deck_errors.extend(_check_deck_contract(state))
    else:
        done = False
        terminal_tick = int(state["tick"])
        result_chain = chain.hexdigest()
    elapsed = time.perf_counter() - started
    return {
        "seed": int(seed),
        "trace_digest": result_chain,
        "trace_ticks": trace_ticks,
        "trace_samples": trace_samples,
        "golden_frames": golden_frames,
        "illegal": illegal,
        "gate_refusals": gate_refusals,
        "deck_errors": sorted(set(deck_errors)),
        "plays": plays,
        "entity_hp": sorted(entity_hp.values(), key=lambda row: str(row["cardId"])),
        "terminal_tick": terminal_tick,
        "done": done,
        "elapsed_seconds": round(elapsed, 3),
    }


def _compare_runs(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if left["trace_ticks"] != right["trace_ticks"]:
        errors.append(
            f"trace length {left['trace_ticks']} != {right['trace_ticks']}"
        )
    if left["trace_digest"] != right["trace_digest"]:
        errors.append("per-tick observation hash chain differs")
    if left["terminal_tick"] != right["terminal_tick"]:
        errors.append("terminal tick differs")
    return errors


def _summary(match: Mapping[str, Any], *, repeats: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "seed": match["seed"],
        "done": match["done"],
        "terminal_tick": match["terminal_tick"],
        "elapsed_seconds": match["elapsed_seconds"],
        "illegal": len(match["illegal"]),
        "gate_refusals": len(match["gate_refusals"]),
        "deck_errors": match["deck_errors"],
        "plays": match["plays"],
        "entity_hp": match.get("entity_hp", []),
        "repeat_terminal_ticks": [run["terminal_tick"] for run in repeats],
        "repeat_illegal": [len(run["illegal"]) for run in repeats],
        "golden_frames": match.get("golden_frames", []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=39031)
    parser.add_argument(
        "--ports",
        help="comma-separated worker ports; overrides --port when given",
    )
    parser.add_argument("--matches", type=int, default=20)
    parser.add_argument("--start-seed", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=2,
                        help="runs per seed; 2 proves tick-for-tick determinism")
    parser.add_argument("--max-ticks", type=int, default=12000)
    parser.add_argument("--decision-period", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--replay",
        type=Path,
        default=SANDBOX / "examples" / "xbow-3.0.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--golden-output",
        type=Path,
        help="write A51-comparable golden frames for --start-seed",
    )
    parser.add_argument(
        "--entity-hp-output",
        type=Path,
        help="write the level-normalized per-card max-HP table for --start-seed",
    )
    args = parser.parse_args(argv)
    if args.matches < 1 or args.repeats < 1:
        parser.error("--matches and --repeats must be positive")

    replay = json.loads(args.replay.read_text(encoding="utf-8-sig"))
    replay_deck = replay["battle"]["deck0"]["sp"]
    if tuple(int(item["d"]) for item in replay_deck) != EXPECTED_DECK:
        parser.error("replay deck order does not match the X-Bow contract")

    ports = (
        [int(value) for value in args.ports.split(",") if value.strip()]
        if args.ports else [args.port]
    )
    if not ports:
        parser.error("at least one worker port is required")

    failures: list[str] = []
    results: list[dict[str, Any]] = []
    started = time.perf_counter()

    def evaluate_seed(seed: int, port: int) -> tuple[
        int, int, list[dict[str, Any]], list[str]
    ]:
        env = NativeRoyaleEnv(
            host=args.host, port=port, timeout=args.timeout
        )
        try:
            runs = [
                run_match(
                    env,
                    replay,
                    seed=seed,
                    max_ticks=args.max_ticks,
                    decision_period=args.decision_period,
                    capture_frames=(
                        args.golden_output is not None
                        and seed == args.start_seed
                    ),
                )
                for _ in range(args.repeats)
            ]
        finally:
            env.close()
        first, repeats = runs[0], runs[1:]
        checks: list[str] = []
        if not first["done"]:
            checks.append(f"seed {seed}: match did not terminate")
        if first["illegal"]:
            checks.append(
                f"seed {seed}: {len(first['illegal'])} illegal actions"
            )
        if first["deck_errors"]:
            checks.append(
                f"seed {seed}: deck contract: {first['deck_errors'][:3]}"
            )
        for run in repeats:
            if run["illegal"]:
                checks.append(
                    f"seed {seed}: {len(run['illegal'])} illegal actions "
                    "on repeat"
                )
            for message in _compare_runs(first, run):
                checks.append(f"seed {seed}: {message}")
        return seed, port, runs, checks

    with ThreadPoolExecutor(max_workers=len(ports)) as executor:
        futures = [
            executor.submit(
                evaluate_seed,
                args.start_seed + offset,
                ports[offset % len(ports)],
            )
            for offset in range(args.matches)
        ]
        for future in as_completed(futures):
            seed, port, runs, checks = future.result()
            first, repeats = runs[0], runs[1:]
            failures.extend(checks)
            results.append(_summary(first, repeats=repeats))
            print(
                f"seed {seed} port {port}: tick={first['terminal_tick']} "
                f"illegal={len(first['illegal'])} "
                f"gate={len(first['gate_refusals'])} "
                f"deterministic={not checks}",
                flush=True,
            )
    results.sort(key=lambda row: row["seed"])

    total = time.perf_counter() - started
    report = {
        "schema": "xbow-native-determinism-v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "runtime": {"host": args.host, "ports": ports},
        "deck": XBOW_DECK.to_dict(),
        "replay": str(args.replay),
        "matches": args.matches,
        "repeats": args.repeats,
        "decision_period": args.decision_period,
        "max_ticks": args.max_ticks,
        "elapsed_seconds": round(total, 3),
        "failed": bool(failures),
        "failures": failures,
        "results": results,
        "totals": {
            "illegal": sum(row["illegal"] for row in results),
            "gate_refusals": sum(row["gate_refusals"] for row in results),
            "deck_errors": sum(len(row["deck_errors"]) for row in results),
            "plays": sum(sum(row["plays"].values()) for row in results),
        },
        "entity_hp": [
            {**row, "name": CARD_LABELS.get(int(row["cardId"]), str(row["cardId"]))}
            for row in (results[0].get("entity_hp", []) if results else [])
        ],
    }
    if args.output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        args.output = (
            PROJECT_ROOT / "builds" / "native"
            / f"xbow-determinism-{stamp}.json"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.golden_output is not None:
        golden = {
            "schema": "xbow-native-golden-v1",
            "build": "15.535.29",
            "abi": "x86_64",
            "seed": args.start_seed,
            "deck": XBOW_DECK.to_dict(),
            "replay": str(args.replay),
            "frames": report["results"][0]["golden_frames"],
        }
        args.golden_output.parent.mkdir(parents=True, exist_ok=True)
        args.golden_output.write_text(
            json.dumps(golden, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.entity_hp_output is not None:
        table = {
            "schema": "xbow-native-entity-hp-v1",
            "build": "15.535.29",
            "abi": "x86_64",
            "seed": args.start_seed,
            "deck": XBOW_DECK.to_dict(),
            "replay": str(args.replay),
            "entity_hp": report["entity_hp"],
        }
        args.entity_hp_output.parent.mkdir(parents=True, exist_ok=True)
        args.entity_hp_output.write_text(
            json.dumps(table, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({
        "passed": not failures,
        "matches": len(results),
        "illegal": report["totals"]["illegal"],
        "gate_refusals": report["totals"]["gate_refusals"],
        "deck_errors": report["totals"]["deck_errors"],
        "failure_count": len(failures),
        "elapsed_seconds": report["elapsed_seconds"],
        "output": str(args.output),
    }, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
