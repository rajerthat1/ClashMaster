"""The cadence gate must classify a wave's stale update and refuse to guess."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "crforge"
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import cadence_compare


def _trace_row(segment: int, *, kl: float, clip: float) -> dict[str, object]:
    return {
        "segment": segment,
        "approx_kl": kl,
        "clip_fraction": clip,
        "return_mean": 0.25,
        "value_explained_variance": 0.1,
        "gradient_norm": 1.0,
    }


def _artifact(*, policy: str, segments_per_wave: int, updates: list[tuple[int, str, str]]) -> dict[str, object]:
    """A minimal producer artifact with one rollout per segment and one update each."""

    segments: list[dict[str, object]] = []
    trace: list[dict[str, object]] = []
    for index, (segment, before, after) in enumerate(updates):
        if segment >= len(segments):
            segments.append(
                {
                    "segment": segment,
                    "rollout": {"policy_state_id": before},
                    "updates": [],
                }
            )
        segments[segment]["updates"].append(  # type: ignore[index]
            {"policy_state_id_before": before, "policy_state_id_after": after}
        )
        trace.append(_trace_row(segment, kl=1e-5 * (index + 1), clip=0.0))
    return {
        "training": {
            "state_policy": policy,
            "segments_per_wave": segments_per_wave,
            "updates": len(trace),
            "pass": True,
            "trace": trace,
        },
        "segments": segments,
        "totals": {
            "lane_decisions": 3072,
            "act_lane_decisions": 96,
            "eligible_lane_decisions": 3000,
            "collection_wall_s": 60.0,
            "update_wall_s": 120.0,
            "wall_s": 180.0,
            "collection_lane_decisions_per_s": 51.2,
        },
    }


def test_classify_updates_marks_only_the_second_update_of_a_wave() -> None:
    reset = cadence_compare.classify_updates(
        [
            {"policy_state_id_before": "A", "policy_state_id_after": "B"},
            {"policy_state_id_before": "B", "policy_state_id_after": "C"},
        ]
    )
    wave = cadence_compare.classify_updates(
        [
            {"policy_state_id_before": "A", "policy_state_id_after": "B"},
            {"policy_state_id_before": "A", "policy_state_id_after": "C"},
        ]
    )
    assert reset == [False, False]
    assert wave == [False, True]


def test_summarize_run_separates_on_policy_from_stale_updates() -> None:
    artifact = _artifact(
        policy="wave",
        segments_per_wave=2,
        updates=[(0, "A", "B"), (1, "A", "C")],
    )
    summary = cadence_compare.summarize_run(artifact)
    assert summary["state_policy"] == "wave"
    assert summary["updates"] == 2
    assert summary["on_policy_updates"] == 1
    assert summary["stale_updates"] == 1
    assert summary["on_policy_approx_kl_mean"] == pytest.approx(1e-5)
    assert summary["stale_approx_kl_mean"] == pytest.approx(2e-5)
    assert summary["distinct_sampling_ids"] == 1
    assert summary["updates_per_sampling_id"] == pytest.approx(2.0)
    assert summary["eligible_act_rate"] == pytest.approx(96 / 3000)


def test_summarize_run_refuses_a_trace_update_count_mismatch() -> None:
    artifact = _artifact(policy="reset", segments_per_wave=1, updates=[(0, "A", "B")])
    artifact["training"]["trace"].append(_trace_row(0, kl=1e-5, clip=0.0))  # type: ignore[index]
    with pytest.raises(ValueError, match="cadence classification would be wrong"):
        cadence_compare.summarize_run(artifact)


def test_build_comparison_reports_wave_cost_and_carry_delta() -> None:
    runs = []
    for policy, segments_per_wave, updates in (
        ("reset", 1, [(0, "A", "B"), (1, "B", "C")]),
        ("carry", 1, [(0, "A", "B"), (1, "B", "C")]),
        ("wave", 2, [(0, "A", "B"), (1, "A", "C")]),
    ):
        summary = cadence_compare.summarize_run(
            _artifact(policy=policy, segments_per_wave=segments_per_wave, updates=updates)
        )
        summary["seed"] = 1
        runs.append(summary)
    summary = {policy: cadence_compare._aggregate(runs, policy) for policy in ("reset", "carry", "wave")}
    comparison = cadence_compare.build_comparison(summary)
    assert comparison["wave_stale_updates"] == pytest.approx(1.0)
    assert comparison["wave_stale_over_on_policy_abs_kl"] == pytest.approx(2.0)
    assert comparison["wave_updates_per_sampling_id"] == pytest.approx(2.0)
    assert comparison["carry_minus_reset_updates"] == pytest.approx(0.0)
    assert comparison["carry_minus_reset_abs_kl"] == pytest.approx(0.0)
    assert comparison["all_training_pass"] is True
