"""Apply the N4 promotion criteria to one native match block.

``native_block.py`` merges evaluator chunks and reports the paired win rate, a
Wilson interval, the seat split and the execution totals.  This tool turns that
artifact into a pass/fail decision, so a run is promoted by the recorded numbers
rather than by reading the block.

The gate is deliberately conservative and is **never** satisfied by a crforge
metric:

* at least ``--min-games`` decisive native games;
* zero illegal actions and zero rejected actions;
* truncations at most ``--max-truncated-fraction`` of the block;
* the candidate win rate at least ``--min-win-rate`` **and** its Wilson lower
  bound above 0.5;
* no seat below ``--min-seat-rate`` (a seat collapse fakes an overall result);
* no ``native_block`` identity/schedule warnings.

Verdicts: ``PASS`` (exit 0), ``FAIL`` (exit 1), ``INSUFFICIENT`` (exit 2).

    python tools/native_analysis/promotion_gate.py \
        --block runs/native-n4-gate/block.json \
        --output runs/native-n4-gate/gate.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA = "native-promotion-gate.v1"
DEFAULT_MIN_GAMES = 150
DEFAULT_MIN_WIN_RATE = 0.55
DEFAULT_MIN_SEAT_RATE = 0.45
DEFAULT_MAX_TRUNCATED_FRACTION = 0.02
DEFAULT_MIN_SEAT_GAMES = 20


def _rate(block: dict[str, Any], key: str) -> float | None:
    section = block.get(key)
    return None if not isinstance(section, dict) else section.get("rate")


def evaluate(
    block: dict[str, Any],
    *,
    min_games: int = DEFAULT_MIN_GAMES,
    min_win_rate: float = DEFAULT_MIN_WIN_RATE,
    min_seat_rate: float = DEFAULT_MIN_SEAT_RATE,
    max_truncated_fraction: float = DEFAULT_MAX_TRUNCATED_FRACTION,
    min_seat_games: int = DEFAULT_MIN_SEAT_GAMES,
) -> dict[str, Any]:
    if min_games <= 0 or not 0.0 < min_win_rate <= 1.0 or not 0.0 <= min_seat_rate <= 1.0:
        raise ValueError("gate thresholds are invalid")
    if not 0.0 <= max_truncated_fraction <= 1.0 or min_seat_games <= 0:
        raise ValueError("gate thresholds are invalid")

    totals = block.get("totals") if isinstance(block.get("totals"), dict) else {}
    games = int(totals.get("games") or 0)
    decisive = int(totals.get("decisive") or 0)
    truncations = int(totals.get("truncated") or 0)
    illegal = int(totals.get("illegal_total") or 0)
    rejected = int(totals.get("rejected_total") or 0)
    win_rate = _rate(block, "win_rate_a")
    interval = block.get("win_rate_a") if isinstance(block.get("win_rate_a"), dict) else {}
    ci_low = interval.get("ci_low")
    truncated_fraction = (truncations / games) if games else 1.0

    seats = block.get("by_a_owner") if isinstance(block.get("by_a_owner"), dict) else {}
    measured_seats = {
        str(seat): row
        for seat, row in seats.items()
        if isinstance(row, dict) and int(row.get("decisive") or 0) >= min_seat_games
    }
    seat_rates = {
        seat: row.get("a_win_rate")
        for seat, row in measured_seats.items()
        if row.get("a_win_rate") is not None
    }
    warnings = list(block.get("warnings") or [])

    criteria = {
        "decisive_games_ge_min": decisive >= min_games,
        "zero_illegal": illegal == 0,
        "zero_rejected": rejected == 0,
        "truncated_fraction_le_max": truncated_fraction <= max_truncated_fraction,
        "win_rate_ge_min": win_rate is not None and win_rate >= min_win_rate,
        "wilson_lower_gt_half": ci_low is not None and ci_low > 0.5,
        "seat_split_present": bool(seat_rates),
        "no_seat_below_min": bool(seat_rates)
        and all(rate >= min_seat_rate for rate in seat_rates.values()),
        "no_block_warnings": not warnings,
    }
    insufficient = decisive < min_games or not seat_rates or win_rate is None or ci_low is None
    if insufficient:
        verdict = "INSUFFICIENT"
    elif all(criteria.values()):
        verdict = "PASS"
    else:
        verdict = "FAIL"

    return {
        "schema": SCHEMA,
        "block": block.get("name"),
        "verdict": verdict,
        "criteria": criteria,
        "measurements": {
            "games": games,
            "decisive": decisive,
            "win_rate": win_rate,
            "ci_low": ci_low,
            "ci_high": interval.get("ci_high"),
            "truncated": truncations,
            "truncated_fraction": truncated_fraction,
            "illegal_total": illegal,
            "rejected_total": rejected,
            "seat_rates": seat_rates,
            "seat_games": {
                seat: int(row.get("decisive") or 0) for seat, row in measured_seats.items()
            },
        },
        "thresholds": {
            "min_games": min_games,
            "min_win_rate": min_win_rate,
            "min_seat_rate": min_seat_rate,
            "max_truncated_fraction": max_truncated_fraction,
            "min_seat_games": min_seat_games,
        },
        "warnings": warnings,
    }


def report(result: dict[str, Any]) -> str:
    measurements = result["measurements"]
    lines = [
        f"{result['block'] or 'block'}: {result['verdict']}",
        (
            f"  decisive {measurements['decisive']}/{measurements['games']}, "
            f"win rate {measurements['win_rate']} "
            f"[{measurements['ci_low']}, {measurements['ci_high']}], "
            f"seats {measurements['seat_rates']}"
        ),
        (
            f"  illegal {measurements['illegal_total']}, rejected {measurements['rejected_total']}, "
            f"truncated {measurements['truncated']} ({measurements['truncated_fraction']:.3f})"
        ),
    ]
    for name, passed in result["criteria"].items():
        lines.append(f"  {'PASS' if passed else 'FAIL'}  {name}")
    for warning in result["warnings"]:
        lines.append(f"  WARNING  {warning}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--block", type=Path, required=True, help="native-match-block.v1 JSON")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--min-games", type=int, default=DEFAULT_MIN_GAMES)
    parser.add_argument("--min-win-rate", type=float, default=DEFAULT_MIN_WIN_RATE)
    parser.add_argument("--min-seat-rate", type=float, default=DEFAULT_MIN_SEAT_RATE)
    parser.add_argument("--max-truncated-fraction", type=float, default=DEFAULT_MAX_TRUNCATED_FRACTION)
    parser.add_argument("--min-seat-games", type=int, default=DEFAULT_MIN_SEAT_GAMES)
    args = parser.parse_args(argv)

    raw = json.loads(args.block.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit(f"{args.block} is not a match block")
    if raw.get("schema") != "native-match-block.v1":
        raise SystemExit(f"{args.block} is not a native-match-block.v1 artifact")
    result = evaluate(
        raw,
        min_games=args.min_games,
        min_win_rate=args.min_win_rate,
        min_seat_rate=args.min_seat_rate,
        max_truncated_fraction=args.max_truncated_fraction,
        min_seat_games=args.min_seat_games,
    )
    text = report(result)
    print(text)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if result["verdict"] == "PASS" else 2 if result["verdict"] == "INSUFFICIENT" else 1


if __name__ == "__main__":
    sys.exit(main())
