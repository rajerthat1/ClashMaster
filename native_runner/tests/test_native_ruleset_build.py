from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from native_runner.attestation import verify_runner_attestation
from native_runner.contracts import ContractError, RunnerAttestationV1, content_hash
from native_runner.ruleset import (
    build_native_ruleset_manifest,
    load_native_build_profile,
    native_build_release_path,
)


CLIENT = "16.635.1"


def _attestation_from_profile(profile) -> RunnerAttestationV1:
    hooks = {
        "game_state_load": True,
        "game_state_step": True,
        "native_replay_runner": True,
        "render_gate": True,
        "replay_json_setter": True,
    }
    capabilities = {
        "headless": True,
        "native_render": True,
        "snapshot_restore": True,
        "structured_observation": True,
        "two_sided_actions": True,
    }
    identity = {
        "abi": "arm64-v8a",
        "capabilities": capabilities,
        "content_assets_sha256": profile["content_assets_sha256"],
        "content_fingerprint_sha256": profile["content_fingerprint_sha256"],
        "content_manifest_sha1": profile["content_manifest_sha1"],
        "content_version": profile["content_version"],
        "hook_install_ok": True,
        "hooks": hooks,
        "libg_build_id": profile["libg_build_id"],
        "libg_sha256": profile["libg_sha256"],
        "package_name": "nullsroyale.rel.free",
        "probe_sha256": profile["probe_sha256"],
        "protocol_version": "cr-native-control.v1",
    }
    digest = content_hash({**identity, "version": RunnerAttestationV1.VERSION})
    return RunnerAttestationV1(
        **identity,
        content_ready=True,
        content_assets_mapped=True,
        production_ready=True,
        attestation_digest=digest,
    )


class NativeBuildPinTests(unittest.TestCase):
    def test_unknown_client_has_no_profile(self) -> None:
        self.assertIsNone(load_native_build_profile("99.999.1"))

    def test_abi_specific_pin_is_separate_from_the_device_pin(self) -> None:
        self.assertEqual(native_build_release_path(CLIENT).name, f"native-{CLIENT}.json")
        self.assertEqual(native_build_release_path(CLIENT, "x86_64").name, f"native-{CLIENT}-x86_64.json")
        arm = load_native_build_profile(CLIENT)
        x86 = load_native_build_profile(CLIENT, abi="x86_64")
        assert arm is not None and x86 is not None
        self.assertEqual((arm["abi"], arm["package"]), ("arm64-v8a", "nullsroyale.rel.free"))
        self.assertEqual((x86["abi"], x86["package"]), ("x86_64", "com.supercell.clashroyale"))
        self.assertNotEqual(arm["libg_sha256"], x86["libg_sha256"])
        with self.assertRaises(ContractError):
            load_native_build_profile(CLIENT, abi="x86_64", release_path=native_build_release_path(CLIENT))

    def test_manifest_binds_device_identity_and_attestation(self) -> None:
        profile = load_native_build_profile(CLIENT)
        assert profile is not None
        manifest = build_native_ruleset_manifest(client_version=CLIENT)

        self.assertEqual(manifest.ruleset_version, profile["content_version"])
        self.assertEqual(manifest.config["runtime_content_version"], profile["content_version"])
        self.assertEqual(manifest.config["runtime_libg_build_id"], profile["libg_build_id"])
        self.assertEqual(manifest.config["native_build"]["client_version"], CLIENT)
        self.assertFalse(manifest.config["native_build"]["combat_ring_hooks"])
        self.assertEqual(list(manifest.files["engine"].values()), [profile["libg_sha256"]])
        self.assertEqual(
            [digest for name, digest in manifest.files["data"].items() if name.endswith("fingerprint.json")],
            [profile["content_fingerprint_sha256"]],
        )
        self.assertEqual(manifest.verify_files("."), ())
        verified = verify_runner_attestation(_attestation_from_profile(profile), manifest)
        self.assertEqual(verified.content_version, profile["content_version"])

    def test_probe_mismatch_fails_closed(self) -> None:
        profile = json.loads(native_build_release_path(CLIENT).read_text(encoding="utf-8"))
        profile["probe_sha256"] = "a" * 64
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"native-{CLIENT}.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            with self.assertRaises(ContractError):
                build_native_ruleset_manifest(client_version=CLIENT, release_path=path)


if __name__ == "__main__":
    unittest.main()
