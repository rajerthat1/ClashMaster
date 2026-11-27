#!/usr/bin/env python3
"""Build the read-only official-client frame reader for Android.

Default source: Hasty-CR's ``hcr_reader.c`` (x86_64 MuMu build 160402012).
Its offsets are build-pinned; on a different client the binary compiles and
runs but reads the wrong addresses.  Re-derive the constants in
``scripts/gamestate/memread.py`` / ``hcr_reader.c`` for the installed build
before trusting a frame.

Usage:
    python tools/live/build_reader.py --abi arm64-v8a
    python tools/live/build_reader.py --abi x86_64 --source path/to/reader.c
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NDK = REPO_ROOT / ".resource" / "android-tools" / "android-ndk-r27c"
DEFAULT_SOURCE = REPO_ROOT / ".resource" / "Hasty-CR" / "scripts" / "gamestate" / "native" / "hcr_reader.c"
OUTPUT_DIR = REPO_ROOT / "build" / "live"

TRIPLES = {
    "arm64-v8a": "aarch64-linux-android",
    "x86_64": "x86_64-linux-android",
}


def clang_for(ndk: Path, abi: str, api: int) -> Path:
    triple = TRIPLES[abi]
    host = "linux-x86_64" if sys.platform.startswith("linux") else "darwin-x86_64"
    candidate = ndk / "toolchains" / "llvm" / "prebuilt" / host / "bin" / f"{triple}{api}-clang"
    if not candidate.is_file():
        raise SystemExit(f"missing NDK clang: {candidate}")
    return candidate


def build(ndk: Path, source: Path, abi: str, api: int, output: Path) -> Path:
    compiler = clang_for(ndk, abi, api)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(compiler),
        "-O2",
        "-fPIE",
        "-pie",
        "-Wall",
        "-Wextra",
        "-o",
        str(output),
        str(source),
    ]
    print("+", " ".join(command))
    subprocess.run(command, check=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--abi", choices=sorted(TRIPLES), action="append", default=None)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--ndk", type=Path, default=Path(os.environ.get("ANDROID_NDK_ROOT", DEFAULT_NDK)))
    parser.add_argument("--api", type=int, default=31)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    arguments = parser.parse_args()

    if not arguments.source.is_file():
        raise SystemExit(f"missing reader source: {arguments.source}")
    abis = arguments.abi or ["arm64-v8a", "x86_64"]
    for abi in abis:
        output = arguments.output_dir / f"hcr_reader-{abi}"
        build(arguments.ndk, arguments.source, abi, arguments.api, output)
        print(f"built {output}")
        print(f"  adb push {output} /data/local/tmp/hcr_reader")
        print("  adb shell chmod 755 /data/local/tmp/hcr_reader")
    print("\nThe reader is read-only (pread on /proc/<pid>/mem). It needs root.")
    print("Run: adb shell su -c '/data/local/tmp/hcr_reader <pid> <libg_base_hex> 50'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
