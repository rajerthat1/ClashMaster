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

echo "[3/4] Forwarding ports and launching $engines engine processes (one at a time)"
"$adb_bin" "${adb_args[@]}" shell am force-stop --user 0 "$package" >/dev/null || true
sleep 1
ports=""
for ((engine_id = 0; engine_id < engines; ++engine_id)); do
  host_port=$((base_port + engine_id))
  guest_port=$((guest_base_port + engine_id))
  "$adb_bin" "${adb_args[@]}" forward "tcp:$host_port" "tcp:$guest_port" >/dev/null
  "$adb_bin" "${adb_args[@]}" shell am start --user 0 -f 0x18000000 \
    -n "$package/com.supercell.clashroyale.Engine${engine_id}App" >/dev/null
  ports="${ports:+$ports,}$host_port"
  # Launch-and-wait: an engine started while another activity covers it can sit
  # below coldReady indefinitely.  Let each engine finish its content gate while
  # it is still the foreground activity, then start the next one.
  echo "  waiting up to ${timeout_seconds}s for engine $engine_id (port $host_port)"
  "$repo_root/.venv/bin/python" - "$repo_root" "$host_port" "$timeout_seconds" <<'PY'
import json
import sys
import time

repo_root, port, timeout = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
sys.path.insert(0, repo_root)
from native_runner.cr_native_env import NativeClashEnv, RunnerError

deadline = time.monotonic() + timeout
while time.monotonic() < deadline:
    try:
        env = NativeClashEnv("127.0.0.1", port, timeout=3)
        status = env.status()
        identity = env._request("engine-status")
        if status.get("coldReady") and identity.get("pid"):
            print(
                f"  port {port}: ready "
                + json.dumps(
                    {
                        key: identity.get(key)
                        for key in ("engineId", "pid", "processName", "assignedCpu", "currentCpu", "affinity")
                    }
                )
            )
            raise SystemExit(0)
    except (RunnerError, OSError, json.JSONDecodeError):
        pass
    time.sleep(1.0)
print(f"  port {port}: NOT READY after {timeout:.0f}s", file=sys.stderr)
raise SystemExit(1)
PY
done

echo "[4/4] Every engine reported cold-ready.  Next:"
echo "  .venv/bin/python tools/native_analysis/resident_batch_throughput.py \\"
echo "      --ports $ports --slots 8 --batches 5 --ticks 600 --actions"
