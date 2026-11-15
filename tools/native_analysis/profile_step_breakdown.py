#!/usr/bin/env python3
"""Attribute the per-packet cost of ``BattleEnvV1.step`` on live x86 engines.

``profile_cluster_collection.py`` measures the transport floor with a scripted
WAIT policy and ``profile_real_collection.py`` measures the whole learner loop,
but both only report the *sum* of the observation build (~26 ms/packet outside
the channel) and tensorization (~14 ms).  Before rewriting the data path we need
to know which part of ``BattleEnvV1.step`` that is.

This tool boots nothing itself: start the engines first (one per core, ports
``26789+N``), then run it in the training shape - one ``ThreadPoolExecutor`` of
``--lanes`` threads per engine, exactly like ``_engine_worker``, so the
coordinator batches the same way the real collector does.

Two measurements are produced:

* ``sections`` - wall time inside every ``BattleEnvV1`` method that matters,
  instrumented on the class and accumulated per worker thread, divided by
  measured lane packets.  This says *where* the step time goes.
* ``cprofile`` - a sequential cProfile of one lane over ``--profile-steps``
  decisions, sorted by tottime.  This says *which functions* dominate inside a
  section (object construction, dataclass ``__init__``, JSON, tuples, ...).

The scripted policy plays a card every ``--play-every`` decisions when the mask
allows it and otherwise WAITs, so the event/pending bookkeeping is exercised at
roughly the training rate. ``--play-every 0`` gives the pure WAIT floor.

    # engines already listening on 26789..26790
    .venv/bin/python tools/native_analysis/profile_step_breakdown.py \
        --engines 1 --lanes 8 --steps 60 --profile-steps 40 \
        --output runs/step-breakdown/one-engine.json
"""

from __future__ import annotations

import argparse
import cProfile
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
import os
from pathlib import Path
import pstats
import resource
import statistics
import sys
import threading
import time
import traceback
from typing import Any, Mapping

import torch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from contextlib import nullcontext  # noqa: E402

from native_runner.battle_env import BattleEnvV1  # noqa: E402
from native_runner.competitive_release import load_release, use_release  # noqa: E402
from native_runner.contracts import ActionKind, ActionV1, TargetKind  # noqa: E402
from native_runner.training.v4.cluster_self_play import (  # noqa: E402
    build_resident_engine_v4,
    _reset_engine,
)
from native_runner.training.v4.policy_session import (  # noqa: E402
    model_config_from_mapping,
    resolve_checkpoint_release,
)
from native_runner.training.v4.ppo_runtime import ppo_environment_config_v4  # noqa: E402
from profile_cluster_collection import _assignments  # noqa: E402

DECISION_TICKS = 5
DEBUG_ERRORS = bool(os.environ.get("CR_STEP_BREAKDOWN_DEBUG"))

# Methods timed on the class.  Every wrapper is a ``perf_counter`` pair plus a
# dict update in thread-local storage, so the instrumentation is far below the
# millisecond times it measures.
TIMED_METHODS: tuple[str, ...] = (
    # the whole call
    "step",
    "observe",
    # native advance / telemetry merge
    "_advance_headless",
    "_advance_synchronized_native_render",
    "_atomic_observation_pair",
    "_rich_snapshot_for",
    "_prime_rich_cache",
    "_invalidate_rich_cache",
    # per-tick scene construction (cached across the two owners)
    "_rich_context",
    "_extract_towers",
    "_entities",
    "_visible_entity_count",
    "_raw_entity_map",
    # per-owner observation
    "_players",
    "_events_for",
    "action_mask",
    "_terminal",
    "_belief",
    # post-advance bookkeeping
    "_update_beliefs",
    "_resolve_pending",
    "_resolve_pending_abilities",
    "_derive_events",
    "_consume_native_public_card_plays",
    "_consume_native_phase_events",
    "_consume_native_visibility_events",
    "_consume_native_remaining_events",
    "_consume_native_combat_events",
    "_append_native_public_card_plays",
    "_rewards",
    # helpers that show up in the hot path
    "_native_form_aliases",
    "_hand_runtime_contract",
    "_cached_placement_entry",
    "_cached_runtime_placement_entry",
    "_ability_action_candidates",
    "_destroyed_enemy_lanes",
    "_active_tower_footprints",
    "_occupied_building_footprints",
    "_validate_compound_actions",
    "_emit_event",
    "_world_target",
    "_ticks_until_affordable",
)

# Sections that are called once per step at the top level.  ``observe`` and
# ``_advance_headless`` are added separately because they are the two big ones.
POST_ADVANCE_SECTIONS: tuple[str, ...] = (
    "_update_beliefs",
    "_resolve_pending",
    "_resolve_pending_abilities",
    "_derive_events",
    "_consume_native_public_card_plays",
    "_consume_native_phase_events",
    "_consume_native_visibility_events",
    "_consume_native_remaining_events",
    "_consume_native_combat_events",
    "_append_native_public_card_plays",
    "_rewards",
)


class _Timer:
    """Thread-local accumulation so no lock is needed on the hot path."""

    def __init__(self) -> None:
        self._local = threading.local()
        self._threads: dict[int, dict[str, list[float]]] = {}
        self._lock = threading.Lock()

    def _bucket(self) -> dict[str, list[float]]:
        local = self._local
        bucket = getattr(local, "bucket", None)
        if bucket is None:
            bucket = {}
            local.bucket = bucket
            with self._lock:
                self._threads[threading.get_ident()] = bucket
        return bucket

    def add(self, name: str, seconds: float) -> None:
        entry = self._bucket().get(name)
        if entry is None:
            self._bucket()[name] = [seconds, 1.0]
        else:
            entry[0] += seconds
            entry[1] += 1.0

    def totals(self) -> dict[str, dict[str, float]]:
        with self._lock:
            buckets = list(self._threads.values())
        merged: dict[str, list[float]] = {}
        for bucket in buckets:
            for name, (seconds, count) in bucket.items():
                target = merged.setdefault(name, [0.0, 0.0])
                target[0] += seconds
                target[1] += count
        return {
            name: {"seconds": seconds, "calls": count}
            for name, (seconds, count) in merged.items()
        }


def _install_stock_freeze() -> Any:
    """A/B control: restore the pre-2026-11-15 ``_freeze`` ordering."""

    from native_runner import contracts as contracts_module

    original = contracts_module._freeze

    def slow_freeze(value: Any) -> Any:
        if isinstance(value, contracts_module.FrozenMapping):
            return value
        if isinstance(value, Mapping):
            return contracts_module.FrozenMapping(value)
        if isinstance(value, (list, tuple)):
            return tuple(slow_freeze(item) for item in value)
        if isinstance(value, (set, frozenset)):
            return tuple(sorted((slow_freeze(item) for item in value), key=repr))
        return value

    contracts_module._freeze = slow_freeze
    return original


def _install_tower_trace(trace: dict[str, list[float]]) -> Any:
    """Classify ``_extract_towers`` calls as hit / miss / rich-hit / rich-miss.

    The method has two caches and rich callers bypass the ordinary one, so the
    per-packet total alone cannot say whether the cost is recomputation or
    bookkeeping.
    """

    original = BattleEnvV1._extract_towers

    def wrapper(self: Any, raw: Mapping[str, Any], *, rich_context: Any = None) -> Any:
        identity = BattleEnvV1._rich_identity(raw)
        if rich_context is not None:
            kind = "rich_hit" if identity in self._rich_tower_cache else "rich_miss"
        else:
            kind = "hit" if identity in self._ordinary_tower_cache else "miss"
        started = time.perf_counter()
        try:
            return original(self, raw, rich_context=rich_context)
        finally:
            entry = trace.setdefault(kind, [0.0, 0.0])
            entry[0] += time.perf_counter() - started
            entry[1] += 1.0

    BattleEnvV1._extract_towers = wrapper
    return original


def _install_timers(timer: _Timer) -> dict[str, Any]:
    """Wrap the timed methods, preserving static/class-method binding."""

    originals: dict[str, Any] = {}
    for name in TIMED_METHODS:
        raw = inspect.getattr_static(BattleEnvV1, name, None)
        if raw is None:
            continue
        originals[name] = raw
        kind = "method"
        if isinstance(raw, staticmethod):
            kind = "static"
            function = raw.__func__
        elif isinstance(raw, classmethod):
            kind = "class"
            function = raw.__func__
        else:
            function = raw

        # Bind the loop variables; a bare closure would time the last method
        # for every wrapper.
        def wrapper(*args: Any, __original: Any = function, __name: str = name, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return __original(*args, **kwargs)
            finally:
                timer.add(__name, time.perf_counter() - started)

        wrapper.__name__ = getattr(function, "__name__", name)
        wrapper.__doc__ = getattr(function, "__doc__", None)
        if kind == "static":
            setattr(BattleEnvV1, name, staticmethod(wrapper))
        elif kind == "class":
            setattr(BattleEnvV1, name, classmethod(wrapper))
        else:
            setattr(BattleEnvV1, name, wrapper)
    return originals


def _restore_timers(originals: Mapping[str, Any]) -> None:
    for name, original in originals.items():
        setattr(BattleEnvV1, name, original)


def _legal_play(observation: Any, owner: int) -> ActionV1 | None:
    """Pick a legal card play from an observation the environment already built.

    The mask is part of ``ObservationV1`` and was cached for this tick, so this
    adds no work outside the measured step.
    """

    mask = observation.action_mask
    if not bool(mask.kinds.get("play_card", False)):
        return None
    affordable = (mask.reasons.get("affordable_from_bin") or {}).get("slots") or {}
    for slot in range(4):
        if not mask.hand_slots[slot]:
            continue
        entry = mask.placement_masks.get(str(slot))
        if not isinstance(entry, Mapping):
            continue
        # ``hand_slots`` is touch-window legality: the card fits at some delay
        # bin of this decision.  A bin above zero must be carried on the action
        # or ``_validate_compound_actions`` reads it as an immediate overspend.
        bin_index = affordable.get(str(slot))
        bin_index = 0 if bin_index is None else int(bin_index)
        rows = entry.get("row_major") or ()
        for y, row in enumerate(rows):
            for x, legal in enumerate(row):
                if not legal:
                    continue
                delay_bin = min(4, max(0, bin_index))
                return ActionV1(
                    owner=owner,
                    kind=ActionKind.PLAY_CARD,
                    hand_slot=slot,
                    card_id=entry.get("visible_card_id"),
                    target_kind=TargetKind.GRID,
                    target_grid=(x, y),
                    execute_offset_ticks=1 + delay_bin,
                    next_decision_ticks=DECISION_TICKS,
                    metadata={
                        "policy_delay_offset_bin": delay_bin,
                        "policy_delay_offset_ms": 50 * delay_bin,
                        "base_latency_ticks": 1,
                        "base_latency_ms": 50.0,
                    },
                )
    return None


def _scripted_actions(
    observations: Mapping[int, Any],
    *,
    step_index: int,
    play_every: int,
) -> dict[int, ActionV1]:
    actions: dict[int, ActionV1] = {}
    play = play_every > 0 and step_index % play_every == 0
    for owner in (0, 1):
        observation = observations.get(owner)
        if observation is None:
            actions[owner] = ActionV1.wait(owner, ticks=DECISION_TICKS)
            continue
        action = _legal_play(observation, owner) if play else None
        actions[owner] = action or ActionV1.wait(owner, ticks=DECISION_TICKS)
    return actions


def _run_round(
    engine: Any,
    observations: dict[int, dict[int, Any]],
    *,
    step_index: int,
    play_every: int,
    executor: ThreadPoolExecutor,
    timings: list[float],
    lock: threading.Lock,
) -> None:
    """One decision round on one engine, in the worker's batch shape.

    ``_engine_worker`` decodes every lane first, calls ``begin_round``, submits
    the steps to a lane thread pool, and only then ``finish_round``.  Card plays
    are only accepted inside an active round, so this order is required, not
    cosmetic.
    """

    environments = engine.environments
    actions_by_lane = {
        index: _scripted_actions(observations[index], step_index=step_index, play_every=play_every)
        for index in range(len(environments))
    }

    def one(index: int) -> tuple[int, Any, float]:
        started = time.perf_counter()
        try:
            result = environments[index].step(
                actions_by_lane[index], record_trace=False, policy_features_only=True
            )
        except BaseException as error:  # noqa: BLE001 - surface the lane-local cause
            if DEBUG_ERRORS:
                traceback.print_exception(type(error), error, error.__traceback__)
                print(f"# lane {index} actions: {actions_by_lane[index]!r}", file=sys.stderr, flush=True)
            raise
        return index, result, time.perf_counter() - started

    engine.coordinator.begin_round(tuple(range(len(environments))))
    futures = [executor.submit(one, index) for index in range(len(environments))]
    try:
        updates = [future.result() for future in futures]
    except BaseException as error:  # noqa: BLE001 - mirror the worker's abort path
        engine.coordinator.abort_round(error)
        for future in futures:
            future.cancel()
        raise
    engine.coordinator.finish_round()
    with lock:
        for index, result, elapsed in updates:
            observations[index] = result[0]
            timings.append(elapsed)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deck-pool", type=Path, default=REPO_ROOT / "runs" / "deck-pool-a51.json")
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=REPO_ROOT / "checkpoints/General/checkpoint-step-00000460.pt",
        help="only used to resolve the competitive release and model config",
    )
    parser.add_argument("--release", help="override the release name resolved from the checkpoint")
    parser.add_argument("--engines", type=int, default=1)
    parser.add_argument("--engine-base-port", type=int, default=26_789)
    parser.add_argument("--lanes", type=int, default=8)
    parser.add_argument("--steps", type=int, default=60, help="measured decision rounds per lane")
    parser.add_argument("--warmup", type=int, default=4, help="discarded decision rounds per lane")
    parser.add_argument("--profile-steps", type=int, default=40, help="sequential cProfile decisions on lane 0")
    parser.add_argument(
        "--play-every",
        type=int,
        default=4,
        help="try a legal card play every N decisions; 0 = pure WAIT floor",
    )
    parser.add_argument("--battle-level", type=int, default=11)
    parser.add_argument("--deck-offset", type=int, default=0)
    parser.add_argument("--deck-source", choices=("pool", "focus"), default="pool")
    parser.add_argument("--card-keys", help="8 comma-separated card keys; pins both decks")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--top", type=int, default=35, help="cProfile rows to print")
    parser.add_argument("--profile-dump", type=Path, help="also write the raw cProfile stats here for callers analysis")
    parser.add_argument(
        "--trace-towers",
        action="store_true",
        help="classify _extract_towers calls as rich / cache-hit / cache-miss",
    )
    parser.add_argument(
        "--stock-freeze",
        action="store_true",
        help="A/B control: measure with the old contracts._freeze ordering",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if not 1 <= args.lanes <= 16:
        raise SystemExit("--lanes must be in 1..16")
    if args.steps < 1 or args.profile_steps < 0:
        raise SystemExit("--steps must be positive and --profile-steps non-negative")

    fast_freeze_original = _install_stock_freeze() if args.stock_freeze else None
    environment_config = ppo_environment_config_v4()
    release_name = args.release
    model_config = None
    if args.checkpoint.is_file():
        payload = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
        contract = payload["contract"]
        model_config = model_config_from_mapping(contract["model_config"], origin="step breakdown")
        if release_name is None:
            release_name, _bundle = resolve_checkpoint_release(contract)
    release_context = use_release(load_release(release_name)) if release_name else nullcontext()

    engines = []
    observations: dict[int, dict[int, dict[int, Any]]] = {}
    started = time.perf_counter()
    with release_context:
        for engine_index in range(args.engines):
            engine = build_resident_engine_v4(
                engine_index,
                environment_config=environment_config,
                host="127.0.0.1",
                base_port=args.engine_base_port,
                lanes=args.lanes,
                timeout=args.timeout,
                capture_mode="compact",
                competitive_release=release_name,
                model_config=model_config,
            )
            engine.prepare()
            assignments = _assignments(
                args.deck_pool,
                engine_index=engine_index,
                lanes=args.lanes,
                battle_level=args.battle_level,
                base_sequence=1,
                deck_offset=args.deck_offset,
                deck_source=args.deck_source,
                card_keys=args.card_keys,
            )
            runtimes = _reset_engine(engine, assignments, environment_config)
            engine.coordinator.start_session()
            engines.append(engine)
            observations[engine_index] = {
                index: runtime.observations for index, runtime in enumerate(runtimes)
            }
    boot_seconds = time.perf_counter() - started
    print(json.dumps({"event": "engines_ready", "engines": len(engines), "boot_seconds": boot_seconds}), flush=True)

    tower_trace: dict[str, list[float]] = {}
    tower_original = _install_tower_trace(tower_trace) if args.trace_towers else None
    timer = _Timer()
    originals = _install_timers(timer)
    threads_per_engine = max(1, args.lanes)
    executors = [
        ThreadPoolExecutor(max_workers=threads_per_engine, thread_name_prefix=f"profile-lane-{index}")
        for index in range(len(engines))
    ]
    step_timings: list[float] = []
    timing_lock = threading.Lock()

    def run_rounds(count: int, *, offset: int) -> None:
        for step_index in range(offset, offset + count):
            for engine_index, engine in enumerate(engines):
                _run_round(
                    engine,
                    observations[engine_index],
                    step_index=step_index,
                    play_every=args.play_every,
                    executor=executors[engine_index],
                    timings=step_timings,
                    lock=timing_lock,
                )

    try:
        run_rounds(args.warmup, offset=0)
        # Reset the accumulator: warmup also warms the per-tick caches, and the
        # measurement should be steady-state.
        timer = _Timer()
        _restore_timers(originals)
        originals = _install_timers(timer)
        cpu_before = resource.getrusage(resource.RUSAGE_SELF)
        measured_started = time.perf_counter()
        run_rounds(args.steps, offset=args.warmup)
        measured_seconds = time.perf_counter() - measured_started
        cpu_after = resource.getrusage(resource.RUSAGE_SELF)
        cpu_seconds = (cpu_after.ru_utime - cpu_before.ru_utime) + (
            cpu_after.ru_stime - cpu_before.ru_stime
        )
        totals = timer.totals()
        tower_summary = {
            kind: {"seconds": entry[0], "calls": entry[1]} for kind, entry in tower_trace.items()
        }

        profile_payload: dict[str, Any] = {}
        if args.profile_steps > 0:
            profiler = cProfile.Profile()
            profiler.enable()
            # cProfile only follows the thread that enables it, so this pass
            # runs lane 0 on the main thread.  The batch channel is
            # partitionable: a round may name a subset of the resident slots.
            engine0 = engines[0]
            environment = engine0.environments[0]
            profiled_steps = 0
            for step_index in range(args.warmup + args.steps, args.warmup + args.steps + args.profile_steps):
                if bool(getattr(environment, "_terminated", False)) or bool(
                    getattr(environment, "_truncated", False)
                ):
                    break
                actions = _scripted_actions(
                    observations[0][0],
                    step_index=step_index,
                    play_every=args.play_every,
                )
                engine0.coordinator.begin_round((0,))
                try:
                    result = environment.step(actions, record_trace=False, policy_features_only=True)
                except BaseException as error:  # noqa: BLE001 - mirror the worker's abort path
                    engine0.coordinator.abort_round(error)
                    raise
                engine0.coordinator.finish_round()
                observations[0][0] = result[0]
                profiled_steps += 1
            profiler.disable()
            if args.profile_dump is not None:
                args.profile_dump.parent.mkdir(parents=True, exist_ok=True)
                profiler.dump_stats(str(args.profile_dump))
            stats = pstats.Stats(profiler)
            stats.sort_stats("tottime")
            profile_payload = {
                "requested_steps": args.profile_steps,
                "measured_steps": profiled_steps,
                "total_seconds": stats.total_tt,
                "top": [],
            }
            # pstats stores (cc, nc, tt, ct, callers) per function key.
            top_rows = sorted(
                (
                    {
                        "function": f"{key[0].replace(str(REPO_ROOT) + '/', '')}:{key[2]}",
                        "calls": int(value[1]),
                        "tottime_ms": 1000.0 * float(value[2]),
                        "cumtime_ms": 1000.0 * float(value[3]),
                    }
                    for key, value in stats.stats.items()
                ),
                key=lambda row: row["tottime_ms"],
                reverse=True,
            )[: args.top]
            profile_payload["top"] = top_rows
            # Aggregate by source file: the rewrite decision is "which module
            # do we thin", not "which individual lambda".
            by_origin: dict[str, list[float]] = {}
            for key, value in stats.stats.items():
                path = key[0]
                if path.startswith("<") or path == "~":
                    origin = "<builtin>"
                else:
                    origin = path.replace(str(REPO_ROOT) + "/", "")
                entry = by_origin.setdefault(origin, [0.0, 0.0])
                entry[0] += float(value[2])
                entry[1] += float(value[3])
            profile_payload["by_origin"] = {
                origin: {"tottime_ms": 1000.0 * totals[0], "cumtime_ms": 1000.0 * totals[1]}
                for origin, totals in sorted(by_origin.items(), key=lambda item: item[1][0], reverse=True)
            }
    finally:
        _restore_timers(originals)
        for executor in executors:
            executor.shutdown(wait=True)
        for engine in engines:
            try:
                engine.close()
            except Exception:  # noqa: BLE001 - best-effort teardown of a profiler
                pass

    packets = args.steps * args.lanes * len(engines)
    per_packet_ms = {name: 1000.0 * entry["seconds"] / packets for name, entry in totals.items()}
    calls_per_packet = {name: entry["calls"] / packets for name, entry in totals.items()}
    ordered_ms = dict(sorted(per_packet_ms.items(), key=lambda item: item[1], reverse=True))

    observe_ms = per_packet_ms.get("observe", 0.0)
    advance_ms = per_packet_ms.get("_advance_headless", 0.0) + per_packet_ms.get(
        "_advance_synchronized_native_render", 0.0
    )
    post_ms = sum(per_packet_ms.get(name, 0.0) for name in POST_ADVANCE_SECTIONS)
    step_ms = per_packet_ms.get("step", 0.0)
    step_overhead_ms = max(0.0, step_ms - advance_ms - observe_ms - post_ms)

    summary = {
        "stock_freeze": bool(args.stock_freeze),
        "engines": len(engines),
        "lanes": args.lanes,
        "steps": args.steps,
        "play_every": args.play_every,
        "release": release_name,
        "packets": packets,
        "measured_seconds": measured_seconds,
        "packets_per_second": packets / measured_seconds if measured_seconds else 0.0,
        # Process CPU (not the engine process): the number that shows whether
        # lanes are overlapping work or serializing on the GIL.
        "cpu_seconds": cpu_seconds,
        "cpu_ms_per_packet": (1000.0 * cpu_seconds / packets) if packets else 0.0,
        "cpu_efficiency": (cpu_seconds / measured_seconds) if measured_seconds else 0.0,
        "boot_seconds": boot_seconds,
        "step_ms": {
            "mean": 1000.0 * statistics.fmean(step_timings) if step_timings else 0.0,
            "median": 1000.0 * statistics.median(step_timings) if step_timings else 0.0,
            "p90": 1000.0 * _percentile(step_timings, 0.90),
            "max": 1000.0 * max(step_timings) if step_timings else 0.0,
            "samples": len(step_timings),
        },
        "sections_ms_per_packet": ordered_ms,
        "calls_per_packet": calls_per_packet,
        "split_ms_per_packet": {
            "advance": advance_ms,
            "observe": observe_ms,
            "observe_share_of_step": (observe_ms / step_ms) if step_ms else 0.0,
            "post_advance": post_ms,
            "step_overhead": step_overhead_ms,
            "atomic_pair_may_nest_in_advance": per_packet_ms.get("_atomic_observation_pair", 0.0),
        },
        "tower_calls": tower_summary,
        "cprofile": profile_payload,
    }
    if profile_payload.get("top"):
        summary["cprofile"]["top"] = profile_payload["top"]

    print(json.dumps({key: value for key, value in summary.items() if key != "cprofile"}, indent=2, sort_keys=True))
    if profile_payload.get("top"):
        print("\n# cProfile tottime (one lane, sequential)")
        print(f"{'ms':>10} {'calls':>8}  function")
        for row in profile_payload["top"]:
            print(f"{row['tottime_ms']:>10.2f} {row['calls']:>8}  {row['function']}")
        print("\n# cProfile cumtime (one lane, sequential)")
        for row in sorted(profile_payload["top"], key=lambda item: item["cumtime_ms"], reverse=True)[: args.top]:
            print(f"{row['cumtime_ms']:>10.2f} {row['calls']:>8}  {row['function']}")

    if tower_original is not None:
        BattleEnvV1._extract_towers = tower_original
    if fast_freeze_original is not None:
        from native_runner import contracts as contracts_module

        contracts_module._freeze = fast_freeze_original
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
