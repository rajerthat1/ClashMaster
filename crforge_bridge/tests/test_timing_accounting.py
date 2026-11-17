"""Timing accounting: a median bucket cannot be compared with a mean wall."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "crforge"
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import timing_accounting


def _artifact(name: str, lanes: int, segments: list[dict]) -> tuple[str, dict]:
    return (
        name,
        {
            "shape": {"count": lanes},
            "segments": segments,
        },
    )


def _segment(
    decisions: int,
    wall_s: float,
    medians: dict[str, float],
    *,
    means: dict[str, float] | None = None,
    first: dict[str, float] | None = None,
) -> dict:
    return {
        "decisions": decisions,
        "collection_wall_s": wall_s,
        "ms": medians,
        "ms_mean": means or {},
        "ms_first": first or {},
        "ms_max": {},
    }


def test_a_cold_first_decision_shows_up_as_a_fixed_cost_not_in_the_medians() -> None:
    name, artifact = _artifact(
        "cold.json",
        128,
        [_segment(8, 6.0232, {"inference_ms": 88.5, "tensorize_ms": 26.0, "decode_ms": 14.0, "step_ms": 8.3})],
    )

    row = timing_accounting.artifact_row(name, artifact, 0)

    assert row["wall_ms_per_decision"] == pytest.approx(752.9, abs=0.1)
    assert row["bucket_median_sum_ms"] == pytest.approx(136.8)
    assert row["residual_from_medians_ms"] == pytest.approx(616.1, abs=0.1)
    assert row["implied_fixed_cost_s"] == pytest.approx(4.93, abs=0.01)
    assert row["bucket_mean_sum_ms"] is None


def test_recorded_means_reconcile_the_wall_instead_of_assuming_a_cold_decision() -> None:
    name, artifact = _artifact(
        "warm.json",
        128,
        [
            _segment(
                8,
                6.0232,
                {"inference_ms": 88.5, "tensorize_ms": 26.0},
                means={"inference_ms": 650.0, "tensorize_ms": 90.0},
                first={"inference_ms": 5000.0},
            )
        ],
    )

    row = timing_accounting.artifact_row(name, artifact, 0)

    assert row["bucket_mean_sum_ms"] == pytest.approx(740.0)
    assert row["residual_from_means_ms"] == pytest.approx(752.9 - 740.0, abs=0.1)
    assert row["first_forward_ms"] == pytest.approx(5000.0)
    assert row["median_forward_ms"] == pytest.approx(88.5)


def test_two_shapes_at_one_lane_count_solve_the_fixed_cost_and_steady_state() -> None:
    short = {"lanes": 128, "decisions": 8, "wall_ms_per_decision": 1018.8, "artifact": "s"}
    long = {"lanes": 128, "decisions": 32, "wall_ms_per_decision": 646.8, "artifact": "l"}

    solved = timing_accounting.solve_fixed_cost(short, long)

    assert solved is not None
    assert solved["fixed_ms"] == pytest.approx(3968.0, abs=1.0)
    assert solved["steady_ms_per_decision"] == pytest.approx(522.8, abs=1.0)
    # 128 lanes x 2 seats, no per-decision discount.
    assert solved["steady_lane_decisions_per_s"] == pytest.approx(1000.0 / 522.8 * 256, abs=1.0)


def test_a_warm_second_segment_measures_the_cold_cost_without_amortising_it() -> None:
    cold = {
        "artifact": "run.json",
        "segment": 0,
        "lanes": 64,
        "decisions": 8,
        "wall_ms_per_decision": 700.0,
    }
    warm = {
        "artifact": "run.json",
        "segment": 1,
        "lanes": 64,
        "decisions": 8,
        "wall_ms_per_decision": 200.0,
    }

    solved = timing_accounting.solve_cold_from_segments(cold, warm)

    assert solved is not None
    assert solved["fixed_ms"] == pytest.approx(4000.0)
    assert solved["warm_ms_per_decision"] == pytest.approx(200.0)
    assert timing_accounting.solve_cold_from_segments(warm, cold) is None
