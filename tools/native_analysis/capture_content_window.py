#!/usr/bin/env python3
"""Capture the content-context window across a client boot.

Used to locate the engine's "content post-process ready" byte on a new build.
The probe's cold-configure gate reads a single byte at
``context + kContentPostProcessReadyOffset``: 0x6e8 on 15.535 and 0x720 on
16.635 (FINDINGS §3.13). This tool dumps a bounded window of the context over
time so the byte that actually settles to 1 can be found or verified
empirically, without root.

Forward the probe control port first, e.g.::

    adb forward tcp:26789 tcp:26789
    .venv/bin/python tools/native_analysis/capture_content_window.py --seconds 120

Launch or force-stop the client between runs so the capture spans a fresh boot::

    adb shell am force-stop nullsroyale.rel.free
    adb shell monkey -p nullsroyale.rel.free -c android.intent.category.LAUNCHER 1

The probe must expose the ``content-window`` command (present on both the
15.535.13 and 16.635.1 builds).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from native_runner.cr_native_env import RunnerError, NativeClashEnv  # noqa: E402


def parse_hex(value: str) -> int:
    return int(value, 16)


def capture(args: argparse.Namespace) -> list[dict]:
    env = NativeClashEnv(args.host, args.port, timeout=args.timeout)
    samples: list[dict] = []
    start = time.monotonic()
    while time.monotonic() - start < args.seconds:
        elapsed = round(time.monotonic() - start, 1)
        record = {"t": elapsed}
        try:
            status = env.status()
            record.update(
                {
                    "coldReady": status.get("coldReady"),
                    "contentPublished": status.get("contentPublished"),
                    "context": status.get("contentContext"),
                }
            )
            if status.get("contentPublished"):
                window = env._request(f"content-window {args.offset:x} {args.length:x}")
                record["hex"] = window.get("hex")
        except (RunnerError, OSError, ValueError) as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        samples.append(record)
        print(
            f"t={elapsed:6.1f}s coldReady={record.get('coldReady')} "
            f"published={record.get('contentPublished')} ctx={record.get('context')} "
            f"{'error=' + record['error'] if 'error' in record else ''}",
            flush=True,
        )
        time.sleep(args.interval)
    return samples


def analyse(samples: list[dict], offset: int, watch: int | None) -> None:
    windowed = [s for s in samples if s.get("hex")]
    if not windowed:
        print("\nno samples with a published content context")
        return

    contexts = sorted({s["context"] for s in windowed})
    print(f"\ncontexts seen: {contexts}")
    for context in contexts:
        group = [s for s in windowed if s["context"] == context]
        cold = sorted({s["coldReady"] for s in group})
        print(f"  {context}: {len(group)} samples, coldReady values {cold}")

    context = max({s["context"] for s in windowed}, key=lambda c: sum(1 for s in windowed if s["context"] == c))
    group = [s for s in windowed if s["context"] == context]
    first = bytes.fromhex(group[0]["hex"])
    last = bytes.fromhex(group[-1]["hex"])
    size = min(len(first), len(last))

    if watch is not None and offset <= watch < offset + size:
        index = watch - offset
        values = [(s["t"], s["hex"][index * 2 : index * 2 + 2]) for s in group]
        distinct = sorted({value for _, value in values})
        print(f"\nwatch 0x{watch:x} over {len(values)} samples: values={distinct}")
        print(f"  first={values[0]}, last={values[-1]}")

    ones = [i for i in range(size) if last[i] == 1]
    print(f"\nbytes equal to 0x01 in the final sample ({len(ones)}):")
    for index in ones[:40]:
        first_one = next((s["t"] for s in group if s["hex"][index * 2 : index * 2 + 2] == "01"), None)
        tail = last[index : index + 8]
        print(f"  +0x{offset + index:03x}: first=1 at t={first_one}s neighbours={' '.join(f'{b:02x}' for b in tail)}")

    flips = [i for i in range(size) if {b for b in (first[i], last[i])} == {0, 1}]
    print(f"\nbytes that differ and are 0/1 across the capture ({len(flips)}):")
    for index in flips[:40]:
        print(f"  +0x{offset + index:03x}: {first[index]:02x} -> {last[index]:02x}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=26789)
    parser.add_argument("--timeout", type=float, default=4.0)
    parser.add_argument("--offset", type=parse_hex, default=0x500, help="hex window start (default 0x500)")
    parser.add_argument("--length", type=parse_hex, default=0x300, help="hex window length (default 0x300)")
    parser.add_argument("--seconds", type=float, default=90.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--watch", type=parse_hex, default=0x6E8, help="hex offset to track (default 0x6e8)")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    samples = capture(args)
    analyse(samples, args.offset, args.watch)

    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(samples, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
