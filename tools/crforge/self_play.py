"""C4 producer: batched crforge self-play for the V4 policy.

This is the loop the C3-B tensorize and the batched action decode exist for.  It
plays one ``VectorBattle`` of ``--count`` matches in lockstep: every five native
ticks it encodes the facts for every lane, tensorizes both actor seats, stacks
the ``count * 2`` rows into one batch, runs one recurrent forward, decodes both
seats' sampled actions back into engine indices, and steps the vector.

Shape choices, all recorded on the artifact:

* **The engine steps one tick at a time** (``LadderConfig(decision_ticks=1)``)
  while the policy decides every five ticks.  The vector action index carries
  one action per seat per step, so a second V4 micro-action can only be honoured
  by giving it at its own tick inside the decision window; a five-tick engine
  step would silently drop it.  ``plan_window_ticks`` owns that mapping: the
  action is given at its delay bin, shifted a tick later on a collision, and
  counted as dropped if the window is full.
* **One recurrent state across both seats**, ``count * 2`` rows in
  ``lane * 2 + seat`` order.  ``episode_start`` is set per row for lanes the
  vector autoreset, and the tensorizer's previous-action record is cleared for
  exactly those lanes.
* **The previous action is recorded every decision** (``record_action``), so the
  model consumes the same training-shaped record a PPO update would.
* **Deploy candidates only** (the bridge envelope; ``FINDINGS.md`` §3.56): no
  ability source and no Mirror runtime yet.

This tool is a **collection/throughput probe**, not yet a PPO update: it does
not store a rollout or compute returns.  It measures the packet the C4 gate
asks for — env-steps/s, tensorize ms, inference ms, decode ms — and counts
engine faults, illegal actions and the eligible-ACT rate.

    .venv/bin/python tools/crforge/self_play.py \
        --count 8 --segments 10 --decisions-per-segment 64 \
        --output runs/crforge-c4/self-play.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / ".resource" / "crforge" / "python"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import numpy as np
import torch

from crforge_bridge.catalog import Catalog
from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
from crforge_bridge.jvm import start as start_jvm
from crforge_bridge.vector_actions import CANDIDATE_DEPLOY, VectorActionDecoder, plan_window_ticks
from crforge_bridge.vector_observation import VectorObservationEncoder
from crforge_bridge.vector_tensorize import VectorTensorizer
from native_runner.contracts import DEFAULT_TICK_MS as TICK_MS
from native_runner.cr_native_env import FIRST_PLAYABLE_TICK
from native_runner.timeline import load_game_mode_timeline
from native_runner.training.v4.factory import deck_roles_from_form_availability, production_semantic_bundle
from native_runner.training.v4.policy_session import load_policy_v4
from native_runner.training.v4.tensorizer import UniversalObservationTensorizerV4
from native_runner.training.v4.tensors import ActionSequenceV4, tensor_padding_value

LEARNER_DECK = (27000008, 27000006, 26000001, 26000000, 26000010, 26000084, 28000000, 28000011)
LEARNER_FORMS = (0, 1, 1, 2, 0, 0, 0, 0)
LEARNER_NAMES = ("Xbow", "Tesla", "Archer", "Knight", "Skeletons", "ElectroSpirit", "Fireball", "Log")
TOWER_TROOP = 159000000
STANDARD_GAME_MODE = 72_000_006
# One match is ~6,068 ticks at five-tick decisions.
MATCH_DECISIONS = 1_214


def _interleave(records: Sequence[Any], *, fill: int = 0) -> Any:
    """Stack one record per actor seat into ``lane * seats + seat`` row order.

    The seats' variable-length sections (relation edges, active effects) need a
    common width before they can be stacked; the mask is what makes the padding
    inert for the model, exactly as it is inside one seat's batch.
    """

    first = records[0]
    if isinstance(first, torch.Tensor):
        target = tuple(max(int(record.shape[axis]) for record in records) for axis in range(1, first.ndim))
        padded = []
        for record in records:
            if tuple(record.shape[1:]) == target:
                padded.append(record)
                continue
            output = record.new_full((int(record.shape[0]), *target), fill)
            slices = (slice(None),) + tuple(slice(0, int(size)) for size in record.shape[1:])
            output[slices] = record
            padded.append(output)
        stacked = torch.stack(padded, dim=1)
        return stacked.reshape(int(first.shape[0]) * len(records), *target)
    if is_dataclass(first):
        return type(first)(
            **{
                item.name: _interleave(
                    [getattr(record, item.name) for record in records],
                    fill=tensor_padding_value(first, item.name),
                )
                for item in fields(first)
            }
        )
    raise TypeError(f"cannot interleave {type(first).__name__}")


def _seat_rows(actions: ActionSequenceV4, index: int, seats: int) -> ActionSequenceV4:
    """One actor seat's rows of an interleaved action batch."""

    return ActionSequenceV4(
        **{item.name: getattr(actions, item.name)[index::seats] for item in fields(actions)}
    )


def _stock_tensorizer(bundle: Any, config: Any, seat: int) -> UniversalObservationTensorizerV4:
    return UniversalObservationTensorizerV4(
        actor_owner=int(seat),
        card_catalog=bundle.card_catalog,
        ability_catalog=bundle.ability_catalog,
        entity_archetype_catalog=bundle.entity_archetype_catalog,
        effect_catalog=bundle.effect_catalog,
        card_specs=bundle.card_specs,
        deck=LEARNER_DECK,
        tracker=None,
        deck_roles=deck_roles_from_form_availability(LEARNER_DECK, LEARNER_FORMS),
        config=config,
        reject_unknown_public_semantics=True,
    )


def _vector(count: int, *, max_ticks: int) -> Any:
    from crforge_env.vector import LadderConfig, SideSetup, VectorBattle

    setup = SideSetup(towers="King_PrincessTowers", king_level=16, tower_level=16)
    vector = VectorBattle(
        count,
        1,
        install=str(DEFAULT_INSTALL),
        tables=str(DEFAULT_TABLES),
        config=LadderConfig(decision_ticks=1, level=16, tower_level=16, max_ticks=max_ticks or None),
    )
    vector.reset(
        list(range(1, count + 1)),
        list(LEARNER_NAMES),
        list(LEARNER_NAMES),
        list(LEARNER_FORMS),
        list(LEARNER_FORMS),
        setup0=setup,
        setup1=setup,
    )
    return vector


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--segments", type=int, default=10)
    parser.add_argument("--decisions-per-segment", type=int, default=64)
    parser.add_argument("--seats", type=int, nargs="+", default=[0, 1], choices=(0, 1))
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--warmup-ticks", type=int, default=FIRST_PLAYABLE_TICK)
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=0,
        help="episode cut at this tick (0 = the engine's full match); a short value exercises autoreset",
    )
    parser.add_argument("--checkpoint", type=Path, default=Path(".resource/RoyaleFish/models/B-general-v275.pt"))
    parser.add_argument("--no-validate", action="store_true", help="skip the per-decision record validation")
    parser.add_argument("--greedy", action="store_true", help="argmax instead of sampling (debug only)")
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c4/self-play.json"))
    args = parser.parse_args(argv)

    if args.count < 1 or args.segments < 1 or args.decisions_per_segment < 1:
        raise SystemExit("--count, --segments and --decisions-per-segment must be positive")
    seats = tuple(dict.fromkeys(int(seat) for seat in args.seats))
    if not seats:
        raise SystemExit("--seats must be a subset of 0 1")
    device = torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise SystemExit("--device cuda requested but CUDA is unavailable")

    start_jvm(str(DEFAULT_INSTALL))
    from crforge_env import _jvm as env_jvm

    env_jvm._started_with = Path(DEFAULT_INSTALL).expanduser().resolve()
    policy = load_policy_v4(args.checkpoint, device=device)
    model = policy.model
    model.eval()
    release = getattr(model, "competitive_release", None)
    config = model.config
    payload = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    contract = payload.get("contract") if isinstance(payload, dict) else None
    bundle = production_semantic_bundle(release)
    catalog = Catalog.load()
    timeline = load_game_mode_timeline(STANDARD_GAME_MODE).timeline

    vector = _vector(args.count, max_ticks=args.max_ticks)
    encoder = VectorObservationEncoder(vector, catalog)
    encoder.reset()
    batch = encoder.encode()
    rows = {
        seat: VectorTensorizer(
            _stock_tensorizer(bundle, config, seat),
            vocabulary=list(vector.vocabulary),
            bridge_catalog=catalog,
            timeline=timeline,
            tower_troop_id=TOWER_TROOP,
        )
        for seat in seats
    }
    for item in rows.values():
        item.reset(batch)
    decoders = {seat: VectorActionDecoder(rows[seat].tensorizer, schema=vector.schema) for seat in seats}

    window = int(config.decision_ticks)
    # Every delay bin must have a tick of its own inside one decision window;
    # a narrower checkpoint would make the second micro-action unplaceable.
    window_span = 1 + int(config.delay_offset_ms[-1]) // TICK_MS
    if window < window_span:
        raise SystemExit(
            f"policy window is {window} ticks but its delay bins need {window_span}"
        )
    state = model.initial_state(args.count * len(seats), device=device)
    torch.manual_seed(int(args.seed))
    np.random.seed(int(args.seed))
    info = vector.schema.info

    # Walk the vector to the first playable tick so the policy starts where the
    # native env starts its first decision, not during the start cooldown.
    # ``tick`` is per lane and non-negative, so this is a plain ``min``: the
    # ``initial=`` argument seeds the reduction and would clamp it to zero.
    while int(batch.tick[:, 0].min()) < int(args.warmup_ticks):
        vector.step()
        encoder.encode(batch)

    segments: list[dict[str, Any]] = []
    totals = {
        "decisions": 0,
        "lane_decisions": 0,
        "act_lane_decisions": 0,
        "eligible_lane_decisions": 0,
        "micro_actions": 0,
        "second_micro_actions": 0,
        "shifted_micro_actions": 0,
        "dropped_micro_actions": 0,
        "resets": 0,
        "illegal_peak": 0,
    }
    timing: dict[str, list[float]] = {
        "facts_ms": [],
        "tensorize_ms": [],
        "inference_ms": [],
        "decode_ms": [],
        "step_ms": [],
    }
    faults_before = dict(vector.fault_counts())
    reset_mask = np.ones(args.count, dtype=bool)
    try:
        for segment in range(args.segments):
            segment_started = time.perf_counter()
            counts = {
                "decisions": 0,
                "lane_decisions": 0,
                "act_lane_decisions": 0,
                "eligible_lane_decisions": 0,
                "resets": 0,
                "second_micro_actions": 0,
                "shifted_micro_actions": 0,
                "dropped_micro_actions": 0,
            }
            samples: dict[str, list[float]] = {name: [] for name in timing}
            illegal_peak = 0
            for _ in range(args.decisions_per_segment):
                started = time.perf_counter()
                if reset_mask.any():
                    for seat in seats:
                        rows[seat].reset_rows(batch, reset_mask)
                encoder.encode(batch)
                facts_done = time.perf_counter()

                stacks = {seat: rows[seat].tensorize(batch) for seat in seats}
                stacked = _interleave([stacks[seat] for seat in seats])
                tensorize_done = time.perf_counter()

                episode_start = torch.from_numpy(np.repeat(reset_mask, len(seats))).to(device)
                with torch.inference_mode():
                    if args.greedy:
                        output = model.act(
                            stacked.to_model_input(device), state, episode_start=episode_start
                        )
                    else:
                        output = model.sample_for_ppo_rollout(
                            stacked.to_model_input(device), state, episode_start=episode_start
                        )
                state = output.next_state
                inference_done = time.perf_counter()

                plans = {}
                eligible = np.zeros((args.count,), dtype=np.int64)
                acted = np.zeros((args.count,), dtype=np.int64)
                intra = {}
                with torch.inference_mode():
                    for index, seat in enumerate(seats):
                        sequence = _seat_rows(output.actions, index, len(seats))
                        candidates = stacks[seat].candidates
                        plan = decoders[seat].decode(batch, sequence, candidates, seat=seat)
                        rows[seat].record_action(sequence, stacks[seat], validate=not args.no_validate)
                        played = plan.actions > 0
                        if played.any() and not bool(plan.masked[played].all()):
                            raise RuntimeError(f"decoded a closed engine mask for seat {seat}")
                        plans[seat] = plan
                        # A lane is eligible when this seat had at least one open
                        # deploy candidate: that is exactly what the decoder can
                        # turn into an engine action.
                        open_deploy = (
                            np.asarray(candidates.mask, dtype=bool)
                            & (np.asarray(candidates.native_hand_slot) >= 0)
                            & (np.asarray(candidates.variant) == CANDIDATE_DEPLOY)
                        )
                        eligible += open_deploy.any(axis=1).astype(np.int64)
                        acted += ((np.asarray(plan.steps) > 0) & open_deploy.any(axis=1)).astype(np.int64)
                        intra[seat], shifted, dropped = plan_window_ticks(plan.offsets, window)
                        second = int((np.asarray(plan.steps) > 1).sum())
                        counts["second_micro_actions"] += second
                        counts["shifted_micro_actions"] += shifted
                        counts["dropped_micro_actions"] += dropped
                        totals["second_micro_actions"] += second
                        totals["shifted_micro_actions"] += shifted
                        totals["dropped_micro_actions"] += dropped
                        totals["micro_actions"] += int(np.asarray(plan.steps).sum())
                decode_done = time.perf_counter()

                reset_mask = np.zeros(args.count, dtype=bool)
                for tick in range(window):
                    actions = np.zeros((args.count, 2), dtype=np.int32)
                    for seat in seats:
                        for step in range(int(intra[seat].shape[1])):
                            due = intra[seat][:, step] == tick
                            if due.any():
                                actions[due, seat] = plans[seat].actions[due, step]
                    # A lane that already restarted this window is a new episode
                    # now: the actions decoded for the old one must not be sent
                    # into it.
                    if reset_mask.any():
                        actions[reset_mask] = 0
                    vector.step(actions)
                    reset_mask |= vector.infos[:, info["reset"]].astype(bool)
                if bool(vector.dones.any()):
                    # Autoreset happens on the step after a terminal.  When the
                    # terminal lands on the window's last tick, take that step
                    # now so the next decision reads a live episode, not the
                    # dead one.
                    vector.step()
                    reset_mask |= vector.infos[:, info["reset"]].astype(bool)
                step_done = time.perf_counter()

                illegal_guard = vector.infos[:, info["illegal_0"]].astype(np.int64) + vector.infos[
                    :, info["illegal_1"]
                ].astype(np.int64)
                illegal_peak = max(illegal_peak, int(illegal_guard.max(initial=0)))

                counts["decisions"] += 1
                counts["lane_decisions"] += args.count * len(seats)
                counts["act_lane_decisions"] += int(acted.sum())
                counts["eligible_lane_decisions"] += int(eligible.sum())
                counts["resets"] += int(reset_mask.sum())
                samples["facts_ms"].append((facts_done - started) * 1000.0)
                samples["tensorize_ms"].append((tensorize_done - facts_done) * 1000.0)
                samples["inference_ms"].append((inference_done - tensorize_done) * 1000.0)
                samples["decode_ms"].append((decode_done - inference_done) * 1000.0)
                samples["step_ms"].append((step_done - decode_done) * 1000.0)

            wall = time.perf_counter() - segment_started
            for name, values in samples.items():
                timing[name].extend(values)
            entry = {
                "segment": segment,
                "wall_s": wall,
                "decisions": counts["decisions"],
                "lane_decisions": counts["lane_decisions"],
                "lane_decisions_per_s": counts["lane_decisions"] / wall if wall else 0.0,
                "matches_per_s": (
                    counts["lane_decisions"] / len(seats) / MATCH_DECISIONS / wall if wall else 0.0
                ),
                "eligible_lane_decisions": counts["eligible_lane_decisions"],
                "act_lane_decisions": counts["act_lane_decisions"],
                "eligible_act_rate": (
                    counts["act_lane_decisions"] / counts["eligible_lane_decisions"]
                    if counts["eligible_lane_decisions"]
                    else 0.0
                ),
                "second_micro_actions": counts["second_micro_actions"],
                "shifted_micro_actions": counts["shifted_micro_actions"],
                "dropped_micro_actions": counts["dropped_micro_actions"],
                "resets": counts["resets"],
                "illegal_peak": illegal_peak,
                "ms": {name: (statistics.median(values) if values else 0.0) for name, values in samples.items()},
            }
            segments.append(entry)
            for name, value in counts.items():
                totals[name] += value
            totals["illegal_peak"] = max(totals["illegal_peak"], illegal_peak)
            print(json.dumps(entry), flush=True)

        faults_after = dict(vector.fault_counts())
        fault_delta = {
            str(key): int(faults_after.get(key, 0)) - int(faults_before.get(key, 0))
            for key in set(faults_after) | set(faults_before)
        }
        fault_delta = {key: value for key, value in fault_delta.items() if value}
        wall_total = sum(item["wall_s"] for item in segments)
        eligible_total = totals["eligible_lane_decisions"]
        act_rate = totals["act_lane_decisions"] / eligible_total if eligible_total else 0.0
        rejected = totals["illegal_peak"] + sum(fault_delta.values())
        artifact = {
            "schema": "crforge-self-play.v1",
            "measured": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "shape": {
                "count": args.count,
                "segments": args.segments,
                "decisions_per_segment": args.decisions_per_segment,
                "seats": list(seats),
                "device": str(device),
                "seed": args.seed,
                "warmup_ticks": args.warmup_ticks,
                "max_ticks": args.max_ticks,
                "policy_cadence_ticks": window,
                "engine_decision_ticks": 1,
                "validated_every_decision": not args.no_validate,
                "sampled": not args.greedy,
            },
            "substrate": {
                "release": release,
                "tables": str(DEFAULT_TABLES),
                "install": str(DEFAULT_INSTALL),
                "checkpoint": str(args.checkpoint),
                "checkpoint_sha256": policy.checkpoint_sha256,
                "checkpoint_id": policy.checkpoint_id,
                "parameters": int(sum(item.numel() for item in model.parameters())),
                "timeline": timeline.name,
                "game_mode": STANDARD_GAME_MODE,
                "tower_troop": TOWER_TROOP,
                "contract_keys": sorted(str(key) for key in contract)[:12] if isinstance(contract, dict) else [],
            },
            "fidelity": {
                "frame": "facts are game-framed; the batched decode applies game -> seat",
                "action_space": "one engine action per seat per tick; every V4 micro-action is scheduled",
                "second_micro_rule": (
                    "micro action k is given at its delay bin ticks after the decision; a second "
                    "action that would share the first's tick is shifted one tick later, and one "
                    "that would leave the window is dropped and counted"
                ),
                "candidates": "deploy candidates only (no ability source, no Mirror runtime)",
                "previous_action": "recorded every decision; cleared per lane on autoreset",
                "episode_start": "per lane, from the vector's own reset flag",
                "rollout_storage": "none yet - this is the collection/throughput probe",
            },
            "gate": {
                "rejected": rejected,
                "illegal_peak": totals["illegal_peak"],
                "fault_delta": fault_delta,
                "eligible_act_rate": act_rate,
                "eligible_lane_decisions": eligible_total,
                "act_lane_decisions": totals["act_lane_decisions"],
                "pass": rejected == 0 and eligible_total > 0 and act_rate > 0.0,
            },
            "totals": {
                **totals,
                "wall_s": wall_total,
                "lane_decisions_per_s": totals["lane_decisions"] / wall_total if wall_total else 0.0,
                "matches_per_s": (
                    totals["lane_decisions"] / len(seats) / MATCH_DECISIONS / wall_total
                    if wall_total
                    else 0.0
                ),
                "ms": {name: (statistics.median(values) if values else 0.0) for name, values in timing.items()},
                # Engine work: every lane is stepped one tick at a time.
                "env_steps_per_s": (
                    totals["decisions"] * window * args.count / wall_total if wall_total else 0.0
                ),
                # What the learner would consume: one transition per actor row.
                "policy_rows_per_s": (
                    totals["decisions"] * args.count * len(seats) / wall_total if wall_total else 0.0
                ),
            },
            "segments": segments,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "pass": artifact["gate"]["pass"],
                    "gate": artifact["gate"],
                    "totals": artifact["totals"],
                    "output": str(args.output),
                },
                indent=2,
            )
        )
    finally:
        vector.close()
    return 0 if artifact["gate"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
