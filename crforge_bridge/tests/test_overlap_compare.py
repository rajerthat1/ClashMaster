"""The pipeline gate must not call a wall win a pass when the collector lost time."""

from __future__ import annotations

import sys
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "crforge"
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import overlap_compare


def _comparison(**overrides: object) -> dict[str, object]:
    comparison: dict[str, object] = {
        "serial_wall_s_mean": 103.8,
        "overlap_wall_s_mean": 100.7,
        "serial_collection_lane_decisions_per_s_mean": 29.2,
        "overlap_collection_lane_decisions_per_s_mean": 18.9,
        "collection_rate_ratio": 0.65,
        "overlap_collection_identity_unchanged_all": True,
        "all_training_pass": True,
    }
    comparison.update(overrides)
    return comparison


def test_a_wall_win_paid_for_by_the_collector_fails_the_gate() -> None:
    gate = overlap_compare.build_gate(_comparison(), min_collection_rate_ratio=0.9)

    assert gate["pipeline_is_faster"] is True
    assert gate["collection_rate_ratio"] == 0.65
    assert gate["collection_rate_preserved"] is False
    assert gate["pass"] is False


def test_the_gate_passes_when_the_update_is_hidden_without_slowing_collection() -> None:
    gate = overlap_compare.build_gate(
        _comparison(
            overlap_wall_s_mean=72.0,
            overlap_collection_lane_decisions_per_s_mean=28.0,
            collection_rate_ratio=0.959,
        ),
        min_collection_rate_ratio=0.9,
    )

    assert gate["collection_rate_preserved"] is True
    assert gate["pass"] is True


def test_the_gate_refuses_a_run_without_a_measured_collection_ratio() -> None:
    gate = overlap_compare.build_gate(_comparison(collection_rate_ratio=None), min_collection_rate_ratio=0.9)

    assert gate["collection_rate_preserved"] is False
    assert gate["pass"] is False
