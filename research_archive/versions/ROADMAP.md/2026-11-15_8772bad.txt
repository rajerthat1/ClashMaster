# Roadmap: Null's Royale 16.635.1 ladder

Status: **active — primary target revised 2026-11-14**
Owner: Raj
Deployment target: **Null's Royale 16.635.1** (`nullsroyale.rel.free`, ARM64)
Primary model: **FirstLight V4 General** (12,684,339 params)
Training stack: **`native_runner/training/v4`** (IL + PPO)
Engine: **16.635.1 offline APK + probe on the A51**; cloud ARM64/AVD is a scale option
Proxy lab: **cr-native 15.535.29 x86_64** (fast, not deployable to Null's)
End goal: **top ladder on Null's, against any opponent/deck**

**Single source of truth.** [`HANDOFF.md`](HANDOFF.md) is the next-session starting
point. [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) is the simulator/CI reference.
[`docs/native/16.635.1/FINDINGS.md`](docs/native/16.635.1/FINDINGS.md) is the
probe-port evidence log.

---

## 0. Decision — why the target changed

The previous plan selected `cr-native` because it runs the original x86_64
`libg.so` on plain Linux with no AVD/MuMu. That was the right call for **engine
throughput**, but it optimized the wrong variable: **Null's runs 16.635.1
ARM64**, and the 15.535.29 x86 engine cannot connect to Null's ladder.

The repository already contains everything needed for the direct path:

- the **FirstLight V4 General** checkpoint (12.68M, trained across decks);
- the **IL_Replay** corpus locally (252,238 replays / 17,836,160 actions);
- the **16.635.1 offline APK, cluster APK and probe**;
- the A51 cluster/deploy tools and the 16.635.1 static-analysis notebook;
- the upstream **V4 IL/PPO stack**, deck-pool builder, league and evaluator.

So the primary path is the **actual deployment target**: 16.635.1 on the A51,
with the V4 General model. `cr-native` 15.535.29 stays as a fast proxy lab for
throughput, reward/league experiments and parity — not as the source of
deployable weights.

**R0 is retired.** It is a third observation contract with no trained
checkpoint, no local training corpus, no 16.x adapter, and no path to Null's.
The work is archived, not continued.

**What would reverse this:** if the 16.x probe cannot reach usable throughput
after N0–N2, revisit the proxy track with a V4-observation adapter on the x86
engine. Do **not** revive R0.

---

## 1. Success milestones

| Milestone | Definition | Gate |
| --- | --- | --- |
| **N0 Probe** | A51 attached; 16.635.1 cluster APK/probe starts; `coldReady`; `observe_rich` complete; 2 engines | 10 clean matches, 0 crashes, stable control ports |
| **N1 General baseline** | General vs IL and the Hog 2.6 specialists on 16.635.1 (the scripted X-Bow is the N0 behaviour soak) | 0 rejected actions; General ≥ IL; throughput recorded |
| **N2 Port complete** | Remaining core anchors and rich telemetry resolved; cold configure + live commands work. Cross-check the community relocation table (RoyaleFish) against `offset-diff.json` first | No `kUnavailableOffset`; 100-match soak |
| **N3 Scale** | Training substrate at scale: 16.635.1 x86_64 native (primary) or crforge (bulk), measured; cloud option evaluated | Enough matches/hour to feed an update budget; parity vs the A51 corpus |
| **N4 Fine-tune** | PPO/IL from General with deck pool + league | Beats frozen General/IL over N games; no degenerate loops |
| **N5 Ladder** | Policy service + touch injection; live canary on Null's | 20 clean live games; win-rate trend; no rule violations |

**Status 2026-11-14: N0, N1 and N3 pass.** N0: 10/10 scripted matches, 0
problems, 93 abilities / 0 rejections, 0 crashes. N1: General 2–0 over IL, 0–2
to Hog26-specialist1, both seats, 0 rejected, all normal terminals
(`docs/native/16.635.1/FINDINGS.md` §3.27–§3.29). N3: one A51 supplies
**12.3–14.4 matches/s** at 4 engines × 8 lanes and **14.0–14.5** at 4×16,
stable and crash-free (§3.30); the `background` cpuset was the hidden 3x cap.
The A51 now produces a valid PPO engine-node manifest (`a51_engine_node.py`,
`launch_ppo_a51.sh`). **N4 is the next gate**, and its first requirement is a
GPU: the production collector is CUDA-graph-only. Compute decision (2026-11-14):
**1× RTX A6000 @ $0.35/h, `--gpus 1`** — 12.7M params need <2 GB, ~2 ms per
batched forward is enough, and 6 vCPU covers 4 engines because tensorization
runs on the engine-node host. L40/A100 buy nothing here.

**Correctness update (2026-11-14 session 7).** Delay-aware candidate legality is
ported (`FINDINGS.md` §3.39) and the chosen config — gate 1.0, 2 ms window — now
has **16 consecutive clean 4-engine segments** (5 single-engine + 4-engine + a
10/10 stress gate with 4,097 native parses / 0 failures, §3.40). Batching at
250 ms was measured and rejected: it halves forwards but is net slower. The one
`env 6` and one x86 engine drop did not recur; both stay diagnostic.

**Revised sequencing (2026-11-14 ecosystem audit).** The community already ported
*this exact client build* (`160635001`) to x86_64, and the artifacts are in
`.resource`: `Clashback/game/engine/libg.so` (sha `c4721bd9…`), RoyaleFish's
`probe_x86_64_16635.diff` + `native_runner_16635.diff`, 16.635 catalogs and
17.5M-param 16.635 baselines. So: **port the x86 engine and adopt 16.635
catalogs first (free), then buy GPU hours for evaluation volume and PPO**, and
keep the A51 as the deploy/parity rig. See `HANDOFF.md` → "Ecosystem audit".

Updated order (each step unlocks the next):

| # | Step | Cost | Gate |
| --- | --- | --- | --- |
| 0 | **DONE** — A51 **parity corpus** (fixed-seed scripted + model games, capacity run) | free | deterministic on repeat; hashes archived (`runs/parity-a51/manifest.json`, §3.32) |
| 1 | **DONE** — 16.635 catalogs adopted as a verified release; `B-general-v275` loads and plays | free | 17.5M model loads; native play smoke 0 rejected (`COMPETITIVE_RELEASES.md`) |
| 2 | **DONE** — 16.635.1 native engine on this Linux host (x86_64 via crx, no bionic bundle); scripted parity seeds 1–5 reproduce the corpus | free | rules/placement **and evaluator** parity pass: the A51 rich-vtable fix makes both General-vs-IL games terminate tick-for-tick with x86 (§3.36) |
| 2b | **DONE** — x86_64 PPO **worker** path + 16.635 tensorization; `B-general-v275` collects on CPU with 0 UNKNOWN effects | free | engine spec carries `competitive_release`/`model_config`; zero decode/fallback counters (§3.37) |
| 2c | **DONE — negative** — the 250 ms window halves forwards (321 → 160, 3.98–4.00 packets/forward) but is **net slower** than the 2 ms default | free | 271 s (2 ms) vs 288–331 s (250 ms); the loop is the lever, not forward count (§3.40) |
| 2d | **DONE** — delay-aware candidate legality ported; gate temperature 1.0 collects cleanly | free | 5/5 single-engine seeds, 4-engine shape, and a 10/10 stress gate: 4,097 native parses / 0 failures (§3.39, §3.40) |
| 2e | **DONE** — the 16.635 evaluator path works; `runtime_scope` counts follow the active release | free | `B-general-v275` vs `A-hog26-v738` native terminals at ticks 4703/6112, 0 rejected (§3.40) |
| 3 | Re-baseline: 400+ paired games on GPU vs General / IL / Hog26 | ~$2 | 0 rejected actions; Wilson intervals reported |
| 4 | PPO fine-tune from the strongest generalist, league + deck pool | ~$8 | beats frozen baselines over ≥400 paired games |
| 5 | X-Bow 3.0 specialist lane (self-play + IL anchor, focus decks) | ~$5 | ≥55% vs champion gate, both decks |
| 6 | Ladder canary (Null's first; official only after rules check) | ~$5 | 20 clean games, no rule violations |

**A51 stays connected, idle by default.** It is the parity oracle for the same
client build and the eventual deployment rig; engines launch on demand
(`--engines 1` for parity, `4` for capacity). Do not keep four engines hot
around the clock.
The node manifest, deck pool (`runs/deck-pool.json`, 7,089 decks, 111 X-Bow) and
the 1-GPU/6-vCPU topology are built and validated. The native-render gate also
SIGSEGVs on 16.635.1 (§3.31); it is an N2 item and is cosmetic for training.

Top ladder is the long-term outcome, not a single gate. The first meaningful
result is a General-based bot that plays Null's cleanly and beats the included
baselines.

---

## 2. Architecture

```
                A51 / 16.635.1 cluster APK + probe
                 (nullsroyale.rel.free, ARM64)
                              │ control port (26789+N)
                              ▼
              native_runner V4 environment (BattleEnvV1)
                              │
              ┌───────────────┴────────────────┐
              ▼                                ▼
     V4 General / PPO trainer          evaluation + league
     (cloud GPU; 12.7M model)          (paired seeds, deck pool)
              │
              ▼
      Null's ladder deployment
      (policy service + touch injection)

Proxy lab (not deployable): cr-native 15.535.29 x86 + sandbox 516k stack
```

The GPU trains the model; the **engine is the throughput bottleneck**. Do not
size the cloud budget around the GPU alone.

---

## 3. Current assets

| Asset | Path | State |
| --- | --- | --- |
| General checkpoint | `checkpoints/General/checkpoint-step-00000460.pt` | ready, 12,684,339 params, `ppo-league-snapshot` |
| IL / Active IL / Hog specialists | `checkpoints/` | ready, opponents/baselines |
| IL_Replay corpus | `datasets/royaleapi/` | 252,238 replays, 17,836,160 actions, 1.8 GB |
| V4 training stack | `native_runner/training/v4/` | IL + PPO + deck pool + league + evaluator |
| 16.635.1 probe source/artifact | `native_runner/probe/`, `native_runner/probe/out/libcrprobe-16.635.1.so` | verified on the A51 (`b80f2bdb…`, §3.36); ARM and x86 headers share the rich vtable anchors |
| 16.635.1 APKs | `builds/cr-ai-16.635.1.apk`, `builds/cr-engine-cluster-16.635.1.apk` | built |
| A51 deploy/cluster | `deploy_a51.sh`, `launch_cluster_a51.sh` | ready; device attached (`R58NA0QTHWV`), engines stopped by default |
| 16.635.1 findings | `docs/native/16.635.1/FINDINGS.md` | in progress; anchors documented |
| A51 parity/probe tools | `tools/native_analysis/` | working for scripted/parity matches |
| PPO deck pool | `runs/deck-pool.json` | 7,089 decks / 443,381 uses (111 X-Bow) |
| A51 node manifest + topology | `runs/a51-node0/`, `runs/topology-a6000-1gpu.json` | validated, 32 resident matches |
| **16.635.1 x86_64 engine** | `.resource/Clashback/game/engine/libg.so` + `tools/native_analysis/crx_engine_host.py` | **validated** (sha `c4721bd9…`, build `160635001`); scripted and evaluator parity both pass (§3.35–§3.36) |
| **16.635 catalogs/models** | `.resource/RoyaleFish/` | `data_16635/`, `B-general-v275`, `A-hog26-v738` — not yet ported |
| Ecosystem index | `.resource/CRkonwledagebase/` | 58 projects, R0–R4 evidence labels |
| cr-native proxy | `.resource/cr-native-sandbox`, `tools/native_bringup/` | working, 15.535.29 only |

---

## 4. Workstream A — 16.635.1 runtime and probe

**Goal:** a verified 16.635.1 engine that can reset, observe, command and report
the rich V4 semantics on the A51.

What is already true:

- The touch JNI hook is port-ready (`0x0156f790`).
- Hand/deck model offsets and the replay-controller trio are recovered.
- The probe is build-selectable for 16.635.1 via
  `build_16_635_1_layout.h`; required anchors fail closed.
- Relocation report: **53 EXACT, 23 LIKELY, 70 MANUAL, 0 AMBIGUOUS, 2 MISSING**
  after the RoyaleFish cross-check (`FINDINGS.md` §3.33). The corrected core
  table is verified on the A51 (`§3.34`): 9/9 hooks install and scripted seeds
  1–2 reproduce the §3.32 corpus exactly.
- Cold configure is open: the content-ready byte is `context+0x720`, and a fresh
  `MatchConfig` reaches `ready/matchReady=true`.
- Live two-sided card plays, abilities and entity/HP observation work on the
  A51 with 0 rejections.
- Object-level rich telemetry is live; the native hook-ring streams (combat
  events, movement/character/visibility/remaining, tower troops) still report
  `unavailable` and fail closed.
- Dynamic verification on the A51 is **done**; the V4 evaluator binds the live
  build through `native_runner/versions/native-16.635.1.json`.

Next actions, in order:

1. ~~Attach the A51; launch the 16.635.1 cluster; forward `tcp:26789`.~~ Done.
2. ~~Run `scripted_xbow_match.py` and confirm 0 problems.~~ Done; N0 soak in
   progress.
3. ~~Inspect `observe_rich`.~~ Done; hook rings remain the N2 gap.
4. Run the General vs IL sampled evaluation (N1) and record throughput.
5. ~~Resolve the AMBIGUOUS/MISSING anchors.~~ Done (`§3.33`). Now install the
   rich hook rings from the recovered 16.635 offsets, one subsystem at a time,
   then re-run the 100-match soak.
6. Fix the native-render gate (`cr_render_gate_swap_buffers` → replay JSON
   setter); it segfaults on 16.635.1 (FINDINGS §3.31). The headless evaluator
   and PPO path are unaffected, but `offline_agent`/`offline_duel` are not
   usable until this lands.

**Gate:** N0 and N2.

`crforge` (`ROADMAP_CRFORGE.md`) reimplements the 16.635.1 client rules without
Android. It is a deterministic CI/differential oracle, and — once a V4
observation adapter exists — the highest-throughput bulk-training backend. It is
never a promotion gate.

---

## 5. Workstream B — General baseline

Run the included General checkpoint on 16.635.1 before training anything.

```bash
adb forward tcp:26789 tcp:26789
.venv/bin/python -m native_runner.training.v4.evaluate \
    --checkpoint-a checkpoints/General/checkpoint-step-00000460.pt \
    --checkpoint-b checkpoints/IL/checkpoint-step-00029396.pt \
    --games 2 --sample --output /tmp/opencode/eval-general-nulls \
    --port 26789 --device cpu
```

Use `--sample` (not argmax): the checkpoints put only ~6% probability on `ACT`
at the 5-tick cadence, so argmax decoding stands still
(`docs/native/16.635.1/FINDINGS.md` §3.27).

Measured on the A51 (sampled, mirror default deck, both seats, 0 rejected):

| match | result (General as A) | ticks | actions A/B |
| --- | --- | --- | --- |
| General vs IL | **General 2–0** | 3601, 3601 | 26/27, 25/27 |
| General vs Hog26-specialist1 | **Hog 2–0** | 6129, 3601 | 52/51, 23/24 |
| General vs Hog26-specialist2 | **Hog 2–0** | 6120, 6118 | 46/47, 48/48 |

General beats the IL baseline. Hog26-specialist1 converts in regulation;
Hog26-specialist2 needs full overtime in both games, so the generalist is
closer on the second specialist. The scripted X-Bow remains the N0 behaviour
soak (it is a hand-coded policy, not a checkpoint opponent); review a General
replay qualitatively against it rather than scoring it as a model.

**Gate:** N1 — passed (0 rejected actions, General ≥ IL, throughput recorded).

**Superseded baseline (2026-11-14).** This 12.7M General runs on 15.535.86
catalogs at step 460. The community's 16.635 generalist
(`.resource/RoyaleFish/models/B-general-v275.pt`, 17.5M, 275 updates) becomes
the primary baseline once the 16.635 catalogs are adopted; keep General only as
a regression reference. Do the same paired runs against
`A-hog26-v738.pt` — it is the published specialist that already reports
71.9% over 1,147 paired games.

---

## 6. Workstream C — data

The generalist corpus is already local:

- `datasets/royaleapi/` — **IL_Replay**, 252,238 replays, 17.8M actions,
  `royaleapi-battle-replay.v1`.
- Use `native_runner/training/v4/cache_builder` to convert it to IL shards.
- Use `build_ppo_deck_pool` to build the exact-form deck pool for PPO.

Data sources and their limits:

- **Official Clash Royale API:** battle logs only (last ~25 battles), no
  tick-level replays. Not an IL corpus; useful for meta/opponent stats.
- **RoyaleAPI downloader** (`.resource/royaleapi-downloader`): live 16.x
  ladder replays, structured JSON, resumable. It collects **official-game**
  replays, not Null's; follow the site's terms.
- **Hugging Face:** IL_Replay (already local), plus optional
  `Cochon123/clash-royale-replays` and `Proxv/clash-royale-ai-checkpoints`.
- **Null's-specific data:** self-play on the 16.635.1 engine and any Null's
  replays that can be captured within the server's rules. This is the data that
  matters for the ladder.

Do not mix 15.535.29 self-play labels with 16.x IL labels without an explicit
adaptation step.

---

## 7. Workstream D — training

Two stages:

1. **IL cache / behavior cloning** from the local corpus to validate the V4
   pipeline and checkpoint compatibility.
2. **PPO fine-tune from the strongest generalist** with a deck pool, league and
   opponent pool. Initialize from the 16.635 generalist once step 1 of the
   revised sequencing lands; General remains the fallback baseline.

Order of work now:

1. ~~Measure the resident channel~~ (N3 done, §3.30).
2. ~~Wire the A51 path into the PPO topology~~ (done).
3. ~~Capture the A51 parity corpus~~ (done, §3.32).
4. ~~Adopt 16.635 catalogs + load `B-general-v275` on CPU~~ (done: `docs/native/COMPETITIVE_RELEASES.md`).
5. ~~Port the x86_64 16.635.1 engine and reproduce the parity corpus~~ (done, §3.35–§3.36).
6. ~~Port delay-aware candidate legality and validate gate 1.0 collection~~ (done, §3.39).
7. ~~Re-measure batching at gate 1.0~~ (done, **negative** — keep 2 ms, §3.40).
8. Pipeline the learner loop (receive/decode/apply across engines): the measured
   critical path is the ~390 ms per-packet closed loop, not forward count.
   Continuous-batched inference (dispatch queue + 2–5 ms coalescing window), then
   the gates: scripted 1× x86 **≥0.45/s**, scripted 8× x86 **≥2.5/s**, real-model
   GPU 1× x86 **≥0.5/s**, real-model GPU 8× x86 **≥2.5/s**. 0.5/s is a one-x86
   number; 4× A51 caps at 0.26/s and is not the throughput rig. Fallback: N
   independent collector processes aggregated by the learner.
9. Rent a GPU and run 400+ paired eval games before any long fine-tune.

Rent only when a local segment completes in <10 min; do not spend GPU hours on
CPU-bound prep. Training throughput notes and the portable `ever_*` techniques
are in `HANDOFF.md` → "Adopt from the field".

Rules:

- The strongest available generalist is the initialization, not a frozen mirror
  opponent.
- Deck diversity comes from the deck pool + league, not from a fixed X-Bow
  mirror.
- Evaluate on paired seeds, both seats, with fixed decks and levels.
- Keep rejected actions at zero; a rollout with rejections is invalid.
- Do not call self-play actions “expert” for IL.
- Promote only on native-engine gates; simulator wins are not evidence.

**Gate:** N4.

---

## 8. Workstream E — deployment to Null's

- The A51 runs the 16.635.1 client with the probe; the policy service supplies
  actions over the control protocol.
- Start with offline/self-play matches, then a live canary on a low-stakes
  account.
- Monitor rejected actions, illegal placements, crashes and server responses.
- Automated ladder play may violate the server's rules; confirm what is allowed
  before running a bot on a real account.

**Gate:** N5.

---

## 9. Workstream F — cloud and scale

The GPU is not the bottleneck. The engine is.

- **ThunderCompute RTX A6000 48 GB — $0.35/hr.** Decided: **1 GPU, `--gpus 1`**,
  ~57 hours for $20. 12.7M params fit in <2 GB and the A51 caps the load at
  ~15.6k decisions/s (~2 ms per batched forward), so bigger GPUs buy nothing.
- **L40 ($0.79/h) and A100 80 GB ($1.09/h) are 2–3× the price for unused
  capacity.** Revisit only if the engine count grows by an order of magnitude.
- **A "$0.00" second GPU is not a quote.** Verify the hourly estimate before
  starting a run; until then assume it double-bills.
- **GCP $300 / Azure $100:** useful only if you need a high-vCPU host with a GPU
  or a cloud Android/AVD cluster. New accounts often have **zero GPU quota**;
  check before planning. Free-trial terms apply per account.
- **Do not spend credits before N0/N1/N3 pass.** They now do. The first useful
  spend is a GPU for the CUDA-only PPO collector and evaluation throughput.

The A51 **can** feed training: 12.3 matches/s at the PPO shape (4 engines × 8
lanes) and 14.0–14.4 at 4×16, stable (§3.30). But it is no longer the right
training engine now that a 16.635.1 x86_64 path is known. Scale options in
leverage order:

1. **x86_64 16.635.1 native engine on this host** — 16 matches/process, no
   phone, same client build `160635001`. Lib, probe port, catalogs and 17.5M
   baselines are all in `.resource`. This replaces phone-bound training.
2. **crforge 16.635.3/8** — 127k steps/s/thread, tick-exact to recorded
   battles, `VectorBattle` Python env. Highest throughput; needs a V4→crforge
   observation adapter and is a reimplementation, so it is a bulk-training
   backend, never the final gate.
3. Add more A51-class ARM64 devices — cheapest per *deployable* engine.
4. Cloud x86 host with KVM + ARM64 Android image/AVD cluster (the upstream
   path; complex, verify nested virtualization and throughput first).

### 9.1 Adopt from the field

Take artifacts where licensed and version-matched; take **methods** where not.
Full table with effort and step mapping: `HANDOFF.md` → "Adopt from the field".

| Gap | Source | Take | License |
| --- | --- | --- | --- |
| 16.635 catalogs, model config | RoyaleFish `data_16635/`, `native_runner_16635.diff` | data + patch | Apache-2.0 |
| x86_64 16.635.1 engine | RoyaleFish `probe_x86_64_16635.diff` + Clashback `libg.so` | probe port (game binary local-only) | Apache-2.0 / unknown |
| Missing anchors | RoyaleFish `logical_*.inc` | second relocation table | Apache-2.0 |
| 16.635 baselines | `B-general-v275`, `A-hog26-v738` | eval opponents + starting point | Apache-2.0 |
| Training throughput | RoyaleFish `ever_*` | fp16+action equality, multi-size CUDA graphs, LSTM state table, sparse + row-packed updates | Apache-2.0 |
| Bulk backend | crforge `python/` | `VectorBattle` (needs V4 adapter) | Apache-2.0 |
| League / specialist recipe | daybreak-cr | PFSP, focus/hard decks, IL anchor, 55% gate, rollback loop | unknown → reimplement |
| X-Bow 3.0 | FirstLight-Zero | self-play recipe + eval protocol; retrain on 16.635 | Apache-2.0 |
| Eval discipline | ClashAI `RESULTS.md` | Wilson intervals, ≥150-game gates, held-out decks | method |
| Anti-overfit probes | Hasty-CR | parameter-perturbation checks | MIT |
| Ladder orchestration | daybreak-cr, RoyaleBot, ClashAI | queue/pending-elixir budget, receipts, no blind retry | MIT / method |

**Never take:** leaked clients, redistributed APKs/assets, `rb-build` binaries as
dependencies, 15.535-labelled weights as 16.635 weights, or in-engine win rates
as ladder evidence.

---

## 10. Proxy track — cr-native 15.535.29 (retained, not critical path)

The x86 runtime remains valuable for:

- reward/league/PPO algorithm experiments with the 516k stack;
- throughput and transport profiling;
- parity checks against 16.635.1 (towers, cards, unit HP, abilities);
- a fallback if the 16.x probe stalls.

It is **not** the deployment path. Do not train a policy on 15.535.29 labels and
assume it transfers to Null's without an adaptation step.

Measured facts (kept for reference):

- 1 h soak passed; 8 workers is the knee; 12 is oversubscribed on the 10-core
  laptop.
- Engine-only at 8 workers: 36.4k ticks/s ≈ 9.6 matches/s (100 ticks/RPC).
- Full 516k loop at 8 workers: 0.074 matches/s at 1 tick/decision, 0.277 at 4,
  0.522 at 8.
- R0 CPU inference: 125–269 ms/pass; not viable for training without a GPU.
- A51 16.635.1 parity: towers, deck ids/forms/costs, starting elixir and all
  unit HP match at level 11 except Electro Spirit (215 vs 217).

Evidence lives under `builds/native/`.

---

## 11. Retired / deferred

- **R0 elite-v3** — retired from the Null's path. Code and evidence are archived
  in `docs/native/R0_CANONICAL_TREE.md`, `tools/native_bringup/smoke_r0_model.py`
  and `verify_r0_batched.py`. Do not start an R0 run.
- **R0 data/checkpoint gate, CUDA gate, R0 adapter** — retired with R0.
- **cr-native 15.535.29 as the critical path** — demoted to proxy lab.
- **Custom ARM64 `libg` loader** — still retired; the 16.635.1 path uses the
  offline APK/probe.
- **Linux AVD / MuMu / phone clusters as the primary** — only a scale option if
  the A51 is insufficient.
- **“16.635.1 is parity only”** — reversed: it is the primary target.
- **A51 as the training engine** — superseded 2026-11-14 by the x86_64 16.635.1
  engine (same client build `160635001`). The A51 stays as the parity oracle and
  the deployment rig.
- **“crforge is not a trainer”** — superseded: `crforge/python/` ships
  `VectorBattle`, so it is a viable bulk backend once a V4 observation adapter
  exists. It is still never a gate.

---

## 12. Risks and unknowns

1. **16.x port completeness.** Rich telemetry and several core anchors are still
   unresolved; N2 may take longer than N0/N1.
2. **Engine throughput / substrate.** The A51 is far below the upstream
   1,528-match training scale, and it is now the wrong axis. The x86_64 16.635.1
   port is the fix; until it reproduces the A51 parity corpus, throughput
   estimates are provisional.
3. **Simulator self-evaluation.** Hasty-CR: 93% against its own engine, and one
   human match found a bug 30M steps missed. Gate on the native engine only.
4. **Null's-specific content.** The General checkpoint and IL_Replay are
   official-game 16.x data; Null's may have custom balance/cards. Verify the
   content fingerprint and card compatibility.
5. **Ladder rules.** Automated play may be prohibited; confirm before a live
   account is used.
6. **Cloud quotas.** GCP/Azure GPU quota may be zero on new accounts; don't
   assume the credits are immediately spendable on GPUs.

---

## 13. QA harness

- `illegal: 0`, zero rejected actions per accepted rollout.
- Paired-seed, both-seat evaluation; fixed decks/levels.
- 100-match soak after any probe change.
- Replay review for same-tile spam, passive openings, wasted spells, bad
  Tesla/X-Bow defense.
- Live canary: 20 games, observation-vs-screen diff, no crashes.
- Record checkpoint SHA-256, dataset/tag selection, deck pool, seed and config
  with every run.

---

## Appendix A — cr-native 15.535.29 evidence index

| Item | Path |
| --- | --- |
| Phase 0 soak/sweep | `builds/native/phase0-20261114T000821Z/` |
| Phase 2 bottleneck | `docs/native/PHASE2_BOTTLENECK.md` |
| X-Bow determinism (20 seeds) | `builds/native/xbow-determinism-20.json` |
| A51 unit HP/ability parity | `builds/native/parity-unit-hp-15.535.29-vs-16.635.1-L11.json` |
| Runtime runbook | `docs/native/PHASE0_BRINGUP.md` |
| Linux runtime | `docs/native/LINUX_BIONIC_RUNTIME.md` |

## Appendix B — R0 archive

R0 adoption and verification are complete as an engineering exercise:
36,854,175 params, schema `825408c3…4d44`, CPU batched-equivalence PASS.
It is retired from this plan. See [`docs/native/R0_CANONICAL_TREE.md`](docs/native/R0_CANONICAL_TREE.md).
