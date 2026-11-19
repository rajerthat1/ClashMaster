"""C2: the resident adapter against the bridge, decision for decision.

Stage C2 of [`the crforge plan`](../the crforge plan) replaces the loopback
socket between ``BattleEnvV1`` and crforge with an in-process dispatch
(``native_runner/crforge_resident.py``).  Its gate is two claims:

1. **Parity:** 50+ steps identical to the bridge — observations, action masks,
   rewards and terminals.
2. **Throughput:** 2x the bridge's environment steps per second.

This tool measures both on the same host, in one process, with the same policy
and the same seed:

* the **resident** episode decides with a sampled policy (seeded per seat per
  decision), and every decision's fingerprints and chosen actions are recorded;
* the **bridge** episode runs a TCP ``Engine`` served on an ephemeral port and
  *replays* those recorded actions, so a policy sampling difference cannot be
  reported as an adapter difference;
* then both episodes are rebuilt and stepped with the recorded actions in
  alternating order, and only ``env.step`` is timed.  Paired ordering matters:
  this host's absolute numbers move ~15-30 % with background load.

The digest deliberately drops the per-episode UUID and the wall-clock capture
stamps, which are not state.

    .venv/bin/python tools/crforge/resident_parity.py \
        --decisions 60 --output runs/crforge-c2/resident-parity.json

Exit status is 0 only when every decision matches; the throughput ratio is
reported, never used to hide a divergence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / ".resource" / "crforge" / "python", _REPO_ROOT / "tools" / "crforge"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

DEFAULT_CHECKPOINT = str(_REPO_ROOT / ".resource" / "RoyaleFish" / "models" / "B-general-v275.pt")
VOLATILE_OBSERVATION_KEYS = ("episode_id",)


# ---- digests ---------------------------------------------------------------


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _observation_digest(observation: Any) -> str:
    """``ObservationV1`` content hash without the per-episode UUID or clock."""

    payload = observation.to_dict()
    for key in VOLATILE_OBSERVATION_KEYS:
        payload.pop(key, None)
    time_state = payload.get("time")
    if isinstance(time_state, dict):
        time_state.pop("capture_time_ms", None)
        time_state.pop("available_time_ms", None)
    return _digest(payload)


def _decision_record(
    environment: Any,
    observations: dict[int, Any],
    rewards: dict[int, float],
    terminated: dict[Any, bool],
    truncated: dict[Any, bool],
) -> dict[str, Any]:
    return {
        "tick": int(observations[0].tick),
        "native": _digest(dict(environment.raw_observation)),
        "owners": {
            str(owner): {
                "observation": _observation_digest(observation),
                "mask": _digest(observation.action_mask.to_dict()),
                "entities": len(observation.entities),
                "towers": len(observation.towers),
                "events": len(observation.events),
                "pending": len(observation.pending_actions),
            }
            for owner, observation in sorted(observations.items())
        },
        "rewards": {str(owner): float(value) for owner, value in sorted(rewards.items(), key=lambda item: str(item[0]))},
        "terminated": {str(key): bool(value) for key, value in sorted(terminated.items(), key=lambda item: str(item[0]))},
        "truncated": {str(key): bool(value) for key, value in sorted(truncated.items(), key=lambda item: str(item[0]))},
    }


# ---- episodes --------------------------------------------------------------


def _resident_episode(*, checkpoint: str, seed: int, deck=None, forms=None):
    from bridge_session import build_episode, initial_elixir

    from native_runner.crforge_resident import CrforgeResidentEnv

    environment, observations, sessions, close = build_episode(
        native=CrforgeResidentEnv(), checkpoint=checkpoint, seed=seed, deck=deck, forms=forms
    )
    starting = initial_elixir(observations)
    for owner, session in sessions.items():
        session.start_episode(observations[owner], initial_elixir=starting)
    return environment, observations, sessions, close


def _bridge_episode(*, checkpoint: str, seed: int, deck=None, forms=None):
    from bridge_session import build_episode, initial_elixir

    from crforge_bridge.engine import Engine
    from crforge_bridge.server import RunnerHandler, ThreadedServer
    from native_runner.cr_native_env import NativeClashEnv

    engine = Engine()
    engine.start()
    handler = type("BoundRunnerHandler", (RunnerHandler,), {"engine": engine})
    server = ThreadedServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    native = NativeClashEnv(host, int(port), timeout=30.0)
    # One socket for the episode, not one per command: the bridge's persistent
    # control session is the shape a bulk run uses.
    native._persistent_transport_requested = True
    environment, observations, sessions, close = build_episode(
        native=native, checkpoint=checkpoint, seed=seed, deck=deck, forms=forms
    )
    starting = initial_elixir(observations)
    for owner, session in sessions.items():
        session.start_episode(observations[owner], initial_elixir=starting)

    def shutdown() -> None:
        close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=10.0)

    return environment, observations, sessions, shutdown


def _decide(sessions: dict[int, Any], observations: dict[int, Any], index: int) -> dict[int, Any]:
    """A reproducible sampled action per seat (reseeded per decision and seat)."""

    import torch

    commands: dict[int, Any] = {}
    for owner, session in sessions.items():
        torch.manual_seed(90_000 + index * 10 + owner)
        commands[owner] = tuple(session.decide(observations[owner]).decoded.actions)
    return commands


def _play(environment, observations, sessions, *, decisions: int, lean: bool, replay=None):
    """Step ``decisions`` times, optionally replaying a recorded action list."""

    records: list[dict[str, Any]] = []
    actions: list[dict[int, Any]] = []
    for index in range(decisions):
        commands = dict(replay[index]) if replay is not None else _decide(sessions, observations, index)
        observations, rewards, terminated, truncated, _info = environment.step(
            commands, record_trace=False, policy_features_only=lean
        )
        records.append(_decision_record(environment, observations, rewards, terminated, truncated))
        actions.append({owner: tuple(value) for owner, value in commands.items()})
        if bool(terminated.get("__all__")) or bool(truncated.get("__all__")):
            break
    return records, actions


def _paired_timing(
    resident_env: Any, bridge_env: Any, *, lean: bool, replay: list[dict[int, Any]], rounds: int = 1
) -> tuple[list[float], list[float], list[float]]:
    """Time both legs in the same loop, alternating order, replaying one script.

    Both environments are fresh (same seed), so replaying decision ``index`` on
    both is the same state transition.  Alternating the order cancels the host's
    drift over the round instead of attributing it to one leg.  The per-decision
    ratio is the paired estimator: this host's absolute ms move ~2x with load, so
    a ratio of medians is not a comparison.
    """

    resident_ms: list[float] = []
    bridge_ms: list[float] = []
    ratios: list[float] = []
    # Warm both JIT paths before any timed step.
    for index in range(min(8, len(replay))):
        for environment in (resident_env, bridge_env):
            environment.step(dict(replay[index]), record_trace=False, policy_features_only=lean)
    resident_stop = bridge_stop = False
    for _round in range(rounds):
        for index in range(len(replay)):
            order = (0, 1) if index % 2 == 0 else (1, 0)
            pair: dict[int, float] = {}
            for which in order:
                environment = resident_env if which == 0 else bridge_env
                finished = resident_stop if which == 0 else bridge_stop
                if finished:
                    continue
                start = time.perf_counter()
                _obs, _rewards, terminated, truncated, _info = environment.step(
                    dict(replay[index]), record_trace=False, policy_features_only=lean
                )
                elapsed = (time.perf_counter() - start) * 1000
                pair[which] = elapsed
                if which == 0:
                    resident_ms.append(elapsed)
                    resident_stop = bool(terminated.get("__all__")) or bool(truncated.get("__all__"))
                else:
                    bridge_ms.append(elapsed)
                    bridge_stop = bool(terminated.get("__all__")) or bool(truncated.get("__all__"))
            if 0 in pair and 1 in pair and pair[0] > 0:
                ratios.append(pair[1] / pair[0])
            if resident_stop and bridge_stop:
                return resident_ms, bridge_ms, ratios
    return resident_ms, bridge_ms, ratios


def _diff_paths(reference: Any, candidate: Any, *, prefix: str = "", limit: int = 8) -> list[str]:
    """The JSON paths that differ, shallowest first (for a readable artifact)."""

    paths: list[str] = []
    if len(paths) >= limit:
        return paths
    if isinstance(reference, dict) and isinstance(candidate, dict):
        for key in sorted(set(reference) | set(candidate)):
            if len(paths) >= limit:
                break
            child = f"{prefix}.{key}" if prefix else str(key)
            if key not in reference or key not in candidate:
                paths.append(child)
                continue
            paths.extend(_diff_paths(reference[key], candidate[key], prefix=child, limit=limit - len(paths)))
        return paths
    if isinstance(reference, list) and isinstance(candidate, list):
        if len(reference) != len(candidate):
            paths.append(f"{prefix}[len={len(reference)}!={len(candidate)}]")
        for index, (left, right) in enumerate(zip(reference, candidate)):
            if len(paths) >= limit:
                break
            paths.extend(_diff_paths(left, right, prefix=f"{prefix}[{index}]", limit=limit - len(paths)))
        return paths
    if reference != candidate:
        paths.append(prefix or "<root>")
    return paths


def _compare(reference: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    for index, (left, right) in enumerate(zip(reference, candidate)):
        if left == right:
            continue
        differing = [key for key in left if left[key] != right.get(key)]
        mismatches.append(
            {
                "decision": index,
                "fields": differing,
                "paths": _diff_paths(left, right),
                "reference": left,
                "candidate": right,
            }
        )
        if len(mismatches) >= 3:
            break
    return {
        "decisions_compared": min(len(reference), len(candidate)),
        "identical": not mismatches and len(reference) == len(candidate),
        "mismatches": mismatches,
    }


# ---- main ------------------------------------------------------------------


def run(
    *,
    checkpoint: str = DEFAULT_CHECKPOINT,
    seed: int = 1,
    decisions: int = 60,
    lean: bool = True,
    deck_name: str = "evaluator",
    output: str | Path | None = None,
) -> dict[str, Any]:
    import torch
    from bridge_session import DECK, FORM_AVAILABILITY, LEARNER_DECK, LEARNER_FORM_AVAILABILITY

    if deck_name == "learner":
        deck = LEARNER_DECK
        forms = LEARNER_FORM_AVAILABILITY
    else:
        deck = DECK
        forms = FORM_AVAILABILITY

    torch.set_num_threads(1)

    # 1. The resident leg decides; its actions become the shared script.
    resident_env, resident_obs, resident_sessions, resident_close = _resident_episode(
        checkpoint=checkpoint, seed=seed, deck=deck, forms=forms
    )
    try:
        resident_records, replay = _play(
            resident_env, resident_obs, resident_sessions, decisions=decisions, lean=lean
        )
    finally:
        resident_close()

    # 2. The bridge leg replays those actions over the wire.
    bridge_env, bridge_obs, bridge_sessions, bridge_close = _bridge_episode(
        checkpoint=checkpoint, seed=seed, deck=deck, forms=forms
    )
    try:
        bridge_records, _ = _play(
            bridge_env, bridge_obs, bridge_sessions, decisions=decisions, lean=lean, replay=replay
        )
    finally:
        bridge_close()

    parity = _compare(resident_records, bridge_records)

    # 3. Paired timing on fresh episodes, same script, alternating legs.
    resident_env, resident_obs, resident_sessions, resident_close = _resident_episode(
        checkpoint=checkpoint, seed=seed, deck=deck, forms=forms
    )
    bridge_env, bridge_obs, bridge_sessions, bridge_close = _bridge_episode(
        checkpoint=checkpoint, seed=seed, deck=deck, forms=forms
    )
    resident_ms: list[float] = []
    bridge_ms: list[float] = []
    paired: list[float] = []
    try:
        resident_ms, bridge_ms, paired = _paired_timing(
            resident_env, bridge_env, lean=lean, replay=replay
        )
    finally:
        resident_close()
        bridge_close()

    def summary(samples: list[float]) -> dict[str, float]:
        if not samples:
            return {"median_ms": 0.0, "mean_ms": 0.0, "decisions_per_s": 0.0, "samples": 0}
        median = statistics.median(samples)
        return {
            "median_ms": round(median, 3),
            "mean_ms": round(statistics.mean(samples), 3),
            "p90_ms": round(statistics.quantiles(samples, n=10)[8], 3) if len(samples) >= 10 else round(median, 3),
            "decisions_per_s": round(1000.0 / median, 1) if median else 0.0,
            "samples": len(samples),
        }

    resident_summary = summary(resident_ms)
    bridge_summary = summary(bridge_ms)
    ratio = (
        bridge_summary["median_ms"] / resident_summary["median_ms"]
        if resident_summary["median_ms"] and bridge_summary["median_ms"]
        else 0.0
    )
    paired_median = statistics.median(paired) if paired else 0.0

    report = {
        "schema": "crforge-resident-parity.v1",
        "checkpoint": checkpoint,
        "seed": seed,
        "deck_name": deck_name,
        "deck": list(deck),
        "form_availability": list(forms),
        "decisions_requested": decisions,
        "observation_mode": "lean" if lean else "validated",
        "parity": parity,
        "throughput": {
            "resident": resident_summary,
            "bridge": bridge_summary,
            "speedup_median": round(ratio, 2),
            "speedup_paired_median": round(paired_median, 2),
            "speedup_paired_pairs": len(paired),
            "gate": {"decisions_identical": 50, "speedup": 2.0},
            "passes_parity": parity["identical"] and parity["decisions_compared"] >= 50,
            "passes_throughput": paired_median >= 2.0,
        },
    }
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C2 resident-adapter parity and throughput gate")
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--decisions", type=int, default=60)
    parser.add_argument("--validated", action="store_true", help="use the validating observation path")
    parser.add_argument(
        "--deck",
        choices=("evaluator", "learner"),
        default="evaluator",
        help="evaluator deck (the C0 soak shape) or the locked X-Bow learner deck",
    )
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    report = run(
        checkpoint=args.checkpoint,
        seed=args.seed,
        decisions=args.decisions,
        lean=not args.validated,
        deck_name=args.deck,
        output=args.output,
    )
    parity = report["parity"]
    throughput = report["throughput"]
    print(
        f"parity: {parity['decisions_compared']} decisions, "
        f"{'identical' if parity['identical'] else 'DIVERGED'}"
    )
    if not parity["identical"]:
        first = parity["mismatches"][0] if parity["mismatches"] else {}
        print(f"  first divergence at decision {first.get('decision')}: fields {first.get('fields')}")
    print(
        f"throughput: resident {throughput['resident']['median_ms']:.2f} ms "
        f"({throughput['resident']['decisions_per_s']:.1f} decisions/s), "
        f"bridge {throughput['bridge']['median_ms']:.2f} ms "
        f"({throughput['bridge']['decisions_per_s']:.1f} decisions/s), "
        f"paired {throughput['speedup_paired_median']:.2f}x "
        f"(ratio of medians {throughput['speedup_median']:.2f}x, {throughput['speedup_paired_pairs']} pairs)"
    )
    print(
        f"gates: parity {'pass' if throughput['passes_parity'] else 'FAIL'}, "
        f"throughput {'pass' if throughput['passes_throughput'] else 'FAIL'}"
    )
    return 0 if parity["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
