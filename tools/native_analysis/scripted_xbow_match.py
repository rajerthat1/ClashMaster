#!/usr/bin/env python3
"""Run one deterministic scripted X-Bow 3.0 self-play match on the native probe.

This is the Phase 1 "scripted X-Bow v0" scaffold (HANDOFF NEXT ACTION item 1,
ROADMAP Phase 1): a small, inspectable ``observe -> action`` policy split into
defense / cycle / offense / spell modules, played through the same two-sided
decision loop the training harness will use.

The deck is FirstLight-Zero's exact X-Bow 3.0 deck (speed variant)::

    X-Bow, Hero Knight, Evo Archers, Evo Tesla, Skeletons,
    Electro Spirit, Fireball, The Log

Every placement is expressed in an owner-local arena frame (own king at
``(9000, 3000)``, river at ``y=16000``) and mirrored for owner 1, so both
seats run the same policy. The action layer keeps a blocked-cell memory and
falls back to the next candidate cell if the engine refuses a placement.

Forward the probe control port first::

    adb forward tcp:26789 tcp:26789
    .venv/bin/python tools/native_analysis/scripted_xbow_match.py \
        --output /tmp/opencode/scripted_xbow_match.json

The tool prints one line per decision (tick, module, actions, latency, problems)
and reports the match result plus per-decision latency statistics. Check the
device crash buffer before and after::

    adb logcat -b crash -d
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from multi_decision_match import validate_decision  # noqa: E402
from native_runner.cr_native_env import AbilityAction, HandAction, NativeClashEnv, RunnerError  # noqa: E402
from native_runner.match_factory import MatchConfig  # noqa: E402
from native_runner.normal_form_evidence import NORMAL_MODE_HERO_FORM_TO_BASE_CARD  # noqa: E402

# FirstLight-Zero's exact deck and form masks (bit 0 evolution, bit 1 Hero).
XBOW_DECK = (27000008, 26000000, 26000001, 27000006, 26000010, 26000084, 28000000, 28000011)
XBOW_FORMS = (0, 2, 1, 1, 0, 0, 0, 0)

XBOW = 27000008
KNIGHT = 26000000
ARCHERS = 26000001
TESLA = 27000006
SKELETONS = 26000010
ELECTRO_SPIRIT = 26000084
FIREBALL = 28000000
THE_LOG = 28000011

# Hero forms deploy as runtime card ids (e.g. Hero Knight 203000000). Normalize
# them back to the deck card id before matching support/threat roles.
HERO_TO_BASE = dict(NORMAL_MODE_HERO_FORM_TO_BASE_CARD)
KNIGHT_HERO_ID = next(
    runtime for runtime, base in HERO_TO_BASE.items() if base == KNIGHT
)
READY_ABILITY_STATES = {2, 4}

CARD_NAMES = {
    XBOW: "X-Bow",
    KNIGHT: "Knight",
    ARCHERS: "Archers",
    TESLA: "Tesla",
    SKELETONS: "Skeletons",
    ELECTRO_SPIRIT: "Electro Spirit",
    FIREBALL: "Fireball",
    THE_LOG: "Log",
}

# Arena geometry in the owner-local frame (own king at y=3000, river at y=16000).
ARENA_WIDTH = 18000
ARENA_HEIGHT = 32000
RIVER_Y = 16000
OWN_KING = (9000, 3000)
MAX_ELIXIR = 10.0
# Wait for a full-ish bar before the first committed X-Bow so the opening is not
# an immediate all-in; the scripted bot is a baseline, not the final policy.
OPENING_ELIXIR = 7.0
X_BOW_COST = 6

# Owner-local placement presets. y grows toward the enemy half.
BRIDGE_Y = 15000
TESLA_Y = 12300
ARCHERS_Y = 11200
CYCLE_CELL = (9000, 9000)


def to_world(owner: int, local_x: float, local_y: float) -> tuple[int, int]:
    """Map an owner-local arena point to world coordinates."""

    x = int(round(local_x))
    y = int(round(local_y))
    if owner == 1:
        x = ARENA_WIDTH - x
        y = ARENA_HEIGHT - y
    return max(0, min(ARENA_WIDTH - 1, x)), max(0, min(ARENA_HEIGHT - 1, y))


def to_local(owner: int, world_x: float, world_y: float) -> tuple[float, float]:
    """Inverse of :func:`to_world`."""

    if owner == 1:
        return ARENA_WIDTH - world_x, ARENA_HEIGHT - world_y
    return float(world_x), float(world_y)


def owner_half(owner: int, world_y: float) -> bool:
    return world_y < RIVER_Y if owner == 0 else world_y > RIVER_Y


@dataclass
class Decision:
    owner: int
    action: HandAction | None
    module: str
    card_id: int | None = None
    note: str = ""


@dataclass
class SeatMemory:
    """Per-seat policy state that outlives one observation."""

    lane: int = 0  # 0 = left princess lane, 1 = right
    blocked_cells: set[tuple[int, int, int]] = field(default_factory=set)
    xbow_ticks: int = 0


class ScriptedXBowPolicy:
    """Deterministic X-Bow 3.0 baseline: defense, cycle, offense, spells."""

    def __init__(self) -> None:
        self.memory = {0: SeatMemory(), 1: SeatMemory()}

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _player(observation: dict[str, Any], owner: int) -> dict[str, Any]:
        return next(item for item in observation["players"] if int(item["owner"]) == owner)

    @staticmethod
    def _hand_entry(player: dict[str, Any], card_id: int) -> dict[str, Any] | None:
        for card in player.get("hand", []):
            if int(card["cardId"]) == card_id:
                return card
        return None

    @staticmethod
    def _card_id(item: dict[str, Any]) -> int:
        card_id = int(item.get("cardId", -1))
        return HERO_TO_BASE.get(card_id, card_id)

    @staticmethod
    def _hp(item: dict[str, Any]) -> int:
        """Hitpoints from ``observe``; a dying/blank entry may report ``None``."""

        value = item.get("hp")
        return int(value) if isinstance(value, (int, float)) else 0

    @classmethod
    def _units(cls, observation: dict[str, Any], owner: int | None = None) -> list[dict[str, Any]]:
        # Projectiles inherit their source card id but carry no hitpoint
        # component (``hp`` is null); only hp-bearing objects are units.
        units = [
            item
            for item in observation.get("objects", [])
            if int(item.get("cardId", -1)) > 0 and cls._hp(item) > 0
        ]
        if owner is None:
            return units
        return [item for item in units if int(item.get("owner", -1)) == owner]

    @staticmethod
    def _enemy_princesses(observation: dict[str, Any], owner: int) -> list[dict[str, Any]]:
        return [
            item
            for item in observation.get("objects", [])
            if int(item.get("cardId", 0)) == -1
            and int(item.get("owner", -1)) == 1 - owner
            and int(item.get("x", 0)) != 9000
        ]

    def _lane_local_x(self, observation: dict[str, Any], owner: int, memory: SeatMemory) -> int:
        """Pick the enemy princess lane with the lowest current HP."""

        princesses = self._enemy_princesses(observation, owner)
        if princesses:
            target = min(princesses, key=lambda item: (self._hp(item), int(item.get("x", 0))))
            local_x = int(to_local(owner, int(target["x"]), int(target["y"]))[0])
            memory.lane = 0 if local_x < 9000 else 1
        return 3500 if memory.lane == 0 else 14500

    def _available(self, player: dict[str, Any], card_id: int, elixir: float) -> dict[str, Any] | None:
        card = self._hand_entry(player, card_id)
        if card is None or float(player["elixir"]) < float(card["cost"]):
            return None
        return card

    def _choose_cell(
        self,
        memory: SeatMemory,
        card_id: int,
        candidates: list[tuple[int, int]],
    ) -> tuple[int, int] | None:
        for x, y in candidates:
            if (card_id, x, y) not in memory.blocked_cells:
                return x, y
        return None

    # -------------------------------------------------------------- policy parts

    def _intercept_point(self, owner: int, threat: dict[str, Any]) -> tuple[int, int]:
        """Owner-local point between our king and the most urgent threat."""

        world_x = float(threat["x"])
        world_y = float(threat["y"])
        local_x, local_y = to_local(owner, world_x, world_y)
        king_x, king_y = OWN_KING
        ratio = 0.62
        x = king_x + (local_x - king_x) * ratio
        y = king_y + (local_y - king_y) * ratio
        return (
            int(max(2200, min(15800, x))),
            int(max(5200, min(15200, y))),
        )

    @staticmethod
    def _cluster_center(owner: int, units: list[dict[str, Any]]) -> tuple[int, int]:
        # Spell targets are world coordinates, not mirrored placements.
        x = int(round(sum(float(item["x"]) for item in units) / len(units)))
        y = int(round(sum(float(item["y"]) for item in units) / len(units)))
        return x, y

    def _threats(self, observation: dict[str, Any], owner: int) -> list[dict[str, Any]]:
        threats = [item for item in self._units(observation, 1 - owner) if owner_half(owner, float(item["y"]))]
        return sorted(threats, key=lambda item: abs(float(item["y"]) - (3000 if owner == 0 else 29000)))

    def _defend(
        self,
        observation: dict[str, Any],
        owner: int,
        player: dict[str, Any],
        threats: list[dict[str, Any]],
        memory: SeatMemory,
    ) -> Decision | None:
        if not threats:
            return None
        elixir = float(player["elixir"])
        urgent = threats[0]
        target = self._intercept_point(owner, urgent)

        # Spells first when several units are stacked: Fireball for value, Log
        # for swarms. The engine clamps spell damage; the centroid is enough
        # for a scripted baseline.
        fireball = self._available(player, FIREBALL, elixir)
        if len(threats) >= 3 and fireball is not None:
            x, y = self._cluster_center(owner, threats)
            return Decision(owner, HandAction(owner, int(fireball["handIndex"]), x, y), "spell.fireball",
                            FIREBALL, f"{len(threats)} threats")
        log = self._available(player, THE_LOG, elixir)
        if len(threats) >= 2 and log is not None:
            x, y = self._cluster_center(owner, threats)
            return Decision(owner, HandAction(owner, int(log["handIndex"]), x, y), "spell.log",
                            THE_LOG, f"{len(threats)} threats")

        big = max(threats, key=lambda item: self._hp(item))
        big_hp = self._hp(big)
        cheap = [SKELETONS, ELECTRO_SPIRIT]
        tanks = [KNIGHT, TESLA, ARCHERS]

        if big_hp >= 900:
            for card_id in tanks:
                card = self._available(player, card_id, elixir)
                if card is None:
                    continue
                cell = self._choose_cell(
                    memory,
                    card_id,
                    [target, (target[0], min(15200, target[1] + 900)), (target[0], max(5500, target[1] - 900))],
                )
                if cell is None:
                    continue
                return Decision(owner, HandAction(owner, int(card["handIndex"]), *to_world(owner, *cell)),
                                "defense.tank", card_id, f"hp {big_hp}")

        # Cheap cycle cards are the default answer to a single unit; they also
        # keep the deck moving toward the next X-Bow / Tesla.
        for card_id in cheap:
            card = self._available(player, card_id, elixir)
            if card is None:
                continue
            cell = self._choose_cell(memory, card_id, [target, (9000, 9000)])
            if cell is None:
                continue
            return Decision(owner, HandAction(owner, int(card["handIndex"]), *to_world(owner, *cell)),
                            "defense.cheap", card_id, f"hp {self._hp(urgent)}")

        for card_id in tanks:
            card = self._available(player, card_id, elixir)
            if card is None:
                continue
            cell = self._choose_cell(memory, card_id, [target])
            if cell is None:
                continue
            return Decision(owner, HandAction(owner, int(card["handIndex"]), *to_world(owner, *cell)),
                            "defense.unit", card_id, f"hp {self._hp(urgent)}")
        return None

    def _support_xbow(
        self,
        observation: dict[str, Any],
        owner: int,
        player: dict[str, Any],
        own_xbow: dict[str, Any],
        memory: SeatMemory,
    ) -> Decision | None:
        elixir = float(player["elixir"])
        xbow_local_x, _unused_local_y = to_local(owner, float(own_xbow["x"]), float(own_xbow["y"]))
        lane_x = 3500 if xbow_local_x < 9000 else 14500

        own_units = self._units(observation, owner)
        own_tesla = [item for item in own_units if self._card_id(item) == TESLA]
        own_support = [
            item for item in own_units if self._card_id(item) in {TESLA, KNIGHT, ARCHERS}
        ]
        near_support = [
            item
            for item in own_support
            if abs(float(item["x"]) - float(own_xbow["x"])) < 3200
            and abs(float(item["y"]) - float(own_xbow["y"])) < 3200
        ]

        # Tesla directly behind the X-Bow is the standard defensive pairing.
        if len(own_tesla) == 0 and self._available(player, TESLA, elixir):
            card = self._available(player, TESLA, elixir)
            assert card is not None
            cell = self._choose_cell(
                memory,
                TESLA,
                [
                    (int(lane_x), TESLA_Y),
                    (9000, 11000),
                    (int(lane_x), 10500),
                ],
            )
            if cell is not None:
                return Decision(owner, HandAction(owner, int(card["handIndex"]), *to_world(owner, *cell)),
                                "offense.tesla", TESLA)
        if len(near_support) <= 1 and self._available(player, KNIGHT, elixir) and elixir >= 6:
            card = self._available(player, KNIGHT, elixir)
            assert card is not None
            cell = self._choose_cell(memory, KNIGHT, [(int(lane_x), BRIDGE_Y + 600)])
            if cell is not None:
                return Decision(owner, HandAction(owner, int(card["handIndex"]), *to_world(owner, *cell)),
                                "offense.knight", KNIGHT)
        if len(near_support) <= 1 and self._available(player, ARCHERS, elixir) and elixir >= 6:
            card = self._available(player, ARCHERS, elixir)
            assert card is not None
            cell = self._choose_cell(memory, ARCHERS, [(int(lane_x), ARCHERS_Y)])
            if cell is not None:
                return Decision(owner, HandAction(owner, int(card["handIndex"]), *to_world(owner, *cell)),
                                "offense.archers", ARCHERS)
        return None

    def _cycle(
        self,
        observation: dict[str, Any],
        owner: int,
        player: dict[str, Any],
        memory: SeatMemory,
    ) -> Decision | None:
        elixir = float(player["elixir"])
        if elixir < 8.5:
            return None
        # Never cycle the win condition deliberately; keep X-Bow for offense.
        for card_id in (SKELETONS, ELECTRO_SPIRIT):
            card = self._available(player, card_id, elixir)
            if card is None:
                continue
            cell = self._choose_cell(memory, card_id, [CYCLE_CELL, (9000, 7200), (5500, 6200)])
            if cell is None:
                continue
            return Decision(owner, HandAction(owner, int(card["handIndex"]), *to_world(owner, *cell)),
                            "cycle", card_id)
        return None

    def decide(self, observation: dict[str, Any], owner: int) -> Decision:
        player = self._player(observation, owner)
        memory = self.memory[owner]
        elixir = float(player["elixir"])
        tick = int(observation.get("tick", 0))

        threats = self._threats(observation, owner)
        if threats:
            decision = self._defend(observation, owner, player, threats, memory)
            if decision is not None:
                return decision
            return Decision(owner, None, "wait.blocked", None, f"{len(threats)} threats, elixir {elixir:.1f}")

        own_units = self._units(observation, owner)
        own_xbows = [item for item in own_units if int(item["cardId"]) == XBOW]
        own_xbow = max(own_xbows, key=lambda item: self._hp(item)) if own_xbows else None
        if own_xbow is not None:
            decision = self._support_xbow(observation, owner, player, own_xbow, memory)
            if decision is not None:
                return decision
            decision = self._cycle(observation, owner, player, memory)
            if decision is not None:
                return decision
            return Decision(owner, None, "wait.support", None, f"xbow hp {self._hp(own_xbow)}")

        if tick < 40:
            return Decision(owner, None, "wait.opening", None, "no action before the first legal window")

        if elixir >= OPENING_ELIXIR:
            card = self._available(player, XBOW, elixir)
            if card is not None:
                lane_x = self._lane_local_x(observation, owner, memory)
                cell = self._choose_cell(memory, XBOW, [(lane_x, BRIDGE_Y), (9000, 13800)])
                if cell is not None:
                    return Decision(owner, HandAction(owner, int(card["handIndex"]), *to_world(owner, *cell)),
                                    "offense.xbow", XBOW, f"lane {memory.lane} elixir {elixir:.1f}")
            # No X-Bow in hand: cycle the cheap cards until it arrives.
            decision = self._cycle(observation, owner, player, memory)
            if decision is not None:
                return decision
            for card_id in (KNIGHT, ARCHERS, TESLA):
                card = self._available(player, card_id, elixir)
                if card is None:
                    continue
                cell = self._choose_cell(memory, card_id, [CYCLE_CELL])
                if cell is None:
                    continue
                return Decision(owner, HandAction(owner, int(card["handIndex"]), *to_world(owner, *cell)),
                                "cycle.expensive", card_id)

        if elixir >= MAX_ELIXIR - 0.1:
            decision = self._cycle(observation, owner, player, memory)
            if decision is not None:
                return decision
        return Decision(owner, None, "wait.economy", None, f"elixir {elixir:.1f}")

    def note_blocked(self, owner: int, action: HandAction, card_id: int) -> None:
        world = (int(action.x), int(action.y))
        local = to_local(owner, *world)
        self.memory[owner].blocked_cells.add((card_id, int(round(local[0])), int(round(local[1]))))


def evaluate_action(
    before: dict[str, Any],
    after: dict[str, Any],
    decision: Decision,
) -> list[str]:
    if decision.action is None:
        return []
    problems = validate_decision(
        before,
        after,
        [decision.action],
        int(after["tick"]) - int(before["tick"]),
    )
    return problems


def placement_refused(before: dict[str, Any], after: dict[str, Any], decision: Decision) -> bool:
    """Return True when the engine kept the card in hand (placement refused)."""

    if decision.action is None or decision.card_id is None:
        return False
    cheap_ok = False
    for player in after.get("players", []):
        if int(player["owner"]) != decision.action.owner:
            continue
        before_player = next(
            item for item in before.get("players", []) if int(item["owner"]) == decision.action.owner
        )
        cost = next(
            (
                float(card["cost"])
                for card in before_player.get("hand", [])
                if int(card["handIndex"]) == decision.action.hand_index
            ),
            None,
        )
        if cost is None:
            continue
        cheap_ok = float(player["elixir"]) > float(before_player["elixir"]) - cost + 0.1
    return cheap_ok


def ready_knight_ability(
    diagnostics: dict[str, Any],
    observation: dict[str, Any],
    owner: int,
) -> tuple[AbilityAction, dict[str, Any]] | None:
    """Return the queued-ready Hero Knight ability for one owner, if any."""

    hero = next(
        (
            item
            for item in observation.get("objects", [])
            if int(item.get("owner", -1)) == owner
            and int(item.get("cardId", 0)) == KNIGHT_HERO_ID
            and int(item.get("objectIndex", -1)) >= 0
        ),
        None,
    )
    if hero is None:
        return None
    player = next(
        (item for item in diagnostics.get("players", []) if int(item.get("owner", -1)) == owner), None
    )
    if player is None:
        return None
    for controller in player.get("controllerSlots", []):
        if int(controller.get("championCount") or 0) <= 0:
            continue
        member_ids = {int(member.get("cardId", 0)) for member in controller.get("champions", [])}
        if KNIGHT_HERO_ID not in member_ids and controller.get("abilityName") != "Knight_hero_Ability":
            continue
        if (
            int(controller.get("buttonState") or 0) in READY_ABILITY_STATES
            and int(controller.get("charges") or 0) > 0
        ):
            return (
                AbilityAction(owner, int(hero["objectIndex"]), int(hero["secondaryIndex"])),
                {
                    "abilityName": controller.get("abilityName"),
                    "buttonState": controller.get("buttonState"),
                    "charges": controller.get("charges"),
                    "maxCharges": controller.get("maxCharges"),
                },
            )
    return None


def summarize(observation: dict[str, Any], owner: int) -> dict[str, Any]:
    player = next(item for item in observation["players"] if int(item["owner"]) == owner)
    return {
        "tick": observation.get("tick"),
        "elixir": round(float(player["elixir"]), 3),
        "crowns": int(player.get("crownsRaw", 0)),
        "hand": [
            {"handIndex": card["handIndex"], "cardId": card["cardId"], "cost": card["cost"]}
            for card in player.get("hand", [])
        ],
        "nextCard": (player.get("nextCard") or {}).get("cardId"),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    env = NativeClashEnv(args.host, args.port, timeout=args.timeout)
    status = env.status()
    if not status.get("coldReady"):
        raise RunnerError("native content runtime is not cold-ready on the device")

    policy = ScriptedXBowPolicy()
    config = MatchConfig(
        deck0=XBOW_DECK,
        deck1=XBOW_DECK,
        deck0_form_availability=XBOW_FORMS,
        deck1_form_availability=XBOW_FORMS,
        seed=args.seed,
    )
    report: dict[str, Any] = {
        "clientVersion": status.get("clientVersion"),
        "mode": status.get("mode"),
        "deck": list(XBOW_DECK),
        "forms": list(XBOW_FORMS),
        "seed": args.seed,
        "decisionTicks": args.advance,
        "maxTicks": args.max_ticks,
        "decisions": [],
        "problems": [],
        "blockedPlacements": [],
        "droppedAtEnd": [],
        "abilities": [],
        "abilityRejections": [],
        "latenciesMs": [],
        "modules": Counter(),
        "cardPlays": Counter(),
    }
    if args.owners != "0,1":
        raise RunnerError("scripted X-Bow self-play requires both owners")

    started = time.monotonic()
    observation = env.create_match(config)
    report["configureSeconds"] = round(time.monotonic() - started, 3)
    report["start"] = {
        "state": observation.get("tick"),
        "players": [summarize(observation, 0), summarize(observation, 1)],
    }

    sample: list[dict[str, Any]] = []
    total_actions = 0
    decision_index = 0
    while True:
        before = env.observe()
        if before.get("ended"):
            report["endedAtTick"] = before.get("tick")
            break
        decisions = [policy.decide(before, 0), policy.decide(before, 1)]
        actions = [item.action for item in decisions if item.action is not None]

        # Ability actions are a separate native command, so only fire them for
        # owners that are not also playing a card on this decision. The poll is
        # throttled: it costs one extra control round trip when it runs.
        if args.abilities and decision_index % max(1, args.ability_poll) == 0:
            busy = {action.owner for action in actions}
            diagnostics = env.champion_diagnostics()
            for owner in (0, 1):
                if owner in busy:
                    continue
                ready = ready_knight_ability(diagnostics, before, owner)
                if ready is None:
                    continue
                ability, receipt = ready
                try:
                    activation = env.activate_ability(ability)
                    report["abilities"].append(
                        {
                            "decision": decision_index,
                            "tick": before.get("tick"),
                            "owner": owner,
                            "sourceEntityKey": activation.get("sourceEntityKey"),
                            "executeTick": activation.get("executeTick"),
                            "before": receipt,
                        }
                    )
                    print(
                        f"decision {decision_index:3d} tick {before.get('tick'):5} "
                        f"ability owner {owner} {receipt['abilityName']} -> tick {activation.get('executeTick')}"
                    )
                except RunnerError as error:
                    report["abilityRejections"].append(
                        {"decision": decision_index, "owner": owner, "error": str(error)}
                    )

        decision_started = time.monotonic()
        result = env.transition(actions, advance_ticks=args.advance)
        after = env.observe()
        elapsed_ms = (time.monotonic() - decision_started) * 1000.0
        report["latenciesMs"].append(round(elapsed_ms, 2))

        entry = {
            "decision": decision_index,
            "before": {"tick": before.get("tick"), "players": [summarize(before, 0), summarize(before, 1)]},
            "after": {"tick": after.get("tick"), "players": [summarize(after, 0), summarize(after, 1)]},
            "actions": [
                {
                    "owner": item.action.owner,
                    "module": item.module,
                    "cardId": item.card_id,
                    "handIndex": item.action.hand_index,
                    "x": item.action.x,
                    "y": item.action.y,
                    "note": item.note,
                }
                for item in decisions
                if item.action is not None
            ],
            "transitionOk": bool(result.get("ok")),
            "milliseconds": round(elapsed_ms, 2),
        }

        # A card submitted on the final decision boundary is never consumed: the
        # engine finalizes the match at the same tick. Its queued command and
        # unchanged hand are the end condition, not a policy failure, so
        # validate only decisions that leave the match running.
        match_ended = bool(result.get("ended")) or bool(after.get("ended"))
        entry["endBoundary"] = match_ended
        for decision in decisions:
            report["modules"][decision.module] += 1
            if decision.action is None:
                continue
            if match_ended:
                report["droppedAtEnd"].append(
                    {"owner": decision.action.owner, "module": decision.module, "cardId": decision.card_id}
                )
                continue
            problems = evaluate_action(before, after, decision)
            if placement_refused(before, after, decision) and decision.card_id is not None:
                report["blockedPlacements"].append(
                    {"owner": decision.action.owner, "cardId": decision.card_id,
                     "x": decision.action.x, "y": decision.action.y}
                )
                policy.note_blocked(decision.action.owner, decision.action, decision.card_id)
            entry.setdefault("problems", []).extend(
                f"owner {decision.action.owner}: {problem}" for problem in problems
            )
            report["problems"].extend(
                f"decision {decision_index} owner {decision.action.owner}: {problem}" for problem in problems
            )
            report["cardPlays"][CARD_NAMES.get(decision.card_id, str(decision.card_id))] += 1

        total_actions += len(actions)
        if entry.get("problems"):
            print(
                f"decision {decision_index:3d} tick {before.get('tick'):5} -> {after.get('tick'):5} "
                f"actions {len(actions)} {elapsed_ms:.1f} ms PROBLEMS {entry['problems']}"
            )
        else:
            modules = ",".join(item.module for item in decisions if item.action is not None) or "wait"
            print(
                f"decision {decision_index:3d} tick {before.get('tick'):5} -> {after.get('tick'):5} "
                f"actions {len(actions)} [{modules}] {elapsed_ms:.1f} ms"
            )
        report["decisions"].append(entry)

        if decision_index % 10 == 0:
            sample.append(
                {
                    "decision": decision_index,
                    "tick": after.get("tick"),
                    "crowns": after.get("crownsRaw"),
                    "count": after.get("count"),
                    "towers": [
                        {"owner": item.get("owner"), "x": item.get("x"), "y": item.get("y"),
                         "hp": item.get("hp"), "maxHp": item.get("maxHp")}
                        for item in after.get("objects", [])
                        if int(item.get("cardId", 0)) == -1
                    ],
                }
            )

        decision_index += 1
        if bool(result.get("ended")) or int(after.get("tick", 0)) >= args.max_ticks:
            break
        if args.max_decisions and decision_index >= args.max_decisions:
            break

    final = env.observe()
    report["seconds"] = round(time.monotonic() - started, 3)
    report["totalActions"] = total_actions
    report["final"] = {
        "tick": final.get("tick"),
        "ended": final.get("ended"),
        "finalized": final.get("finalized"),
        "winner": final.get("winner"),
        "crowns": final.get("crownsRaw"),
        "players": [summarize(final, 0), summarize(final, 1)],
        "towers": [
            {"owner": item.get("owner"), "x": item.get("x"), "y": item.get("y"),
             "hp": item.get("hp"), "maxHp": item.get("maxHp")}
            for item in final.get("objects", [])
            if int(item.get("cardId", 0)) == -1
        ],
    }
    report["samples"] = sample
    report["modules"] = dict(report["modules"])
    report["cardPlays"] = dict(report["cardPlays"])
    latencies = report["latenciesMs"]
    if latencies:
        ordered = sorted(latencies)
        report["latency"] = {
            "count": len(latencies),
            "meanMs": round(statistics.fmean(latencies), 2),
            "medianMs": round(statistics.median(latencies), 2),
            "p95Ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
            "maxMs": round(ordered[-1], 2),
        }
    report["matchesPerSecond"] = round(1.0 / report["seconds"], 4) if report["seconds"] else None
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=26789)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--advance", type=int, default=20, help="ticks advanced per decision (20 = one live second)")
    parser.add_argument("--max-ticks", type=int, default=7200, help="stop at this tick if the match has not ended")
    parser.add_argument("--max-decisions", type=int, default=800)
    parser.add_argument("--owners", default="0,1")
    parser.add_argument(
        "--abilities",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="activate a ready Hero Knight ability (polled, separate native command)",
    )
    parser.add_argument("--ability-poll", type=int, default=5, help="poll ability readiness every N decisions")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    try:
        report = run(args)
    except RunnerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(
        f"\nclient {report['clientVersion']} seed {report['seed']}: "
        f"{len(report['decisions'])} decisions, {report['totalActions']} actions, "
        f"{report['seconds']} s, {len(report['problems'])} problems"
    )
    print(f"final tick {report['final']['tick']} ended {report['final']['ended']} "
          f"crowns {report['final']['crowns']} winner {report['final']['winner']}")
    print(f"modules {report['modules']}")
    print(f"plays {report['cardPlays']}")
    print(f"abilities {len(report['abilities'])} (rejected {len(report['abilityRejections'])})")
    if "latency" in report:
        print(f"latency {report['latency']}")
    for problem in report["problems"]:
        print(f"  PROBLEM {problem}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"report: {args.output}")
    return 0 if not report["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
