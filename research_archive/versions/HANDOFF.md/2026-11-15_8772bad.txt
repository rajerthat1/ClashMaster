# Session handoff — 2026-11-14 — Null's Royale 16.635.1

> **Read [`ROADMAP.md`](ROADMAP.md) first.** It is the single plan.
> This file is the next-session starting point.

## Decision — what changed

The previous plan optimized **engine throughput** by moving to the x86_64
`cr-native` 15.535.29 runtime. That was the right call for throughput, but it
optimized the wrong variable: **Null's runs 16.635.1 ARM64**, and the 15.535.29
x86 engine cannot connect to Null's ladder.

The repo already has the direct path:

- **FirstLight V4 General** — `checkpoints/General/checkpoint-step-00000460.pt`,
  12,684,339 params, `ppo-league-snapshot`, trained across decks.
- **IL_Replay** — `datasets/royaleapi/`, 252,238 replays / 17,836,160 actions,
  already local.
- **16.635.1 offline APK + cluster APK + probe** — `builds/cr-ai-16.635.1.apk`,
  `builds/cr-engine-cluster-16.635.1.apk`,
  `native_runner/probe/out/libcrprobe-16.635.1.so`.
- **A51 cluster/deploy tooling** — `deploy_a51.sh`, `launch_cluster_a51.sh`.
- **V4 IL/PPO stack** — `native_runner/training/v4/`.

**R0 is retired.** It is a third observation contract with no trained
checkpoint, no local corpus and no 16.x adapter. Its code/evidence is archived;
do not start another R0 run.

**Primary track:** 16.635.1 on the A51 with the V4 General model.
**Proxy lab:** cr-native 15.535.29 x86, for throughput/reward/parity only.

> **2026-11-14 (late): the x86_64 16.635.1 engine now runs on this host**
> through `.resource/Clashback` + `crx`, with a native probe port and scripted
> parity seeds 1–5 matching the A51 corpus. The A51 remains the ARM64
> deployment oracle. See §3.35 and "Next session" step 1.

> **2026-11-14 (session 2): first GPU PPO update ran; the A51 is the wrong
> training engine.** 1×A6000 (ThunderCompute) completed a full update with
> **0 rejected actions** and a saved checkpoint, but the learner only reached
> **19 lane-decisions/s (0.04 matches/s)**. Measured at the PPO 5-tick decision
> cadence, four A51 engines cap at **0.26 matches/s**, while **one x86_64
> process does 0.61 matches/s** and is CPU-bound. Two config bugs also blocked
> the gate: the flat `gate_temperature 0.2` drove `ACT` to 0 over 5,120 ticks,
> and the trainer hard-requires a 7,695/20 deck pool the local corpus cannot
> produce. The GPU box was deleted after the smoke (~$0.23). See
> "GPU smoke 2026-11-14 — results and what they mean".

> **2026-11-14 (session 4): the x86_64 PPO worker path works, and 16.635
> checkpoints now train through it.** The `Error -5 … truncated stream` decode
> failure was a **host-side zlib resolution bug** (`libz` preloaded
> `RTLD_LOCAL`, so crx's missing-symbol stub answered `compress2` with `Z_OK`
> and a zero-byte output). The engine spec now carries the checkpoint's
> `competitive_release` + `model_config`, so engine-node tensorizers build the
> 16.635 catalogs and the 21-column schema instead of the pinned 15.535/16-column
> default. `B-general-v275` collects through the CPU worker on one x86 engine
> with 0 UNKNOWN effects (29 lane-decisions/s, model-time bound). See
> `FINDINGS.md` §3.37. The next lever is still the learner loop's one-packet-per-
> forward batching, not the transport.

> **2026-11-14 (session 5): batching measured; host engines are multi-process.**
> The 20–40 ms window advice was wrong for the A51: a window shorter than one
> engine round-trip (~100–150 ms) re-batches the same two-engine cohort every
> time. At `CR_PPO_INFERENCE_BATCH_WAIT_MS=250`, four A51 engines coalesce
> **3.88 packets/forward and 33 forwards instead of 64**; CPU wall time stays
> flat (the 17.5M model is the local bottleneck), which is exactly the
> per-forward cost the 4 %-utilised GPU was idling on. `cr_probe_attach` now
> initialises engine identity, so `crx_engine_host.py --engine-index N` gives
> each x86 host process its own port (`26789 + N`) and control CPU — verified
> with two concurrent engines. The same session confirmed the §3.37 caveat:
> B-general at gate temperature **1.0** reproducibly trips a native action
> failure (env 5), while the same seeded run at **0.2** is clean. See
> `FINDINGS.md` §3.38. *(Superseded by session 6 below: the delay-aware port is
> in and gate 1.0 now collects cleanly in six segments.)*

> **2026-11-14 (session 6): delay-aware candidate legality is ported, and
> full-rate ACT collection passes at gate temperature 1.0.** `B-general-v275`
> declares `delay_aware_legality: true`, but the decoder and `BattleEnvV1` were
> still applying the 15.535 decision-tick rule. Both now check elixir at the
> touch delay bin (`max(e0, min(10, e0 + regen*tick)) - spent`) with the match
> multiplier, the environment exports `touch_window_elixir` /
> `affordable_from_bin`, and compound validation runs in touch order. Gates:
> **1×A51 8 lanes × 5 seeds: 5/5 segments**, **4×A51 8 lanes (the §3.38 failing
> shape): 28.5 lane-decisions/s, 1/1**, x86 1 engine: 23.9/s, 0 rejected and
> **3,979 native command parses with 0 failures**. One post-port failure
> (`env 6`) did not reproduce in six later segments and is not claimed fixed;
> `CR_PPO_FAILURE_DUMP` now captures the failing actions if it returns. See
> `FINDINGS.md` §3.39.

> **2026-11-14 (session 7): the batching window is a dead end; the closed loop is
> the critical path.** The combined config the GPU job would use — gate 1.0 +
> delay-aware + 250 ms — halves forwards (321 → 160) and reaches 3.98–4.00
> packets/forward, but is **net slower** than the 2 ms default (271 s vs
> 288–331 s): the added wait exceeds the saved per-forward cost because the CPU
> model is row-bound, not forward-bound. **Keep 2 ms.** On a GPU the forward is
> ~2 ms, so a fixed 250 ms window can only lose; the paid run's 416 ms/packet was
> ~390 ms of loop bookkeeping, not forward count. The 16.635 evaluator was also
> fixed (release scope for the ruleset manifest and `BattleEnvV1`, release-aware
> `runtime_scope` counts); seeds 7 and 21 ran `B-general-v275` vs
> `A-hog26-v738` to native terminals with 0 rejected. A **10-segment stress gate**
> at the chosen config is 10/10 clean (4,097 native parses, 0 failures, no
> `env 6`), so the GPU gate is unblocked. See `FINDINGS.md` §3.40.

---

## Next session (2026-11-15, session 8) — pipeline the learner loop

**Read first:** this section, `FINDINGS.md` §3.40, `ROADMAP.md` §7 item 8.
Everything is committed (`620ead5`), tree clean, suite **1,292 passed / 18
skipped**. No GPU is rented and none is needed for this session.

### The one open problem

The learner loop resolves **one packet per forward** and pays the whole closed
loop per packet: ~416 ms wall on the GPU box, of which the model forward is
27 ms and assembly 4 ms. Measured today: **0.04–0.05 matches/s** (4×A51, 8 lanes,
30–38 lane-decisions/s). Nothing else in the stack is blocking.

### Gate ladder — corrected (0.5/s is a *1× x86* number)

| Rig | Channel ceiling (measured) | Gate to pass | Today |
| --- | --- | --- | --- |
| **1× x86 process** | **0.61 matches/s** | **0.5/s** | 0.033/s |
| 4× x86 | 2.44/s | 1.5–2.0/s | — |
| **8× x86** | 4.9/s | **2.5/s (goal)** | — |
| 16× x86 | 9.8/s | 5–8/s (stretch ≈ FirstLight-Zero) | — |
| **4× A51** | **0.26/s** | 0.15–0.2/s | 0.05/s |

4 A51 engines **cannot** reach 0.5/s — the phone channel caps at 0.26/s. The
0.5/s gate is one x86 engine; 2.5/s is eight of them.

### Task order

1. **Rewrite `native_runner/training/v4/async_cluster_self_play.py`**:
   continuous-batched inference instead of a cohort wave. Dispatch queue plus one
   inference thread that batches *all pending* requests (bounded rows) with a
   2–5 ms coalescing window; decode and apply each request as its result lands;
   keep per-(engine, lane) LSTM state; preserve `CR_PPO_FAILURE_DUMP` and the
   rejected-action accounting.
2. **Measure in this order**, because the CPU model is ~93 % of local wall time
   and would mask the gain:
   - `profile_cluster_collection.py` (scripted responder) — the transport + loop
     floor: 1× x86 **≥0.45/s**, 8× x86 **≥2.5/s**;
   - `profile_real_collection.py` on GPU — 1× x86 **≥0.5/s**, 8× x86 **≥2.5/s**;
   - compare against the 4-engine baseline: **311.5 s mean** for 4×8×40 decisions.
3. **Gates on the rewrite:** 10 consecutive segments clean, 0 rejected,
   packets/forward ≥8, wall time down. Record as `FINDINGS.md` §3.41.
4. **Fallback if the rewrite stalls:** N independent collector processes
   aggregated by the learner. Each is model-bound on one core, so 4–6 processes
   is a near-linear 4–6× without touching the loop.
5. **Only after ≥0.5/s:** rent the A6000 and run the 400+ paired-game gate
   (Wilson intervals), then the PPO fine-tune.

### Reference numbers

- 1× x86 channel ceiling 0.61 matches/s at 5 ticks; 4× A51 0.26; 1× A51 0.065.
- FirstLight-Zero: **1,024 episodes / ~104 s ≈ 9.85 matches/s**, 1.17M params,
  async server, ~16 collectors × 8 resident.
- daybreak-cr: ~10 matches/s, 50 collectors, 4 decision servers, 4×A100 learner,
  25–45k decisions/s serving.
- Our serving cost is higher: 17.5M params, 21-column schema, ~4–5× theirs.

### Do not

- Do not raise `CR_PPO_INFERENCE_BATCH_WAIT_MS` again (250 ms is a measured loss).
- Do not build a CPU trainer (same ~30 lane-decisions/s, days of NCCL/CUDA work).
- Do not use phones as a throughput farm (~150 A51s for 10/s); 16 x86 cores is
  the equivalent and they stay the deployment/parity oracle.
- Do not start the crforge adapter unless 16–20 x86 cores still miss 2.5/s.
- Do not rent a GPU before the scripted 1× x86 gate passes.
- Do not rebuild the x86 probe without re-pinning `probe_sha256`; do not edit
  generated `logical_*.inc`.
- Do not trust in-engine win rates as ladder evidence.

### Watch for

The one-off A51 `env 6` (16 consecutive clean segments since) and the one-off
x86 `crx` host death at step 5 (unreproduced). Keep `CR_PPO_FAILURE_DUMP` set and
`adb logcat -s CRNativeProbe` running on any real-engine gate; both remain
diagnostic, not claimed fixed.

### Start state

Engines are **stopped**; the phone is attached and idle (`R58NA0QTHWV`).

```bash
# x86 engines, one process per core, ports 26789+N
for i in 0 1 2 3; do
  .venv/bin/python tools/native_analysis/crx_engine_host.py \
      --engine-index "$i" --seconds 3600 &
done
# A51 (optional this session; use --base-port 26799 to avoid the x86 engine)
./launch_cluster_a51.sh --engines 4 --base-port 26799
```

---

## Ecosystem audit — 2026-11-14 (`CRkonwledagebase`)

`.resource/CRkonwledagebase` indexes **58 projects, 13 capabilities, 90
project×capability relations** with R0–R4 evidence grades. Its own headline is
that **nothing reaches R3/R4**: most entries are author-documented (R1) and a few
are fixed-commit static reviews (R2). Read it as a map, not a parts bin.
(`.resource/REA` is a generic reverse-engineering MCP, not a CR project.)

### Scoreboard for "X-Bow 3.0 beats top players, any deck, Null's or official"

| Component | Best public asset | Evidence | Where we stand |
| --- | --- | --- | --- |
| 16.635 rules at speed | **crforge** 16.635.3/8: 127k steps/s/thread, held tick-for-tick to recorded battles; **RoyaleFish** x86_64 16.635.1 probe, 16 matches/process | author conformance set (43,807 battles); R2 interface audit | **behind** — training runs on 4 phone engines |
| Native ARM64 16.635.1 | our A51 probe: touch hook, 32 resident lanes, attested manifest | verified on device | **ahead of the field** |
| Deck-general policy | RoyaleFish `B-general-v275` — 17.5M params, **16.635 data**, 275 updates | paired evals of its sibling | **behind** — ours is 12.7M on 15.535.86 data, step 460 |
| Specialist policy | RoyaleFish `A-hog26-v738` **71.9% ±2.6** vs its start; Log Bait and anti-2.6 exploiters | 1,000+ game gates | behind, but the recipe is public |
| X-Bow 3.0 | FirstLight-Zero `v1450`: **1.17M params, self-play only, 97.5% vs a 16.8M IL model** | author-reported, 40 games, **mirror only, 15.535.13** | not started |
| Live ladder play | daybreak-cr `play.py --queue --losses N` (Null's); ClashAI vision+taps; Hasty-CR; RoyaleBot `rb-build 0.1.2` for Null's 16.635.1 | all author-documented | **ahead** on in-process control, behind on ladder orchestration |
| Ladder win rate | **ClashAI**: 14,060 trophies / 4,499 matches; Ranked **32.7% → 42.7%** over 150–629 games | author-published | — |

### The honest answer

**Nobody has published a bot that beats top players.** The best public Ranked
number is ~43%, i.e. it is still losing. FirstLight-Zero's 97.5% is an X-Bow
*mirror* result. Hasty-CR states it plainly: *"No trained policy has beaten a
human. One match played by a person found the pathing bug thirty million
training steps never did."* The goal is unclaimed, and the field sits at roughly
"loses narrowly to strong humans".

### The unlock we were missing

The community already ported **this exact client build to x86_64**, and the
artifacts are already in `.resource`:

| Thing | Value |
| --- | --- |
| Official x86_64 `libg.so` 16.635.1 | `.resource/Clashback/game/engine/libg.so`, sha `c4721bd9…fd6539`, BuildID `4b3bf061…` |
| Our ARM64 `libg.so` 16.635.1 | sha `0b580222…e4eaa`, BuildID `179ac4bd…`, `apk_version_code 160635001` |
| Probe port | `RoyaleFish/patches/probe_x86_64_16635.diff`: x86 hooks, `native_call_x86_64.cpp`, x86↔ARM `logical_{funcs,offsets,vtables}.inc`, resident 16-match engine, NDK bionic build |
| 16.635 catalogs | `RoyaleFish/patches/native_runner_16635.diff` + `RoyaleFish/data_16635/` (123 cards, 453 archetypes, 26 abilities) |
| 16.635 models | `RoyaleFish/models/B-general-v275.pt`, `A-hog26-v738.pt` (17,506,419 params each) |

Their tables and ours agree independently: their patch annotates
`0x10f76b0 /* arm 0x10a7230 */`, and our `offset-diff.json` resolves
`kGoldenKnightHop` to `0x010a7230` at **EXACT** confidence. Same client build,
two ports, same answer.

**What that buys:**

1. A **16.635.1 native x86_64 engine on this host** — no phone, no Android UI,
   16 matches per process — the target ruleset at training scale.
2. A **second relocation table** that closed our 37 AMBIGUOUS + 8 of 10 MISSING
   anchors (N2) without new reverse engineering — **done, §3.33**.
3. **Published 16.635 baselines** instead of our stale General, once the
   framework patch is in. Their model config differs (LSTM 768 vs our 512, 5 vs
   3 spatial res blocks, 16.635 catalogs), so it is a patch, not a drop-in.

### X-Bow 3.0 specifics

- `runs/deck-pool.json` holds **111 X-Bow decks, 2.25% of 443,381 uses**. The
  current meta list (`archers-ev1, knight-hero, tesla-ev1, skeletons,
  electro-spirit, fireball, the-log, x-bow`) is the top one at 2,983 uses.
- FirstLight-Zero plays the classic no-evo list (`XBow, Knight, Archers, Tesla,
  Skeletons, ESpirit, Fireball, Log`) and proves the archetype is learnable from
  **self-play alone**, without human data. It does not prove ladder strength.
- The transfer recipe is daybreak-cr's: one-deck specialist, PFSP league, focus
  + hard decks, an IL anchor (`bc_index`, `bc_coef 0.2`), 55% promotion gate.

### Consequence for the plan

Training on the A51 is now the weakest link, not the strongest. Reorder:
**x86 16.635 → adopt 16.635 catalogs/models → GPU eval volume → X-Bow
specialist → ladder.** The A51 keeps its job as the deploy/parity rig, which is
still the only public in-process ARM64 16.635.1 path.

---

## Adopt from the field — gap → source → action

Take the artifact where it is licensed and version-matched; take the **method**
where it is not. Effort: S < 1 day, M 2–5 days, L 1–2 weeks.

| Gap | Source (license) | Take exactly | Effort / viability | Step |
| --- | --- | --- | --- | --- |
| 16.635 catalogs & model config | RoyaleFish `data_16635/` + `native_runner_16635.diff` (Apache-2.0) | data root + framework patch; 123 cards, 453 archetypes, 26 abilities | S — mechanical, already validated upstream | 1.1 |
| Native 16.635.1 engine at scale | RoyaleFish `probe_x86_64_16635.diff` (Apache-2.0) + `Clashback/game/engine/libg.so` (license unknown, **game asset — local use only**) | x86 call path, `x86_hook.h`, `logical_*.inc`, resident multimatch | M — our tree is already 16.635.1 ARM | 1.2–1.3 |
| 37 AMBIGUOUS / 10 MISSING anchors | RoyaleFish `logical_{funcs,offsets,vtables}.inc` | **done** — `crosscheck_royalefish.py` + `royalefish-overrides.json`; 0 AMBIGUOUS left (§3.33) | S — free second opinion, `0x10a7230` agrees | 6 |
| Modern baselines | `B-general-v275`, `A-hog26-v738` (Apache-2.0) | load + run as eval opponents and as the starting point | S — after 1.1 | 1.1 / 4 |
| Training throughput | RoyaleFish `ever_server/collector/learner/packed/loader` (Apache-2.0) | fp16 with action-equality check, CUDA graphs at several batch sizes, central LSTM state table, sparse trajectories, row-packed updates, loader process | M — port piecemeal, measure each | 5 |
| Bulk-training backend | crforge `python/` env (Apache-2.0) | `VectorBattle(256, 8)` + tick-exact 16.635.3/8 core | L — needs a V4→crforge observation adapter; never the final gate | deferred |
| Specialist / league recipe | daybreak-cr settings + `loop.py` (license unknown → **reimplement**) | PFSP, focus + hard-deck shares, `bc_index`/`bc_coef 0.2` anchor, 55% promotion gate, rollback loop | M | 4 / X-Bow |
| X-Bow 3.0 policy | FirstLight-Zero (Apache-2.0) | self-play recipe, feature/model shape, eval protocol; **checkpoints are 15.535.13 — retrain on 16.635, do not load** | M | after 5 |
| Evaluation discipline | ClashAI `RESULTS.md` (license unknown → method) | Wilson intervals, ≥150-game samples per gate, held-out decks, σ reported per comparison | S — pure protocol | 4 |
| Anti-self-deception | Hasty-CR (MIT) | parameter-perturbation probes: flag wins that depend on one card, tile or timing | S — good gate for crforge-trained policies | 4 |
| Ladder orchestration | daybreak-cr `play.py --queue --losses N`, RoyaleBot `rb-build 0.1.2` (MIT), ClashAI | queue/pending-elixir budget, receipt handling, anti-blind-retry | M — reference only, we control the client in-process | N5 |
| Snapshots / lookahead | Clashback `native_snapshots.py` (license unknown) | per-tick encode/load for MCTS or teacher search | L — deferred until the policy is strong | deferred |
| Remaining RE tooling | REA MCP (MIT) | Ghidra/IDA workflows for the leftover anchors | S — optional convenience | 6 |

**Do not take:** leaked clients or redistributed APKs/assets; `rb-build`
binaries as a dependency; any 15.535-labelled weights as 16.635 weights;
in-engine win rates as ladder evidence.

### Keep the A51 connected?

Yes — but as a **parity oracle**, not the training engine.

- It is the only ARM64 in-process 16.635.1 rig we have, and the x86 port needs a
  ground truth from the *same* client build (`160635001`).
- It does **not** need to be running. Leave it plugged in and authorised
  (`adb`, `svc power stayon usb`); launch engines on demand with
  `./launch_cluster_a51.sh --engines 1` for single-match parity, `--engines 4`
  only for capacity comparisons.
- Before starting the port, capture a **parity corpus** on the A51 (step 1.0
  below) so most of the x86 validation can run offline. That is the highest-value
  use of the phone this week.
- Keeping four engines hot 24/7 buys nothing and re-exposes the §3.28 idle
  control-socket wedge and thermal drift.

---

## Current state

| Item | State |
| --- | --- |
| A51 device | **attached** — `SM-A515U`, Android 13, `arm64-v8a`, serial `R58NA0QTHWV` |
| 16.635.1 client | `nullsroyale.rel.free`, `libg.so` SHA `0b580222…e4eaa`, content `16.635.93` |
| 16.635.1 probe | **`b80f2bdb…`** — RoyaleFish-corrected core table **plus the ARM 16.635 rich-vtable anchors**; 9/9 runner hooks install, no mismatch; evaluator now matches x86 (§3.36) |
| 16.635.1 APK | built (`builds/cr-ai-16.635.1.apk`, 841 MB) |
| Cluster APK | `builds/cr-engine-cluster-16.635.1-rf.apk` (`17893d1c…`) built **and installed**; 1 engine attested with the new probe; 4-engine cold-ready path unchanged |
| Probe port evidence | `docs/native/16.635.1/FINDINGS.md` — §3.26 ruleset binding, §3.27 evaluation, §3.28 N0 soak, §3.30 N3 throughput, §3.31 render-gate crash, §3.33 RoyaleFish cross-check, §3.34 corrected core table, §3.36 ARM observation parity |
| General checkpoint | ready, 12.68M params |
| IL_Replay | ready locally, 252,238 replays |
| V4 evaluator | working on 16.635.1; **use `--sample`** (argmax stands still) |
| N0 soak | **10/10 clean**, 0 problems, 93 abilities / 0 rejections, 0 crashes |
| General vs IL baseline | **General 2–0**, 0 rejected, 2/2 normal terminals |
| General vs Hog 2.6 #1 | **General 0–2**, 0 rejected, 2/2 normal terminals (Hog converts by tick 3601 in game 2) |
| General vs Hog 2.6 #2 | **General 0–2**, both in overtime (6120/6118), 0 rejected, 2/2 normal terminals (§3.29) |
| N3 A51 throughput | **4×8: 12.3–14.4 matches/s**; 4×16: 14.0–14.5; stable, no crashes (§3.30) |
| A51 → PPO wiring | `a51_engine_node.py` + `launch_ppo_a51.sh` landed; manifest accepted by the topology builder |
| A51 parity corpus | **re-captured on `b80f2bdb…`** — `runs/parity-a51/` + `manifest.json`; scripted seeds unchanged, evaluator now 3601/4794 like x86, capacity 1.918 matches/s, deterministic, 0 rejected (§3.36) |
| A51 node manifest | re-attested on `b80f2bdb…`: `runs/a51-node0/engine-node-ready.json`, 4 engines, `parallel_matches: 32`, digest `ce9d4f85…`; topologies rebuilt |
| PPO deck pool | built: `runs/deck-pool.json`, 7,089 decks / 443,381 uses |
| PPO topology | validated for 1 GPU / 6 vCPU (`worker-cpus 0,1,2,3`, `control-cpus 4`) |
| PPO trainer on this host | **CPU-blocked** (collector is CUDA/NCCL-only), but a **full update ran on a rented A6000** with 0 rejected actions (`runs/gpu-smoke-20261114/`); throughput is the open problem, not correctness |
| x86 PPO worker path | **working** — zlib is preloaded `RTLD_GLOBAL` in `crx_engine_host.py` (a `RTLD_LOCAL` preload made crx's stub return `Z_OK` with a zero-byte payload); the host now fails closed if the probe's zlib imports are unresolved (§3.37) |
| 16.635 remote training | **working on CPU** — engine specs carry `competitive_release` + `model_config`; `B-general-v275` collects through the worker with 0 UNKNOWN effects, 29 lane-decisions/s, 1 engine × 8 lanes (§3.37) |
| Learner-loop batching | **measured; not a win** — 250 ms + gate 1.0 coalesces **3.98–4.00 packets/forward, 321 → 160 forwards**, but the added wait makes it **slower than 2 ms** (271 s vs 288–331 s, §3.40). Keep 2 ms; the lever is loop pipelining, not forward coalescing |
| Full-rate ACT collection | **working** — `delay_aware_legality` ported (decoder + env + expert, §3.39); gate 1.0 is clean on 5/5 single-engine seeds, the 4-engine shape, and a **10/10 stress gate** (4,097 native parses / 0 failures, 16 consecutive clean segments). The one post-port `env 6` did not recur; `CR_PPO_FAILURE_DUMP` stays armed |
| 16.635.1 x86_64 engine | **running on this host** — `.resource/Clashback/game/engine/libg.so` (sha `c4721bd9…`) booted headless by the crx ELF loader in 0.3 s; no Android/bionic bundle needed |
| 16.635.1 x86_64 probe | **built** — `native_runner/probe/out/x86_64/libcrprobe.so` (`cd303311…`), `--abi x86_64`; all runner hooks install, `attest` reports `production_ready: true` |
| x86_64 multi-engine host | **working** — `crx_engine_host.py --engine-index N` binds engine N to port `26789+N` and CPU N; two concurrent engines verified with one worker run (§3.38) |
| x86_64 parity corpus | **scripted seeds 1–5 and the General-vs-IL evaluator both reproduce `runs/parity-a51`** — identical ticks/actions on both seats after the A51 vtable fix (§3.36) |
| 16.635 catalogs | **adopted as a verified release** — `native_runner/data/competitive-16.635/` + `native_runner/releases/16.635.1.json`; all 5 catalog ids match `B-general-v275` |
| 16.635 models | **`B-general-v275` and `A-hog26-v738` load and play on the A51** (17.5M, LSTM 768, schema 21; 0 rejected in the play smoke) |
| Cloud GPU | **1× A6000 @ $0.35/h ran and was deleted** (~$0.23). Do not re-rent until a local segment completes in <10 min; the GPU sat at 4 % utilisation |
| Decision-rate reality | A51 4×8 @ 5 ticks = **0.26 matches/s**; one x86_64 process = **0.61**; learner loop on the A51 = **0.04**. See the GPU-smoke section |
| Loop-pipelining gates | **0.5/s on 1× x86** (80 % of its 0.61 ceiling) and **2.5/s on 8× x86** (~50 % of 4.9); 4× A51 caps at 0.26/s, so its gate is 0.15–0.2/s. Measure scripted first, then real-model on GPU |
| cr-native proxy | working, 15.535.29 only |

### State at handoff (processes are ephemeral)

No long-lived probe endpoint is needed. Both oracles start on demand:

```bash
# x86_64 engine + probe on this host (port 26789)
.venv/bin/python tools/native_analysis/crx_engine_host.py --seconds 1800
# independent host engines: 26789+N and control cpu N
for i in 0 1 2 3; do
  .venv/bin/python tools/native_analysis/crx_engine_host.py \
      --engine-index "$i" --seconds 1800 &
done
# A51 ARM64 oracle (phone attached; engine 0 fullscreen)
./launch_cluster_a51.sh --engines 1 --window-mode plain --base-port 26799
```

The x86 host boots in ~0.3 s and can be restarted at will. The A51 waits for
`adb`; re-run the launcher if the phone slept. The parity gate is reproducible
without the phone:

```bash
.venv/bin/python tools/native_analysis/parity_manifest.py \
    --corpus runs/parity-x86 --compare runs/parity-a51/manifest.json \
    --output runs/parity-x86/manifest.json   # exit 0 / "reproduces: True"
```

`runs/parity-a51/` is the archived ARM corpus, `runs/parity-x86/` the matching
x86_64 candidate; both are gitignored but local. The reproducible tools are
`crx_engine_host.py`, `scripted_xbow_match.py`,
`resident_batch_throughput.py`, `multi_decision_match.py`,
`parity_manifest.py` and `tests/test_parity_manifest.py`.

The 4-engine A51 cluster used for re-attestation was stopped at the end of the
session. To resume GPU prep:

```bash
./launch_cluster_a51.sh --engines 4
.venv/bin/python tools/native_analysis/a51_engine_node.py \
    --serial R58NA0QTHWV --engines 4 --output runs/a51-node0/engine-node-ready.json
.venv/bin/python -m native_runner.training.v4.build_ppo_multinode_topology \
    --node-ready runs/a51-node0/engine-node-ready.json \
    --output runs/topology-a6000-1gpu.json --trainer-job-id 1 \
    --trainer-host "$(hostname -s)" --gpus 1 \
    --worker-cpus 0,1,2,3 --control-cpus 4
```

The 4-engine attestation digest is `ce9d4f85…`; the 2-GPU topology needs
`--control-cpus 4,5` (one control CPU per rank).

### N3 — the windowing bug that capped throughput

Every engine except the resumed activity lands in Android's `background`
cpuset, which is pinned to **CPUs 0–1** (little cores). The default
`launch_cluster_a51.sh` therefore gave one fast lane and one ~3x slower lane.
Keeping all engines visible fixes it:

- engine 0 fullscreen, engine 1 split-screen-secondary, engines 2+ freeform
  (plus `force_resizable_activities` / freeform settings);
- big-core CPU map `4,5,6,7`;
- `cached_apps_freezer disabled` (the §3.28 idle control-socket wedge).

`launch_cluster_a51.sh --window-mode visible` (default) does this and reports
each engine's schedtune cgroup. One A51 now supplies ~12–14 matches/s at the
PPO shape (4 engines × 8 lanes) and ~14.5 at 4×16 — well above host CPU
inference (~1 match/s), so **no cloud engine cluster is needed**.


### 16.635.1 port status (verified on the A51)

From `docs/native/16.635.1/FINDINGS.md`:

- Touch JNI hook is **port-ready** (`0x0156f790`).
- Hand/deck model offsets and the replay-controller trio are recovered.
- The probe is build-selectable for 16.635.1; required anchors fail closed.
- Relocation: **53 EXACT, 23 LIKELY, 70 MANUAL, 0 AMBIGUOUS, 2 MISSING**
  (2026-11-14 cross-check with RoyaleFish, §3.33). The two remaining rows are
  `kTranslatedSha256InitializerCall` and `kCombatHealEffectReturn`.
- **Cold configure is open**: the content-ready byte is `0x720` (§3.13), and a
  fresh `MatchConfig` reaches `ready/matchReady=true`.
- **Live command injection works**: two-sided card plays, abilities, entity/HP
  observation and the scripted X-Bow policy all run with 0 rejections
  (§3.14–§3.22).
- **Rich telemetry is mixed**: object/phase/ability/HP streams are live, but the
  native hook rings (combat events, phase/special-movement/action-movement,
  character state, visibility, remaining, tower troops) report `unavailable` on
  this build. `observe_rich` still validates because those streams fail closed.
- The V4 evaluator now binds the live 16.635.1 identity explicitly
  (`native_runner/versions/native-16.635.1.json`, §3.26); the local card catalog
  remains the pinned 15.535.86 release, with the level-11 parity caveat.

This is finite reverse-engineering work with existing tools, not a missing model
or dataset. The relocation table is now closed (0 AMBIGUOUS, 2 genuinely
missing — §3.33/§3.34); the remaining N2 items are installing the rich hook
rings from the recovered 16.635 offsets and fixing the render gate. They do not
block N1 evaluation or a CPU baseline.

---

## Session status — N0, N1 and N3 passed

Done on the attached `SM-A515U` (`R58NA0QTHWV`):

1. **Engine bring-up.** Two engines cold-ready for N0/N1
   (`nullsroyale.rel.free:engine0/1`, ports `26789`/`26790`), later four for
   N3 (`26789`–`26792`), identity verified against the 16.635.1 pin.
   Operational: keep `adb shell svc power stayon usb` on. After long idle a
   background engine's control socket can stop answering while the process is
   still alive (seen on `:engine0`); `launch_cluster_a51.sh` force-stops and
   relaunches both engines, which restores it. The launcher now also disables
   `cached_apps_freezer`, which is the likely cause.
2. **Scripted soak.** 10/10 matches, 0 problems, 93 abilities with 0 rejections,
   empty crash buffer (`FINDINGS.md` §3.28).
3. **`observe_rich`.** Validates; object/HP/phase/ability streams live, native
   hook rings `unavailable` as expected (§3.26/§3.27).
4. **N1 baseline set.** General **2–0 over IL**, **0–2 to Hog26-specialist1**,
   both seats, sampled, 0 rejected actions, all normal terminals (§3.27,
   §3.29). General's volume is healthy; the Hog specialist converts earlier.
5. **N3 throughput.** Four engines on big cores in visible windows give
   **12.3 matches/s** at 4×8 lanes and **14.0–14.4** at 4×16, stable over
   eight runs, no crashes. A backgrounded engine is pinned to CPUs 0–1 and
   loses ~3x; `--window-mode visible` fixes it (§3.30).
6. **A51 → PPO wiring.** `tools/native_analysis/a51_engine_node.py` attests the
   cluster and writes a `v4-ppo-engine-node.v1` manifest; `launch_ppo_a51.sh`
   launches the cluster, attests it and starts `remote_ppo_worker_server`. The
   manifest builds a valid `v4-ppo-multinode-topology.v1` topology.
7. **`offline_agent.py` / `offline_duel.py`** now bind the live 16.635.1
   ruleset and honour `combat_ring_hooks: false` (they no longer request the
   combat-ring feed). **They still crash on the native-render path**: the probe
   SIGSEGVs in `cr_render_gate_swap_buffers+996` (§3.31). Use the headless
   evaluator; fixing the render gate is an N2 item.

**Blocker for a local PPO smoke:** the production collector is CUDA-graph-only
(`_persistent_rollout_graph_v4` raises when a graph is unavailable; the trainer
uses NCCL and `torch.cuda.*` unconditionally). There is no CPU path, so the
first real PPO segment/update needs the rented GPU. The A51 node plumbing is
ready for it.

## GPU smoke 2026-11-14 — results and what they mean

Run: ThunderCompute 1× RTX A6000 (48 GB), 6 vCPU, 48 GB RAM, driver 610,
`torch 2.5.1+cu121` in a venv; A51 over a reverse SSH tunnel on `29900`
(`-R 127.0.0.1:29900:127.0.0.1:29900`). Evidence: `runs/gpu-smoke-20261114/`
(`metrics.jsonl`, `config.json`, `ppo-smoke.log`).

| Gate | Result |
| --- | --- |
| Trainer ↔ worker ↔ engine wiring | **pass** — 4 engines, 32 lanes, tunnel transparent |
| `native_rejected_actions` | **0** |
| Checkpoint | `update-00000001.pt`, 145,702,430 B, sha `9566a110…02654` |
| Optimizer / update step | written at a strict boundary (resume contract saved) |
| GPU peak allocation | **6.33 GiB** (not the <2 GB estimated; still trivial for 48 GB) |
| GPU utilisation during collection | **4 %** — the engine feeds it |
| All 32 matches reaching terminal | **no** — 32/32 truncated by the segment budget |
| `eligible_act_rate` | **0.0** — 5,120 eligible ticks, all `WAIT` |
| Wall time for one 40 s segment | 266 s collection + 11 s update |
| Cost | ~$0.23 for the whole session |

### Two configuration bugs, both fixed

1. **Gate temperature.** `DEFAULT_GATE_TEMPERATURE_START = 0.20` is flat and
   sharpens an already-passive gate (General takes `ACT` ≈ 6 % at 5 ticks).
   Result: 0 actions in 5,120 eligible ticks, i.e. no policy signal at all.
   The launcher now honours `CR_PPO_GATE_TEMPERATURE_{START,END,ANNEAL_UPDATES}`;
   collect at `1.0` and anneal to `0.2`.
2. **Deck pool.** The trainer requires **exactly 7,695 decks / 20 focus decks**.
   The local corpus yields 7,089 at `--minimum-uses 5` and 8,530 at 4 — the
   upstream number is unreachable locally. Added `--allow-unverified-deck-pool`
   (still requires ≥5,000 decks + exactly 20 focus decks) and
   `native_runner/training/v4/select_focus_decks.py`, which derives a
   deterministic focus list from observed usage.

Also fixed: `CR_PPO_SEED` defaults to 0, which trips the positivity check — set 1.

### Why the A51 cannot train this policy

The resident channel costs ~150 ms per framed request regardless of payload, and a
PPO decision is only 5 ticks. Measured with `resident_batch_throughput.py`:

| Engine | Config | Capacity | Notes |
| --- | --- | --- | --- |
| 4× A51 | 8 slots, 5 ticks | **0.26 matches/s** | 0.042 / 0.040 / 0.092 / 0.085 per lane; makespan 0.161 |
| 4× A51 | 8 slots, 600 ticks (old §3.30) | 12–14 matches/s | amortises the same fixed cost 120× |
| 1× x86_64 | 8 slots, 5 ticks | **0.61 matches/s** | 296 actions/s |
| 1× x86_64 | 16 slots, 5 ticks | **0.616 matches/s** | 299 actions/s, CPU-bound at ~3,700 slot-ticks/s |

So the §3.30 number was a coarse-tick transport ceiling, not a training rate.
Inside the learner loop the A51 is worse still: **19 lane-decisions/s**, i.e. a
21× gap on top of the 0.26 ceiling. Per-iteration accounting for one packet:
~416 ms wall, of which policy forward is 27 ms and assembly 4 ms; the rest is the
closed loop (engine round + worker decode + action application). The receive
executor already runs one thread per engine, so parallel reception is not the
problem; the loop still resolves **one packet per forward**
(`inference_mean_frame_packets: 1.0`, batch ≈16 rows, `_INFERENCE_BATCH_WAIT_SECONDS = 0.002`).

### What to do next (CPU-only first — no GPU spend)

**Done 2026-11-14 (session 3):** the CPU profiling harness exists and the real
loop runs without a GPU. See "Learner-loop profiling" below for numbers and for
the three new blockers it exposed.

**Done 2026-11-14 (session 4):** all three blockers are closed (`FINDINGS.md`
§3.37). The x86 engine is now a *training* substrate, not just a control/eval
oracle: `profile_real_collection.py --checkpoint B-general-v275.pt` collects a
48-decision, 8-lane segment through the CPU PPO worker with 0 UNKNOWN effects,
0 fallbacks, and the 16.635 catalogs/21-column schema in place.

1. **Profile the learner loop without a GPU — done.** Transport floor and the
   real loop are both measured; remaining cost is the CPU forward.
2. **Make the x86 engine the training substrate — done.** `B-general-v275`
   collects on one local engine. The official x86_64 build is content 16.635.8,
   so it trains **16.635-contract** checkpoints (B-general/A-hog26); a
   15.535-contract checkpoint still needs the A51 or an adaptation step.
3. **Learner-loop batching — done (`FINDINGS.md` §3.38).** The window must be
   about one engine round-trip, not 20–40 ms. With four A51 engines and 8 lanes,
   `CR_PPO_INFERENCE_BATCH_WAIT_MS=250` gives **3.88 packets/forward and 33
   forwards instead of 64**; 2/30/100 ms stay at 2.0, and 150 ms only reaches
   2.56. CPU wall time is flat (model-bound), so the win is per-forward fixed
   cost — the thing the GPU paid while idle. Use 250 ms for the GPU smoke and
   re-measure if engine count or lanes change. Multi-engine x86 now works:
   `crx_engine_host.py --engine-index N` → port `26789+N`, CPU N.
4. **Delay-aware candidate legality — done (`FINDINGS.md` §3.39).** The
   decoder, environment action mask and compound validator now check elixir at
   the touch delay bin with the match multiplier, matching the 16.635 engine.
   Gate temperature 1.0 is clean: 5/5 single-engine seed segments, the 4-engine
   shape at 28.5 lane-decisions/s, 3,979 native command parses and 0 failures.
   One failure seen immediately after the port did not reproduce in six later
   segments; `CR_PPO_FAILURE_DUMP` now records the actions if it returns, and
   the probe's `CRNativeProbe` parse log separates injection from capture.
5. **Re-measure before renting.** Rent only when a local segment completes in
   <10 min; the GPU is idle 96 % of the time at the current feed rate.

### Learner-loop profiling — 2026-11-14 (session 3)

Two tools, both CPU-only and both committed:

| Tool | What it measures |
| --- | --- |
| `tools/native_analysis/profile_cluster_collection.py` | transport floor — speaks the worker protocol, answers every frame with a scripted all-WAIT batch; no model, no storage |
| `tools/native_analysis/profile_real_collection.py` | the real `collect_async_cluster_wave_v4` loop on CPU (model + tensorizers + rollout storage) |

`async_cluster_self_play.py` now falls back to `_EagerRolloutSamplerV4` on a
non-CUDA device instead of requiring a CUDA graph, so the collector runs locally.
Covered by `native_runner/tests/test_training_v4_cpu_sampler.py`.

| Measurement | Packets/s | Per packet | Notes |
| --- | --- | --- | --- |
| Scripted, 1× A51, 8 lanes | **4.25** | 235 ms | transport + worker only |
| Scripted, 4× A51, 8 lanes | **10.6** | 94 ms | 2.66/engine, not 4 — already serialising |
| Scripted, 4× A51 + 30 ms model | **9.75** | 103 ms | model cost is *not* the problem |
| Scripted, 1× x86_64, 8 lanes | **13.3** | 75 ms | 3× the A51 per decision |
| Real loop, 1× A51, CPU model | **1.68** | 596 ms | model 256 ms/packet on CPU |
| Real loop, 4× A51, CPU model | **1.54** | 649 ms | trainer-bound: `ready_wait` is only 22 % |
| **Real loop, 4× A51, A6000 (the paid run)** | **2.4** | **416 ms** | `ready_wait` 91 %, GPU 4 % |

Subtracting model time, the non-model cost is **~340 ms/packet on CPU and ~390 ms
on the GPU box** — the same number, so the CPU harness reproduces the paid run's
bottleneck. A scripted reply costs 235 ms/packet, so ~100–150 ms is loop
bookkeeping on top of the engine.

**Diagnosis:** the loop keeps exactly one packet in flight per engine and
replies serially, so per-engine latency is paid end to end. `ready_wait` grows
with engine count and `_INFERENCE_BATCH_WAIT_SECONDS = 0.002` never lets ready
packets coalesce at engine speed. With four engines the trainer is the serial
resource; the GPU is idle while it waits.

### Three new blockers found while profiling

1. **The x86 worker path is broken — FIXED 2026-11-14 session 4
   (`FINDINGS.md` §3.37).** `profile_cluster_collection.py` against the local
   x86 engine failed on the first batch with `Error -5 while decompressing data:
   incomplete or truncated stream`. It was not the engine: crx resolves probe
   imports through `dlsym(RTLD_DEFAULT)`, and `crx_engine_host.py` had preloaded
   `libz.so.1` **`RTLD_LOCAL`**. crx's missing-symbol stub then returned
   `0 == Z_OK` for `compress2`, so every "compressed" batch entry shipped as a
   zero-byte zlib payload. Fixed by preloading zlib `RTLD_GLOBAL`; the host now
   also refuses to attach the probe if `compress2`/`compressBound` are
   unresolved. Probe hashes are unchanged.
2. **The worker was hard-wired to the 16-column schema — FIXED.** `build_episode_tensorizers_v4` was called without a model config, so a 16.635
   checkpoint failed with `mat1 and mat2 shapes cannot be multiplied (128x16 and
   21x128)`. `AsyncResidentEngineSpecV4` now carries `competitive_release` and
   `model_config`, derived in `collect_async_cluster_wave_v4` from the actor
   model so a remote worker cannot disagree with it. Mixed anchor/history
   releases are rejected at startup.
3. **The 15.535 catalogs cannot tensorize some 16.635 entities — FIXED for
   16.635-contract checkpoints.** `active public effect resolved to UNKNOWN:
   buff:…` on the 16.635.8 x86 engine. The worker now binds the checkpoint's
   release (`use_release`) around engine construction and reset, because
   `BattleEnvV1` re-verifies the semantic subset against the *active* release's
   form evidence. `B-general-v275` now collects with 0 UNKNOWN effects. A
   15.535-contract checkpoint (General) still fails on 16.635 effect ids — that
   is correct and means the x86 substrate needs a 16.635 checkpoint.

---

## Next session — start here

> **Historical (2026-11-14).** Steps 1–5 in this section are done; the live
> next-session plan is the "Session 8" section at the top of this file. Kept for
> the runbooks and reference numbers.

### 0. Already done at this handoff (no compute spent)

| Step | Result |
| --- | --- |
| **RoyaleFish cross-check (§3.33)** | **done** — 145 named x86→ARM rows compared; **0 AMBIGUOUS** left (was 33), 8 of 10 MISSING recovered; 7 core corrections, 56 adopted overrides |
| **Corrected core table on device (§3.34)** | **done** — probe `db9d5609…`, APK `9e345ad3…`; 9/9 hooks install, scripted seeds 1–2 identical to §3.32, 0 rejected, 0 crashes |
| **ARM observation parity (§3.36)** | **done** — probe `b80f2bdb…`, APK `17893d1c…`; 16.635 rich vtables emitted for ARM; General-vs-IL evaluator now **3601 / 4794** on both seats, identical to x86; `runs/parity-a51` re-captured and re-pinned |
| Hog26-2 result | **0–2**, both overtime (6120/6118), 0 rejected — in `FINDINGS.md` §3.29 |
| **A51 parity corpus (1.0)** | **done** — `runs/parity-a51/` + `manifest.json`; seeds 1–5 + seed-1 repeat, General 2–0 IL (`--sample`), 1×8 capacity; deterministic, 0 rejected — `FINDINGS.md` §3.32 |
| Deck pool | `runs/deck-pool.json` — **7,089 decks**, 443,381 player uses from 252,238 replays |
| A51 cluster | 4 engines launched and attested on `26789`–`26792` this session; **stopped at handoff** (idle phone, launch on demand) |
| Node manifest | `runs/a51-node0/engine-node-ready.json`, probe `b80f2bdb…`, `parallel_matches: 32`, digest `ce9d4f85…`; topologies rebuilt |
| Worker service | `remote_ppo_worker_server` on `127.0.0.1:29900` |
| Topology (1 GPU / 6 vCPU) | builds and validates: `runs/topology-a6000-1gpu.json` |
| Topology (2 GPU / 6 vCPU) | builds and validates: `runs/topology-a6000-2gpu.json` |

All of it is CPU/phone work. Build the deck pool and IL cache **before** the
meter starts.

### 1. x86_64 16.635.1 — **done** (see §3.35 and §3.36)

The engine runs on this host through the crx ELF loader; the probe builds with
`--abi x86_64`; scripted X-Bow seeds 1–5 **and** the General-vs-IL evaluator
reproduce `runs/parity-a51` decision-for-decision. Reproduce with:

```bash
.venv/bin/python tools/native_analysis/crx_engine_host.py --seconds 1800 &
.venv/bin/python tools/native_analysis/scripted_xbow_match.py --seed 1 \
    --output runs/parity-x86/x86-seed1.json
CR_PROBE=native_runner/probe/out/x86_64/libcrprobe.so CR_NATIVE_BUILD_ABI=x86_64 \
  .venv/bin/python tools/native_analysis/parity_manifest.py \
    --corpus runs/parity-x86 --compare runs/parity-a51/manifest.json \
    --output runs/parity-x86/manifest.json
# evaluator gate: must print the same 3601 / 4794 rows as runs/parity-a51
CR_PROBE=native_runner/probe/out/x86_64/libcrprobe.so CR_NATIVE_BUILD_ABI=x86_64 \
  .venv/bin/python -m native_runner.training.v4.evaluate \
    --checkpoint-a checkpoints/General/checkpoint-step-00000460.pt \
    --checkpoint-b checkpoints/IL/checkpoint-step-00029396.pt \
    --games 2 --sample --seed 7 --port 26789 \
    --output runs/parity-x86/x86-model-general-vs-il.json
```

**RESOLVED — evaluator parity (§3.36).** The root cause was the one-sided
vtable fix described in §3.35: on ARM the rich-classification constants were
still gated to x86_64, so the probe fell back to the 15.535 literals
(`entities[].entity_kind`, `projectile_state`, `runtime_provenance`). The ARM
16.635 header now emits the nine `offset-diff.json` vtable anchors, the probe
selects them on both ABIs, and the A51 runs probe `b80f2bdb…` / cluster APK
`17893d1c…`.

General-vs-IL, seed 7, both seats, `--sample`: game 1 **tick 3601, actions
25/27**, game 2 **tick 4794, actions 40/37** — identical to x86_64. Scripted
seeds 1–5 are unchanged; `runs/parity-a51` was re-captured and the ARM pin now
records `b80f2bdb…`; `out/libcrprobe.so` was refreshed to match. Do not
resurrect option B (mirroring the stale table on x86).

1. **1.0 — Parity corpus from the A51 — DONE (`FINDINGS.md` §3.32).**
   `runs/parity-a51/` holds seeds 1–5, a seed-1 repeat (decision-identical
   after dropping latency), two `evaluate.py --sample` games (General 2–0 IL)
   and one `resident_batch_throughput.py` run (2.07 matches/s, 1×8).
   `runs/parity-a51/manifest.json` archives the probe/checkpoint/APK hashes and
   the determinism check. The phone may now idle; re-capture only if the probe
   or client changes. The x86 port must reproduce the corpus:

   ```bash
   .venv/bin/python tools/native_analysis/parity_manifest.py \
       --corpus <x86-corpus> --compare runs/parity-a51/manifest.json \
       --output <x86-corpus>/manifest.json   # exit 0 == gate passed
   ```
2. **1.1 — 16.635 catalogs adopted; `B-general-v275` loads and plays — DONE.**
   Do **not** apply `RoyaleFish/patches/native_runner_16635.diff`: it is based on
   a different 15.535 tree and gets 16.635 data through by disabling SHA checks.
   Instead, the 16.635.1 catalogs are now a first-class, verified release:

   - descriptor `native_runner/releases/16.635.1.json`, data
     `native_runner/data/competitive-16.635/` (1.5 MB);
   - `native_runner/competitive_release.py` selects releases with a
     `contextvars` context, so 15.535 and 16.635 bundles coexist in one process;
   - the compiled catalogs were adopted from `RoyaleFish/data_16635`
     (Apache-2.0) and verified against `B-general-v275`'s contract. Three repair
     steps made the bundle self-consistent without changing catalog semantics:
     stale `model_catalogs.input_catalog_ids`, stale 15.535 hashes inside
     `form_evidence`, and an incorrect manifest. Full detail:
     `docs/native/COMPETITIVE_RELEASES.md`.
   - all five built catalog ids equal the published checkpoint contract
     (`card 8c0baac9…`, `ability 128b6679…`, `entity_archetype 140e32c1…`,
     `effect a5bf4db4…`, `mechanic_profile 00b73f61…`).

   `load_policy_v4()` now reads the checkpoint contract: it resolves the release
   by catalog ids, builds `ModelConfigV4` from `contract.model_config`, and loads
   strictly. `B-general-v275` and `A-hog26-v738` load as **17,506,419 params,
   LSTM 768, spatial 128/5, runtime schema 21**; General/IL still load as
   12,684,339 params on the 16-column schema.

   **Play smoke passed (A51, seed 11, one game):** B-general vs General, both
   loaded in one process, `native_final_result` at tick 6089, 56/55 actions,
   **0 rejected**, empty crash buffer. General won this single game — that is a
   plumbing gate, not a strength result. `contract_compatible()` tolerates only the declared
   additive config keys, so an older checkpoint still loads and a real dimension
   change still fails.

   The two schema additions this required:
   - `CARD_RUNTIME_FEATURE_NAMES_V2` (16 + 5 pending-execution columns) and the
     tensorizer block that fills them from `observation.pending_actions`;
   - the additive `ModelConfigV4` fields (`neutralize_pending_ticks_remaining`,
     `delay_aware_legality`, `card_id_gate`, `ablate_*`).

   **Caveat (updated 2026-11-14 session 6):** `delay_aware_legality` is now
   branched on in the decoder, the environment action mask and compound
   validation (§3.39). Only the ablation/gate switches are still carried for
   exact round-tripping. B-general collects at full ACT rate with 0 rejected
   actions; its *strength* is still unvalidated until a paired gate runs.

   Note for future catalog work: recompiling from the local `Clashback` assets
   does **not** reproduce these ids — those assets are a different 16.635
   content revision (all 258 source-file hashes differ, and only 25 abilities
   bind). The compiled 16.635 set is the version-matched source for now.
3. **1.2 — x86 probe — DONE.** `native_runner/probe/out/x86_64/libcrprobe.so`
   builds with `native_runner/probe/build_probe.sh --target-build 16.635.1
   --abi x86_64`. Added the x86 call path, `x86_hook.h`, the logical
   offset/function/vtable tables, and `build_16_635_1_layout_x86.h`. The rich
   rings stay ARM-only exactly as on the A51; the JSON path needed its own key
   ABI (see §3.35 — the mirror of §3.34's trap).
4. **1.3 — host — DONE, without the bionic bundle.** `.resource/Clashback/vendor/crx`
   maps the x86_64 engine into a plain Linux process with glibc shims. The probe
   attaches with `cr_probe_attach(libg_base)`; `tools/native_analysis/crx_engine_host.py`
   boots the engine, installs the presentation guards, attaches the probe and
   serves the usual `cr-native-control.v1` port. The pinned 15.535.29 bionic
   runtime is no longer needed for 16.635.1.
5. **1.4 — GATE: PASSED.** Scripted X-Bow seeds 1–5 reproduce the A51 corpus
   decision-for-decision (`first_difference: null`), 0 rejected actions, normal
   terminals, and the seed-1 repeat is deterministic. After the A51 vtable fix
   (§3.36) the General-vs-IL evaluator also matches: both seats terminate at
   the same ticks/actions as x86_64 (3601 and 4794), with 0 rejected actions.
   `attest` is `production_ready` against
   `native_runner/versions/native-16.635.1-x86_64.json`. The x86 engine is now a
   **ruleset, scripted and evaluator** oracle.

The phone is no longer the training engine. The GPU line below is about
evaluation volume and PPO updates, not about rescuing a 4-engine farm. The phone
stays plugged in as the ARM64 oracle (`--engines 1` on demand).

### 2. Then buy this: 1× RTX A6000, `--gpus 1` — **done; box deleted, re-rent only when the engine feed justifies it**

| Option | $/h | Hours for $20 | Verdict |
| --- | --- | --- | --- |
| **RTX A6000 ×1** | **0.35** | **~57** | **Used for the first smoke; 6.33 GiB peak, 4 % utilisation** |
| RTX A6000 ×2 | 0.35 claimed | ~57 | Only if the 0.00 second GPU survives checkout — and still run `--gpus 1` |
| L40 ×1 / ×2 | 0.79 / 1.58 | 25 / 12.7 | 2.3× price for FLOPs this model cannot use |
| A100 80 GB ×1 / ×2 | 1.09 / 2.18 | 18 / 9 | 3.1× price; 80 GB VRAM and 24 vCPU are both irrelevant |

**Why the A6000 is not a compromise.** The model is 12.7M params today (17.5M
with the 16.635 config): ~70 MB fp32 weights, ~210 MB with Adam moments, and
activations for `--lane-minibatch-size 384` × `--sequence-chunk-steps 16` are
small — the update fits well under 2 GB, so 48 GB is 20–40× headroom. The load
is set by the engine, not the GPU:

| Engine | Actor rate | Batched-forward budget |
| --- | --- | --- |
| 4 A51 engines × 8 lanes | ~15.6k decisions/s | ~2 ms @ 32 lanes |
| x86_64, 16 matches/process × 8 | ~62k decisions/s | ~2 ms @ 128 lanes |

Either way Ampere clears it with the CUDA-graph replay path in
`async_cluster_self_play.py`; a bigger card buys latency margin the engine
cannot consume. `torch==2.5.1` + cu121 covers sm_86 (A6000) and sm_89 (L40).

**Do not start on two ranks.** `--gpus N` becomes
`torch.distributed.run --nproc-per-node=N` and splits the engines across ranks
(`rank_engine_indices [[0,2],[1,3]]` for the A51). That adds NCCL init and DDP
averaging to the first smoke for throughput the engine cannot produce. Split
ranks later, when the x86 farm is large enough that one GPU is genuinely
saturated — that is an evaluation/update problem, not a training-start problem.

**The $0.00 second A6000 is not a plan.** It is almost certainly the per-unit
price echoed onto the second slot. Read the provider's hourly estimate before
starting. If a 2-GPU box genuinely bills $0.35/h, take it and still pass
`--gpus 1` — a free spare GPU helps `serve_policy`/evaluation, not training.

**Skip the A100 host.** In remote-worker mode the tensorizers run on the
engine-node host; the GPU box only runs the rank, and `_bind_rank_cpus` needs
one worker CPU per engine plus ≥1 control CPU. With 4 engines,
`--worker-cpus 0,1,2,3 --control-cpus 4` validates on 6 vCPU. 24 vCPU/192 GB
buys nothing. If the x86 engine runs *on the GPU box*, CPU:GPU ratio flips and
16 vCPU is the useful upgrade — check that after step 1, not before.

### 3. Pre-flight on the GPU box, before a long run

- torch **2.5.1 + cu121** wheel, not the local `+cpu` build.
- The A51 is behind NAT and the trainer needs **one** inbound port (`29900`).
  Reverse-tunnel it from this laptop instead of exposing anything:
  `ssh -N -R 29900:127.0.0.1:29900 <gpu-box>`
  The manifest already records `remote_worker_host: 127.0.0.1`, so the tunnel is
  transparent — no manifest edit and no `--worker-host 0.0.0.0`.
- `--trainer-host` must equal `hostname -s` **on the GPU box**; the local `.env`
  has empty `CR_PPO_*`, so export `CR_PPO_DECK_POOL`, `CR_PPO_CHECKPOINT`,
  `CR_PPO_TOPOLOGY`, `CR_PPO_OUTPUT_DIR`, `CR_AI_PYTHON` there (environment
  wins over `.env`).
- ~20 GB disk (repo + 1.8 GB dataset + checkpoints).
- Confirm preemption policy; if spot, `--snapshot-every 1`.
- Keep the phone awake: `adb shell svc power stayon usb`.

Budget plan: 3–4 h (~$1.4) for the smoke + a few hundred paired eval games,
then stop and decide. Evaluation volume, not engine hours, is what $20 buys:
at ~2 s/game on an A6000 a 400-game paired gate is minutes, versus ~5 min/game
on this host's CPU.

### 4. N3 — done; reproduce or extend

```bash
./launch_cluster_a51.sh --engines 4          # visible windows, big cores 4-7
.venv/bin/python tools/native_analysis/resident_batch_throughput.py \
    --ports 26789,26790,26791,26792 --slots 16 --batches 10 --ticks 600 --actions \
    --output /tmp/opencode/resident_4x16.json
adb -s R58NA0QTHWV logcat -b crash -d
```

Expect ~14 matches/s capacity and a ~13.9 makespan floor. Record any change in
`FINDINGS.md` §3.30. The next lever is GPU inference, not more engines.

### 5. The GPU smoke — **done 2026-11-14**, see the GPU-smoke section above

The procedure that actually works (ThunderCompute box, A51 over a tunnel):

```bash
# on this host
./launch_cluster_a51.sh --engines 4
./launch_ppo_a51.sh --skip-launch --engines 4 --worker-host 127.0.0.1 --output-dir runs/a51-node0
setsid ssh -N -o ExitOnForwardFailure=yes \
    -R 127.0.0.1:29900:127.0.0.1:29900 <box>      # worker service
setsid ssh -N -R 127.0.0.1:26789:127.0.0.1:26789 <box>   # evaluator only

# on the box
.venv/bin/python -m native_runner.training.v4.build_ppo_multinode_topology \
    --node-ready runs/a51-node0/engine-node-ready.json --output runs/topology.json \
    --trainer-job-id 1 --trainer-host "$(hostname -s)" --gpus 1 \
    --worker-cpus 0,1,2,3 --control-cpus 4
export SLURM_JOB_ID=1 CR_AI_PYTHON=$PWD/.venv/bin/python CR_PPO_SEED=1
export CR_PPO_ALLOW_UNVERIFIED_DECK_POOL=1
export CR_PPO_GATE_TEMPERATURE_START=1.0 CR_PPO_GATE_TEMPERATURE_END=0.2 \
       CR_PPO_GATE_TEMPERATURE_ANNEAL_UPDATES=20
export CR_PPO_INFERENCE_BATCH_WAIT_MS=250   # ~one A51 engine step (§3.38)
bash native_runner/training/v4/launch_ppo_multinode_training.sh \
    runs/ppo-smoke runs/topology.json 1 40      # 1 update, 40 s segment
```

Non-obvious requirements: `SLURM_JOB_ID` must equal the topology's
`trainer_slurm_job_id` (there is no Slurm; just export it), the seed must be >0,
and the pool must be the 20-focus variant
(`select_focus_decks.py --deck-pool runs/deck-pool.json --top 20 ...`).

The 40 s segment is a **decision budget** (`segment_decision_steps = seconds*4`
per lane), not wall time: 40 s = 160 decisions/lane ≈ 5,120 frames.

Gate the run on: all 32 resident matches completing (needs ≥180 s at 720
decisions/match), **0 rejected actions**, `native_final_result` terminals, and a
saved checkpoint with a matching SHA/optimizer step. Then run 2–3 evaluator games
at `--device cuda` against IL and Hog26-2 before deciding on the fine-tune
length. Do not train on 15.535 labels.

### 6. N2 — the remaining port work

Resolve the `AMBIGUOUS`/`MISSING` anchors and install the rich hook rings
(combat events, movement/character/visibility/remaining, tower troops). Follow
§5 of `FINDINGS.md`; validate each anchor with `cr_elf.py` / `diff_builds.py`.

**Free head start — DONE (§3.33/§3.34).** The RoyaleFish table closed every
AMBIGUOUS anchor and 8 of 10 MISSING ones. The rich-subsystem offsets are now in
`offset-diff.json`; installing the rings is mechanical: emit a per-subsystem
16.635 layout header (combat, phase, special/action movement, character state,
remaining, visibility, tower troops), drop that subsystem's
`#if !defined(CR_PROBE_TARGET_16_635_1)` guard, and re-attest one subsystem at a
time. Validate each with a prologue match plus a live `observe_rich` sample. The
ARM inline hook copies 16 bytes verbatim, so a hook whose first 16 bytes contain
a branch target outside the steal window needs a relocated trampoline (most
ring anchors are calls/returns, not branch entries, so this is a small set).

**New, concrete N2 blocker:** the native-render gate SIGSEGVs on 16.635.1
(`cr_render_gate_swap_buffers` → replay JSON setter, §3.31). Fix the
replay-manager `+0x20` controller slot / setter `+0x90` manager read, then
re-attest `render_gate` with a rendered-match smoke. Until then,
`offline_agent.py` / `offline_duel.py` are headless-only in practice. This is
cosmetic for training: deprioritize it behind step 1.


---

## General baseline

`checkpoints/General/checkpoint-step-00000460.pt`

| Field | Value |
| --- | --- |
| Parameters | 12,684,339 |
| Contract | `universal-card-policy.v4` |
| Observation | `universal-semantic-observation.v4` |
| Action | `universal-candidate-action.v4`, 2 micro-actions, 50 ms offsets |
| Decision cadence | 5 ticks |
| Training stage | `ppo-league-snapshot`, step 460 |
| SHA-256 | `2fb7a94fa5df0f12bd72433874487bc7d84a4efb2a6dac827d7adff0a5eef083` |

Other checkpoints are opponents/baselines:

- `checkpoints/IL/checkpoint-step-00029396.pt`
- `checkpoints/active IL/checkpoint-step-00000030.pt`
- `checkpoints/2_6hog_expert/hog26-specialist1.pt`
- `checkpoints/2_6hog_expert/hog26-specialist2.pt`

**Published 16.635 baselines** (loadable now through the 16.635.1 release):

| Model | Deck | Params | Notes |
| --- | --- | --- | --- |
| `.resource/RoyaleFish/models/B-general-v275.pt` | many | 17,506,419 | 275 updates on 16.635 data; best public generalist; **loads** (step 1.1) |
| `.resource/RoyaleFish/models/A-hog26-v738.pt` | Hog 2.6 | 17,506,419 | **71.9% ±2.6 vs its start over 1,147 paired games**; same release/schema as B-general |
| `.resource/FirstLight-Zero/checkpoints/firstlight-zero-v1450.pt` | X-Bow 3.0 | ~1.17M | self-play only, **15.535.13**, mirror-only result |

The General model was trained on 252k official-game replays; Null's may have
custom balance/cards. Verify content compatibility before assuming transfer.

---

## Data — no scraping needed to start

`datasets/royaleapi/` is the full **IL_Replay** dataset:

- 252,238 replays, 17,836,160 actions, 1.8 GB
- `replays/part-*.parquet`, `actions/part-*.parquet`, `manifest.json`
- Format: `royaleapi-battle-replay.v1`

Sources and limits:

| Source | What it gives | Use |
| --- | --- | --- |
| **Local IL_Replay** | 252k replays / 17.8M actions | generalist pretraining/IL, already here |
| **Official Clash Royale API** | battle logs only (last ~25), no replays | meta/opponent stats, not IL |
| **RoyaleAPI downloader** (`.resource/royaleapi-downloader`) | live 16.x ladder replays, structured JSON | fresh official-game data; follow site terms |
| **Hugging Face** | IL_Replay (local), optional replay/checkpoint datasets | extras only |
| **Null's self-play/captures** | the actual target distribution | the data that matters for the ladder |

Do not mix 15.535.29 self-play labels with 16.x IL labels without an explicit
adaptation step. Do not call self-play actions “expert” for IL.

Deck diversity: use `native_runner/training/v4/build_ppo_deck_pool.py` on
`datasets/` (exact forms + tower troops), then the V4 league/opponent scheduler.
No scraping is required for the first fine-tune.

---

## Training pipeline

The local V4 stack:

| Step | Command |
| --- | --- |
| Build IL cache | `python -m native_runner.training.v4.cache_builder datasets/royaleapi datasets/cache --source-count 0 --ports 26789 --resident-slots 1 --verify-roundtrip` |
| IL smoke | `bash native_runner/training/v4/launch_stateful_il.sh --index … --train-tags … --output-dir runs/il-smoke --epochs 1 --max-groups 1 --save-every-groups 1` |
| Deck pool | `python -m native_runner.training.v4.build_ppo_deck_pool --dataset-root ./datasets --output ./runs/deck-pool.json --minimum-uses 5` |
| Policy service | `python -m native_runner.training.v4.serve_policy --checkpoint checkpoints/General/checkpoint-step-00000460.pt` |

PPO caveat: the upstream launchers assume AVD/engine-ready topology. The A51
cluster is not wired into that topology yet; adding an A51 engine-ready path is
the first N3 task. Until then, use the evaluator and a single-engine PPO smoke.

Training rules:

- Initialize from General; do not freeze it as a mirror opponent.
- Keep rejected actions at zero; reject the rollout otherwise.
- Evaluate paired seeds, both seats, fixed decks/levels.
- Record checkpoint SHA-256, dataset/tag selection, deck pool, seed and config.

---

## Cloud and credits

**Decision: 1× RTX A6000 @ $0.35/h, `--gpus 1`, ~57 h of runway for $20.**
The GPU is not the bottleneck; the engine is.

- **RTX A6000 48 GB — $0.35/hr.** 12.7M (or 17.5M on the 16.635 config) params
  fit in <2 GB; 48 GB is headroom, not a reason to buy more compute. Cheapest
  per useful hour and the only GPU this plan needs.
- **L40 $0.79/hr and A100 80 GB $1.09/hr are 2–3× the price for FLOPs and VRAM
  the engine cannot feed** (~15.6k decisions/s on 4 A51 engines, ~2 ms per
  batched forward). They only make sense if the x86 farm grows by an order of
  magnitude, which is a later decision.
- **A "$0.00" second GPU must be verified at checkout** before it influences
  any plan; until then assume the second unit double-bills.
- **GCP $300 / Azure $100.** Useful only if you need a high-vCPU host with a GPU
  or a cloud Android/AVD cluster. New accounts often have **zero GPU quota**;
  check before planning. Free-trial terms apply per account.
- **One inbound port is all the networking you need.** Reverse-SSH `29900` from
  this laptop; keep the worker bound to `127.0.0.1`.
- **Spend order:** x86 16.635 engine, catalogs, deck pool, IL cache first
  (CPU, free), then GPU hours for evaluation volume and PPO updates. Never bill
  GPU hours to CPU-bound prep.

Scale options, in order of leverage:

1. **x86_64 16.635.1 native engine on this host** — 16 matches/process, no
   phone, same ruleset. The correct replacement for phone-bound training.
2. **crforge 16.635.3/8** — 127k steps/s/thread, tick-exact to recorded
   battles, `VectorBattle` Python env. Highest raw throughput; needs a V4→crforge
   observation adapter, and it is a reimplementation.
3. **More A51-class ARM64 devices** — cheapest per *deployable* engine, but
   hardware-bound and now the wrong axis for training.
4. **Cloud x86 host with KVM + ARM64 Android image/AVD cluster** — only if the
   deployment target itself must be scaled.

---

## Risks

1. **16.x port completeness** — the rich hook rings and several core anchors
   remain; N2 may take longer than N0/N1.
2. **Engine throughput** — no longer the limiter: one A51 gives ~12–14
   matches/s (§3.30). The remaining risk is a regression into the `background`
   cpuset; the launcher now verifies it. GPU policy inference is the cap.
3. **Host inference, not the phone** — one CPU core spends ~217 ms per policy
   forward; a full evaluation game is ~5 min. Continuous training needs a GPU.
4. **Argmax is passive** — both checkpoints give `ACT` only ~6% probability at
   the 5-tick cadence, so deterministic decoding stands still. Use `--sample`
   for evaluation and check gate entropy before live deployment.
5. **Catalog adaptation** — the device runs content 16.635.93 while the local
   default catalogs are pinned at 15.535.86. Deck ids/forms/costs match at
   level 11; Electro Spirit HP differs (215 vs 217). **Resolved for loading:**
   16.635 is a selectable, verified release (`native_runner/releases/16.635.1.json`)
   adopted from `RoyaleFish/data_16635` with a repaired manifest, and
   `B-general-v275` loads against it. The local `Clashback` 16.635 `csv_logic`
   assets are a *different content revision* and do not reproduce those catalog
   ids, so do not assume a recompile equals the published models. See
   `docs/native/COMPETITIVE_RELEASES.md`.
6. **Null's content** — official-game 16.x data may not match Null's custom
   balance/cards. Verify the fingerprint. Note FirstLight-Zero and RoyaleFish
   both train on official 16.635 tables, so Null's custom balance remains the
   one unmeasured domain gap.
7. **Ladder rules** — automated play may be prohibited; confirm before using a
   live account.
8. **Cloud quotas** — GCP/Azure GPU quota may be zero on new accounts.
9. **Native-render gate crash** — 16.635.1 SIGSEGVs in
   `cr_render_gate_swap_buffers` (§3.31). Headless evaluation/training is
   unaffected; the offline renderer and any live-render demo are blocked.
10. **GPU pricing is not a quote** — the "0.00" second GPU is almost certainly
    a display artifact. Verify the hourly estimate before starting a run, and
    treat preemption as real: `--snapshot-every 1` on spot.
11. **NAT between the GPU host and the A51** — the trainer needs inbound
    `29900`. The reverse SSH tunnel from this laptop is the supported path; a
    dropped tunnel looks like an engine timeout mid-segment. `tcp_endpoint_proxy.py`
    is the fallback, not a public bind.
12. **Simulator self-evaluation** — Hasty-CR's own warning: 93% against its own
    engine, and one human match found a bug 30M training steps had missed.
    In-engine win rates are not evidence. Paired gates must run on the native
    16.635.1 engine (A51 or x86), never only on crforge.
13. **Model-quality gap, not an engine gap** — the best public Ranked result is
    ~43% (ClashAI). Nothing in the ecosystem is above water vs top humans yet.
    Budget the work as a league/opponent/eval problem, and expect the first
    honest target to be "beats General and the Hog specialists reliably", not
    "beats top ladder".
14. **Candidate legality gap — closed.** `delay_aware_legality` is now branched
    on in the decoder, the environment action mask and the compound validator
    (§3.39), and gate temperature 1.0 collects cleanly. The residual risk is
    diagnostic, not semantic: one post-port `env 6` failure did not reproduce,
    and the resident batch error string still collapses injection, step-bound
    and capture failures. Capture it with `CR_PPO_FAILURE_DUMP` and
    `adb logcat -s CRNativeProbe` if it returns.

---

## Retired / deferred

- **R0 elite-v3** — retired. Archived in
  `docs/native/R0_CANONICAL_TREE.md`,
  `tools/native_bringup/smoke_r0_model.py`,
  `tools/native_bringup/verify_r0_batched.py`. Do not start an R0 run.
- **R0 checkpoint/data/CUDA/adapter gates** — retired with R0.
- **cr-native 15.535.29 as critical path** — demoted to proxy lab.
- **Custom ARM64 `libg` loader** — retired; 16.635.1 uses the offline APK/probe.
- **“16.635.1 is parity only”** — reversed; it is the primary target.
- **Linux AVD/MuMu/phone clusters** — only a scale option if the A51 is
  insufficient.

---

## Key paths

| Path | Purpose |
| --- | --- |
| `ROADMAP.md` | The single plan |
| `docs/native/16.635.1/FINDINGS.md` | 16.635.1 probe port evidence log (§3.35 = x86_64 host) |
| `tools/native_analysis/crx_engine_host.py` | Boot the x86_64 16.635.1 engine on Linux (crx) and attach the probe; `--engine-index N` for multiple host engines |
| `tools/native_analysis/profile_real_collection.py` | Real V4 collector on CPU; use it to measure batching/throughput without a GPU |
| `native_runner/probe/build_16_635_1_layout_x86.h` | Generated x86_64 core anchors |
| `native_runner/versions/native-16.635.1-x86_64.json` | x86_64 build pin (select with `CR_NATIVE_BUILD_ABI=x86_64`) |
| `native_runner/probe/out/x86_64/libcrprobe.so` | x86_64 probe (build artifact, pinned by hash) |
| `ROADMAP_CRFORGE.md` | 16.635.1 Java simulator: CI oracle + optional bulk backend |
| `docs/native/16.635.1/*.json` | Relocation, field offsets, vtables, overrides |
| `tools/native_analysis/` | A51 parity/probe tools |
| `tools/native_analysis/a51_engine_node.py` | Attest the A51 cluster → `v4-ppo-engine-node.v1` manifest |
| `launch_ppo_a51.sh` | Cluster + manifest + remote PPO worker service |
| `tools/native_analysis/resident_batch_throughput.py` | Resident-channel capacity measurement |
| `tools/native_analysis/scripted_xbow_match.py` | Scripted X-Bow match on the probe |
| `tools/native_analysis/parity_manifest.py` | Archive/verify/compare an A51↔x86 parity corpus |
| `runs/parity-a51/` | A51 ground-truth corpus + `manifest.json`, re-captured on `b80f2bdb…` (§3.32, §3.36) |
| `runs/parity-x86/` | x86_64 candidate corpus; reproduces every A51 entry (§3.36) |
| `docs/native/COMPETITIVE_RELEASES.md` | Selectable competitive data releases, 16.635 adoption |
| `native_runner/releases/16.635.1.json` | 16.635.1 release descriptor |
| `native_runner/data/competitive-16.635/` | Verified 16.635 catalogs (1.5 MB) |
| `tools/native_analysis/adopt_compiled_release.py` | Adopt a compiled bundle as a verified release |
| `deploy_a51.sh`, `launch_cluster_a51.sh` | Install/launch the 16.635.1 cluster |
| `builds/cr-ai-16.635.1.apk` | 16.635.1 offline APK |
| `builds/cr-engine-cluster-16.635.1.apk` | 16.635.1 cluster APK |
| `native_runner/probe/out/libcrprobe-16.635.1.so` | 16.635.1 probe |
| `native_runner/training/v4/` | V4 IL/PPO stack, evaluator, deck pool, league |
| `checkpoints/General/checkpoint-step-00000460.pt` | Primary baseline |
| `datasets/royaleapi/` | IL_Replay corpus (252k/17.8M) |
| `.resource/royaleapi-downloader/` | Optional fresh 16.x replay downloader |
| `docs/native/R0_CANONICAL_TREE.md` | R0 archive (retired) |
| `.resource/cr-native-sandbox/` | 15.535.29 proxy lab |
| `docs/native/PHASE0_BRINGUP.md` | cr-native runbook (proxy only) |
| `docs/native/PHASE2_BOTTLENECK.md` | cr-native throughput evidence |
| `.resource/CRkonwledagebase/` | Ecosystem index (58 projects, R0–R4 evidence) |
| `.resource/RoyaleFish/` | 16.635 probe/framework patches, catalogs, 17.5M models |
| `.resource/Clashback/game/engine/libg.so` | **x86_64 16.635.1 engine**, sha `c4721bd9…` |
| `.resource/FirstLight-Zero/` | X-Bow 3.0 self-play checkpoints (15.535.13) |
| `.resource/daybreak-cr/` | IL→PPO→live-Null's pipeline with league/bc recipe |
| `runs/deck-pool.json` | 7,089 decks / 443,381 uses (111 X-Bow) |

---

## Do not do

- Do not train R0 or start an R0 data/checkpoint/adapter run.
- Do not use the x86 15.535.29 engine as the source of deployable Null's
  weights without an explicit adaptation step. (The **16.635.1 x86_64** engine
  is a different case: same client build, so it is a training substrate for the
  same ruleset.)
- Do not apply `RoyaleFish/patches/probe_x86_64_16635.diff` as-is: its base is
  the 15.535.1 ARM64 probe, while our tree is already 16.635.1 ARM64. Port the
  x86 call path and tables, not the retarget.
- Do not treat in-engine or mirror win rates as ladder evidence. Hasty-CR: 93%
  vs its own engine, and one human match found a bug 30M steps missed.
- Do not gate a promoted checkpoint only on crforge; gate on the native
  16.635.1 engine.
- Do not run native-render (`offline_agent.py`, `offline_duel.py`) on 16.635.1:
  the probe SIGSEGVs in the render gate (§3.31). The headless evaluator/PPO path
  is safe.
- Do not spend cloud credits before the x86 16.635 engine runs. (N0/N1 pass; the
  next spend is evaluation volume, after step 1.)
- Do not evaluate or deploy these checkpoints with deterministic argmax: it
  collapses to `WAIT`. Use `--sample` or verify gate entropy first. The PPO
  collector needs the same care: `gate_temperature 0.2` produced `ACT` = 0 over
  5,120 eligible ticks. Collect at 1.0 and anneal down.
- Do not read §3.30's 12–14 matches/s as a training decision rate: it was
  measured at 600 ticks per RPC. At the PPO 5-tick cadence four A51 engines are
  worth **0.26 matches/s** and one x86_64 process **0.61**.
- Do not raise `CR_PPO_INFERENCE_BATCH_WAIT_MS` to coalesce engines. 250 ms does
  hit 4.0 packets/forward, but the wait costs more than the saved forward: it is
  net slower than 2 ms on both row-bound CPU and a ~2 ms GPU forward (§3.40).
  Keep the default and pipeline the loop instead.
- Do not collect `B-general-v275` at gate temperature 1.0 without the
  delay-aware legality port — it is now in place (§3.39). If an
  `env N ... resident batch step or atomic capture failed` appears again, set
  `CR_PPO_FAILURE_DUMP` and watch `adb logcat -s CRNativeProbe`; the current
  probe error string still conflates injection, step-bound and capture.
- Do not rent a GPU until a local segment completes in <10 min; at 0.04
  matches/s the A6000 idles at 4 %.
- Do not assume one A51 matches the upstream 1,528-match training scale.
- Do not mix 15.535.29 labels with 16.x labels.
- Do not mix the ARM64 and x86_64 build pins: `CR_NATIVE_BUILD_ABI` selects
  `native-16.635.1-x86_64.json`; the A51 must stay on the unsuffixed pin.
- Do not load the x86_64 probe through `JNI_OnLoad` on the host: crx mappings
  are anonymous. Use `cr_probe_attach(libg_base)` from `crx_engine_host.py`.
- Do not preload the probe's zlib `RTLD_LOCAL`. crx resolves imports with
  `dlsym(RTLD_DEFAULT)` and substitutes a stub that returns 0 for missing
  symbols, so `compress2` "succeeds" with a zero-byte output and every
  compressed batch entry is truncated (`FINDINGS.md` §3.37).
- Do not mix competitive releases (15.535 vs 16.635) in one training run or on
  one engine: a single engine tensorizes every actor row, so the learner, the IL
  anchor and every historical opponent must share the catalog release and model
  config. The trainer and collector now fail closed instead of shape-erroring.
- Do not expect a 15.535-contract checkpoint (General/IL) to tensorize on the
  x86_64 engine: its content is 16.635.8 and unresolved effect ids fail closed.
  Train 16.635-contract checkpoints there; keep the 15.535 pair for the A51 or
  an explicit adaptation step.
- Do not rebuild `native_runner/probe/out/x86_64/libcrprobe.so` without
  regenerating the pin's `probe_sha256` (attestation is fail-closed).
- Do not gate a 16.635 rich-classification anchor on `__x86_64__`.  Both
  `build_16_635_1_layout*.h` headers must carry the same vtable names and the
  probe must select them on both ABIs; the stale 15.535 fallback silently
  degrades the observation (§3.35–§3.36, enforced by
  `tests/test_probe_build_layout.py`).
- Do not run automated ladder play without confirming the server's rules.
- Do not treat `IL_Replay` as retired: it is the generalist corpus now.
- Do not commit large APKs/datasets.

---

## Appendix — historical facts worth keeping

**cr-native 15.535.29 proxy (not the deployment path):**

- 1 h soak passed; 8 workers is the knee; 12 is oversubscribed on the 10-core
  laptop.
- Engine-only at 8 workers: 36.4k ticks/s ≈ 9.6 matches/s at 100 ticks/RPC.
- 516k loop at 8 workers: 0.074 matches/s (1 tick/decision), 0.277 (4),
  0.522 (8).
- R0 CPU inference: 125–269 ms/pass; not viable without a GPU.
- Evidence: `builds/native/phase0-20261114T000821Z/`,
  `docs/native/PHASE2_BOTTLENECK.md`.

**16.635.1 parity (A51):**

- Towers: King 4824, Princess 3052 at level 11.
- Deck ids/forms/costs and starting elixir match 15.535.29.
- Unit HP matches except Electro Spirit (A51 215 vs native 217).
- Hero Knight ability state/charges/elixir match.
- Evidence: `builds/native/parity-unit-hp-15.535.29-vs-16.635.1-L11.json`.

**R0 archive:** 36,854,175 params, schema `825408c3…4d44`, CPU
batched-equivalence PASS. Retired.
