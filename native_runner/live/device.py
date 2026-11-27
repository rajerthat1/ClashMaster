"""Read-only ADB diagnostics for the official-client live path.

The memory readers require one of two things:

* the app process runs as root (``su -c id`` returns 0), or
* ``adb root`` is available (MuMu's "ADB Root" setting).

A production phone image has neither, and ``/proc/<pid>/mem`` of another app is
then unreadable no matter how the ADB client is configured.  This module says
so before a reader is compiled or a match is started.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

OFFICIAL_PACKAGE = "com.supercell.clashroyale"
OFFLINE_PACKAGE = "nullsroyale.rel.free"


class DeviceError(RuntimeError):
    """The connected device cannot run the requested live path."""


def adb(
    serial: str,
    *arguments: str,
    adb_path: str = "adb",
    timeout: float = 20.0,
    check: bool = True,
) -> str:
    result = subprocess.run(
        [adb_path, "-s", serial, *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and result.returncode:
        raise DeviceError(result.stderr.strip() or result.stdout.strip() or "adb command failed")
    return result.stdout.strip()


def _first(lines: str) -> str:
    return lines.splitlines()[0].strip() if lines.strip() else ""


@dataclass(frozen=True, slots=True)
class DeviceProbe:
    serial: str
    model: str
    abi: str
    sdk: int
    release: str
    debuggable: bool
    packages: tuple[str, ...] = ()
    official_version_code: int | None = None
    official_version_name: str | None = None
    official_primary_abi: str | None = None
    official_running: bool = False
    official_pid: int | None = None
    libg_path: str | None = None
    uid: int | None = None
    adb_root: bool = False
    notes: tuple[str, ...] = ()

    @property
    def official_installed(self) -> bool:
        return OFFICIAL_PACKAGE in self.packages

    @property
    def offline_installed(self) -> bool:
        return OFFLINE_PACKAGE in self.packages

    @property
    def memory_access(self) -> str:
        if self.uid == 0:
            return "root"
        if self.adb_root:
            return "adb-root"
        return "none"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["memory_access"] = self.memory_access
        data["official_installed"] = self.official_installed
        data["offline_installed"] = self.offline_installed
        return data


def probe_device(serial: str, *, adb_path: str = "adb") -> DeviceProbe:
    def shell(command: str, *, timeout: float = 20.0, check: bool = True) -> str:
        return adb(serial, "shell", command, adb_path=adb_path, timeout=timeout, check=check)

    model = _first(shell("getprop ro.product.model"))
    abi = _first(shell("getprop ro.product.cpu.abi"))
    sdk = int(_first(shell("getprop ro.build.version.sdk")) or 0)
    release = _first(shell("getprop ro.build.version.release"))
    debuggable = _first(shell("getprop ro.debuggable")) == "1"
    packages_output = shell("pm list packages")
    packages = tuple(
        sorted(line.removeprefix("package:").strip() for line in packages_output.splitlines() if line.startswith("package:"))
    )

    version_code = version_name = primary_abi = None
    if OFFICIAL_PACKAGE in packages:
        dump = shell(f"dumpsys package {OFFICIAL_PACKAGE}")
        for line in dump.splitlines():
            text = line.strip()
            if text.startswith("versionCode="):
                version_code = int(text.split("=", 1)[1].split()[0])
            elif text.startswith("versionName="):
                version_name = text.split("=", 1)[1].strip()
            elif text.startswith("primaryCpuAbi="):
                primary_abi = text.split("=", 1)[1].strip()

    pid_output = shell(f"pidof {OFFICIAL_PACKAGE}", check=False)
    pid = int(pid_output.split()[0]) if pid_output.split() else None
    libg_path = None
    if pid:
        maps = shell(f"grep libg.so /proc/{pid}/maps", check=False)
        if maps.strip():
            libg_path = _first(maps).split()[-1]

    uid_output = shell("id -u")
    uid = int(uid_output) if uid_output.isdigit() else None
    adb_root = uid == 0
    if not adb_root:
        try:
            adb(serial, "root", adb_path=adb_path, timeout=15.0, check=False)
            adb(serial, "wait-for-device", adb_path=adb_path, timeout=15.0)
            root_uid = shell("id -u")
            adb_root = root_uid.strip() == "0"
            uid = int(root_uid) if adb_root else uid
        except DeviceError:
            adb_root = False

    notes: list[str] = []
    if not packages:
        notes.append("no packages visible; is the device authorized?")
    if OFFICIAL_PACKAGE not in packages:
        notes.append(f"{OFFICIAL_PACKAGE} is not installed")
    if uid != 0:
        notes.append("no root: /proc/<pid>/mem of another app is unreadable")
    if primary_abi and abi and primary_abi != abi:
        notes.append(f"app ABI {primary_abi} differs from device ABI {abi}")
    return DeviceProbe(
        serial=serial,
        model=model,
        abi=abi,
        sdk=sdk,
        release=release,
        debuggable=debuggable,
        packages=packages,
        official_version_code=version_code,
        official_version_name=version_name,
        official_primary_abi=primary_abi,
        official_running=pid is not None,
        official_pid=pid,
        libg_path=libg_path,
        uid=uid,
        adb_root=adb_root,
        notes=tuple(notes),
    )


def require_memory_access(probe: DeviceProbe) -> None:
    """Refuse to start a reader that cannot possibly read memory."""

    if not probe.official_installed:
        raise DeviceError(f"{OFFICIAL_PACKAGE} is not installed on {probe.serial}")
    if probe.memory_access == "none":
        raise DeviceError(
            "no memory access: the device is unrooted and adb root is unavailable.\n"
            "  Use a rooted Android image (MuMu with root enabled) or the offline "
            "engine client; a production phone cannot expose /proc/<pid>/mem of "
            "another app. ADB taps still work without root."
        )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    probe = probe_device(arguments.serial, adb_path=arguments.adb)
    if arguments.json:
        print(json.dumps(probe.to_dict(), indent=2))
        return 0
    print(f"device      : {probe.model} ({probe.release}, SDK {probe.sdk}, {probe.abi})")
    print(f"packages    : official={probe.official_installed} offline={probe.offline_installed}")
    if probe.official_installed:
        print(
            f"official    : {probe.official_version_name} (code {probe.official_version_code}), "
            f"abi {probe.official_primary_abi}"
        )
        print(f"running     : {probe.official_running} pid={probe.official_pid}")
        print(f"libg        : {probe.libg_path or 'not mapped'}")
    print(f"root        : {probe.memory_access} (uid={probe.uid}, adb_root={probe.adb_root})")
    for note in probe.notes:
        print(f"note        : {note}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
