"""The parity manifest must compare experiments, not build identities.

`runs/parity-a51` is captured on Null's ARM64 client, while the candidate
corpus comes from the official x86_64 build.  Package, ABI, libg/probe hashes
and the ruleset id are *supposed* to differ; the match definition and the
per-game rows are what the gate compares.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from native_runner.paths import REPOSITORY_ROOT

TOOLS = REPOSITORY_ROOT / "tools" / "native_analysis"
sys.path.insert(0, str(TOOLS))

import parity_manifest  # type: ignore[import-not-found]


MATCH = {
    "deck0": [1, 2, 3, 4, 5, 6, 7, 8],
    "deck1": [1, 2, 3, 4, 5, 6, 7, 8],
    "seed": 7,
    "game_mode": 72000006,
}


def write_evaluator(
    directory: Path,
    *,
    tick: int = 3601,
    package: str = "nullsroyale.rel.free",
    abi: str = "arm64-v8a",
    probe: str = "a" * 64,
    ruleset: str = "r1",
) -> Path:
    directory.mkdir(parents=True)
    identity = {
        "games": 2,
        "sample": True,
        "match": dict(MATCH),
        "client_version": "16.635.1",
        "ruleset_id": ruleset,
        "native_build": {
            "client_version": "16.635.1",
            "package": package,
            "abi": abi,
            "libg_sha256": "b" * 64,
            "probe_sha256": probe,
            "content_version": "16.635.93",
            "catalog_release": "nr_15.535.13_release_ff1c6c29",
            "combat_ring_hooks": False,
        },
        "checkpoints": [
            {"sha256": "c" * 64, "checkpoint_id": "checkpoint-a"},
            {"sha256": "d" * 64, "checkpoint_id": "checkpoint-b"},
        ],
    }
    (directory / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
    row = {
        "game": 1,
        "seed": 7,
        "a_owner": 0,
        "winner": "a",
        "terminated": True,
        "truncated": False,
        "tick": tick,
        "terminal": {"ended": True, "winner": 0, "terminal_tick": tick},
        "actions_by_owner": [28, 28],
        "rejected_actions": 0,
    }
    (directory / "matches.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    return directory


def test_evaluator_key_is_the_experiment_not_the_directory(tmp_path: Path) -> None:
    write_evaluator(tmp_path / "a51-model-general-vs-il.json")
    write_evaluator(
        tmp_path / "x86-model-general-vs-il.json",
        package="com.supercell.clashroyale",
        abi="x86_64",
        probe="e" * 64,
        ruleset="r2",
    )
    manifest = parity_manifest.build(tmp_path)
    evaluator = [entry for entry in manifest["entries"] if entry["kind"] == "evaluator"]
    assert len(evaluator) == 2
    assert evaluator[0]["key"] == evaluator[1]["key"]
    assert evaluator[0]["key"].startswith("evaluator-checkpoint-a-checkpoint-b-seed7-games2")
    # A build-identity difference between two runs of the same experiment is
    # not nondeterminism.
    assert manifest["deterministic"] is True


def test_evaluator_compare_ignores_build_identity_but_not_rows(tmp_path: Path) -> None:
    write_evaluator(tmp_path / "reference")
    reference = parity_manifest.build(tmp_path)

    candidate_root = tmp_path / "candidate"
    write_evaluator(
        candidate_root / "run",
        package="com.supercell.clashroyale",
        abi="x86_64",
        probe="e" * 64,
        ruleset="different",
    )
    candidate = parity_manifest.build(candidate_root)
    assert parity_manifest.compare(reference, candidate)["matched"] is True

    diverged_root = tmp_path / "diverged"
    write_evaluator(diverged_root / "run", tick=3774)
    diverged = parity_manifest.build(diverged_root)
    result = parity_manifest.compare(reference, diverged)
    assert result["matched"] is False
    assert "terminal_tick" in result["results"][0]["first_difference"]


def write_capacity(root: Path, *, generation: int) -> Path:
    root.mkdir(parents=True)
    report = {
        "clientVersion": "16.635.1",
        "engineCount": 1,
        "slots": 8,
        "batches": 10,
        "ticks": 600,
        "totalActions": 48,
        "lanes": [
            {
                "slots": 8,
                "ok": True,
                "clientVersion": "16.635.1",
                "totalActions": 48,
                "residentStatus": {
                    "ok": True,
                    "capacity": 16,
                    "occupied": 8,
                    "boundEnvId": 7,
                    "switches": 80,
                    "totalSteps": 48000,
                    "slots": [
                        {
                            "envId": index,
                            "manager": f"0x{index:08x}",
                            "generation": generation + index,
                            "stateEpoch": generation + index,
                            "steps": 6000,
                            "tick": 6000,
                            "ended": False,
                            "telemetryReady": True,
                        }
                        for index in range(8)
                    ],
                },
            }
        ],
    }
    (root / "capacity.json").write_text(json.dumps(report), encoding="utf-8")
    return root


def test_capacity_compare_ignores_per_process_generation(tmp_path: Path) -> None:
    write_capacity(tmp_path / "reference", generation=9)
    reference = parity_manifest.build(tmp_path)
    candidate_root = tmp_path / "candidate"
    write_capacity(candidate_root / "run", generation=11)
    candidate = parity_manifest.build(candidate_root)
    result = parity_manifest.compare(reference, candidate)
    assert result["matched"] is True, result["results"]


def test_evaluator_compare_rejects_a_different_match_definition(tmp_path: Path) -> None:
    write_evaluator(tmp_path / "reference")
    reference = parity_manifest.build(tmp_path)

    candidate_root = tmp_path / "candidate"
    directory = write_evaluator(candidate_root / "run")
    identity_path = directory / "identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["match"]["deck0"] = [8, 7, 6, 5, 4, 3, 2, 1]
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    candidate = parity_manifest.build(candidate_root)

    result = parity_manifest.compare(reference, candidate)
    assert result["matched"] is False
    assert "/match/deck0" in result["results"][0]["first_difference"]
