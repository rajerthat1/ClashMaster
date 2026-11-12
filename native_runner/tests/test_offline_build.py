"""Source-only build boundary tests; no third-party APK or resources required."""

import json
import hashlib
import zipfile

import pytest

from native_runner.offline_build import patch, prepare, relative_file, verify, sha256
from native_runner.match_factory import MatchConfig


def test_pristine_bootstrap_skips_online_gate_and_keeps_render_init(tmp_path):
    game = tmp_path / "smali/com/supercell/clashroyale/GameApp.smali"
    titan = tmp_path / "smali/com/supercell/titan/GameApp.smali"
    game.parent.mkdir(parents=True)
    titan.parent.mkdir(parents=True)
    game.write_text(
        ".method public final a()Z\n    .locals 3\n\n    .line 1\n    new-instance v0, Lk0/a;\n.end method\n"
    )
    titan.write_text(
        '# direct methods\n    const-string v3, "g"\n    .line 304\n    invoke-static {v3}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n'
    )
    patch(tmp_path)
    assert game.read_text().index("return v0") < game.read_text().index("new-instance v0, Lk0/a;")
    assert "new-instance v0, Lk0/a;" in game.read_text()
    text = titan.read_text()
    assert text.index('"scid_sdk"') < text.index('"g"')
    assert (
        text.index('"g"')
        < text.index('"crprobe"')
        < text.index("invoke-static {}, Lcom/supercell/titan/GameApp;->nativeBootstrapProbe()V")
    )
    with pytest.raises(ValueError, match="already patched"):
        patch(tmp_path)


def test_output_reuses_device_resources_and_rejects_bundled_update(tmp_path, monkeypatch):
    import native_runner.offline_build as build

    apk = tmp_path / "local.apk"
    probe = tmp_path / "probe.so"
    probe.write_bytes(b"probe")
    engine = tmp_path / "engine.so"
    engine.write_bytes(b"engine")
    monkeypatch.setattr(build, "supported_engine", lambda: {"libg_sha256": sha256(engine)})
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("lib/arm64-v8a/libg.so", b"engine")
        archive.writestr("lib/arm64-v8a/libcrprobe.so", b"probe")
    verify(apk, probe)
    assert json.loads(apk.with_suffix(".build.json").read_text())["resources"] == "existing-device-update"
    with zipfile.ZipFile(apk, "a") as archive:
        archive.writestr("assets/firstlight/runtime-update/fingerprint.json", b"update")
    with pytest.raises(ValueError, match="must reuse device resources"):
        verify(apk, probe)


def test_wrong_apk_rejected_before_creating_output(tmp_path):
    apk = tmp_path / "wrong.apk"
    apk.write_bytes(b"unsupported")
    with pytest.raises(ValueError, match="does not carry the supported engine"):
        prepare(apk, None, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_input_apk_accepts_resigned_copy_with_pinned_engine(tmp_path, monkeypatch):
    import native_runner.offline_build as build

    apk = tmp_path / "resigned.apk"
    engine = b"engine-payload"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("lib/arm64-v8a/libg.so", engine)
    monkeypatch.setattr(
        build,
        "supported_engine",
        lambda: {"apk_sha256": "0" * 64, "libg_sha256": hashlib.sha256(engine).hexdigest()},
    )
    assert build.apk_matches_release(apk)


def test_input_apk_rejects_different_engine_payload(tmp_path, monkeypatch):
    import native_runner.offline_build as build

    apk = tmp_path / "wrong_engine.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("lib/arm64-v8a/libg.so", b"other-engine")
    monkeypatch.setattr(
        build,
        "supported_engine",
        lambda: {"apk_sha256": "0" * 64, "libg_sha256": hashlib.sha256(b"engine-payload").hexdigest()},
    )
    assert not build.apk_matches_release(apk)


def test_input_apk_accepts_pinned_whole_file_hash(tmp_path, monkeypatch):
    import native_runner.offline_build as build

    apk = tmp_path / "pinned.apk"
    apk.write_bytes(b"not-a-zip-required-when-the-whole-file-hash-matches")
    monkeypatch.setattr(
        build,
        "supported_engine",
        lambda: {"apk_sha256": build.sha256(apk), "libg_sha256": "0" * 64},
    )
    assert build.apk_matches_release(apk)


@pytest.mark.parametrize("path", ["../outside", "/outside", "C:/outside", "assets/../../outside", "assets\\outside"])
def test_archive_paths_stay_relative(path):
    with pytest.raises(ValueError, match="Invalid resource path"):
        relative_file(path)


def test_synthetic_match_has_no_capture_or_real_account_dependency():
    match = MatchConfig(seed=17, king_tower_level=11)
    replay = json.loads(match.to_json())
    assert replay["cmd"] == replay["evt"] == []
    assert replay["rndSeed"] == 17
    for owner in (0, 1):
        assert replay["battle"][f"avatar{owner}"]["accountID.lo"] == owner + 1
        assert replay["battle"]["hbd"][owner]["kt"] == 11
        assert len(replay["battle"][f"deck{owner}"]["sp"]) == 8
