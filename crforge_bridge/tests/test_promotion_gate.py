"""The N4 promotion gate: native block -> PASS / FAIL / INSUFFICIENT."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "native_analysis"
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import promotion_gate


def _block(
    *,
    rate: float = 0.6,
    ci_low: float = 0.52,
    decisive: int = 150,
    games: int = 150,
    illegal: int = 0,
    rejected: int = 0,
    truncated: int = 0,
    seats: dict | None = None,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "schema": "native-match-block.v1",
        "name": "n4-vs-seed",
        "totals": {
            "games": games,
            "decisive": decisive,
            "illegal_total": illegal,
            "rejected_total": rejected,
            "truncated": truncated,
        },
        "win_rate_a": {"rate": rate, "ci_low": ci_low, "ci_high": 0.68},
        "by_a_owner": seats
        or {
            "0": {"decisive": 75, "a_win_rate": rate, "ci_low": ci_low},
            "1": {"decisive": 75, "a_win_rate": rate, "ci_low": ci_low},
        },
        "warnings": warnings or [],
    }


def test_a_significant_win_rate_with_clean_seats_passes() -> None:
    result = promotion_gate.evaluate(_block())
    assert result["verdict"] == "PASS"
    assert all(result["criteria"].values())


def test_an_even_win_rate_fails_the_interval_and_threshold() -> None:
    result = promotion_gate.evaluate(_block(rate=0.5, ci_low=0.42))
    assert result["verdict"] == "FAIL"
    assert result["criteria"]["win_rate_ge_min"] is False
    assert result["criteria"]["wilson_lower_gt_half"] is False


def test_illegal_or_rejected_actions_fail_the_gate() -> None:
    assert promotion_gate.evaluate(_block(illegal=1))["verdict"] == "FAIL"
    assert promotion_gate.evaluate(_block(rejected=1))["verdict"] == "FAIL"


def test_a_collapsed_seat_fails_the_gate() -> None:
    seats = {
        "0": {"decisive": 75, "a_win_rate": 0.3, "ci_low": 0.2},
        "1": {"decisive": 75, "a_win_rate": 0.8, "ci_low": 0.7},
    }
    result = promotion_gate.evaluate(_block(seats=seats))
    assert result["verdict"] == "FAIL"
    assert result["criteria"]["no_seat_below_min"] is False


def test_block_warnings_fail_the_gate() -> None:
    result = promotion_gate.evaluate(_block(warnings=["mixed checkpoint identity"]))
    assert result["verdict"] == "FAIL"
    assert result["criteria"]["no_block_warnings"] is False


def test_too_few_decisive_games_is_insufficient() -> None:
    result = promotion_gate.evaluate(_block(decisive=100, games=100))
    assert result["verdict"] == "INSUFFICIENT"


def test_the_cli_writes_the_verdict_and_its_exit_code(tmp_path: Path) -> None:
    block = tmp_path / "block.json"
    output = tmp_path / "gate.json"
    block.write_text(json.dumps(_block()), encoding="utf-8")
    assert promotion_gate.main(["--block", str(block), "--output", str(output)]) == 0
    verdict = json.loads(output.read_text(encoding="utf-8"))
    assert verdict["verdict"] == "PASS"
    assert verdict["measurements"]["decisive"] == 150


def test_the_cli_rejects_a_foreign_schema(tmp_path: Path) -> None:
    block = tmp_path / "block.json"
    block.write_text(json.dumps({"schema": "something-else"}), encoding="utf-8")
    with pytest.raises(SystemExit):
        promotion_gate.main(["--block", str(block)])
