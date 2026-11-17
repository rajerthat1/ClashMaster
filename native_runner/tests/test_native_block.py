"""A native promotion block is several evaluator chunks, not one run.

The handoff's gate is a 50-game paired block assembled from chunks, because a
native transport can die mid-match.  This pins the merge: the counts, the
Wilson interval, the seat split, and the refusal to merge different
experiments without saying so.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from native_runner.paths import REPOSITORY_ROOT

TOOLS = REPOSITORY_ROOT / "tools" / "native_analysis"
sys.path.insert(0, str(TOOLS))

import native_block  # type: ignore[import-not-found]  # noqa: E402


def write_evaluator(
    directory: Path,
    rows: list[dict],
    *,
    checkpoints=(("pilot", "aa"), ("seed", "bb")),
    abi: str = "x86_64",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    identity = {
        "games": len(rows),
        "sample": True,
        "match": {"deck0": [1, 2], "deck1": [3, 4], "seed": rows[0]["seed"]},
        "checkpoints": [
            {"path": f"/models/{name}.pt", "sha256": digest, "checkpoint_id": name}
            for name, digest in checkpoints
        ],
        "client_version": "16.635.1",
        "ruleset_id": "r1",
        "native_build": {"abi": abi, "probe_sha256": "cc"},
    }
    (directory / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
    (directory / "matches.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return directory


def row(game: int, seed: int, a_owner: int, winner, **extra) -> dict:
    payload = {
        "game": game,
        "seed": seed,
        "a_owner": a_owner,
        "winner": winner,
        "terminated": winner is not None,
        "truncated": False,
        "tick": 3601,
        "illegal_total": 0,
        "rejected_total": 0,
        "rejected_actions": 0,
        "rejected_reasons": {},
    }
    payload.update(extra)
    return payload


def test_wilson_interval_and_exact_test():
    low, high = native_block.wilson_interval(10, 10)
    assert low == pytest.approx(0.723, abs=1e-3)
    assert high == pytest.approx(1.0)
    low, high = native_block.wilson_interval(0, 10)
    assert low == pytest.approx(0.0)
    assert high == pytest.approx(0.277, abs=1e-3)
    low, high = native_block.wilson_interval(5, 10)
    assert low == pytest.approx(0.237, abs=1e-3)
    assert high == pytest.approx(0.763, abs=1e-3)
    # 10/10 under an even prior is two tails of 1/1024.
    assert native_block.binomial_two_sided(10, 10) == pytest.approx(2 / 1024, abs=1e-9)
    assert native_block.binomial_two_sided(5, 10) == pytest.approx(1.0, abs=1e-9)
    assert native_block.binomial_two_sided(0, 0) == 1.0


def test_block_merges_chunks_and_splits_by_seat(tmp_path):
    first = write_evaluator(
        tmp_path / "chunk1",
        [
            row(1, 101, 0, "a", actions_by_owner=[10, 12]),
            row(2, 102, 1, "b", actions_by_owner=[11, 9]),
        ],
    )
    second = write_evaluator(
        tmp_path / "chunk2",
        [
            row(3, 103, 0, "a"),
            row(4, 104, 1, "a"),
            row(5, 105, 0, None, terminated=False, truncated=True),
        ],
    )
    block = native_block.summarize([first, second], name="pilot-vs-seed")
    assert block["schema"] == native_block.SCHEMA
    assert block["warnings"] == []
    totals = block["totals"]
    assert totals["games"] == 5
    assert totals["decisive"] == 4
    assert (totals["a_wins"], totals["b_wins"], totals["draws"], totals["truncated"]) == (3, 1, 0, 1)
    assert block["win_rate_a"]["trials"] == 4
    assert block["win_rate_a"]["rate"] == pytest.approx(0.75)
    assert block["win_rate_a"]["ci_low"] < 0.75 < block["win_rate_a"]["ci_high"]
    assert block["by_a_owner"]["0"]["a_wins"] == 2
    assert block["by_a_owner"]["0"]["decisive"] == 2
    assert block["by_a_owner"]["1"]["a_wins"] == 1
    assert block["by_a_owner"]["1"]["b_wins"] == 1
    assert block["seeds"] == {"first": 101, "last": 105, "distinct": 5, "contiguous": True}
    assert [chunk["games"] for chunk in block["chunks"]] == [2, 3]


def test_block_warns_on_mixed_experiments_and_duplicates(tmp_path):
    first = write_evaluator(tmp_path / "chunk1", [row(1, 101, 0, "a")])
    second = write_evaluator(
        tmp_path / "chunk2",
        [row(2, 101, 0, "a")],
        checkpoints=(("pilot", "aa"), ("hog26", "dd")),
        abi="arm64-v8a",
    )
    block = native_block.summarize([first, second])
    joined = " ".join(block["warnings"])
    assert "differing" in joined
    assert "duplicated" in joined
