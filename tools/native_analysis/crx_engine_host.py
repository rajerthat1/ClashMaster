#!/usr/bin/env python3
"""Host the official x86_64 16.635.1 engine on Linux and attach the probe.

The A51 remains the ARM64 ground truth, but it is a phone.  The same client
build exists as x86_64 (`Clashback/game/engine/libg.so`, sha `c4721bd9…`), and
`.resource/Clashback/vendor/crx` already knows how to map that ELF into a plain
Linux process with glibc shims — no Android, no emulator, no KVM.  This script
boots the engine headless (JNI_OnLoad -> GameMain::init) and then loads
`libcrprobe-16.635.1-x86_64.so` into the same process, passing the engine base
with `cr_probe_attach()` (crx maps libg anonymously, so the probe's
`/proc/self/maps` scanner cannot find it).

After attach the probe's control server is the same `cr-native-control.v1`
endpoint the A51 uses, so every existing tool works unchanged:

    .venv/bin/python tools/native_analysis/crx_engine_host.py --seconds 600 &
    .venv/bin/python tools/native_analysis/scripted_xbow_match.py --seed 1 \
        --output /tmp/opencode/x86-seed1.json

Run several independent engines with ``--engine-index N`` (ports 26789+N by
default), which also gives each probe process its own control cpu:

    for i in 0 1 2 3; do
        .venv/bin/python tools/native_analysis/crx_engine_host.py \
            --engine-index "$i" --seconds 1800 &
    done

The host exits when `--seconds` elapses or stdin closes.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CLASHBACK = REPO / ".resource" / "Clashback"
DEFAULT_PROBE = REPO / "native_runner" / "probe" / "out" / "x86_64" / "libcrprobe.so"


def probe_command(port: int, text: str, timeout: float = 30.0) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.sendall((text + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(1 << 20)
            if not chunk:
                break
            data += chunk
    return json.loads(data.decode())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--clashback", type=Path, default=DEFAULT_CLASHBACK)
    parser.add_argument("--probe", type=Path, default=DEFAULT_PROBE)
    parser.add_argument(
        "--work",
        type=Path,
        default=None,
        help="crx scratch directory (default: /tmp/opencode/crx-work[-N])",
    )
    parser.add_argument(
        "--engine-index",
        type=int,
        default=0,
        help="probe engine id (0..23); selects CR_PROBE_ENGINE_ID and the control cpu",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="control port (default: 26789 + engine index)",
    )
    parser.add_argument("--seconds", type=float, default=0.0, help="0 = run until stdin closes")
    parser.add_argument(
        "--package",
        default="com.supercell.clashroyale",
        help="attestation package identity (the x86_64 engine is the official client)",
    )
    args = parser.parse_args(argv)
    if not 0 <= args.engine_index < 24:
        parser.error("--engine-index must be in 0..23")
    port = args.port if args.port is not None else 26789 + args.engine_index
    if not 1 <= port <= 65535:
        parser.error("--port must be in 1..65535")
    work = args.work
    if work is None:
        suffix = "" if args.engine_index == 0 else f"-{args.engine_index}"
        work = Path(f"/tmp/opencode/crx-work{suffix}")

    clashback = args.clashback.resolve()
    engine_dir = clashback / "game" / "engine"
    assets = clashback / "game" / "assets"
    if not (engine_dir / "libg.so").is_file() or not (engine_dir / "libc++_shared.so").is_file():
        print(f"missing engine files under {engine_dir}", file=sys.stderr)
        return 2
    if not args.probe.is_file():
        print(f"missing probe: {args.probe} (build with --abi x86_64)", file=sys.stderr)
        return 2

    # crx imports; the engine host needs the Clashback root for neo_replay.
    sys.path.insert(0, str(clashback))
    sys.path.insert(0, str(clashback / "vendor" / "crx"))
    # The probe imports zlib/math; make sure those are in the global scope crx
    # resolves against (it uses dlsym(RTLD_DEFAULT)).  RTLD_GLOBAL matters:
    # Python loads its own zlib extension RTLD_LOCAL, so a plain CDLL() here
    # leaves compress2/compressBound unresolvable, and crx then hands the probe
    # its "missing function" stub, which returns 0 == Z_OK with an unchanged
    # (zero) output size — every compressed batch entry would ship truncated.
    for library in ("libz.so.1", "libm.so.6"):
        try:
            ctypes.CDLL(library, mode=ctypes.RTLD_GLOBAL)
        except OSError:
            pass

    import loader as L  # noqa: E402
    from env import CrxEnv  # noqa: E402

    # CrxEnv boots the engine, installs the presentation/render guards and pumps
    # the manager until the data tables (and the content post-process flag the
    # probe checks) are ready.  We only use it as the host.
    env = CrxEnv(engine_dir, work, assets.resolve())
    g = env.g
    print(f"engine base=0x{g.base:x} manager=0x{env.manager:x}", flush=True)

    ld = L.Loader()
    cxx = ld.load(str(engine_dir / "libc++_shared.so"))
    ld.run_constructors(cxx)
    probe = ld.load(str(args.probe))
    ld.run_constructors(probe)
    # crx resolves probe imports through dlsym(RTLD_DEFAULT) and substitutes a
    # "returns 0" stub for anything it cannot find.  For zlib that stub is
    # indistinguishable from success (`compress2` returns 0 == Z_OK but writes
    # nothing), which silently corrupts every compressed batch entry.  Refuse
    # to host the probe rather than ship a data plane that fails much later.
    unresolved = sorted(name for name in ("compress2", "compressBound") if name in ld.missing)
    if unresolved:
        print(
            "probe imports are unresolved in this process: "
            + ", ".join(unresolved)
            + " (zlib must be loaded RTLD_GLOBAL before the probe)",
            file=sys.stderr,
        )
        return 4
    # crx maps everything anonymously and serves assets through a file shim, so
    # the probe cannot discover the on-disk identities by itself.
    os.environ.update(
        {
            "CR_PROBE_LIBG_PATH": str((engine_dir / "libg.so").resolve()),
            "CR_PROBE_SELF_PATH": str(args.probe.resolve()),
            "CR_PROBE_CONTENT_ROOT": str(assets.resolve()),
            "CR_PROBE_PACKAGE": args.package,
            "CR_PROBE_ASSETS_MAPPED": "1",
            # Several host processes can share one machine; the probe identity
            # is otherwise discovered from an Android process name it cannot
            # have here.  Keep one engine per port and one control cpu each.
            "CR_PROBE_ENGINE_ID": str(args.engine_index),
            "CR_PROBE_CONTROL_PORT": str(port),
        }
    )
    attach = L.cfunc(probe.exports["cr_probe_attach"], ctypes.c_int32, ctypes.c_uint64)
    if not attach(g.base):
        print("cr_probe_attach failed", file=sys.stderr)
        return 3
    time.sleep(0.2)
    print("attestation:", json.dumps(probe_command(port, "attest")), flush=True)
    print(f"probe listening on 127.0.0.1:{port}", flush=True)

    if args.seconds > 0:
        time.sleep(args.seconds)
    else:
        for _line in sys.stdin:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
