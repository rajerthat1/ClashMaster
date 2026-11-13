#!/usr/bin/env bash
# Launch N engine processes from the cluster APK on a USB-connected A51.
#
# The cluster APK declares Engine0..N activities, each in its own
# `:engineN` process.  This script forwards one host port per engine, points
# the probe's control-lane affinity at the phone's big cluster (optional), and
# waits until every engine reports a cold-ready content context.
#
# Usage:
#   ./launch_cluster_a51.sh --engines 2 --cpu-map 4,5
#   ./launch_cluster_a51.sh --engines 4 --cpu-map 4,5,6,7 --base-port 26789
#   ./launch_cluster_a51.sh --engines 2 --cpu-map none   # let the scheduler place lanes
#
# `--cpu-map` maps engine id -> guest cpu through debug.cr.engine_cpu_map.
# Omit it to keep the MuMu-compatible engine N -> cpu N mapping.  The property
# persists until reboot or until it is overwritten.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
package="nullsroyale.rel.free"
engines=2
base_port=26789
guest_base_port=26789
cpu_map=""
serial=""
timeout_seconds=180

while [[ $# -gt 0 ]]; do
  case "$1" in
    --engines) engines="${2:?missing value}"; shift 2 ;;
    --base-port) base_port="${2:?missing value}"; shift 2 ;;
    --cpu-map) cpu_map="${2:?missing value}"; shift 2 ;;
    --serial) serial="${2:?missing value}"; shift 2 ;;
    --timeout) timeout_seconds="${2:?missing value}"; shift 2 ;;
    -h|--help)
      sed -n '2,/^[^#]/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if ! [[ "$engines" =~ ^[0-9]+$ ]] || (( engines < 1 || engines > 24 )); then
  echo "--engines must be between 1 and 24." >&2
  exit 2
fi

adb_bin="$(command -v adb)"
if [[ -z "$adb_bin" ]]; then
  echo "adb is not on PATH." >&2
  exit 1
fi
adb_args=()
if [[ -n "$serial" ]]; then
  adb_args=(-s "$serial")
fi

echo "[1/4] Checking the device"
"$adb_bin" "${adb_args[@]}" wait-for-device
"$adb_bin" "${adb_args[@]}" shell getprop ro.product.model
if ! "$adb_bin" "${adb_args[@]}" shell dumpsys package "$package" | grep -q 'versionName='; then
  echo "$package is not installed; build and install the cluster APK first." >&2
  exit 1
fi

echo "[2/4] Configuring the control-lane CPU map"
if [[ -n "$cpu_map" ]]; then
  "$adb_bin" "${adb_args[@]}" shell setprop debug.cr.engine_cpu_map "$cpu_map"
fi
echo "engine_cpu_map=$("$adb_bin" "${adb_args[@]}" shell getprop debug.cr.engine_cpu_map | tr -d '\r')"

echo "[3/4] Forwarding ports and launching $engines engine processes"
"$adb_bin" "${adb_args[@]}" shell am force-stop --user 0 "$package" >/dev/null || true
sleep 1
for ((engine_id = 0; engine_id < engines; ++engine_id)); do
  host_port=$((base_port + engine_id))
  guest_port=$((guest_base_port + engine_id))
  "$adb_bin" "${adb_args[@]}" forward "tcp:$host_port" "tcp:$guest_port" >/dev/null
  "$adb_bin" "${adb_args[@]}" shell am start --user 0 -f 0x18000000 \
    -n "$package/com.supercell.clashroyale.Engine${engine_id}App" >/dev/null
done

echo "[4/4] Waiting up to ${timeout_seconds}s for every engine to become cold-ready"
ports="$("$repo_root/.venv/bin/python" - "$base_port" "$engines" <<'PY'
import sys
base, count = int(sys.argv[1]), int(sys.argv[2])
print(",".join(str(base + i) for i in range(count)))
PY
)"
"$repo_root/.venv/bin/python" - "$ports" "$timeout_seconds" <<'PY'
import json
import sys
import time

sys.path.insert(0, ".")
from native_runner.cr_native_env import NativeClashEnv, RunnerError

ports = [int(port) for port in sys.argv[1].split(",")]
timeout = float(sys.argv[2])
pending = {port: None for port in ports}
deadline = time.monotonic() + timeout
while pending and time.monotonic() < deadline:
    for port in list(pending):
        try:
            env = NativeClashEnv("127.0.0.1", port, timeout=3)
            status = env.status()
            identity = env._request("engine-status")
            if status.get("coldReady") and identity.get("pid"):
                pending[port] = {
                    "engine": identity.get("engineId"),
                    "pid": identity.get("pid"),
                    "process": identity.get("processName"),
                    "assignedCpu": identity.get("assignedCpu"),
                    "currentCpu": identity.get("currentCpu"),
                    "affinity": identity.get("affinity"),
                }
        except (RunnerError, OSError, json.JSONDecodeError):
            pass
    if pending:
        time.sleep(1.0)

for port in ports:
    value = pending.get(port)
    if value:
        print(f"port {port}: ready {value}")
    else:
        print(f"port {port}: NOT READY after {timeout:.0f}s", file=sys.stderr)
raise SystemExit(0 if not pending else 1)
PY

echo "Cluster engines are ready.  Next:"
echo "  .venv/bin/python tools/native_analysis/resident_batch_throughput.py \\"
echo "      --ports $ports --slots 8 --batches 5 --ticks 600 --actions"
