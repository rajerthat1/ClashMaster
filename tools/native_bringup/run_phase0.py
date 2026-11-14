#!/usr/bin/env python3
"""Unattended Phase 0 gate: 1 h soak + active-worker sweep.

The script owns the whole lifecycle so it can be detached from an interactive
session:

* ensures the requested worker slots are installed under ``builds/rootless``;
* starts N rootless workers via ``run_rootless_worker.sh``;
* pings every worker and samples RSS/CPU every interval for the soak window;
* stops the soak workers;
* optionally starts the largest tier and runs the existing active-worker sweep
  for 4/8/12 workers;
* writes CSV/JSON evidence and stops every worker it started.

Run it detached::

    setsid nohup .venv/bin/python tools/native_bringup/run_phase0.py \
        --output-dir builds/native/phase0-$(date -u +%Y%m%dT%H%M%SZ) \
        >/dev/null 2>&1 &

The PID and a ``RUNNING`` marker are written to the output directory.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SANDBOX = PROJECT_ROOT / ".resource" / "cr-native-sandbox"
RUNTIME_SOURCE = PROJECT_ROOT / ".resource" / "cr-native-linux-bionic"
BUNDLE = (
    PROJECT_ROOT / "builds" / "bundles"
    / "cr-native-linux-bionic-runtime-150535029-local-overlay"
)
MANIFEST = BUNDLE / "BUNDLE-MANIFEST.json"
SYSTEM_ROOT = PROJECT_ROOT / "builds" / "rootless" / "system-root"
RUNTIME_ROOT = PROJECT_ROOT / "builds" / "rootless" / "runtime"
WORKER_SCRIPT = PROJECT_ROOT / "tools" / "native_bringup" / "run_rootless_worker.sh"
SWEEP_SCRIPT = SANDBOX / "scripts" / "run_native_active_worker_sweep.py"

if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))
from native_core.client import JsonLineClient  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def slot_dir(slot: int) -> Path:
    return SYSTEM_ROOT / "data" / "local" / "tmp" / f"cr-native-direct-{slot}"


def ensure_slots(workers: int, output_dir: Path) -> None:
    missing = [slot for slot in range(workers) if not slot_dir(slot).is_dir()]
    if not missing:
        return
    print(f"installing {workers} worker slots (missing {len(missing)})", flush=True)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(RUNTIME_SOURCE)
    command = [
        sys.executable, "-m", "cr_native_bionic", "install",
        "--bundle-dir", str(BUNDLE),
        "--manifest", str(MANIFEST),
        "--system-root", str(SYSTEM_ROOT),
        "--runtime-root", str(RUNTIME_ROOT),
        "--workers", str(workers),
        "--apply",
    ]
    with (output_dir / "install.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, check=True, env=environment, stdout=log,
                       stderr=subprocess.STDOUT)
    still_missing = [slot for slot in range(workers) if not slot_dir(slot).is_dir()]
    if still_missing:
        raise RuntimeError(f"worker slots still missing after install: {still_missing}")


def listener_pid(port: int) -> int | None:
    try:
        output = subprocess.check_output(
            ["ss", "-ltnp", f"sport = :{port}"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.search(r"pid=(\d+)", output)
    return int(match.group(1)) if match else None


def process_sample(pid: int | None) -> tuple[int | None, int | None, float | None]:
    """Return (rss_kb, threads, cumulative_cpu_seconds)."""
    if pid is None:
        return None, None, None
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None, None, None
    rss = threads = None
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            rss = int(line.split()[1])
        elif line.startswith("Threads:"):
            threads = int(line.split()[1])
    fields = stat.rsplit(")", 1)[-1].split()
    # after the comm field: state=index 0; utime=11, stime=12 (0-based).
    ticks = int(fields[11]) + int(fields[12])
    hertz = os.sysconf("SC_CLK_TCK")
    return rss, threads, ticks / hertz


def ping(port: int, timeout: float) -> tuple[bool, float]:
    started = time.perf_counter()
    client = JsonLineClient(host="127.0.0.1", port=port, timeout=timeout)
    try:
        response = client.request({"op": "ping"})
        ok = bool(response.get("ok", True))
    except Exception:
        ok = False
    finally:
        client.close()
    return ok, (time.perf_counter() - started) * 1000.0


def start_worker(slot: int, port: int, output_dir: Path) -> subprocess.Popen:
    log_path = output_dir / "workers" / f"worker-{slot}-{port}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("w", encoding="utf-8")
    environment = dict(os.environ)
    environment.update({
        "SLOT": str(slot),
        "PORT": str(port),
        "LOG": str(log_path),
    })
    process = subprocess.Popen(
        [str(WORKER_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    setattr(process, "_log_handle", log)
    return process


def wait_ready(
    ports: list[int], *, timeout: float, output_dir: Path
) -> dict[int, int]:
    deadline = time.monotonic() + timeout
    pending = list(ports)
    ready: dict[int, int] = {}
    while pending and time.monotonic() < deadline:
        for port in list(pending):
            ok, _latency = ping(port, timeout=2.0)
            pid = listener_pid(port)
            if ok and pid is not None:
                ready[port] = pid
                pending.remove(port)
        if pending:
            time.sleep(2.0)
    if pending:
        raise RuntimeError(f"workers did not become ready: {pending}")
    print(f"workers ready: {sorted(ready)}", flush=True)
    return ready


def stop_workers(ports: list[int], processes: list[subprocess.Popen]) -> None:
    pids = {listener_pid(port) for port in ports}
    for pid in sorted(value for value in pids if value):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 15
    for process in processes:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
    for port in ports:
        pid = listener_pid(port)
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    for process in processes:
        handle = getattr(process, "_log_handle", None)
        if handle is not None:
            handle.close()


def soak(
    ports: list[int],
    processes: list[subprocess.Popen],
    *,
    minutes: float,
    interval: float,
    output_dir: Path,
    ping_timeout: float,
) -> dict:
    csv_path = output_dir / "soak.csv"
    samples = 0
    failures: list[dict] = []
    previous_cpu: dict[int, float] = {}
    stats: dict[int, dict] = {
        port: {"rss_kb": [], "cpu_pct": [], "ping_ms": [], "failures": 0}
        for port in ports
    }
    started = time.monotonic()
    deadline = started + minutes * 60.0
    next_report = started + 600.0
    with csv_path.open("w", encoding="utf-8") as csv:
        csv.write(
            "utc,elapsed_s,port,pid,rss_kb,threads,cpu_pct,ping_ms,ok\n"
        )
        csv.flush()
        while time.monotonic() < deadline:
            for port in ports:
                pid = listener_pid(port)
                rss, threads, cpu_seconds = process_sample(pid)
                ok, ping_ms = ping(port, ping_timeout)
                cpu_pct = None
                if cpu_seconds is not None and port in previous_cpu:
                    cpu_pct = (
                        (cpu_seconds - previous_cpu[port]) / interval * 100.0
                    )
                if cpu_seconds is not None:
                    previous_cpu[port] = cpu_seconds
                if rss is not None:
                    stats[port]["rss_kb"].append(rss)
                if cpu_pct is not None:
                    stats[port]["cpu_pct"].append(cpu_pct)
                stats[port]["ping_ms"].append(ping_ms)
                if not ok or pid is None:
                    stats[port]["failures"] += 1
                    failures.append({
                        "utc": utc_now(),
                        "port": port,
                        "pid": pid,
                        "ok": ok,
                    })
                csv.write(
                    f"{utc_now()},{time.monotonic() - started:.1f},{port},"
                    f"{pid or ''},{rss or ''},{threads or ''},"
                    f"{'' if cpu_pct is None else f'{cpu_pct:.2f}'},"
                    f"{ping_ms:.2f},{int(ok)}\n"
                )
            csv.flush()
            samples += 1
            if time.monotonic() >= next_report:
                print(
                    f"soak {int(time.monotonic() - started)}s "
                    f"samples={samples} failures={len(failures)}",
                    flush=True,
                )
                next_report += 600.0
            time.sleep(max(0.0, min(interval, deadline - time.monotonic())))

    def mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    summary = {
        "schema": "phase0-soak-v1",
        "started_utc": utc_now(),
        "duration_minutes": minutes,
        "interval_seconds": interval,
        "samples": samples,
        "ping_failures": len(failures),
        "crash_or_ping_failures": len(failures),
        "passed": not failures,
        "workers": {
            str(port): {
                "rss_min_mb": round(min(row["rss_kb"]) / 1024, 1) if row["rss_kb"] else None,
                "rss_max_mb": round(max(row["rss_kb"]) / 1024, 1) if row["rss_kb"] else None,
                "rss_last_mb": round(row["rss_kb"][-1] / 1024, 1) if row["rss_kb"] else None,
                "cpu_mean_pct": round(mean(row["cpu_pct"]), 2) if row["cpu_pct"] else None,
                "cpu_max_pct": round(max(row["cpu_pct"]), 2) if row["cpu_pct"] else None,
                "ping_mean_ms": round(mean(row["ping_ms"]), 2) if row["ping_ms"] else None,
                "failures": row["failures"],
            }
            for port, row in stats.items()
        },
        "failure_samples": failures[:50],
    }
    atomic_json(output_dir / "soak-summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)
    return summary


def run_sweep(
    ports: list[int], *, tiers: str, measure_seconds: float,
    warmup_seconds: float, output_dir: Path,
) -> dict | None:
    output = output_dir / "sweep.json"
    command = [
        sys.executable, str(SWEEP_SCRIPT),
        "--ports", f"{ports[0]}-{ports[-1]}",
        "--tiers", tiers,
        "--output", str(output),
        "--warmup-seconds", str(warmup_seconds),
        "--measure-seconds", str(measure_seconds),
        "--action-mode", "deterministic-legal",
    ]
    print("sweep:", " ".join(command), flush=True)
    log_path = output_dir / "sweep.log"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(SANDBOX)
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command, cwd=SANDBOX, env=environment, stdout=log,
            stderr=subprocess.STDOUT,
        )
    if not output.is_file():
        raise RuntimeError(
            f"active-worker sweep produced no output (returncode {completed.returncode}); "
            f"see {log_path}"
        )
    report = json.loads(output.read_text(encoding="utf-8"))
    if completed.returncode != 0 or report.get("status") == "failed":
        raise RuntimeError(
            f"active-worker sweep failed ({report.get('status')}: "
            f"{report.get('error', 'unknown')}); see {log_path}"
        )
    summary = {
        "schema": "phase0-sweep-summary-v1",
        "returncode": completed.returncode,
        "status": report.get("status"),
        "recommended_workers": report.get("recommended_workers"),
        "tiers": [
            {
                "workers": row.get("workers"),
                "status": row.get("status"),
                "native_ticks_per_second": row.get("native_ticks_per_second"),
                "worker_ticks_per_second_p50": row.get("worker_ticks_per_second_p50"),
                "safety_reasons": row.get("safety_reasons"),
            }
            for row in report.get("tiers", [])
        ],
        "report": str(output),
    }
    atomic_json(output_dir / "sweep-summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-port", type=int, default=39031)
    parser.add_argument("--soak-workers", type=int, default=4)
    parser.add_argument("--soak-minutes", type=float, default=60.0)
    parser.add_argument("--sample-seconds", type=float, default=60.0)
    parser.add_argument("--ready-timeout", type=float, default=240.0)
    parser.add_argument("--ping-timeout", type=float, default=10.0)
    parser.add_argument("--no-sweep", action="store_true")
    parser.add_argument(
        "--skip-soak",
        action="store_true",
        help="rerun only the sweep (reuses an existing soak result)",
    )
    parser.add_argument("--sweep-tiers", default="4,8,12")
    parser.add_argument("--sweep-measure-seconds", type=float, default=180.0)
    parser.add_argument("--sweep-warmup-seconds", type=float, default=20.0)
    parser.add_argument("--keep-workers", action="store_true")
    args = parser.parse_args(argv)
    # The sweep subprocess runs with cwd=SANDBOX; keep every evidence path
    # absolute so output lands under this run directory, not the sandbox.
    args.output_dir = args.output_dir.resolve()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    marker = args.output_dir / "RUNNING"
    marker.write_text(f"pid={os.getpid()} started={utc_now()}\n")
    (args.output_dir / "runner.log").write_text("", encoding="utf-8")
    started = time.monotonic()
    report: dict = {
        "schema": "phase0-run-v1",
        "started_utc": utc_now(),
        "output_dir": str(args.output_dir),
        "host": {
            "logical_cpus": os.cpu_count(),
        },
    }
    if args.skip_soak:
        prior = args.output_dir / "phase0-summary.json"
        if prior.is_file():
            prior_report = json.loads(prior.read_text(encoding="utf-8"))
            if "soak" in prior_report:
                report["soak"] = prior_report["soak"]
            report["soak_source"] = str(prior)
    workers: list[subprocess.Popen] = []
    ports: list[int] = []
    try:
        tiers = [
            int(value) for value in args.sweep_tiers.split(",") if value.strip()
        ] if not args.no_sweep else []
        max_slots = max([args.soak_workers, *tiers]) if tiers else args.soak_workers
        ensure_slots(max_slots, args.output_dir)

        # Phase A: soak on its own worker count (skippable for sweep reruns).
        if not args.skip_soak:
            ports = [args.base_port + slot for slot in range(args.soak_workers)]
            workers = [
                start_worker(slot, port, args.output_dir)
                for slot, port in enumerate(ports)
            ]
            wait_ready(ports, timeout=args.ready_timeout, output_dir=args.output_dir)
            report["soak"] = soak(
                ports, workers,
                minutes=args.soak_minutes,
                interval=args.sample_seconds,
                output_dir=args.output_dir,
                ping_timeout=args.ping_timeout,
            )
            if not args.keep_workers:
                stop_workers(ports, workers)
                workers, ports = [], []

        # Phase B: active-worker sweep on the largest tier.
        if tiers:
            ports = [args.base_port + slot for slot in range(max(tiers))]
            workers = [
                start_worker(slot, port, args.output_dir)
                for slot, port in enumerate(ports)
            ]
            wait_ready(ports, timeout=args.ready_timeout, output_dir=args.output_dir)
            report["sweep"] = run_sweep(
                ports,
                tiers=",".join(str(value) for value in tiers),
                measure_seconds=args.sweep_measure_seconds,
                warmup_seconds=args.sweep_warmup_seconds,
                output_dir=args.output_dir,
            )
        report["elapsed_seconds"] = round(time.monotonic() - started, 1)
        report["status"] = "complete"
        return 0
    except Exception as error:  # noqa: BLE001 - unattended evidence matters
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        return 1
    finally:
        if not args.keep_workers:
            stop_workers(ports, workers)
        report["finished_utc"] = utc_now()
        atomic_json(args.output_dir / "phase0-summary.json", report)
        marker.unlink(missing_ok=True)
        (args.output_dir / "DONE").write_text(
            f"status={report.get('status')} finished={utc_now()}\n"
        )


if __name__ == "__main__":
    raise SystemExit(main())
