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

The loop keeps **one AdamW** for the whole run and states its boundary policy
(``--update-state-policy``): ``reset`` zeroes the recurrent state after every
update, ``carry`` keeps it (the deployed inference shape), and ``wave`` freezes
the behaviour weights for ``--wave-segments`` segments before applying their
updates and resetting.  Every update appends a reward/return trace row, and the
training gate needs **two consecutive finite updates** — one update proves the
mechanics, not the loop.  ``--save-checkpoint`` writes a V4 checkpoint (weights,
persistent optimizer, loop counters) and reloads it to verify the weight bytes.

``--overlap-update`` pipelines the loop: the trainer owns a second model and a
worker thread, so segment ``k``'s update runs while segment ``k+1`` collects.  The
collection model is changed only at a joined boundary, and every boundary records
the two invariants — the collecting weights still hash to the id the segment was
sampled with, and the copy reproduces the update model byte-for-byte.  The price
is one update of staleness: segment ``k`` is sampled before update ``k-1`` lands,
so every update after the first starts from newer weights than the data it reads.
The trace records ``sampling_policy_state_id`` against
``policy_state_id_at_update_start`` and flags the row ``stale``.  Whether that trade is
worth it is not assumed: ``tools/crforge/overlap_compare.py`` measures the wall,
the collection rate and the stale KL/clip against the serial loop.

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
import cProfile
import json
import pstats
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import fields
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / ".resource" / "crforge" / "python"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import numpy as np
import torch

from crforge_bridge.catalog import Catalog
from crforge_bridge.fast_forward import FastForwardV4
from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
from crforge_bridge.jvm import start as start_jvm
from crforge_bridge.rollout import CrforgeRewardConfig, CrforgeRolloutStore, PotentialShapingReward, rollout_statistics
from crforge_bridge.training import (
    CrforgeTrainer,
    CrforgeTrainingConfig,
    OverlapUpdater,
    copy_weights,
    load_resume_state,
    optimizer_signature,
    policy_state_id,
    weights_digest,
)
from crforge_bridge.vector_actions import (
    CANDIDATE_DEPLOY,
    CandidateTables,
    VectorActionDecoder,
    VectorActionPlan,
    candidate_tables,
    plan_window_ticks,
)
from crforge_bridge.vector_observation import VectorObservationEncoder
from crforge_bridge.vector_tensorize import VectorTensorizer, interleave_rows
from native_runner.contracts import DEFAULT_TICK_MS as TICK_MS
from native_runner.cr_native_env import FIRST_PLAYABLE_TICK
from native_runner.timeline import load_game_mode_timeline
from native_runner.training.v4.checkpoint import save_actor_critic_checkpoint
from native_runner.training.v4.factory import deck_roles_from_form_availability, production_semantic_bundle
from native_runner.training.v4.policy_session import load_policy_v4
from native_runner.training.v4.tensorizer import UniversalObservationTensorizerV4
from native_runner.training.v4.tensors import (
    GATE_ACT,
    TARGET_GRID,
    ActionSequenceV4,
    RecurrentPolicyStateV4,
)

LEARNER_DECK = (27000008, 27000006, 26000001, 26000000, 26000010, 26000084, 28000000, 28000011)
LEARNER_FORMS = (0, 1, 1, 2, 0, 0, 0, 0)
LEARNER_NAMES = ("Xbow", "Tesla", "Archer", "Knight", "Skeletons", "ElectroSpirit", "Fireball", "Log")
TOWER_TROOP = 159000000
STANDARD_GAME_MODE = 72_000_006
# One match is ~6,068 ticks at five-tick decisions.
MATCH_DECISIONS = 1_214


def _timing_buckets(samples: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    """Median, mean, max and first sample per bucket.

    The wall is a mean and a bucket's median is not: a cold first decision can
    be seconds and still leave every median untouched.  Summing medians against
    a mean wall is what produced the "unattributed ms" in ``FINDINGS.md`` §3.62;
    the means and the residual are the reconciling statistics.
    """

    return {
        "ms": {name: (statistics.median(values) if values else 0.0) for name, values in samples.items()},
        "ms_mean": {name: (statistics.fmean(values) if values else 0.0) for name, values in samples.items()},
        "ms_max": {name: (max(values) if values else 0.0) for name, values in samples.items()},
        "ms_first": {name: (values[0] if values else 0.0) for name, values in samples.items()},
    }


def _timing_residual(
    buckets: dict[str, dict[str, float]], *, collection_wall_s: float, decisions: int, boundary_ms: float
) -> dict[str, float]:
    """The part of the collection wall no bucket covers, per decision."""

    if decisions <= 0:
        return {"collection_ms_per_decision": 0.0, "accounted_ms_mean": 0.0, "unaccounted_ms_mean": 0.0}
    per_decision = 1000.0 * collection_wall_s / decisions
    accounted = sum(buckets["ms_mean"].values()) + boundary_ms / decisions
    return {
        "collection_ms_per_decision": per_decision,
        "accounted_ms_mean": accounted,
        "unaccounted_ms_mean": per_decision - accounted,
    }


def _inference_steady_state(segments: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The forward's share of a decision and the CPU half beside it.

    The last segment is the steady state (the warmup is on by default and the
    first decisions are cold, FINDINGS.md §3.67).  ``inference_ms`` is the model
    forward *plus its per-call overhead*; the CPU half is what has to fall for a
    faster forward to pay, so the artifact reports both rather than the forward
    alone (ROADMAP_CRFORGE.md §4).
    """

    if not segments:
        return {
            "segment": None,
            "decision_ms_mean": 0.0,
            "inference_ms_mean": 0.0,
            "inference_share": 0.0,
            "cpu_half_ms_mean": 0.0,
            "unaccounted_ms_mean": 0.0,
        }
    last = segments[-1]
    means = last.get("ms_mean") or {}
    decision = float(last.get("collection_ms_per_decision") or 0.0)
    forward = float(means.get("inference_ms") or 0.0)
    return {
        "segment": int(last.get("segment", 0)),
        "decision_ms_mean": decision,
        "inference_ms_mean": forward,
        "inference_share": (forward / decision) if decision else 0.0,
        "cpu_half_ms_mean": max(decision - forward, 0.0),
        "unaccounted_ms_mean": float(last.get("unaccounted_ms_mean") or 0.0),
        "cpu_half": last.get("cpu_half"),
    }


def _profile_summary(profiler: cProfile.Profile, *, decisions: int) -> dict[str, Any]:
    """Top functions of a profiled collection loop, in milliseconds per decision.

    Two orders are recorded because they answer different questions: cumulative
    time names the phase (a builder, the interleave, the decode), and own time
    names the code inside it.  ``decisions`` normalises the totals so a profile
    at any shape is comparable with the artifact's bucket means.
    """

    stats = pstats.Stats(profiler)
    scale = 1000.0 / max(int(decisions), 1)

    def rows(field: str) -> list[dict[str, Any]]:
        values = []
        for (filename, line, name), (cc, calls, own, cumulative, _callers) in stats.stats.items():
            values.append(
                {
                    "function": f"{name} ({Path(filename).name}:{line})",
                    "calls": int(calls),
                    "own_ms_per_decision": own * scale,
                    "cumulative_ms_per_decision": cumulative * scale,
                }
            )
        values.sort(key=lambda item: item[field], reverse=True)
        return values[:30]

    return {
        "decisions": int(decisions),
        "by_cumulative": rows("cumulative_ms_per_decision"),
        "by_own_time": rows("own_ms_per_decision"),
        "note": "a profiled run is slower than an unprofiled one; read the shares, not the wall",
    }


def _seat_rows(actions: ActionSequenceV4, index: int, seats: int) -> ActionSequenceV4:
    """One actor seat's rows of an interleaved action batch."""

    return ActionSequenceV4(**{item.name: getattr(actions, item.name)[index::seats] for item in fields(actions)})


def _candidate_record(section: Any) -> Any:
    return getattr(section, "candidates", section)


def _force_second_micro_actions(
    sequence: ActionSequenceV4,
    plan: VectorActionPlan,
    candidates: Any,
    *,
    mode: str,
    budget: int,
    window: int,
    tables: CandidateTables | None = None,
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
    table = candidate_tables(record) if tables is None else tables
    mask = np.asarray(table.mask, dtype=bool)
    slots = np.asarray(table.native_hand_slot, dtype=np.int64)
    variant = np.asarray(table.variant, dtype=np.int64)
    target_mode = np.asarray(table.target_mode, dtype=np.int64)
    cost = np.asarray(record.cost, dtype=np.float64)
    elixir = np.asarray(record.elixir, dtype=np.float64)
    uid = np.asarray(table.uid, dtype=np.int64)
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


def _save_checkpoint(
    path: Path,
    model: torch.nn.Module,
    trainer: CrforgeTrainer | None,
    *,
    policy: Any,
    args: argparse.Namespace,
    reward_config: CrforgeRewardConfig,
) -> dict[str, Any]:
    """Write a V4 checkpoint and prove it reloads to the same weight bytes.

    The saved payload carries the persistent optimizer, so a later run can
    continue the momentum instead of restarting Adam; the reload check is the
    gate that the weights on disk are the weights the loop trained, not the
    seed checkpoint's.
    """

    digest = weights_digest(model)
    # ``base_update_step`` is the step the cumulative ``updates`` count starts
    # from.  A fresh run starts at the source policy's step; a resumed run
    # rebases it (see ``CrforgeTrainer.resume``), so this stays exact instead of
    # counting the previous run's updates twice.
    update_step = int(trainer.base_update_step) + trainer.updates if trainer is not None else int(policy.update_step)

    checkpoint_id = save_actor_critic_checkpoint(
        path,
        model,
        optimizer=None if trainer is None else trainer.optimizer,
        update_step=update_step,
        training_stage="crforge-c4-ppo",
        gamma_per_decision=reward_config.gamma_per_decision,
        gae_lambda=float(args.gae_lambda),
        extra={
            "weights_digest": digest,
            "crforge": {
                "source_checkpoint": str(args.checkpoint),
                "source_checkpoint_sha256": policy.checkpoint_sha256,
                "source_checkpoint_id": policy.checkpoint_id,
                "source_update_step": int(policy.update_step),
                "loop": None if trainer is None else trainer.checkpoint_state(),
                "reward": reward_config.as_dict(),
                "shape": {
                    "count": args.count,
                    "segments": args.segments,
                    "decisions_per_segment": args.decisions_per_segment,
                    "seats": [int(seat) for seat in args.seats],
                },
            },
        },
    )
    reloaded = load_policy_v4(path, device="cpu")
    reloaded_digest = weights_digest(reloaded.model)
    return {
        "path": str(path),
        "checkpoint_id": checkpoint_id,
        "update_step": update_step,
        "training_stage": "crforge-c4-ppo",
        "weights_digest": digest,
        "reload_weights_digest": reloaded_digest,
        "reload_match": reloaded_digest == digest,
        "bytes": int(path.stat().st_size),
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
        "--no-warmup-forward",
        action="store_true",
        help=(
            "skip the startup warmup before segment 0; the default keeps the "
            "first decision's lazy kernel and cuDNN setup out of every measured rate"
        ),
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=0,
        help="episode cut at this tick (0 = the engine's full match); a short value exercises autoreset",
    )
    parser.add_argument(
        "--fast-forward",
        action="store_true",
        help=(
            "run the in-process port of ever_server's inference tricks: padded staging reused "
            "per row count, one fp32 recurrent-state table, fp16 autocast on CUDA and a CUDA "
            "Graph per row count. Sampling stays eager (a captured sampler would freeze its RNG)"
        ),
    )
    parser.add_argument(
        "--inference-precision",
        choices=("auto", "fp32", "fp16", "bf16"),
        default="auto",
        help=(
            "autocast precision for --fast-forward: 'auto' is fp16 on CUDA and fp32 on CPU; "
            "'bf16' is the opt-in CPU check of the half path; fp16 needs CUDA"
        ),
    )
    parser.add_argument(
        "--no-cuda-graphs",
        action="store_true",
        help="use the fast forward without CUDA Graph capture (the correctness path on a GPU box)",
    )
    parser.add_argument(
        "--full-cuda-graph",
        action="store_true",
        help=(
            "capture the sampled decode with the forward in one CUDA Graph instead of leaving the "
            "sampler eager; refused unless two replays draw different numbers (the frozen-RNG gate)"
        ),
    )
    parser.add_argument(
        "--verify-fast-forward",
        action="store_true",
        help=(
            "before the run, replay the warmup batch through eager fp32 and the fast forward on "
            "the same RNG and record action/log-prob equality; abort if the actions differ"
        ),
    )
    parser.add_argument("--checkpoint", type=Path, default=Path(".resource/RoyaleFish/models/B-general-v275.pt"))
    parser.add_argument("--no-validate", action="store_true", help="skip the per-decision record validation")
    parser.add_argument(
        "--profile-collection",
        action="store_true",
        help=(
            "profile the collection loop with cProfile and record the top functions by cumulative and own "
            "time; attribution for the CPU-half work that has to fall (ROADMAP_CRFORGE.md §4)"
        ),
    )
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
            "train: run PPO over each finalized segment's stored rollout with one persistent "
            "optimizer; a lane that autoreset mid-segment exercises the episode-boundary replay"
        ),
    )
    parser.add_argument(
        "--update-state-policy",
        choices=("carry", "reset", "wave"),
        default="reset",
        help=(
            "recurrent-state policy at an update boundary: 'reset' zeroes it after every update, "
            "'carry' keeps it (deployment inference shape), 'wave' freezes the weights for "
            "--wave-segments segments, updates at the wave end, then resets"
        ),
    )
    parser.add_argument(
        "--overlap-update",
        action="store_true",
        help=(
            "pipeline the loop: apply each segment's update on a second model in a worker "
            "thread while the next segment collects; costs one update of staleness, which "
            "the training trace records as 'stale'"
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "continue the run stored in --checkpoint: load its persistent optimizer and loop "
            "counters instead of starting a fresh Adam (needs --ppo-update)"
        ),
    )
    parser.add_argument(
        "--wave-segments",
        type=int,
        default=2,
        help="segments collected with frozen weights before one update wave (wave policy only)",
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
    parser.add_argument(
        "--ppo-learning-rate",
        type=float,
        default=1e-5,
        help="persistent AdamW learning rate (the native trainer default)",
    )
    parser.add_argument("--ppo-value-coefficient", type=float, default=0.5, help="value-loss coefficient")
    parser.add_argument("--ppo-entropy-coefficient", type=float, default=0.0, help="entropy bonus coefficient")
    parser.add_argument("--ppo-max-grad-norm", type=float, default=1.0, help="gradient clipping norm (0 disables)")
    parser.add_argument("--ppo-adam-eps", type=float, default=1e-5, help="AdamW epsilon")
    parser.add_argument("--ppo-weight-decay", type=float, default=0.0, help="AdamW weight decay")
    parser.add_argument(
        "--save-checkpoint",
        type=Path,
        default=None,
        help="write a V4 checkpoint (weights + the persistent optimizer) and reload it to verify",
    )
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
    if args.ppo_update and args.segments < 2:
        raise SystemExit("--ppo-update needs at least two segments: one update is not a training loop")
    if args.ppo_update and args.wave_segments > args.segments:
        raise SystemExit("--wave-segments cannot exceed --segments")
    if args.resume and not args.ppo_update:
        raise SystemExit("--resume continues a training loop; pass --ppo-update as well")
    if args.overlap_update and not args.ppo_update:
        raise SystemExit("--overlap-update pipelines the training loop; pass --ppo-update as well")
    if args.overlap_update and args.update_state_policy == "wave":
        raise SystemExit(
            "--overlap-update is the per-segment pipeline (reset/carry); the wave already batches "
            "segments and is a diagnostic cadence, so combining them would measure neither"
        )
    if args.ppo_update and (
        args.ppo_epochs < 1
        or args.ppo_sequence_chunk < 1
        or args.ppo_lane_minibatch < 0
        or args.ppo_learning_rate <= 0.0
        or args.ppo_clip_range <= 0.0
    ):
        raise SystemExit("--ppo-update settings must be positive (and the lane minibatch non-negative)")
    if args.verify_fast_forward and not args.fast_forward:
        raise SystemExit("--verify-fast-forward compares the fast forward; pass --fast-forward")
    if args.verify_fast_forward and args.no_warmup_forward:
        raise SystemExit("--verify-fast-forward needs the warmup batch; drop --no-warmup-forward")
    if args.inference_precision != "auto" and not args.fast_forward:
        raise SystemExit("--inference-precision is a --fast-forward setting")
    if args.no_cuda_graphs and not args.fast_forward:
        raise SystemExit("--no-cuda-graphs is a --fast-forward setting")
    if args.full_cuda_graph and not args.fast_forward:
        raise SystemExit("--full-cuda-graph is a --fast-forward setting")
    if args.full_cuda_graph and args.no_cuda_graphs:
        raise SystemExit("--full-cuda-graph is the captured path; drop --no-cuda-graphs")
    reward_config = CrforgeRewardConfig(shaping_beta=float(args.shaping_beta))
    training_config = None
    if args.ppo_update:
        # The update's gamma must be the reward's per-decision discount, or GAE
        # and the shaping term would optimize two different games.
        training_config = CrforgeTrainingConfig(
            state_policy=args.update_state_policy,
            segments_per_wave=(args.wave_segments if args.update_state_policy == "wave" else 1),
            learning_rate=float(args.ppo_learning_rate),
            epochs=int(args.ppo_epochs),
            lane_minibatch_size=int(args.ppo_lane_minibatch),
            sequence_chunk_steps=int(args.ppo_sequence_chunk),
            clip_range=float(args.ppo_clip_range),
            value_coefficient=float(args.ppo_value_coefficient),
            entropy_coefficient=float(args.ppo_entropy_coefficient),
            max_grad_norm=(None if args.ppo_max_grad_norm <= 0.0 else float(args.ppo_max_grad_norm)),
            adam_eps=float(args.ppo_adam_eps),
            weight_decay=float(args.ppo_weight_decay),
            gamma=reward_config.shaping_gamma,
            gae_lambda=float(args.gae_lambda),
            validate=not args.no_validate,
        )
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
    fast = None
    fast_verification = None
    if args.fast_forward:
        fast = FastForwardV4(
            model,
            device=device,
            rows=args.count * len(seats),
            precision=args.inference_precision,
            cuda_graphs=not args.no_cuda_graphs,
            full_cuda_graph=bool(args.full_cuda_graph),
        )
        state = fast.state
    # The overlapped loop needs a second, independent model: the collector keeps
    # sampling with stable weights while the worker updates this copy.  Loading
    # the checkpoint twice (rather than deepcopy) makes the two models visibly
    # separate objects and verifies they start from the same weight bytes.
    trainer_model = model
    if args.overlap_update:
        update_policy = load_policy_v4(args.checkpoint, device=device)
        trainer_model = update_policy.model
        trainer_model.eval()
        if weights_digest(trainer_model) != weights_digest(model):
            raise SystemExit("the second model loaded different weights from the same checkpoint")
    trainer = (
        None
        if training_config is None
        else CrforgeTrainer(
            trainer_model,
            config=training_config,
            checkpoint_id=str(policy.checkpoint_id),
            device=device,
            base_update_step=int(policy.update_step),
        )
    )
    resume_state = None
    if args.resume:
        if trainer is None:  # defensive: the flag check above already pairs it with --ppo-update
            raise SystemExit("--resume needs a trainer")
        # The policy above loaded the weights; this brings the Adam moments and
        # the loop counters from the same file, and refuses a checkpoint that
        # lacks them (a restart of Adam is a different run, not a resume).
        try:
            resume_state = load_resume_state(args.checkpoint, trainer.model, trainer.optimizer, device=device)
        except (TypeError, ValueError) as error:
            raise SystemExit(f"--resume refused: {error}") from error
        # The cadence is a CLI setting, so a resume must state the one the
        # checkpoint was actually trained under: silently switching it would
        # change when the optimizer steps mid-run.
        recorded_policy = resume_state.state_policy
        if recorded_policy is not None and recorded_policy != training_config.state_policy:
            raise SystemExit(
                f"--resume: the checkpoint was trained with --update-state-policy {recorded_policy}, "
                f"not {training_config.state_policy}"
            )
        if (
            recorded_policy == "wave"
            and resume_state.segments_per_wave is not None
            and int(resume_state.segments_per_wave) != training_config.segments_per_wave
        ):
            raise SystemExit(
                f"--resume: the checkpoint collected {resume_state.segments_per_wave} segments per wave, "
                f"not {training_config.segments_per_wave}"
            )
        trainer.resume(resume_state)
        if args.overlap_update:
            # The worker's model holds the resumed weights; the collector must
            # start from the same bytes or the first segment would bind the seed.
            copy_weights(trainer.model, model)
            if weights_digest(model) != resume_state.weights_digest:
                raise SystemExit("--resume: the collection model did not receive the resumed weights")
        print(json.dumps({"event": "resumed", **resume_state.as_dict()}), flush=True)
    overlap = None
    if args.overlap_update:
        if trainer is None:  # defensive: the flag check above already pairs it with --ppo-update
            raise SystemExit("--overlap-update needs a trainer")
        overlap = OverlapUpdater(trainer, model, device=device)
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

    if not args.no_warmup_forward:
        # The engine warmup above never runs the model.  On CUDA the first
        # sampled decision pays the RNN's lazy setup and the allocator's first
        # growth — a fixed ~5-7 s at every batch size (FINDINGS.md §3.67) —
        # which lands in decision 0's bucket and inflates a mean rate while every
        # median stays blind to it.  Prime the exact sampled path here, then put
        # the RNG back so the action stream is byte-identical to a cold run.
        warm_stacks = {seat: rows[seat].tensorize(batch) for seat in seats}
        warm_batch = interleave_rows([warm_stacks[seat] for seat in seats])
        rng_state = torch.get_rng_state()
        numpy_state = np.random.get_state()
        cuda_rng_states = torch.cuda.get_rng_state_all() if device.type == "cuda" else None
        try:
            with torch.inference_mode():
                warm_starts = torch.zeros(args.count * len(seats), dtype=torch.bool, device=device)
                if fast is not None:
                    output = fast.sample(warm_batch, state, episode_start=warm_starts)
                else:
                    output = model.sample_for_ppo_rollout(
                        warm_batch.to_model_input(device),
                        state,
                        episode_start=warm_starts,
                        validate=not args.no_validate,
                    )
                # Prime the decode path too; it is inside the measured wall and
                # has its own first-call cost.
                for index, seat in enumerate(seats):
                    sequence = _seat_rows(output.actions, index, len(seats))
                    plan = decoders[seat].decode(batch, sequence, warm_stacks[seat].candidates, seat=seat)
                    plan_window_ticks(plan.offsets, window)
                if fast is not None and args.verify_fast_forward:
                    # The gate is numerical, not the wall: the half-precision or
                    # graph-replayed path must sample the same actions as eager
                    # fp32 on this batch, and stay inside the log-prob tolerance.
                    fast_verification = fast.verify(warm_batch, fast.state, episode_start=warm_starts)
                    if not fast_verification["passed"]:
                        raise SystemExit(
                            "fast-forward verification failed: "
                            f"{fast_verification['action_mismatches']} "
                            f"{fast_verification['max_abs_delta']} tol {fast_verification['tolerance']}"
                        )
            if fast is not None:
                # A sampled decision advances the table; the warmup must not, or
                # the first real decision would start from a fictitious turn.
                fast.rebind(model.initial_state(args.count * len(seats), device=device))
            if device.type == "cuda":
                torch.cuda.synchronize()
        finally:
            torch.set_rng_state(rng_state)
            np.random.set_state(numpy_state)
            if cuda_rng_states is not None:
                torch.cuda.set_rng_state_all(cuda_rng_states)

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
    # Sub-buckets of the CPU half.  They are recorded beside ``ms`` instead of in
    # it: a section is a subset of ``tensorize_ms`` and a sum of every bucket is
    # what ``timing_accounting.py`` reconciles against the wall.
    section_names = (
        "cards_ms",
        "groups_ms",
        "towers_ms",
        "empty_ms",
        "candidates_ms",
        "relations_ms",
        "planes_ms",
        "scalars_ms",
    )
    cpu_half: dict[str, list[float]] = {
        **{name: [] for name in section_names},
        "interleave_ms": [],
        "decode_only_ms": [],
        "record_ms": [],
        "plan_ms": [],
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
    flushed_partial_wave = 0
    pipeline_started = time.perf_counter()
    profiler = cProfile.Profile() if args.profile_collection else None
    if profiler is not None:
        profiler.enable()
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
            cpu_samples: dict[str, list[float]] = {name: [] for name in cpu_half}
            illegal_peak = 0
            rollout_store = None
            segment_policy_state_id = None
            boundary_wall = 0.0
            if rollout_enabled:
                segment_policy_state_id = policy_state_id(model, str(policy.checkpoint_id))
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
                    device=device,
                )
            for decision in range(args.decisions_per_segment):
                started = time.perf_counter()
                episode_start_lanes = reset_mask.copy()
                if reset_mask.any():
                    for seat in seats:
                        rows[seat].reset_rows(batch, reset_mask)

                section_timings: dict[str, float] = {}
                stacks = {seat: rows[seat].tensorize(batch, timings=section_timings) for seat in seats}
                sections_done = time.perf_counter()
                stacked = interleave_rows([stacks[seat] for seat in seats])
                tensorize_done = time.perf_counter()

                episode_start = torch.from_numpy(np.repeat(episode_start_lanes, len(seats))).to(device)
                # ``--no-validate`` covers the model's own contract check too: it is
                # pure Python, and on a GPU every value it inspects syncs the device,
                # so leaving it on makes a fast forward look slower than the CPU's.
                validate_rows = not args.no_validate
                with torch.inference_mode():
                    if fast is not None:
                        output = (
                            fast.act(stacked, state, episode_start=episode_start)
                            if args.greedy
                            else fast.sample(stacked, state, episode_start=episode_start)
                        )
                    elif args.greedy:
                        output = model.act(
                            stacked.to_model_input(device),
                            state,
                            episode_start=episode_start,
                            validate=validate_rows,
                        )
                    else:
                        output = model.sample_for_ppo_rollout(
                            stacked.to_model_input(device),
                            state,
                            episode_start=episode_start,
                            validate=validate_rows,
                        )
                state = output.next_state
                if device.type == "cuda":
                    # Every bucket below starts with a host read (``.cpu().numpy()``
                    # in the decoder), which would otherwise absorb the queued
                    # forward and make ``inference_ms`` a launch time, not a cost.
                    torch.cuda.synchronize()
                inference_done = time.perf_counter()

                plans = {}
                eligible = np.zeros((args.count,), dtype=np.int64)
                acted = np.zeros((args.count,), dtype=np.int64)
                intra = {}
                decode_only_ms = 0.0
                record_ms = 0.0
                plan_ms = 0.0
                forced_mode = args.force_second_micro
                if forced_mode == "alternate":
                    forced_mode = "shift" if decision % 2 == 0 else "drop"
                with torch.inference_mode():
                    for index, seat in enumerate(seats):
                        sequence = _seat_rows(output.actions, index, len(seats))
                        candidates = stacks[seat].candidates
                        # Convert the candidate columns once: the decode, the
                        # duplicate probe and the previous-action record all read
                        # the same rows, and on a GPU each conversion copies.
                        tables = candidate_tables(candidates)
                        if forced_mode != "off":
                            # Decode the sample first: the duplicate is built from
                            # the action the policy actually chose.
                            sampled = decoders[seat].decode(batch, sequence, candidates, seat=seat, tables=tables)
                            sequence, forced = _force_second_micro_actions(
                                sequence,
                                sampled,
                                candidates,
                                mode=forced_mode,
                                budget=max(1, args.count // 4),
                                window=window,
                                tables=tables,
                            )
                            counts["forced_second_micro_actions"] += forced
                            totals["forced_second_micro_actions"] += forced
                        decode_started = time.perf_counter()
                        plan = decoders[seat].decode(batch, sequence, candidates, seat=seat, tables=tables)
                        decode_only_ms += (time.perf_counter() - decode_started) * 1000.0
                        record_started = time.perf_counter()
                        rows[seat].record_action(
                            sequence, stacks[seat], validate=not args.no_validate, tables=tables
                        )
                        record_ms += (time.perf_counter() - record_started) * 1000.0
                        played = plan.actions > 0
                        if played.any() and not bool(plan.masked[played].all()):
                            raise RuntimeError(f"decoded a closed engine mask for seat {seat}")
                        plans[seat] = plan
                        # A lane is eligible when this seat had at least one open
                        # deploy candidate: that is exactly what the decoder can
                        # turn into an engine action.
                        open_deploy = (
                            np.asarray(tables.mask, dtype=bool)
                            & (np.asarray(tables.native_hand_slot) >= 0)
                            & (np.asarray(tables.variant) == CANDIDATE_DEPLOY)
                        )
                        eligible += open_deploy.any(axis=1).astype(np.int64)
                        acted += ((np.asarray(plan.steps) > 0) & open_deploy.any(axis=1)).astype(np.int64)
                        plan_started = time.perf_counter()
                        intra[seat], shifted, dropped = plan_window_ticks(plan.offsets, window)
                        plan_ms += (time.perf_counter() - plan_started) * 1000.0
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
                for name in section_names:
                    cpu_samples[name].append(section_timings.get(name, 0.0))
                cpu_samples["interleave_ms"].append((tensorize_done - sections_done) * 1000.0)
                cpu_samples["decode_only_ms"].append(decode_only_ms)
                cpu_samples["record_ms"].append(record_ms)
                cpu_samples["plan_ms"].append(plan_ms)

            segment_rollout = None
            segment_updates: list[dict[str, Any]] = []
            segment_boundary_ms = 0.0
            if rollout_store is not None:
                boundary_started = time.perf_counter()
                # The observation after the last decision is the time-limit
                # bootstrap every still-running lane needs at the segment cut.
                boundary_stacks = {seat: rows[seat].tensorize(batch) for seat in seats}
                boundary_batch = interleave_rows([boundary_stacks[seat] for seat in seats])
                with torch.inference_mode():
                    boundary_starts = torch.zeros(args.count * len(seats), dtype=torch.bool, device=device)
                    if fast is not None:
                        context = fast.forward(boundary_batch, state, episode_start=boundary_starts)
                    else:
                        context = model.forward(
                            boundary_batch.to_model_input(device),
                            state,
                            episode_start=boundary_starts,
                            validate=not args.no_validate,
                        )
                rollout = rollout_store.finalize(boundary_value=context.value.detach().float())
                segment_rollout = rollout_statistics(
                    rollout, gamma=reward_config.shaping_gamma, gae_lambda=float(args.gae_lambda)
                )
                segment_rollout["summary"] = rollout_store.summary().as_dict()
                segment_rollout["storage_mib"] = round(rollout_store.storage_bytes() / 2**20, 3)
                segment_boundary_ms = (time.perf_counter() - boundary_started) * 1000.0
                if trainer is not None:
                    if overlap is not None:
                        # Close this segment: join the previous update, sync the
                        # collection weights at the boundary, and start this
                        # segment's update in the worker.  The next segment
                        # collects while it runs.
                        boundary = overlap.submit(
                            rollout,
                            segment=segment,
                            expected_policy_state_id=str(segment_policy_state_id),
                        )
                        boundary_wall = float(boundary["boundary_wall_s"])
                        state = trainer.boundary_state(state, rows=args.count * len(seats))
                        if fast is not None:
                            fast.rebind(state)
                            state = fast.state
                    else:
                        # The optimizer steps at the wave boundary; the recurrence
                        # policy decides whether the next segment keeps this state.
                        updates = trainer.enqueue(
                            rollout, segment=segment, expected_policy_state_id=str(segment_policy_state_id)
                        )
                        if updates is not None:
                            segment_updates = updates
                            state = trainer.boundary_state(state, rows=args.count * len(seats))
                            if fast is not None:
                                fast.rebind(state)
                                state = fast.state

            wall = time.perf_counter() - segment_started
            update_wall = sum(float(update["wall_s"]) for update in segment_updates)
            collection_wall = max(wall - update_wall, 0.0)
            if overlap is not None:
                # The segment clock includes the boundary join + weight sync that
                # closes it; the update itself is in the worker's ledger.
                collection_wall = max(wall - boundary_wall, 0.0)
            for name, values in samples.items():
                timing[name].extend(values)
            for name, values in cpu_samples.items():
                cpu_half[name].extend(values)
            buckets = _timing_buckets(samples)
            entry = {
                "segment": segment,
                "wall_s": wall,
                "update_wall_s": update_wall,
                "collection_wall_s": collection_wall,
                "boundary_wall_s": boundary_wall,
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
                "updates": segment_updates,
                **buckets,
                # The CPU half's own breakdown: the sections of ``tensorize_ms``,
                # the interleave, and the three parts of ``decode_ms``.  Recorded
                # per decision (means) and deliberately outside ``ms`` so the
                # accounting tool's bucket sum still equals the wall.
                "cpu_half": {
                    "seats": len(seats),
                    "lanes": args.count,
                    "rows": args.count * len(seats),
                    "breakdown_ms_mean": {
                        name: statistics.fmean(values) if values else 0.0 for name, values in cpu_samples.items()
                    },
                    "whole_ms_mean": {
                        "tensorize": statistics.fmean(samples["tensorize_ms"]) if samples["tensorize_ms"] else 0.0,
                        "decode": statistics.fmean(samples["decode_ms"]) if samples["decode_ms"] else 0.0,
                        "step": statistics.fmean(samples["step_ms"]) if samples["step_ms"] else 0.0,
                        "facts": statistics.fmean(samples["facts_ms"]) if samples["facts_ms"] else 0.0,
                    },
                },
                "segment_boundary_ms": segment_boundary_ms,
                **_timing_residual(
                    buckets,
                    collection_wall_s=collection_wall,
                    decisions=counts["decisions"],
                    boundary_ms=segment_boundary_ms,
                ),
            }
            segments.append(entry)
            for name, value in counts.items():
                totals[name] += value
            totals["illegal_peak"] = max(totals["illegal_peak"], illegal_peak)
            print(json.dumps(entry), flush=True)

        pipeline_wall = None
        if overlap is not None:
            # Join the last update and bring the collection model up to date, then
            # attach each worker update to its segment.  The pipeline wall is the
            # loop's wall, not the sum of the segment clocks: the updates overlap
            # the collection, so collection + update deliberately exceeds it.
            # This join must happen before anything reads trainer.pending: the
            # worker is the only writer while an update is in flight.
            overlap.finish()
            pipeline_wall = time.perf_counter() - pipeline_started
            for update in trainer.update_entries:
                segments[int(update["segment"])]["updates"].append(update)
            for item in segments:
                item["update_wall_s"] = sum(float(update["wall_s"]) for update in item["updates"])

        if trainer is not None and trainer.pending:
            # A run whose segment count is not a whole number of waves still
            # trains on what it collected; the artifact marks the partial wave.
            partial_wave = len(trainer.pending)
            flushed = trainer.flush()
            flushed_partial_wave = partial_wave
            flush_wall = sum(float(update["wall_s"]) for update in flushed)
            last = segments[-1]
            last["updates"].extend(flushed)
            # The flush ran after the last segment's clock stopped: move both
            # clocks forward so the run total still adds up, but keep the
            # collection wall where the collection actually ended.
            last["wall_s"] += flush_wall
            last["update_wall_s"] += flush_wall
            last["lane_decisions_per_s"] = last["lane_decisions"] / last["wall_s"] if last["wall_s"] else 0.0
            last["matches_per_s"] = (
                last["lane_decisions"] / len(seats) / MATCH_DECISIONS / last["wall_s"] if last["wall_s"] else 0.0
            )
            if trainer.config.resets_recurrence:
                state = trainer.boundary_state(state, rows=args.count * len(seats))
                if fast is not None:
                    fast.rebind(state)
                    state = fast.state
            print(
                json.dumps({"event": "partial_wave_flush", "segments": partial_wave, "updates": len(flushed)}),
                flush=True,
            )

        faults_after = dict(vector.fault_counts())
        fault_delta = {
            str(key): int(faults_after.get(key, 0)) - int(faults_before.get(key, 0))
            for key in set(faults_after) | set(faults_before)
        }
        fault_delta = {key: value for key, value in fault_delta.items() if value}
        wall_total = pipeline_wall if pipeline_wall is not None else sum(item["wall_s"] for item in segments)
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
        update_entries = [update for item in segments for update in item["updates"]]
        update_totals = {
            "segments_updated": len(update_entries),
            "updates": len(update_entries),
            "time_steps": sum(int(item["time_steps"]) for item in update_entries),
            "valid_count": sum(float(item["valid_count"]) for item in update_entries),
            "wall_s": sum(float(item["wall_s"]) for item in update_entries),
            "finite": all(bool(item["finite"]) for item in update_entries) if update_entries else True,
            "mid_rollout_episode_starts": sum(
                int(segments[int(item["segment"])]["rollout"]["mid_rollout_episode_starts"]) for item in update_entries
            ),
            "multiple_episodes_per_lane": any(
                int(segments[int(item["segment"])]["rollout"]["mid_rollout_episode_starts"]) > 0
                for item in update_entries
            ),
        }
        update_gate = {
            "enabled": bool(args.ppo_update),
            **update_totals,
            "pass": (not args.ppo_update)
            or (
                bool(update_entries) and all(bool(item["pass"]) for item in update_entries) and update_totals["finite"]
            ),
        }
        pipeline_block = None
        if overlap is not None:
            update_wall_total = sum(float(item["wall_s"]) for item in update_entries)
            serial_equivalent = collection_wall_total + update_wall_total
            pipeline_block = {
                **overlap.summary(),
                "wall_s": wall_total,
                "collection_wall_s": collection_wall_total,
                "update_wall_s": update_wall_total,
                "boundary_wall_s": sum(float(item["boundary_wall_s"]) for item in segments),
                "serial_equivalent_wall_s": serial_equivalent,
                "wall_saved_s": serial_equivalent - wall_total,
                "speedup": (serial_equivalent / wall_total) if wall_total else 0.0,
                "collection_lane_decisions_per_s": (
                    totals["lane_decisions"] / collection_wall_total if collection_wall_total else 0.0
                ),
                "pipeline_lane_decisions_per_s": totals["lane_decisions"] / wall_total if wall_total else 0.0,
                "stale_updates": trainer.stale_updates,
                "sampling_policy_state_ids": [
                    str((item.get("rollout") or {}).get("policy_state_id", "")) for item in segments
                ],
                "update_start_policy_state_ids": [
                    str(item.get("policy_state_id_at_update_start", "")) for item in update_entries
                ],
                "semantics": (
                    "segment k is collected with the weights left by update k-1 while update k runs; "
                    "the update therefore starts one update behind the data it reads (stale), and "
                    "the collection model is only changed at a joined boundary"
                ),
            }
        checkpoint_info = None
        if args.save_checkpoint is not None:
            checkpoint_info = _save_checkpoint(
                args.save_checkpoint,
                trainer.model if trainer is not None else model,
                trainer,
                policy=policy,
                args=args,
                reward_config=reward_config,
            )
            print(json.dumps({"event": "checkpoint_saved", **checkpoint_info}), flush=True)
        if trainer is None:
            training = {"enabled": False, "pass": True, "note": "no update ran: pass --ppo-update with rollout storage"}
        else:
            training_pass = trainer.gate()
            resume_block = None
            if resume_state is not None:
                # A resume must continue the step count, not restart it: the
                # saved step has to be the resumed step plus the updates this
                # process ran.
                continued = (
                    None
                    if checkpoint_info is None
                    else int(checkpoint_info["update_step"])
                    == int(resume_state.update_step) + trainer.updates_this_run
                )
                resume_block = {
                    **resume_state.as_dict(),
                    "updates_this_run": trainer.updates_this_run,
                    "optimizer_digest_after": optimizer_signature(trainer.optimizer)[0],
                    "update_step_continued": continued,
                }
            training = {
                "enabled": True,
                **trainer.config.as_dict(),
                **training_pass,
                "base_update_step": trainer.base_update_step,
                "checkpoint_id": trainer.checkpoint_id,
                "optimizer_state": ("persistent_for_the_run" if trainer.optimizer.state else "no_steps_taken"),
                "behaviour_ids": sorted({str(item["policy_state_id_after"]) for item in trainer.update_entries}),
                "trace": trainer.trace,
                "partial_wave_segments_flushed": flushed_partial_wave,
                "checkpoint": checkpoint_info,
                "resume": resume_block,
                "pipeline": None if pipeline_block is None else {
                    key: pipeline_block[key]
                    for key in (
                        "collection_identity_checks",
                        "collection_identity_unchanged",
                        "weights_verified",
                        "boundary_wall_s",
                        "join_wall_s",
                        "sync_wall_s",
                        "update_in_flight_wall_s",
                        "stale_updates",
                        "speedup",
                    )
                },
                "pass": training_pass["pass"]
                and (checkpoint_info is None or bool(checkpoint_info["reload_match"]))
                and (resume_block is None or resume_block["update_step_continued"] is not False),
            }
        if profiler is not None:
            # Stop the profiler before the artifact is assembled: the loop is the
            # measurement, not the JSON.
            profiler.disable()
        profile_summary = (
            None if profiler is None else _profile_summary(profiler, decisions=totals["decisions"])
        )
        artifact = {
            "schema": "crforge-self-play.v3",
            "measured": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "collection_profile": profile_summary,            "shape": {
                "count": args.count,
                "segments": args.segments,
                "decisions_per_segment": args.decisions_per_segment,
                "seats": list(seats),
                "device": str(device),
                "seed": args.seed,
                "warmup_ticks": args.warmup_ticks,
                "warmup_forward": not args.no_warmup_forward,
                "max_ticks": args.max_ticks,
                "policy_cadence_ticks": window,
                "engine_decision_ticks": 1,
                "validated_every_decision": not args.no_validate,
                "sampled": not args.greedy,
                "rollout_storage": rollout_enabled,
                "forced_second_micro": args.force_second_micro,
                "training": bool(args.ppo_update),
                "resume": bool(args.resume),
                "overlap_update": bool(args.overlap_update),
                "update_state_policy": args.update_state_policy,
                "wave_segments": (args.wave_segments if args.update_state_policy == "wave" else 1),
                "save_checkpoint": None if args.save_checkpoint is None else str(args.save_checkpoint),
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
            "inference": {
                "fast_forward": fast.describe() if fast is not None else {"enabled": False},
                "verification": fast_verification,
                "sampling": (
                    "graph-replayed full call (encode + core + decode)"
                    if fast is not None and fast.full_cuda_graph
                    else ("eager outside the captured graph" if fast is not None and fast.cuda_graphs else "eager")
                ),
                "steady_state": _inference_steady_state(segments),
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
                    "update_cadence": (
                        "carry/reset: one update per segment, immediately after collection; wave: "
                        "the behaviour weights stay frozen for the wave's segments, their updates "
                        "run at the wave end, and the recurrence resets there"
                    ),
                    "overlap_update": (
                        "none"
                        if overlap is None
                        else "each segment's update runs on a second model in a worker thread while "
                        "the next segment collects; every update after the first is stale by one "
                        "update and the trace records it"
                    ),
                },
            },
            "training": training,
            "pipeline": pipeline_block,
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
                "training_pass": training["pass"],
                "pipeline_pass": (
                    True
                    if pipeline_block is None
                    else bool(
                        pipeline_block["collection_identity_unchanged"] and pipeline_block["weights_verified"]
                    )
                ),
                "pass": (
                    rejected == 0
                    and eligible_total > 0
                    and act_rate > 0.0
                    and reward_mismatches == 0
                    and rollout_finite
                    and update_gate["pass"]
                    and training["pass"]
                    and (
                        pipeline_block is None
                        or bool(
                            pipeline_block["collection_identity_unchanged"]
                            and pipeline_block["weights_verified"]
                        )
                    )
                ),
            },
            "totals": {
                **totals,
                "wall_s": wall_total,
                "update_wall_s": sum(item["update_wall_s"] for item in segments),
                "collection_wall_s": sum(item["collection_wall_s"] for item in segments),
                "boundary_wall_s": sum(item["boundary_wall_s"] for item in segments),
                "lane_decisions_per_s": totals["lane_decisions"] / wall_total if wall_total else 0.0,
                "collection_lane_decisions_per_s": (
                    totals["lane_decisions"] / collection_wall_total if collection_wall_total else 0.0
                ),
                "matches_per_s": (
                    totals["lane_decisions"] / len(seats) / MATCH_DECISIONS / wall_total if wall_total else 0.0
                ),
                **_timing_buckets(timing),
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
