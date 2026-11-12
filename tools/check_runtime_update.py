"""Verify (and optionally install) a Null's Royale runtime update folder.

The offline engine reuses the game's own downloaded content from
``/data/user/0/nullsroyale.rel.free/update``.  This tool checks a local copy of that
folder against the pinned 15.535.86 hashes before anything touches the emulator.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_runner.offline_build import PACKAGE, sha256, supported_engine  # noqa: E402

APP = "nullsroyale.rel.free"
DEVICE_UPDATE = f"/data/user/0/{APP}/update"


def pinned_files():
    manifest = json.loads((PACKAGE / "data/competitive/manifest.json").read_text(encoding="utf-8"))
    return {
        name.removeprefix("runtime-update/"): digest
        for name, digest in manifest["source_files"].items()
        if name.startswith("runtime-update/")
    }


def verify(update, pinned=None, fingerprint=None):
    release = supported_engine()
    pinned = pinned_files() if pinned is None else pinned
    fingerprint_sha = fingerprint or release["runtime_fingerprint_sha256"]
    missing, mismatched = [], []
    for name, expected in pinned.items():
        path = update / name
        if not path.is_file():
            missing.append(name)
        elif sha256(path) != expected:
            mismatched.append(name)
    fingerprint_path = update / "fingerprint.json"
    fingerprint_ok = fingerprint_path.is_file() and sha256(fingerprint_path) == fingerprint_sha
    return {
        "ok": fingerprint_ok and not missing and not mismatched,
        "fingerprint_ok": fingerprint_ok,
        "pinned_files": len(pinned),
        "missing": missing,
        "mismatched": mismatched,
    }


def fill_from_apk(update, apk, pinned=None):
    """Copy pinned files that the client never downloads from the APK's bundled assets."""
    import zipfile

    pinned = pinned_files() if pinned is None else pinned
    restored, unavailable = [], []
    with zipfile.ZipFile(apk) as archive:
        for name, expected in pinned.items():
            destination = update / name
            if destination.is_file() and sha256(destination) == expected:
                continue
            try:
                payload = archive.read(f"assets/{name}")
            except KeyError:
                unavailable.append(name)
                continue
            if hashlib.sha256(payload).hexdigest() != expected:
                unavailable.append(name)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            restored.append(name)
    return {"restored": restored, "unavailable": unavailable}


def device_hashes(adb, serial):
    text = subprocess.run(
        [adb, "-s", serial, "shell", f"cd {DEVICE_UPDATE} && find . -type f -exec sha256sum {{}} +"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    hashes = {}
    for line in text.splitlines():
        digest, name = line.split(None, 1)
        if name.startswith("./"):
            hashes[name[2:]] = digest
    return hashes


def install(update, adb, serial, pinned=None):
    pinned = pinned_files() if pinned is None else pinned
    uid = subprocess.run(
        [adb, "-s", serial, "shell", f"stat -c %u /data/user/0/{APP}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not uid.isdecimal() or int(uid) < 10000:
        raise SystemExit("Install the game and enable root first; could not read the app uid")
    files = dict(pinned)
    files["fingerprint.json"] = sha256(update / "fingerprint.json")
    directories = sorted({str(Path(name).parent).replace("\\", "/") for name in files} - {"."})
    for directory in directories:
        subprocess.run([adb, "-s", serial, "shell", f"mkdir -p {DEVICE_UPDATE}/{directory}"], check=True)
    for name in files:
        subprocess.run([adb, "-s", serial, "push", str(update / name), f"{DEVICE_UPDATE}/{name}"], check=True)
    subprocess.run([adb, "-s", serial, "shell", f"chown -R {uid}:{uid} {DEVICE_UPDATE}"], check=True)
    subprocess.run([adb, "-s", serial, "shell", f"restorecon -RF {DEVICE_UPDATE}"], check=True)
    hashes = device_hashes(adb, serial)
    mismatched = [name for name, expected in pinned.items() if hashes.get(name) != expected]
    if hashes.get("fingerprint.json") != files["fingerprint.json"]:
        mismatched.append("fingerprint.json")
    if mismatched:
        raise SystemExit(f"Device content still differs for {len(mismatched)} files")
    return {"installed": True, "verified_files": len(pinned), "fingerprint_installed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", type=Path, required=True, help="local copy of the device update folder")
    parser.add_argument("--fill-from-apk", type=Path, help="copy missing files from this APK's assets")
    parser.add_argument("--adb", help="adb executable; required with --install")
    parser.add_argument("--serial", default="127.0.0.1:16384")
    parser.add_argument("--install", action="store_true", help="push the verified folder to the emulator")
    args = parser.parse_args()

    if args.fill_from_apk:
        print(json.dumps(fill_from_apk(args.update, args.fill_from_apk), indent=2), flush=True)
    report = verify(args.update)
    print(json.dumps(report, indent=2), flush=True)
    if not report["ok"]:
        raise SystemExit("Update folder does not match the pinned 15.535.86 content")
    if args.install:
        if not args.adb:
            raise SystemExit("--install requires --adb")
        print(json.dumps(install(args.update, args.adb, args.serial), indent=2), flush=True)


if __name__ == "__main__":
    main()
