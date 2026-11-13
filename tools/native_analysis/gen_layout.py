#!/usr/bin/env python3
"""Emit a build-pinned probe layout header from verified diff rows.

The header is the bridge between the static analysis report and the native
probe source.  It only contains anchors whose new offset was resolved (EXACT,
LIKELY or MANUAL) and whose 16-byte prologue is read straight out of the target
binary.  An anchor without a verified prologue is refused instead of emitted.

Anchors that are part of the runner path but are still AMBIGUOUS or MISSING are
emitted as sentinels (``kUnavailableOffset``) plus a ``*Resolved = false`` flag
so the probe compiles against one include and fails closed instead of falling
back to a 15.535.13 offset.

Usage:
    python3 tools/native_analysis/gen_layout.py \
        .resource/native/16.635.1/reference/libg.so \
        --diff docs/native/16.635.1/offset-diff.json \
        --fields docs/native/16.635.1/field-offsets.json \
        --out native_runner/probe/build_16_635_1_layout.h

The runner hook list is the same ordered system the probe installs in
``cr_replay_probe.cpp``; the touch JNI entry is emitted separately.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cr_elf import Binary  # noqa: E402

CLIENT_VERSION = "16.635.1"
NAMESPACE = "cr_native_16_635_1"
RUNNER_HOOKS = (
    "kNativeReplayRunner",
    "kReplayJsonSetter",
    "kGameAppPendingEventSetter",
    "kReplayBattleControllerLifecycle",
    "kReplayBattleControllerFullUpdate",
    "kReplayBattleControllerClock",
    "kReplayBattleControllerAlternateUpdate",
    "kGameStateLoad",
    "kGameStateStep",
)
TOUCH_HOOK = "kGameAppTouch"
# Non-hook functions the cold and native-render paths call directly.  These are
# emitted as plain offsets; the probe does not install instruction hooks there.
CORE_CODE_ANCHORS = (
    "kReplayManagerGetter",
    "kLibgStringFromUtf8",
    "kGameAppGetter",
    "kGameAppPauseGuard",
    "kReplayClockGlobalPauseGuard",
    "kGameStateConstructor",
    "kGameStateSetup",
    "kGameStateDestroy",
    "kGameStateBaseDestroy",
    "kGameStateTick",
    "kGameStateDeserializeHeader",
    "kGameStateDeserializeBody",
    "kByteStreamResetRead",
    "kByteStreamFlipRead",
    "kByteStreamResetChecksum",
    "kJsonParse",
    "kStateSnapshot",
    "kStateSnapshotDelete",
    "kCardSelectionBuild",
    "kLogicCommandQueueConstructor",
    "kLogicCommandQueueDestroy",
    "kLogicCommandAppend",
    "kLogicDataByGlobalId",
    "kGameStateNew",
    "kGameStateDelete",
    "kGameStateContextA",
    "kGameStateContextB",
    "kLogicDataTypeGetter",
    "kProjectileKindGetter",
)
# Core functions that are not resolved yet.  They are part of the fail-closed
# sentinel set so the 16.635 build never points at a 15.535 address.
CORE_UNRESOLVED_ANCHORS = (
    "kJsonMemberFind",
    "kLogicCommandParse",
    "kLocationTileMapEnsure",
    "kLocationTileMapLookup",
    "kBuffAssetClassNameGetter",
)
# Data anchors: these are GOT/global slots, not code.  Their contents are the
# relocated pointer/value, so no 16-byte fingerprint is emitted for them.
CORE_DATA_ANCHORS = (
    "kEglSwapBuffersGot",
    "kGameStateContextRootSlot",
)
ALLOWED_CONFIDENCE = {"EXACT", "LIKELY", "MANUAL"}


def load_hooks(diff_path: Path) -> dict[str, dict]:
    payload = json.loads(diff_path.read_text(encoding="utf-8"))
    rows = payload["anchors"] if isinstance(payload, dict) else payload
    return {row["hook"]: row for row in rows}


def prologue_bytes(binary: Binary, offset: int) -> list[int]:
    try:
        raw = binary.read(offset, 16)
    except SystemExit as error:
        raise SystemExit(f"0x{offset:08x} is not mapped: {error}")
    if len(raw) != 16:
        raise SystemExit(f"0x{offset:08x} is short ({len(raw)} bytes)")
    if not any(raw):
        raise SystemExit(f"0x{offset:08x} has an all-zero prologue; refusing to emit")
    return list(raw)


def format_prologue(values: list[int]) -> str:
    return ", ".join(f"0x{value:02x}" for value in values)


def is_resolved(row: dict | None) -> bool:
    return (
        row is not None
        and row.get("confidence") in ALLOWED_CONFIDENCE
        and "new_offset" in row
    )


def _refuse_tiny_shape_mismatch(name: str, row: dict | None) -> None:
    """Reject an auto-mapped tiny getter whose instruction shape disagrees.

    ``diff_builds.py`` sets ``tiny_shape_mismatch`` when a two-instruction
    accessor was mapped to a neighbouring accessor in the same cluster.  Only a
    human-verified ``MANUAL`` override clears the flag.
    """
    if row is None or not row.get("tiny_shape_mismatch"):
        return
    if row.get("confidence") == "MANUAL":
        return
    raise SystemExit(
        f"anchor {name} is a tiny getter mapped with a mismatched instruction shape "
        f"(old={row.get('old_shape')} new={row.get('new_shape')}); "
        f"confirm it in manual-overrides.json before generating the layout"
    )


def resolve_anchor(name: str, rows: dict[str, dict], binary: Binary) -> tuple[int, list[int], str]:
    row = rows.get(name)
    if not is_resolved(row):
        raise SystemExit(
            f"anchor {name} is not resolved (confidence={None if row is None else row.get('confidence')})"
        )
    _refuse_tiny_shape_mismatch(name, row)
    offset = int(row["new_offset"], 16)
    if offset % 4:
        raise SystemExit(f"anchor {name} offset 0x{offset:08x} is not instruction-aligned")
    return offset, prologue_bytes(binary, offset), row["confidence"]


def constant_name(hook: str, suffix: str) -> str:
    return f"{hook}{suffix}"


def render_core_anchor(
    hook: str, rows: dict[str, dict], binary: Binary, *, data: bool, allow_unresolved: bool
) -> tuple[list[str], bool]:
    """Render one core anchor, returning (lines, resolved)."""
    row = rows.get(hook)
    if not is_resolved(row):
        if not allow_unresolved:
            raise SystemExit(
                f"required core anchor {hook} is not resolved "
                f"(confidence={None if row is None else row.get('confidence')})"
            )
        lines = [
            f"inline constexpr std::uintptr_t {constant_name(hook, 'Offset')} = kUnavailableOffset;",
            f"inline constexpr bool {hook}Resolved = false;",
        ]
        return lines, False

    _refuse_tiny_shape_mismatch(hook, row)
    offset = int(row["new_offset"], 16)
    if offset % 4:
        raise SystemExit(f"anchor {hook} offset 0x{offset:08x} is not instruction-aligned")
    lines = [
        f"inline constexpr std::uintptr_t {constant_name(hook, 'Offset')} = 0x{offset:08x};",
        f"inline constexpr bool {hook}Resolved = true;",
    ]
    if not data:
        prologue = prologue_bytes(binary, offset)
        lines.insert(
            1,
            f"inline constexpr std::uint8_t {constant_name(hook, 'Prologue')}[16] = "
            f"{{{format_prologue(prologue)}}};",
        )
    return lines, True


def render(build: Binary, rows: dict[str, dict], fields: list[dict], source: str) -> str:
    anchors = []
    for hook in RUNNER_HOOKS + (TOUCH_HOOK,):
        offset, prologue, confidence = resolve_anchor(hook, rows, build)
        anchors.append((hook, offset, prologue, confidence))

    lines = [
        f"// Generated by {source} -- do not edit by hand.",
        f"// Client {CLIENT_VERSION}; libg.so sha256 {build.sha256()}.",
        "// Every offset below was resolved from the 15.535.13 reference build and",
        "// its 16-byte prologue was read from the target binary. Verify hooks on a",
        "// native arm64 device before trusting a live run.",
        "#pragma once",
        "",
        "#include <cstddef>",
        "#include <cstdint>",
        "",
        f"namespace {NAMESPACE} {{",
        "",
        f'inline constexpr char kClientVersion[] = "{CLIENT_VERSION}";',
        f'inline constexpr char kLibgSha256[] = "{build.sha256()}";',
        f'inline constexpr char kBuildId[] = "{build.build_id()}";',
        f"inline constexpr std::uintptr_t kLibgSize = {len(build.data)};",
        "inline constexpr std::uintptr_t kUnavailableOffset = static_cast<std::uintptr_t>(-1);",
        "",
        "struct HookAnchor {",
        "  const char *name;",
        "  std::uintptr_t offset;",
        "  const std::uint8_t (&prologue)[16];",
        "};",
        "",
        "// Runner hook anchors (installed as instruction hooks).",
    ]
    for hook, offset, prologue, _confidence in anchors:
        lines.append(f"inline constexpr std::uintptr_t {constant_name(hook, 'Offset')} = 0x{offset:08x};")
        lines.append(
            f"inline constexpr std::uint8_t {constant_name(hook, 'Prologue')}[16] = "
            f"{{{format_prologue(prologue)}}};"
        )

    lines.extend(["", "// Core code anchors called directly by the runner path."])
    unresolved: list[str] = []
    for hook in CORE_CODE_ANCHORS:
        anchor_lines, resolved = render_core_anchor(hook, rows, build, data=False, allow_unresolved=False)
        lines.extend(anchor_lines)
        if not resolved:
            unresolved.append(hook)

    if CORE_UNRESOLVED_ANCHORS:
        lines.extend(
            [
                "",
                "// Core anchors still AMBIGUOUS/MISSING.  The probe must fail closed",
                "// (leave the pointer null) instead of using a 15.535.13 address.",
            ]
        )
        for hook in CORE_UNRESOLVED_ANCHORS:
            anchor_lines, resolved = render_core_anchor(hook, rows, build, data=False, allow_unresolved=True)
            lines.extend(anchor_lines)
            if not resolved:
                unresolved.append(hook)

    lines.extend(["", "// Data anchors (GOT/global slots; read the slot, never fingerprint it)."])
    for hook in CORE_DATA_ANCHORS:
        anchor_lines, resolved = render_core_anchor(hook, rows, build, data=True, allow_unresolved=False)
        lines.extend(anchor_lines)
        if not resolved:
            unresolved.append(hook)

    lines.extend(["", "// Layout fields confirmed by the 16.635.1 disassembly."])
    for field in fields:
        lines.append(
            f"inline constexpr std::size_t {field['name']} = {field['new']};  // was {field['old']}"
        )

    lines.extend(["", "// Anchors the probe intentionally leaves unavailable on this build."])
    if unresolved:
        entries = ", ".join(f'"{name}"' for name in sorted(set(unresolved)))
        lines.append(f"inline constexpr const char *kUnresolvedAnchors[] = {{{entries}}};")
    else:
        lines.append("inline constexpr const char *kUnresolvedAnchors[] = {nullptr};")

    lines.extend(["", "inline constexpr HookAnchor kRunnerHooks[] = {"])
    for hook in RUNNER_HOOKS:
        lines.append(f'    {{"{hook}", {constant_name(hook, "Offset")}, {constant_name(hook, "Prologue")}}},')
    lines.extend(
        [
            "};",
            "",
            f'inline constexpr HookAnchor kTouchHook{{"{TOUCH_HOOK}", '
            f"{constant_name(TOUCH_HOOK, 'Offset')}, {constant_name(TOUCH_HOOK, 'Prologue')}}};",
            "",
            f"}}  // namespace {NAMESPACE}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("new_libg")
    parser.add_argument("--diff", default="docs/native/16.635.1/offset-diff.json")
    parser.add_argument("--fields", default="docs/native/16.635.1/field-offsets.json")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    build = Binary(args.new_libg)
    rows = load_hooks(Path(args.diff))
    fields = json.loads(Path(args.fields).read_text(encoding="utf-8"))["fields"]
    header = render(build, rows, fields, "tools/native_analysis/gen_layout.py")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(header, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(header)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
