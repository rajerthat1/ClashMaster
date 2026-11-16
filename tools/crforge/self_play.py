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

This tool is the C4 producer.  It measures the packet the C4 gate asks for —
env-steps/s, tensorize ms, inference ms, decode ms — counts engine faults,
illegal actions and the eligible-ACT rate, and (by default) **stores the
rollout** the PPO update consumes: per decision the stacked observation, the
sampled action, the behaviour log-probability and value, the native-shape
reward, and the episode boundary.  ``--no-rollout`` runs the throughput probe
without retaining tensors.

The reward is ``BattleEnvV1._rewards`` rebuilt from the facts layer
(``crforge_bridge.rollout``): the engine's terminal result plus potential-based
shaping over crowns and tower damage.  A time-limit cut stores a zero shaping
term because the vector has already autoreset the lane (see that module).

``--ppo-update`` closes the loop: each finalized segment runs one
``distributed_ppo_update_v4`` over exactly the stored record (the same reward,
GAE and validation the native path uses).  A lane that autoreset inside the
segment is replayed as two contiguous episode segments, so the recurrence never
carries the dead episode's state into the new one.  The store binds a hash of the
sampling weights, so an update between segments changes the next segment's
behaviour identity instead of silently reusing the checkpoint's.

``--force-second-micro`` is a **timing probe, not a rollout**: the sampled
policy never emits two micro-actions, so this mode appends a different legal
play from the open candidates at the sampled play's own delay bin.  The
collision shifts it one tick, or drops it when the sampled play already runs on
the window's last tick.  It is refused together with rollout storage, because
the stored sampled action would no longer be the action the engine ran.

    .venv/bin/python tools/crforge/self_play.py \
        --count 8 --segments 10 --decisions-per-segment 64 \
        --output runs/crforge-c4/self-play.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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
from crforge_bridge.rollout import CrforgeRewardConfig, CrforgeRolloutStore, PotentialShapingReward, rollout_statistics
from crforge_bridge.vector_actions import CANDIDATE_DEPLOY, VectorActionDecoder, VectorActionPlan, plan_window_ticks
from crforge_bridge.vector_observation import VectorObservationEncoder
from crforge_bridge.vector_tensorize import VectorTensorizer
from native_runner.contracts import DEFAULT_TICK_MS as TICK_MS
from native_runner.cr_native_env import FIRST_PLAYABLE_TICK
from native_runner.timeline import load_game_mode_timeline
from native_runner.training.v4.distributed_ppo import distributed_ppo_update_v4
from native_runner.training.v4.factory import deck_roles_from_form_availability, production_semantic_bundle
from native_runner.training.v4.policy_session import load_policy_v4
from native_runner.training.v4.ppo import PPOConfigV4
from native_runner.training.v4.tensorizer import UniversalObservationTensorizerV4
from native_runner.training.v4.tensors import (
    GATE_ACT,
    TARGET_GRID,
    ActionSequenceV4,
    RecurrentPolicyStateV4,
    tensor_padding_value,
)

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
                    [getattr(record, item.name) for record in records], fill=tensor_padding_value(first, item.name)
                )
                for item in fields(first)
            }
        )
    raise TypeError(f"cannot interleave {type(first).__name__}")


def _seat_rows(actions: ActionSequenceV4, index: int, seats: int) -> ActionSequenceV4:
    """One actor seat's rows of an interleaved action batch."""

    return ActionSequenceV4(**{item.name: getattr(actions, item.name)[index::seats] for item in fields(actions)})


def _candidate_record(section: Any) -> Any:
    return getattr(section, "candidates", section)


def _force_second_micro_actions(
    sequence: ActionSequenceV4, plan: VectorActionPlan, candidates: Any, *, mode: str, budget: int, window: int
) -> tuple[ActionSequenceV4, int]:
    """Add a legal second micro-action at the first action's own tick.

    This is a timing probe: the sampled policy emits one micro-action, so this
    appends a **different** play from the open candidates at the same delay bin.
    ``shift`` picks a lane whose first action runs before the last window tick,
    so the collision pushes the second play one tick later; ``drop`` picks a
    lane whose first action already runs on the last tick, so the second play
    has no free tick and is counted as dropped.

    Two rules keep it legal, and both matter:

    * the duplicate is a **different hand slot** than the sampled play, because
      giving the same card again one tick later finds the queued slot closed;
    * both plays must be payable from the decision's effective elixir (one tick
      of regeneration is ignored), and the chosen candidate must have a legal
      tile in its own placement grid.

    The first action is never modified: its rule, index and delay bin are the
    policy's, so a refusal here can only come from the appended play.
    """

    if mode not in ("shift", "drop"):
        raise ValueError(f"unknown second micro-action mode {mode!r}")
    if budget < 1 or window < 2:
        return sequence, 0
    record = _candidate_record(candidates)
    mask = np.asarray(record.mask, dtype=bool)
    slots = np.asarray(record.native_hand_slot, dtype=np.int64)
    variant = np.asarray(record.variant, dtype=np.int64)
    target_mode = np.asarray(record.target_mode, dtype=np.int64)
    cost = np.asarray(record.cost, dtype=np.float64)
    elixir = np.asarray(record.elixir, dtype=np.float64)
    uid = np.asarray(record.uid, dtype=np.int64)
    placement = np.asarray(record.placement, dtype=bool)
    lanes, width = mask.shape
    grid = placement.reshape(lanes, width, -1)
    legal_cell = grid.any(axis=2)

    gate = sequence.gate.detach().cpu().numpy()
    counts = sequence.micro_action_count.detach().cpu().numpy()
    first_rows = np.asarray(plan.candidate_rows[:, 0], dtype=np.int64)
    first_ticks = np.asarray(plan.offsets[:, 0], dtype=np.int64) - 1
    plays = np.asarray(plan.actions[:, 0]) > 0
    lane_index = np.arange(lanes)
    safe_rows = np.clip(first_rows, 0, max(0, width - 1))
    first_cost = np.where(first_rows >= 0, cost[lane_index, safe_rows], 0.0)
    first_slot = np.where(first_rows >= 0, slots[lane_index, safe_rows], -2)
    timing = first_ticks == (window - 1) if mode == "drop" else (first_ticks >= 0) & (first_ticks < window - 1)
    other = (
        mask
        & (slots >= 0)
        & (variant == CANDIDATE_DEPLOY)
        & (target_mode == TARGET_GRID)
        & legal_cell
        & (slots != first_slot[:, None])
        # Both plays must fit the decision's effective elixir.
        & (cost <= (elixir - first_cost)[:, None] + 1e-6)
    )
    eligible = (gate == GATE_ACT) & (counts == 1) & plays & timing & other.any(axis=1)
    chosen = np.flatnonzero(eligible)[: int(budget)]
    if not chosen.size:
        return sequence, 0

    updated = {item.name: getattr(sequence, item.name).clone() for item in fields(sequence)}
    for lane in chosen:
        lane = int(lane)
        row = int(np.flatnonzero(other[lane])[0])
        updated["micro_action_count"][lane] = 2
        updated["candidate_index"][lane, 1] = row
        updated["candidate_uid"][lane, 1] = int(uid[lane, row])
        updated["target_cell"][lane, 1] = int(np.flatnonzero(grid[lane, row])[0])
        updated["delay_offset_bin"][lane, 1] = updated["delay_offset_bin"][lane, 0]
    return ActionSequenceV4(**updated), int(chosen.size)


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


def _policy_state_id(model: torch.nn.Module, checkpoint_id: str) -> str:
    """Identity of the behaviour weights, not just the file they came from.

    A crforge gate run may update between segments; the stored rollout must say
    which weights sampled it, or the update could silently replay old behaviour
    under new parameters.  Parameter and buffer bytes are cheap to hash at a
    segment boundary.
    """

    digest = hashlib.sha256(checkpoint_id.encode("utf-8"))
    for name, tensor in sorted(model.named_parameters()):
        digest.update(name.encode("utf-8"))
        digest.update(tensor.detach().to(device="cpu", dtype=torch.float32).contiguous().numpy().tobytes())
    for name, tensor in sorted(model.named_buffers()):
        digest.update(name.encode("utf-8"))
        digest.update(tensor.detach().to(device="cpu", dtype=torch.float32).contiguous().numpy().tobytes())
    return digest.hexdigest()[:32]


def _run_segment_update(
    model: torch.nn.Module,
    rollout: Any,
    *,
    args: argparse.Namespace,
    device: torch.device,
    expected_policy_state_id: str,
    gamma: float,
) -> dict[str, Any]:
    """One PPO update over a finalized segment, with the C4 update gate.

    The optimizer is created per call: this is a correctness gate over the
    stored record, not the training loop, so AdamW momentum does not span the
    segment boundary.  A real crforge trainer keeps one optimizer.
    """

    rows = int(rollout.batch_size)
    minibatch = int(args.ppo_lane_minibatch) or rows
    minibatch = min(minibatch, rows)
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(args.ppo_learning_rate))
    config = PPOConfigV4(
        gamma=gamma,
        gae_lambda=float(args.gae_lambda),
        clip_range=float(args.ppo_clip_range),
        value_clip_range=float(args.ppo_clip_range),
        value_coefficient=0.5,
        entropy_coefficient=0.0,
        max_grad_norm=1.0,
    )
    started = time.perf_counter()
    metrics = distributed_ppo_update_v4(
        model,
        optimizer,
        rollout,
        epochs=int(args.ppo_epochs),
        lane_minibatch_size=minibatch,
        sequence_chunk_steps=int(args.ppo_sequence_chunk),
        config=config,
        expected_policy_state_id=expected_policy_state_id,
        autocast_dtype=None,
        validate=not args.no_validate,
    )
    wall = time.perf_counter() - started
    finite = bool(metrics) and all(
        math.isfinite(float(value)) for row in metrics for value in row.values() if isinstance(value, (int, float))
    )
    valid = sum(float(row.get("valid_count", 0.0)) for row in metrics)
    return {
        "segments_updated": 1,
        "epochs": int(args.ppo_epochs),
        "lane_minibatch_size": minibatch,
        "sequence_chunk_steps": int(args.ppo_sequence_chunk),
        "learning_rate": float(args.ppo_learning_rate),
        "clip_range": float(args.ppo_clip_range),
        "gamma": float(gamma),
        "gae_lambda": float(args.gae_lambda),
        "device": str(device),
        "time_steps": int(rollout.time_steps),
        "batch_size": rows,
        "episode_starts": int(rollout.episode_start.sum()),
        "mid_rollout_episode_starts": int(rollout.episode_start[1:].sum()) if rollout.time_steps > 1 else 0,
        "wall_s": wall,
        "valid_count": valid,
        "finite": finite,
        "metrics": metrics,
        "pass": finite and valid > 0.0,
    }


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
    parser.add_argument(
        "--no-rollout",
        action="store_true",
        help="throughput probe only: do not retain the per-decision rollout tensors",
    )
    parser.add_argument(
        "--shaping-beta",
        type=float,
        default=0.05,
        help="potential-shaping weight of the native PPO reward (0 disables shaping)",
    )
    parser.add_argument(
        "--gae-lambda",
        type=float,
        default=0.98,
        help="GAE lambda used to check the stored rollout (the update's own value)",
    )
    parser.add_argument(
        "--force-second-micro",
        choices=("off", "shift", "drop", "alternate"),
        default="off",
        help=(
            "timing probe: repeat a sampled play as a second micro-action that is shifted "
            "or dropped; 'alternate' does both; refused with rollout storage"
        ),
    )
    parser.add_argument(
        "--ppo-update",
        action="store_true",
        help=(
            "run one PPO update on each finalized segment's stored rollout; a lane that "
            "autoreset mid-segment exercises the episode-boundary replay"
        ),
    )
    parser.add_argument("--ppo-epochs", type=int, default=1, help="PPO epochs per segment update")
    parser.add_argument(
        "--ppo-lane-minibatch",
        type=int,
        default=0,
        help="lanes per PPO minibatch (0 = every actor row in one minibatch)",
    )
    parser.add_argument("--ppo-sequence-chunk", type=int, default=32, help="TBPTT chunk length in decisions")
    parser.add_argument("--ppo-clip-range", type=float, default=0.1, help="PPO policy/value clip range")
    parser.add_argument("--ppo-learning-rate", type=float, default=1e-4, help="AdamW learning rate of the gate update")
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c4/self-play.json"))
    args = parser.parse_args(argv)

    if args.count < 1 or args.segments < 1 or args.decisions_per_segment < 1:
        raise SystemExit("--count, --segments and --decisions-per-segment must be positive")
    if args.force_second_micro != "off" and not args.no_rollout:
        raise SystemExit(
            "--force-second-micro replaces the sampled action, so it cannot be stored as a "
            "policy rollout; pass --no-rollout"
        )
    if args.ppo_update and args.no_rollout:
        raise SystemExit("--ppo-update needs the stored rollout; drop --no-rollout")
    if args.ppo_update and (
        args.ppo_epochs < 1
        or args.ppo_sequence_chunk < 1
        or args.ppo_lane_minibatch < 0
        or args.ppo_learning_rate <= 0.0
        or args.ppo_clip_range <= 0.0
    ):
        raise SystemExit("--ppo-update settings must be positive (and the lane minibatch non-negative)")
    reward_config = CrforgeRewardConfig(shaping_beta=float(args.shaping_beta))
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
        raise SystemExit(f"policy window is {window} ticks but its delay bins need {window_span}")
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
        "forced_second_micro_actions": 0,
        "resets": 0,
        "illegal_peak": 0,
    }
    timing: dict[str, list[float]] = {
        "tensorize_ms": [],
        "inference_ms": [],
        "decode_ms": [],
        "step_ms": [],
        "facts_ms": [],
        "rollout_ms": [],
    }
    # The reward tracker spans segments: the board does not reset at a segment
    # boundary, only the rollout does (as a truncation).
    shaping = PotentialShapingReward(args.count, config=reward_config)
    result_index = int(vector.schema.header["result"][0])
    won_code = int(vector.schema.results["won"])
    lost_code = int(vector.schema.results["lost"])
    rollout_enabled = not args.no_rollout
    reward_mismatches = 0
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
                "forced_second_micro_actions": 0,
            }
            samples: dict[str, list[float]] = {name: [] for name in timing}
            illegal_peak = 0
            rollout_store = None
            segment_policy_state_id = None
            if rollout_enabled:
                segment_policy_state_id = _policy_state_id(model, str(policy.checkpoint_id))
                rollout_store = CrforgeRolloutStore(
                    lanes=args.count,
                    seats=len(seats),
                    initial_state=RecurrentPolicyStateV4(
                        hidden=state.hidden.detach().clone(), cell=state.cell.detach().clone()
                    ),
                    policy_state_id=segment_policy_state_id,
                    gate_temperature=float(model.ppo_gate_temperature),
                    action_temperature=float(model.ppo_action_temperature),
                    continue_temperature=float(model.ppo_continue_temperature),
                )
            for decision in range(args.decisions_per_segment):
                started = time.perf_counter()
                episode_start_lanes = reset_mask.copy()
                if reset_mask.any():
                    for seat in seats:
                        rows[seat].reset_rows(batch, reset_mask)

                stacks = {seat: rows[seat].tensorize(batch) for seat in seats}
                stacked = _interleave([stacks[seat] for seat in seats])
                tensorize_done = time.perf_counter()

                episode_start = torch.from_numpy(np.repeat(episode_start_lanes, len(seats))).to(device)
                with torch.inference_mode():
                    if args.greedy:
                        output = model.act(stacked.to_model_input(device), state, episode_start=episode_start)
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
                forced_mode = args.force_second_micro
                if forced_mode == "alternate":
                    forced_mode = "shift" if decision % 2 == 0 else "drop"
                with torch.inference_mode():
                    for index, seat in enumerate(seats):
                        sequence = _seat_rows(output.actions, index, len(seats))
                        candidates = stacks[seat].candidates
                        if forced_mode != "off":
                            # Decode the sample first: the duplicate is built from
                            # the action the policy actually chose.
                            sampled = decoders[seat].decode(batch, sequence, candidates, seat=seat)
                            sequence, forced = _force_second_micro_actions(
                                sequence,
                                sampled,
                                candidates,
                                mode=forced_mode,
                                budget=max(1, args.count // 4),
                                window=window,
                            )
                            counts["forced_second_micro_actions"] += forced
                            totals["forced_second_micro_actions"] += forced
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
                terminated_lanes = np.zeros(args.count, dtype=bool)
                truncated_lanes = np.zeros(args.count, dtype=bool)
                terminal_reward = np.zeros((args.count, 2), dtype=np.float32)
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
                    done_now = vector.dones.astype(bool)
                    if done_now.any():
                        # Read the result on the tick it was written: the next
                        # step autoresets the lane and clears it.
                        codes = vector.observations[done_now][:, :, result_index]
                        mapped = np.where(codes == won_code, 1.0, np.where(codes == lost_code, -1.0, 0.0))
                        terminal_reward[done_now] = mapped.astype(np.float32)
                        cut = np.asarray(vector.infos[done_now, info["truncated"]], dtype=bool)
                        terminated_lanes[done_now] |= ~cut
                        truncated_lanes[done_now] |= cut
                        # The engine's own reward must be the terminal result;
                        # a mismatch invalidates the reconstructed reward.
                        mismatch = np.abs(vector.rewards[done_now] - mapped) > 1e-6
                        reward_mismatches += int(mismatch.sum())
                if bool(vector.dones.any()):
                    # Autoreset happens on the step after a terminal.  When the
                    # terminal lands on the window's last tick, take that step
                    # now so the next decision reads a live episode, not the
                    # dead one.
                    vector.step()
                    reset_mask |= vector.infos[:, info["reset"]].astype(bool)
                step_done = time.perf_counter()

                # The post-window facts are this decision's reward state and the
                # next decision's observation: encode once, at the end.
                encoder.encode(batch)
                facts_done = time.perf_counter()
                if rollout_store is not None:
                    rewards = shaping.advance(
                        batch, terminal_reward=terminal_reward, terminated=terminated_lanes, truncated=truncated_lanes
                    )
                    rollout_store.append(
                        observation=stacked,
                        actions=output.actions,
                        log_prob=output.log_prob,
                        value=output.value,
                        rewards=rewards,
                        episode_start=episode_start_lanes,
                        terminated=terminated_lanes,
                        truncated=truncated_lanes,
                    )
                rollout_done = time.perf_counter()

                illegal_guard = vector.infos[:, info["illegal_0"]].astype(np.int64) + vector.infos[
                    :, info["illegal_1"]
                ].astype(np.int64)
                illegal_peak = max(illegal_peak, int(illegal_guard.max(initial=0)))

                counts["decisions"] += 1
                counts["lane_decisions"] += args.count * len(seats)
                counts["act_lane_decisions"] += int(acted.sum())
                counts["eligible_lane_decisions"] += int(eligible.sum())
                counts["resets"] += int(reset_mask.sum())
                samples["tensorize_ms"].append((tensorize_done - started) * 1000.0)
                samples["inference_ms"].append((inference_done - tensorize_done) * 1000.0)
                samples["decode_ms"].append((decode_done - inference_done) * 1000.0)
                samples["step_ms"].append((step_done - decode_done) * 1000.0)
                samples["facts_ms"].append((facts_done - step_done) * 1000.0)
                samples["rollout_ms"].append((rollout_done - facts_done) * 1000.0)

            segment_rollout = None
            if rollout_store is not None:
                # The observation after the last decision is the time-limit
                # bootstrap every still-running lane needs at the segment cut.
                boundary_stacks = {seat: rows[seat].tensorize(batch) for seat in seats}
                boundary_batch = _interleave([boundary_stacks[seat] for seat in seats])
                with torch.inference_mode():
                    context = model.forward(
                        boundary_batch.to_model_input(device),
                        state,
                        episode_start=torch.zeros(args.count * len(seats), dtype=torch.bool, device=device),
                        validate=not args.no_validate,
                    )
                rollout = rollout_store.finalize(boundary_value=context.value.detach().float())
                segment_rollout = rollout_statistics(
                    rollout, gamma=reward_config.shaping_gamma, gae_lambda=float(args.gae_lambda)
                )
                segment_rollout["summary"] = rollout_store.summary().as_dict()
                segment_rollout["storage_mib"] = round(rollout_store.storage_bytes() / 2**20, 3)
                if args.ppo_update:
                    segment_rollout["update"] = _run_segment_update(
                        model,
                        rollout,
                        args=args,
                        device=device,
                        expected_policy_state_id=str(segment_policy_state_id),
                        gamma=reward_config.shaping_gamma,
                    )

            wall = time.perf_counter() - segment_started
            update = None if segment_rollout is None else segment_rollout.get("update")
            update_wall = 0.0 if update is None else float(update["wall_s"])
            collection_wall = max(wall - update_wall, 0.0)
            for name, values in samples.items():
                timing[name].extend(values)
            entry = {
                "segment": segment,
                "wall_s": wall,
                "update_wall_s": update_wall,
                "collection_wall_s": collection_wall,
                "decisions": counts["decisions"],
                "lane_decisions": counts["lane_decisions"],
                "lane_decisions_per_s": counts["lane_decisions"] / wall if wall else 0.0,
                "collection_lane_decisions_per_s": (
                    counts["lane_decisions"] / collection_wall if collection_wall else 0.0
                ),
                "matches_per_s": (counts["lane_decisions"] / len(seats) / MATCH_DECISIONS / wall if wall else 0.0),
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
                "forced_second_micro_actions": counts["forced_second_micro_actions"],
                "resets": counts["resets"],
                "illegal_peak": illegal_peak,
                "rollout": segment_rollout,
                "update": update,
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
        collection_wall_total = sum(item["collection_wall_s"] for item in segments)
        eligible_total = totals["eligible_lane_decisions"]
        act_rate = totals["act_lane_decisions"] / eligible_total if eligible_total else 0.0
        rejected = totals["illegal_peak"] + sum(fault_delta.values())
        rollout_segments = [item["rollout"] for item in segments if item["rollout"] is not None]
        rollout_finite = all(bool(item["finite"]) for item in rollout_segments) if rollout_segments else True
        rollout_totals = {
            "segments": len(rollout_segments),
            "time_steps": sum(int(item["time_steps"]) for item in rollout_segments),
            "rows_per_decision": len(seats) * args.count,
            "episode_starts": sum(int(item["episode_starts"]) for item in rollout_segments),
            "mid_rollout_episode_starts": sum(int(item["mid_rollout_episode_starts"]) for item in rollout_segments),
            "terminated": sum(int(item["terminated"]) for item in rollout_segments),
            "truncated": sum(int(item["truncated"]) for item in rollout_segments),
            "storage_mib": round(sum(float(item["storage_mib"]) for item in rollout_segments), 3),
            "reward_sum": sum(float(item["summary"]["reward_sum"]) for item in rollout_segments),
            "reward_nonzero_rows": sum(int(item["summary"]["reward_nonzero_rows"]) for item in rollout_segments),
            "finite": rollout_finite,
        }
        update_segments = [item["update"] for item in segments if item["update"] is not None]
        update_totals = {
            "segments_updated": len(update_segments),
            "time_steps": sum(int(item["time_steps"]) for item in update_segments),
            "valid_count": sum(float(item["valid_count"]) for item in update_segments),
            "wall_s": sum(float(item["wall_s"]) for item in update_segments),
            "finite": all(bool(item["finite"]) for item in update_segments) if update_segments else True,
            "mid_rollout_episode_starts": sum(int(item["mid_rollout_episode_starts"]) for item in update_segments),
            "multiple_episodes_per_lane": any(int(item["mid_rollout_episode_starts"]) > 0 for item in update_segments),
        }
        update_gate = {
            "enabled": bool(args.ppo_update),
            **update_totals,
            "pass": (not args.ppo_update)
            or (
                bool(update_segments)
                and all(bool(item["pass"]) for item in update_segments)
                and update_totals["finite"]
            ),
        }
        artifact = {
            "schema": "crforge-self-play.v2",
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
                "rollout_storage": rollout_enabled,
                "forced_second_micro": args.force_second_micro,
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
            "rollout": {
                "stored": rollout_enabled,
                "float_dtype": "float16",
                "reward": reward_config.as_dict(),
                "gae_lambda": float(args.gae_lambda),
                "policy_state_id": str(policy.checkpoint_id),
                "behavior_state_ids": sorted(
                    {str(item["policy_state_id"]) for item in rollout_segments if "policy_state_id" in item}
                ),
                "totals": rollout_totals,
                "segments": rollout_segments,
                "update": update_gate,
                "fidelity": {
                    "rows": "lane * seats + seat, the producer's interleave order",
                    "reward": (
                        "BattleEnvV1._rewards rebuilt from the facts layer: terminal result plus "
                        "potential-based shaping over crowns and tower damage"
                    ),
                    "potentials": "zero at a terminal; a time-limit cut (max_ticks) carries no shaping term",
                    "reward_result_mismatches": reward_mismatches,
                    "boundary": "each segment finalizes with a bootstrap forward on the post-window observation",
                    "behavior_identity": (
                        "the store binds a hash of the sampling weights, not just the checkpoint id; "
                        "an update between segments changes the next segment's identity"
                    ),
                    "multiple_episodes_per_lane": (
                        "a lane may carry more than one episode (autoreset); episode_start resets "
                        "the recurrence at that row and the GAE trace ends at the truncation, and "
                        "the update replays each contiguous episode segment separately"
                    ),
                },
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
                "forced_second_micro": (
                    "none"
                    if args.force_second_micro == "off"
                    else (
                        "timing probe only: a sampled play is repeated as a second micro-action "
                        "at a colliding or saturated delay bin; the sampled action is not the "
                        "engine action, so this run stores no rollout"
                    )
                ),
            },
            "gate": {
                "rejected": rejected,
                "illegal_peak": totals["illegal_peak"],
                "fault_delta": fault_delta,
                "eligible_act_rate": act_rate,
                "eligible_lane_decisions": eligible_total,
                "act_lane_decisions": totals["act_lane_decisions"],
                "reward_result_mismatches": reward_mismatches,
                "rollout_finite": rollout_finite,
                "update_pass": update_gate["pass"],
                "pass": (
                    rejected == 0
                    and eligible_total > 0
                    and act_rate > 0.0
                    and reward_mismatches == 0
                    and rollout_finite
                    and update_gate["pass"]
                ),
            },
            "totals": {
                **totals,
                "wall_s": wall_total,
                "update_wall_s": sum(item["update_wall_s"] for item in segments),
                "collection_wall_s": sum(item["collection_wall_s"] for item in segments),
                "lane_decisions_per_s": totals["lane_decisions"] / wall_total if wall_total else 0.0,
                "collection_lane_decisions_per_s": (
                    totals["lane_decisions"] / collection_wall_total if collection_wall_total else 0.0
                ),
                "matches_per_s": (
                    totals["lane_decisions"] / len(seats) / MATCH_DECISIONS / wall_total if wall_total else 0.0
                ),
                "ms": {name: (statistics.median(values) if values else 0.0) for name, values in timing.items()},
                # Engine work: every lane is stepped one tick at a time.
                "env_steps_per_s": (totals["decisions"] * window * args.count / wall_total if wall_total else 0.0),
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
