#!/usr/bin/env bash
# Run one native match block in chunks, each behind a fresh engine host.
#
# A native block is the promotion gate, and a transport can die mid-match, so
# the unit of work is a chunk: one engine host, one evaluator invocation, and
# one retry before the block stops.  Resume by passing the seed a chunk
# stopped on; `native_block.py` merges the finished chunk directories.
#
#   tools/native_analysis/native_block_run.sh <name> <a.pt> <b.pt> \
#       <match-config.json> <start-seed> <games> <chunk> <out-root>
#
# Independent blocks can run side by side: set PORT (and, for the engine's
# control-cpu binding, ENGINE_INDEX) per process.  The default keeps the
# historical single-host shape (port 26789, engine index 0).  DEVICE selects
# the evaluator's policy device; it defaults to cpu for gate reproducibility.
#
# Example (50-game pilot gate in 10-game chunks, seeds 101-150):
#
#   tools/native_analysis/native_block_run.sh pilot-vs-seed \
#       runs/crforge-c4/pilot-s39-checkpoint.pt \
#       .resource/RoyaleFish/models/B-general-v275.pt \
#       runs/native-parity-s40/match-config-mirror-xbow.json \
#       101 50 10 runs/native-parity-s40
#
# A completed chunk writes <out-root>/<name>-seeds<first>-<last>/, which is what
# native_block.py consumes.  The block stops rather than skipping a game, so a
# failure names the seed to resume from.
set -u
NAME=$1; A=$2; B=$3; CONFIG=$4; START=$5; GAMES=$6; CHUNK=$7; ROOT=$8

cd "$(dirname "$0")/../.." || exit 1
mkdir -p "$ROOT"
LOG="$ROOT/$NAME.log"
: > "$LOG"
ATTEMPTS=2
PORT=${PORT:-26789}
ENGINE_INDEX=${ENGINE_INDEX:-$((PORT - 26789))}
DEVICE=${DEVICE:-cpu}

seed=$START
end=$((START + GAMES))
while [ "$seed" -lt "$end" ]; do
  n=$CHUNK
  remaining=$((end - seed))
  if [ "$remaining" -lt "$n" ]; then n=$remaining; fi
  out="$ROOT/${NAME}-seeds${seed}-$((seed + n - 1))"
  echo "=== chunk $out ($n games)" | tee -a "$LOG"
  ok=0
  attempt=1
  while [ "$attempt" -le "$ATTEMPTS" ] && [ "$ok" -eq 0 ]; do
    echo "=== attempt $attempt" | tee -a "$LOG"
    rm -rf "$out"
    .venv/bin/python tools/native_analysis/crx_engine_host.py --seconds 5400 \
      --engine-index "$ENGINE_INDEX" --port "$PORT" \
      >> "$ROOT/$NAME-engine.log" 2>&1 &
    engine=$!
    for _ in $(seq 1 120); do
      if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then break; fi
      sleep 1
    done
    CR_NATIVE_BUILD_ABI=x86_64 \
    CR_PROBE=native_runner/probe/out/x86_64/libcrprobe.so \
    OMP_NUM_THREADS=1 \
      timeout 7200 .venv/bin/python -m native_runner.training.v4.evaluate \
        --checkpoint-a "$A" --checkpoint-b "$B" --games "$n" \
        --match-config "$CONFIG" --output "$out" --sample --seed "$seed" \
        --device "$DEVICE" --port "$PORT" \
        >> "$LOG" 2>&1
    status=$?
    kill "$engine" 2>/dev/null
    wait "$engine" 2>/dev/null
    count=0
    if [ -f "$out/matches.jsonl" ]; then count=$(wc -l < "$out/matches.jsonl"); fi
    echo "=== attempt $attempt exit=$status games=$count/$n" | tee -a "$LOG"
    if [ "$status" -eq 0 ] && [ "$count" -eq "$n" ]; then ok=1; fi
    attempt=$((attempt + 1))
  done
  if [ "$ok" -eq 0 ]; then
    echo "=== STOP at seed $seed (resume from here)" | tee -a "$LOG"
    exit 1
  fi
  seed=$((seed + n))
done
echo "=== block $NAME complete" | tee -a "$LOG"
