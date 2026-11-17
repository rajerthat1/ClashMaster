#!/usr/bin/env python3
"""C4 reward-share probe: does a longer segment make the return rankable?

The horizon's return curve is mostly the value bootstrap at the segment cut, not
observed reward.  ``reward_sum`` cannot answer how large the reward is: the two
seats' rewards are antisymmetric (``[s, -s]``), so it is identically zero in every
possible match.  This tool measures what does answer it, at several segment
lengths and with the training cadence's own trace::

    reward_share = (reward_abs_sum / rows) / abs(return_mean)
    rows         = valid_count / time_steps

``reward_abs_sum / rows`` is the reward mass one lane accumulated inside the
window (terminal result plus crown/tower shaping); the denominator is the magnitude
of the GAE return the update scored.  Everything the share does not cover is the
boundary bootstrap.  A shape whose typical (median) share is at
``REWARD_SHARE_RANKING_THRESHOLD`` can be ranked on the return; below it the
return is a value-fit diagnostic, not a score.

The probe runs the producer's default ``reset`` cadence at each ``--decisions``
length, all at the same update count, so the shapes differ only in the window::

    OMP_NUM_THREADS=4 .venv/bin/python tools/crforge/reward_share.py \\
        --decisions 24 60 120 --updates 16 --seeds 1 2 \\
        --output runs/crforge-c4/reward-share.json

The artifact is ``crforge-c4-reward-share.v1``.  ``--reuse`` re-aggregates the
per-run artifacts in ``--run-dir``; ``--aggregate-only`` also accepts a missing
run (it is listed under ``missing_runs`` and ``gate.complete`` is false), so a
probe that was stopped still reports the shapes it finished.  This is a crforge
diagnostic: native parity is unchanged and is never replaced by it.
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
SCHEMA = "crforge-c4-reward-share.v1"

_TOOL_DIR = Path(__file__).resolve().parent
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import learning_horizon  # the per-update summary and the reward-share formula


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _optional_mean(values: list[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return _mean(present) if present else None


def _fraction(row: dict[str, Any]) -> float | None:
    """Share of this run's updates that reached the ranking threshold."""

    updates = int(row.get("updates_run") or 0)
    reached = row.get("updates_reward_share_at_least_threshold")
    if updates <= 0 or reached is None:
        return None
    return float(reached) / float(updates)


def _aggregate(runs: list[dict[str, Any]], decisions: int) -> dict[str, Any]:
    selected = [row for row in runs if int(row["decisions_per_segment"]) == decisions]
    if not selected:
        raise ValueError(f"no runs at {decisions} decisions per segment")
    shares = [_fraction(row) for row in selected]
    return {
        "decisions_per_segment": decisions,
        "runs": len(selected),
        "seeds": [int(row["seed"]) for row in selected],
        "updates_run_mean": _mean([float(row["updates_run"]) for row in selected]),
        "reward_share_median_mean": _optional_mean([row.get("reward_share_median") for row in selected]),
        "reward_share_mean_mean": _optional_mean([row.get("reward_share_mean") for row in selected]),
        "reward_share_max_mean": _optional_mean([row.get("reward_share_max") for row in selected]),
        "reward_abs_max_median_mean": _optional_mean([row.get("reward_abs_max_median") for row in selected]),
        "updates_with_reward_mean": _optional_mean([row.get("updates_with_reward") for row in selected]),
        "fraction_updates_at_least_threshold_mean": _optional_mean(shares),
        "return_last_mean_mean": _mean([float(row["return_trend"]["last_mean"]) for row in selected]),
        "wall_s_mean": _mean([float(row["wall_s"]) for row in selected]),
        "training_pass_all": all(bool(row["training_pass"]) for row in selected),
        "stale_updates_mean": _mean([float(row["stale_updates"]) for row in selected]),
    }


def build_comparison(summary: dict[str, dict[str, Any]], baseline: int) -> dict[str, Any]:
    """The share by shape, its growth over the baseline, and the verdict.

    The verdict is deliberately conservative: a shape only qualifies when its
    typical update is reward-dominated (median share at or above the threshold)
    *and* at least half of its updates reach that on their own.  A single terminal
    update can push a mean above the line; it does not make the curve a score.
    """

    threshold = learning_horizon.REWARD_SHARE_RANKING_THRESHOLD
    by_shape: dict[str, float | None] = {}
    fractions: dict[str, float | None] = {}
    for decisions, row in summary.items():
        by_shape[str(decisions)] = row["reward_share_median_mean"]
        fractions[str(decisions)] = row["fraction_updates_at_least_threshold_mean"]

    baseline_share = by_shape.get(str(baseline))
    growth: dict[str, float | None] = {}
    for decisions, share in by_shape.items():
        if share is None or baseline_share is None or baseline_share <= 0.0:
            growth[decisions] = None
        else:
            growth[decisions] = float(share) / float(baseline_share)

    qualifying = [
        int(decisions)
        for decisions, row in summary.items()
        if row["reward_share_median_mean"] is not None
        and float(row["reward_share_median_mean"]) >= threshold
        and row["fraction_updates_at_least_threshold_mean"] is not None
        and float(row["fraction_updates_at_least_threshold_mean"]) >= 0.5
    ]
    rankable_at = min(qualifying) if qualifying else None
    return {
        "baseline_decisions": baseline,
        "reward_share_threshold": threshold,
        "share_median_by_decisions": by_shape,
        "fraction_updates_at_least_threshold": fractions,
        "share_growth_vs_baseline": growth,
        "decision_rule": (
            "rankable when a shape's mean per-run median reward share is >= the threshold "
            "and at least half of its updates reach it"
        ),
        "rankable_at_decisions": rankable_at,
        "return_is_a_score": rankable_at is not None,
    }


def _run(command: list[str], log_path: Path) -> float:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as handle:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
            env=dict(os.environ),
        )
    if completed.returncode != 0:
        raise SystemExit(f"producer failed ({completed.returncode}); see {log_path}")
    return time.perf_counter() - started


def _artifact_path(*, run_dir: Path, policy: str, seed: int, decisions: int) -> Path:
    return run_dir / f"{policy}-seed{seed}-d{decisions}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=4, help="lanes per VectorBattle")
    parser.add_argument(
        "--decisions",
        type=int,
        nargs="+",
        default=[24, 60, 120],
        help="decisions per segment to measure",
    )
    parser.add_argument("--updates", type=int, default=16, help="segments = updates per run")
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2])
    parser.add_argument("--policy", default="reset", choices=("reset", "carry"))
    parser.add_argument("--baseline-decisions", type=int, default=24)
    parser.add_argument("--ppo-sequence-chunk", type=int, default=24)
    parser.add_argument("--ppo-epochs", type=int, default=1)
    parser.add_argument("--ppo-learning-rate", type=float, default=1e-5)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--omp-threads", type=int, default=4)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--checkpoint", type=Path, default=Path(".resource/RoyaleFish/models/B-general-v275.pt"))
    parser.add_argument("--run-dir", type=Path, default=Path("runs/crforge-c4/reward-share"))
    parser.add_argument("--reuse", action="store_true", help="load existing per-run artifacts")
    parser.add_argument(
        "--aggregate-only",
        action="store_true",
        help="summarize the artifacts already on disk and report a missing run instead of collecting it",
    )
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c4/reward-share.json"))
    args = parser.parse_args(argv)

    if args.updates < 2:
        raise SystemExit("the probe needs at least two updates per run")
    if any(decisions < 1 for decisions in args.decisions):
        raise SystemExit("--decisions must be positive")
    if args.omp_threads < 1:
        raise SystemExit("--omp-threads must be positive")

    if args.baseline_decisions not in args.decisions:
        raise SystemExit("--baseline-decisions must be one of --decisions")

    os.environ["OMP_NUM_THREADS"] = str(args.omp_threads)
    args.run_dir.mkdir(parents=True, exist_ok=True)

    runs: list[dict[str, Any]] = []
    substrates: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for decisions in args.decisions:
        for seed in args.seeds:
            artifact_path = _artifact_path(
                run_dir=args.run_dir, policy=args.policy, seed=seed, decisions=decisions
            )
            log_path = args.run_dir / f"{args.policy}-seed{seed}-d{decisions}.log"
            print(f"{args.policy} seed {seed} @ {decisions} decisions: {artifact_path.name}", file=sys.stderr)
            if args.aggregate_only and not artifact_path.is_file():
                missing.append({"policy": args.policy, "seed": int(seed), "decisions_per_segment": int(decisions)})
                print("    missing: not collected", file=sys.stderr)
                continue
            if not ((args.reuse or args.aggregate_only) and artifact_path.is_file()):
                command = [
                    sys.executable,
                    str(PRODUCER),
                    "--count",
                    str(args.count),
                    "--segments",
                    str(args.updates),
                    "--decisions-per-segment",
                    str(decisions),
                    "--seed",
                    str(seed),
                    "--device",
                    args.device,
                    "--checkpoint",
                    str(args.checkpoint),
                    "--ppo-update",
                    "--update-state-policy",
                    args.policy,
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
                _run(command, log_path)
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            summary = learning_horizon._summarize(artifact, policy=args.policy, seed=seed)
            if summary["state_policy"] != args.policy:
                raise SystemExit(
                    f"{artifact_path} records state policy {summary['state_policy']!r}, "
                    f"expected {args.policy!r}"
                )
            summary["decisions_per_segment"] = int(decisions)
            summary["artifact"] = str(artifact_path)
            runs.append(summary)
            substrates.append(artifact.get("substrate") or {})
            share = summary["reward_share_median"]
            share_text = "n/a" if share is None else f"{share:.2e}"
            print(
                f"    updates {summary['updates_run']}, median share {share_text}, "
                f"max {summary['reward_share_max'] or 0.0:.2e}, "
                f"return {summary['return_trend']['last_mean']:.4f}, wall {summary['wall_s']:.1f}s",
                file=sys.stderr,
            )

    summary_by_shape = {
        str(decisions): _aggregate(runs, decisions)
        for decisions in args.decisions
        if any(int(row["decisions_per_segment"]) == decisions for row in runs)
    }
    if not summary_by_shape:
        raise SystemExit("no collected runs to summarize")
    substrate = substrates[0] if substrates else {}
    if any(json.dumps(item, sort_keys=True) != json.dumps(substrate, sort_keys=True) for item in substrates[1:]):
        raise SystemExit("the runs do not share one substrate; the comparison is not like-for-like")
    comparison = build_comparison(summary_by_shape, args.baseline_decisions)
    shares_present = all(row["reward_share_median_mean"] is not None for row in summary_by_shape.values())
    gate = {
        "all_training_pass": all(row["training_pass_all"] for row in summary_by_shape.values()),
        "all_updates_on_policy": all(row["stale_updates_mean"] == 0.0 for row in summary_by_shape.values()),
        "reward_share_present": shares_present,
        "complete": not missing,
        # ``pass`` is about the collected runs; ``complete`` says whether every
        # requested run exists, so a stopped probe can still be summarized.
        "pass": bool(
            shares_present
            and all(row["training_pass_all"] for row in summary_by_shape.values())
            and all(row["stale_updates_mean"] == 0.0 for row in summary_by_shape.values())
        ),
    }
    artifact = {
        "schema": SCHEMA,
        "measured": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "substrate": substrate,
        "shape": {
            "count": args.count,
            "decisions_per_segment": list(args.decisions),
            "baseline_decisions": args.baseline_decisions,
            "updates": args.updates,
            "actor_rows": args.count * 2,
            "seeds": list(args.seeds),
            "policy": args.policy,
            "device": args.device,
            "validate": bool(args.validate),
            "omp_num_threads": args.omp_threads,
            "checkpoint": str(args.checkpoint),
            "ppo_sequence_chunk": args.ppo_sequence_chunk,
            "ppo_epochs": args.ppo_epochs,
            "ppo_learning_rate": args.ppo_learning_rate,
        },
        "summary": summary_by_shape,
        "comparison": comparison,
        "gate": gate,
        "requests": {
            "policy": args.policy,
            "decisions": list(args.decisions),
            "seeds": list(args.seeds),
        },
        "missing_runs": missing,
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {args.output}", file=sys.stderr)
    header = (
        f"{'decisions':>9} {'runs':>4} {'updates':>7} {'share med':>10} {'share max':>10} "
        f"{'>= thr':>7} {'abs max med':>12} {'return last':>11} {'wall s':>8}"
    )
    print(header)
    for row in summary_by_shape.values():
        share = row["reward_share_median_mean"]
        fraction = row["fraction_updates_at_least_threshold_mean"]
        abs_max = row["reward_abs_max_median_mean"]
        print(
            f"{row['decisions_per_segment']:>9} {row['runs']:>4} {row['updates_run_mean']:>7.0f} "
            f"{(0.0 if share is None else share):>10.2e} "
            f"{(0.0 if row['reward_share_max_mean'] is None else row['reward_share_max_mean']):>10.2e} "
            f"{(0.0 if fraction is None else fraction):>7.2f} "
            f"{(0.0 if abs_max is None else abs_max):>12.2e} "
            f"{row['return_last_mean_mean']:>11.4f} {row['wall_s_mean']:>8.1f}"
        )
    if missing:
        print(
            f"note: {len(missing)} requested run(s) are missing: "
            + ", ".join(f"seed {item['seed']} @ {item['decisions_per_segment']}" for item in missing)
        )
    verdict = comparison["rankable_at_decisions"]
    if verdict is None:
        print(
            "verdict: the observed reward is too small a share of the return to rank on; "
            "treat the return as a value-fit diagnostic"
        )
    else:
        print(f"verdict: rankable at {verdict} decisions per segment")
    print(f"gate: {json.dumps(gate)}")
    return 0 if gate["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
