#!/usr/bin/env python3
"""C4 pipeline gate: synchronous ``reset`` vs the overlapped loop.

``--overlap-update`` runs each segment's PPO update on a second model in a worker
thread while the next segment collects.  It hides the update behind collection,
but it samples segment ``k`` with the weights left by update ``k-1``, so every
update after the first is stale by one update.  This tool runs the serial loop and
the overlapped loop at one shape, checkpoint, seed list and thread count, and
reports the trade side by side:

* **wall** — the pipeline's loop wall against the serial collection + update;
* **collection rate** — lane-decisions/s while the update runs underneath;
* **the tax** — how many updates are stale and the |KL| / clip they pay;
* **the identity gate** — every segment's collecting pass must hash to the id it
  was sampled with, and every boundary weight copy must reproduce the update
  model byte-for-byte.

The artifact is ``crforge-c4-overlap-compare.v1``; per-run producer artifacts
stay under ``--run-dir`` and ``--reuse`` re-aggregates them without re-running::

    OMP_NUM_THREADS=4 .venv/bin/python tools/crforge/overlap_compare.py \\
        --count 4 --segments 6 --decisions-per-segment 24 --seeds 1 2 \\
        --output runs/crforge-c4/overlap-compare.json
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
SCHEMA = "crforge-c4-overlap-compare.v1"
MODES = ("serial", "overlap")

_TOOL_DIR = Path(__file__).resolve().parent
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import cadence_compare  # the shared per-run summary and stale classifier


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


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


def _summarize(artifact: dict[str, Any], *, mode: str, seed: int) -> dict[str, Any]:
    summary = cadence_compare.summarize_run(artifact)
    summary["mode"] = mode
    summary["seed"] = int(seed)
    summary["serial_equivalent_wall_s"] = float(summary["collection_wall_s"]) + float(summary["update_wall_s"])
    summary["speedup_vs_serial_equivalent"] = (
        summary["serial_equivalent_wall_s"] / summary["wall_s"] if summary["wall_s"] else 0.0
    )
    return summary


def _aggregate(runs: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    rows = [run for run in runs if run["mode"] == mode]
    if not rows:
        raise ValueError(f"no runs for mode {mode!r}")
    return {
        "mode": mode,
        "runs": len(rows),
        "seeds": [int(run["seed"]) for run in rows],
        "segments": rows[0]["segments"],
        "updates_mean": _mean([float(run["updates"]) for run in rows]),
        "stale_updates_mean": _mean([float(run["stale_updates"]) for run in rows]),
        "on_policy_updates_mean": _mean([float(run["on_policy_updates"]) for run in rows]),
        "on_policy_abs_approx_kl_mean": _mean([float(run["on_policy_abs_approx_kl_mean"]) for run in rows]),
        "stale_abs_approx_kl_mean": _mean([float(run["stale_abs_approx_kl_mean"]) for run in rows]),
        "stale_clip_fraction_mean": _mean([float(run["stale_clip_fraction_mean"]) for run in rows]),
        "lane_decisions_mean": _mean([float(run["lane_decisions"]) for run in rows]),
        "eligible_act_rate_mean": _mean([float(run["eligible_act_rate"]) for run in rows]),
        "wall_s_mean": _mean([float(run["wall_s"]) for run in rows]),
        "collection_wall_s_mean": _mean([float(run["collection_wall_s"]) for run in rows]),
        "update_wall_s_mean": _mean([float(run["update_wall_s"]) for run in rows]),
        "boundary_wall_s_mean": _mean([float(run["boundary_wall_s"]) for run in rows]),
        "serial_equivalent_wall_s_mean": _mean([float(run["serial_equivalent_wall_s"]) for run in rows]),
        "speedup_vs_serial_equivalent_mean": _mean(
            [float(run["speedup_vs_serial_equivalent"]) for run in rows]
        ),
        "collection_lane_decisions_per_s_mean": _mean(
            [float(run["collection_lane_decisions_per_s"]) for run in rows]
        ),
        "pipeline_lane_decisions_per_s_mean": _mean(
            [float(run["lane_decisions"]) / float(run["wall_s"]) if float(run["wall_s"]) else 0.0 for run in rows]
        ),
        "collection_identity_unchanged_all": all(
            bool(run["collection_identity_unchanged"]) for run in rows if run.get("pipeline_enabled")
        )
        if any(run.get("pipeline_enabled") for run in rows)
        else None,
        "training_pass_all": all(bool(run["training_pass"]) for run in rows),
    }


def build_comparison(summary: dict[str, dict[str, Any]]) -> dict[str, Any]:
    serial = summary["serial"]
    overlap = summary["overlap"]
    ratio = (
        overlap["collection_lane_decisions_per_s_mean"] / serial["collection_lane_decisions_per_s_mean"]
        if serial["collection_lane_decisions_per_s_mean"]
        else None
    )
    wall_saved = serial["wall_s_mean"] - overlap["wall_s_mean"]
    return {
        "serial_wall_s_mean": serial["wall_s_mean"],
        "overlap_wall_s_mean": overlap["wall_s_mean"],
        "wall_saved_s_mean": wall_saved,
        "speedup_mean": (serial["wall_s_mean"] / overlap["wall_s_mean"]) if overlap["wall_s_mean"] else None,
        "serial_collection_lane_decisions_per_s_mean": serial["collection_lane_decisions_per_s_mean"],
        "overlap_collection_lane_decisions_per_s_mean": overlap["collection_lane_decisions_per_s_mean"],
        "collection_rate_ratio": ratio,
        "serial_stale_updates_mean": serial["stale_updates_mean"],
        "overlap_stale_updates_mean": overlap["stale_updates_mean"],
        "overlap_on_policy_abs_approx_kl_mean": overlap["on_policy_abs_approx_kl_mean"],
        "overlap_stale_abs_approx_kl_mean": overlap["stale_abs_approx_kl_mean"],
        "overlap_stale_clip_fraction_mean": overlap["stale_clip_fraction_mean"],
        "overlap_boundary_wall_s_mean": overlap["boundary_wall_s_mean"],
        "overlap_collection_identity_unchanged_all": overlap["collection_identity_unchanged_all"],
        "all_training_pass": serial["training_pass_all"] and overlap["training_pass_all"],
    }


def build_gate(comparison: dict[str, Any], *, min_collection_rate_ratio: float) -> dict[str, Any]:
    """The pipeline gate: correctness, a wall win, and a surviving collector.

    A wall-only gate would call a 3 % win a pass while the collector lost 35 % of
    its throughput to the worker; the collection rate is the criterion that says
    whether the update was actually hidden.
    """

    ratio = comparison["collection_rate_ratio"]
    preserved = ratio is not None and ratio >= min_collection_rate_ratio
    faster = comparison["overlap_wall_s_mean"] < comparison["serial_wall_s_mean"]
    identity = bool(comparison["overlap_collection_identity_unchanged_all"])
    training = bool(comparison["all_training_pass"])
    return {
        "collection_identity_unchanged": identity,
        "all_training_pass": training,
        "pipeline_is_faster": bool(faster),
        "collection_rate_ratio": ratio,
        "min_collection_rate_ratio": min_collection_rate_ratio,
        "collection_rate_preserved": bool(preserved),
        "pass": bool(identity and training and faster and preserved),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=4, help="lanes per VectorBattle")
    parser.add_argument("--segments", type=int, default=6, help="segments collected per run")
    parser.add_argument("--decisions-per-segment", type=int, default=24)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2])
    parser.add_argument("--ppo-sequence-chunk", type=int, default=24)
    parser.add_argument(
        "--ppo-lane-minibatch",
        type=int,
        default=0,
        help="PPO lane rows per minibatch (0: one minibatch; needed to fit 8k frames on a 48 GB card)",
    )
    parser.add_argument("--ppo-epochs", type=int, default=1)
    parser.add_argument("--ppo-learning-rate", type=float, default=1e-5)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--fast-forward",
        action="store_true",
        help="run the producer's in-process forward port (fp16 + CUDA graphs on a GPU)",
    )
    parser.add_argument(
        "--no-cuda-graphs",
        action="store_true",
        help="with --fast-forward: eager fp16 forward, the measured default on the A6000",
    )
    parser.add_argument(
        "--full-cuda-graph",
        action="store_true",
        help="with --fast-forward: capture the sampled decode in the same graph (RNG-gated)",
    )
    parser.add_argument("--omp-threads", type=int, default=4, help="OMP_NUM_THREADS for every run")
    parser.add_argument(
        "--jvm-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="extra option for every run's engine JVM (repeatable)",
    )
    parser.add_argument(
        "--min-collection-rate-ratio",
        type=float,
        default=0.9,
        help=(
            "gate: the overlapped collection rate must stay at least this fraction of the "
            "serial collection rate, or the update was paid for in contention rather than hidden"
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="keep the per-decision record validation (default off, as in the cadence and GPU runs)",
    )
    parser.add_argument("--checkpoint", type=Path, default=Path(".resource/RoyaleFish/models/B-general-v275.pt"))
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path("runs/crforge-c4/overlap"),
        help="per-run producer artifacts and logs",
    )
    parser.add_argument("--reuse", action="store_true", help="load existing per-run artifacts instead of re-running")
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c4/overlap-compare.json"))
    args = parser.parse_args(argv)

    if args.segments < 2:
        raise SystemExit("a pipeline comparison needs at least two segments")
    if args.omp_threads < 1:
        raise SystemExit("--omp-threads must be positive")
    if args.no_cuda_graphs and not args.fast_forward:
        raise SystemExit("--no-cuda-graphs is a --fast-forward setting")
    if args.full_cuda_graph and not args.fast_forward:
        raise SystemExit("--full-cuda-graph is a --fast-forward setting")
    if args.full_cuda_graph and args.no_cuda_graphs:
        raise SystemExit("--full-cuda-graph is the captured path; drop --no-cuda-graphs")

    os.environ["OMP_NUM_THREADS"] = str(args.omp_threads)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    substrates: list[dict[str, Any]] = []
    for mode in MODES:
        for seed in args.seeds:
            artifact_path = args.run_dir / f"{mode}-seed{seed}.json"
            log_path = args.run_dir / f"{mode}-seed{seed}.log"
            print(f"{mode} seed {seed}: {artifact_path.name}", file=sys.stderr)
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
                    "reset",
                    "--ppo-sequence-chunk",
                    str(args.ppo_sequence_chunk),
                    "--ppo-lane-minibatch",
                    str(args.ppo_lane_minibatch),
                    "--ppo-epochs",
                    str(args.ppo_epochs),
                    "--ppo-learning-rate",
                    str(args.ppo_learning_rate),
                    "--output",
                    str(artifact_path),
                ]
                if mode == "overlap":
                    command.append("--overlap-update")
                if not args.validate:
                    command.append("--no-validate")
                for value in args.jvm_arg:
                    command.append(f"--jvm-arg={value}")
                if args.fast_forward:
                    command.append("--fast-forward")
                    if args.full_cuda_graph:
                        command.append("--full-cuda-graph")
                    if args.no_cuda_graphs:
                        command.append("--no-cuda-graphs")
                _run(command, log_path)
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            summary = _summarize(artifact, mode=mode, seed=seed)
            substrates.append(artifact.get("substrate") or {})
            runs.append(summary)
            print(
                f"    updates {summary['updates']} (stale {summary['stale_updates']}), "
                f"wall {summary['wall_s']:.1f}s "
                f"(collection {summary['collection_wall_s']:.1f}s + update {summary['update_wall_s']:.1f}s), "
                f"collection {summary['collection_lane_decisions_per_s']:.1f} ld/s, "
                f"identity {summary['collection_identity_unchanged']}",
                file=sys.stderr,
            )

    summary_by_mode = {mode: _aggregate(runs, mode) for mode in MODES}
    substrate = substrates[0] if substrates else {}
    if any(json.dumps(item, sort_keys=True) != json.dumps(substrate, sort_keys=True) for item in substrates[1:]):
        raise SystemExit("the runs do not share one substrate; the comparison is not like-for-like")
    comparison = build_comparison(summary_by_mode)
    gate = build_gate(comparison, min_collection_rate_ratio=args.min_collection_rate_ratio)
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
            "omp_num_threads": args.omp_threads,
            "checkpoint": str(args.checkpoint),
            "ppo_sequence_chunk": args.ppo_sequence_chunk,
            "ppo_lane_minibatch": args.ppo_lane_minibatch,
            "ppo_epochs": args.ppo_epochs,
            "ppo_learning_rate": args.ppo_learning_rate,
            "fast_forward": bool(args.fast_forward),
            "cuda_graphs": bool(args.fast_forward and not args.no_cuda_graphs),
            "full_cuda_graph": bool(args.full_cuda_graph),
            "update_state_policy": "reset",
            "min_collection_rate_ratio": args.min_collection_rate_ratio,
        },
        "modes": list(MODES),
        "summary": summary_by_mode,
        "comparison": comparison,
        "gate": gate,
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {args.output}", file=sys.stderr)
    header = f"{'mode':<8} {'wall s':>8} {'coll s':>8} {'upd s':>8} {'ld/s':>8} {'stale':>6} {'speedup':>8}"
    print(header)
    for mode, row in summary_by_mode.items():
        print(
            f"{mode:<8} {row['wall_s_mean']:>8.1f} {row['collection_wall_s_mean']:>8.1f} "
            f"{row['update_wall_s_mean']:>8.1f} {row['collection_lane_decisions_per_s_mean']:>8.1f} "
            f"{row['stale_updates_mean']:>6.1f} {row['speedup_vs_serial_equivalent_mean']:>8.2f}"
        )
    print(f"gate: {json.dumps(gate)}")
    return 0 if gate["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
