#!/usr/bin/env bash
# Session 51 — what was run on the rented Thunder hosts, as a script.
#
#   A.  the engine reproduction (the C2 miscompile) + LogCompilation
#   B.  the warmup-corrected scale sweep (8..128 lanes, steady state)
#   C.  the C1 workaround arm on the same host
#
# Requires a checkout with .resource/ (engine jars, RoyaleFish tables and
# B-general-v275.pt), PYTHONPATH on .resource/crforge/python, and the python
# dependencies (jpype1, numpy, orjson, gymnasium, pettingzoo).  hsdis is optional
# but needed for the disassembly: `apt install libhsdis0-fcml` and link
# /usr/lib/x86_64-linux-gnu/libhsdis.so.0.0.0 to $JAVA_HOME/lib/hsdis-amd64.so.
#
# MPS was not run: the base template has no nvidia-cuda-mps-control (it ships
# with the driver, not the toolkit).  See HANDOFF.md (session 51b).
set -u

cd "$HOME/clashmaster"
export OMP_NUM_THREADS=1
export PYTHONPATH=".resource/crforge/python${PYTHONPATH:+:$PYTHONPATH}"
CKPT=".resource/RoyaleFish/models/B-general-v275.pt"
RUNS="runs/s51"
mkdir -p "$RUNS"
PY=python3

echo "=== host ===" | tee "$RUNS/host.txt"
lscpu >> "$RUNS/host.txt"
java -version 2>> "$RUNS/host.txt"
cat "$RUNS/host.txt"

# ---------------------------------------------------------------------------
# A. The engine reproduction — this is the working reproducer for the C2 defect
#    (the stand-alone hotspot_repro.py probe is a false negative on this host;
#    see FINDINGS.md 3.88).  Exit 139 and `segfault at c` in dmesg, no hs_err.
# ---------------------------------------------------------------------------
echo ""
echo "=== A: engine reproduction (default C2) ==="
timeout 900 $PY tools/crforge/self_play.py \
    --count 4 --segments 3 --decisions-per-segment 24 \
    --device cpu --ppo-update \
    --jvm-arg=-XX:+UnlockDiagnosticVMOptions \
    --jvm-arg=-XX:+LogCompilation \
    --jvm-arg=-XX:LogFile="$PWD/$RUNS/hotspot-%p.log" \
    --jvm-arg=-XX:CompileCommand=print,org.crforge.env.SeatIds::of \
    --checkpoint "$CKPT" \
    --save-checkpoint "$HOME/ck-s51.pt" \
    --output "$RUNS/cpu-producer.json" > "$RUNS/cpu-producer.log" 2>&1
echo "engine exit=$?  (139 = the reproduction)"
dmesg 2>/dev/null | grep -i segfault | tail -3
# map the fault PC: python3 find_nmethod.py "$RUNS"/hotspot-*.log 0x<ip>

# ---------------------------------------------------------------------------
# B. The warmup-corrected sweep: steady state per lane count.
# ---------------------------------------------------------------------------
echo ""
echo "=== B: scale sweep ==="
for LANES in 8 16 32 64 128; do
    OUT="$RUNS/sweep-lanes${LANES}.json"
    timeout 900 $PY tools/crforge/self_play.py \
        --count "$LANES" \
        --segments 3 --decisions-per-segment 32 \
        --device cuda \
        --fast-forward --full-cuda-graph --no-validate \
        --no-rollout \
        --checkpoint "$CKPT" \
        --output "$OUT" > "${OUT%.json}.log" 2>&1
    echo "lanes=$LANES exit=$?"
done

# ---------------------------------------------------------------------------
# C. The workaround arm: C1 only.
# ---------------------------------------------------------------------------
echo ""
echo "=== C: C1 workaround ==="
timeout 600 $PY tools/crforge/self_play.py \
    --count 4 --segments 3 --decisions-per-segment 24 \
    --device cpu --ppo-update \
    --jvm-arg=-XX:TieredStopAtLevel=1 \
    --checkpoint "$CKPT" \
    --save-checkpoint "$HOME/ck-s51-c1.pt" \
    --output "$RUNS/cpu-producer-c1.json" > "$RUNS/cpu-producer-c1.log" 2>&1
echo "c1 exit=$?"

echo ""
echo "Results in $RUNS/"
