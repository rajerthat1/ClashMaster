"""Tests for the runtime-update verifier used before restoring 15.535.86 content."""

import hashlib
import importlib.util
from pathlib import Path
import zipfile

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools/check_runtime_update.py"
SPEC = importlib.util.spec_from_file_location("check_runtime_update", SCRIPT)
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_verify_accepts_complete_matching_update(tmp_path):
    payload = b"card-table"
    (tmp_path / "csv_logic").mkdir()
    (tmp_path / "csv_logic/characters.toml").write_bytes(payload)
    (tmp_path / "fingerprint.json").write_bytes(b"fingerprint")
    report = check.verify(
        tmp_path,
        pinned={"csv_logic/characters.toml": digest(payload)},
        fingerprint=digest(b"fingerprint"),
    )
    assert report == {
        "ok": True,
        "fingerprint_ok": True,
        "pinned_files": 1,
        "missing": [],
        "mismatched": [],
    }


def test_verify_reports_missing_and_mismatched_files(tmp_path):
    (tmp_path / "fingerprint.json").write_bytes(b"fingerprint")
    (tmp_path / "present.toml").write_bytes(b"wrong")
    report = check.verify(
        tmp_path,
        pinned={"present.toml": digest(b"expected"), "absent.toml": digest(b"absent")},
        fingerprint=digest(b"fingerprint"),
    )
    assert report["ok"] is False
    assert report["missing"] == ["absent.toml"]
    assert report["mismatched"] == ["present.toml"]


def test_verify_requires_pinned_fingerprint(tmp_path):
    report = check.verify(
        tmp_path,
        pinned={},
        fingerprint=digest(b"fingerprint"),
    )
    assert report["ok"] is False
    assert report["fingerprint_ok"] is False


def test_fill_from_apk_restores_never_downloaded_files(tmp_path):
    payload = b"ghost-evolution"
    expected = digest(payload)
    apk = tmp_path / "input.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/csv_logic/characters/ghost_ev1.toml", payload)
    update = tmp_path / "update"
    update.mkdir()
    pinned = {"csv_logic/characters/ghost_ev1.toml": expected}
    report = check.fill_from_apk(update, apk, pinned=pinned)
    assert report == {"restored": ["csv_logic/characters/ghost_ev1.toml"], "unavailable": []}
    assert (update / "csv_logic/characters/ghost_ev1.toml").read_bytes() == payload


def test_fill_from_apk_refuses_payload_that_differs_from_the_pin(tmp_path):
    apk = tmp_path / "input.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/csv_logic/characters/ghost_ev1.toml", b"different")
    report = check.fill_from_apk(tmp_path, apk, pinned={"csv_logic/characters/ghost_ev1.toml": digest(b"expected")})
    assert report == {"restored": [], "unavailable": ["csv_logic/characters/ghost_ev1.toml"]}
    assert not (tmp_path / "csv_logic").exists()


def test_pinned_files_cover_the_full_runtime_set():
    pinned = check.pinned_files()
    assert len(pinned) == 83
    assert "assets.scdb" in pinned
