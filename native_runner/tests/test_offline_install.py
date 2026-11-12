"""Installation must retain resource data across signing changes and failures."""

import json
import zipfile

import pytest

from native_runner import offline_install as install


class Device:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def shell(self, command):
        self.calls.append(command)
        if command.startswith("dumpsys package"):
            return "versionName=15.535.13\nUser 0: installed=true"
        if command.startswith("pm path"):
            return "package:/data/app/test/base.apk"
        if command.startswith("sha256sum"):
            return "a" * 64 + "  apk"
        if command.startswith("if [ -d"):
            return "present"
        if command.startswith("stat -c %u"):
            return "10099"
        if command.startswith("pm uninstall"):
            return "Success"
        return ""

    def run(self, *args):
        self.calls.append(args)
        return ""

    def install_apk(self, path):
        self.calls.append("install " + path)
        return next(self.results)


@pytest.fixture
def setup_install(monkeypatch, tmp_path):
    hashes = {"fingerprint.json": "b" * 64, "assets.scdb": "c" * 64}
    monkeypatch.setattr(install, "check_apk", lambda apk: None)
    monkeypatch.setattr(install, "sha256", lambda apk: "a" * 64)
    monkeypatch.setattr(install, "resource_hashes", lambda *args: hashes.copy())
    monkeypatch.setattr(
        install, "validate_resources", lambda *args: {"content_mode": "pinned", "content_version": "15.535.86"}
    )
    return tmp_path / "local.apk", tmp_path / "install.json", hashes


def test_signature_change_restores_device_data_with_new_uid(setup_install):
    apk, receipt, hashes = setup_install
    device = Device(["Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]", "Success"])
    result = install.install(device, apk, receipt)
    calls = device.calls
    uninstall = calls.index("pm uninstall " + install.APP)
    assert any(isinstance(call, tuple) and call[-1].endswith("/state.json") for call in calls[:uninstall])
    assert any(isinstance(call, str) and call.startswith("cp -a ") for call in calls[:uninstall])
    assert any(isinstance(call, str) and "chown -R 10099:10099" in call for call in calls[uninstall:])
    assert all(not isinstance(call, tuple) or call[0] != "pull" for call in calls)
    assert json.loads(receipt.read_text())["resource_hashes"] == hashes
    assert result["installed"]


def test_same_signer_upgrade_does_not_uninstall(setup_install):
    apk, receipt, _ = setup_install
    device = Device(["Success"])
    install.install(device, apk, receipt)
    assert not any(isinstance(c, str) and c.startswith("pm uninstall") for c in device.calls)
    assert not any(isinstance(c, str) and "chown -R" in c for c in device.calls)


def test_unrelated_install_failure_does_not_uninstall(setup_install):
    apk, receipt, _ = setup_install
    device = Device(["Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]"])
    with pytest.raises(RuntimeError, match="INSUFFICIENT_STORAGE"):
        install.install(device, apk, receipt)
    assert "pm uninstall " + install.APP not in device.calls


def test_failure_after_signature_replacement_restores_previous_install(setup_install, monkeypatch):
    apk, receipt, _ = setup_install
    device = Device(["Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]", "Failure [INSTALL_FAILED_INTERNAL_ERROR]"])
    restored = []
    monkeypatch.setattr(install, "restore", lambda device, backup: restored.append(backup))
    with pytest.raises(RuntimeError, match="INTERNAL_ERROR"):
        install.install(device, apk, receipt)
    assert restored == [json.loads(receipt.read_text())["backup"]]


def test_changed_resources_trigger_rollback(setup_install, monkeypatch):
    apk, receipt, hashes = setup_install
    reads = iter([hashes, hashes, {"fingerprint.json": "changed"}])
    monkeypatch.setattr(install, "resource_hashes", lambda *args: next(reads))
    restored = []
    monkeypatch.setattr(install, "restore", lambda device, backup: restored.append(backup))
    with pytest.raises(ValueError, match="resources differ"):
        install.install(Device(["Success"]), apk, receipt)
    assert len(restored) == 1


def test_incomplete_original_update_stops_before_device_changes(setup_install, monkeypatch):
    apk, receipt, _ = setup_install
    def incomplete(*args):
        raise ValueError("incomplete update")
    monkeypatch.setattr(install, "validate_resources", incomplete)
    device = Device([])
    with pytest.raises(ValueError, match="incomplete"):
        install.install(device, apk, receipt)
    assert not receipt.exists()
    assert not any(isinstance(c, str) and c.startswith(("am force-stop", "cp ", "mkdir", "pm uninstall", "install ")) for c in device.calls)


def test_restore_rejects_arbitrary_device_path():
    with pytest.raises(ValueError, match="Invalid Firstlight backup"):
        install.restore(Device([]), "/data/user/0")


def test_check_original_game_reads_apk_and_resources_without_replacing_app(monkeypatch, tmp_path):
    release = install.supported_engine()
    apk = tmp_path / "original.apk"
    monkeypatch.setattr(install, "apk_matches_release", lambda path: True)
    monkeypatch.setattr(install, "sha256", lambda path: release["apk_sha256"])
    monkeypatch.setattr(install, "resource_hashes", lambda device: {"fingerprint.json": "verified"})
    monkeypatch.setattr(
        install, "validate_resources", lambda *args: {"content_mode": "pinned", "content_version": "15.535.86"}
    )

    class OriginalDevice(Device):
        def shell(self, command):
            if command.startswith("sha256sum "):
                self.calls.append(command)
                return release["apk_sha256"] + "  base.apk"
            return super().shell(command)

    device = OriginalDevice([])
    result = install.check_original_game(device, apk)
    assert result["original_apk_verified"] is True
    assert result["resource_files_verified"] == 1
    assert not any(isinstance(c, str) and c.startswith(("am force-stop", "cp ", "mkdir", "pm uninstall", "install ")) for c in device.calls)


def test_check_original_game_rejects_wrong_apk_before_device_access(monkeypatch, tmp_path):
    monkeypatch.setattr(install, "sha256", lambda path: "0" * 64)
    device = Device([])
    with pytest.raises(ValueError, match="does not carry the supported engine"):
        install.check_original_game(device, tmp_path / "wrong.apk")
    assert device.calls == []


class FingerprintDevice:
    """Device stub that answers the update/fingerprint.json read."""

    def __init__(self, manifest):
        self.manifest = manifest
        self.calls = []

    def shell(self, command):
        self.calls.append(command)
        assert command.startswith("cat ")
        return json.dumps(self.manifest)


def _pinned_files():
    manifest = json.loads((install.PACKAGE / "data/competitive/manifest.json").read_text(encoding="utf-8"))
    return {
        name.removeprefix("runtime-update/"): digest
        for name, digest in manifest["source_files"].items()
        if name.startswith("runtime-update/")
    }


def test_baseline_content_reads_bundled_fingerprint(tmp_path):
    apk = tmp_path / "client.apk"
    payload = json.dumps({"version": "15.535.3", "sha": "d" * 40})
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/fingerprint.json", payload)
    assert install.baseline_content(apk) == {"version": "15.535.3", "sha": "d" * 40}


def test_baseline_content_requires_a_bundled_fingerprint(tmp_path):
    apk = tmp_path / "client.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"")
    assert install.baseline_content(apk) is None


def test_pinned_update_is_still_verified_strictly():
    release = install.supported_engine()
    pinned = _pinned_files()
    hashes = {**pinned, "fingerprint.json": release["runtime_fingerprint_sha256"]}
    device = FingerprintDevice({})
    content = install.validate_resources(device, hashes, None)
    assert content == {"content_mode": "pinned", "content_version": release["runtime_content_version"]}
    assert device.calls == []
    broken = {**hashes, next(iter(pinned)): "0" * 64}
    with pytest.raises(ValueError, match="incomplete or unsupported"):
        install.validate_resources(device, broken, None)


def test_base_content_is_accepted_when_it_matches_the_supplied_apk():
    baseline = {"version": "15.535.3", "sha": "d" * 40}
    content = install.validate_resources(FingerprintDevice(baseline), {"fingerprint.json": "e" * 64}, baseline)
    assert content == {"content_mode": "apk-base", "content_version": "15.535.3"}


def test_base_content_rejects_a_pinned_file_that_does_not_match():
    baseline = {"version": "15.535.3", "sha": "d" * 40}
    hashes = {"fingerprint.json": "e" * 64, next(iter(_pinned_files())): "0" * 64}
    with pytest.raises(ValueError, match="incomplete or unsupported"):
        install.validate_resources(FingerprintDevice(baseline), hashes, baseline)


def test_empty_update_folder_still_requires_the_first_open():
    with pytest.raises(ValueError, match="First open the unmodified game"):
        install.validate_resources(FingerprintDevice({}), {}, {"version": "15.535.3", "sha": "d" * 40})


def test_content_from_an_unknown_client_is_rejected():
    device = FingerprintDevice({"version": "16.635.1", "sha": "d" * 40})
    with pytest.raises(ValueError, match="neither the pinned"):
        install.validate_resources(device, {"fingerprint.json": "e" * 64}, {"version": "15.535.3", "sha": "d" * 40})
