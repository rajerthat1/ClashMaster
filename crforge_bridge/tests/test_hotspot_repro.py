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


def test_the_phased_mode_keeps_the_engine_s_compile_sequence() -> None:
    # The driver is only a reproducer if it seeds populated slots plus an in-range null
    # tail, flips bci 120 the way the engine's log did (an assigned slot read as zero),
    # fills one null slot so the grow path is compiled, and only then shows a second one.
    source = (Path(_TOOL_DIR) / "hotspot" / "SeatIdsOfRepro.java").read_text(encoding="utf-8")
    phased = source[source.index("private static long phased("):]
    phased = phased[: phased.index("public static void main")]
    assert "ids.seed(kinds + holes, n, kinds);" in phased
    assert "ids.clear(0, 0, 0);" in phased
    assert "sum += ids.of(0, 0);" in phased
    assert "sum += ids.of(0, kinds * IDS_PER_KIND);" in phased
    assert "warm(ids, kinds, n, 1, kinds)" in phased
    seed = source[source.index("private void seed("):]
    seed = seed[: seed.index("\n  }")]
    assert "int[][] byKind = new int[length][];" in seed
    assert "for (int kind = 0; kind < filled; kind++)" in seed
    assert "ids[seat] = byKind;" in seed


def test_the_nmethod_reader_records_the_body_sizes_not_the_bytecode_size() -> None:
    log = """
<nmethod compile_id='110' compiler='c1' level='3' entry='0x1' size='5296' address='0x2'
         method='SeatIdsOfRepro of (II)I' bytes='194' count='9692' stamp='0.031'/>
<nmethod compile_id='111' compiler='c2' level='4' entry='0x3' size='1528' address='0x4'
         method='SeatIdsOfRepro of (II)I' bytes='194' count='417857' stamp='0.036'/>
<nmethod compile_id='112' compiler='c2' level='4' entry='0x5' size='2848' address='0x6'
         method='java.util.Arrays copyOf ([II)[I' bytes='33' count='2' stamp='0.037'/>
"""
    parsed = hotspot_repro._nmethods(log)
    assert [(entry["compile_id"], entry["size"]) for entry in parsed] == [(110, 5296), (111, 1528)]
    assert parsed[1]["compiler"] == "c2"
    assert parsed[1]["level"] == 4
    assert parsed[1]["installed"] == 0.036


def test_phased_mode_passes_its_own_knobs_and_defaults() -> None:
    class Args:
        mode = "phased"
        kinds = 8
        rounds = 12
        phased_n = 512
        phased_settle_ms = 300

    assert hotspot_repro._mode_args(Args()) == ["phased", "8", "512", "12", "300"]


def test_a_phased_run_must_show_both_c2_bodies() -> None:
    one = {"arm": "c2", "timed_out": False, "c2_nmethods": [{"size": 1528}]}
    two = {
        "arm": "c2",
        "timed_out": False,
        "c2_nmethods": [{"size": 1528}, {"size": 4096}],
    }
    control = {"arm": "c1", "timed_out": False, "c2_nmethods": []}
    summary = hotspot_repro._phased_summary([one, control])
    assert summary["c2_bodies"] == [[1528]]
    assert not summary["reached_recompile"]
    assert hotspot_repro._phased_summary([one, two, control])["reached_recompile"]
