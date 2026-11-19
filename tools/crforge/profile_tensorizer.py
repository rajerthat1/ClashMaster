"""Attribute ``UniversalObservationTensorizerV4.tensorize`` on a bridge episode.

The C1 spike (``tools/crforge/encoder_spike.py``) showed where the per-decision
packet goes on the existing bridge path: the vectorized observation facts are
~0.03 ms/lane and ``BattleEnvV1.observe`` is under a millisecond, while
``UniversalObservationTensorizerV4.tensorize`` is the largest remaining row.  So
stage C3 is "make the tensorizer cheaper", and C3 needs the same thing C0-C2 got:
a section breakdown first, so effort goes to the dominant rows instead of to
whatever looks vectorizable.

This tool runs one live crforge bridge episode with a real V4 policy (the C0
soak's shape: the evaluator deck, both seats, sampled decoding), captures each
decision's observation, the decoded action and the tensorized batch, then
replays the *tensorizer half* of the decision -- ``tensorize`` plus
``record_action`` -- under ``cProfile``.  Replaying is deliberate: it removes the
model forward and the engine round trip, so the profile is the tensorizer's own
cost, and the same decision sequence can be replayed against a candidate fast
path later.

A policy is required rather than a scripted play because the public tracker is
only consistent when every played card is recorded: replaying observations
without their actions trips the hand/cycle partition check, which is exactly the
kind of silent difference C3 must not learn around.

Output:

* ``timed`` - ms/decision for the replay (best and median over repeats)
* ``sections`` - cumulative ms/decision per tensorizer method
* ``modules`` - self ms/decision by source module
* ``top_cumulative`` / ``top_self`` - the cProfile rows that matter

    .venv/bin/python tools/crforge/profile_tensorizer.py \
        --port 26789 --decisions 200 --profile-decisions 120 \
        --output runs/crforge-c3/tensorizer-profile.json

``--resident`` replaces the socket with the in-process adapter, and ``--deck
learner`` measures the locked X-Bow list (the C1/C3 gate the evaluator deck
alone cannot certify).
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import pstats
import statistics
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / ".resource" / "crforge" / "python"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
_TOOL_DIR = Path(__file__).resolve().parent
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import torch
from bridge_session import (
    DECK,
    FORM_AVAILABILITY,
    LEARNER_DECK,
    LEARNER_FORM_AVAILABILITY,
    TOWER_TROOP,
    build_episode,
    initial_elixir,
)

from crforge_bridge.vector_tensorize import batch_digest

# Methods on the tensorizer are the sections C3 can attack.
_TENSORIZER_SECTIONS = (
    "_card_sets",
    "_towers",
    "_groups",
    "_active_effects",
    "_events",
    "_candidates",
    "_relations",
    "_spatial_planes",
    "_scalars",
    "_remember_public_evolution_forms",
    "_card_runtime_features",
    "_entity_spatial_extent",
    "_entity_archetype_id",
    "_entity_child_kind",
    "_validate_episode_step",
    "tensorize",
    "record_action",
    "reconcile_owner_private_state",
    "update",
)


def _capture(
    environment, observations, sessions, *, decisions: int
) -> tuple[list[tuple[Any, Any]], dict[int, float], list[int]]:
    """Drive both seats and capture owner 0's ``(observation, action_tensors)``.

    ``PolicySessionV4.decide`` keeps the public tracker consistent by recording
    every played card, but it does not expose the ``ActionSequenceV4`` it
    recorded, so ``record_action`` is spied for the duration of the capture.
    Both seats' initial elixir is needed by the tracker, and each owner's exact
    elixir only exists in that owner's own observation.
    """

    tensorizer = sessions[0].tensorizer
    starting_elixir = initial_elixir(observations)

    seen: list[Any] = []
    original = tensorizer.record_action

    def spy(actions: Any, *arguments: Any, **keywords: Any) -> Any:
        seen.append(actions)
        return original(actions, *arguments, **keywords)

    captured: list[tuple[Any, Any]] = []
    entity_counts: list[int] = []
    for owner, session in sessions.items():
        session.start_episode(observations[owner], initial_elixir=starting_elixir)
    tensorizer.record_action = spy
    try:
        for _ in range(decisions):
            observation = observations[0]
            entity_counts.append(len(observation.entities))
            commands = {}
            for owner, session in sessions.items():
                seen.clear()
                decision = session.decide(observations[owner])
                commands[owner] = tuple(decision.decoded.actions)
                if owner == 0:
                    captured.append((observation, seen[-1] if seen else None))
            observations, _, terminated, truncated, _ = environment.step(
                commands, record_trace=False
            )
            if bool(terminated["__all__"]) or bool(truncated["__all__"]):
                break
    finally:
        tensorizer.record_action = original
    return captured, starting_elixir, entity_counts


def _capture_digest(captured: Sequence[tuple[Any, Any]]) -> dict[str, Any]:
    """A cheap identity for a captured sequence, so an A/B replay is provable."""

    plays = sum(1 for _observation, actions in captured if actions is not None)
    return {
        "decisions": len(captured),
        "recorded_actions": plays,
        "first_tick": captured[0][0].tick,
        "last_tick": captured[-1][0].tick,
        "entity_total": sum(len(observation.entities) for observation, _ in captured),
        "pending_total": sum(len(observation.pending_actions) for observation, _ in captured),
        "event_total": sum(len(observation.events) for observation, _ in captured),
    }


def _replay(
    tensorizer,
    captured: Sequence[tuple[Any, Any]],
    initial_elixir: dict[int, float],
    *,
    digests: list[str] | None = None,
) -> None:
    """Replay ``tensorize`` + ``record_action`` in the captured order."""

    first = captured[0][0]
    tensorizer.reset(first, initial_elixir=initial_elixir)
    for observation, actions in captured:
        batch = tensorizer.tensorize(observation, validate=False)
        if digests is not None:
            digests.append(batch_digest(batch))
        if actions is not None:
            tensorizer.record_action(actions, batch, row=0, validate=False)


def _time_replay(
    tensorizer,
    captured: Sequence[tuple[Any, Any]],
    initial_elixir: dict[int, float],
    *,
    repeats: int,
) -> dict[str, Any]:
    totals: list[float] = []
    cpu_totals: list[float] = []
    for _ in range(repeats):
        before = time.perf_counter()
        cpu_before = time.process_time()
        _replay(tensorizer, captured, initial_elixir)
        cpu_totals.append(time.process_time() - cpu_before)
        totals.append(time.perf_counter() - before)
    best = min(totals)
    best_cpu = min(cpu_totals)
    decisions = len(captured)
    # CPU ms/decision is the portable number on this host (the session notes: absolute
    # wall time moves ~2x with background load, CPU time does not).
    return {
        "decisions": decisions,
        "ms_per_decision": best / decisions * 1_000.0,
        "median_ms_per_decision": statistics.median(totals) / decisions * 1_000.0,
        "cpu_ms_per_decision": best_cpu / decisions * 1_000.0,
        "median_cpu_ms_per_decision": statistics.median(cpu_totals) / decisions * 1_000.0,
        "repeats": repeats,
    }


def _profile(
    tensorizer,
    captured: Sequence[tuple[Any, Any]],
    initial_elixir: dict[int, float],
    *,
    dump_path: Path | None = None,
) -> dict[str, Any]:
    profiler = cProfile.Profile()
    profiler.enable()
    _replay(tensorizer, captured, initial_elixir)
    profiler.disable()
    if dump_path is not None:
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        profiler.dump_stats(str(dump_path))
    stats = pstats.Stats(profiler)
    decisions = len(captured)

    sections: dict[str, dict[str, float]] = {}
    modules: dict[str, float] = {}
    entries = []
    for key, value in stats.stats.items():
        filename, line, function = key
        cumulative = value[3] / decisions * 1_000.0
        total = value[2] / decisions * 1_000.0
        entries.append(
            {
                "function": function,
                "file": Path(filename).name,
                "line": line,
                "cumulative_ms": cumulative,
                "self_ms": total,
                "calls": value[1] / decisions,
            }
        )
        module = Path(filename).name
        modules[module] = modules.get(module, 0.0) + total
        if function in _TENSORIZER_SECTIONS:
            name = f"{module}:{function}"
            row = sections.setdefault(
                name, {"cumulative_ms": 0.0, "self_ms": 0.0, "calls": 0.0}
            )
            # A section can appear through several callers; keep the largest
            # single cumulative frame so nesting is not double counted.
            row["cumulative_ms"] = max(row["cumulative_ms"], cumulative)
            row["self_ms"] += total
            row["calls"] += value[1] / decisions

    entries.sort(key=lambda row: row["cumulative_ms"], reverse=True)
    return {
        "decisions": decisions,
        "sections": sections,
        "modules": dict(sorted(modules.items(), key=lambda item: item[1], reverse=True)),
        "top_cumulative": entries[:25],
        "top_self": sorted(entries, key=lambda row: row["self_ms"], reverse=True)[:25],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--port", type=int, default=26789)
    parser.add_argument(
        "--resident",
        action="store_true",
        help="use the in-process resident adapter instead of the socket bridge",
    )
    parser.add_argument(
        "--deck",
        choices=("evaluator", "learner"),
        default="evaluator",
        help="evaluator deck (the C0 soak shape) or the locked X-Bow learner deck",
    )
    parser.add_argument(
        "--checkpoint", default=".resource/RoyaleFish/models/B-general-v275.pt"
    )
    parser.add_argument("--decisions", type=int, default=200)
    parser.add_argument("--profile-decisions", type=int, default=120)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--profile-dump",
        type=Path,
        default=None,
        help="write the raw cProfile dump for callee analysis",
    )
    args = parser.parse_args(argv)

    torch.set_num_threads(1)
    if args.deck == "learner":
        deck = LEARNER_DECK
        forms = LEARNER_FORM_AVAILABILITY
    else:
        deck = DECK
        forms = FORM_AVAILABILITY
    native = None
    if args.resident:
        from native_runner.crforge_resident import CrforgeResidentEnv

        native = CrforgeResidentEnv()
    environment, observations, sessions, close = build_episode(
        port=None if args.resident else args.port,
        native=native,
        checkpoint=args.checkpoint,
        seed=args.seed,
        deck=deck,
        forms=forms,
    )
    try:
        captured, starting_elixir, entity_counts = _capture(
            environment, observations, sessions, decisions=args.decisions
        )
    finally:
        close()

    tensorizer = sessions[0].tensorizer
    _replay(tensorizer, captured[: args.warmup], starting_elixir)

    timed = _time_replay(tensorizer, captured, starting_elixir, repeats=args.repeats)
    digests: list[str] = []
    _replay(tensorizer, captured, starting_elixir, digests=digests)
    # ``--profile-decisions 0`` means "timing only": cProfile costs several
    # times the work it measures, so an A/B timing run skips it.
    profile_set = captured[: args.profile_decisions] if args.profile_decisions > 0 else []
    profiled = (
        _profile(tensorizer, profile_set, starting_elixir, dump_path=args.profile_dump)
        if profile_set
        else None
    )

    report = {
        "tool": "crforge-tensorizer-profile.v1",
        "port": None if args.resident else args.port,
        "transport": "resident" if args.resident else "bridge",
        "checkpoint": args.checkpoint,
        "deck": list(deck),
        "deck_name": args.deck,
        "form_availability": list(forms),
        "tower_troop": TOWER_TROOP,
        "seed": args.seed,
        "session_decisions": len(captured),
        "capture": _capture_digest(captured),
        "batch_digests": digests,
        "batch_digest": hashlib.sha256("".join(digests).encode()).hexdigest(),
        "entity_count": {
            "min": min(entity_counts),
            "median": statistics.median(entity_counts),
            "max": max(entity_counts),
        },
        "timed": timed,
        "profile": profiled,
    }
    print(
        f"tensorize+record_action {timed['ms_per_decision']:.3f} ms/decision "
        f"(cpu {timed['cpu_ms_per_decision']:.3f}, median {timed['median_ms_per_decision']:.3f}), "
        f"entities median {report['entity_count']['median']:.0f} "
        f"max {report['entity_count']['max']}",
        flush=True,
    )
    if profiled is not None:
        print("sections (cumulative ms/decision):", flush=True)
        for name, row in sorted(
            profiled["sections"].items(), key=lambda item: item[1]["cumulative_ms"], reverse=True
        ):
            print(
                f"  {name:<56} {row['cumulative_ms']:7.3f}  self {row['self_ms']:6.3f}",
                flush=True,
            )
        print("top self:", flush=True)
        for row in profiled["top_self"][:12]:
            print(
                f"  {row['file']}:{row['function']:<40} self {row['self_ms']:6.3f}  cum {row['cumulative_ms']:7.3f}",
                flush=True,
            )

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
