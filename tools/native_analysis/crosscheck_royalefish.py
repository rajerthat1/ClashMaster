#!/usr/bin/env python3
"""Cross-check RoyaleFish's 16.635.1 x86_64 relocation table against our diff.

RoyaleFish's ``probe_x86_64_16635.diff`` is a port of *this exact client build*
to x86_64.  It ships a generated x86 -> ARM mapping for named anchors, e.g.

    constexpr std::uintptr_t kGoldenKnightHopOffset =
        0x10f76b0 /* arm 0x10a7230 */;

That is an independent second opinion on our ``offset-diff.json``: for every
named anchor we can compare the ARM offset they recovered with the one we
recovered.  This tool classifies each name as

    AGREE      both resolved, same offset
    CONFLICT   both resolved, different offset (list for manual review)
    RESOLVED   we had no offset (MISSING / unresolved AMBIGUOUS), they do
    NO-ANCHOR  named in the patch but not tracked in offset-diff.json

It also checks the candidate against the ARM binary (mapped, instruction
aligned, decodes) and against the patch's own ``logical_*.inc`` tables so an
internally inconsistent row is never adopted.

Usage:
    python3 tools/native_analysis/crosscheck_royalefish.py \
        --json-out docs/native/16.635.1/royalefish-crosscheck.json \
        --md-out docs/native/16.635.1/ROYALEFISH_CROSSCHECK.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cr_elf import Binary  # noqa: E402

NAMED_RE = re.compile(
    r"constexpr\s+std::uintptr_t\s+(k\w+)\s*=\s*(0x[0-9a-fA-F]+)\s*"
    r"/\*\s*arm\s+(0x[0-9a-fA-F]+)\s*\*/"
)
PAIR_RE = re.compile(r"\{0x([0-9a-fA-F]+),\s*0x([0-9a-fA-F]+)\}")
WINDOW_RE = re.compile(
    r"\{0x([0-9a-fA-F]+),\s*0x([0-9a-fA-F]+),\s*0x([0-9a-fA-F]+)\}"
)


@dataclass
class NamedMapping:
    name: str
    x86: int
    arm: int
    table_kind: str = ""
    table_ok: bool = False
    mapped: bool = False
    aligned: bool = False
    executable: bool = False
    first_instructions: list[str] = field(default_factory=list)

    @property
    def anchor(self) -> str:
        return self.name[:-len("Offset")] if self.name.endswith("Offset") else self.name


def parse_named(text: str) -> list[NamedMapping]:
    rows: list[NamedMapping] = []
    seen: set[tuple[str, int]] = set()
    for match in NAMED_RE.finditer(text):
        name = match.group(1)
        x86 = int(match.group(2), 16)
        arm = int(match.group(3), 16)
        # The same constant can appear twice (a deletion + an addition); keep one.
        key = (name, x86)
        if key in seen:
            continue
        seen.add(key)
        rows.append(NamedMapping(name=name, x86=x86, arm=arm))
    rows.sort(key=lambda row: row.name)
    return rows


def parse_tables(text: str) -> tuple[dict[int, int], dict[int, int], list[tuple[int, int, int]]]:
    funcs: dict[int, int] = {}
    offsets: dict[int, int] = {}
    windows: list[tuple[int, int, int]] = []

    funcs_block = text.split("kLogicalFuncs[]", 1)
    if len(funcs_block) == 2:
        body = funcs_block[1].split("};", 1)[0]
        for lo, hi in PAIR_RE.findall(body):
            funcs.setdefault(int(lo, 16), int(hi, 16))

    offsets_block = text.split("kLogicalOffsets[]", 1)
    if len(offsets_block) == 2:
        body = offsets_block[1].split("};", 1)[0]
        for lo, hi in PAIR_RE.findall(body):
            offsets.setdefault(int(lo, 16), int(hi, 16))

    windows_block = text.split("kLogicalDataWindows[]", 1)
    if len(windows_block) == 2:
        body = windows_block[1].split("};", 1)[0]
        for lo, hi, delta in WINDOW_RE.findall(body):
            windows.append((int(lo, 16), int(hi, 16), int(delta, 16)))

    return funcs, offsets, windows


def table_lookup(
    x86: int,
    funcs: dict[int, int],
    offsets: dict[int, int],
    windows: list[tuple[int, int, int]],
) -> tuple[str, int | None]:
    """Return (kind, mapped arm) using the patch's own generated tables."""
    if x86 in funcs:
        return "func", funcs[x86]
    if x86 in offsets:
        return "offset", offsets[x86]
    for lo, hi, delta in windows:
        if lo <= x86 < hi:
            return "data-window", x86 - delta
    return "", None


def annotate(rows: list[NamedMapping], funcs, offsets, windows, arm: Binary) -> None:
    for row in rows:
        kind, expected = table_lookup(row.x86, funcs, offsets, windows)
        row.table_kind = kind
        row.table_ok = expected == row.arm
        segment = arm.segment_for_va(row.arm)
        row.mapped = segment is not None
        row.aligned = row.arm % 4 == 0
        row.executable = bool(segment and segment.executable)
        if row.mapped and row.executable and row.aligned:
            raw = arm.read(row.arm, 16)
            row.first_instructions = [
                f"{ins.address:#x}: {ins.mnemonic} {ins.op_str}".rstrip()
                for ins in arm.dis.disasm(raw, row.arm)
            ][:4]


def load_diff(path: Path) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {row["hook"]: row for row in payload["anchors"]}


def classify(rows: list[NamedMapping], anchors: dict[str, dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {
        "agree": [],
        "conflict": [],
        "resolved": [],
        "no_anchor": [],
    }
    for row in rows:
        anchor = anchors.get(row.anchor)
        base = {
            "name": row.name,
            "anchor": row.anchor,
            "x86": f"0x{row.x86:08x}",
            "arm": f"0x{row.arm:08x}",
            "table_kind": row.table_kind,
            "table_ok": row.table_ok,
            "mapped": row.mapped,
            "aligned": row.aligned,
            "executable": row.executable,
            "insn": row.first_instructions,
        }
        if anchor is None:
            buckets["no_anchor"].append(base)
            continue
        ours = anchor.get("new_offset")
        our_offset = int(ours, 16) if ours else None
        base["ours"] = ours
        base["our_confidence"] = anchor.get("confidence")
        if our_offset is None:
            buckets["resolved"].append(base)
        elif our_offset == row.arm:
            buckets["agree"].append(base)
        else:
            base["delta"] = row.arm - our_offset
            buckets["conflict"].append(base)
    return buckets


def render_markdown(buckets, summary, arm_sha, x86_sha) -> str:
    lines = [
        "# RoyaleFish 16.635.1 relocation cross-check",
        "",
        "Generated by `tools/native_analysis/crosscheck_royalefish.py`.",
        "RoyaleFish's x86_64 probe ships a generated x86 -> ARM table for this",
        "client build; it is an independent second opinion on",
        "`docs/native/16.635.1/offset-diff.json`.",
        "",
        f"- ARM64 `libg.so` sha256 `{arm_sha}`",
        f"- x86_64 `libg.so` sha256 `{x86_sha}`",
        "",
        "| Bucket | Count |",
        "| --- | ---: |",
        f"| AGREE | {summary['agree']} |",
        f"| CONFLICT | {summary['conflict']} |",
        f"| RESOLVED (we had none) | {summary['resolved']} |",
        f"| NO-ANCHOR | {summary['no_anchor']} |",
        f"| **named mappings parsed** | **{summary['total']}** |",
        "",
    ]

    lines += [
        "## Resolved anchors (adopt candidates)",
        "",
        "These were `MISSING` or unresolved `AMBIGUOUS` in our report and now have",
        "an ARM offset from the community port. Each row is checked against the",
        "patch's own table and the ARM binary.",
        "",
        "| Anchor | Our state | ARM offset | Table | Mapped | Kind | First insn |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in buckets["resolved"]:
        insn = row["insn"][0].split(" ", 1)[-1] if row["insn"] else "-"
        kind = "code" if row["executable"] else "data"
        lines.append(
            f"| `{row['anchor']}` | {row['our_confidence']} | `{row['arm']}` | "
            f"{row['table_kind'] or '-'}{'' if row['table_ok'] else ' (!)'} | "
            f"{'yes' if row['mapped'] else 'NO'} | {kind} | `{insn}` |"
        )

    lines += [
        "",
        "## Conflicts (manual review)",
        "",
        "Both sides resolved the name, but to different offsets. Small deltas are",
        "usually entry-point conventions (branch thunk vs body, or the guard before",
        "the prologue); large deltas mean one side is wrong.",
        "",
        "| Anchor | Ours | Theirs | Δ | Our conf. | Their insn |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in buckets["conflict"]:
        insn = row["insn"][0].split(" ", 1)[-1] if row["insn"] else "-"
        lines.append(
            f"| `{row['anchor']}` | `{row['ours']}` | `{row['arm']}` | "
            f"{row['delta']:+#x} | {row['our_confidence']} | `{insn}` |"
        )

    lines += [
        "",
        "## Agreements",
        "",
        "| Anchor | Offset | Our conf. |",
        "| --- | --- | --- |",
    ]
    for row in buckets["agree"]:
        lines.append(f"| `{row['anchor']}` | `{row['arm']}` | {row['our_confidence']} |")

    if buckets["no_anchor"]:
        lines += [
            "",
            "## Named mappings with no tracked anchor",
            "",
            "| Name | x86 | ARM | Table |",
            "| --- | --- | --- | --- |",
        ]
        for row in buckets["no_anchor"]:
            lines.append(
                f"| `{row['name']}` | `{row['x86']}` | `{row['arm']}` | {row['table_kind'] or '-'} |"
            )

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--patch",
        default=".resource/RoyaleFish/patches/probe_x86_64_16635.diff",
    )
    parser.add_argument("--diff", default="docs/native/16.635.1/offset-diff.json")
    parser.add_argument(
        "--arm", default=".resource/native/16.635.1/reference/libg.so"
    )
    parser.add_argument("--x86", default=".resource/Clashback/game/engine/libg.so")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    args = parser.parse_args()

    patch = Path(args.patch)
    text = patch.read_text(encoding="utf-8", errors="replace")
    rows = parse_named(text)
    funcs, offsets, windows = parse_tables(text)
    arm = Binary(args.arm)
    x86 = Binary(args.x86)
    annotate(rows, funcs, offsets, windows, arm)

    anchors = load_diff(Path(args.diff))
    buckets = classify(rows, anchors)
    summary = {
        key: len(value) for key, value in buckets.items()
    }
    summary["total"] = len(rows)

    print(f"parsed {len(rows)} named mappings")
    for key in ("agree", "conflict", "resolved", "no_anchor"):
        print(f"  {key:<9} {len(buckets[key])}")
    for key in ("resolved", "conflict"):
        print(f"\n[{key}]")
        for row in buckets[key]:
            print(
                f"  {row['anchor']:<42} ours={row['ours']} arm={row['arm']} "
                f"table={row['table_kind'] or '-'}{'' if row['table_ok'] else '(!)'}"
            )

    payload = {
        "patch": str(patch),
        "arm_sha256": arm.sha256(),
        "x86_sha256": x86.sha256(),
        "summary": summary,
        "buckets": buckets,
    }
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {out}")
    if args.md_out:
        out = Path(args.md_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            render_markdown(buckets, summary, arm.sha256(), x86.sha256()),
            encoding="utf-8",
        )
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
