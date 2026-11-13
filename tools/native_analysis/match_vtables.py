#!/usr/bin/env python3
"""Match probe vtable anchors across two libg.so builds.

Vtable slots in the file are zeros; the real function pointers live in
R_AARCH64_RELATIVE relocations.  This tool:

1. reads an old vtable from the old build's relocations,
2. relocates each entry to the new build by exact byte-window search,
3. finds the new vtable by looking for the same sequence of relocated entries
   in the new build's relocation table.

Usage:
    python3 tools/native_analysis/match_vtables.py OLD NEW \
        --out docs/native/16.635.1/vtable-diff.json
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cr_elf import Binary  # noqa: E402

OFFSET_RE = re.compile(
    r"(?:constexpr|inline\s+constexpr)\s+std::uintptr_t\s+(\w+)Offset\s*=\s*(0x[0-9a-fA-F]+);"
)
R_AARCH64_RELATIVE = 1027


def relative_relocations(path: str) -> dict[int, int]:
    data = Path(path).read_bytes()
    e_phoff = struct.unpack_from("<Q", data, 0x20)[0]
    e_phentsize = struct.unpack_from("<H", data, 0x36)[0]
    e_phnum = struct.unpack_from("<H", data, 0x38)[0]
    segments = []
    dynamic = None
    for index in range(e_phnum):
        base = e_phoff + index * e_phentsize
        p_type, _flags, p_offset, p_vaddr, _paddr, p_filesz = struct.unpack_from("<IIQQQQ", data, base)
        if p_type == 1:
            segments.append((p_offset, p_vaddr, p_filesz))
        elif p_type == 2:
            dynamic = (p_offset, struct.unpack_from("<Q", data, base + 32)[0])

    def va_to_offset(va: int) -> int | None:
        for offset, vaddr, filesz in segments:
            if vaddr <= va < vaddr + filesz:
                return offset + va - vaddr
        return None

    rela = relasz = relaent = 0
    for cursor in range(dynamic[0], dynamic[0] + dynamic[1], 16):
        tag, value = struct.unpack_from("<QQ", data, cursor)
        if tag == 0:
            break
        if tag == 7:
            rela = value
        elif tag == 8:
            relasz = value
        elif tag == 9:
            relaent = value
    offset = va_to_offset(rela)
    result: dict[int, int] = {}
    for index in range(relasz // relaent):
        r_offset, r_info, r_addend = struct.unpack_from("<QQq", data, offset + index * relaent)
        if (r_info & 0xFFFFFFFF) == R_AARCH64_RELATIVE:
            result[r_offset] = r_addend
    return result


def parse_vtables(probe: Path) -> list[tuple[str, int, int | None]]:
    found: dict[str, int] = {}
    for path in sorted(probe.rglob("*")):
        if path.suffix not in {".h", ".inc", ".cpp"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, value in OFFSET_RE.findall(text):
            if name.endswith("Vtable"):
                found.setdefault(name, int(value, 16))
    ordered = sorted(found.items(), key=lambda item: item[1])
    result = []
    for index, (name, address) in enumerate(ordered):
        next_address = ordered[index + 1][1] if index + 1 < len(ordered) else None
        # Probe anchors include static-data vtables in the middle; only use the
        # next anchor as a bound when it is close enough to be the neighbour.
        if next_address is not None and next_address - address > 0x1000:
            next_address = None
        result.append((name, address, next_address))
    return result


class Relocator:
    def __init__(self, old: Binary, new: Binary):
        self.old = old
        self.new = new
        self.cache: dict[int, int | None] = {}

    def relocate(self, old_address: int) -> int | None:
        if old_address in self.cache:
            return self.cache[old_address]
        result = None
        for size in (96, 64, 48, 40, 32, 28, 24):
            try:
                needle = self.old.read(old_address, size)
            except SystemExit:
                break
            if len(needle) < size:
                break
            start = 0
            hits = []
            while len(hits) < 3:
                index = self.new.data.find(needle, start)
                if index < 0:
                    break
                hits.append(index)
                start = index + 1
            if len(hits) == 1:
                result = hits[0]
                break
        self.cache[old_address] = result
        return result


def read_vtable(
    relocations: dict[int, int],
    address: int,
    code_ranges: list[tuple[int, int]],
    limit: int = 128,
    end_address: int | None = None,
) -> list[int]:
    if end_address is not None:
        limit = min(limit, max(1, (end_address - address) // 8))
    def is_code(value: int) -> bool:
        return any(start <= value < end for start, end in code_ranges)

    entries: list[int] = []
    zero_run = 0
    for index in range(limit):
        slot = address + index * 8
        if slot not in relocations:
            zero_run += 1
            entries.append(0)
            if zero_run >= 3:
                break
            continue
        value = relocations[slot]
        if not is_code(value):
            # The table ended and a neighbouring object started.
            break
        zero_run = 0
        entries.append(value)
    while entries and entries[-1] == 0:
        entries.pop()
    return entries


def lcs_align(old: list[int], new: list[int]) -> list[tuple[int, int]]:
    """Longest common subsequence; returns aligned (old_index, new_index) pairs."""
    n, m = len(old), len(new)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row = table[i]
        below = table[i + 1]
        value = old[i]
        for j in range(m - 1, -1, -1):
            if value == new[j] and value:
                row[j] = below[j + 1] + 1
            else:
                row[j] = max(below[j], row[j + 1])
    pairs = []
    i = j = 0
    while i < n and j < m:
        if old[i] == new[j] and old[i]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("old_libg")
    parser.add_argument("new_libg")
    parser.add_argument("--probe", default="native_runner/probe")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    old = Binary(args.old_libg)
    new = Binary(args.new_libg)
    old_relocs = relative_relocations(args.old_libg)
    new_relocs = relative_relocations(args.new_libg)
    relocator = Relocator(old, new)

    # inverse map: addend -> data addresses (sorted)
    inverse: dict[int, list[int]] = {}
    for address, addend in sorted(new_relocs.items()):
        inverse.setdefault(addend, []).append(address)
    new_code = [(segment.vaddr, segment.end) for segment in new.segments if segment.executable]

    def is_new_code(value: int) -> bool:
        return any(start <= value < end for start, end in new_code)

    old_code = [(segment.vaddr, segment.end) for segment in old.segments if segment.executable]
    report = []
    for name, address, end_address in parse_vtables(Path(args.probe)):
        entries = read_vtable(old_relocs, address, old_code, end_address=end_address)
        mapped = [relocator.relocate(entry) if entry else None for entry in entries]
        entry = {
            "name": name,
            "old_offset": f"0x{address:08x}",
            "slots": len(entries),
            "mapped_slots": sum(1 for value in mapped if value is not None),
        }

        # Vote for the new table base: entry i at new address v implies the
        # table begins at v - i*8.  The true base collects votes from many
        # entries; other tables that share a few functions collect fewer.
        from collections import Counter

        votes: Counter[int] = Counter()
        for index, value in enumerate(mapped[:48]):
            if value is None:
                continue
            for location in inverse.get(value, [])[:64]:
                votes[location - index * 8] += 1

        best = None
        covered = sum(1 for value in mapped if value is not None)
        old_slots = [(index, value) for index, value in enumerate(mapped) if value is not None]
        old_sequence = [value for _index, value in old_slots]
        for base, vote_count in votes.most_common(24):
            if not covered:
                continue
            new_sequence = [new_relocs.get(base + index * 8, 0) for index in range(len(mapped) + 33)]
            pairs = lcs_align(old_sequence, new_sequence)
            if not pairs:
                continue
            shifts = [j - old_slots[i][0] for i, j in pairs]
            shift = max(set(shifts), key=shifts.count)
            adjusted_base = base + shift * 8
            matches = len(pairs)
            score = matches / covered
            key = (matches, score, -abs(shift), vote_count)
            if best is None or key > best[0]:
                best = (key, base, matches, score, shift)
        if best is not None:
            _key, base, matches, score, shift = best
            entry["score"] = round(score, 3)
            entry["matched_slots"] = matches
            entry["slot_shift"] = shift
            base += shift * 8
            if matches >= 6 and score >= 0.55:
                entry["new_offset"] = f"0x{base:08x}"
                entry["delta"] = f"{base - address:+#x}"
                entry["confidence"] = "EXACT" if score >= 0.85 and matches >= 10 else "LIKELY"
        entry["mapped_entries"] = [None if value is None else f"0x{value:08x}" for value in mapped]
        report.append(entry)
        status = entry.get("confidence", "UNMAPPED")
        print(f"{status:<9} {name:<44} old={entry['old_offset']} new={entry.get('new_offset', '-'):<12} "
              f"slots={entry['slots']} mapped={entry['mapped_slots']} "
              f"matched={entry.get('matched_slots', 0)} score={entry.get('score', 0)}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
