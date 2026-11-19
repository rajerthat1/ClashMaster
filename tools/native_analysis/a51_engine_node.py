#!/usr/bin/env python3
"""Attest a USB-attached A51 engine cluster and emit a V4 PPO engine-node manifest.

The emulator path (`native_runner/training/v4/launch_ppo_engine_node.sh`) writes
`engine-node-ready.json`, which `build_ppo_multinode_topology` turns into the
trainer topology.  The phone path has the same contract but no Slurm/KVM/AVD:
the engines already run from the cluster APK and are reachable through adb
forwards on loopback.  This tool performs the node-level checks and writes the
compatible manifest:

    # start the device engine cluster before running this helper
    .venv/bin/python tools/native_analysis/a51_engine_node.py \
        --serial R58NA0QTHWV --engines 4 \
        --output runs/a51-node0/engine-node-ready.json

    .venv/bin/python -m native_runner.training.v4.build_ppo_multinode_topology \
        --node-ready runs/a51-node0/engine-node-ready.json \
        --output runs/topology.json --trainer-job-id 1 --trainer-host "$(hostname -s)" \
        --gpus 1 --worker-cpus 0 --control-cpus 1,2,3,4,5,6,7

The manifest records eight resident slots per engine because the PPO topology
requires that lane count; the A51 probe itself can host more.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from native_runner.cr_native_env import NativeClashEnv, RunnerError  # noqa: E402
from native_runner.ruleset import load_native_build_profile  # noqa: E402

PACKAGE = "nullsroyale.rel.free"
DEFAULT_BASE_PORT = 26789
DEFAULT_WORKER_PORT = 29900
SNAPSHOT_SCHEMA = "v4-ppo-engine-node.v1"


def adb(serial: str, *args: str) -> str:
    command = ["adb"]
    if serial:
        command += ["-s", serial]
    result = subprocess.run(
        [*command, *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30.0,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed ({result.returncode}): {result.stdout.strip()}")
    return result.stdout.strip()


def wait_endpoint(port: int, timeout: float) -> tuple[NativeClashEnv, dict[str, Any], dict[str, Any]]:
    """Wait for coldReady and return the control env, engine status and attestation."""

    deadline = time.monotonic() + timeout
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            env = NativeClashEnv("127.0.0.1", port, timeout=5.0)
            status = env.status()
            if not status.get("coldReady"):
                raise RuntimeError("engine is not cold-ready")
            engine = env._request("engine-status")
            attested = env._request("attest")
            attestation = attested.get("attestation", {})
            return env, engine, attestation
        except (RunnerError, OSError, json.JSONDecodeError) as error:
            last_error = repr(error)
            time.sleep(0.5)
    raise RuntimeError(f"engine on port {port} did not become ready: {last_error}")


def engine_cgroup(serial: str, pid: int) -> str:
    if not serial:
        return "unchecked"
    output = adb(serial, "shell", f"grep schedtune /proc/{pid}/cgroup 2>/dev/null")
    return output.strip() or "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serial", default="", help="adb serial (defaults to the single attached device)")
    parser.add_argument("--base-port", type=int, default=DEFAULT_BASE_PORT)
    parser.add_argument("--engines", type=int, required=True)
    parser.add_argument("--global-start", type=int, default=0)
    parser.add_argument("--host", default="127.0.0.1", help="engine endpoint host written to the manifest")
    parser.add_argument(
        "--worker-host",
        default=socket.gethostname().split(".", 1)[0],
        help="host of the remote PPO worker service",
    )
    parser.add_argument("--worker-port", type=int, default=DEFAULT_WORKER_PORT)
    parser.add_argument("--slots", type=int, default=8, help="resident slots per engine recorded for PPO")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--no-render-off", action="store_true")
    parser.add_argument(
        "--no-verify-build",
        action="store_true",
        help="skip comparison against native_runner/versions/native-<client>.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.engines < 1 or args.engines > 24:
        parser.error("--engines must be in 1..24")
    if args.global_start < 0:
        parser.error("--global-start must be nonnegative")
    if args.slots != 8:
        parser.error("the V4 PPO topology requires exactly 8 resident slots per engine")

    serial = args.serial or adb("", "get-serialno")
    engines: list[dict[str, Any]] = []
    digests: set[str] = set()
    client_versions: set[str] = set()
    backgrounded: list[int] = []
    checks: dict[str, bool] = {}

    for offset in range(args.engines):
        engine_index = args.global_start + offset
        port = args.base_port + offset
        env, status, attestation = wait_endpoint(port, args.timeout)
        try:
            if not args.no_render_off:
                env._request("render off")
            pid = int(status.get("pid", -1))
            process_name = str(status.get("processName", ""))
            cgroup = engine_cgroup(serial, pid)
            if "/background" in cgroup:
                backgrounded.append(engine_index)
            client_version = str(env.status().get("clientVersion") or attestation.get("content_version") or "")
            client_versions.add(client_version)
            row_checks = {
                "ok": status.get("ok") is True,
                "cold_ready": True,
                "engine_id": int(status.get("engineId", -1)) == offset,
                "process": process_name == f"{PACKAGE}:engine{offset}",
                "port": int(status.get("port", -1)) == port,
                "slots": int(status.get("slotsPerEngine", -1)) == args.slots,
                "production_ready": attestation.get("production_ready") is True,
                "foreground": "/background" not in cgroup,
            }
            if not args.no_verify_build:
                build_profile = load_native_build_profile(client_version) if client_version else None
                row_checks["build_pinned"] = build_profile is not None
                if build_profile is not None:
                    for key in (
                        "libg_sha256",
                        "probe_sha256",
                        "content_fingerprint_sha256",
                        "content_version",
                    ):
                        if str(attestation.get(key)) != str(build_profile.get(key)):
                            row_checks[f"build_{key}"] = False
            failed = [name for name, passed in row_checks.items() if not passed]
            if failed:
                raise RuntimeError(
                    f"engine {engine_index} (port {port}) failed checks {failed}; "
                    f"status={json.dumps(status)} attestation={json.dumps(attestation)}"
                )
            digest = str(attestation.get("attestation_digest", ""))
            if not digest:
                raise RuntimeError(f"engine {engine_index} has no attestation digest")
            digests.add(digest)
            engines.append(
                {
                    "engine_index": engine_index,
                    "host": args.host,
                    "port": port,
                    "pid": pid,
                    "assigned_cpu": status.get("assignedCpu"),
                    "cgroup": cgroup,
                    "probe_sha256": attestation.get("probe_sha256"),
                    "libg_sha256": attestation.get("libg_sha256"),
                    "content_version": attestation.get("content_version"),
                }
            )
        finally:
            env.close_transport()

    if len(digests) != 1:
        raise RuntimeError(f"engines do not share one attestation digest: {sorted(digests)}")
    if len(client_versions) != 1:
        raise RuntimeError(f"engines report different client versions: {sorted(client_versions)}")
    if backgrounded:
        raise RuntimeError(
            f"engines {backgrounded} are in Android's background cpuset (CPUs 0-1); "
            "relaunch the engine cluster with visible windows so engines stay out of the background cpuset"
        )

    manifest = {
        "ok": True,
        "schema": SNAPSHOT_SCHEMA,
        "slurm_job_id": None,
        "host": socket.gethostname().split(".", 1)[0],
        "device_serial": serial,
        "global_engine_start": args.global_start,
        "engine_count": len(engines),
        "resident_slots_per_engine": args.slots,
        "decision_ticks": 5,
        "parallel_matches": len(engines) * args.slots,
        "client_version": next(iter(client_versions)),
        "remote_worker_host": args.worker_host,
        "remote_worker_port": args.worker_port,
        "engines": engines,
        "attestation_digest": next(iter(digests)),
        "checks": {
            "engine_count": len(engines) == args.engines,
            "unique_ports": len({row["port"] for row in engines}) == len(engines),
            "unique_pids": len({row["pid"] for row in engines}) == len(engines),
            "shared_attestation": len(digests) == 1,
            "shared_client_version": len(client_versions) == 1,
            "foreground": not backgrounded,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
