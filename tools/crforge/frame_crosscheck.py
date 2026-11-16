#!/usr/bin/env python3
"""C3 native cross-check: game-frame observations agree between the engines.

The 2026-11-19 frame fix converts crforge's seat-framed scene into the game
frame the model consumes.  Bridge-vs-bridge parity cannot prove that conversion:
both sides would move together.  This gate compares one deterministic play in
two independent runtimes:

* the official x86_64 16.635.1 engine (``crx_engine_host.py``) plays a card at
  a game point and reports the spawned units' ``x``/``y``;
* a one-lane ``VectorBattle`` plays the same card at the same game point and
  reports the spawned units from the vectorized facts layer.

The engine's action space stays seat-framed, so the artifact also records the
pending header: the game point ``(6500, 10500)`` must appear as
``pending_x ~= 18000 - 6500`` inside the bridge while the observed unit is at
``x ~= 6500``.  That is the invariant that was wrong before the fix.

A running engine is required.  Start one first (it exits when ``--seconds``
elapses)::

    CR_NATIVE_BUILD_ABI=x86_64 \\
        .venv/bin/python tools/native_analysis/crx_engine_host.py --seconds 600 &

    .venv/bin/python tools/crforge/frame_crosscheck.py \\
        --native-port 26789 --output runs/crforge-c3/frame-crosscheck.json

Without an engine the tool records ``status: unavailable`` and exits 0 unless
``--require-native`` is passed (useful in the periodic gate run).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / ".resource" / "crforge" / "python"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import numpy as np

from native_runner.cr_native_env import HandAction, NativeClashEnv, RunnerError
from native_runner.match_factory import MatchConfig

# The learner deck rotated so the checked card is in the opening hand after the
# vector's own deck shuffle.  Forms follow the locked X-Bow deck.
DECK = (26000010, 27000008, 27000006, 26000001, 26000000, 26000084, 28000000, 28000011)
FORMS = (0, 0, 1, 1, 2, 0, 0, 0)
NAMES = ("Skeletons", "Xbow", "Tesla", "Archer", "Knight", "ElectroSpirit", "Fireball", "Log")
CARD_ID = 26000010
CARD_NAME = "Skeleton"
# Hand cycling avoids spells: their first legal tile can be anywhere, and a
# cross-check should not damage the arena it is measuring.
SPELL_CARD_IDS = {28000000, 28000011}
GAME_POINT = (6500, 10500)
SEAT = 0
TOWER_ROWS = 6
# One-tick decisions make the first spawned frame observable, so the native and
# crforge units are compared before either side has moved them.
WARMUP_STEPS = 300
DECISION_TICKS = 1
ADVANCE_TICKS = 1
SPAWN_TICK_LIMIT = 120


def _engine_available(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        NativeClashEnv(host, port, timeout=timeout).status()
        return True
    except Exception:  # noqa: BLE001 - any connection/protocol failure means "no engine"
        return False


def _native_play(host: str, port: int, *, seed: int, timeout: float) -> dict[str, Any]:
    env = NativeClashEnv(host, port, timeout=timeout)
    status = env.status()
    observation = env.create_match(
        MatchConfig(
            deck0=DECK,
            deck1=DECK,
            deck0_form_availability=FORMS,
            deck1_form_availability=FORMS,
            seed=seed,
        )
    )
    hand = observation["players"][SEAT]["hand"]
    slot = next((int(item["handIndex"]) for item in hand if int(item["cardId"]) == CARD_ID), None)
    if slot is None:
        raise RunnerError(f"native opening hand has no card {CARD_ID}: {[item['cardId'] for item in hand]}")
    result = env.transition([HandAction(SEAT, slot, *GAME_POINT)], advance_ticks=ADVANCE_TICKS)
    if not result.get("ok"):
        raise RunnerError(f"native play was refused: {result}")
    after = env.observe()
    units: list[tuple[int, int]] = []
    for _ in range(SPAWN_TICK_LIMIT):
        units = [
            (int(item["x"]), int(item["y"]))
            for item in after.get("objects", [])
            if int(item.get("cardId", -1)) == CARD_ID
            and int(item.get("owner", -1)) == SEAT
            and isinstance(item.get("hp"), (int, float))
            and int(item["hp"]) > 0
        ]
        if units:
            break
        env.transition([], advance_ticks=ADVANCE_TICKS)
        after = env.observe()
    if not units:
        raise RunnerError(f"native card {CARD_ID} never spawned a unit at {GAME_POINT}")
    return {
        "clientVersion": status.get("clientVersion"),
        "contentVersion": status.get("contentVersion"),
        "seed": seed,
        "handSlot": slot,
        "commandPoint": list(GAME_POINT),
        "tick": int(after.get("tick", 0)),
        "unitCount": len(units),
        "positions": sorted(units),
    }


def _crforge_play(*, seed: int) -> dict[str, Any]:
    from crforge_bridge.catalog import Catalog
    from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
    from crforge_bridge.jvm import start as start_jvm
    from crforge_bridge.vector_observation import VectorObservationEncoder

    start_jvm(str(DEFAULT_INSTALL))
    from crforge_env import _jvm as env_jvm
    from crforge_env.vector import LadderConfig, SideSetup, VectorBattle

    env_jvm._started_with = Path(DEFAULT_INSTALL).expanduser().resolve()
    setup = SideSetup(towers="King_PrincessTowers", king_level=16, tower_level=16)
    vector = VectorBattle(
        1,
        1,
        install=str(DEFAULT_INSTALL),
        tables=str(DEFAULT_TABLES),
        config=LadderConfig(decision_ticks=DECISION_TICKS, level=16, tower_level=16),
    )
    try:
        vector.reset([seed], list(NAMES), list(NAMES), list(FORMS), list(FORMS), setup0=setup, setup1=setup)
        encoder = VectorObservationEncoder(vector, Catalog.load())
        encoder.reset()
        batch = encoder.encode()
        for _ in range(WARMUP_STEPS):
            vector.step(np.zeros((1, 2), dtype=np.int32))
        encoder.encode(batch)

        first_ability = int(vector.schema.first_ability)
        hand_slots = int(batch.hand_slots)
        rows = int(batch.rows)
        cols = int(batch.cols)
        slot: int | None = None
        for _ in range(500):
            matches = np.flatnonzero(batch.hand_card_id[0, SEAT] == CARD_ID)
            if matches.size:
                slot = int(matches[0])
                break
            # Cycle the hand with a legal non-spell play; the checked card
            # enters from the deck.  Nothing here needs to be policy-quality.
            legal = np.flatnonzero(vector.masks[0, SEAT, 1:first_ability])
            actions = np.zeros((1, 2), dtype=np.int32)
            for raw in legal:
                hand_index = int(raw) // (rows * cols)
                if int(batch.hand_card_id[0, SEAT, hand_index]) not in SPELL_CARD_IDS:
                    actions[0, SEAT] = int(raw) + 1
                    break
            vector.step(actions)
            encoder.encode(batch)
        if slot is None:
            raise RuntimeError(f"crforge never drew card {CARD_ID}")

        # ``VectorBattle`` masks are seat-framed; seat 0 mirrors x, so the game
        # point ``(6500, 10500)`` is seat cell ``(11, 10)``.
        seat_x = (18000 - GAME_POINT[0]) // 1000
        seat_y = GAME_POINT[1] // 1000
        cell = seat_y * cols + seat_x
        for _ in range(500):
            if int(vector.masks[0, SEAT, 1 + slot * rows * cols + cell]):
                break
            vector.step(np.zeros((1, 2), dtype=np.int32))
            encoder.encode(batch)
        else:
            raise RuntimeError(f"crforge target cell ({seat_x}, {seat_y}) never became legal for card {CARD_ID}")
        if int(batch.hand_pending[0, SEAT, slot]):
            raise RuntimeError("the same play is already waiting; the cross-check needs a fresh slot")

        actions = np.zeros((1, 2), dtype=np.int32)
        actions[0, SEAT] = 1 + slot * rows * cols + cell
        vector.step(actions)
        encoder.encode(batch)
        pending = {
            "count": int(batch.pending_count[0, SEAT]),
            "cardIds": batch.pending_card_id[0, SEAT].tolist(),
            "x": batch.pending_x[0, SEAT].tolist(),
            "y": batch.pending_y[0, SEAT].tolist(),
        }

        # Advance one tick at a time and stop on the first frame the spawns
        # exist: both engines are then compared at the spawn point, not after
        # an arbitrary amount of unit movement.
        units: list[tuple[int, int]] = []
        for _ in range(SPAWN_TICK_LIMIT):
            vector.step(np.zeros((1, 2), dtype=np.int32))
            encoder.encode(batch)
            units = []
            for row in np.flatnonzero(batch.entity_valid[0]):
                entity_id = int(batch.entity_id[0, row])
                if entity_id < TOWER_ROWS:
                    continue
                vocab = str(vector.vocabulary[int(batch.entity_row_vocab[0, row])])
                if CARD_NAME.lower() not in vocab.lower():
                    continue
                units.append((int(batch.entity_x[0, row]), int(batch.entity_y[0, row])))
            if units:
                break
        if not units:
            raise RuntimeError(f"crforge card {CARD_ID} never spawned a unit at {GAME_POINT}")
        return {
            "seed": seed,
            "handSlot": slot,
            "seatCell": [seat_x, seat_y],
            "tick": int(batch.tick[0, SEAT]),
            "pending": pending,
            "unitCount": len(units),
            "positions": sorted(units),
            "illegal": int(batch.illegal[0, SEAT]),
            "lastRefusal": int(batch.last_refusal[0, SEAT]),
            "handSlots": hand_slots,
        }
    finally:
        vector.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--native-host", default="127.0.0.1")
    parser.add_argument("--native-port", type=int, default=26789)
    parser.add_argument("--native-timeout", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--tolerance", type=int, default=0, help="max per-axis unit difference")
    parser.add_argument("--require-native", action="store_true", help="fail when no engine is listening")
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c3/frame-crosscheck.json"))
    args = parser.parse_args(argv)

    artifact: dict[str, Any] = {
        "schema": "crforge-frame-crosscheck.v1",
        "measured": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "shape": {
            "cardId": CARD_ID,
            "cardName": CARD_NAME,
            "gamePoint": list(GAME_POINT),
            "tolerance": args.tolerance,
            "seed": args.seed,
        },
        "substrate": {
            "native": f"{args.native_host}:{args.native_port}",
            "release": "16.635.1",
            "tables": "16.635.8",
        },
    }

    if not _engine_available(args.native_host, args.native_port):
        artifact["status"] = "unavailable"
        artifact["error"] = "no native engine is listening; start crx_engine_host.py first"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "unavailable", "output": str(args.output)}, indent=2))
        return 2 if args.require_native else 0

    native = _native_play(args.native_host, args.native_port, seed=args.seed, timeout=args.native_timeout)
    crforge = _crforge_play(seed=args.seed)
    artifact["native"] = native
    artifact["crforge"] = crforge

    native_positions = [tuple(value) for value in native["positions"]]
    crforge_positions = [tuple(value) for value in crforge["positions"]]
    diffs: list[str] = []
    if len(native_positions) != len(crforge_positions):
        diffs.append(f"unit count {len(native_positions)} != {len(crforge_positions)}")
    for index, (left, right) in enumerate(zip(native_positions, crforge_positions, strict=False)):
        if abs(left[0] - right[0]) > args.tolerance or abs(left[1] - right[1]) > args.tolerance:
            diffs.append(f"unit {index}: native {left} != crforge {right}")

    target_pending = crforge["pending"]["x"]
    seat_x = 18000 - GAME_POINT[0]
    # The facts layer stores each seat's pending point in game coordinates;
    # the engine's internal action space stays seat-framed (the play was
    # addressed with seat cell ``seatCell``).  This is the conversion the
    # 2026-11-19 fix made, and the one the model consumes.
    pending_ok = any(abs(int(value) - GAME_POINT[0]) <= 2 for value in target_pending if int(value) > 0)
    artifact["frame"] = {
        "gamePoint": list(GAME_POINT),
        "seatPoint": [seat_x, GAME_POINT[1]],
        "pendingX": target_pending,
        "pendingIsGameFramed": pending_ok,
        "unitPositionsMatch": not diffs,
        "mismatches": diffs,
    }
    artifact["status"] = "pass" if not diffs and pending_ok else "fail"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": artifact["status"],
                "nativePositions": native_positions,
                "crforgePositions": crforge_positions,
                "pendingX": target_pending,
                "pendingIsGameFramed": pending_ok,
                "mismatches": diffs,
                "output": str(args.output),
            },
            indent=2,
        )
    )
    return 0 if artifact["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
