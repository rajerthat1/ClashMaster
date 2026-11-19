# Roadmap: Null's Royale 16.635.1 ladder

Status: **active — substrate decision revised 2026-11-15**
Owner: Raj
Deployment target: **Null's Royale 16.635.1** (`nullsroyale.rel.free`, ARM64)
Primary model: **FirstLight V4 General** (12,684,339 params)
Training stack: **`native_runner/training/v4`** (IL + PPO)
Bulk trainer: **crforge 16.635.1 `VectorBattle`** (after the C1 encoder gate)
Gate + deploy rig: **16.635.1 native engine** — x86_64 on this host, A51 as the
ARM64 parity/deploy oracle. Kept warm; never deleted.
Proxy lab: **cr-native 15.535.29 x86_64** (fast, not deployable to Null's)
End goal: **top ladder on Null's, against any opponent/deck**
Out of scope: **official Supercell servers** (different client/protocol, legal/ToS
risk; not a later phase).

**Single source of truth.** [`HANDOFF.md`](HANDOFF.md) is the next-session starting
point. [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) is the bulk-training plan and
CI/parity reference. [`docs/native/16.635.1/FINDINGS.md`](docs/native/16.635.1/FINDINGS.md)
is the probe-port evidence log.

---

## 0. Decisions

### 2026-11-15 — crforge is the bulk trainer; the native engine is the gate

The native path works, but its **Python per-packet data path** sets the ceiling,
not the engine: ~8–10 ms per lane (`step` 8.7 = engine round 3.1 + observe 4.0 +
masks 1.6, plus tensorize) → **0.082 matches/s per process** and **4 % GPU
utilisation** on the rented A6000. crforge removes the engine round from the
critical path (one JVM call for 256 lanes, zero-copy NumPy observations) and
measured **2.6 matches/s** on one thread of this laptop, **6.0** at 4 threads,
**13.0** at 8; 18.5 at 64 battles/1 thread and 52.5 at 4 threads on an M4 Pro.

So: **bulk-train on crforge, promote on native.** The engine is not deleted or
idled — it stays the promotion gate, the differential-parity oracle and the
deploy rig, exercised on a fixed cadence.

The one decision variable is **not** the simulator's tick rate; it is the
observation/tensorizer cost per lane. A straight per-lane port buys ~1.4×; a
vectorized 256-lane encoder at ≤0.5 ms/lane buys ~10–30×. **Stage C1 measures
that before any scale spend.** Detail, gates and stop conditions:
[`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §0–§4.

Two rules that do not move:

1. **Never promote on crforge.** A policy that wins in a simulator has found a
   simulator. ≥150 paired native games is the gate.
2. **Parity is continuous, not a final exam.** 50–150 native matches every N
   updates and after every adapter change; a persistent win-rate gap freezes the
   checkpoint.

### 2026-11-15 — learner deck fixed to X-Bow 3.0; the opponent pool varies

The learner plays **one deck** (X-Bow 3.0); variety comes from the **opponent
pool**, not from training a second generalist. `B-general-v275` is the
initialization and a **frozen league opponent**; it is not retrained as the
learner.

Locked deck (highest-use X-Bow in `runs/deck-pool.json`, 2,983 uses):

| Slot | Deck-pool key | 16.635 name | Form |
|---|---|---|---|
| 1 | `x-bow` | `Xbow` | base |
| 2 | `tesla-ev1` | `Tesla` | evo 1 |
| 3 | `archers-ev1` | `Archer` | evo 1 |
| 4 | `knight-hero` | `Knight` | hero (catalog has evo + hero; deck uses hero) |
| 5 | `skeletons` | `Skeletons` | base |
| 6 | `electro-spirit` | `ElectroSpirit` | base |
| 7 | `fireball` | `Fireball` | base |
| 8 | `the-log` | `Log` | base |
| tower | `tower-princess` (159000000) | `Princess` | — |

**Support check (2026-11-15): pass.** All eight cards are in the verified 16.635
release (`native_runner/data/competitive-16.635/card_support.json`, 123 cards)
and in `crforge_catalog.json`; the needed forms exist in both catalogs
(`Archer_EV1`, `Tesla_EV1`, `Knight_hero`). The only open item is the **tower
troop** (`SideSetup` at C2) — verify the Princess tower troop id on the first
adapter run.

**Opponent pool for the specialist** (do not train against a mirror only):

- a **learning generalist** (co-trained, or refreshed from `B-general-v275` every N
  updates) plus a frozen `B-general-v275` copy as the anchor; both sample decks
  from the 7,089-deck pool;
- `A-hog26-v738` (71.9 % ±2.6 / 1,147 games **vs its own start**) as the
  strength bar — **not a bar on the learner deck**: session 40 measured
  `B-general-v275` beating it **49–1** on the fixed X-Bow/Hog pairing
  (`FINDINGS.md` §3.74). Keep it as an opponent and a canary; the seed mirror is
  the policy gate;
- mirror X-Bow (10 %);
- 2–3 historical learner checkpoints, PFSP-weighted;
- hard decks: Mortar, Evo Witch, Minion Giant, Golem/beatdown, Log Bait, Graveyard;
- **focus decks chosen from the cards the learner loses to most**, and **drills**
  that replay real failures with small variations (the X-Bow equivalents: X-Bow
  unprotected, Tesla wasted, second lane abandoned).

Fixed levels, both seats, paired seeds. The specialist gate is ≥55 % vs the
champion on the X-Bow matchup, plus the ≥150-game native promotion gate.

**Reuse check before training from scratch:** FirstLight-Zero's X-Bow 3.0 is a
**1.17M model trained only on the X-Bow mirror** (15.535.13), with a different
architecture (`unit_type`/`blocks.*`, 75 tensors). It is a **reference and a
possible opponent**, not a drop-in init for the V4 12.7M/17.5M stack — a card-row
converter does not change the architecture. RoyaleFish's 15.535 → 16.635
converter is useful for **same-architecture V4 models**, not for this one.

**What this does not change:** C0–C3 are deck-agnostic, so the adapter schedule is
the same. Hog 2.6 is not the learner; it is the published bar and the fastest
fallback canary (`A-hog26-v738` can be deployed unchanged) if X-Bow slips.

### 2026-11-14 — why the deployment target changed

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
| **N3 Scale** | Bulk substrate chosen and measured: crforge `VectorBattle` after the C1 encoder gate; native engine remains the gate and the fallback | ≥ 0.8 matches/s per process (≥ 1,000 decisions/s); target ≤ 0.5 ms/lane; parity vs the A51 corpus |
| **N4 Fine-tune** | **X-Bow 3.0 specialist** fine-tuned from the generalist with a varied opponent pool (generalist, Hog 2.6, mirror, history, hard decks) + league; bulk collection on crforge, promotion on native | ≥55 % vs champion on the X-Bow matchup **and** ≥150 native paired games; 0 rejected; no degenerate loops |
| **N5 Ladder** | Policy service + touch injection; live canary on Null's | 20 clean live games; win-rate trend; no rule violations. Official servers are out of scope |

**Status 2026-11-15: N0, N1 and N3 pass.** N0: 10/10 scripted matches, 0
problems, 93 abilities / 0 rejections, 0 crashes. N1: General 2–0 over IL, 0–2
to Hog26-specialist1, both seats, 0 rejected, all normal terminals
(`docs/native/16.635.1/FINDINGS.md` §3.27–§3.29). N3 is redefined: the native
x86_64 path is measured at **0.61 matches/s raw / 0.082 training floor per
process** and is CPU/data-path bound, while crforge measures **2.6–13.0
matches/s** on this laptop (1–8 threads) and **18.5–52.5** on an M4 Pro. The
A51's 12–14 matches/s figure is a 600-tick transport ceiling, not a training
rate (at the PPO 5-tick cadence it is 0.26). **N4 is the next gate**, and its
first requirement is the C1 encoder measurement, not a GPU rental.

Compute decision (2026-11-15, supersedes the 2026-11-14 rental): **1× RTX A6000
@ $0.35/h, `--gpus 1`, rented only for C4/C5 and promotion runs.** At 0.04
matches/s the card idled at 4 %; at crforge's vectorized rate it is the
bottleneck and worth saturating (~51 matches/s per card). L40/A100 still buy
nothing.

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
| 3 | **C0 — close crforge legality** (last MiniPekka reject; 20-game run) | free | `illegal: 0`, `truncated: 0`, no unexplained rejects |
| 4 | **C1 — encoder spike** (per-lane vs vectorized 256-lane `ObservationV1`) | free | ≥ 1,000 decisions/s per process (≤ 1 ms/lane); target ≤ 0.5 ms/lane, else fix the encoder |
| 5 | **C2–C3 — resident adapter + fast observation** | free | 50+ steps identical to the bridge; ≥ 0.8 matches/s per process; tensorizer parity |
| 6 | **C4 — batch + PPO integration**; native eval on every checkpoint | ~$2 | 10 segments: 0 rejected, `eligible_act_rate > 0`, native eval not degrading |
| 7 | **C5 — parity curve + league / specialist fine-tune** | ~$8 | ≥150 native paired games; win-rate gap inside Wilson; no simulator-exploit signature |
| 8 | **N4 native fine-tune / adaptation** (short, on the extracted engine) | ~$2 | closes the sim-real gap before any live run |
| 9 | **C7 — live canary on Null's** (low-stakes account) | ~$5 | 20 clean games, no rule violations, then a slow ladder push |

**Both engines stay available.** The x86_64 16.635.1 extracted engine and the A51
are the parity oracles and the deploy rig; launch on demand (A51 `--engines 1`
for parity, `4` for capacity). Do not keep four phones hot around the clock, and
do not delete the host engine. The node manifest, deck pool
(`runs/deck-pool.json`, 7,089 decks, 111 X-Bow) and the 1-GPU/6-vCPU topology are
built and validated. The native-render gate also SIGSEGVs on 16.635.1 (§3.31); it
is cosmetic for training and does not block the gate runs.

Top ladder is the long-term outcome, not a single gate. The first meaningful
result is a General-based bot that plays Null's cleanly and beats the included
baselines.

---

## 2. Architecture

```
        BULK (throughput)                    GATE (truth)
  crforge VectorBattle (JVM)          16.635.1 native engine
  256 lanes · zero-copy NumPy          x86_64 host + A51 oracle
            │                                   │
   vectorized ObservationV1             native_runner BattleEnvV1
   + action masks (C1–C3)               (validated, lean=False)
            │                                   │
            └──────────────┬────────────────────┘
                           ▼
               V4 PPO trainer (cloud GPU)
               league · deck pool · opponent pool
                           │
                           ├─ C5 parity curve: 50–150 native games / N updates
                           ├─ N4 native fine-tune / adaptation
                           ▼
               Null's ladder deployment
               (policy service + touch injection)
               A51: deploy + 20-game live canary

Proxy lab (not deployable): cr-native 15.535.29 x86 + sandbox 516k stack
```

The **GPU is not the bottleneck** on the bulk path, and the **simulator is not
the truth**. crforge feeds the GPU; the native engine decides whether the
checkpoint is real. Size the cloud budget per matches/s per dollar at the gate,
not per raw tick rate.

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
| **16.635 catalogs/models** | `.resource/RoyaleFish/` | adopted as a verified release; `B-general-v275`, `A-hog26-v738` load and play |
| Ecosystem index | `.resource/CRkonwledagebase/` | 58 projects, R0–R4 evidence labels |
| cr-native proxy | `.resource/cr-native-sandbox`, `tools/native_bringup/` | working, 15.535.29 only |
| **crforge simulator** | `.resource/crforge/` | 16.635.3/8 tables; `VectorBattle` 1–256 lanes, zero-copy NumPy; core 127k ticks/s/thread |
| **crforge bridge** | `crforge_bridge/` | `status/attest/configure/step/observe…`; 152/152 cards, 118 forms, `unmapped: []`; evaluation only (20–30 decisions/s) |
| **crforge bench (2026-11-15)** | `crforge_env.bench` | 2.6 / 6.0 / 13.0 matches/s at 1 / 4 / 8 threads, 256 lanes on this laptop |
| **C1 encoder spike** | `tools/crforge/encoder_spike.py` (to add) | per-lane vs vectorized `ObservationV1`; the stage gate |

### 3.1 Third-party substrate — Clashback and RoyaleFish

Both live in `.resource/` (gitignored). Neither is on the crforge bulk path
directly; both are load-bearing for the gate and for C4. Pin the commits in every
checkpoint artifact.

**Clashback** (`github.com/ak1NaN/Clashback`, commit `002aa8c`; **license
unknown, game binary local use only, do not redistribute or commit**):

| Piece | Role |
| --- | --- |
| `game/engine/libg.so` (`c4721bd9…`, build `160635001`) | **The gate's rules engine.** The extracted 16.635.1 x86_64 client that `crx_engine_host.py` boots. Without it there is no native gate. |
| `vendor/crx/` (`loader.py`, `env.py`, `jni.py`, `hooks.py`, gates) | ELF loader + JNI shims + `CrxEnv` boot/pump. **We use the loader, `CrxEnv` as the host, and `neo_replay.resourcefs` as the asset shim — not `observe.py`'s schema.** |
| `neo_replay/` (`runner.py`, `tick_cache.py`, `native_snapshots.py`, `engine_lock.py`) | Replay drive + per-tick cache/snapshots. Use for **parity corpus extension and golden traces**, not as a third observation contract. |
| `game/assets.scdb`, assets | 16.635.8 content for the gate. Local only. |

Rules: never redistribute or commit the binary/assets; never let `neo_replay`
become a training data path; the gate is `libg.so` + our probe + our
`BattleEnvV1`, not Clashback's observation schema.

**RoyaleFish** (`github.com/fabiospalla9-tech/RoyaleFish`, Apache-2.0, commit
`c42108d`; 501 MB):

| Piece | Role |
| --- | --- |
| `data_16635/` | **Already adopted** as `native_runner/data/competitive-16.635/` (verified release, all 5 catalog ids match `B-general-v275`). |
| `models/B-general-v275.pt`, `A-hog26-v738.pt` | **The league seed and the gate's reference opponents.** v275 = 17.5M generalist; v738 = 71.9 % ±2.6 over 1,147 games. The 15.535 v49/v98 pair stays as legacy references. |
| `royalefish/ever_{server,collector,learner,loader,packed,node_fork}.py`, `net.py`, `reward.py` | **C4's throughput recipe.** The **inference port** is four tricks: fp16 + fp32 action equality, CUDA graphs at several batch sizes, one GPU-side LSTM state table, layout/pack once. **Ported and measured (session 36):** `crforge_bridge/fast_forward.py`; forward 182 → 133 ms on the A6000, but the partial graph does not pay and six producers gain only 3 % ($77.5/1M) — the CPU half is the gate. The server's TCP/pickle wire protocol (`net.py`) is **discarded** — crforge is in-process. Sparse trajectories, row-packed updates and the loader process are the separate training-side items. `ms.inference_ms` 157 → 21.5 ms is the forward target for ~$20/1M **physical** at $0.35/h; the ~490 ms CPU half is a separate gate (`ROADMAP_CRFORGE.md` §4). |
| `patches/native_runner_16635.diff`, `probe_x86_64_16635.diff` | **Reference only.** Different base tree; our catalogs are a first-class release and our x86 probe is already ported. Do not apply as-is. |
| `logical_{funcs,offsets,vtables}.inc` | **Already used** for the relocation cross-check (0 AMBIGUOUS, §3.33). Keep for re-checks on a version bump. |

**Upstream watch (pulled 2026-11-15, `c42108d`; README + media only since `da9c8cf`):**

- **The November 2026 Hog 2.6 replay set is cancelled at the source.** RoyaleAPI closed replay access for everyone on **2026-11-16** (Discord login plus a hand-reviewed request form, no stated turnaround). RoyaleFish's collector stopped at a few hundred games; the Hugging Face upload is **postponed with no date**. Treat the set as unavailable — no tier-2 Hog IL anchor and no meta snapshot from it, and an X-Bow collector hits the same wall. The local 252k corpus is unaffected.
- **A v1881 learned defense from real human attacks.** 52,632 human attacks replayed card-for-card as "ghosts", four attempts each, rewarded for beating its own earlier attempts; 2,666 ghosts held out. On those unseen attacks: defense score 0.53 → **0.75**, princess towers lost per game 0.47 → **0.25**, fully clean defenses 11 % → **19 %**; ~1,900 updates and 99 % of the 17.5M weights changed. Open: it under-attacks and rarely commits the Hog, and its Fireball leads moving troops. **The checkpoint is not uploaded yet.** A template for the Hog opponent, not a change to the X-Bow learner.
- **Model conversion script (15.535 → 16.635) is coming.** It remaps **card rows
  for the same architecture**, so it helps older V4-family checkpoints (FirstLight
  CR, community V4 models), **not** FirstLight-Zero's X-Bow 3.0 — that is a
  1.17M, mirror-only model with its own architecture, so it stays a reference /
  possible opponent, not a V4 init. Verify forms and a native parity sample before
  trusting any conversion.
- **The league is no longer frozen.** RoyaleFish now trains A against a generalist B that **keeps learning**, plus older versions, mirror games, focus decks picked from the cards A loses to, and drills from real mistakes. A frozen opponent can be memorized; adopt this shape for X-Bow (co-trained or periodically refreshed generalist, 10 % mirror, hard-deck focus).
- **New models (not X-Bow):** A v1290 (Hog 2.6) uploading soon; **Frost** (Knight-EV1 / Hero Ice Wizard / Tesla-EV1 / Bats / Minion Giant / Skeletons / Barbarian Barrel / Fireball) in 1–2 days; an anti-2.6 model at ~81 %. Use them as league opponents and as the strength bar. `Blade` is no longer mentioned in their notes.
- **Throughput validation:** ~2,500–3,000 complete games/h on an RTX 5060 Ti + V100, ~55k decisions from ~64 games per update, 70–90 s/update; **NVIDIA MPS** nearly doubled games/h. That is the `ever_*` recipe working; make MPS part of C4.

What RoyaleFish does **not** give us: a crforge adapter, a gate waiver, or
permission to use 15.535 weights as 16.635 weights. It is a data/recipe/opponent
source.

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
Android. It is the deterministic CI/differential oracle and, after the C1–C3
adapter, the bulk-training backend. It is never a promotion gate; the native
engine stays warm as the gate, the parity oracle and the deploy rig.

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
71.9% over 1,147 paired games **against its own start**; on the learner deck it
loses **49–1** to the seed in session 40's 50-game block, so it is a canary, not
the bar (`FINDINGS.md` §3.74).

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

### 6.1 Do expert replays help? (2026-11-15)

**Yes, but as a tier-2 input — three specific jobs, not "more data".**

What we already have: `IL_Replay` is 252,238 games; **2.25 % of deck uses are
X-Bow** (111 decks, 9,970 uses), i.e. roughly **5,700 X-Bow games** already in the
corpus, version-mapped to 16.635. The gap is **current-patch, top-player X-Bow**
games.

Ranked by value per hour spent:

1. **Drills and reward mining (highest leverage, cheapest).** 200–500 expert games
   are enough to extract the decision points that matter (X-Bow placement and
   timing, Tesla pull, spell cycle, second-lane defence) and to replay the
   learner's own failures with small variations — the method RoyaleFish uses.
   This improves PPO directly and does not need a big corpus.
2. **BC warm-start / KL anchor.** 2,000–5,000 version-matched X-Bow games, 1–2
   epochs, then PPO. This roughly doubles the X-Bow-specific data and can teach
   high-level lines faster than self-play. **Only do it if the generalist is
   actually weak on the deck** — see the decision rule below.
3. **Opponent/meta modelling.** Deck frequencies, hard matchups and focus weights
   for the league; optionally BC a Hog / Log Bait opponent from replays. The
   RoyaleFish Hog 2.6 set (HF) would have belonged here, not in the X-Bow learner,
   but it was **cancelled 2026-11-16** when RoyaleAPI closed replay access; a Hog
   opponent anchor now has to come from the local corpus or a fresh collector.

**Not worth much:** mass-scraping "more ladder data" as a substitute for PPO and
the league; TV-Royale frame images (no state or actions); non-16.635 replays
without a conversion step; using expert action agreement as a *strength* metric.

**Decision rule (do this before collecting anything):** run `B-general-v275` on
the locked X-Bow deck for 100–200 native games vs Hog 2.6 and the generalist.

- **≥ 50 %** → the model already knows the deck; skip BC. Use replays for drills,
  meta and evaluation, and spend the time on C1–C4.
- **< 40 %** → collect 2,000–5,000 X-Bow games, BC 1–2 epochs, then PPO.
- Always mine 200–500 games for drills; it is hours, not weeks.

**Collection constraints:** top-1k / Ultimate Champion X-Bow mains, last ~25
battles per player, RoyaleAPI at **1 request / 10 s** (terms + attribution),
filter 16.635 **and** ≥7/8 X-Bow cards, dedupe, keep local. Store the source
version, player-tier band and a hash with the cache. Never mix 15.535 and 16.635
rows in one run.

---

## 7. Workstream D — training

Two stages:

1. **IL cache / behavior cloning** from the local corpus to validate the V4
   pipeline and checkpoint compatibility.
2. **PPO fine-tune from the strongest generalist** with a deck pool, league and
   opponent pool. Initialize from the 16.635 generalist once step 1 of the
   revised sequencing lands; General remains the fallback baseline.

**Deck scope (locked 2026-11-15).** The learner deck is **X-Bow 3.0**; the
opponent's deck is sampled from the pool and the league. The exact eight cards,
forms, tower troop and support check are in §0. Do not retrain a generalist as
the learner — `B-general-v275` is the init and a frozen opponent.

### Plan of record (2026-11-15) — crforge bulk, native gate

The native path below is **not deleted**; it is the gate and the fallback. The
bulk path is crforge, in the order that protects the schedule:

1. **C0 — legality.** Close the last MiniPekka `26000018` reject; 20-game run with
   `illegal: 0`, `truncated: 0`. A rollout with a reject is invalid.
2. **C1 — encoder spike.** 256-lane `VectorBattle`; build `ObservationV1` per-lane
   and vectorized; record `ms/lane` and decisions/s. **Gate: ≥ 1,000 decisions/s
   per process** (≤1 ms/lane at 256 lanes); target ≤0.5 ms/lane for the full
   10–30×. This is the only evidence that the speedup exists; it costs a day.
3. **C2–C3 — adapter + fast observation.** Resident `NativeClashEnv` surface,
   native masks, cached towers, `policy_features_only`; gate ≥ 0.8 matches/s per
   process and tensorizer parity to the native path.
4. **C4 — batch + PPO.** 8 lanes/process, 2–4 processes, pipelined inference;
   gate a 10-segment run with 0 rejected, `eligible_act_rate > 0`, and a native
   eval that does not regress. The loop, its **rollout storage** and the
   **update** are in (`tools/crforge/self_play.py`, `crforge_bridge/rollout.py`):
   the gate shape stores every decision, rebuilds the native reward, finalizes one
   `StoredRolloutV4` per segment, and `--ppo-update` runs
   `distributed_ppo_update_v4` over it (`FINDINGS.md` §3.58–§3.59). The update
   now splits at every episode boundary, so a lane that autoresets mid-segment
   replays as contiguous slices: gate shape 1,024 lane-decisions / 45 ACT /
   0 rejected, one 1,024-frame update; multi-episode 8 × 2 × 60 with 16
   mid-rollout starts, 2 updates, 0 rejected. Still open: CUDA/MPS throughput
   (NVIDIA MPS, the 8-vCPU question, the A6000 overlapped-loop gate). The native
   parity cadence is defined: `--checkpoint-every` plus
   `tools/native_analysis/parity_curve.py`, priced at 27.9 updates per 4-game
   gate (`FINDINGS.md` §3.87). A **CPU-updating producer needs
   `--jvm-arg=-XX:TieredStopAtLevel=1`** until the JVM's C2 miscompile of
   `SeatIds.of` is fixed: it reproduces on every host of the rented class tried
   (3/3 on a fresh A6000, and the fault maps to `of`'s own level-4 nmethod), and
   the stand-alone probe is a **false negative** on that host, so "the probe
   passed" is not a clean bill (`FINDINGS.md` §3.85–§3.88).
5. **C5 — parity curve.** 50–150 native matches every N updates and after every
   adapter change; a persistent win-rate gap freezes the checkpoint.
6. **C6–C7 — native promotion gate, then live canary.** ≥150 native paired games,
   Wilson intervals, held-out decks; then 20–100 live games on Null's.

Full gates, stop conditions and fidelity pins:
[`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §2–§4. Do not rent a GPU before C1;
do not train at scale before C0; do not promote on crforge.

### Native-path work log (the gate and the fallback)

> **Historical (2026-11-15).** The plan below is what the native path would need
> to become a bulk trainer again. It is kept because the engine is the gate and
> the fallback, and because the thin/fused observation work is shared with the
> crforge adapter. The native 2.5/s goal is **not** the current bulk plan; crforge
> C0–C4 is.

1. ~~Measure the resident channel~~ (N3 done, §3.30).
2. ~~Wire the A51 path into the PPO topology~~ (done).
3. ~~Capture the A51 parity corpus~~ (done, §3.32).
4. ~~Adopt 16.635 catalogs + load `B-general-v275` on CPU~~ (done: `docs/native/COMPETITIVE_RELEASES.md`).
5. ~~Port the x86_64 16.635.1 engine and reproduce the parity corpus~~ (done, §3.35–§3.36).
6. ~~Port delay-aware candidate legality and validate gate 1.0 collection~~ (done, §3.39).
7. ~~Re-measure batching at gate 1.0~~ (done, **negative** — keep 2 ms, §3.40).
8. ~~Pipeline the learner loop~~ (**done 2026-11-15, §3.41**). Continuous
   batching: one `ppo-inference` thread with one batch in flight, receives staged
   during the forward, boundary bootstraps on the same thread. CPU-verified
   (non-model wall −14 % with a cheap forward, **neutral with the 17.5M model**;
   10/10 segments clean); the CUDA path is still unverified. **The gates in the
   old plan were wrong**: their "ceilings" came from the raw resident channel,
   which skips the observation build (~26 ms/packet) and tensorization (~14 ms),
   i.e. ~90 % of a real packet. Measured training floor: **1× x86 0.082/s,
   2× 0.10/s, 4× 0.15/s** in 6,068-tick matches.
9. **Decision 2026-11-15 — fix the data path, then scale.** The engine, the loop
   and the model are done; the one thing ~5× behind the field is the per-decision
   data path. FirstLight's 9.85 matches/s is ~128 slots at our engine's *raw*
   per-slot rate (0.077 vs 0.081/s); daybreak gets there with a C feature
   extension and one decision server per GPU. So the plan of record is:
   1. ~~micro-profile `BattleEnvV1.step`~~ (**done 2026-11-15, §3.42**): one lane
      is 17.1 ms `step` / 10.7 ms `observe`, split `_entities` 4.7,
      `_extract_towers` 2.7, `action_mask` 1.9, `_players` 0.9,
      `_events_for` 0.8; ~34 % of self time is contract validate/freeze, ~11 %
      rich telemetry projection. Two fixes shipped: scalar-first
      `contracts._freeze` (**+19 % median pkt/s**) and one tower projection per
      tick. **Lanes are GIL-bound** (0.90–0.94 of one core at 1–8 lanes, ≤1.5×),
      so throughput is processes, not lanes.
   2. ~~thin the observation for training~~ (**done 2026-11-15, §3.43**):
      `policy_features_only` now builds `EntityStateV1`/`TowerStateV1`/
      `PlayerStateV1`/`ObservationV1` and every runtime state through a
      non-validating `construct_contract`, skips provenance trees and unused
      runtime fields, caches the hand runtime contract and form aliases, and
      keys every observation cache by `(identity, lean)` so validated callers
      never see a lean object. Paired A/B: **CPU 11.75 → 7.81 ms/packet
      (median −28…−34 %), 78 → 113 lane-packets/s**; 8 lanes 10.6/12.5 →
      8.2/9.4 ms. The validated parity path is untouched (`lean=False` default)
      and the tensorizer is bit-identical. The observation build is still ~4 ms
      of an ~8.7 ms step; the remainder is native round trip (~3 ms) and rich
      telemetry binding;
   3. ~~drop per-lane Python thread pooling~~ — deprioritised: lanes are
      GIL-bound and the per-packet cost is no longer dominated by pooling.
      Next measured target is the fused data path: build tensor rows straight
      from the rich frame and skip the intermediate Python contract objects
      (daybreak's `native_features.c` shape); parallelise tensorization only
      where torch releases the GIL, otherwise use processes.
   4. one short GPU session to validate the pipelined CUDA path;
   5. move 8 engines to a real 8-core host (this laptop has 2 P-cores;
      gate ~2/s aggregate);
   6. 400+ paired native games, then a **bounded X-Bow 3.0 fine-tune** before
      the generalist league.
   If step 2 lands, 1 engine ≈ 0.26/s and 8 engines ≈ 2.1/s ≈ 180 k matches/day,
   i.e. FirstLight's whole 1,450-round run in ~8 days. The 2.5/s goal survives;
   it needs 8 engines and a thin path, not one engine and a raw-channel ceiling.
10. **Cheap parallel experiment.** Rules permitting, ~20 ladder games of
   `A-hog26-v738` / `B-general-v275` on Null's measure the official-16.635 →
   Null's-balance domain gap before a long run.
11. Rent a GPU for the 400+ paired eval games and the fine-tune only after step
   9.2 lands. At today's packet cost the GPU idles 96 % of the time.

> **Scale-out (measured 2026-11-19).** The crforge 0.33 matches/s is **per
> process**, not per host. Multiproc is the largest immediate $/match lever, but
> the forward is 24 % of the packet, so 8 processes ask for ~194 % of one A6000:
> they land near **1.3–1.6/s** without MPS + a batched decision server and
> **2.0–2.6/s** with one. That is **$46–92 per 1M matches** against $295 today.
> The engine-only 2.8/s is per process too. Order: **multiproc → wire C3-B →
> de-Python the 228 ms → graphs/state → more boxes at ~1 A6000 per 8–16 vCPU**.
> Units and the full table: `ROADMAP_CRFORGE.md` §1 and §4.
>
> **The batched decision server is priced on the card (2026-11-18,
> `FINDINGS.md` §3.82–§3.84).** One A6000 serving four collectors through one
> forward is **1.38×** the four-per-process forward arm at 32 lanes per producer
> (736.7 vs 534.4 lane-decisions/s) and **1.16×** at six producers on six vCPU
> (4 lanes), but **0.68×** at four producers/4 lanes and **0.62×** at 16 lanes:
> an 8-row forward is launch-bound, so the batch only pays once a producer's own
> batch is off that floor. The server also trains now (weight push at each
> segment boundary, verified by digest). Plan the box as **one server + 4–6
> collectors at ≥32 lanes each**, or add collectors past the host's cores; MPS
> is the remaining step for >1 process per core.

Rent only when a local segment completes in <10 min; do not spend GPU hours on
CPU-bound prep. Training throughput notes and the portable `ever_*` techniques
are in `HANDOFF.md` → "Adopt from the field".

Rules:

- The strongest available generalist is the initialization, not a frozen mirror
  opponent.
- The **learner** deck is fixed (X-Bow 3.0); deck diversity comes from the
  **opponent** pool + league, not from a fixed X-Bow mirror.
- Evaluate on paired seeds, both seats, with fixed decks and levels.
- Keep rejected actions at zero; a rollout with rejections is invalid.
- Do not call self-play actions “expert” for IL.
- Promote only on native-engine gates; simulator wins are not evidence.
- **Do not rank a training cadence on the crforge return.** Observed reward is
  0.2–0.8 % of a typical update's return at 24 decisions, and 60→120-decision
  windows are flat (2.5 %); the curve is a boundary value fit, not a score
  (`docs/native/16.635.1/FINDINGS.md` §3.67). Rank on native matches, or on a
  simulator statistic that observes the game (terminal/crown/tower rate).

**Gate:** N4.

---

## 8. Workstream E — deployment to Null's

- The A51 runs the 16.635.1 client with the probe; the policy service supplies
  actions over the control protocol.
- **Promotion chain:** crforge bulk → native gate (≥150 paired games) → short
  native fine-tune/adaptation → live canary → ladder. Never skip the native step.
- Start with offline/self-play matches, then a live canary on a low-stakes
  account: 20 clean games before any push, then 50–100 monitored games.
- Monitor rejected actions, illegal placements, crashes and server responses.
  Log receipts, no blind retry.
- Automated ladder play may violate the server's rules; confirm what is allowed
  before a live account is used. **Official Supercell servers are out of scope** —
  different client/version/protocol, and a legal/ToS question, not a milestone.

**Gate:** N5.

---

## 9. Workstream F — cloud and scale

**The GPU is not the bottleneck on the bulk path, and it is not the unit to
optimise.** The unit is **matches/s per dollar at a gateable policy**. On the
native path the CPU data path starves the card (4 %); on crforge a vectorized
encoder lets one A6000 become the limiter (~51 matches/s).

- **ThunderCompute RTX A6000 48 GB — $0.35/h, 6 vCPU.** The right box for C4/C5
  and promotion runs. 12.7M/17.5M params fit in <2 GB; measured peak 6.33 GiB.
  Rent only after C1; the card was 4 % utilised in the 2026-11-14 smoke.
- **L40 ($0.79/h) and A100 80 GB ($1.09/h) buy nothing today.** Revisit only when
  the adapter sustains >60k decisions/s (≈51 matches/s) per card, i.e. when the
  GPU is genuinely the limit.
- **A "$0.00" second GPU is not a quote.** Verify the hourly estimate before
  starting a run; until then assume it double-bills.
- **GCP $300 / Azure $100:** useful for a high-vCPU host with a GPU or an Android
  cluster; new accounts often have **zero GPU quota**. Check before planning.
- **Do not spend before C1.** C0–C3 are CPU-only and free. The first useful spend
  is one short GPU session to validate the pipelined CUDA path at C4.

**Cost per 1M matches** (6,068-tick convention, $0.35/h GPU):

| path | matches/s | wall clock | GPU $ |
|---|---:|---:|---:|
| today's loop | 0.045 | 257 d | $2,161 |
| native, 6 processes on 6 vCPU | 0.49 | 24 d | $198 |
| crforge, straight port | ~1.0 | 278 h | $97 |
| crforge, vectorized | ~20 | 14 h | $4.9 |
| crforge, GPU-saturated | ~51 | 5.6 h | $1.9 |

Provisioning: **~1 A6000 per 8–16 vCPU** with a vectorized encoder (vs 100+ cores
per card on the native data path). Scale by adding identical boxes
(~4.3M matches/day each, ~$8/day), not bigger GPUs.

The A51 **can** feed training at a coarse cadence (12.3 matches/s at 600 ticks ×
4 engines × 8 lanes, §3.30), but at the PPO 5-tick cadence it is 0.26 matches/s and
its learner loop 0.04. It is the parity/deploy rig, not the throughput path.
Scale options in leverage order:

1. **crforge `VectorBattle` + vectorized `ObservationV1`** — highest throughput;
   bulk backend, never the gate. This is the plan of record.
2. **x86_64 16.635.1 native engine** — the gate, the fallback, and the slow-path
   trainer if crforge stalls. 0.61 raw / 0.082 training floor per process.
3. Add more A51-class ARM64 devices — cheapest per *deployable* engine.
4. Cloud x86 host with KVM + ARM64 Android image/AVD cluster (complex; verify
   nested virtualization and throughput first).

### 9.1 Adopt from the field

Take artifacts where licensed and version-matched; take **methods** where not.
Full table with effort and step mapping: `HANDOFF.md` → "Adopt from the field".

| Gap | Source | Take | License | Plan role |
| --- | --- | --- | --- | --- |
| 16.635 catalogs, model config | RoyaleFish `data_16635/`, `native_runner_16635.diff` | data + patch | Apache-2.0 | **done** — adopted as `native_runner/data/competitive-16.635/` |
| x86_64 16.635.1 engine | RoyaleFish `probe_x86_64_16635.diff` + Clashback `game/engine/libg.so` + `vendor/crx` | probe port + ELF loader (game binary local-only) | Apache-2.0 / unknown | **the gate**; pin `c4721bd9…`, build `160635001` |
| Missing anchors | RoyaleFish `logical_*.inc` | second relocation table | Apache-2.0 | **done** — 0 AMBIGUOUS (§3.33) |
| 16.635 baselines | `B-general-v275`, `A-hog26-v738` | eval opponents + starting point | Apache-2.0 | **the league seed and the gate's reference opponents** |
| Training throughput | RoyaleFish `ever_server/collector/learner/packed/loader` | inference: fp16+action equality, multi-size CUDA graphs, LSTM state table, layout/pack once; training: sparse + row-packed updates, loader | Apache-2.0 | **C4 deliverable** — forward port in-process; wire protocol discarded; CPU half is a separate gate |
| Bulk backend | crforge `python/` | `VectorBattle` (needs V4 adapter) | Apache-2.0 | **C0–C4, plan of record** |
| Snapshots / lookahead | Clashback `neo_replay/native_snapshots.py` | per-tick encode/load for search | unknown → local use only | optional; parity/replay lab only |
| League / specialist recipe | daybreak-cr | PFSP, focus/hard decks, IL anchor, 55% gate, rollback loop | unknown → reimplement | after C4 |
| X-Bow 3.0 | FirstLight-Zero | self-play recipe + eval protocol; the shipped checkpoints are **1.17M, mirror-only, different architecture** (15.535.13) — a reference/opponent, not a V4 init; retrain the V4 specialist on 16.635 | Apache-2.0 | reference; reuse recipe, not weights |
| Hog 2.6 replay set (Nov 2026) | RoyaleFish → Hugging Face (announced 2026-11-15) | **Cancelled 2026-11-16:** RoyaleAPI closed replay access; the collector stopped at a few hundred games and the upload is postponed with no date | unavailable | do not plan the Hog IL anchor or meta snapshot on it |
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
- **Native as the primary bulk trainer** — superseded 2026-11-15. The per-packet
  Python data path caps it at 0.082 matches/s and 4 % GPU; crforge is the bulk
  path. The native engine is retained as the gate, the parity oracle, the deploy
  rig and the fallback. Do not delete it; do not idle it for months.
- **Official Supercell servers** — out of scope, not deferred. Different client,
  version and protocol; a legal/ToS question, not an engineering milestone.

---

## 12. Risks and unknowns

1. **Adapter ROI.** The 10–30× is conditional on a vectorized observation/
   tensorizer. C1 measures it in a day; a per-lane port is only ~1.4×. Stop at C1
   if the gate misses twice.
2. **Simulator drift.** crforge is a reimplementation. The core is held
   tick-for-tick to recorded battles, but the **adapter layer** (observations,
   masks, refusals, deck/form setup) is ours. Continuous native parity (C5) is the
   mitigation; end-only validation is not.
3. **Legality leak.** One open reject (MiniPekka `26000018`) at the 2026-11-15
   handoff. Training before C0 teaches the policy a wrong mask.
4. **Policy exploits the simulator.** Hasty-CR: 93 % against its own engine and a
   human match found a bug 30M steps missed. Gate on native only; keep
   anti-overfit probes.
5. **Null's-specific content.** General/IL_Replay are official-game 16.x data;
   Null's may have custom balance/cards. Verify the fingerprint and card
   compatibility; use the 16.635.8 tables.
6. **Ladder rules.** Automated play may be prohibited; confirm before a live
   account is used. Official servers are out of scope.
7. **Cloud quotas.** GCP/Azure GPU quota may be zero on new accounts; do not
   assume credits are spendable on GPUs. The bulk path is CPU-heavy, so CPU
   credits are more useful than GPU credits before C4.
8. **Throughput measurement noise.** This host's absolute numbers move ~2× with
   load (the 2026-11-15 bench ran under load ~11). Use paired runs and record the
   load; treat laptop numbers as floors.
9. **Gate completeness (N2).** Rich telemetry and two missing anchors are still
   open on 16.635.1. The gate works for normal terminals and observation parity,
   but a gate with a known telemetry gap is a weaker truth source than it looks;
   keep the two missing rows and the render-gate crash on the list.

---

## 13. QA harness

- `illegal: 0`, zero rejected actions per accepted rollout.
- Paired-seed, both-seat evaluation; fixed decks/levels.
- 100-match soak after any probe change.
- **crforge:** `illegal: 0`, `truncated: 0`, no hangs, 20 games nightly; golden
  trace hash diff on every adapter change; 50–150 native matches per parity
  checkpoint (C5). A reject invalidates the rollout.
- **Encoder gate:** record decisions/s and ms/lane for per-lane and vectorized
  builds; no scale run without the C1 number in the artifact.
- Replay review for same-tile spam, passive openings, wasted spells, bad
  Tesla/X-Bow defense.
- Live canary: 20 clean games, then 50–100 monitored; observation-vs-screen diff,
  no crashes.
- Record checkpoint SHA-256, dataset/tag selection, deck pool, seed, config,
  crforge table lock and adapter commit with every run.

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
