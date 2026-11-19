"""Evaluate two checkpoints in complete, sequential offline native matches."""

import argparse
from contextlib import nullcontext
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

import torch

from ...battle_env import (
    ENTITY_LIMIT,
    EVENT_LIMIT,
    EVENT_WINDOW_TICKS,
    STANDARD_MATCH_TICKS,
    BattleEnvV1,
)
from ...contracts import ActionKind, ActionV1, EnvironmentConfigV1
from ...competitive_release import load_release, use_release
from ...cr_native_env import DEFAULT_HOST, DEFAULT_PORT, FIRST_PLAYABLE_TICK, NativeClashEnv
from ...match_factory import MatchConfig
from ...rich_telemetry_adapter import RuntimeEffectCatalog
from ...ruleset import build_native_ruleset_manifest, load_native_build_profile
from .action_window import separate_micro_action_ticks
from .expert import FIRST_POLICY_DECISION_TICK, POLICY_DECISION_TICKS
from .factory import production_semantic_bundle
from .policy_session import build_policy_session_v4, load_policy_v4


def evaluate(
    checkpoint_a,
    checkpoint_b,
    *,
    games,
    match,
    output,
    host=DEFAULT_HOST,
    port=DEFAULT_PORT,
    device="cpu",
    sample=False,
):
    if games < 1:
        raise ValueError("games must be positive")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    policies = [load_policy_v4(path, device=device) for path in (checkpoint_a, checkpoint_b)]
    # Both seats share one engine, and the engine's content build decides which
    # native catalog can resolve its entities.  Derive it from the checkpoints
    # (16.635 policies need the 16.635 catalogs) and refuse a mixed pairing.
    release_a = getattr(policies[0].model, "competitive_release", None)
    release_b = getattr(policies[1].model, "competitive_release", None)
    if release_a != release_b:
        raise ValueError(
            f"checkpoints use different competitive releases ({release_a!r} vs {release_b!r}); "
            "one native catalog must serve both seats"
        )
    bundle = production_semantic_bundle(release_a)
    config_a = getattr(policies[0].model, "config", None)
    config_b = getattr(policies[1].model, "config", None)
    if config_a is not None and config_b is not None and asdict(config_a) != asdict(config_b):
        raise ValueError("checkpoints use different model configs")
    native = NativeClashEnv(host, port, timeout=30.0)
    environment_config = EnvironmentConfigV1(
        warmup_ticks=FIRST_PLAYABLE_TICK,
        max_battle_ticks=STANDARD_MATCH_TICKS,
        entity_limit=ENTITY_LIMIT,
        event_limit=EVENT_LIMIT,
        event_window_ticks=EVENT_WINDOW_TICKS,
        shaping_beta=0.0,
        shaping_tau_ticks=1200.0,
        include_native_digest=True,
    )
    status_fn = getattr(native, "status", None)
    client_version = str(status_fn().get("clientVersion") or "") if callable(status_fn) else ""
    native_profile = load_native_build_profile(client_version) if client_version else None
    ruleset_manifest = None
    if native_profile is not None:
        # The A51 runs a build whose device identities differ from the local
        # catalog release. Bind that build explicitly so attestation stays
        # fail-closed instead of comparing against the 15.535 table.
        # The manifest resolves card logic, runtime facts and the semantic
        # subset through the *active* release, so a 16.635 checkpoint needs its
        # release bound here or the catalog hash check fails closed.
        with use_release(load_release(release_a)) if release_a else nullcontext():
            ruleset_manifest = build_native_ruleset_manifest(
                client_version=client_version,
                catalog=bundle.native_card_catalog,
                environment_config=environment_config,
            )
    # Verification resolves the *default* form evidence for whichever release
    # is active, so a 16.635 subset only validates inside its own release scope.
    with use_release(load_release(release_a)) if release_a else nullcontext():
        environment = BattleEnvV1(
            native=native,
            card_catalog=bundle.native_card_catalog,
            semantic_subset_contract=bundle.semantic_subset_contract,
            effect_catalog=RuntimeEffectCatalog.from_catalog(bundle.native_effect_catalog),
            ruleset_manifest=ruleset_manifest,
            warmup_ticks=FIRST_PLAYABLE_TICK,
            max_battle_ticks=STANDARD_MATCH_TICKS,
            entity_limit=ENTITY_LIMIT,
            event_limit=EVENT_LIMIT,
            event_window_ticks=EVENT_WINDOW_TICKS,
        )
    # The combat-ring card-play feed is a native-probe hook. crforge records its
    # own plays; asking it for an attested native ring fails closed at reset.
    # Builds whose probe omits the rich hook rings (16.635.1 today) must not
    # request it either: model actions already emit action-executed events.
    manifest = getattr(environment, "ruleset_manifest", None)
    combat_ring_hooks = True
    if native_profile is not None:
        combat_ring_hooks = bool(native_profile.get("combat_ring_hooks", False))
    if (manifest is None or manifest.config.get("engine") != "crforge") and combat_ring_hooks:
        native.public_card_play_events_from_combat_ring = True
    native_build_identity = None
    if manifest is not None and manifest.config.get("native_build"):
        native_build_identity = dict(manifest.config["native_build"])
    identity = {
        "games": games,
        "sample": sample,
        "match": asdict(match),
        "client_version": client_version,
        "ruleset_id": getattr(environment, "ruleset_id", None),
        "native_build": native_build_identity,
        "checkpoints": [
            {"path": str(p.checkpoint_path), "sha256": p.checkpoint_sha256, "checkpoint_id": p.checkpoint_id}
            for p in policies
        ],
    }
    (output / "identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
    rows = []
    sessions = []
    try:
        with (output / "matches.jsonl").open("w", encoding="utf-8", buffering=1) as log:
            for index in range(games):
                a_owner = index % 2
                current = replace(match, seed=match.seed + index)
                if a_owner:
                    current = replace(
                        current,
                        deck0=match.deck1,
                        deck1=match.deck0,
                        deck0_form_availability=match.deck1_form_availability,
                        deck1_form_availability=match.deck0_form_availability,
                        tower_troop0_id=match.tower_troop1_id,
                        tower_troop1_id=match.tower_troop0_id,
                    )
                torch.manual_seed(current.seed)
                observations, _ = environment.reset(
                    match_config=current,
                    options={
                        "render_mode": "headless",
                        "decision_ticks": POLICY_DECISION_TICKS,
                        "event_driven_decisions": False,
                        "allow_initial_remaining_runtime_rejections": True,
                    },
                )
                initial_elixir = {
                    owner: next(p.elixir_exact for p in obs.players if p.owner == owner)
                    for owner, obs in observations.items()
                }
                sessions = [
                    build_policy_session_v4(
                        environment,
                        policies[0 if owner == a_owner else 1].model,
                        actor_owner=owner,
                        device=device,
                        sample=sample,
                    )
                    for owner in (0, 1)
                ]
                for owner, session in enumerate(sessions):
                    session.start_episode(observations[owner], initial_elixir=initial_elixir)
                actions = [0, 0]
                tick_shifts = [0, 0]
                dropped_micro_actions = [0, 0]
                rejected: set[str] = set()
                rejected_examples: list[dict] = []
                rejected_total = 0
                rejected_reasons: dict[str, int] = {}
                terminated = truncated = False
                decisions = 0
                while not terminated and not truncated:
                    commands = {}
                    for owner, session in enumerate(sessions):
                        observation = observations[owner]
                        chosen = ()
                        if observation.tick >= FIRST_POLICY_DECISION_TICK:
                            # The engine runs one action per seat per tick; two
                            # micro-actions sampled onto one tick would leave the
                            # second card in hand and be attested as
                            # ``card_still_in_slot`` (FINDINGS.md §3.96).  Space
                            # them first, as the crforge producer does, and keep
                            # the counts so the artifact shows the difference.
                            chosen, shifted, dropped = separate_micro_action_ticks(
                                session.decide(observation).decoded.actions,
                                window=POLICY_DECISION_TICKS,
                            )
                            tick_shifts[owner] += shifted
                            dropped_micro_actions[owner] += dropped
                            actions[owner] += sum(a.kind != ActionKind.WAIT for a in chosen)
                        else:
                            session.tensorizer.tensorize(observation, validate=False)
                        commands[owner] = tuple(chosen) or (ActionV1.wait(owner, ticks=POLICY_DECISION_TICKS),)
                    observations, _, terms, truncs, _ = environment.step(commands, record_trace=False)
                    terminated, truncated = bool(terms["__all__"]), bool(truncs["__all__"])
                    decisions += 1
                    if decisions % 100 == 0:
                        print(
                            f"game {index + 1}/{games} tick {observations[0].tick} decisions {decisions}",
                            file=sys.stderr,
                            flush=True,
                        )
                    for observation in observations.values():
                        for event in observation.events:
                            if event.event_type == "action_rejected":
                                rejected_total += 1
                                # Two rejection shapes exist: a command refused before
                                # it was queued (`data.reason`) and a queued play the
                                # engine refused at its run tick (`data.rejection_diagnostic`).
                                # Name both; "unknown" hid a real learner-deck refusal.
                                payload = event.to_dict()
                                data = payload.get("data", {})
                                reason = data.get("reason")
                                if not reason:
                                    diagnostic = data.get("rejection_diagnostic") or {}
                                    run_name = diagnostic.get("native_run_refusal_name")
                                    if run_name:
                                        reason = f"native_run_{str(run_name).lower()}"
                                    elif diagnostic.get("same_card_still_in_slot"):
                                        reason = "card_still_in_slot"
                                reason = str(reason or "unknown")
                                rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
                                key = json.dumps(payload, sort_keys=True)
                                if key not in rejected and len(rejected_examples) < 3:
                                    # The same event repeats in every observation
                                    # window; keep the first few distinct payloads
                                    # so a refusal names its seat, card and hand.
                                    rejected_examples.append(payload)
                                rejected.add(key)
                terminal = observations[0].terminal
                winner = None if terminal.winner is None else ("a" if terminal.winner == a_owner else "b")
                illegal_by_owner: dict[int, int] = {}
                try:
                    final_raw = environment.native.observe()
                    for player in final_raw.get("players", ()):
                        illegal_by_owner[int(player.get("owner", -1))] = int(
                            player.get("illegal", 0) or 0
                        )
                except Exception:
                    # The native probe does not expose the crforge header's
                    # cumulative illegal counter; rejection events still do.
                    illegal_by_owner = {}
                row = {
                    "game": index + 1,
                    "seed": current.seed,
                    "a_owner": a_owner,
                    "winner": winner,
                    "terminated": terminated,
                    "truncated": truncated,
                    "tick": observations[0].tick,
                    "terminal": terminal.to_dict(),
                    "actions_by_owner": actions,
                    # The deck each owner actually played.  The seat swap is
                    # invisible in ``identity.match`` (which records the
                    # requested pairing), and a table that guesses it from
                    # ``a_owner`` has already been wrong once.
                    "decks_by_owner": [list(current.deck0), list(current.deck1)],
                    "illegal_by_owner": illegal_by_owner,
                    "illegal_total": sum(illegal_by_owner.values()),
                    "rejected_actions": len(rejected),
                    "rejected_total": rejected_total,
                    "rejected_reasons": rejected_reasons,
                    "rejected_examples": rejected_examples,
                    # Micro-actions moved off a colliding engine tick, and the
                    # ones a full window could not fit (never silently hidden).
                    "micro_action_tick_shifts": list(tick_shifts),
                    "micro_actions_dropped": list(dropped_micro_actions),
                }
                rows.append(row)
                log.write(json.dumps(row) + "\n")
                print(json.dumps(row), flush=True)
                for session in sessions:
                    session.end_episode()
                sessions = []
        result = {
            "requested": games,
            "completed": len(rows),
            "a_wins": sum(r["winner"] == "a" for r in rows),
            "b_wins": sum(r["winner"] == "b" for r in rows),
            "draws": sum(r["terminated"] and r["winner"] is None for r in rows),
            "truncated": sum(r["truncated"] for r in rows),
            "illegal_total": sum(r["illegal_total"] for r in rows),
            # Rejected commands are their own gate: they are not "illegal"
            # moves and must never be folded into a win/loss statistic.
            # ``rejected_total`` counts each event in every observation window
            # it is visible in; ``rejected_unique_total`` counts the distinct
            # refusals, which is the number a rollout gate should read.
            "rejected_total": sum(r["rejected_total"] for r in rows),
            "rejected_unique_total": sum(r["rejected_actions"] for r in rows),
            "games_with_rejections": sum(bool(r["rejected_total"]) for r in rows),
            "rejected_reasons": {
                reason: sum(r["rejected_reasons"].get(reason, 0) for r in rows)
                for reason in sorted({reason for r in rows for reason in r["rejected_reasons"]})
            },
            "micro_action_tick_shifts": sum(sum(r["micro_action_tick_shifts"]) for r in rows),
            "micro_actions_dropped": sum(sum(r["micro_actions_dropped"]) for r in rows),
        }
        (output / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    finally:
        for session in sessions:
            session.end_episode()
        native.close_transport()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-a", required=True)
    parser.add_argument("--checkpoint-b", required=True)
    parser.add_argument("--games", type=int, default=2)
    parser.add_argument("--match-config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--sample",
        action="store_true",
        help="sample from the policy instead of argmax; sparse-action checkpoints "
        "collapse to WAIT under argmax (docs/native/16.635.1/FINDINGS.md §3.27)",
    )
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    config = json.loads(args.match_config.read_text(encoding="utf-8")) if args.match_config else {}
    for key in ("owner0_account_id", "owner1_account_id"):
        config.pop(key, None)
    config["seed"] = args.seed
    torch.set_num_threads(1)
    evaluate(
        args.checkpoint_a,
        args.checkpoint_b,
        games=args.games,
        match=MatchConfig(**config),
        output=args.output,
        host=args.host,
        port=args.port,
        device=args.device,
        sample=args.sample,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
