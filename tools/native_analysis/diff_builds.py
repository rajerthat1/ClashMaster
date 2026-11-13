#!/usr/bin/env python3
"""Diff two libg.so builds through the 15.535 probe's offset table.

Given the old build (15.535.13), the new build (16.635.1) and the probe source,
this searches for exact byte windows taken from each old offset.  A 48-byte
window inside an unchanged function is far more selective than the 16-byte
prologue the probe uses for attestation.

Outputs a table of old offset -> best new offset with a confidence:

    EXACT      unique window match (>= 32 bytes)
    LIKELY     unique 24-byte match, or one of two neighbours
    AMBIGUOUS  multiple equally good matches
    MISSING    no window found (function changed or removed)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cr_elf import Binary  # noqa: E402

GENERATED_LAYOUT_MARKER = "tools/native_analysis/gen_layout.py"
OFFSET_RE = re.compile(
    r"(?:constexpr|inline\s+constexpr)\s+std::uintptr_t\s+(\w+)Offset\s*=\s*(0x[0-9a-fA-F]+);"
)
SKIP_SUFFIXES = (
    "OffsetCompare",  # not present, defensive
)


@dataclass
class OldAnchor:
    name: str
    offset: int
    source: str


def parse_offsets(probe: Path) -> list[OldAnchor]:
    anchors: dict[str, OldAnchor] = {}
    for path in sorted(probe.rglob("*")):
        if path.suffix not in {".h", ".inc", ".cpp"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if GENERATED_LAYOUT_MARKER in text:
            continue
        for name, value in OFFSET_RE.findall(text):
            offset = int(value, 16)
            if offset <= 0:
                continue
            anchors.setdefault(name, OldAnchor(name, offset, str(path)))
    return sorted(anchors.values(), key=lambda anchor: anchor.offset)


def search(data: bytes, needle: bytes, limit: int = 5000) -> list[int]:
    hits: list[int] = []
    start = 0
    while len(hits) < limit:
        index = data.find(needle, start)
        if index < 0:
            break
        hits.append(index)
        start = index + 1
    return hits


def relocate(old: Binary, new: Binary, anchor: OldAnchor) -> dict:
    entry = {
        "hook": anchor.name,
        "old_offset": f"0x{anchor.offset:08x}",
        "source": anchor.source,
    }

    def unique_at(relative: int, size: int) -> int | None:
        try:
            needle = old.read(anchor.offset + relative, size)
        except SystemExit:
            return None
        if len(needle) < size:
            return None
        hits = search(new.data, needle)
        if len(hits) == 1:
            address = new.offset_to_va(hits[0])
            return None if address is None else address - relative
        return None

    # Fast path: a long window at the anchor itself.
    for size in (192, 128, 96, 72, 64, 48, 40, 32, 28, 24, 20, 16):
        try:
            needle = old.read(anchor.offset, size)
        except SystemExit:
            break
        if len(needle) < size:
            continue
        hits = search(new.data, needle)
        if not hits:
            continue
        entry["window"] = size
        entry["occurrences"] = len(hits)
        if len(hits) == 1:
            address = new.offset_to_va(hits[0])
            if address is None:
                entry["confidence"] = "MISSING"
                return entry
            entry["candidates"] = [f"0x{address:08x}"]
            entry["new_offset"] = f"0x{address:08x}"
            entry["delta"] = f"{address - anchor.offset:+#x}"
            entry["confidence"] = "EXACT" if size >= 32 else "LIKELY"
            return entry
        entry["candidates"] = [
            f"0x{address:08x}" for address in (new.offset_to_va(hit) for hit in hits[:8]) if address is not None
        ]
        break

    # Multi-window vote: the same recovered base from independent windows.
    votes: dict[int, list[int]] = {}
    for relative in (16, 32, 48, 64, 96, 128, 160, 192, 256):
        for size in (96, 64, 48, 32):
            base = unique_at(relative, size)
            if base is not None:
                votes.setdefault(base, []).append((relative, size))
    if votes:
        base, evidence = max(votes.items(), key=lambda item: (len(item[1]), max(e[1] for e in item[1])))
        entry["new_offset"] = f"0x{base:08x}"
        entry["delta"] = f"{base - anchor.offset:+#x}"
        entry["window"] = max(e[1] for e in evidence)
        entry["votes"] = [{"relative": rel, "size": size} for rel, size in evidence]
        entry["confidence"] = "EXACT" if len(evidence) >= 2 else "LIKELY"
        return entry

    if "occurrences" in entry:
        entry["confidence"] = "AMBIGUOUS"
    else:
        entry["confidence"] = "MISSING"
    return entry


def load_overrides(path: Path | None) -> dict[str, dict]:
    """Manual corrections for anchors the byte diff cannot separate on its own.

    The file is a JSON object with an ``overrides`` list.  Each entry names a
    probe anchor and its verified new offset, plus free-form evidence.  This
    keeps reproducible corrections (e.g. a near-duplicate function pair) in
    version control instead of editing the generated report by hand.
    """
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("overrides", payload) if isinstance(payload, dict) else payload
    result: dict[str, dict] = {}
    for entry in entries:
        hook = entry.get("hook")
        new_offset = entry.get("new_offset")
        if not hook or not new_offset:
            raise SystemExit(f"{path}: override needs hook and new_offset: {entry!r}")
        result[hook] = entry
    return result


def _expected_delta(rows: list[dict], old_offset: int) -> int | None:
    """Local delta from the nearest EXACT code anchors."""
    exact = []
    for row in rows:
        if row["confidence"] != "EXACT" or "new_offset" not in row:
            continue
        old = int(row["old_offset"], 16)
        new = int(row["new_offset"], 16)
        if old < 0x17E6550:  # old code segment
            exact.append((old, new - old))
    if not exact:
        return None
    exact.sort()
    nearest = min(exact, key=lambda item: abs(item[0] - old_offset))
    if abs(nearest[0] - old_offset) > 0x60000:
        return None
    return nearest[1]


def _function_start_before(binary: Binary, va: int, window: int = 160) -> int:
    """Walk back to the previous terminator; prologue lengths change per build."""
    import struct

    for address in range(va - 4, max(0, va - window) - 4, -4):
        offset = binary.va_to_offset(address)
        if offset is None:
            break
        word = struct.unpack_from("<I", binary.data, offset)[0]
        if word in (0xD65F03C0, 0xD503201F, 0x00000000) or (word & 0xFC000000) == 0x14000000:
            return address + 4
    return va


def _constrained_search(
    old: Binary, new: Binary, anchor: OldAnchor, expected: int, tolerance: int = 0x3000
) -> tuple[int, int, int] | None:
    for relative in (0, 16, 32, 48, 64, 96, 128):
        for size in (96, 64, 48, 32, 24, 20, 16):
            try:
                needle = old.read(anchor.offset + relative, size)
            except SystemExit:
                continue
            if len(needle) < size:
                continue
            bases: set[int] = set()
            start = 0
            while len(bases) < 4:
                index = new.data.find(needle, start)
                if index < 0:
                    break
                start = index + 1
                hit = new.offset_to_va(index)
                if hit is None:
                    continue
                base = _function_start_before(new, hit)
                # The matched instructions must sit at roughly the same
                # distance from the function start in both builds.
                if abs((hit - base) - relative) > 0x40:
                    continue
                if abs((base - anchor.offset) - expected) <= tolerance:
                    bases.add(base)
            if len(bases) == 1:
                return bases.pop(), size, relative
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("old_libg")
    parser.add_argument("new_libg")
    parser.add_argument("--probe", default="native_runner/probe")
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--overrides",
        default="",
        help="JSON file with verified manual corrections (hook + new_offset + evidence)",
    )
    args = parser.parse_args()

    old = Binary(args.old_libg)
    new = Binary(args.new_libg)
    anchors = parse_offsets(Path(args.probe))
    rows = [relocate(old, new, anchor) for anchor in anchors]

    # Second pass: discard matches that disagree with the local code shift and
    # try a delta-constrained search for the real function.
    by_name = {anchor.name: anchor for anchor in anchors}
    for row in rows:
        anchor = by_name[row["hook"]]
        if anchor.offset >= 0x17E6550:  # data anchor: different segment
            continue
        expected = _expected_delta(rows, anchor.offset)
        if expected is None:
            continue
        row["expected_delta"] = f"{expected:+#x}"
        if "new_offset" in row:
            delta = int(row["new_offset"], 16) - anchor.offset
            if abs(delta - expected) <= 0x2000:
                row["delta_verified"] = True
                continue
        found = _constrained_search(old, new, anchor, expected)
        if found is not None:
            address, size, relative = found
            row["new_offset"] = f"0x{address:08x}"
            row["delta"] = f"{address - anchor.offset:+#x}"
            row["window"] = size
            row["window_relative"] = relative
            row["confidence"] = "LIKELY"
            row["delta_constrained"] = True
        else:
            row["suspect"] = True

    counts: dict[str, int] = {}
    overrides = load_overrides(Path(args.overrides) if args.overrides else None)
    for row in rows:
        override = overrides.get(row["hook"])
        if override is not None:
            new_offset = int(override["new_offset"], 16)
            row["new_offset"] = f"0x{new_offset:08x}"
            row["delta"] = f"{new_offset - int(row['old_offset'], 16):+#x}"
            row["confidence"] = "MANUAL"
            row.pop("suspect", None)
            row["manual_evidence"] = override.get("evidence", "")
            if override.get("note"):
                row["manual_note"] = override["note"]
        counts[row["confidence"]] = counts.get(row["confidence"], 0) + 1
        flags = ""
        if row["confidence"] == "MANUAL":
            flags = " manual"
        elif row.get("delta_verified"):
            flags = " verified"
        elif row.get("delta_constrained"):
            flags = " constrained"
        elif row.get("suspect"):
            flags = " SUSPECT"
        print(
            f"{row['confidence']:<9} {row['hook']:<44} old={row['old_offset']} "
            f"new={row.get('new_offset', '-'):<12} delta={row.get('delta', '-'):<10} "
            f"exp={row.get('expected_delta', '-'):<10} win={row.get('window', '-')}{flags}"
        )
    print("\nsummary:", ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps({"summary": counts, "anchors": rows}, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
