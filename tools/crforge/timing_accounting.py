#!/usr/bin/env python3
"""C4 timing accounting: reconcile the producer's buckets with its wall.

§3.62 reported "the remaining ~228 ms of the 647 ms decision is not in any
bucket".  It is not: the buckets are **medians** and the wall is a **mean**, so a
single cold decision (the first forward pays lazy kernel/cuDNN setup and the first
allocator growth) inflates the mean and leaves every median where it was.  This
tool reads producer artifacts and reports, per segment:

* the wall per decision (a mean);
* the sum of the bucket medians and of the bucket means;
* the residual under each reading, and the fixed cost it implies;
* the first and median forward, when the artifact records ``ms_first``.

Two artifacts at the same lane count but different segment lengths give a
two-point solve for the fixed cost ``F`` and the steady-state per-decision cost
``S`` (``ms = S + F/decisions``), which is what a throughput projection should
use::

    .venv/bin/python tools/crforge/timing_accounting.py \\
        --output runs/crforge-c4/timing-accounting.json

The artifact is ``crforge-c4-timing-accounting.v1``.  Run it on artifacts
produced before 2026-11-19 carefully: they have medians only, so the residual is
attributed to the cold decision by assumption, not by measurement.  Artifacts
from the current producer carry ``ms_mean``, ``ms_max`` and ``ms_first``, so the
reconciliation is exact.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "crforge-c4-timing-accounting.v1"


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def artifact_row(name: str, artifact: dict[str, Any], index: int) -> dict[str, Any]:
    """One segment's wall against its buckets, in ms per decision."""

    lanes = int((artifact.get("shape") or {}).get("count") or 0)
    segments = artifact.get("segments") or []
    if index >= len(segments):
        raise ValueError(f"{name} has no segment {index}")
    segment = segments[index]
    decisions = int(segment["decisions"])
    wall_ms = 1000.0 * float(segment["collection_wall_s"]) / decisions if decisions else 0.0
    medians = dict(segment.get("ms") or {})
    means = dict(segment.get("ms_mean") or {})
    first = dict(segment.get("ms_first") or {})
    maxima = dict(segment.get("ms_max") or {})
    median_sum = sum(float(value) for value in medians.values())
    mean_sum = sum(float(value) for value in means.values()) if means else None
    residual_median = wall_ms - median_sum
    return {
        "artifact": name,
        "segment": index,
        "lanes": lanes,
        "decisions": decisions,
        "wall_ms_per_decision": wall_ms,
        "bucket_median_sum_ms": median_sum,
        "bucket_mean_sum_ms": mean_sum,
        "residual_from_medians_ms": residual_median,
        "implied_fixed_cost_s": residual_median * decisions / 1000.0,
        "residual_from_means_ms": None if mean_sum is None else wall_ms - mean_sum,
        "first_forward_ms": None if "inference_ms" not in first else float(first["inference_ms"]),
        "median_forward_ms": None if "inference_ms" not in medians else float(medians["inference_ms"]),
        "max_forward_ms": None if "inference_ms" not in maxima else float(maxima["inference_ms"]),
        "buckets": sorted(medians),
    }


def solve_fixed_cost(short: dict[str, Any], long: dict[str, Any]) -> dict[str, Any] | None:
    """``ms = S + F/decisions`` from two artifacts at one lane count.

    ``F`` is the one-time cost in ms (the cold first decision); ``S`` is the
    steady-state cost per decision.  Returns ``None`` unless the lane counts
    match, the segment lengths differ, and both walls are present.
    """

    if short["lanes"] != long["lanes"] or short["decisions"] == long["decisions"]:
        return None
    span = 1.0 / short["decisions"] - 1.0 / long["decisions"]
    if abs(span) < 1e-12:
        return None
    fixed_ms = (short["wall_ms_per_decision"] - long["wall_ms_per_decision"]) / span
    steady_ms = short["wall_ms_per_decision"] - fixed_ms / short["decisions"]
    if steady_ms <= 0.0:
        return None
    return {
        "lanes": short["lanes"],
        "short_artifact": short["artifact"],
        "long_artifact": long["artifact"],
        "short_decisions": short["decisions"],
        "long_decisions": long["decisions"],
        "fixed_ms": fixed_ms,
        "steady_ms_per_decision": steady_ms,
        "steady_lane_decisions_per_s": 1000.0 / steady_ms * short["lanes"] * 2,
        "recorded_lane_decisions_per_s_at_long_shape": (
            1000.0 / long["wall_ms_per_decision"] * long["lanes"] * 2
        ),
    }


def solve_cold_from_segments(first: dict[str, Any], warm: dict[str, Any]) -> dict[str, Any] | None:
    """The cold cost from segment 0 against a later, already-warm segment.

    Same lane count, same segment length: the only difference is that segment 0's
    first decision is the process's first forward.  ``F = (wall0 - wall1) * T``.
    """

    if first["artifact"] != warm["artifact"] or first["decisions"] != warm["decisions"]:
        return None
    if first["segment"] >= warm["segment"]:
        return None
    fixed_ms = (first["wall_ms_per_decision"] - warm["wall_ms_per_decision"]) * first["decisions"]
    if fixed_ms <= 0.0:
        return None
    return {
        "lanes": first["lanes"],
        "artifact": first["artifact"],
        "cold_segment": first["segment"],
        "warm_segment": warm["segment"],
        "decisions": first["decisions"],
        "fixed_ms": fixed_ms,
        "warm_ms_per_decision": warm["wall_ms_per_decision"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "artifacts",
        type=Path,
        nargs="+",
        help="producer artifacts to reconcile",
    )
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c4/timing-accounting.json"))
    args = parser.parse_args(argv)

    rows: list[dict[str, Any]] = []
    by_artifact: dict[str, list[dict[str, Any]]] = {}
    for path in args.artifacts:
        artifact = json.loads(path.read_text(encoding="utf-8"))
        accessible = sorted(
            (index for index, segment in enumerate(artifact.get("segments") or []) if segment.get("decisions")),
            key=int,
        )
        if not accessible:
            raise SystemExit(f"{path} has no segment with a decision count")
        selected = [accessible[0]]
        if len(accessible) > 1:
            selected.append(accessible[1])
        for index in selected:
            row = artifact_row(path.name, artifact, index)
            rows.append(row)
            by_artifact.setdefault(path.name, []).append(row)

    solves: list[dict[str, Any]] = []
    for group in by_artifact.values():
        if len(group) >= 2:
            solved = solve_cold_from_segments(group[0], group[1])
            if solved is not None:
                solves.append(solved)
    by_lanes: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        if row["segment"] == 0:
            by_lanes.setdefault(int(row["lanes"]), []).append(row)
    for group in by_lanes.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda row: int(row["decisions"]))
        solved = solve_fixed_cost(ordered[0], ordered[-1])
        if solved is not None:
            solves.append(solved)

    artifact = {
        "schema": SCHEMA,
        "measured": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inputs": [str(path) for path in args.artifacts],
        "rows": rows,
        "solve": solves,
        "note": (
            "residual_from_medians is exact only when the artifact also records ms_mean; "
            "otherwise it attributes the mean-median gap to the cold first decision"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    print(
        f"{'artifact':<30} {'seg':>3} {'lanes':>5} {'dec':>4} {'wall':>9} {'med sum':>9} "
        f"{'mean sum':>9} {'resid':>9} {'first fwd':>10} {'med fwd':>9}"
    )
    for row in rows:
        mean_sum = row["bucket_mean_sum_ms"]
        first_forward = row["first_forward_ms"]
        median_forward = row["median_forward_ms"]
        print(
            f"{row['artifact']:<30} {row['segment']:>3} {row['lanes']:>5} {row['decisions']:>4} "
            f"{row['wall_ms_per_decision']:>9.1f} {row['bucket_median_sum_ms']:>9.1f} "
            f"{'n/a' if mean_sum is None else f'{mean_sum:.1f}':>9} "
            f"{row['residual_from_medians_ms']:>9.1f} "
            f"{'n/a' if first_forward is None else f'{first_forward:.1f}':>10} "
            f"{'n/a' if median_forward is None else f'{median_forward:.1f}':>9}"
        )
    for solved in solves:
        if "steady_ms_per_decision" in solved:
            print(
                f"fixed {solved['fixed_ms'] / 1000.0:.2f} s + {solved['steady_ms_per_decision']:.0f} ms/decision "
                f"at {solved['lanes']} lanes = {solved['steady_lane_decisions_per_s']:.0f} lane-decisions/s "
                f"(recorded {solved['recorded_lane_decisions_per_s_at_long_shape']:.0f})"
            )
        else:
            print(
                f"cold {solved['fixed_ms'] / 1000.0:.2f} s into {solved['decisions']} decisions at "
                f"{solved['lanes']} lanes; warm segment {solved['warm_ms_per_decision']:.1f} ms/decision"
            )
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
