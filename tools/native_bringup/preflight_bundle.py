#!/usr/bin/env python3
"""Static cold-deploy preflight for a cr-native-linux-bionic runtime bundle.

`cr-native-bionic doctor` only checks that files exist and that archive hashes
match.  It does not catch the defects that stop a *fresh* install from starting:
executable bits, the public-library list, root-owned property-area files, the
host JAR/bridge JNI registration, the headless UI-locale guard, or game asset
packs.  This tool reads the bundle without extracting it and reports each of
those checks, so a VM is not spent discovering them.

It never needs root and never writes to ``/``.

    .venv/bin/python tools/native_bringup/preflight_bundle.py \
        --bundle-dir builds/bundles/cr-native-linux-bionic-runtime-150535029

Exit status 0 means every required check passed; 1 means at least one did not.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[2]
    / ".resource"
    / "cr-native-linux-bionic"
    / "manifests"
    / "runtime-150535029-linux-bionic.json"
)

USERLAND_ARCHIVE = "android31-userland.tar.gz"
SYSTEM_ETC_ARCHIVE = "system-etc.tar.gz"
WORKER_ARCHIVE = "direct-runtime-slot0.tar.gz"
PROPERTY_ARCHIVE = "android-properties.tar.gz"

EXECUTABLES = (
    "apex/com.android.runtime/com.android.runtime/bin/linker64",
    "apex/com.android.art/com.android.art/bin/dalvikvm64",
)
PUBLIC_LIBRARY = "libandroid_runtime.so"
# The direct worker needs these two files; the 886 MB asset-pack.apk is only
# used by the AVD/Windows runtime and is not required here (verified cold).
REQUIRED_ASSETS = (
    "assets/assets.scdb",
    "data/update/data_manifest.toml",
)
OPTIONAL_ASSETS = ("asset-pack.apk",)

# bionic's prop_area::map_fd_ro() rejects property files whose st_uid/st_gid is
# not 0, so a fresh install must keep the archive's root ownership.
PROPERTY_MEMBERS = ("properties_serial", "property_info")

# The bridge installs this byte cave for the binderless UI-locale guard.  The
# published bridge calls the locale registration at 0x1AE0270, which allocates
# from a bogus pointer-sized length and aborts the worker.  The fixed bridge
# jumps straight to the resume point instead.
LEGACY_LOCALE_CAVE = bytes.fromhex(
    "4885ff741048833f00740a"
    "e87b502d00e93008caffe92b08caff"
)
FIXED_LOCALE_CAVE = bytes.fromhex(
    "4885ff741048833f00740a"
    "e93508caffe93008caffe92b08caff"
)
REQUIRED_JAR_SYMBOLS = ("nativeRegisterAndroidRuntime",)
REQUIRED_BRIDGE_SYMBOLS = (
    "Java_royale_nativehost_JniHost_nativeRegisterAndroidRuntime",
    "Java_royale_nativehost_JniHost_nativeStep",
    "Java_royale_nativehost_JniHost_nativeObserveTrain",
    "Java_royale_nativehost_JniHost_nativeStepTrainTrace",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def member(tar: tarfile.TarFile, name: str) -> tarfile.TarInfo:
    for candidate in (name, f"./{name}"):
        try:
            return tar.getmember(candidate)
        except KeyError:
            continue
    raise KeyError(f"archive member missing: {name}")


def read_member(archive: Path, name: str) -> bytes:
    with tarfile.open(archive, "r:gz") as tar:
        info = member(tar, name)
        handle = tar.extractfile(info)
        if handle is None:
            raise ValueError(f"archive member is not a regular file: {name}")
        return handle.read()


def check_manifest(bundle: Path, manifest_path: Path | None) -> dict[str, Any]:
    if manifest_path is None:
        return {"ok": None, "skipped": "no manifest supplied"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = []
    for entry in manifest["archives"]:
        path = bundle / entry["name"]
        actual_size = path.stat().st_size if path.is_file() else None
        actual_hash = sha256(path) if path.is_file() else None
        rows.append({
            "name": entry["name"],
            "size_ok": actual_size == int(entry["size"]),
            "sha256_ok": actual_hash == entry["sha256"],
            "expected_size": int(entry["size"]),
            "actual_size": actual_size,
        })
    return {
        "ok": all(row["size_ok"] and row["sha256_ok"] for row in rows),
        "runtime_version": manifest.get("runtime_version"),
        "game_version": manifest.get("game_version"),
        "libg_sha256": manifest.get("libg_sha256"),
        "archives": rows,
    }


def check_exec_bits(bundle: Path) -> dict[str, Any]:
    rows = []
    with tarfile.open(bundle / USERLAND_ARCHIVE, "r:gz") as tar:
        for name in EXECUTABLES:
            try:
                info = member(tar, name)
                executable = bool(info.mode & 0o111)
                rows.append({
                    "member": name,
                    "mode": f"{info.mode:o}",
                    "executable": executable,
                })
            except KeyError as error:
                rows.append({"member": name, "executable": False, "error": str(error)})
    return {"ok": all(row.get("executable") for row in rows), "files": rows}


def check_public_libraries(bundle: Path, library: str) -> dict[str, Any]:
    raw = read_member(bundle / SYSTEM_ETC_ARCHIVE, "etc/public.libraries.txt")
    names = {
        line.split()[0]
        for line in raw.decode("utf-8", "replace").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    return {
        "ok": library in names,
        "library": library,
        "entries": len(names),
    }


def check_assets(bundle: Path) -> dict[str, Any]:
    with tarfile.open(bundle / WORKER_ARCHIVE, "r:gz") as tar:
        names = {name.lstrip("./") for name in tar.getnames()}
    rows = [{"member": item, "present": item in names} for item in REQUIRED_ASSETS]
    optional = [
        {"member": item, "present": item in names} for item in OPTIONAL_ASSETS
    ]
    return {
        "ok": all(row["present"] for row in rows),
        "files": rows,
        "optional": optional,
    }


def _symbol_present(blob: bytes, symbol: str) -> bool:
    return symbol.encode("ascii") in blob


def check_jar(bundle: Path) -> dict[str, Any]:
    jar_bytes = read_member(bundle / WORKER_ARCHIVE, "lifecycle-probe.jar")
    with zipfile.ZipFile(io.BytesIO(jar_bytes)) as jar:
        dex = b"".join(
            jar.read(name) for name in jar.namelist() if name.endswith(".dex")
        )
    rows = [
        {"symbol": symbol, "present": _symbol_present(dex, symbol)}
        for symbol in REQUIRED_JAR_SYMBOLS
    ]
    return {"ok": all(row["present"] for row in rows), "symbols": rows,
            "dex_bytes": len(dex)}


def check_bridge(bundle: Path) -> dict[str, Any]:
    blob = read_member(bundle / WORKER_ARCHIVE, "libnative_host_bridge.so")
    rows = [
        {"symbol": symbol, "present": _symbol_present(blob, symbol)}
        for symbol in REQUIRED_BRIDGE_SYMBOLS
    ]
    return {"ok": all(row["present"] for row in rows), "symbols": rows,
            "bytes": len(blob)}


def check_properties_owner(bundle: Path) -> dict[str, Any]:
    rows = []
    with tarfile.open(bundle / PROPERTY_ARCHIVE, "r:gz") as tar:
        for name in PROPERTY_MEMBERS:
            info = member(tar, name)
            rows.append({
                "member": name,
                "uid": info.uid,
                "gid": info.gid,
                "root_owned": info.uid == 0 and info.gid == 0,
            })
    return {"ok": all(row["root_owned"] for row in rows), "files": rows}


def check_locale_guard(bundle: Path) -> dict[str, Any]:
    blob = read_member(bundle / WORKER_ARCHIVE, "libnative_host_bridge.so")
    return {
        "ok": FIXED_LOCALE_CAVE in blob,
        "legacy_cave": LEGACY_LOCALE_CAVE in blob,
        "fixed_cave": FIXED_LOCALE_CAVE in blob,
    }


CHECKS = (
    ("manifest", check_manifest),
    ("exec_bits", check_exec_bits),
    ("public_libraries", check_public_libraries),
    ("properties_owner", check_properties_owner),
    ("assets", check_assets),
    ("host_jar", check_jar),
    ("host_bridge", check_bridge),
    ("headless_locale_guard", check_locale_guard),
)


def render(report: dict[str, Any]) -> str:
    lines = []
    for name, result in report["checks"].items():
        if result.get("ok") is None:
            status = "SKIP"
        else:
            status = "PASS" if result.get("ok") else "FAIL"
        lines.append(f"[{status}] {name}")
        if result.get("error"):
            lines.append(f"         {result['error']}")
        if name == "manifest":
            for row in result.get("archives", []):
                if not (row["size_ok"] and row["sha256_ok"]):
                    lines.append(f"         {row['name']}: size_ok={row['size_ok']} "
                                 f"sha256_ok={row['sha256_ok']}")
        elif name == "exec_bits":
            for row in result.get("files", []):
                if not row.get("executable"):
                    lines.append(f"         {row['member']} mode={row.get('mode')}")
        elif name == "public_libraries" and not result.get("ok"):
            lines.append(f"         {result['library']} is absent from "
                         f"{SYSTEM_ETC_ARCHIVE}:etc/public.libraries.txt")
        elif name == "properties_owner":
            for row in result.get("files", []):
                if not row.get("root_owned"):
                    lines.append(
                        f"         {row['member']} uid={row.get('uid')} "
                        f"gid={row.get('gid')} (bionic requires 0/0)"
                    )
        elif name == "assets":
            for row in result.get("files", []):
                if not row["present"]:
                    lines.append(f"         {row['member']} is absent")
            for row in result.get("optional", []):
                if not row["present"]:
                    lines.append(
                        f"         note: optional {row['member']} is absent "
                        "(not needed by the direct worker)"
                    )
        elif name == "headless_locale_guard" and not result.get("ok"):
            if result.get("legacy_cave"):
                lines.append(
                    "         bridge still calls the UI locale registration "
                    "at 0x1AE0270 (bogus allocation; rebuild with the "
                    "headless locale guard fix)"
                )
            else:
                lines.append(
                    "         fixed UI locale cave not found; rebuild the "
                    "bridge from android_probe/native/jni_bridge.cpp"
                )
        elif name in ("host_jar", "host_bridge"):
            for row in result.get("symbols", []):
                if not row["present"]:
                    lines.append(f"         {row['symbol']} is absent")
    lines.append(f"RESULT: {'PASS' if report['ok'] else 'FAIL'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=(
            "runtime bundle manifest; defaults to the bundle's "
            "BUNDLE-MANIFEST.json, then the repository manifest"
        ),
    )
    parser.add_argument("--json", action="store_true", help="print JSON only")
    args = parser.parse_args(argv)

    bundle = args.bundle_dir.resolve(strict=True)
    manifest_path = args.manifest
    if manifest_path is None:
        bundled = bundle / "BUNDLE-MANIFEST.json"
        manifest_path = bundled if bundled.is_file() else DEFAULT_MANIFEST
    manifest = manifest_path if manifest_path.is_file() else None
    checks: dict[str, Any] = {}
    for name, function in CHECKS:
        try:
            if name == "manifest":
                checks[name] = function(bundle, manifest)
            elif name == "public_libraries":
                checks[name] = function(bundle, PUBLIC_LIBRARY)
            else:
                checks[name] = function(bundle)
        except Exception as error:  # a broken bundle must fail cleanly, not crash
            checks[name] = {
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
            }
    report = {
        "schema_version": 1,
        "kind": "cr_native_bundle_preflight_v1",
        "bundle_dir": str(bundle),
        "ok": all(value.get("ok") is not False for value in checks.values()),
        "checks": checks,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
