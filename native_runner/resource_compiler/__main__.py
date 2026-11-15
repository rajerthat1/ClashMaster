"""Reproduce the pinned runtime datasets from locally supplied resources.

Verification mode (default) regenerates the compiled facts and requires each
one to match the supported release manifest byte-for-byte.  ``--emit-release``
bootstraps a **new** release from a source tree instead: it writes the compiled
facts without a comparison and emits a manifest whose SHA-256 values describe
exactly those files.  The emitted release is then verified like any other, so a
new catalog line never requires disabling integrity checks.
"""

import argparse
from dataclasses import fields, is_dataclass
from collections.abc import Mapping
from enum import Enum
import hashlib
import json
import os
from pathlib import Path


def normalize(value):
    if is_dataclass(value):
        return {item.name: normalize(getattr(value, item.name)) for item in fields(value) if item.init}
    if isinstance(value, Mapping):
        return {str(k): normalize(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [normalize(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _source_file_hashes(source_root: Path, workspace: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(source_root.rglob("*")):
        if path.is_file():
            hashes[str(path.relative_to(workspace))] = _sha256(path.read_bytes())
    return hashes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--emit-release",
        action="store_true",
        help="bootstrap a new release: write the compiled facts and a manifest that describes them",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        help="csv_logic directory for --emit-release (defaults to workspace discovery)",
    )
    args = parser.parse_args()
    os.environ["CR_WORKSPACE_ROOT"] = str(args.workspace.resolve())
    os.environ["CR_COMPETITIVE_DATA_ROOT"] = str(args.output.resolve())

    from native_runner.contracts import content_hash
    from .card_specs import build_card_catalog, discover_card_source
    from .card_logic import build_static_card_logic_catalog
    from .effect_catalog import build_effect_catalog
    from .projectile_catalog import build_projectile_catalog

    emitted: dict[str, dict[str, object]] = {}
    if args.emit_release:
        manifest = None
    else:
        from native_runner.competitive_data import validate_competitive_sources

        manifest = validate_competitive_sources(args.workspace)
    args.output.mkdir(parents=True, exist_ok=True)

    def write(name, value, *, contract=False):
        # Contract JSON uses sorted keys; model snapshots retain schema field order.
        raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=contract) + "\n").encode("utf-8")
        digest = _sha256(raw)
        if manifest is not None and digest != manifest["files"][name]["sha256"]:
            raise ValueError(f"Generated {name} differs from the supported release: {digest}")
        destination = args.output / f"{name}.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_bytes(raw)
        temporary.replace(destination)
        emitted[name] = {"sha256": digest, "bytes": len(raw)}
        print(f"{'Emitted' if manifest is None else 'Verified'} {name}: {digest}", flush=True)

    cards = build_card_catalog(args.workspace)
    logic = build_static_card_logic_catalog(args.workspace, card_catalog=cards)
    effects = build_effect_catalog(logic, args.workspace)
    projectiles = build_projectile_catalog(logic, args.workspace)
    for name, value in (
        ("card_specs", cards),
        ("card_logic", logic),
        ("effects", effects),
        ("projectiles", projectiles),
    ):
        write(name, value.to_dict(), contract=True)

    # Runtime form compatibility initialization can now read the four base files.
    from .catalog import CardCatalogV1, AbilityCatalogV1, EntityArchetypeCatalogV1, normal_mode_card_specs
    from .mechanics import EffectSemanticCatalogV1, MechanicProfileCatalogV1
    from .card_features import build_card_feature_rows

    specs = normal_mode_card_specs(cards.by_id, require_frozen_baseline=manifest is not None)
    card_catalog = CardCatalogV1.from_card_spec_catalog(
        cards, static_logic=logic, require_frozen_baseline=manifest is not None
    )
    abilities = AbilityCatalogV1.from_card_spec_catalog(cards, require_frozen_baseline=manifest is not None)
    entities = EntityArchetypeCatalogV1.from_card_specs(
        specs, static_logic=logic, projectile_catalog=projectiles
    )
    semantic_effects = EffectSemanticCatalogV1.from_native_catalog(
        specs, static_logic=logic, native_effect_catalog=effects
    )
    mechanics = MechanicProfileCatalogV1.from_sources(
        specs,
        card_catalog=card_catalog,
        ability_catalog=abilities,
        entity_archetype_catalog=entities,
        effect_catalog=semantic_effects,
        static_logic=logic,
        native_effect_catalog=effects,
        projectile_catalog=projectiles,
    )
    catalogs = dict(
        card_catalog=card_catalog,
        ability_catalog=abilities,
        entity_archetype_catalog=entities,
        effect_catalog=semantic_effects,
        mechanic_profile_catalog=mechanics,
    )
    input_catalog_ids = {
        k: v.catalog_id for k, v in (("card_logic", logic), ("effects", effects), ("projectiles", projectiles))
    } if manifest is None else {k: manifest["files"][k]["content_hash"] for k in ("card_logic", "effects", "projectiles")}
    model = {
        "schema": "competitive-model-catalogs.v1",
        "source_release": cards.source_release,
        "card_spec_hashes": {str(k): content_hash(v.to_dict()) for k, v in specs.items()},
        "ability_spec_hashes": {
            v.ability_id: content_hash(v.to_dict()) for v in cards.abilities if v.ability_id in abilities.ability_ids
        },
        "card_feature_rows": normalize(
            build_card_feature_rows(specs, static_logic=logic, ability_specs=cards.abilities)
        ),
        "catalogs": {k: normalize(v) for k, v in catalogs.items()},
        "catalog_ids": {k: v.catalog_id for k, v in catalogs.items()},
        "input_catalog_ids": input_catalog_ids,
        "description": "Exact level-11 feature rows, ordered vocabulary facts, and descriptive mechanic operations for the frozen competitive V4 checkpoint contract. Conditions and paths are categorical feature labels; the loader never executes them.",
    }
    write("model_catalogs", model)

    if manifest is not None:
        return

    # --- bootstrap a new release -------------------------------------------
    compatibility: dict[str, object] = {}
    card_support_path = args.output / "card_support.json"
    if card_support_path.is_file():
        compatibility = json.loads(card_support_path.read_text(encoding="utf-8")).get("compatibility", {})
    source_root = args.source_root or discover_card_source(args.workspace)
    source_release = cards.source_release
    identity = {
        "source_release": source_release,
        "source_card_count": len(cards.specs),
        "policy_card_count": len(specs),
        "ability_vocab_size": abilities.vocab_size,
        "child_archetype_count": entities.vocab_size,
        "card_runtime_feature_dim": 21 if source_release.startswith("nr_16.635") else 16,
        "card_specs_hash": cards.specs_hash,
        "ability_specs_hash": cards.ability_specs_hash,
        "policy_card_ids": sorted(int(card_id) for card_id in specs),
        "excluded_card_ids": sorted(int(card_id) for card_id in set(cards.by_id) - set(specs)),
        "mechanic_readiness_report_id": compatibility.get("mechanics_readiness_report_id"),
        "static_catalog_id": compatibility.get("static_catalog_id"),
        "case_scope_id": compatibility.get("case_scope_id"),
        "form_evidence_sha256": None,
    }
    files = {
        name: {"sha256": meta["sha256"], "bytes": meta["bytes"]} for name, meta in sorted(emitted.items())
    }
    for name, value in (
        ("card_specs", cards),
        ("card_logic", logic),
        ("effects", effects),
        ("projectiles", projectiles),
    ):
        files[name]["content_hash"] = content_hash(value.to_dict())
    for name in ("card_support", "form_evidence", "runtime"):
        path = args.output / f"{name}.json"
        gz = args.output / f"{name}.json.gz"
        if path.is_file():
            raw = path.read_bytes()
            files[name] = {"sha256": _sha256(raw), "bytes": len(raw)}
        elif gz.is_file():
            import gzip

            raw = gzip.decompress(gz.read_bytes())
            files[name] = {"sha256": _sha256(raw), "bytes": len(raw)}
    manifest_out = {
        "version": "competitive-static-data.v1",
        "scope": "normal-mode-only",
        "source_release": source_release,
        "source_root": str(source_root.relative_to(args.workspace)),
        "source_files": _source_file_hashes(source_root, args.workspace),
        "files": files,
        "identity": identity,
        "catalog_compatibility": (
            f"{len(cards.specs)} original CardSpec rows retained for content hash identity; "
            f"only {len(specs)} normal-mode rows are legal policy cards"
        ),
    }
    destination = args.output / "manifest.json"
    destination.write_text(json.dumps(manifest_out, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"Emitted release manifest: {_sha256(destination.read_bytes())}", flush=True)

    # Pin the reviewed form evidence under the emitted identity.  This must run
    # *after* the manifest exists so the release context can verify it.
    from native_runner.competitive_release import CompetitiveRelease, ReleaseIdentity, use_release

    identity_obj = ReleaseIdentity(
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
    )
    release = CompetitiveRelease(
        name="emit",
        data_root=args.output.resolve(),
        manifest_sha256=_sha256(destination.read_bytes()),
        identity=identity_obj,
    )
    from native_runner.card_specs import build_card_catalog as load_card_catalog
    from native_runner.normal_form_evidence import build_normal_mode_policy_form_evidence

    with use_release(release):
        evidence = build_normal_mode_policy_form_evidence(load_card_catalog())
    identity["form_evidence_sha256"] = evidence.evidence_id
    manifest_out["identity"] = identity
    destination.write_text(json.dumps(manifest_out, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"Form evidence pin: {evidence.evidence_id}", flush=True)
    print(f"Final release manifest: {_sha256(destination.read_bytes())}", flush=True)


if __name__ == "__main__":
    main()
