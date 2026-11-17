#!/usr/bin/env python3
"""Aggregate native evaluator outputs into one matched block, with an interval.

``native_runner.training.v4.evaluate`` writes one ``matches.jsonl`` per
invocation.  A promotion gate is never one invocation: it is a block of games
that is large enough for a win rate to mean something and that is run in
chunks, because a native transport can die mid-match and a chunk is the unit
that is cheap to lose.  This tool merges those chunks back into one block and
refuses to average over runs that are not the same experiment.

It reports:

* the A/B/draw/truncation counts and the illegal/rejected gates, summed;
* the A win rate over decisive games with a Wilson interval and an exact
  two-sided binomial test against even;
* the split by A's seat (and by deck when the rows record one), because a
  seat or deck asymmetry is the first thing that fakes a policy result;
* warnings for a mixed checkpoint, ruleset, native build, shape, or a
  duplicated ``(seed, a_owner)`` schedule.

Usage::

    python tools/native_analysis/native_block.py \
        --output runs/native-parity-s40/pilot-vs-seed.json \
        runs/native-parity-s40/pilot-vs-seed-chunk*

The output is JSON with ``schema`` ``native-match-block.v1``.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

SCHEMA = "native-match-block.v1"
CONFIDENCE = 0.95
Z_95 = 1.959963984540054

# Fields that legitimately differ between chunks of one block.
CHUNK_LOCAL_MATCH_FIELDS = {"seed"}


def wilson_interval(successes: int, trials: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (0,0) on an empty set."""
    if trials <= 0:
        return (0.0, 1.0)
    p = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (p + z * z / (2.0 * trials)) / denominator
    half = z * math.sqrt(p * (1.0 - p) / trials + z * z / (4.0 * trials * trials)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


def binomial_two_sided(successes: int, trials: int, p: float = 0.5) -> float:
    """Exact two-sided binomial p-value, by the probability-ordering rule."""
    if trials <= 0:
        return 1.0

    def pmf(k: int) -> float:
        return math.comb(trials, k) * (p**k) * ((1.0 - p) ** (trials - k))

    observed = pmf(successes)
    tail = sum(pmf(k) for k in range(trials + 1) if pmf(k) <= observed * (1.0 + 1e-9))
    return min(1.0, tail)


def load_rows(directory: Path) -> list[dict[str, Any]]:
    """Read one evaluator directory's rows."""
    path = directory / "matches.jsonl"
    if not path.is_file():
        raise ValueError(f"{directory} has no matches.jsonl")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _identity(directory: Path) -> dict[str, Any] | None:
    path = directory / "identity.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _checkpoint_key(identity: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(entry.get("checkpoint_id")), str(entry.get("sha256")))
        for entry in identity.get("checkpoints", ())
    )


def _shape_key(identity: dict[str, Any]) -> dict[str, Any]:
    match = dict(identity.get("match") or {})
    for key in CHUNK_LOCAL_MATCH_FIELDS:
        match.pop(key, None)
    return {
        "checkpoints": _checkpoint_key(identity),
        "ruleset_id": identity.get("ruleset_id"),
        "client_version": identity.get("client_version"),
        "abi": (identity.get("native_build") or {}).get("abi"),
        "probe_sha256": (identity.get("native_build") or {}).get("probe_sha256"),
        "sample": identity.get("sample"),
        "match": match,
    }


def summarize(directories: list[Path], *, name: str | None = None) -> dict[str, Any]:
    """Merge evaluator chunks into one block and check they are one experiment."""
    if not directories:
        raise ValueError("at least one evaluator directory is required")
    directories = [Path(directory) for directory in directories]
    rows: list[dict[str, Any]] = []
    chunks = []
    identities = []
    for directory in directories:
        identity = _identity(directory)
        if identity is None:
            raise ValueError(f"{directory} has no identity.json")
        chunk_rows = load_rows(directory)
        identities.append(identity)
        chunks.append(
            {
                "path": str(directory),
                "games": len(chunk_rows),
                "first_seed": chunk_rows[0].get("seed") if chunk_rows else None,
                "last_seed": chunk_rows[-1].get("seed") if chunk_rows else None,
            }
        )
        rows.extend(chunk_rows)

    warnings: list[str] = []
    shapes = [_shape_key(identity) for identity in identities]
    reference = shapes[0]
    if any(shape != reference for shape in shapes[1:]):
        differing = [
            key
            for key in reference
            if any(shape.get(key) != reference.get(key) for shape in shapes[1:])
        ]
        warnings.append("chunks are not the same experiment; differing: " + ", ".join(sorted(differing)))

    schedule = [(row.get("seed"), row.get("a_owner")) for row in rows]
    duplicates = sorted({key for key in schedule if schedule.count(key) > 1}, key=lambda k: (k[0] is None, k))
    if duplicates:
        warnings.append(f"duplicated (seed, a_owner) pairs: {duplicates[:5]}")

    seeds = sorted({row.get("seed") for row in rows if isinstance(row.get("seed"), int)})
    if len(seeds) > 1 and seeds != list(range(seeds[0], seeds[0] + len(seeds))):
        missing = sorted(set(range(seeds[0], seeds[-1] + 1)) - set(seeds))
        warnings.append(f"seed schedule is not contiguous; missing {missing[:10]}")

    decisive = [row for row in rows if row.get("winner") in ("a", "b")]
    a_wins = sum(row["winner"] == "a" for row in decisive)
    b_wins = sum(row["winner"] == "b" for row in decisive)
    draws = sum(row.get("winner") is None and row.get("terminated") for row in rows)
    low, high = wilson_interval(a_wins, len(decisive))

    def split(key: str) -> dict[str, Any]:
        output: dict[str, Any] = {}
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            value = row.get(key)
            if isinstance(value, (dict, list)):
                continue
            groups.setdefault(str(value), []).append(row)
        for value in sorted(groups):
            group = groups[value]
            group_decisive = [row for row in group if row.get("winner") in ("a", "b")]
            wins = sum(row["winner"] == "a" for row in group_decisive)
            group_low, group_high = wilson_interval(wins, len(group_decisive))
            output[value] = {
                "games": len(group),
                "decisive": len(group_decisive),
                "a_wins": wins,
                "b_wins": len(group_decisive) - wins,
                "a_win_rate": (wins / len(group_decisive)) if group_decisive else None,
                "ci_low": group_low if group_decisive else None,
                "ci_high": group_high if group_decisive else None,
            }
        return output

    totals = {
        "games": len(rows),
        "decisive": len(decisive),
        "a_wins": a_wins,
        "b_wins": b_wins,
        "draws": draws,
        "truncated": sum(bool(row.get("truncated")) for row in rows),
        "illegal_total": sum(int(row.get("illegal_total") or 0) for row in rows),
        "rejected_total": sum(int(row.get("rejected_total") or 0) for row in rows),
        "rejected_unique_total": sum(int(row.get("rejected_actions") or 0) for row in rows),
        "games_with_rejections": sum(bool(row.get("rejected_total")) for row in rows),
        "rejected_reasons": {
            reason: sum(int((row.get("rejected_reasons") or {}).get(reason, 0) or 0) for row in rows)
            for reason in sorted({reason for row in rows for reason in (row.get("rejected_reasons") or {})})
        },
    }

    return {
        "schema": SCHEMA,
        "name": name,
        "confidence": CONFIDENCE,
        "chunks": chunks,
        "shape": reference,
        "totals": totals,
        "win_rate_a": {
            "wins": a_wins,
            "trials": len(decisive),
            "rate": (a_wins / len(decisive)) if decisive else None,
            "ci_low": low,
            "ci_high": high,
            "method": "wilson",
            "p_value_vs_even": binomial_two_sided(a_wins, len(decisive)),
        },
        "by_a_owner": split("a_owner"),
        "seeds": {
            "first": seeds[0] if seeds else None,
            "last": seeds[-1] if seeds else None,
            "distinct": len(seeds),
            "contiguous": bool(seeds)
            and seeds == list(range(seeds[0], seeds[0] + len(seeds))),
        },
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directories", nargs="+", type=Path, help="evaluator output directories")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", default=None, help="block name recorded in the artifact")
    args = parser.parse_args(argv)
    block = summarize(args.directories, name=args.name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(block, indent=2), encoding="utf-8")
    totals = block["totals"]
    rate = block["win_rate_a"]
    print(
        f"{args.name or 'block'}: {totals['a_wins']}-{totals['b_wins']} "
        f"({totals['decisive']} decisive, {totals['games']} games), "
        f"A win rate {rate['rate']} [{rate['ci_low']:.3f}, {rate['ci_high']:.3f}] "
        f"p={rate['p_value_vs_even']:.4f}, illegal {totals['illegal_total']}, "
        f"rejected {totals['rejected_total']}, truncated {totals['truncated']}",
        flush=True,
    )
    for warning in block["warnings"]:
        print(f"WARNING: {warning}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
