#!/usr/bin/env python3
"""Build a startable *local overlay* of a published cr-native-linux-bionic bundle.

The published ``runtime-150535029`` bundle cannot cold-start (see
``docs/native/PHASE0_BRINGUP.md``).  This tool patches a copy of that bundle
under ``builds/`` and rewrites the manifest so ``verify-bundle``/``install``
accept the patched archives:

* executable bits on ``linker64`` and ``dalvikvm64``;
* ``libandroid_runtime.so`` in ``system/etc/public.libraries.txt``;
* the rebuilt host JAR and bridge (the bridge must contain the headless
  UI-locale guard fix - see ``android_probe/native/jni_bridge.cpp``);
* the worker asset files the direct runtime needs:
  ``assets/assets.scdb`` and ``data/update/data_manifest.toml``.

The 886 MB ``asset-pack.apk`` is *not* required by a direct worker and is not
added (verified 2026-11-13: the worker cold-starts without it).

Output is a local artifact, not an upstream release: keep it under
``builds/`` and label it "local overlay".

    python3 tools/native_bringup/build_local_overlay.py \
        --source-bundle builds/bundles/cr-native-linux-bionic-runtime-150535029 \
        --sandbox-runtime-zip builds/bundles/cr-native-sandbox-runtime-150535029.zip \
        --bridge builds/local-overlay/artifacts/libnative_host_bridge.so \
        --probe-jar builds/local-overlay/artifacts/lifecycle-probe.jar
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = (
    PROJECT_ROOT
    / ".resource"
    / "cr-native-linux-bionic"
    / "manifests"
    / "runtime-150535029-linux-bionic.json"
)

EXECUTABLES = (
    "apex/com.android.runtime/com.android.runtime/bin/linker64",
    "apex/com.android.art/com.android.art/bin/dalvikvm64",
)
MODIFIED_ARCHIVES = (
    "android31-userland.tar.gz",
    "system-etc.tar.gz",
    "direct-runtime-slot0.tar.gz",
)
EXTRA_WORKER_FILES = (
    "assets/assets.scdb",
    "data/update/data_manifest.toml",
)
ASSET_PACK_MEMBER = "runtime/apks/split_install_time_asset_pack.apk"
ASSET_SOURCES = {
    "assets/assets.scdb": "assets/assets.scdb",
    "data/update/data_manifest.toml": "assets/data_manifest.toml",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_archive(path: Path, entry: dict[str, object]) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size != int(entry["size"]):
        raise RuntimeError(f"size mismatch: {path}")
    actual = sha256_file(path)
    if actual != str(entry["sha256"]):
        raise RuntimeError(f"SHA-256 mismatch: {path}")


def extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(destination)


def repack_archive(directory: Path, archive: Path) -> None:
    archive.unlink(missing_ok=True)
    # Record uid/gid 0 so a real root install reproduces the upstream
    # ownership instead of inheriting the overlay builder's user.
    subprocess.run(
        [
            "tar",
            "--owner=0",
            "--group=0",
            "--numeric-owner",
            "-czf",
            str(archive),
            "-C",
            str(directory),
            ".",
        ],
        check=True,
        stdin=subprocess.DEVNULL,
    )


def extract_asset_pack(sandbox_zip: Path, destination: Path) -> Path:
    """Extract the split asset-pack APK from the sandbox runtime ZIP."""
    with zipfile.ZipFile(sandbox_zip) as archive:
        try:
            info = archive.getinfo(ASSET_PACK_MEMBER)
        except KeyError as error:
            raise RuntimeError(
                f"{ASSET_PACK_MEMBER} is missing from {sandbox_zip}"
            ) from error
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, destination.open("wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
    return destination


def patch_exec_bits(userland: Path) -> list[str]:
    patched = []
    for relative in EXECUTABLES:
        path = userland / relative
        if not path.is_file():
            raise RuntimeError(f"missing executable: {path}")
        if not path.stat().st_mode & 0o111:
            path.chmod(0o755)
            patched.append(relative)
    return patched


def patch_public_libraries(system: Path) -> bool:
    listing = system / "etc" / "public.libraries.txt"
    text = listing.read_text(encoding="utf-8")
    names = {line.split()[0] for line in text.splitlines() if line.strip()}
    if "libandroid_runtime.so" in names:
        return False
    if not text.endswith("\n"):
        text += "\n"
    listing.write_text(text + "libandroid_runtime.so\n", encoding="utf-8")
    return True


def patch_worker(
    worker: Path, bridge: Path, probe_jar: Path, asset_pack: Path
) -> dict[str, object]:
    for target, source in (
        (worker / "libnative_host_bridge.so", bridge),
        (worker / "lifecycle-probe.jar", probe_jar),
    ):
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copy2(source, target)
    with zipfile.ZipFile(asset_pack) as archive:
        for relative, member in ASSET_SOURCES.items():
            target = worker / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    (worker / "asset-pack.apk").unlink(missing_ok=True)
    return {
        "libnative_host_bridge.so": sha256_file(worker / "libnative_host_bridge.so"),
        "lifecycle-probe.jar": sha256_file(worker / "lifecycle-probe.jar"),
    }


def update_manifest(
    manifest: dict[str, object],
    bundle: Path,
    bridge: Path,
    probe_jar: Path,
    worker: Path,
) -> dict[str, object]:
    value = json.loads(json.dumps(manifest))
    for entry in value["archives"]:
        if entry["name"] in MODIFIED_ARCHIVES:
            path = bundle / entry["name"]
            entry["size"] = path.stat().st_size
            entry["sha256"] = sha256_file(path)
    worker_files = {entry["name"]: entry for entry in value["worker_files"]}
    for name, hash_value in (
        ("libnative_host_bridge.so", sha256_file(bridge)),
        ("lifecycle-probe.jar", sha256_file(probe_jar)),
    ):
        entry = worker_files.get(name, {"name": name})
        entry["size"] = (worker / name).stat().st_size
        entry["sha256"] = hash_value
        worker_files[name] = entry
    for name in EXTRA_WORKER_FILES:
        entry = worker_files.get(name, {"name": name})
        entry["size"] = (worker / name).stat().st_size
        entry["sha256"] = sha256_file(worker / name)
        worker_files[name] = entry
    value["worker_files"] = sorted(worker_files.values(), key=lambda row: row["name"])
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--sandbox-runtime-zip", type=Path, required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--probe-jar", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "builds" / "bundles")
    parser.add_argument(
        "--name",
        default="cr-native-linux-bionic-runtime-150535029-local-overlay",
    )
    parser.add_argument("--keep-work", action="store_true")
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.source_bundle.resolve(strict=True)
    bundle = (args.output / args.name).resolve()
    archive_path = (args.output / f"{args.name}.zip").resolve()
    receipt_path = (args.output / f"{args.name}.zip.manifest.json").resolve()
    if bundle.exists() or archive_path.exists():
        raise RuntimeError(f"refusing to overwrite {bundle} or {archive_path}")

    for entry in manifest["archives"]:
        verify_archive(source / entry["name"], entry)

    staging = Path(tempfile.mkdtemp(prefix="cr-overlay-", dir=str(args.output)))
    work = staging / "work"
    bundle.mkdir(parents=True)
    try:
        for name in MODIFIED_ARCHIVES:
            shutil.copy2(source / name, bundle / name)
        for entry in manifest["archives"]:
            name = entry["name"]
            if name not in MODIFIED_ARCHIVES:
                shutil.copy2(source / name, bundle / name)
        (bundle / "README.txt").write_text(
            "CR Native Linux Bionic Runtime 150535029 - LOCAL OVERLAY\n"
            "=======================================================\n\n"
            "Not an upstream release. Patched from the published bundle to\n"
            "cold-start: exec bits, public libraries, rebuilt host JAR/bridge\n"
            "with the headless UI-locale fix, assets.scdb and data_manifest.\n"
            "Asset pack APK is intentionally absent; the direct worker does\n"
            "not need it.\n",
            encoding="utf-8",
        )

        userland = work / "userland"
        system = work / "system"
        worker = work / "worker"
        extract_archive(bundle / "android31-userland.tar.gz", userland)
        extract_archive(bundle / "system-etc.tar.gz", system)
        extract_archive(bundle / "direct-runtime-slot0.tar.gz", worker)

        patched_exec = patch_exec_bits(userland)
        patched_libs = patch_public_libraries(system)
        asset_pack = extract_asset_pack(
            args.sandbox_runtime_zip.resolve(strict=True), work / "asset-pack.apk"
        )
        patch_worker(worker, args.bridge.resolve(strict=True),
                     args.probe_jar.resolve(strict=True), asset_pack)
        asset_pack.unlink(missing_ok=True)

        repack_archive(userland, bundle / "android31-userland.tar.gz")
        repack_archive(system, bundle / "system-etc.tar.gz")
        repack_archive(worker, bundle / "direct-runtime-slot0.tar.gz")

        manifest = update_manifest(
            manifest, bundle, args.bridge.resolve(strict=True),
            args.probe_jar.resolve(strict=True), worker,
        )
        (bundle / "BUNDLE-MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        rows = [
            (sha256_file(bundle / entry["name"]), entry["name"])
            for entry in manifest["archives"]
        ]
        rows.extend(
            (sha256_file(bundle / name), name)
            for name in ("BUNDLE-MANIFEST.json", "README.txt")
        )
        (bundle / "SHA256SUMS.txt").write_text(
            "".join(f"{digest}  {name}\n" for digest, name in sorted(rows)),
            encoding="utf-8",
        )

        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as archive:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    archive.write(
                        path, f"{bundle.name}/{path.relative_to(bundle).as_posix()}"
                    )
    finally:
        if not args.keep_work:
            shutil.rmtree(staging, ignore_errors=True)

    receipt = {
        "schema_version": 1,
        "kind": "cr_native_local_overlay_receipt_v1",
        "bundle_directory": str(bundle),
        "archive": archive_path.name,
        "archive_size": archive_path.stat().st_size,
        "archive_sha256": sha256_file(archive_path),
        "patched_exec_bits": patched_exec,
        "patched_public_libraries": patched_libs,
        "worker_files_added": list(EXTRA_WORKER_FILES),
        "asset_pack_apk": "omitted (not required by the direct worker)",
        "bridge_sha256": sha256_file(args.bridge.resolve(strict=True)),
        "probe_jar_sha256": sha256_file(args.probe_jar.resolve(strict=True)),
    }
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
