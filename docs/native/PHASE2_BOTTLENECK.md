# Phase 2 — throughput and bottleneck plan

Status: **measured 2026-11-14**, host `i5-1235U` (10 physical / 12 logical
cores), 12 rootless workers on `39031–39042`, all evidence in
`builds/native/phase2-20261114T015424Z/`.

This is the Phase 2 deliverable: the engine-only target, a measured breakdown
of the training loop, and the next steps in cost order. It does not change the
Phase 3 model choice; it explains what has to be fixed before a long PPO run.

## Verdict

1. **The engine is not the bottleneck.** At 100 native ticks per RPC the host
   serves **36.4k ticks/s at 8 workers** and **40.2k at 12**
   (≈9.6 / 10.6 matches/s at 3,790 ticks/match). The Phase 2 engine-only gate
   of 10 matches/s is met at 12 workers and is within 4 % at 8.
2. **At one decision per tick, the JSON-RPC boundary is the bottleneck.** The
   worker spends roughly half of each transition serializing the response and
   a third parsing the observation; the engine JNI call is under 10 %.
3. **Two already-available levers recover most of the loss.**
   `joint_training_transition_fast_v1` (`transition_mode="fast-json"`) removes
   the worker's `JSONObject` round trip and is **1.5–2.1× faster** at one tick
   per RPC. Stepping 4–8 ticks per decision amortizes the RPC and inference and
   lifts the full sandbox loop from **0.074 to 0.277 / 0.522 matches/s** at 8
   workers (decision intervals 1 / 4 / 8, measured with the real collector
   after the training-stack change).
4. **The next bottleneck is model inference.** With fast-json and interval 8,
   inference is still **43.6 %** of wall time. The R0 elite-v3 stack measures
   **125–269 ms per forward pass** on this CPU (batch 2–8), so on-policy R0
   rollouts are not viable without a GPU or a much smaller model. The sandbox
   recurrent policy is 2.8–7.4 ms and is the only CPU-feasible option today.
5. **Keep the training host at 8 workers.** 12 workers adds ≈10 % engine-only
   throughput but no RPC throughput (897–988 RPC/s at both 8 and 12), and the
   Phase 0 one-tick sweep already showed 12 is oversubscribed. Use 8 for the
   training loop; 12 only for engine-only/coarse-decision throughput.

## What was measured

`tools/native_bringup/profile_native_loop.py` has three phases:

| Phase | Workload | Answers |
| --- | --- | --- |
| `transport` | native ticking with empty joint actions, no policy | RPC + JSON + engine cost per tick |
| `loop` | full self-play matches with the sandbox recurrent policy | end-to-end matches/s and stage attribution |
| `model` | forward-pass microbenchmarks | model cost in isolation |

Caveats:

- `--profile-native` adds one **extra** response serialization inside the
  worker (`response.toString()` probe followed by the real write). Profiled
  runs are therefore ~40–50 % slower and are used for **shares**, not
  throughput.
- The loop uses a randomly initialized `RecurrentPolicyValueNet`, not a trained
  checkpoint. Inference cost is representative; win rates are not.
- Match lengths vary (3,681–5,849 ticks), so ticks/s → matches/s uses the
  measured per-run tick count, not a fixed divisor. Transport equivalence uses
  3,790 ticks/match and is labelled as an estimate.
- All numbers are CPU-only, torch threads 2 unless stated.

## 1. Transport: legacy vs fast-json, one tick per RPC

Aggregate native ticks/s, 10 s measured after 2 s warm-up:

| Workers | legacy | fast-json | speed-up |
| ---: | ---: | ---: | ---: |
| 4 | 410.3 | 928.3 | 2.26× |
| 8 | 611.0 | 1,024.9 | 1.68× |
| 12 | 562.0 | 973.6 | 1.73× |

Equivalent matches/s at 3,790 ticks/match: legacy **0.11 / 0.16 / 0.15**,
fast-json **0.24 / 0.27 / 0.26**. Per-RPC latency at 8 workers falls from
10.2 ms (legacy) to 5.9 ms (fast-json); the client's own serialize + parse is
only ~0.15 ms, so almost all of the gain is inside the worker.

Repeat runs of the same configuration vary by up to ~20 % on this laptop
(thermal/frequency scaling); the direction and rough magnitude are stable.

## 2. Decision interval: amortizing the RPC

Fast-json, 8 and 12 workers, native ticks per RPC:

| Ticks/RPC | 8 workers ticks/s | 8 eq. matches/s | 12 workers ticks/s | 12 eq. matches/s | RPC/s (8 / 12) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1,024.9 | 0.27 | 973.6 | 0.26 | 1,025 / 974 |
| 4 | 3,836.7 | 1.01 | 3,951.7 | 1.04 | 960 / 988 |
| 8 | 7,357.7 | 1.94 | 7,383.7 | 1.95 | 921 / 924 |
| 100 | 36,382.2 | 9.60 | 40,249.8 | 10.62 | 367 / 406 |

The RPC rate is nearly flat at ~920–990 RPC/s for 1–8 ticks/RPC: the host is
limited by request handling, not by native ticks. Only at 100 ticks/RPC does
the limit move to engine CPU (≈4.5–5.0k ticks/s per worker).

**Implication:** a decision interval of 4–8 ticks costs nothing in transport
and cuts policy-inference calls by 4–8×. A decision interval of 1 is the worst
case for this stack.

## 3. Full loop: end-to-end matches/s

`VectorNativeSelfPlayCollector`, X-Bow deck, one match per worker, 6,000-tick
cap:

| Workers | Mode | Ticks/decision | Matches/s | Ticks/s | Seconds/match |
| ---: | --- | ---: | ---: | ---: | ---: |
| 4 | legacy | 1 | 0.041 | 156.1 | 24.63 |
| 4 | fast-json | 1 | 0.058 | 228.4 | 17.13 |
| 8 | fast-json | 1 | 0.074 | 314.6 | 13.55 |
| 8 | fast-json | 4 | **0.293** | 1,189.9 | 3.41 |
| 8 | fast-json | 8 | **0.503** | 2,317.0 | 1.99 |
| 8 | fast-json via `decision_ticks` | 4 | **0.277** | 1,263.5 | 3.61 |
| 8 | fast-json via `decision_ticks` | 8 | **0.522** | 2,302.6 | 1.91 |

The last two rows use the real collector argument (no environment
monkey-patch) after the training-stack change; they reproduce the interval-4
and interval-8 results.

Attribution of the round wall (inference is the serial main-thread batch;
transition wait is the worker round trip; python is mask probe + encoding +
mask build + reward + trajectory):

| Configuration | Inference | Transition wait | Python observation/mask |
| --- | ---: | ---: | ---: |
| 4 workers, legacy, 1 tick | 30.9 % | 62.2 % | 5.6 % |
| 4 workers, fast-json, 1 tick | 48.1 % | 41.4 % | 8.4 % |
| 8 workers, fast-json, 1 tick | 48.1 % | 39.9 % | 9.9 % |
| 8 workers, fast-json, 4 ticks | 46.9 % | 41.5 % | 9.3 % |
| 8 workers, fast-json, 8 ticks | 43.6 % | 43.5 % | 10.3 % |

Two conclusions:

- fast-json removes roughly half of the transition wait and makes inference the
  co-dominant cost at every interval;
- observation encoding and mask construction are **not** the first lever:
  ~0.7–1.4 s per match, under 10 % of wall time. The earlier “profile the
  observation path” hypothesis is only partly right — the observation **RPC**
  is expensive, the Python encoder is not.

At 8 workers and interval 1, the per-match main-thread budget is 7.9 s
(inference 6.5 s, encoding 0.73 s, mask build 0.55 s, mask probe 0.03 s) while
each worker's 4,185 transitions take 24.5 s in parallel. That is why the loop
scales with the decision interval rather than with more workers.

## 4. Where the worker time goes (profiled legacy run)

`timing_v1` from the instrumented 8-worker, one-tick legacy run below
(`profile-w8-legacy-steps1-profiled.json`; 3,840 ticks and 3,742 decisions per
match, 73.8 s receive time per match). The probe serializes the response once
more, so subtract that extra pass before reading shares:

| Stage | Instrumented s/match | Corrected estimate | Share of unprofiled receive |
| --- | ---: | ---: | ---: |
| response JSON serialization | ≈48.9 (22.9 probe + ≈26 write) | 26.0 | ≈51 % |
| observation JSON parse | 16.1 | 16.1 | ≈32 % |
| step result JSON parse | 4.4 | 4.4 | ≈9 % |
| engine `step` JNI | 2.3 | 2.3 | ≈4.6 % |
| engine `observe` JNI | 1.6 | 1.6 | ≈3.1 % |
| request parse + joint action | 0.5 | 0.5 | ≈1 % |
| **extra probe serialization** | **22.9** | 0 | — |

So the native engine is ~8 % of worker transition time at one tick per RPC;
the remaining ~90 % is JSON encoding/parsing of the step result and the full
training state. That is exactly what `fast-json` bypasses, and why it wins.

The same run measured 167.4 ticks/s and 0.044 matches/s with profiling enabled
(vs 611.0 ticks/s unprofiled in the transport A/B), which is the size of the
probe overhead. Do not quote profiled throughput as a capacity number.

## 5. Model cost

Forward pass, CPU, median of 20 iterations:

| Model | Batch | Threads | p50 | Parameters |
| --- | ---: | ---: | ---: | ---: |
| sandbox `RecurrentPolicyValueNet` | 2 | 1 | 2.78 ms | — |
| sandbox `RecurrentPolicyValueNet` | 8 | 2 | 4.47–9.85 ms | — |
| sandbox `RecurrentPolicyValueNet` | 8 | 8 | 4.86 ms | — |
| R0 elite-v3 | 2 | 8 | 125.3 ms | 36.85 M |
| R0 elite-v3 | 8 | 2 | 158.2 ms | 36.85 M |
| R0 elite-v3 | 8 | 8 | 190.5 ms | 36.85 M |

R0 is 20–90× slower than the sandbox recurrent policy on this CPU and does not
scale with thread count. At ~1,000 decisions/match it would need minutes of
serial inference per match even after the decision-interval fix.

## 6. Measured next step (in order)

1. **Run the training collector at fast-json + decision interval 4 or 8.**
   ~~The collectors hardcode `steps=1`; add a `decision_ticks` argument and
   plumb it through `training/train.py` and `selfplay_v2/train.py`.~~
   **Implemented 2026-11-14** in the sandbox clone: both collectors take
   `decision_ticks`, `training/train.py` and `selfplay_v2/train.py` expose
   `--decision-ticks`, `--transition-mode` defaults to `fast-json`, and the
   run manifest records both. Re-measured with the real collector at 8
   workers: **0.277 matches/s** at interval 4 and **0.522 matches/s** at
   interval 8 (3,613 and 1,913 ms/match;
   `profile-w8-fast-json-steps4-collector.json`,
   `profile-w8-fast-json-steps8-collector.json`). This is the single largest
   available step and it is now in place.
2. **Decide the model/inference host.** If Phase 3 stays on R0 elite-v3,
   inference must move to a GPU (or a distilled/smaller policy must be
   selected); CPU R0 is not a training option. If Phase 3 uses the sandbox
   recurrent policy, 8 workers × batch 16 at interval 8 stays CPU-feasible.
3. **Then re-profile.** At interval 8, inference and transition are close to
   equal (43.6 % / 43.5 %). The next win is either larger policy batches
   (more workers per batch does not help: the RPC ceiling is at 8), a faster
   forward pass, or a quantized/compiled model.
4. **Do not optimize the Python observation encoder yet.** It is 0.7–0.8 s per
   match at interval 1, and drops to 0.11 s at interval 8.
5. **Lock host sizing at 8 workers for training.** 12 workers is an
   engine-only point; it does not raise RPC throughput and was already
   oversubscribed in the one-tick sweep.

## Reproduce

```bash
cd /home/raj/Desktop/ClashMaster
OUT=builds/native/phase2-$(date -u +%Y%m%dT%H%M%SZ)

# transport A/B (start 8 workers on 39031-39038 first)
.venv/bin/python tools/native_bringup/profile_native_loop.py \
    --ports 39031-39038 --skip-start --phases transport \
    --transition-mode legacy --transport-steps 1 --label w8-legacy-steps1 \
    --output-dir "$OUT"
.venv/bin/python tools/native_bringup/profile_native_loop.py \
    --ports 39031-39038 --skip-start --phases transport \
    --transition-mode fast-json --transport-steps 8 --label w8-fast-steps8 \
    --output-dir "$OUT"

# full loop at the proposed decision interval
.venv/bin/python tools/native_bringup/profile_native_loop.py \
    --ports 39031-39038 --skip-start --phases loop \
    --transition-mode fast-json --loop-steps 8 --label w8-loop-steps8 \
    --output-dir "$OUT"

# model microbenchmarks
.venv/bin/python tools/native_bringup/profile_native_loop.py \
    --workers 1 --phases model --torch-threads 2 --model-batch-size 8
```

Worker lifecycle and the rootless install are in
[`PHASE0_BRINGUP.md`](PHASE0_BRINGUP.md); the Phase 0 sweep remains the
capacity baseline for one-tick decision loops.
