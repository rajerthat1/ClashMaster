#!/usr/bin/env python3
"""HotSpot C2 reproducer for ``org.crforge.env.SeatIds.of`` (session 49's segfault).

Session 49 named a rented host's crash as a C2 miscompile: once ``SeatIds.of``
was a level-4 nmethod, the next new entity kind made the compiled body compare
``n`` against the previous ``byKind.length`` and load through a null ``of``
(``segfault at c``, the array-length slot).  That host is not this one, so the
finding rests on one box.  This tool is the cheap way to settle whether any host
in front of you has it::

    .venv/bin/python tools/crforge/hotspot_repro.py \\
        --jdks .resource/jdk21/jdk-21.0.13+9 .resource/jdk25/jdk-25.0.5+8 \\
        --output runs/crforge-c4/hotspot-seatids.json

It compiles ``tools/crforge/hotspot/SeatIdsOfRepro.java`` (the method is the
crforge body verbatim, 194 bytes) with each JDK and runs three arms:

``c2``           default flags, ``--repeats`` times — the arm expected to crash
``c1``           ``-XX:TieredStopAtLevel=1`` — the workaround, expected to pass
``interpreter``  ``-Xint`` — expected to pass

Every arm runs with ``-XX:+PrintCompilation`` so a pass can be read as "C2
compiled ``of`` at level 4 and it was correct" rather than "the method was never
compiled".  An arm whose output never shows the level-4 nmethod is reported
``inconclusive``; it is not a pass.  Before any arm runs, the tool also diffs
``javap -p -c`` of the reproducer's ``of`` against the built engine jar
(``.resource/crforge/env/build/install/env/lib/env-*.jar``): if the instruction
streams differ, the run is ``inconclusive`` too, because a clean pass from a
different method would prove nothing.

``--mode soak`` sweeps for the engine's shape (a long run where kinds appear
rarely) instead of the deterministic warm-then-new-kind loop; the box's crash
came 65.1 s in, so the default soak is 70 s.

The artifact is ``crforge-hotspot-seatids.v1``.  Exit status: 0 when the defect
reproduced (the report has its reproducer), 1 when every arm passed (a recorded
negative for this host), 2 when an arm was inconclusive.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "tools" / "crforge" / "hotspot" / "SeatIdsOfRepro.java"
SCHEMA = "crforge-hotspot-seatids.v1"
METHOD = "SeatIdsOfRepro::of (194 bytes)"

ARMS: dict[str, list[str]] = {
    "c2": [],
    "c1": ["-XX:TieredStopAtLevel=1"],
    "interpreter": ["-Xint"],
}

_LEVEL4 = re.compile(r"\s4\s+" + re.escape(METHOD))
_LEVEL1 = re.compile(r"\s[13]\s+" + re.escape(METHOD))
_PROGRESS = re.compile(
    r"(?:warm: kinds=\d+ rounds=\d+ sum=-?\d+ in [\d.]+ ms"
    r"|soak: done sum=-?\d+ in [\d.]+ ms"
    r"|t=[\d.]+s kinds=\d+ calls=\d+)"
)
_JAVA_VERSION = re.compile(r'(?:openjdk|java) version "([^"]+)"')
# The program writes progress to stderr and the JVM writes PrintCompilation to stdout, so
# each stream is contiguous; the artifact keeps a tail and the parse sees up to _KEEP chars.
_KEEP = 2_000_000


def _run(command: list[str], *, timeout: float) -> dict[str, Any]:
    def _as_text(value: object) -> str:
        return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")

    try:
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=timeout, errors="replace"
        )
    except subprocess.TimeoutExpired as expired:
        return {
            "command": command,
            "returncode": None,
            "timed_out": True,
            "stdout": _as_text(expired.stdout)[-_KEEP:],
            "stderr": _as_text(expired.stderr)[-_KEEP:],
        }
    return {
        "command": command,
        "returncode": completed.returncode,
        "timed_out": False,
        "stdout": completed.stdout[-_KEEP:],
        "stderr": completed.stderr[-_KEEP:],
    }


def _crashed(run: dict[str, Any]) -> bool:
    code = run["returncode"]
    if code is not None and (code < 0 or code in (134, 139)):
        return True
    return "A fatal error has been detected" in run["stderr"] or "SIGSEGV" in run["stderr"]


def _jdk_identity(java: Path) -> dict[str, Any]:
    version = _run([str(java), "-version"], timeout=60)
    text = version["stderr"] + version["stdout"]
    match = _JAVA_VERSION.search(text)
    return {
        "home": str(java.parent.parent),
        "java": str(java),
        "version": match.group(1) if match else "unknown",
        "banner": text.strip().splitlines()[0] if text.strip() else "",
    }


def _find_jdks(named: list[Path]) -> list[Path]:
    homes: list[Path] = list(named)
    if not named:
        for pattern in (".resource/jdk21*", ".resource/jdk25*"):
            homes.extend(sorted(REPO_ROOT.glob(pattern)))
        homes.extend(sorted((Path.home() / ".gradle" / "jdks").glob("*/")))
        if os.environ.get("JAVA_HOME"):
            homes.append(Path(os.environ["JAVA_HOME"]))
    found: list[Path] = []
    seen: set[Path] = set()
    for home in homes:
        javac = home / "bin" / "javac"
        if not javac.is_file():
            continue
        resolved = javac.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        found.append(home)
    return found


def _cpu_field(name: str) -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith(name):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def _cpu_model() -> str:
    return _cpu_field("model name") or platform.processor() or "unknown"


def _cpu_flags() -> list[str]:
    """The CPU's feature flags.

    Session 49 did not keep these and the trigger narrowed to the host CPU, so the
    artifact has to carry them: the two recorded negatives are both AVX2-only Xeons
    and the untested variable is a host that reports AVX-512.
    """
    return _cpu_field("flags").split()


def _vector_flags(flags: list[str]) -> list[str]:
    """The subset of ``flags`` that names a vector ISA, in /proc/cpuinfo order."""
    names = ("sse", "avx", "avx2", "avx_vnni", "avx512", "amx")
    return [flag for flag in flags if any(flag.startswith(name) for name in names)]


def _host() -> dict[str, object]:
    flags = _cpu_flags()
    return {
        "platform": platform.platform(),
        "kernel": platform.release(),
        "cpu": _cpu_model(),
        "cpus": os.cpu_count(),
        "microcode": _cpu_field("microcode"),
        "vector_flags": _vector_flags(flags),
        "flags": flags,
    }


def _mode_args(args: argparse.Namespace) -> list[str]:
    if args.mode == "soak":
        return ["soak", str(args.seconds), str(args.new_every)]
    return ["quick", str(args.rounds), str(args.kinds)]


_ENGINE_JAR = REPO_ROOT / ".resource" / "crforge" / "env" / "build" / "install" / "env" / "lib"
_OF_BLOCK = re.compile(r"\n  int of\(int, int\);\n.*?\n\n", re.DOTALL)
_INSTRUCTION = re.compile(r"^\s+(\d+):\s+(\S+)(?:\s+(\S+))?")


def _instruction_stream(javap: Path, *, classpath: Path, class_name: str) -> list[str]:
    """The opcodes of `of`, with constant-pool indices folded away."""
    listing = _run(
        [str(javap), "-p", "-c", "-cp", str(classpath), class_name], timeout=120
    )
    match = _OF_BLOCK.search("\n" + listing["stdout"])
    if not match:
        return []
    stream: list[str] = []
    for line in match.group(0).splitlines():
        instruction = _INSTRUCTION.match(line)
        if not instruction:
            continue
        operand = instruction.group(3) or ""
        operand = re.sub(r"#\d+", "#", operand)
        stream.append(f"{instruction.group(2)} {operand}".strip())
    return stream


def _bytecode_check(java_home: Path, class_dir: Path) -> dict[str, Any]:
    """Prove the reproducer's `of` is the engine's `of` before reading a pass as a negative."""
    jars = sorted(_ENGINE_JAR.glob("env-*.jar"))
    if not jars:
        return {"available": False, "reason": f"no engine jar under {_ENGINE_JAR}"}
    javap = java_home / "bin" / "javap"
    engine = _instruction_stream(javap, classpath=jars[-1], class_name="org.crforge.env.SeatIds")
    repro = _instruction_stream(javap, classpath=class_dir, class_name="SeatIdsOfRepro")
    return {
        "available": bool(engine and repro),
        "jar": str(jars[-1].relative_to(REPO_ROOT)),
        "engine_instructions": len(engine),
        "repro_instructions": len(repro),
        "identical": bool(engine) and engine == repro,
    }


def _arm(
    *,
    java: Path,
    class_dir: Path,
    flags: list[str],
    mode: list[str],
    timeout: float,
) -> dict[str, Any]:
    command = [str(java), "-XX:+PrintCompilation", *flags, "-cp", str(class_dir), "SeatIdsOfRepro", *mode]
    run = _run(command, timeout=timeout)
    crashed = _crashed(run)
    level4 = _LEVEL4.search(run["stdout"])
    progress = _PROGRESS.findall(run["stderr"])
    return {
        "flags": flags,
        "returncode": run["returncode"],
        "timed_out": run["timed_out"],
        "crashed": crashed,
        "c2_level4_compiled": bool(level4),
        "level4_line": level4.group(0).strip() if level4 else "",
        "c1_compiled": bool(_LEVEL1.search(run["stdout"])),
        "stdout_tail": run["stdout"][-1_500:],
        "stderr_tail": run["stderr"][-1_500:],
        "last_progress": progress[-1] if progress else "",
    }


def _verdict(arms: list[dict[str, Any]], *, bytecode_identical: bool) -> dict[str, Any]:
    """Classify the arm table; a pass only counts if `of` really was C2-compiled."""
    controls = [arm for arm in arms if arm["arm"] != "c2" and not arm["timed_out"]]
    c2_arms = [arm for arm in arms if arm["arm"] == "c2" and not arm["timed_out"]]
    crashes = [arm for arm in c2_arms if arm["crashed"]]
    compiled_c2 = [arm for arm in c2_arms if arm["c2_level4_compiled"]]
    control_crash = [arm for arm in controls if arm["crashed"]]
    inconclusive = not c2_arms or not compiled_c2 or not bytecode_identical
    if crashes and not control_crash:
        verdict = "reproduced"
    elif not crashes and not inconclusive and not control_crash:
        verdict = "not reproduced"
    else:
        verdict = "inconclusive"
    return {
        "result": verdict,
        "c2_arms": len(c2_arms),
        "crashes": len(crashes),
        "c2_level4_compiled": len(compiled_c2),
        "bytecode_identical": bytecode_identical,
        "control_arms": len(controls),
        "control_crashes": len(control_crash),
        "note": {
            "reproduced": "a default-flags arm crashed while C1 and the interpreter passed",
            "not reproduced": "every C2 arm compiled `of` at level 4 and passed, and its bytecode is the engine's",
            "inconclusive": (
                "an arm timed out, crashed in a control arm, or never compiled `of` at level 4; "
                "or the engine jar was absent so the bytecode check did not run"
            ),
        }[verdict],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--jdks",
        type=Path,
        nargs="+",
        default=[],
        help="JDK homes (default: .resource/jdk21*, jdk25*, gradle jdks, $JAVA_HOME)",
    )
    parser.add_argument("--mode", choices=("quick", "soak"), default="quick")
    parser.add_argument("--repeats", type=int, default=3, help="C2 arms per JDK")
    parser.add_argument("--rounds", type=int, default=200_000, help="quick mode: warm rounds")
    parser.add_argument("--kinds", type=int, default=2, help="quick mode: kinds that exist before the method is hot")
    parser.add_argument("--seconds", type=int, default=70, help="soak mode: run length (the box crashed at 65.1 s)")
    parser.add_argument("--new-every", type=int, default=2_000_000, help="soak mode: calls between new kinds")
    parser.add_argument("--timeout", type=float, default=0.0, help="per-arm timeout (0: 30 s quick, seconds + 60 soak)")
    parser.add_argument("--keep-build", type=Path, default=None, help="compile here instead of a temp dir")
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c4/hotspot-seatids.json"))
    args = parser.parse_args(argv)

    if not SOURCE.is_file():
        raise SystemExit(f"missing {SOURCE}")
    jdks = _find_jdks(list(args.jdks))
    if not jdks:
        raise SystemExit("no JDK with javac found; pass --jdks")

    timeout = args.timeout or (30.0 if args.mode == "quick" else args.seconds + 60.0)
    mode = _mode_args(args)
    build_root = args.keep_build or Path(tempfile.mkdtemp(prefix="crforge-hotspot-"))
    build_root.mkdir(parents=True, exist_ok=True)

    jvms: list[dict[str, Any]] = []
    arms: list[dict[str, Any]] = []
    try:
        for home in jdks:
            javac = home / "bin" / "javac"
            java = home / "bin" / "java"
            identity = _jdk_identity(java)
            class_dir = build_root / home.name
            class_dir.mkdir(parents=True, exist_ok=True)
            compiled = _run([str(javac), "-d", str(class_dir), str(SOURCE)], timeout=120)
            identity["compiled"] = compiled["returncode"] == 0
            if compiled["returncode"] != 0:
                identity["compile_error"] = compiled["stderr"][-1_500:]
                jvms.append(identity)
                continue
            identity["bytecode"] = _bytecode_check(home, class_dir)
            jvms.append(identity)
            for name, flags in ARMS.items():
                repeats = args.repeats if name == "c2" else 1
                for repeat in range(repeats):
                    print(f"{home.name} {name} #{repeat + 1}", file=sys.stderr)
                    arms.append({
                        "jdk": home.name,
                        "jvm_version": identity["version"],
                        "arm": name,
                        "repeat": repeat + 1,
                        **_arm(java=java, class_dir=class_dir, flags=flags, mode=mode, timeout=timeout),
                    })
    finally:
        if args.keep_build is None:
            shutil.rmtree(build_root, ignore_errors=True)

    bytecode_identical = any(jvm.get("bytecode", {}).get("identical") for jvm in jvms)
    verdict = _verdict(arms, bytecode_identical=bytecode_identical)

    artifact = {
        "schema": SCHEMA,
        "measured": datetime.now().isoformat(timespec="seconds"),
        "host": _host(),
        "source": str(SOURCE.relative_to(REPO_ROOT)),
        "method": METHOD,
        "mode": args.mode,
        "mode_args": mode,
        "jvms": jvms,
        "arms": arms,
        "verdict": verdict,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"verdict: {verdict['result']} ({verdict['crashes']}/{verdict['c2_arms']} C2 arms crashed) -> {args.output}")
    return {"reproduced": 0, "not reproduced": 1, "inconclusive": 2}[verdict["result"]]


if __name__ == "__main__":
    raise SystemExit(main())
