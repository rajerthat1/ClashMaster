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
