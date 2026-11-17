"""The reward-share probe must separate a shape's share from a training pass."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "crforge"
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import reward_share


def _row(seed: int, decisions: int, share: float, *, updates: int = 16, reached: int | None = None) -> dict:
    return {
        "policy": "reset",
        "seed": seed,
        "decisions_per_segment": decisions,
        "updates_run": updates,
        "updates_reward_share_at_least_threshold": (
            sum(1 for _ in range(updates)) if reached is None else reached
        ),
        "reward_share_median": share,
        "reward_share_mean": share * 2.0,
        "reward_share_max": share * 10.0,
        "reward_abs_max_median": 1e-3,
        "updates_with_reward": updates,
        "return_trend": {"last_mean": 0.4},
        "wall_s": 600.0,
        "training_pass": True,
        "stale_updates": 0,
    }


def test_aggregate_reports_the_median_share_and_the_fraction_of_updates() -> None:
    runs = [_row(1, 60, 0.01, reached=4), _row(2, 60, 0.03, reached=8)]

    row = reward_share._aggregate(runs, 60)

    assert row["runs"] == 2
    assert row["reward_share_median_mean"] == pytest.approx(0.02)
    assert row["fraction_updates_at_least_threshold_mean"] == pytest.approx((0.25 + 0.5) / 2)
    assert row["training_pass_all"] is True


def test_a_short_shape_is_a_diagnostic_not_a_score() -> None:
    runs = [_row(seed, 24, 0.004) for seed in (1, 2)] + [_row(seed, 60, 0.02) for seed in (1, 2)]
    summary = {str(decisions): reward_share._aggregate(runs, decisions) for decisions in (24, 60)}

    comparison = reward_share.build_comparison(summary, baseline=24)

    assert comparison["share_median_by_decisions"] == {"24": pytest.approx(0.004), "60": pytest.approx(0.02)}
    assert comparison["share_growth_vs_baseline"]["60"] == pytest.approx(5.0)
    assert comparison["rankable_at_decisions"] is None
    assert comparison["return_is_a_score"] is False


def _artifact() -> dict:
    trace = {
        "update_index": 1,
        "segment": 0,
        "optimizer_step": 0,
        "optimizer_steps": 1,
        "time_steps": 24,
        "valid_count": 48.0,
        "reward_sum": 0.0,
        "reward_abs_max": 0.01,
        "return_mean": 0.4,
        "return_std": 0.1,
        "value_explained_variance": 0.5,
        "approx_kl": 1e-5,
        "clip_fraction": 0.0,
        "gradient_norm": 1.0,
        "stale": False,
        "finite": True,
        "policy_state_id_before": "a",
        "policy_state_id_after": "b",
    }
    return {
        "substrate": {"release": "16.635.1"},
        "shape": {"count": 1},
        "totals": {
            "lane_decisions": 48,
            "wall_s": 10.0,
            "collection_wall_s": 5.0,
            "act_lane_decisions": 1,
            "eligible_lane_decisions": 10,
            "collection_lane_decisions_per_s": 9.6,
        },
        "training": {
            "state_policy": "reset",
            "segments_per_wave": 1,
            "updates": 1,
            "pass": True,
            "trace": [trace],
        },
        "segments": [
            {
                "eligible_act_rate": 0.1,
                "updates": [{"stale": False, "policy_state_id_before": "a", "policy_state_id_after": "b"}],
                "rollout": {
                    "finite": True,
                    "policy_state_id": "a",
                    "summary": {
                        "rows": 2,
                        "decisions": 24,
                        "reward_abs_sum": 0.08,
                        "reward_min": -0.01,
                        "reward_max": 0.01,
                    },
                },
            }
        ],
    }


def test_aggregate_only_reports_a_missing_run_instead_of_collecting_it(tmp_path: Path) -> None:
    (tmp_path / "reset-seed1-d24.json").write_text(json.dumps(_artifact()), encoding="utf-8")
    output = tmp_path / "summary.json"

    code = reward_share.main(
        [
            "--decisions",
            "24",
            "--seeds",
            "1",
            "2",
            "--aggregate-only",
            "--run-dir",
            str(tmp_path),
            "--output",
            str(output),
        ]
    )

    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert artifact["gate"]["complete"] is False
    assert artifact["missing_runs"] == [{"policy": "reset", "seed": 2, "decisions_per_segment": 24}]
    assert artifact["summary"]["24"]["runs"] == 1


def test_a_shape_qualifies_only_when_its_typical_update_reaches_the_threshold() -> None:
    # One seed's median share clears the bar but only 1/16 of its updates do; a
    # single terminal update must not turn the curve into a score.
    runs = [
        _row(1, 120, 0.6, reached=1),
        _row(2, 120, 0.6, reached=1),
    ]
    summary = {"120": reward_share._aggregate(runs, 120)}
    assert reward_share.build_comparison(summary, baseline=24)["rankable_at_decisions"] is None

    runs = [
        _row(1, 120, 0.6, reached=12),
        _row(2, 120, 0.8, reached=14),
    ]
    summary = {"120": reward_share._aggregate(runs, 120)}
    comparison = reward_share.build_comparison(summary, baseline=24)
    assert comparison["rankable_at_decisions"] == 120
    assert comparison["return_is_a_score"] is True
