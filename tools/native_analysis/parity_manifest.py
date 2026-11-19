#!/usr/bin/env python3
"""Archive, verify and compare an A51 parity corpus.

The 16.635.1 port is gated on the x86_64 engine reproducing the A51 corpus
tick-for-tick (the session notes step 1.0/1.4, the project plan gate 0).  This tool builds the
manifest that gate needs:

* hashes every corpus file plus the checkpoint/probe/client artifacts the
  reports were produced with;
* checks that repeated runs of the same seed are decision-identical (timing
  fields are ignored);
* compares a candidate corpus (e.g. from the x86 engine) against a reference
  and reports the first divergent field per match.

Corpus files are recognised by content, not name:

* scripted match report — ``clientVersion`` + ``decisions`` + ``final``
* evaluator output   — directory with ``identity.json`` (+ ``matches.jsonl``)
* capacity run       — ``engineCount`` + ``lanes`` + ``slots``

Usage::

    python tools/native_analysis/parity_manifest.py \
        --corpus /tmp/opencode/parity \
        --output /tmp/opencode/parity/manifest.json

    python tools/native_analysis/parity_manifest.py \
        --corpus /tmp/opencode/x86-parity \
        --compare /tmp/opencode/parity/manifest.json \
        --output /tmp/opencode/x86-parity/compare.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA = "v4-a51-parity-corpus.v1"

# Volatile per-run fields: wall-clock timing, host ports, latency samples and
# engine-process counters (a resident slot's generation/stateEpoch increments
# with every configure in that process, so it differs between the A51 and x86
# even when the capacity result is identical).
TIMING_KEYS = {
    "generation",
    "stateEpoch",
    "configureSeconds",
    "seconds",
    "matchesPerSecond",
    "latenciesMs",
    "latency",
    "wallSeconds",
    "laneSeconds",
    "batchSeconds",
    "totalBatchSeconds",
    "medianBatchSeconds",
    "aggregateWindowSeconds",
    "aggregateSlotTicksPerSecond",
    "aggregateSlotMatchesPerSecond",
    "makespanSlotTicksPerSecond",
    "makespanSlotMatchesPerSecond",
    "engineTicksPerSecond",
    "slotMatchesPerSecond",
    "aggregateActionsPerSecond",
    "actionsPerSecond",
    "connect_ns",
    "send_ns",
    "request_ack_ns",
    "last_request_ack_ns",
    "receive_ns",
    "decode_ns",
    "profile",
    "sampleReceipts",
    "ports",
    "port",
    "host",
}

MAX_HASH_BYTES = 64 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalise(value: Any) -> Any:
    """Drop volatile timing/provenance fields so runs are comparable."""

    if isinstance(value, dict):
        return {
            key: normalise(item)
            for key, item in sorted(value.items())
            if key not in TIMING_KEYS and key != "milliseconds" and key != "manager"
        }
    if isinstance(value, list):
        return [normalise(item) for item in value]
    return value


def first_difference(reference: Any, candidate: Any, path: str = "") -> str | None:
    if type(reference) is not type(candidate):
        return f"{path or '/'}: type {type(reference).__name__} != {type(candidate).__name__}"
    if isinstance(reference, dict):
        for key in sorted(set(reference) | set(candidate)):
            if key not in reference:
                return f"{path}/{key}: unexpected in candidate"
            if key not in candidate:
                return f"{path}/{key}: missing in candidate"
            found = first_difference(reference[key], candidate[key], f"{path}/{key}")
            if found:
                return found
        return None
    if isinstance(reference, list):
        if len(reference) != len(candidate):
            return f"{path or '/'}: length {len(reference)} != {len(candidate)}"
        for index, (left, right) in enumerate(zip(reference, candidate)):
            found = first_difference(left, right, f"{path}[{index}]")
            if found:
                return found
        return None
    if reference != candidate:
        return f"{path or '/'}: {reference!r} != {candidate!r}"
    return None


def load_report(path: Path) -> dict[str, Any] | None:
    if path.stat().st_size > MAX_HASH_BYTES:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def classify(path: Path) -> tuple[str, dict[str, Any]] | None:
    if path.is_dir():
        identity = path / "identity.json"
        if identity.is_file():
            report = load_report(identity)
            if report is not None:
                return "evaluator", report
        return None
    report = load_report(path)
    if report is None:
        return None
    if all(key in report for key in ("clientVersion", "decisions", "final")):
        return "scripted", report
    if all(key in report for key in ("engineCount", "lanes", "slots")):
        return "capacity", report
    return None


def semantic_summary(kind: str, report: dict[str, Any]) -> dict[str, Any]:
    if kind == "scripted":
        final = report.get("final", {})
        return {
            "seed": report.get("seed"),
            "client_version": report.get("clientVersion"),
            "decisions": len(report.get("decisions") or []),
            "total_actions": report.get("totalActions"),
            "problems": report.get("problems"),
            "ability_rejections": report.get("abilityRejections"),
            "final": {
                "tick": final.get("tick"),
                "ended": final.get("ended"),
                "crowns": final.get("crowns"),
                "winner": final.get("winner"),
            },
        }
    if kind == "capacity":
        lanes = report.get("lanes") or []
        return {
            "engines": report.get("engineCount"),
            "slots": report.get("slots"),
            "batches": report.get("batches"),
            "ticks": report.get("ticks"),
            "total_actions": report.get("totalActions"),
            "lanes": [
                {
                    "slots": lane.get("slots"),
                    "ok": lane.get("ok"),
                    "resident_status": lane.get("residentStatus"),
                    "total_actions": lane.get("totalActions"),
                    "client_version": lane.get("clientVersion"),
                }
                for lane in lanes
            ],
        }
    # evaluator: per-game rows are compared separately in ``compare``.
    return {"games": report.get("games"), "sample": report.get("sample")}


def evaluate_rows(directory: Path) -> list[dict[str, Any]]:
    matches = directory / "matches.jsonl"
    rows: list[dict[str, Any]] = []
    if matches.is_file():
        for line in matches.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def corpus_key(kind: str, name: str, report: dict[str, Any]) -> str:
    if kind == "scripted":
        return f"scripted-seed{report.get('seed')}"
    if kind == "capacity":
        return f"capacity-{report.get('engineCount')}x{report.get('slots')}"
    # Key the evaluator run by its experiment, not by its directory name: the
    # A51 and x86 outputs live in differently named directories, and the build
    # identity (package, abi, libg/probe hashes, ruleset id) *must* differ
    # between the two oracles.
    ids = [checkpoint.get("checkpoint_id") for checkpoint in report.get("checkpoints", [])]
    match = report.get("match") or {}
    return "evaluator-{}-seed{}-games{}".format(
        "-".join(str(value) for value in ids) or name,
        match.get("seed"),
        report.get("games"),
    )


def evaluator_definition(report: dict[str, Any]) -> dict[str, Any]:
    """The portable part of an evaluator identity.

    Everything else in ``identity.json`` describes the build that produced it
    (package, ABI, libg/probe hashes, ruleset id).  Those are expected to
    differ between the ARM64 oracle and the x86_64 engine; the experiment
    definition and the per-game rows have to match.
    """
    return {
        "client_version": report.get("client_version"),
        "games": report.get("games"),
        "sample": report.get("sample"),
        "match": normalise(report.get("match")),
        "catalog_release": (report.get("native_build") or {}).get("catalog_release"),
        "combat_ring_hooks": (report.get("native_build") or {}).get("combat_ring_hooks"),
        "checkpoints": [
            {"sha256": checkpoint.get("sha256"), "checkpoint_id": checkpoint.get("checkpoint_id")}
            for checkpoint in report.get("checkpoints", [])
        ],
    }


def build(corpus: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted(corpus.iterdir()):
        if path.name in ("manifest.json", "compare.json", "installed-cluster.apk"):
            continue
        classified = classify(path)
        if classified is None:
            continue
        kind, report = classified
        entry: dict[str, Any] = {
            "kind": kind,
            "name": path.name,
            "key": corpus_key(kind, path.name, report),
            "path": str(path),
            "sha256": sha256_file(path / "identity.json") if path.is_dir() else sha256_file(path),
            "summary": semantic_summary(kind, report),
        }
        if kind == "evaluator":
            entry["games"] = evaluate_rows(path)
        entries.append(entry)

    entries.sort(key=lambda item: (item["kind"], item["key"], item["name"]))

    # Determinism: every repeated key must normalise identically.
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        groups.setdefault((entry["kind"], entry["key"]), []).append(entry)
    checks: list[dict[str, Any]] = []
    for (kind, key), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        for member in members:
            payload = classify(Path(member["path"]))
            if payload is None:
                continue
            if kind == "evaluator":
                # Repeats must agree on the experiment and the rows, not on
                # the build identity that produced them.
                member["_normalised"] = {
                    "definition": evaluator_definition(payload[1]),
                    "games": normalise(evaluate_rows(Path(member["path"]))),
                }
            else:
                member["_normalised"] = normalise(payload[1])
        reference = members[0]
        for candidate in members[1:]:
            difference = first_difference(reference["_normalised"], candidate["_normalised"])
            checks.append(
                {
                    "kind": kind,
                    "key": key,
                    "deterministic": difference is None,
                    "first_difference": difference,
                    "files": [reference["name"], candidate["name"]],
                }
            )
    for entry in entries:
        entry.pop("_normalised", None)

    artifact_paths = [
        "native_runner/versions/native-16.635.1.json",
        "native_runner/probe/out/libcrprobe.so",
        "native_runner/probe/out/libcrprobe-16.635.1.so",
        "builds/cr-engine-cluster-16.635.1-rf.apk",
        "builds/cr-engine-cluster-16.635.1.apk",
        "checkpoints/General/checkpoint-step-00000460.pt",
        "checkpoints/IL/checkpoint-step-00029396.pt",
    ]
    repo_root = Path(__file__).resolve().parents[2]
    artifacts = {}
    for relative in artifact_paths:
        path = repo_root / relative
        if path.is_file():
            artifacts[relative] = {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }

    return {
        "schema": SCHEMA,
        "corpus": str(corpus),
        "artifacts": artifacts,
        "entries": entries,
        "determinism": checks,
        "deterministic": all(check["deterministic"] for check in checks),
    }


def compare(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    def index(manifest: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for entry in manifest.get("entries", []):
            grouped.setdefault((entry["kind"], entry["key"]), []).append(entry)
        return grouped

    left, right = index(reference), index(candidate)
    results: list[dict[str, Any]] = []
    for key in sorted(set(left) | set(right)):
        kind, name = key
        if key not in right:
            results.append({"kind": kind, "key": name, "status": "missing-in-candidate"})
            continue
        if key not in left:
            results.append({"kind": kind, "key": name, "status": "unexpected-in-candidate"})
            continue
        ref_entry = left[key][0]
        cand_entry = right[key][0]
        reference_payload = classify(Path(ref_entry["path"]))
        candidate_payload = classify(Path(cand_entry["path"]))
        if reference_payload is None or candidate_payload is None:
            results.append({"kind": kind, "key": name, "status": "unreadable"})
            continue
        if kind == "evaluator":
            difference = first_difference(
                evaluator_definition(reference_payload[1]),
                evaluator_definition(candidate_payload[1]),
            )
            if difference is None:
                difference = first_difference(
                    normalise(ref_entry.get("games", [])), normalise(cand_entry.get("games", []))
                )
        else:
            difference = first_difference(
                normalise(reference_payload[1]), normalise(candidate_payload[1])
            )
        results.append(
            {
                "kind": kind,
                "key": name,
                "status": "match" if difference is None else "mismatch",
                "first_difference": difference,
            }
        )
    return {
        "schema": "v4-a51-parity-compare.v1",
        "reference": reference.get("corpus"),
        "candidate": candidate.get("corpus"),
        "matched": all(result["status"] == "match" for result in results),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--compare",
        type=Path,
        help="reference manifest; report whether this corpus reproduces it",
    )
    args = parser.parse_args(argv)

    if not args.corpus.is_dir():
        print(f"corpus directory not found: {args.corpus}", file=sys.stderr)
        return 2

    manifest = build(args.corpus)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(
        f"{len(manifest['entries'])} entries, "
        f"{len(manifest['determinism'])} determinism checks, "
        f"deterministic={manifest['deterministic']}"
    )
    for check in manifest["determinism"]:
        if not check["deterministic"]:
            print(f"  NON-DETERMINISTIC {check['key']}: {check['first_difference']}")
    print(f"manifest: {args.output}")

    if args.compare:
        reference = json.loads(args.compare.read_text(encoding="utf-8"))
        result = compare(reference, manifest)
        compare_path = args.output.with_name("compare.json")
        compare_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"reproduces {reference.get('corpus')}: {result['matched']}")
        for item in result["results"]:
            if item["status"] != "match":
                print(f"  {item['status']}: {item['key']} ({item.get('first_difference')})")
        print(f"comparison: {compare_path}")
        return 0 if result["matched"] else 1

    return 0 if manifest["deterministic"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
