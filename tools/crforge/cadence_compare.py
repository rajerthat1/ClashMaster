#!/usr/bin/env python3
"""C4 cadence gate: ``reset`` vs ``carry`` vs ``wave`` on one shape and seed set.

The C4 trainer states the recurrence policy at an update boundary
(``crforge_bridge/training.py``), but the choice was never measured against
itself: session 29's wave run and reset run used different shapes (48 vs 64
decisions), so their KL/clip numbers are not comparable.  This tool removes that
excuse.  It runs the producer at one shape, one checkpoint and one seed list for
every policy, then classifies each update as **on-policy** (the weights that
sampled its rollout are the weights the update starts from) or **stale** (an
earlier update in the same wave already replaced them).

What the policies actually differ in:

* ``reset`` — one segment collected, one update, recurrence zeroed.  Every
  update sees on-policy data; the LSTM is cut at every segment.
* ``carry`` — one segment collected, one update, recurrence kept.  Every update
  sees on-policy data, but the first state of a segment was computed by the
  pre-update weights.
* ``wave`` — ``--wave-segments`` segments collected under one frozen behaviour
  id, then their updates applied in order.  The first update of a wave is
  on-policy; every later one is stale by construction.  It is the native
  collect-then-update cadence, and its cost is measurable as the stale-update
  KL/clip against the on-policy baseline.

Run one shape across seeds, sequentially (wall times are only comparable when
the host is not running another producer)::

    .venv/bin/python tools/crforge/cadence_compare.py \\
        --count 8 --segments 6 --decisions-per-segment 48 --seeds 1 2 \\
        --output runs/crforge-c4/cadence-compare.json

The per-run producer artifacts stay under ``--run-dir`` and are named
``<policy>-seed<seed>.json``; ``--reuse`` loads an existing one instead of
re-collecting it.  The artifact is a summary, not a verdict: the cadence
decision is recorded in the session notes and the native notes.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCER = REPO_ROOT / "tools" / "crforge" / "self_play.py"
SCHEMA = "crforge-cadence-compare.v1"
POLICIES = ("reset", "carry", "wave")


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def classify_updates(update_entries: list[dict[str, Any]]) -> list[bool]:
    """True when an update's rollout was sampled by weights already replaced.

    The trainer records three things now: the id the rollout was sampled with
    (``sampling_policy_state_id``), the id of the weights the update actually
    started from (``policy_state_id_at_update_start``), and the verdict
    (``stale``).  Prefer the measured verdict: in a pipelined run the update
    starts from newer weights than the ones that sampled the rollout, which the
    old id-chain comparison cannot see because ``policy_state_id_before`` is the
    *sampling* id.  Older artifacts only carry the chain, so keep that fallback.
    """

    if update_entries and all("stale" in entry for entry in update_entries):
        return [bool(entry["stale"]) for entry in update_entries]
    stale: list[bool] = []
    previous_after: str | None = None
    for entry in update_entries:
        before = str(entry.get("policy_state_id_before", ""))
        after = str(entry.get("policy_state_id_after", ""))
        stale.append(previous_after is not None and before != previous_after)
        previous_after = after
    return stale


def summarize_run(artifact: dict[str, Any]) -> dict[str, Any]:
    """The cadence-relevant fields of one producer artifact."""

    training = artifact.get("training") or {}
    trace = list(training.get("trace") or [])
    segments = list(artifact.get("segments") or [])
    update_entries = [entry for segment in segments for entry in segment.get("updates") or []]
    stale = classify_updates(update_entries)
    if len(stale) != len(trace):
        raise ValueError(
            f"artifact has {len(trace)} trace rows but {len(stale)} update entries; "
            "the cadence classification would be wrong"
        )
    sampling_ids = [str((segment.get("rollout") or {}).get("policy_state_id", "")) for segment in segments]
    distinct_ids = len({value for value in sampling_ids if value})

    on_policy_kl = [float(row["approx_kl"]) for row, flag in zip(trace, stale, strict=True) if not flag]
    stale_kl = [float(row["approx_kl"]) for row, flag in zip(trace, stale, strict=True) if flag]
    on_policy_clip = [float(row["clip_fraction"]) for row, flag in zip(trace, stale, strict=True) if not flag]
    stale_clip = [float(row["clip_fraction"]) for row, flag in zip(trace, stale, strict=True) if flag]
    totals = artifact.get("totals") or {}
    pipeline = artifact.get("pipeline") or {}
    return {
        "state_policy": training.get("state_policy"),
        "segments_per_wave": training.get("segments_per_wave"),
        "updates": int(training.get("updates", 0)),
        "segments": len(segments),
        "lane_decisions": int(totals.get("lane_decisions", 0)),
        "stale_updates": int(sum(stale)),
        "on_policy_updates": int(len(stale) - sum(stale)),
        "on_policy_approx_kl_mean": _mean(on_policy_kl),
        "stale_approx_kl_mean": _mean(stale_kl),
        "on_policy_abs_approx_kl_mean": _mean([abs(value) for value in on_policy_kl]),
        "stale_abs_approx_kl_mean": _mean([abs(value) for value in stale_kl]),
        "on_policy_clip_fraction_mean": _mean(on_policy_clip),
        "stale_clip_fraction_mean": _mean(stale_clip),
        "approx_kl_trace": [float(row["approx_kl"]) for row in trace],
        "clip_fraction_trace": [float(row["clip_fraction"]) for row in trace],
        "return_mean_trace": [float(row["return_mean"]) for row in trace],
        "value_explained_variance_trace": [float(row["value_explained_variance"]) for row in trace],
        "gradient_norm_trace": [float(row["gradient_norm"]) for row in trace],
        "sampling_ids": sampling_ids,
        "distinct_sampling_ids": distinct_ids,
        "updates_per_sampling_id": (len(trace) / distinct_ids) if distinct_ids else 0.0,
        "act_lane_decisions": int(totals.get("act_lane_decisions", 0)),
        "eligible_lane_decisions": int(totals.get("eligible_lane_decisions", 0)),
        "eligible_act_rate": (
            int(totals.get("act_lane_decisions", 0)) / int(totals.get("eligible_lane_decisions", 1))
            if int(totals.get("eligible_lane_decisions", 0))
            else 0.0
        ),
        "trace_finite": bool(training.get("trace_finite")),
        "collection_wall_s": float(totals.get("collection_wall_s", 0.0)),
        "update_wall_s": float(totals.get("update_wall_s", 0.0)),
        "wall_s": float(totals.get("wall_s", 0.0)),
        "collection_lane_decisions_per_s": float(totals.get("collection_lane_decisions_per_s", 0.0)),
        "boundary_wall_s": float(totals.get("boundary_wall_s", 0.0)),
        "pipeline_enabled": bool(pipeline),
        "pipeline_wall_s": float(pipeline.get("wall_s", 0.0)),
        "pipeline_speedup": float(pipeline.get("speedup", 0.0)),
        "pipeline_serial_equivalent_wall_s": float(pipeline.get("serial_equivalent_wall_s", 0.0)),
        "pipeline_wall_saved_s": float(pipeline.get("wall_saved_s", 0.0)),
        "pipeline_update_in_flight_wall_s": float(pipeline.get("update_in_flight_wall_s", 0.0)),
        "collection_identity_unchanged": pipeline.get("collection_identity_unchanged"),
        "training_pass": bool(training.get("pass")),
    }


def _aggregate(runs: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    rows = [run for run in runs if run["state_policy"] == policy]
    if not rows:
        raise ValueError(f"no runs for policy {policy!r}")
    return {
        "state_policy": policy,
        "runs": len(rows),
        "seeds": [int(run["seed"]) for run in rows],
        "segments_per_wave": rows[0]["segments_per_wave"],
        "updates_mean": _mean([run["updates"] for run in rows]),
        "on_policy_updates_mean": _mean([run["on_policy_updates"] for run in rows]),
        "stale_updates_mean": _mean([run["stale_updates"] for run in rows]),
        "on_policy_approx_kl_mean": _mean([run["on_policy_approx_kl_mean"] for run in rows]),
        "stale_approx_kl_mean": _mean([run["stale_approx_kl_mean"] for run in rows]),
        "on_policy_abs_approx_kl_mean": _mean([run["on_policy_abs_approx_kl_mean"] for run in rows]),
        "stale_abs_approx_kl_mean": _mean([run["stale_abs_approx_kl_mean"] for run in rows]),
        "on_policy_clip_fraction_mean": _mean([run["on_policy_clip_fraction_mean"] for run in rows]),
        "stale_clip_fraction_mean": _mean([run["stale_clip_fraction_mean"] for run in rows]),
        "distinct_sampling_ids_mean": _mean([run["distinct_sampling_ids"] for run in rows]),
        "updates_per_sampling_id_mean": _mean([run["updates_per_sampling_id"] for run in rows]),
        "lane_decisions_mean": _mean([run["lane_decisions"] for run in rows]),
        "eligible_act_rate_mean": _mean([run["eligible_act_rate"] for run in rows]),
        "collection_wall_s_mean": _mean([run["collection_wall_s"] for run in rows]),
        "update_wall_s_mean": _mean([run["update_wall_s"] for run in rows]),
        "wall_s_mean": _mean([run["wall_s"] for run in rows]),
        "collection_lane_decisions_per_s_mean": _mean(
            [run["collection_lane_decisions_per_s"] for run in rows]
        ),
        "return_mean_trace_mean": _mean([_mean(run["return_mean_trace"]) for run in rows]),
        "training_pass_all": all(bool(run["training_pass"]) for run in rows),
    }


def build_comparison(summary: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The numbers the cadence decision turns on, side by side."""

    reset = summary.get("reset")
    carry = summary.get("carry")
    wave = summary.get("wave")
    comparison: dict[str, Any] = {}
    if wave is not None:
        comparison["wave_stale_updates"] = wave["stale_updates_mean"]
        comparison["wave_stale_over_on_policy_abs_kl"] = (
            wave["stale_abs_approx_kl_mean"] / wave["on_policy_abs_approx_kl_mean"]
            if wave["on_policy_abs_approx_kl_mean"]
            else None
        )
        comparison["wave_stale_abs_kl"] = wave["stale_abs_approx_kl_mean"]
        comparison["wave_on_policy_abs_kl"] = wave["on_policy_abs_approx_kl_mean"]
        comparison["wave_stale_clip_fraction"] = wave["stale_clip_fraction_mean"]
        comparison["wave_updates_per_sampling_id"] = wave["updates_per_sampling_id_mean"]
    if reset is not None and carry is not None:
        comparison["carry_minus_reset_wall_s"] = carry["wall_s_mean"] - reset["wall_s_mean"]
        comparison["carry_minus_reset_updates"] = carry["updates_mean"] - reset["updates_mean"]
        comparison["carry_minus_reset_abs_kl"] = (
            carry["on_policy_abs_approx_kl_mean"] - reset["on_policy_abs_approx_kl_mean"]
        )
    comparison["all_training_pass"] = all(row["training_pass_all"] for row in summary.values())
    return comparison


def _run_command(command: list[str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as handle:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if completed.returncode != 0:
        raise SystemExit(f"producer failed ({completed.returncode}); see {log_path}")
    print(f"    {time.perf_counter() - started:.1f}s -> {log_path.name}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=8, help="lanes per VectorBattle")
    parser.add_argument("--segments", type=int, default=6, help="segments collected per run")
    parser.add_argument("--decisions-per-segment", type=int, default=48)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2])
    parser.add_argument("--policies", nargs="+", choices=POLICIES, default=list(POLICIES))
    parser.add_argument("--wave-segments", type=int, default=2, help="segments per wave (wave policy)")
    parser.add_argument("--ppo-sequence-chunk", type=int, default=32)
    parser.add_argument("--ppo-epochs", type=int, default=1)
    parser.add_argument("--ppo-learning-rate", type=float, default=1e-5)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--jvm-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="extra option for every run's engine JVM (repeatable)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="keep the per-decision record validation (default off, as in the GPU runs)",
    )
    parser.add_argument("--checkpoint", type=Path, default=Path(".resource/RoyaleFish/models/B-general-v275.pt"))
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path("runs/crforge-c4/cadence"),
        help="per-run producer artifacts and logs",
    )
    parser.add_argument("--reuse", action="store_true", help="load an existing per-run artifact instead of re-collecting")
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c4/cadence-compare.json"))
    args = parser.parse_args(argv)

    if args.segments < 2:
        raise SystemExit("a cadence comparison needs at least two segments")
    if "wave" in args.policies and args.wave_segments > args.segments:
        raise SystemExit("--wave-segments cannot exceed --segments")

    args.run_dir.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    substrates: list[dict[str, Any]] = []
    for policy in args.policies:
        for seed in args.seeds:
            artifact_path = args.run_dir / f"{policy}-seed{seed}.json"
            log_path = args.run_dir / f"{policy}-seed{seed}.log"
            print(f"{policy} seed {seed}: {artifact_path.name}", file=sys.stderr)
            if not (args.reuse and artifact_path.is_file()):
                command = [
                    sys.executable,
                    str(PRODUCER),
                    "--count",
                    str(args.count),
                    "--segments",
                    str(args.segments),
                    "--decisions-per-segment",
                    str(args.decisions_per_segment),
                    "--seed",
                    str(seed),
                    "--device",
                    args.device,
                    "--checkpoint",
                    str(args.checkpoint),
                    "--ppo-update",
                    "--update-state-policy",
                    policy,
                    "--wave-segments",
                    str(args.wave_segments),
                    "--ppo-sequence-chunk",
                    str(args.ppo_sequence_chunk),
                    "--ppo-epochs",
                    str(args.ppo_epochs),
                    "--ppo-learning-rate",
                    str(args.ppo_learning_rate),
                    "--output",
                    str(artifact_path),
                ]
                if not args.validate:
                    command.append("--no-validate")
                for value in args.jvm_arg:
                    command.append(f"--jvm-arg={value}")
                _run_command(command, log_path)
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            summary = summarize_run(artifact)
            if summary["state_policy"] != policy:
                raise SystemExit(
                    f"{artifact_path} records state policy {summary['state_policy']!r}, expected {policy!r}"
                )
            substrates.append(artifact.get("substrate") or {})
            summary["seed"] = int(seed)
            runs.append(summary)
            print(
                f"    updates {summary['updates']} (stale {summary['stale_updates']}), "
                f"on-policy KL {summary['on_policy_approx_kl_mean']:.2e}, "
                f"stale KL {summary['stale_approx_kl_mean']:.2e}, "
                f"wall {summary['wall_s']:.1f}s",
                file=sys.stderr,
            )

    summary_by_policy = {policy: _aggregate(runs, policy) for policy in args.policies}
    substrate = substrates[0] if substrates else {}
    if any(json.dumps(item, sort_keys=True) != json.dumps(substrate, sort_keys=True) for item in substrates[1:]):
        raise SystemExit("the runs do not share one substrate; the comparison is not like-for-like")
    artifact = {
        "schema": SCHEMA,
        "measured": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "substrate": substrate,
        "shape": {
            "count": args.count,
            "segments": args.segments,
            "decisions_per_segment": args.decisions_per_segment,
            "actor_rows": args.count * 2,
            "lane_decisions_per_run": args.count * 2 * args.segments * args.decisions_per_segment,
            "seeds": list(args.seeds),
            "device": args.device,
            "validate": bool(args.validate),
            "omp_num_threads": os.environ.get("OMP_NUM_THREADS"),
            "checkpoint": str(args.checkpoint),
            "ppo_sequence_chunk": args.ppo_sequence_chunk,
            "ppo_epochs": args.ppo_epochs,
            "ppo_learning_rate": args.ppo_learning_rate,
        },
        "policies": list(args.policies),
        "summary": summary_by_policy,
        "comparison": build_comparison(summary_by_policy),
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {args.output}", file=sys.stderr)
    header = f"{'policy':<7} {'updates':>7} {'stale':>5} {'KL(pol)':>10} {'KL(stale)':>10} {'clip':>7} {'wall s':>8}"
    print(header)
    for policy, row in summary_by_policy.items():
        print(
            f"{policy:<7} {row['updates_mean']:>7.0f} {row['stale_updates_mean']:>5.0f} "
            f"{row['on_policy_approx_kl_mean']:>10.2e} {row['stale_approx_kl_mean']:>10.2e} "
            f"{row['on_policy_clip_fraction_mean']:>7.3f} {row['wall_s_mean']:>8.1f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
