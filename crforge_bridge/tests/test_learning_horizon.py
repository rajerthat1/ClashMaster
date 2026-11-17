"""The learning-horizon summary must describe a curve, not a single update."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "crforge"
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import learning_horizon


def test_trend_reports_the_windows_delta_and_slope() -> None:
    values = [0.2 + 0.01 * index for index in range(50)]

    trend = learning_horizon._trend(values)

    assert trend["count"] == 50
    assert trend["window"] == 10  # 20 % of the updates
    assert trend["first_mean"] == pytest.approx(0.245)
    assert trend["last_mean"] == pytest.approx(0.645)
    assert trend["delta"] == pytest.approx(0.4)
    assert trend["slope_per_update"] == pytest.approx(0.01)
    assert trend["min"] == pytest.approx(0.2)
    assert trend["max"] == pytest.approx(0.69)


def _policy(policy: str, last: float, slope: float) -> dict[str, object]:
    return {
        "policy": policy,
        "return_trend_last_mean_mean": last,
        "return_trend_slope_per_update_mean": slope,
        "act_rate_trend_last_mean_mean": 0.1,
        "wall_s_mean": 900.0,
        "training_pass_all": True,
        "stale_updates_mean": 0.0,
    }


def test_the_higher_last_window_return_is_named_and_the_gap_reported() -> None:
    comparison = learning_horizon.build_comparison(
        {"reset": _policy("reset", 0.495, 5.93e-3), "carry": _policy("carry", 0.580, 8.14e-3)}
    )

    assert comparison["higher_last_window_return"] == "carry"
    assert comparison["carry_minus_reset_last_return"] == pytest.approx(0.085)
    assert comparison["carry_minus_reset_slope"] == pytest.approx(2.21e-3)
    assert comparison["all_updates_on_policy"] is True
    assert comparison["native_parity"]["matches"] == 0


def test_equal_returns_are_a_tie_not_a_win() -> None:
    comparison = learning_horizon.build_comparison(
        {"reset": _policy("reset", 0.5, 1e-3), "carry": _policy("carry", 0.5, 1e-3)}
    )
    assert comparison["higher_last_window_return"] == "tie"


def _run(policy: str, seed: int, last: float) -> dict[str, object]:
    return {"policy": policy, "seed": seed, "return_trend": {"last_mean": last}}


def test_a_horizon_without_a_recorded_reward_signal_is_null_not_zero() -> None:
    summary = {
        "reset": _policy("reset", 0.495, 5.9e-3),
        "carry": _policy("carry", 0.580, 8.1e-3),
    }

    comparison = learning_horizon.build_comparison(summary)

    assert comparison["updates_with_reward_mean"] == {"reset": None, "carry": None}
    assert comparison["reward_abs_max_mean"] == {"reset": None, "carry": None}
    summary["carry"]["updates_with_reward_mean"] = 12.0
    summary["carry"]["reward_abs_max_mean"] = 0.0
    comparison = learning_horizon.build_comparison(summary)
    assert comparison["updates_with_reward_mean"]["carry"] == 12.0
    assert comparison["reward_abs_max_mean"]["carry"] == 0.0


def test_paired_seeds_count_a_repeated_gap_as_agreement() -> None:
    runs = [
        _run("reset", 1, 0.495),
        _run("carry", 1, 0.580),
        _run("reset", 2, 0.512),
        _run("carry", 2, 0.603),
        _run("reset", 3, 0.480),
        _run("carry", 3, 0.521),
    ]

    paired = learning_horizon.build_comparison(
        {"reset": _policy("reset", 0.496, 5.9e-3), "carry": _policy("carry", 0.568, 8.1e-3)},
        runs,
    )["paired_seeds"]

    assert paired["seeds_compared"] == 3
    assert paired["carry_leads_seeds"] == 3
    assert paired["reset_leads_seeds"] == 0
    assert paired["mean_carry_minus_reset"] == pytest.approx((0.085 + 0.091 + 0.041) / 3)
    assert paired["seeds_agree"] is True
    assert [pair["seed"] for pair in paired["pairs"]] == [1, 2, 3]


def test_paired_seeds_expose_a_split_ranking() -> None:
    runs = [
        _run("reset", 1, 0.495),
        _run("carry", 1, 0.580),
        _run("reset", 2, 0.512),
        _run("carry", 2, 0.431),
        _run("reset", 3, 0.480),
        # seed 3 has no carry run yet: it is not a pair
    ]

    paired = learning_horizon.build_comparison(
        {"reset": _policy("reset", 0.496, 5.9e-3), "carry": _policy("carry", 0.506, 8.1e-3)},
        runs,
    )["paired_seeds"]

    assert paired["seeds_compared"] == 2
    assert paired["carry_leads_seeds"] == 1
    assert paired["reset_leads_seeds"] == 1
    assert paired["seeds_agree"] is False
