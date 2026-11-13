from __future__ import annotations

import json
from pathlib import Path

import pytest

from native_runner.attestation import (
    RunnerAttestationError,
    crforge_attestation_from_response,
    verify_crforge_attestation,
)
from native_runner.contracts import (
    CRFORGE_ATTESTATION_VERSION,
    CrforgeAttestationV1,
    content_hash,
)
from native_runner.ruleset import (
    DEFAULT_CRFORGE_RELEASE,
    RulesetManifestV1,
    build_crforge_ruleset_manifest,
)


def _identity() -> dict[str, object]:
    return {
        "bridge_version": "0.1.0",
        "catalog_sha256": "1" * 64,
        "client_version": "16.635.1",
        "compatible": ["16.635.3", "16.635.8"],
        "content_sha": "2" * 40,
        "data_version": "16.635.8",
        "lock_commit": "3" * 40,
        "protocol_version": "crforge-control.v1",
        "schema": "4" * 64,
        "tables_sha256": "5" * 64,
        "version": CRFORGE_ATTESTATION_VERSION,
        "vocabulary_sha256": "6" * 64,
    }


def _attestation(**overrides: object) -> CrforgeAttestationV1:
    identity = _identity()
    identity.update(overrides)
    ready = identity["data_version"] in identity["compatible"] and all(
        identity[name]
        for name in (
            "bridge_version",
            "catalog_sha256",
            "client_version",
            "content_sha",
            "data_version",
            "lock_commit",
            "schema",
            "tables_sha256",
            "vocabulary_sha256",
        )
    )
    return CrforgeAttestationV1.from_mapping(
        {
            **identity,
            "production_ready": ready,
            "attestation_digest": content_hash(identity),
        }
    )


def _pinned_manifest(attestation: CrforgeAttestationV1 | None = None) -> RulesetManifestV1:
    identity = _attestation() if attestation is None else attestation
    hashes = [character * 64 for character in "abcdef0123456"]
    return RulesetManifestV1(
        ruleset_version="crforge-16.635.1",
        engine_build_hash=hashes[0],
        simulator_build_hash=hashes[1],
        card_schema_hash=hashes[2],
        observation_contract_hash=hashes[3],
        action_contract_hash=hashes[4],
        tick_rng_latency_rules_hash=hashes[5],
        card_specs_hash=hashes[6],
        ability_specs_hash=hashes[7],
        slot_rules_hash=hashes[8],
        legal_action_schema_hash=hashes[9],
        elixir_rules_hash=hashes[10],
        mode_map_hash=hashes[11],
        visual_asset_pack_hash=hashes[12],
        card_spec_count=152,
        contract_versions={"crforge_attestation": CRFORGE_ATTESTATION_VERSION},
        files={"engine": {}, "simulator": {}, "schema": {}, "data": {}, "visual": {}},
        config={
            "engine": "crforge",
            "crforge": {
                "bridge_version": identity.bridge_version,
                "catalog_sha256": identity.catalog_sha256,
                "client_version": identity.client_version,
                "compatible": list(identity.compatible),
                "content_sha": identity.content_sha,
                "data_version": identity.data_version,
                "lock_commit": identity.lock_commit,
                "protocol": identity.protocol_version,
                "schema": identity.schema,
                "tables_sha256": identity.tables_sha256,
                "vocabulary_sha256": identity.vocabulary_sha256,
            },
        },
    )


def test_crforge_attestation_round_trips_the_wire_contract() -> None:
    actual = _attestation()
    raw = {"ok": True, "crforge": actual.to_dict()}
    restored = crforge_attestation_from_response(raw)
    assert restored == actual
    assert restored.production_ready
    assert restored.attestation_digest == actual.calculate_attestation_digest()


def test_crforge_attestation_binds_the_pinned_release() -> None:
    actual = verify_crforge_attestation(_attestation(), _pinned_manifest())
    assert actual.data_version == "16.635.8"


@pytest.mark.parametrize(
    "field,value",
    [
        ("tables_sha256", "7" * 64),
        ("vocabulary_sha256", "8" * 64),
        ("catalog_sha256", "9" * 64),
        ("content_sha", "a" * 40),
        ("lock_commit", "b" * 40),
        ("bridge_version", "9.9.9"),
        ("data_version", "16.635.3"),
    ],
)
def test_crforge_attestation_rejects_any_release_mismatch(field: str, value: object) -> None:
    with pytest.raises(RunnerAttestationError, match=field):
        verify_crforge_attestation(_attestation(**{field: value}), _pinned_manifest())


def test_crforge_attestation_requires_a_crforge_manifest() -> None:
    manifest = _pinned_manifest()
    object.__setattr__(manifest, "config", {})  # type: ignore[misc]
    with pytest.raises(RunnerAttestationError, match="not a crforge manifest"):
        verify_crforge_attestation(_attestation(), manifest)


def _tables_available() -> bool:
    data = json.loads(Path(DEFAULT_CRFORGE_RELEASE).read_text(encoding="utf-8"))
    version = data["default_data_version"]
    return (Path.home() / ".crforge" / "tables" / version / "build.properties").is_file()


@pytest.mark.skipif(
    not Path(DEFAULT_CRFORGE_RELEASE).is_file() or not _tables_available(),
    reason="crforge release pin or generated tables are not installed",
)
def test_generated_manifest_pins_the_installed_tables() -> None:
    manifest = build_crforge_ruleset_manifest()
    assert manifest.config["engine"] == "crforge"
    assert manifest.config["crforge"]["data_version"] == "16.635.8"
    assert manifest.config["runtime_content_version"] == "16.635.8"
    assert manifest.verify_files(".") == ()
    assert "native_runner/data/native_match.json" in manifest.files["data"]
    assert "native_runner/data/competitive/crforge_catalog.json" in manifest.files["data"]
