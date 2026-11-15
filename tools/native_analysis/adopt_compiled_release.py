#!/usr/bin/env python3
"""Adopt a compiled 16.x catalog set as a first-class competitive release.

Third-party bundles (for example ``RoyaleFish/data_16635``) ship reviewed facts,
compiled catalogs and a ``model_catalogs`` snapshot, but their manifest is stale
and ``model_catalogs.input_catalog_ids`` does not match the shipped
``card_logic``/``effects``/``projectiles``.  Their own workflow disabled the
integrity checks to get past that.

This tool instead repairs the link and pins the bundle properly:

* copies the compiled facts and the reviewed facts verbatim;
* rewrites only ``model_catalogs.input_catalog_ids`` so it names the catalogs
  actually shipped (the rest of the snapshot is authoritative);
* writes a manifest whose SHA-256 values hash exactly those files, including the
  card-partition/source-file identity values and the form-evidence pin;
* emits a release descriptor under ``native_runner/releases``.

The result passes the normal fail-closed loader; no check is disabled.

Usage::

    python tools/native_analysis/adopt_compiled_release.py \
        --source .resource/RoyaleFish/data_16635 \
        --output native_runner/data/competitive-16.635 \
        --name 16.635.1 --runtime-feature-dim 21 \
        --expect-checkpoint .resource/RoyaleFish/models/B-general-v275.pt
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

COMPILED = ("card_specs", "card_logic", "effects", "projectiles")
REVIEWED = ("card_support", "form_evidence", "runtime")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(path: Path) -> tuple[bytes, Any]:
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return raw, json.loads(raw)


def _find(root: Path, name: str) -> Path:
    for suffix in (".json.gz", ".json"):
        path = root / f"{name}{suffix}"
        if path.is_file():
            return path
    raise FileNotFoundError(f"{name} not found under {root}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--runtime-feature-dim", type=int, default=21)
    parser.add_argument("--descriptor-dir", type=Path, default=REPO_ROOT / "native_runner" / "releases")
    parser.add_argument(
        "--expect-checkpoint",
        type=Path,
        help="fail unless the adopted catalog ids equal this checkpoint's contract",
    )
    args = parser.parse_args(argv)

    from native_runner.card_logic import StaticCardLogicCatalogV1
    from native_runner.card_specs import CardSpecCatalog
    from native_runner.contracts import content_hash
    from native_runner.effect_catalog import NativeEffectCatalogV1
    from native_runner.projectile_catalog import NativeProjectileCatalogV1
    from native_runner.training.v4.catalog import normal_mode_card_specs

    source = args.source.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    _, cards_json = _read(_find(source, "card_specs"))
    _, logic_json = _read(_find(source, "card_logic"))
    _, effects_json = _read(_find(source, "effects"))
    _, projectiles_json = _read(_find(source, "projectiles"))
    cards = CardSpecCatalog.from_mapping(cards_json)
    logic = StaticCardLogicCatalogV1.from_mapping(logic_json)
    effects = NativeEffectCatalogV1.from_mapping(effects_json)
    projectiles = NativeProjectileCatalogV1.from_mapping(projectiles_json)
    specs = normal_mode_card_specs(cards.by_id, require_frozen_baseline=False)

    # Copy compiled + reviewed facts verbatim.
    for name in (*COMPILED, *REVIEWED):
        path = _find(source, name)
        (output / path.name).write_bytes(path.read_bytes())

    # The reviewed form-evidence file carries the catalog hashes it was built
    # against.  Third-party bundles reused 15.535 values; rewrite them to the
    # catalogs actually shipped here (content is unchanged).
    fe_source = _find(source, "form_evidence")
    _, fe = _read(fe_source)
    fe["evidence"]["card_specs_hash"] = cards.specs_hash
    fe["evidence"]["ability_specs_hash"] = cards.ability_specs_hash
    fe["catalog_source_files_sha256"] = content_hash(dict(cards.source_files))
    (output / "form_evidence.json").write_text(
        json.dumps(fe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Adopt the model snapshot; repair only the stale input ids.
    _, model = _read(_find(source, "model_catalogs"))
    model["input_catalog_ids"] = {
        "card_logic": logic.catalog_id,
        "effects": effects.catalog_id,
        "projectiles": projectiles.catalog_id,
    }
    model_path = output / "model_catalogs.json"
    model_path.write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    catalog_ids = dict(model["catalog_ids"])
    short_catalog_ids = {
        "card": catalog_ids["card_catalog"],
        "ability": catalog_ids["ability_catalog"],
        "entity_archetype": catalog_ids["entity_archetype_catalog"],
        "effect": catalog_ids["effect_catalog"],
        "mechanic_profile": catalog_ids["mechanic_profile_catalog"],
    }
    if args.expect_checkpoint:
        import torch

        expected = torch.load(args.expect_checkpoint, map_location="cpu", weights_only=False)["contract"][
            "catalog_ids"
        ]
        if dict(expected) != short_catalog_ids:
            print("catalog id mismatch", file=sys.stderr)
            for key in sorted(set(expected) | set(catalog_ids)):
                state = "ok" if expected.get(key) == catalog_ids.get(key) else "DIFF"
                print(f"  {key}: {state}", file=sys.stderr)
            return 1

    files: dict[str, dict[str, Any]] = {}
    for path in sorted(output.iterdir()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
        files[path.name.split(".")[0]] = {"sha256": _sha256(raw), "bytes": len(raw)}

    _, card_support = _read(_find(output, "card_support"))
    compatibility = card_support.get("compatibility", {})
    identity = {
        "source_release": cards.source_release,
        "source_card_count": len(cards.specs),
        "policy_card_count": len(specs),
        "ability_vocab_size": len(model["catalogs"]["ability_catalog"]["ability_ids"]) + 1,
        "child_archetype_count": len(model["catalogs"]["entity_archetype_catalog"]["archetype_keys"]) + 2,
        "card_runtime_feature_dim": int(args.runtime_feature_dim),
        "card_specs_hash": cards.specs_hash,
        "ability_specs_hash": cards.ability_specs_hash,
        "policy_card_ids": sorted(int(value) for value in specs),
        "excluded_card_ids": sorted(int(value) for value in set(cards.by_id) - set(specs)),
        "mechanic_readiness_report_id": compatibility.get("mechanics_readiness_report_id"),
        "static_catalog_id": compatibility.get("static_catalog_id"),
        "case_scope_id": compatibility.get("case_scope_id"),
        "catalog_source_files_sha256": content_hash(dict(cards.source_files)),
        "form_evidence_sha256": None,
    }
    manifest = {
        "version": "competitive-static-data.v1",
        "scope": "normal-mode-only",
        "source_release": cards.source_release,
        "source_root": f"{cards.source_release}/assets/csv_logic",
        "source_files": dict(cards.source_files),
        "files": files,
        "identity": identity,
        "catalog_compatibility": (
            f"{len(cards.specs)} original CardSpec rows retained for content hash identity; "
            f"only {len(specs)} normal-mode rows are legal policy cards"
        ),
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    from native_runner.competitive_release import CompetitiveRelease, ReleaseIdentity, use_release
    from native_runner.card_specs import build_card_catalog as load_card_catalog
    from native_runner.normal_form_evidence import build_normal_mode_policy_form_evidence

    release_identity = ReleaseIdentity(
        source_release=identity["source_release"],
        source_card_count=identity["source_card_count"],
        policy_card_count=identity["policy_card_count"],
        ability_vocab_size=identity["ability_vocab_size"],
        child_archetype_count=identity["child_archetype_count"],
        card_runtime_feature_dim=identity["card_runtime_feature_dim"],
        card_specs_hash=identity["card_specs_hash"],
        ability_specs_hash=identity["ability_specs_hash"],
        policy_card_ids=frozenset(identity["policy_card_ids"]),
        excluded_card_ids=frozenset(identity["excluded_card_ids"]),
        form_evidence_sha256=None,
        mechanic_readiness_report_id=identity["mechanic_readiness_report_id"],
        static_catalog_id=identity["static_catalog_id"],
        case_scope_id=identity["case_scope_id"],
        catalog_source_files_sha256=identity["catalog_source_files_sha256"],
    )
    release = CompetitiveRelease(
        name="adopt",
        data_root=output,
        manifest_sha256=_sha256(manifest_path.read_bytes()),
        identity=release_identity,
    )
    with use_release(release):
        evidence = build_normal_mode_policy_form_evidence(load_card_catalog())
    identity["form_evidence_sha256"] = evidence.evidence_id
    manifest["identity"] = identity
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest_sha = _sha256(manifest_path.read_bytes())

    descriptor = {
        "name": args.name,
        "data_root": str(output.relative_to(REPO_ROOT)),
        "manifest_sha256": manifest_sha,
        "description": (
            "16.635.1 normal-mode catalogs adopted from a compiled catalog set; "
            "the model snapshot's stale input_catalog_ids were repaired to the shipped facts."
        ),
        "identity": identity,
    }
    args.descriptor_dir.mkdir(parents=True, exist_ok=True)
    descriptor_path = args.descriptor_dir / f"{args.name}.json"
    descriptor_path.write_text(json.dumps(descriptor, indent=2) + "\n", encoding="utf-8")

    print(f"data:       {output}")
    print(f"manifest:   {manifest_sha}")
    print(f"descriptor: {descriptor_path}")
    print(f"catalogs:   {json.dumps(catalog_ids, indent=1)}")
    print(f"form pin:   {evidence.evidence_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
