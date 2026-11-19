"""The generated 16.635.1 build header must stay pinned to the target binary.

These tests are the guard rail between static analysis and the native probe:
every emitted code anchor is re-read from the reference libg.so, every
unresolved anchor is explicitly marked, and the required-anchor policy refuses
to emit a sentinel for a core function the runner path calls directly.
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from native_runner.paths import REPOSITORY_ROOT

PROBE_ROOT = REPOSITORY_ROOT / "native_runner" / "probe"
HEADER = PROBE_ROOT / "build_16_635_1_layout.h"
HEADER_X86 = PROBE_ROOT / "build_16_635_1_layout_x86.h"
PROBE_SOURCE = PROBE_ROOT / "cr_replay_probe.cpp"
REFERENCE = REPOSITORY_ROOT / ".resource" / "native" / "16.635.1" / "reference" / "libg.so"
OFFSET_DIFF = REPOSITORY_ROOT / "docs" / "native" / "16.635.1" / "offset-diff.json"
TOOLS = REPOSITORY_ROOT / "tools" / "native_analysis"

# Vtables used to classify objects before any rich hook ring is installed.
# Both ABI headers must expose the same set of names; the ARM values are pinned
# to the relocation table below.  A stale entry silently degrades the live
# observation (the native notes), so drift must fail here instead of on device.
RICH_VTABLES = (
    "kBuffAssetVtableOffset",
    "kProjectileVtableOffset",
    "kCharacterVtableOffset",
    "kCaptureCharacterActionDataVtableOffset",
    "kCaptureCharacterRuntimeVtableOffset",
    "kThresholdRelocationActionDataVtableOffset",
    "kThresholdRelocationRuntimeVtableOffset",
    "kPeriodicAttackModifierActionDataVtableOffset",
    "kPeriodicAttackModifierRuntimeVtableOffset",
)

_OFFSET = re.compile(r"inline constexpr std::uintptr_t (\w+) = 0x([0-9a-fA-F]+);")
_PROLOGUE = re.compile(r"inline constexpr std::uint8_t (\w+)\[16\] = \{([^}]*)\};")
_RESOLVED = re.compile(r"inline constexpr bool (\w+Resolved) = (true|false);")
_UNRESOLVED = re.compile(r"inline constexpr const char \*kUnresolvedAnchors\[\] = \{([^}]*)\};")


def read_header() -> str:
    assert HEADER.is_file(), f"missing generated header: {HEADER}"
    return HEADER.read_text(encoding="utf-8")


def mapped_bytes(data: bytes, virtual_address: int, size: int) -> bytes:
    """Read a virtual address range through ELF64 program headers."""
    program_offset = struct.unpack_from("<Q", data, 0x20)[0]
    entry_size = struct.unpack_from("<H", data, 0x36)[0]
    entry_count = struct.unpack_from("<H", data, 0x38)[0]
    for index in range(entry_count):
        offset = program_offset + index * entry_size
        p_type, _flags, p_offset, p_vaddr, _paddr, p_filesz, _memsz, _align = struct.unpack_from(
            "<IIQQQQQQ", data, offset
        )
        if p_type != 1:  # PT_LOAD
            continue
        if p_vaddr <= virtual_address and virtual_address + size <= p_vaddr + p_filesz:
            file_offset = p_offset + (virtual_address - p_vaddr)
            return data[file_offset : file_offset + size]
    raise AssertionError(f"0x{virtual_address:08x} (+{size}) is not mapped by a PT_LOAD segment")


def test_every_emitted_prologue_matches_the_reference_binary() -> None:
    if not REFERENCE.is_file():
        pytest.skip("16.635.1 reference libg.so is not present")
    header = read_header()
    offsets = {name: int(value, 16) for name, value in _OFFSET.findall(header)}
    prologues = {
        name: [int(value.strip(), 16) for value in values.split(",") if value.strip()]
        for name, values in _PROLOGUE.findall(header)
    }
    resolved = {name: value == "true" for name, value in _RESOLVED.findall(header)}

    binary = REFERENCE.read_bytes()
    assert prologues, "header emitted no prologues"

    for prologue_name, expected in prologues.items():
        anchor = prologue_name.removesuffix("Prologue")
        offset_name = f"{anchor}Offset"
        assert offset_name in offsets, f"{prologue_name} has no {offset_name}"
        assert len(expected) == 16, f"{prologue_name} is not 16 bytes"
        flags = resolved.get(f"{anchor}Resolved")
        assert flags is not False, f"{prologue_name} is emitted for an unresolved anchor"
        actual = list(mapped_bytes(binary, offsets[offset_name], 16))
        assert actual == expected, (
            f"{anchor} prologue drift at +0x{offsets[offset_name]:08x}: "
            f"header={expected} target={actual}"
        )


def test_unresolved_anchors_are_explicit_and_never_have_prologues() -> None:
    header = read_header()
    match = _UNRESOLVED.search(header)
    assert match is not None, "header is missing kUnresolvedAnchors"
    # ``{nullptr}`` is the C++ sentinel for "no unresolved anchors".
    names = [
        name.strip().strip('"')
        for name in match.group(1).split(",")
        if name.strip() and name.strip() != "nullptr"
    ]
    resolved = {name: value == "true" for name, value in _RESOLVED.findall(header)}
    prologues = {name.removesuffix("Prologue") for name, _values in _PROLOGUE.findall(header)}

    for name in names:
        key = f"{name}Resolved"
        assert key in resolved, f"{name} is listed unresolved but has no {key} flag"
        assert resolved[key] is False, f"{name} is listed unresolved but marked resolved"
        assert name not in prologues, f"{name} is unresolved but emitted a prologue"


def test_probe_selects_the_generated_header_and_keeps_defaults_isolated() -> None:
    source = PROBE_SOURCE.read_text(encoding="utf-8")
    assert '#include "build_16_635_1_layout.h"' in source
    assert "CR_PROBE_TARGET_16_635_1" in source
    # The 16.635 branch must source the runner anchors from the header, not
    # repeat the literals.  Guard the two most load-bearing ones.
    assert "probe_target::kNativeReplayRunnerOffset" in source
    assert "probe_target::kGameStateStepOffset" in source
    # Unresolved core anchors must be gated instead of blindly bound.
    assert "kJsonMemberFindResolved" in source
    assert "kLogicCommandParseResolved" in source
    assert "kLocationTileMapEnsureResolved" in source
    # Rich-classification vtables come from the generated header on every
    # architecture, never only on x86_64 (the A51 observation regression in
    # the native notes).
    for name in RICH_VTABLES:
        assert f"probe_target::{name}" in source
    assert "CR_PROBE_TARGET_16_635_1) && defined(__x86_64__)" not in source


def test_rich_vtable_anchors_are_shared_by_both_abi_headers() -> None:
    arm_offsets = {name: int(value, 16) for name, value in _OFFSET.findall(read_header())}
    missing_arm = [name for name in RICH_VTABLES if name not in arm_offsets]
    assert not missing_arm, f"ARM64 header is missing {missing_arm}"
    if not HEADER_X86.is_file():
        pytest.skip("x86_64 layout header is not present")
    x86_offsets = {name: int(value, 16) for name, value in _OFFSET.findall(HEADER_X86.read_text(encoding="utf-8"))}
    missing_x86 = [name for name in RICH_VTABLES if name not in x86_offsets]
    assert not missing_x86, f"x86_64 header is missing {missing_x86}"
    for name in RICH_VTABLES:
        assert arm_offsets[name] % 8 == 0
        assert x86_offsets[name] % 8 == 0


def test_arm_rich_vtable_anchors_match_the_relocation_table() -> None:
    if not OFFSET_DIFF.is_file():
        pytest.skip("16.635.1 offset-diff.json is not present")
    payload = json.loads(OFFSET_DIFF.read_text(encoding="utf-8"))
    rows = {row["hook"]: row for row in payload["anchors"]}
    offsets = {name: int(value, 16) for name, value in _OFFSET.findall(read_header())}
    for name in RICH_VTABLES:
        hook = name.removesuffix("Offset")
        assert hook in rows, f"{hook} is not in offset-diff.json"
        expected = int(rows[hook]["new_offset"], 16)
        assert offsets[name] == expected, f"{name} drifted from offset-diff.json"


def test_header_compiles_as_cpp17(tmp_path: Path) -> None:
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ is unavailable for header validation")
    source = tmp_path / "check_header.cpp"
    source.write_text(
        '#include "build_16_635_1_layout.h"\n'
        "static_assert(cr_native_16_635_1::kRunnerHooks[0].offset ==\n"
        "              cr_native_16_635_1::kNativeReplayRunnerOffset);\n"
        "static_assert(cr_native_16_635_1::kTouchHook.offset ==\n"
        "              cr_native_16_635_1::kGameAppTouchOffset);\n"
        "int main() { return 0; }\n",
        encoding="utf-8",
    )
    subprocess.run(
        [compiler, "-std=c++17", "-fsyntax-only", "-I", str(PROBE_ROOT), str(source)],
        check=True,
        capture_output=True,
    )


def test_required_core_anchor_policy_fails_loud() -> None:
    pytest.importorskip("capstone")
    sys.path.insert(0, str(TOOLS))
    import gen_layout  # type: ignore[import-not-found]

    rows = {
        "kGameStateConstructor": {
            "hook": "kGameStateConstructor",
            "confidence": "AMBIGUOUS",
            "old_offset": "0x01060ba8",
        }
    }
    with pytest.raises(SystemExit):
        gen_layout.render_core_anchor(
            "kGameStateConstructor", rows, binary=None, data=False, allow_unresolved=False
        )
