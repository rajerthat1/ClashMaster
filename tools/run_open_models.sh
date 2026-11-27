#!/usr/bin/env bash
# Evaluate open-weight V4 checkpoints on the pinned 16.635.1 x86_64 engine.
#
# No phone, root, emulator, or APK is needed: the host maps the official
# 16.635.1 libg.so with crx and attaches the x86_64 probe, then the normal
# evaluator drives it. Output goes to runs/open-models/<timestamp>/.
#
#   tools/run_open_models.sh <a.pt> [b.pt] [match-config] [games] [seed]
#
# Two checkpoints play head-to-head. With one checkpoint both seats use it,
# which is a quick smoke test. Checkpoint A always plays deck0 and B deck1,
# so pick the config that gives each model its own deck, for example:
#
#   tools/run_open_models.sh \
#     .resource/RoyaleFish/models/B-general-v275.pt \
#     .resource/RoyaleFish/models/A-hog26-v1881.pt \
#     tools/native_analysis/match-configs/xbow-vs-hog26.json 10 101
#
# B-general-v275 opens on the X-Bow deck and A-hog26-v1881 on Hog 2.6.
set -euo pipefail

A=${1:?usage: run_open_models.sh <a.pt> [b.pt] [match-config] [games] [seed]}
B=${2:-$1}
CONFIG=${3:-tools/native_analysis/match-configs/xbow-vs-hog26.json}
GAMES=${4:-2}
SEED=${5:-1}
PORT=${PORT:-26789}
DEVICE=${DEVICE:-cpu}
ONLINE_CPUS=${ONLINE_CPUS:-$(nproc)}
ENGINE_INDEX=${ENGINE_INDEX:-$((PORT - 26789))}
# The probe pins its control lane to one CPU and refuses an offline index.
# Fall back to CPU 0 when the port-derived index is outside this host.
if (( ENGINE_INDEX < 0 || ENGINE_INDEX >= ONLINE_CPUS )); then
  ENGINE_INDEX=0
fi

cd "$(dirname "$0")/.."

PY=.venv/bin/python
[[ -x $PY ]] || PY=python
PROBE=native_runner/probe/out/x86_64/libcrprobe.so
[[ -f $PROBE ]] || { echo "missing probe: $PROBE (build with native_runner/probe/build_probe.sh --abi x86_64)" >&2; exit 2; }
[[ -f $CONFIG ]] || { echo "missing match config: $CONFIG" >&2; exit 2; }

NAME=$(date +%Y%m%d-%H%M%S)-$(basename "$A" .pt)-vs-$(basename "$B" .pt)
OUT=runs/open-models/$NAME
mkdir -p "$OUT"
LOG=$OUT/evaluate.log
ENGINE_LOG=$OUT/engine.log

ENGINE_SECONDS=$((GAMES * 150 + 600))
"$PY" tools/native_analysis/crx_engine_host.py \
  --seconds "$ENGINE_SECONDS" --port "$PORT" --engine-index "$ENGINE_INDEX" > "$ENGINE_LOG" 2>&1 &
ENGINE_PID=$!
cleanup() {
  kill "$ENGINE_PID" 2>/dev/null || true
  wait "$ENGINE_PID" 2>/dev/null || true
}
trap cleanup EXIT

# crx boots the engine and installs the probe; give it up to two minutes.
for _ in $(seq 1 120); do
  if "$PY" - "$PORT" <<'PY' 2>/dev/null
import socket, sys
with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1):
    pass
PY
  then
    break
  fi
  if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
    echo "engine host exited during boot; see $ENGINE_LOG" >&2
    tail -20 "$ENGINE_LOG" >&2 || true
    exit 1
  fi
  sleep 1
done

echo "model A: $A"
echo "model B: $B"
echo "config : $CONFIG"
echo "output : $OUT"

CR_NATIVE_BUILD_ABI=x86_64 \
CR_PROBE="$PROBE" \
OMP_NUM_THREADS=1 \
  "$PY" -m native_runner.training.v4.evaluate \
    --checkpoint-a "$A" --checkpoint-b "$B" \
    --games "$GAMES" --match-config "$CONFIG" \
    --output "$OUT" --sample --seed "$SEED" \
    --device "$DEVICE" --port "$PORT" | tee "$LOG"

echo "done: $OUT/summary.json"
