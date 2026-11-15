#!/usr/bin/env bash
# Wire a USB-attached A51 engine cluster into the V4 PPO topology.
#
# This is the phone equivalent of `launch_ppo_engine_node.sh`: it launches the
# 16.635.1 cluster APK (unless --skip-launch), attests every engine, writes the
# `engine-node-ready.json` manifest and starts the local remote-worker service
# that the PPO trainer connects to.  It does not require Slurm, KVM or an AVD.
#
# Usage:
#   ./launch_ppo_a51.sh --engines 4 --output-dir runs/a51-node0
#   ./launch_ppo_a51.sh --engines 4 --skip-launch --output-dir runs/a51-node0
#   ./launch_ppo_a51.sh --engines 1 --serial R58NA0QTHWV --worker-port 29900
#
# `--worker-host` (default 127.0.0.1) is written to the manifest and bound by
# the worker service.  A trainer on the same host uses 127.0.0.1; a remote GPU
# host needs this node's reachable name (and the engine ports exposed, e.g. via
# an SSH tunnel or `tcp_endpoint_proxy.py`).
#
# Then, on the trainer host (this host for a single-engine CPU smoke):
#   .venv/bin/python -m native_runner.training.v4.build_ppo_multinode_topology \
#       --node-ready runs/a51-node0/engine-node-ready.json --output runs/topology.json \
#       --trainer-job-id 1 --trainer-host "$(hostname -s)" --gpus 1 \
#       --worker-cpus 0 --control-cpus 1,2,3,4,5,6,7
#
# The PPO trainer itself is CUDA/NCCL-only, so a real update still needs the
# rented GPU; this script makes the A51 a normal engine node for that run.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
engines=4
base_port=26789
worker_port=29900
worker_host="127.0.0.1"
global_start=0
serial=""
output_dir=""
window_mode="visible"
cpu_map="auto"
skip_launch=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --engines) engines="${2:?missing value}"; shift 2 ;;
    --base-port) base_port="${2:?missing value}"; shift 2 ;;
    --worker-port) worker_port="${2:?missing value}"; shift 2 ;;
    --worker-host) worker_host="${2:?missing value}"; shift 2 ;;
    --global-start) global_start="${2:?missing value}"; shift 2 ;;
    --serial) serial="${2:?missing value}"; shift 2 ;;
    --output-dir) output_dir="${2:?missing value}"; shift 2 ;;
    --window-mode) window_mode="${2:?missing value}"; shift 2 ;;
    --cpu-map) cpu_map="${2:?missing value}"; shift 2 ;;
    --skip-launch) skip_launch=1; shift ;;
    -h|--help)
      sed -n '2,/^[^#]/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$output_dir" ]]; then
  echo "--output-dir is required" >&2
  exit 2
fi
mkdir -p "$output_dir"
output_dir="$(cd -- "$output_dir" && pwd)"
python="$repo_root/.venv/bin/python"
[[ -x "$python" ]] || python="$(command -v python3)"

if (( ! skip_launch )); then
  echo "[1/4] Launching the A51 cluster"
  launch_args=(--engines "$engines" --base-port "$base_port" --window-mode "$window_mode" --cpu-map "$cpu_map")
  if [[ -n "$serial" ]]; then
    launch_args+=(--serial "$serial")
  fi
  "$repo_root/launch_cluster_a51.sh" "${launch_args[@]}"
else
  echo "[1/4] Reusing the running cluster"
fi

echo "[2/4] Attesting engines and writing the node manifest"
manifest="$output_dir/engine-node-ready.json"
"$python" "$repo_root/tools/native_analysis/a51_engine_node.py" \
  --serial "$serial" --base-port "$base_port" --engines "$engines" \
  --global-start "$global_start" --worker-host "$worker_host" --worker-port "$worker_port" \
  --output "$manifest" >/dev/null

echo "[3/4] Starting the remote PPO worker service on port $worker_port"
worker_name="a51-ppo-worker-$worker_port"
if [[ -f "$output_dir/$worker_name.pid" ]]; then
  old_pid="$(cat "$output_dir/$worker_name.pid" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "  stopping previous worker $old_pid"
    kill -TERM "$old_pid" 2>/dev/null || true
    sleep 1
  fi
fi
: > "$output_dir/$worker_name.log"
detach=()
if command -v setsid >/dev/null 2>&1; then
  detach=(setsid)
fi
"${detach[@]}" nohup "$python" -m native_runner.training.v4.remote_ppo_worker_server \
  --bind-host "$worker_host" --port "$worker_port" \
  --global-start "$global_start" --engine-count "$engines" \
  --engine-base-port "$base_port" \
  >> "$output_dir/$worker_name.log" 2>&1 &
worker_pid=$!
echo "$worker_pid" > "$output_dir/$worker_name.pid"
sleep 1
if ! kill -0 "$worker_pid" 2>/dev/null; then
  echo "the remote PPO worker failed to start:" >&2
  sed -n '1,40p' "$output_dir/$worker_name.log" >&2 || true
  exit 1
fi

echo "[4/4] Ready"
echo "  manifest: $manifest"
echo "  worker:   pid $worker_pid, log $output_dir/$worker_name.log"
echo "  next: build the topology with build_ppo_multinode_topology, then run the trainer"
