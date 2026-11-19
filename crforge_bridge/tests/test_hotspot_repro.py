"""The C2 reproducer's classification: a pass only counts if ``of`` was compiled at level 4.

Session 49 left one rented box's HotSpot miscompile of ``SeatIds.of`` as the only
evidence for a JVM defect.  ``tools/crforge/hotspot_repro.py`` turns that into a
runnable check; this file pins the reading of its arm table, so an arm that never
reached C2 can never be reported as "the host is clean".
"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "crforge"
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import hotspot_repro  # noqa: E402  (the tool lives outside the package)


def _arm(name: str, *, crashed: bool = False, level4: bool = True, timed_out: bool = False) -> dict[str, object]:
    return {
        "arm": name,
        "crashed": crashed,
        "c2_level4_compiled": level4,
        "timed_out": timed_out,
    }


def test_a_crash_with_clean_controls_is_the_reproduction() -> None:
    arms = [_arm("c2", crashed=True), _arm("c1", level4=False), _arm("interpreter", level4=False)]
    verdict = hotspot_repro._verdict(arms, bytecode_identical=True)
    assert verdict["result"] == "reproduced"
    assert verdict["crashes"] == 1
    assert verdict["control_crashes"] == 0


def test_a_pass_counts_only_when_c2_compiled_the_method() -> None:
    arms = [_arm("c2", level4=False), _arm("c1", level4=False), _arm("interpreter", level4=False)]
    assert hotspot_repro._verdict(arms, bytecode_identical=True)["result"] == "inconclusive"


def test_a_clean_c2_pass_with_the_engine_bytecode_is_a_negative() -> None:
    arms = [_arm("c2"), _arm("c2"), _arm("c1", level4=False), _arm("interpreter", level4=False)]
    verdict = hotspot_repro._verdict(arms, bytecode_identical=True)
    assert verdict["result"] == "not reproduced"
    assert verdict["c2_level4_compiled"] == 2
    assert verdict["c2_arms"] == 2


def test_a_reproducer_whose_bytecode_is_not_the_engine_s_cannot_conclude() -> None:
    arms = [_arm("c2"), _arm("c1", level4=False), _arm("interpreter", level4=False)]
    assert hotspot_repro._verdict(arms, bytecode_identical=False)["result"] == "inconclusive"


def test_a_timeout_is_not_counted_as_a_pass() -> None:
    arms = [_arm("c2", timed_out=True), _arm("c1", level4=False), _arm("interpreter", level4=False)]
    assert hotspot_repro._verdict(arms, bytecode_identical=True)["result"] == "inconclusive"


def test_a_crashing_control_arm_invalidates_the_run() -> None:
    arms = [_arm("c2", crashed=True), _arm("c1", crashed=True, level4=False)]
    assert hotspot_repro._verdict(arms, bytecode_identical=True)["result"] == "inconclusive"


def test_the_crash_classifier_reads_a_signal_and_a_fatal_log() -> None:
    assert hotspot_repro._crashed({"returncode": -11, "stderr": ""})
    assert hotspot_repro._crashed({"returncode": 139, "stderr": ""})
    assert hotspot_repro._crashed({"returncode": 1, "stderr": "A fatal error has been detected by the Java Runtime"})
    assert not hotspot_repro._crashed({"returncode": 0, "stderr": ""})


def test_the_artifact_carries_the_cpu_it_ran_on() -> None:
    # Session 49 kept no lscpu and the trigger narrowed to the CPU, so the host block
    # has to name the model and its vector ISA: a negative from an AVX2-only Xeon and
    # one from an AVX-512 host are different evidence.
    host = hotspot_repro._host()
    assert host["cpu"] and host["cpu"] != "unknown"
    assert isinstance(host["flags"], list)
    assert host["vector_flags"] == [
        flag
        for flag in host["flags"]
        if flag.startswith(("sse", "avx", "avx2", "avx_vnni", "avx512", "amx"))
    ]
    assert "microcode" in host


def test_the_engine_method_is_the_one_this_tool_compiles() -> None:
    source = (Path(_TOOL_DIR) / "hotspot" / "SeatIdsOfRepro.java").read_text(encoding="utf-8")
    # The body this tool compiles must still be the engine's: the stale-length guard and
    # the null slot it reads are what the miscompile gets wrong.
    assert "int[] of = byKind[kind];" in source
    assert "if (of == null || n >= of.length) {" in source
    assert "IDS_PER_KIND = 1_000_000" in source
    assert hotspot_repro.METHOD == "SeatIdsOfRepro::of (194 bytes)"
