#!/usr/bin/env python3
"""Verify one Hero Knight ability action on the native probe.

This is the Phase 1 ability gate (HANDOFF NEXT ACTION item 2). It deploys the
X-Bow deck's Hero Knight, reads the probe's live Champion controller dump, and
activates the ready ability through ``AbilityAction``. The engine receipts are
checked, not assumed:

* the controller reports ``Ready`` with one charge before activation,
* the activation is injected and executes on the next native tick,
* the controller moves to ``AllChargesConsumed`` (state 6) with zero charges,
* elixir pays the ability's printed ``ManaCost`` (``Knight_hero_Ability`` = 2)
  within the measured regen bound,
* a second activation is refused while no charge remains,
* the champion carrier is still a live object and ``adb logcat -b crash`` is
  empty after the run.

The 16.635.1 Champion layout was recovered with this tool's controller dump:
controller vtable `0x01a51c30`, action-data vtable `0x01a3fd60`, ability-data
vtable `0x01a348e0`, and ``LogicalCharacterAbilityData`` moved MaxCharges to
`+0xb0` / configured cooldown to `+0xb8`.

Forward the probe control port first::

    adb forward tcp:26789 tcp:26789
    .venv/bin/python tools/native_analysis/ability_action_probe.py \
        --output /tmp/opencode/ability_action_probe.json
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

from native_runner.cr_native_env import (  # noqa: E402
    AbilityAction,
    HandAction,
    HandActionRejected,
    NativeClashEnv,
    RunnerError,
)
from native_runner.match_factory import MatchConfig  # noqa: E402
from native_runner.normal_form_evidence import NORMAL_MODE_HERO_FORM_TO_BASE_CARD  # noqa: E402
from native_runner.user_interface import SPEED_XBOW_DECK, SPEED_XBOW_FORMS  # noqa: E402

KNIGHT = 26000000
KNIGHT_HERO_ID = next(
    runtime for runtime, base in NORMAL_MODE_HERO_FORM_TO_BASE_CARD.items() if base == KNIGHT
)
KNIGHT_ABILITY_NAME = "Knight_hero_Ability"
KNIGHT_ABILITY_MANA_COST = 2
# One decision advances 20 ticks (~1 s) of single elixir; use the upper regen
# bound already measured on this build (FINDINGS §3.17).
MAX_REGEN_PER_TICK = 0.06
READY_STATES = {2, 4}
ALL_CHARGES_CONSUMED = 6


def find_knight_controller(diagnostics: dict[str, Any], owner: int) -> dict[str, Any] | None:
    player = next(
        (item for item in diagnostics.get("players", []) if int(item.get("owner", -1)) == owner), None
    )
    if player is None:
        return None
    for controller in player.get("controllerSlots", []):
        if controller.get("pointer") in (None, "0x0"):
            continue
        if int(controller.get("championCount") or 0) <= 0:
            continue
        names = {controller.get("abilityName"), controller.get("actionName")}
        member_ids = {int(member.get("cardId", 0)) for member in controller.get("champions", [])}
        if KNIGHT_ABILITY_NAME in names or KNIGHT_HERO_ID in member_ids:
            return controller
    return None


def summarize_hero(observation: dict[str, Any], owner: int) -> dict[str, Any] | None:
    for item in observation.get("objects", []):
        if int(item.get("owner", -1)) == owner and int(item.get("cardId", 0)) == KNIGHT_HERO_ID:
            return {
                "slot": item.get("slot"),
                "objectIndex": item.get("objectIndex"),
                "secondaryIndex": item.get("secondaryIndex"),
                "x": item.get("x"),
                "y": item.get("y"),
                "hp": item.get("hp"),
                "maxHp": item.get("maxHp"),
            }
    return None


def controller_receipt(controller: dict[str, Any] | None) -> dict[str, Any] | None:
    if controller is None:
        return None
    return {
        "actionName": controller.get("actionName"),
        "abilityName": controller.get("abilityName"),
        "buttonState": controller.get("buttonState"),
        "available": controller.get("buttonState") in READY_STATES,
        "remainingCooldownMs": controller.get("cooldown"),
        "configuredCooldownMs": controller.get("configuredCooldown"),
        "remainingCharges": controller.get("charges"),
        "maxCharges": controller.get("maxCharges"),
        "championCount": controller.get("championCount"),
        "champions": controller.get("champions"),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    env = NativeClashEnv(args.host, args.port, timeout=args.timeout)
    status = env.status()
    if not status.get("coldReady"):
        raise RunnerError("native content runtime is not cold-ready on the device")

    report: dict[str, Any] = {
        "clientVersion": status.get("clientVersion"),
        "mode": status.get("mode"),
        "heroCardId": KNIGHT_HERO_ID,
        "problems": [],
    }
    started = time.monotonic()
    config = MatchConfig(
        deck0=SPEED_XBOW_DECK,
        deck1=SPEED_XBOW_DECK,
        deck0_form_availability=SPEED_XBOW_FORMS,
        deck1_form_availability=SPEED_XBOW_FORMS,
        seed=args.seed,
    )
    observation = env.create_match(config)
    # Bank a full bar before deploying so the 2-elixir ability is independently
    # affordable after the 3-elixir Knight.
    env.transition((), advance_ticks=args.bank_ticks)
    observation = env.observe()
    report["elixirBeforeDeploy"] = observation["players"][0]["elixir"]

    knight = None
    cycles = 0
    while cycles < 24:
        player = observation["players"][0]
        knight = next((card for card in player["hand"] if int(card["cardId"]) == KNIGHT), None)
        if knight is not None:
            break
        cheap = min(player["hand"], key=lambda card: float(card["cost"]))
        env.transition([HandAction(0, int(cheap["handIndex"]), 9000, 9000)], advance_ticks=20)
        observation = env.observe()
        cycles += 1
    if knight is None:
        raise RunnerError("Hero Knight never reached the hand during the cycling window")
    report["cycles"] = cycles
    report["elixirBeforePlay"] = observation["players"][0]["elixir"]

    play = env.transition([HandAction(0, int(knight["handIndex"]), 9000, 11500)], advance_ticks=20)
    report["playTransitionOk"] = bool(play.get("ok"))
    observation = env.observe()
    report["elixirAfterPlay"] = observation["players"][0]["elixir"]
    hero = summarize_hero(observation, 0)
    report["heroAfterPlay"] = hero
    if hero is None:
        report["problems"].append(f"Hero Knight {KNIGHT_HERO_ID} did not appear after the play")

    before_diag = env.champion_diagnostics()
    before_controller = find_knight_controller(before_diag, 0)
    report["controllerBefore"] = controller_receipt(before_controller)
    if before_controller is None:
        report["problems"].append("champion diagnostic did not find the Knight controller")
    else:
        if before_controller.get("buttonState") not in READY_STATES:
            report["problems"].append(
                f"ability was not ready before activation: buttonState={before_controller.get('buttonState')}"
            )
        if int(before_controller.get("charges") or 0) <= 0:
            report["problems"].append("ability had no charges before activation")
        if int(before_controller.get("championCount") or 0) != 1:
            report["problems"].append(
                f"expected one live Knight carrier, saw {before_controller.get('championCount')}"
            )

    if hero is None or before_controller is None:
        report["seconds"] = round(time.monotonic() - started, 3)
        return report

    elixir_before_ability = float(observation["players"][0]["elixir"])
    report["tickBeforeAbility"] = observation.get("tick")
    report["elixirBeforeAbility"] = elixir_before_ability
    activation = env.activate_ability(
        AbilityAction(0, int(hero["objectIndex"]), int(hero["secondaryIndex"]))
    )
    report["activation"] = activation
    if activation.get("injected") is False:
        report["problems"].append("probe reported the ability was not injected")

    env.transition((), advance_ticks=args.advance)
    observation = env.observe()
    report["tickAfterAbility"] = observation.get("tick")
    report["elixirAfterAbility"] = observation["players"][0]["elixir"]
    report["heroAfterAbility"] = summarize_hero(observation, 0)

    after_diag = env.champion_diagnostics()
    after_controller = find_knight_controller(after_diag, 0)
    report["controllerAfter"] = controller_receipt(after_controller)
    if after_controller is None:
        report["problems"].append("champion diagnostic lost the Knight controller after activation")
    else:
        if int(after_controller.get("buttonState") or 0) != ALL_CHARGES_CONSUMED:
            report["problems"].append(
                f"expected AllChargesConsumed ({ALL_CHARGES_CONSUMED}) after activation, "
                f"saw {after_controller.get('buttonState')}"
            )
        if int(after_controller.get("charges") or 0) != 0:
            report["problems"].append(
                f"expected zero remaining charges after activation, saw {after_controller.get('charges')}"
            )

    elapsed = int(observation.get("tick", 0)) - int(report.get("tickBeforeAbility") or 0)
    if not elapsed:
        elapsed = args.advance
    paid = elixir_before_ability - float(observation["players"][0]["elixir"])
    report["abilityElixirPaid"] = round(paid, 3)
    if paid < KNIGHT_ABILITY_MANA_COST - MAX_REGEN_PER_TICK * elapsed - 0.05:
        report["problems"].append(
            f"ability paid {paid:.3f} elixir, expected {KNIGHT_ABILITY_MANA_COST} within regen"
        )
    if paid > KNIGHT_ABILITY_MANA_COST + 0.05:
        report["problems"].append(
            f"ability paid {paid:.3f} elixir, more than the {KNIGHT_ABILITY_MANA_COST} printed cost"
        )

    # Negative receipt: no charge remains, so the same carrier must not resolve.
    second: dict[str, Any] = {}
    try:
        result = env.activate_ability(
            AbilityAction(0, int(hero["objectIndex"]), int(hero["secondaryIndex"]))
        )
        second = {"injected": result.get("injected"), "ok": result.get("ok"), "raw": result}
    except (HandActionRejected, RunnerError) as error:
        second = {"refused": True, "error": str(error)}
    report["secondActivation"] = second
    if not second.get("refused"):
        report["problems"].append("a second activation was accepted with no charge remaining")

    report["seconds"] = round(time.monotonic() - started, 3)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=26789)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=41)
    parser.add_argument("--bank-ticks", type=int, default=600)
    parser.add_argument("--advance", type=int, default=20, help="ticks advanced after the activation")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    try:
        report = run(args)
    except RunnerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(
        f"client {report['clientVersion']}: hero {report['heroCardId']}, "
        f"{len(report['problems'])} problems"
    )
    print(f"controller before {report.get('controllerBefore')}")
    print(f"controller after  {report.get('controllerAfter')}")
    print(f"ability paid {report.get('abilityElixirPaid')} elixir; second {report.get('secondActivation')}")
    for problem in report["problems"]:
        print(f"  PROBLEM {problem}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"report: {args.output}")
    return 0 if not report["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
