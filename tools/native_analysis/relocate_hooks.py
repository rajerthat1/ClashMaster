#!/usr/bin/env python3
"""Relocate the 15.535.13 probe hook prologues inside another libg.so build.

The probe ships a 16-byte prologue fingerprint for every inline hook.  Some of
those functions are byte-identical in 16.635.1; those relocate automatically.
Others changed, and this report makes that explicit instead of guessing.

Usage:
    python3 tools/native_analysis/relocate_hooks.py \
        .resource/native/16.635.1/reference/libg.so \
        --probe native_runner/probe --out docs/native/16.635.1/hook-relocation.json
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cr_elf import Binary  # noqa: E402

PROLOGUE_RE = re.compile(
    r"(?:constexpr|inline\s+constexpr)\s+std::uint8_t\s+(\w+?)(?:Prologue)?\[16\]\s*=\s*\{(.*?)\};",
    re.S,
)
OFFSET_RE = re.compile(r"(?:constexpr|inline\s+constexpr)\s+std::uintptr_t\s+(\w+)Offset\s*=\s*(0x[0-9a-fA-F]+);")
BYTE_RE = re.compile(r"0x([0-9a-fA-F]{2})")


@dataclass
class Hook:
    name: str
    offset: int | None
    prologue: bytes
    source: str


def parse_probe(root: Path) -> list[Hook]:
    hooks: list[Hook] = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".h", ".inc", ".cpp"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        offsets = {name: int(value, 16) for name, value in OFFSET_RE.findall(text)}
        for full_name, body in PROLOGUE_RE.findall(text):
            values = [int(value, 16) for value in BYTE_RE.findall(body)]
            if len(values) != 16:
                continue
            name = full_name[: -len("Prologue")] if full_name.endswith("Prologue") else full_name
            offset = offsets.get(name)
            hooks.append(Hook(name, offset, bytes(values), str(path)))
    return hooks


def find_occurrences(data: bytes, needle: bytes) -> list[int]:
    hits: list[int] = []
    start = 0
    while True:
        index = data.find(needle, start)
        if index < 0:
            return hits
        hits.append(index)
        start = index + 1


def _is_function_start(data: bytes, va: int) -> bool:
    """Heuristic: the word before the prologue is padding or a terminator."""
    if va < 4 or va + 4 > len(data):
        return True
    previous = struct.unpack_from("<I", data, va - 4)[0]
    return previous in (0xD65F03C0, 0xD503201F, 0x00000000) or (
        previous & 0xFC000000
    ) == 0x14000000 or (previous & 0xFFFFFC1F) == 0xD61F0000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("libg")
    parser.add_argument("--probe", default="native_runner/probe")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    binary = Binary(args.libg)
    data = binary.data
    exported = {value: name for value, _size, name in binary.symbols() if value}
    hooks = parse_probe(Path(args.probe))
    report = []
    for hook in hooks:
        hits = find_occurrences(data, hook.prologue)
        starts = [hit for hit in hits if _is_function_start(data, hit)]
        entry = {
            "hook": hook.name,
            "old_offset": None if hook.offset is None else f"0x{hook.offset:08x}",
            "prologue": hook.prologue.hex(),
            "occurrences": len(hits),
            "function_starts": len(starts),
            "candidates": [f"0x{hit:08x}" for hit in starts[:20]],
            "source": hook.source,
        }
        pool = starts or hits
        entry["exported_candidates"] = [
            {"address": f"0x{hit:08x}", "symbol": exported[hit]} for hit in hits if hit in exported
        ]
        if hook.offset is not None and pool:
            nearest = min(pool, key=lambda hit: abs(hit - hook.offset))
            entry["nearest"] = f"0x{nearest:08x}"
            entry["delta"] = f"{nearest - hook.offset:+#x}"
            if nearest in exported:
                entry["symbol"] = exported[nearest]
        report.append(entry)

    for entry in report:
        old = entry["old_offset"] or "?"
        if entry["function_starts"] == 1:
            verdict = "UNIQUE-START"
        elif entry["function_starts"] == 0:
            verdict = "CHANGED"
        else:
            verdict = f"AMBIGUOUS({entry['function_starts']})"
        near = entry.get("nearest", "")
        delta = entry.get("delta", "")
        symbol = entry.get("symbol", "")
        exports = entry.get("exported_candidates", [])
        if exports:
            symbol = exports[0]["symbol"]
        print(f"{verdict:<17} {entry['hook']:<52} old={old:<12} near={near:<12} delta={delta:<10} {symbol}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.out} ({len(report)} hooks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
