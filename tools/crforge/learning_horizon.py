#!/usr/bin/env python3
"""C4 learning horizon: rank ``reset`` against ``carry`` over many updates.

Session 31's cadence gate compared six updates, which is a mechanics trace, not
a learning claim.  This tool runs the two on-policy cadences at one shape, one
checkpoint and one seed list for at least ``--updates`` optimizer steps and
summarizes the curve the trainer already records:

* the reward/return trace per update (first-window mean, last-window mean, delta
  and least-squares slope);
* the eligible-ACT rate per segment (a policy-behaviour signal, not a win rate);
* the wall and the update count.

``reset`` and ``carry`` differ only in whether the recurrent state survives the
segment's update.  Both are on-policy, so this is the comparison session 31
deferred.  Native matches are the promotion gate and are **not** run here:
crforge is never a promotion source, and the native parity cadence is unchanged.

The artifact is ``crforge-c4-learning-horizon.v1``.  Per-run producer artifacts
stay under ``--run-dir`` and ``--reuse`` re-aggregates them.  With more than one
seed the comparison also carries ``paired_seeds``: the same seed is the unit that
is compared, so a ranking that flips between seeds is visible rather than averaged
away::

    OMP_NUM_THREADS=4 .venv/bin/python tools/crforge/learning_horizon.py \\
        --count 4 --decisions-per-segment 24 --updates 50 --seeds 1 2 3 \\
        --output runs/crforge-c4/learning-horizon.json
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
SCHEMA = "crforge-c4-learning-horizon.v1"
POLICIES = ("reset", "carry")

_TOOL_DIR = Path(__file__).resolve().parent
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import cadence_compare  # the shared per-run summary and stale classifier


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _optional_mean(values: list[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return _mean(present) if present else None


def _window(values: list[float], fraction: float = 0.2) -> int:
    return max(1, min(len(values), round(len(values) * fraction) or 1))


def _slope(values: list[float]) -> float:
    """Least-squares slope per update; zero for fewer than two points."""

    count = len(values)
    if count < 2:
        return 0.0
    xs = list(range(count))
    mean_x = _mean([float(x) for x in xs])
    mean_y = _mean(values)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values, strict=True))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    return numerator / denominator if denominator else 0.0


def _trend(values: list[float]) -> dict[str, float]:
    window = _window(values)
    first = _mean(values[:window])
    last = _mean(values[-window:])
    return {
        "count": len(values),
        "window": window,
        "first_mean": first,
        "last_mean": last,
        "delta": last - first,
        "slope_per_update": _slope(values),
        "min": min(values) if values else 0.0,
        "max": max(values) if values else 0.0,
    }


def _summarize(artifact: dict[str, Any], *, policy: str, seed: int) -> dict[str, Any]:
    summary = cadence_compare.summarize_run(artifact)
    trace = artifact["training"]["trace"]
    segments = artifact.get("segments") or []
    act_rates = [float(segment.get("eligible_act_rate", 0.0)) for segment in segments]
    reachable = [bool((segment.get("rollout") or {}).get("finite", False)) for segment in segments]
    # Reward presence is recorded twice: the trace's per-update max |reward|
    # (2026-11-19 onward) and each segment rollout's ``reward_abs_sum`` (always).
    # ``reward_sum`` itself is identically zero by antisymmetry and is not a signal.
    trace_abs_max = (
        [float(row["reward_abs_max"]) for row in trace]
        if trace and all(row.get("reward_abs_max") is not None for row in trace)
        else None
    )
    segment_abs_sum = [
        float((segment.get("rollout") or {}).get("summary", {}).get("reward_abs_sum", 0.0))
        for segment in segments
        if "reward_abs_sum" in ((segment.get("rollout") or {}).get("summary") or {})
    ]
    if trace_abs_max is not None:
        updates_with_reward = sum(1 for value in trace_abs_max if value > 1e-12)
    elif len(segment_abs_sum) == len(trace):
        updates_with_reward = sum(1 for value in segment_abs_sum if value > 1e-12)
    else:
        updates_with_reward = None
    summary.update(
        {
            "policy": policy,
            "seed": int(seed),
            "updates_run": len(trace),
            "updates_with_reward": updates_with_reward,
            "reward_abs_max": max(trace_abs_max, default=None) if trace_abs_max is not None else None,
            "reward_abs_sum_median": (
                statistics.median(segment_abs_sum) if segment_abs_sum else None
            ),
            "return_trend": _trend([float(row["return_mean"]) for row in trace]),
            "reward_trend": _trend([float(row["reward_sum"]) for row in trace]),
            "value_explained_variance_trend": _trend(
                [float(row["value_explained_variance"]) for row in trace]
            ),
            "act_rate_trend": _trend(act_rates),
            "rollout_finite_all": all(reachable),
            "seconds_per_update": (
                float(summary["wall_s"]) / len(trace) if trace else 0.0
            ),
        }
    )
    return summary


def _policy_view(rows: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    selected = [row for row in rows if row["policy"] == policy]
    if not selected:
        raise ValueError(f"no runs for policy {policy!r}")
    keys = (
        "return_trend",
        "reward_trend",
        "value_explained_variance_trend",
        "act_rate_trend",
    )
    aggregate: dict[str, Any] = {
        "policy": policy,
        "runs": len(selected),
        "seeds": [int(row["seed"]) for row in selected],
        "updates_run_mean": _mean([float(row["updates_run"]) for row in selected]),
        "lane_decisions_mean": _mean([float(row["lane_decisions"]) for row in selected]),
        "eligible_act_rate_mean": _mean([float(row["eligible_act_rate"]) for row in selected]),
        "wall_s_mean": _mean([float(row["wall_s"]) for row in selected]),
        "seconds_per_update_mean": _mean([float(row["seconds_per_update"]) for row in selected]),
        "training_pass_all": all(bool(row["training_pass"]) for row in selected),
        "stale_updates_mean": _mean([float(row["stale_updates"]) for row in selected]),
        "updates_with_reward_mean": _optional_mean(
            [row.get("updates_with_reward") for row in selected]
        ),
        "reward_abs_max_mean": _optional_mean([row.get("reward_abs_max") for row in selected]),
        "reward_abs_sum_median_mean": _optional_mean(
            [row.get("reward_abs_sum_median") for row in selected]
        ),
    }
    for key in keys:
        for field in ("first_mean", "last_mean", "delta", "slope_per_update"):
            aggregate[f"{key}_{field}_mean"] = _mean([float(row[key][field]) for row in selected])
    return aggregate


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


def _paired_seeds(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Pair the policies at each seed: one seed is one observation of the gap."""

    by_seed: dict[int, dict[str, dict[str, Any]]] = {}
    for row in runs:
        by_seed.setdefault(int(row["seed"]), {})[str(row["policy"])] = row
    pairs: list[dict[str, Any]] = []
    for seed in sorted(by_seed):
        pair = by_seed[seed]
        if "reset" not in pair or "carry" not in pair:
            continue
        reset_last = float(pair["reset"]["return_trend"]["last_mean"])
        carry_last = float(pair["carry"]["return_trend"]["last_mean"])
        delta = carry_last - reset_last
        pairs.append(
            {
                "seed": seed,
                "reset_last_return": reset_last,
                "carry_last_return": carry_last,
                "carry_minus_reset": delta,
                "winner": "tie" if abs(delta) < 1e-9 else ("carry" if delta > 0 else "reset"),
            }
        )
    deltas = [float(pair["carry_minus_reset"]) for pair in pairs]
    return {
        "seeds_compared": len(pairs),
        "pairs": pairs,
        "carry_leads_seeds": sum(1 for delta in deltas if delta > 0),
        "reset_leads_seeds": sum(1 for delta in deltas if delta < 0),
        "mean_carry_minus_reset": _mean(deltas),
        "seeds_agree": (
            None
            if len(deltas) < 2
            else all(delta > 0 for delta in deltas) or all(delta < 0 for delta in deltas)
        ),
    }


def build_comparison(
    summary: dict[str, dict[str, Any]], runs: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    reset = summary["reset"]
    carry = summary["carry"]
    delta = carry["return_trend_last_mean_mean"] - reset["return_trend_last_mean_mean"]
    if abs(delta) < 1e-9:
        ranking = "tie"
    else:
        ranking = "carry" if delta > 0 else "reset"
    return {
        "reset_last_return_mean": reset["return_trend_last_mean_mean"],
        "carry_last_return_mean": carry["return_trend_last_mean_mean"],
        "carry_minus_reset_last_return": delta,
        "reset_return_slope": reset["return_trend_slope_per_update_mean"],
        "carry_return_slope": carry["return_trend_slope_per_update_mean"],
        "carry_minus_reset_slope": (
            carry["return_trend_slope_per_update_mean"] - reset["return_trend_slope_per_update_mean"]
        ),
        "reset_act_rate_last": reset["act_rate_trend_last_mean_mean"],
        "carry_act_rate_last": carry["act_rate_trend_last_mean_mean"],
        "higher_last_window_return": ranking,
        "wall_delta_s": carry["wall_s_mean"] - reset["wall_s_mean"],
        "all_training_pass": reset["training_pass_all"] and carry["training_pass_all"],
        "all_updates_on_policy": reset["stale_updates_mean"] == 0.0 and carry["stale_updates_mean"] == 0.0,
        "updates_with_reward_mean": {
            "reset": reset.get("updates_with_reward_mean"),
            "carry": carry.get("updates_with_reward_mean"),
        },
        "reward_abs_max_mean": {
            "reset": reset.get("reward_abs_max_mean"),
            "carry": carry.get("reward_abs_max_mean"),
        },
        "reward_abs_sum_median_mean": {
            "reset": reset.get("reward_abs_sum_median_mean"),
            "carry": carry.get("reward_abs_sum_median_mean"),
        },
        "paired_seeds": _paired_seeds(runs or []),
        "native_parity": {
            "matches": 0,
            "note": (
                "not run: crforge is never a promotion source; the native parity cadence "
                "(50-150 matches every N updates) is unchanged and is the promotion gate"
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=4, help="lanes per VectorBattle")
    parser.add_argument("--updates", type=int, default=50, help="segments = optimizer updates per run")
    parser.add_argument("--decisions-per-segment", type=int, default=24)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1])
    parser.add_argument("--policies", nargs="+", choices=POLICIES, default=list(POLICIES))
    parser.add_argument("--ppo-sequence-chunk", type=int, default=24)
    parser.add_argument("--ppo-epochs", type=int, default=1)
    parser.add_argument("--ppo-learning-rate", type=float, default=1e-5)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--omp-threads", type=int, default=4, help="OMP_NUM_THREADS for every run")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="keep the per-decision record validation (default off, as in the cadence runs)",
    )
    parser.add_argument("--checkpoint", type=Path, default=Path(".resource/RoyaleFish/models/B-general-v275.pt"))
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path("runs/crforge-c4/horizon"),
        help="per-run producer artifacts and logs",
    )
    parser.add_argument("--reuse", action="store_true", help="load existing per-run artifacts instead of re-running")
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c4/learning-horizon.json"))
    args = parser.parse_args(argv)

    if args.updates < 2:
        raise SystemExit("a learning horizon needs at least two updates")
    if args.omp_threads < 1:
        raise SystemExit("--omp-threads must be positive")

    os.environ["OMP_NUM_THREADS"] = str(args.omp_threads)
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
                    str(args.updates),
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
            summary = _summarize(artifact, policy=policy, seed=seed)
            if summary["state_policy"] != policy:
                raise SystemExit(
                    f"{artifact_path} records state policy {summary['state_policy']!r}, expected {policy!r}"
                )
            substrates.append(artifact.get("substrate") or {})
            runs.append(summary)
            trend = summary["return_trend"]
            print(
                f"    updates {summary['updates_run']}, return {trend['first_mean']:.4f} -> "
                f"{trend['last_mean']:.4f} (delta {trend['delta']:+.4f}), "
                f"ACT {summary['act_rate_trend']['last_mean']:.4f}, wall {summary['wall_s']:.1f}s",
                file=sys.stderr,
            )

    summary_by_policy = {policy: _policy_view(runs, policy) for policy in args.policies}
    substrate = substrates[0] if substrates else {}
    if any(json.dumps(item, sort_keys=True) != json.dumps(substrate, sort_keys=True) for item in substrates[1:]):
        raise SystemExit("the runs do not share one substrate; the comparison is not like-for-like")
    comparison = build_comparison(summary_by_policy, runs)
    gate = {
        "updates_at_least_50": all(
            row["updates_run_mean"] >= 50.0 for row in summary_by_policy.values()
        ),
        "all_training_pass": bool(comparison["all_training_pass"]),
        "all_updates_on_policy": bool(comparison["all_updates_on_policy"]),
        "pass": bool(
            comparison["all_training_pass"]
            and comparison["all_updates_on_policy"]
            and all(row["updates_run_mean"] >= 50.0 for row in summary_by_policy.values())
        ),
    }
    artifact = {
        "schema": SCHEMA,
        "measured": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "substrate": substrate,
        "shape": {
            "count": args.count,
            "updates": args.updates,
            "decisions_per_segment": args.decisions_per_segment,
            "actor_rows": args.count * 2,
            "lane_decisions_per_run": args.count * 2 * args.updates * args.decisions_per_segment,
            "seeds": list(args.seeds),
            "policies": list(args.policies),
            "device": args.device,
            "validate": bool(args.validate),
            "omp_num_threads": args.omp_threads,
            "checkpoint": str(args.checkpoint),
            "ppo_sequence_chunk": args.ppo_sequence_chunk,
            "ppo_epochs": args.ppo_epochs,
            "ppo_learning_rate": args.ppo_learning_rate,
        },
        "summary": summary_by_policy,
        "comparison": comparison,
        "gate": gate,
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {args.output}", file=sys.stderr)
    header = f"{'policy':<7} {'updates':>8} {'ret first':>10} {'ret last':>9} {'delta':>9} {'slope':>10} {'ACT':>7} {'wall s':>8}"
    print(header)
    for policy, row in summary_by_policy.items():
        print(
            f"{policy:<7} {row['updates_run_mean']:>8.0f} "
            f"{row['return_trend_first_mean_mean']:>10.4f} "
            f"{row['return_trend_last_mean_mean']:>9.4f} "
            f"{row['return_trend_delta_mean']:>+9.4f} "
            f"{row['return_trend_slope_per_update_mean']:>10.2e} "
            f"{row['act_rate_trend_last_mean_mean']:>7.4f} {row['wall_s_mean']:>8.1f}"
        )
    print(f"ranking: {comparison['higher_last_window_return']}")
    paired = comparison["paired_seeds"]
    agree = paired["seeds_agree"]
    print(
        f"paired seeds: {paired['seeds_compared']}, carry leads "
        f"{paired['carry_leads_seeds']}/{paired['seeds_compared']}, "
        f"mean delta {paired['mean_carry_minus_reset']:+.4f}, "
        f"agree {'n/a' if agree is None else agree}"
    )
    reward_max = comparison["reward_abs_max_mean"]
    reward_updates = comparison["updates_with_reward_mean"]
    if all(value is not None for value in reward_max.values()) and not any(reward_max.values()):
        print(
            "warning: no update saw a nonzero reward; the return trace carries no observed "
            "score in this shape",
            file=sys.stderr,
        )
    elif all(value is None for value in reward_updates.values()):
        print(
            "note: this artifact records neither per-update reward nor a segment reward sum; "
            "reward_sum is identically zero by antisymmetry and cannot show a reward",
            file=sys.stderr,
        )
    print(f"gate: {json.dumps(gate)}")
    return 0 if gate["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
