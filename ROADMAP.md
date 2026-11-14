# Roadmap: X-Bow 3.0 on the CR Native Linux runtime

Status: **active — single track**, revised 2026-11-13
Owner: Raj
Engine: **cr-native-linux-bionic** — original x86_64 `libg.so` in a plain Linux
process (Android 31 Bionic userland, no AVD, no QEMU, no KVM)
Training stack: **cr-native-sandbox** — `NativeRoyaleEnv`, self-play, PPO
Target: **15.535.29 / 150535029, x86_64**
End goal: **Ultimate Champion with X-Bow 3.0**

**Single source of truth.** This file replaced the multi-track plan. crforge is
the ultrafast simulator/CI reference in [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md).
Runtime details and exact install commands live in
[`docs/native/LINUX_BIONIC_RUNTIME.md`](docs/native/LINUX_BIONIC_RUNTIME.md).

---

## 0. Decision

**One path: cr-native on x86_64 Linux.**

- `cr-native-linux-bionic` is the engine host: Android 31 userland + the
  original x86_64 `libg.so`, up to 48 isolated workers, TCP `39031 + slot`.
- `cr-native-sandbox` is the training stack: `NativeRoyaleEnv`, card catalog,
  self-play, PPO, evaluation.
- No Android emulator, AVD, MuMu, KVM, or custom loader is on the critical path.
- **Retired from the plan:** Linux AVD, phone/engine-cluster throughput work,
  the custom ARM64 `libg` loader, and the 16.635.1 probe as the trainer.
- The A51 / 16.635.1 probe stays as **parity and live-play reference only**.
- **crforge** stays as an ultrafast simulator and CI/differential oracle; it is
  not the primary trainer.

**Why this one:** it is the build we were about to write (Android userland +
original engine on Linux) and it already exists, is version-pinned, has a
worker lifecycle, and ships a native training API. It removes weeks of loader and
Bionic-shim work.

---

## 1. Success milestones

| Milestone | Definition | Gate |
| --- | --- | --- |
| **M0 Runtime** | Dedicated VM; bundle verified; `doctor` hard-pass; 4 workers; `smoke` passes | 1 worker completes a match; 1 h with no crash |
| **M1 API + deck** | `NativeRoyaleEnv` reset/step/observe/act; X-Bow deck configured; 20 matches | 0 errors; deterministic reset; observations sane |
| **M2 Throughput** | Engine-only and end-to-end rates measured from 4 → 8 → 12 workers | ≥5 matches/s engine-only first; then ≥10 |
| **M3 Training** | Self-play/PPO run on the X-Bow deck; checkpoints promote | Learned bot beats scripted X-Bow over 200 games |
| **M4 Parity + live** | 15.535.29 vs 16.635.93 parity report; A51 live canary | 20 clean live games, 0 illegal taps |
| **M5 Scale** | 24 h unattended soak at target throughput | No crash, no leak, restart recovery |

**Throughput target:** 10 matches/s **engine-only** is the first real target.
The published sandbox loop with observations + a 4.8M model, 4 matches/process,
measured **0.84–0.93 matches/s** on 12–24 processes (25-core host). Treat the
full training loop as a separate optimization problem, not a given.

---

## 2. Architecture

```
cr-native-linux-bionic
  Android 31 userland: linker64 -> dalvikvm64 -Xint -> binderless JniHost
  -> libnative_host_bridge.so -> original x86_64 libg.so (15.535.29)
  -> 48 workers, JSON-line TCP 39031 + slot
                    │
                    ▼
cr-native-sandbox
  NativeRoyaleEnv: reset / observe / act / step
  card catalog + deck config + self-play + PPO + evaluation
                    │
                    ▼
X-Bow 3.0 policy -> checkpoints -> evaluation -> live canary

A51 / 16.635.1 probe  -> parity + live reference only
crforge               -> ultrafast simulator / CI oracle (reference only)
```

---

## 3. Runtime facts

| Item | Value |
| --- | --- |
| Game version | **15.535.29** (`150535029`) |
| ABI | **x86_64** |
| `libg.so` | `fa6704b8…46ba` |
| Workers | up to 48, ports `39031 + slot` |
| Linux runtime bundle | 273,274,290 B, SHA-256 `e25f6484…30db0` |
| Sandbox runtime bundle | 899,751,022 B, SHA-256 `8d829f21…bc28` |
| Root / KVM | root **yes**, KVM **no** |

The runtime is version-locked and fails closed on hash mismatch. It is not
compatible with 16.635.1 or the 160402002 ARM64 build.

---

## 4. Phases

### Phase 0 — Bring up the runtime (2–3 days)

- [x] Run `tools/native_bringup/preflight_bundle.py` on the bundle; `doctor`
      alone does not prove startability (see
      [`docs/native/PHASE0_BRINGUP.md`](docs/native/PHASE0_BRINGUP.md)).
- [x] Build the local overlay with `tools/native_bringup/build_local_overlay.py`
      (the published bundle is not startable).
- [x] Either a **dedicated Ubuntu 22.04/24.04 x86_64 VM/container** with root,
      or the **rootless user-namespace path** on the daily driver
      (`--system-root` + `tools/native_bringup/run_rootless_worker.sh`). Never
      install as root on the daily driver.
- [x] Download and SHA-256-verify the runtime bundle (273 MB) and the sandbox
      runtime (900 MB).
- [x] `verify-bundle` → `install --workers 4 --apply` → `doctor`
      (`hard_pass=true`).
- [x] 4 workers ping; one worker completed a full match (tick 6147).
- [x] Measure worker throughput at 4 / 8 / 12 workers and record the knee.
      *(Active-worker sweep, `decision_ticks=1` (RPC-bound decision loop),
      2026-11-14: **4 → 300.5**, **8 → 486.1**, **12 → 474.0 ticks/s**
      aggregate; p50 per worker 75.6 / 60.0 / 39.0. Each tier `complete`, no
      `safety_reasons`, 100 % action acceptance, 0 unexpected rejections.
      `recommended_workers: 8`; 12 is the oversubscription point. Engine-only
      (100-tick no-policy steps) remains 8.3k ticks/s one worker / 32.6k at 4
      from the earlier session — different workload, do not compare directly.)*
- [x] 1 h soak; record CPU/RSS and the knee.
      *(PASS 2026-11-14: `builds/native/phase0-20261114T000821Z/`, 60
      samples, `ping_failures: 0`; RSS flat 324.6–327.3 MB per worker, CPU
      mean 0.06 %, ping mean ≈0.85 ms. The first sweep attempt hit a
      one-frame terminal-lag bug (fixed in the sandbox sweep script); the
      4/8/12 rerun supplies the knee.)*

**Gate:** `doctor` hard-pass, 4 workers healthy, one worker completes a match,
1 h with no crash.

### Phase 1 — Native API, X-Bow deck, determinism (3–5 days)

- [x] Install `cr-native-sandbox`; connect `NativeRoyaleEnv` to the workers.
- [x] Configure the X-Bow deck: X-Bow, Hero Knight, Evo Archers, Evo Tesla,
      Skeletons, Electro Spirit, Fireball, The Log. Record forms and levels.
- [x] Exercise `reset` / `observe` / `act` / `step`; 20 matches.
- [x] Same seed + same actions → same observations (determinism).
- [x] Validate observations against A51/16.635.1 goldens: card IDs, HP, elixir,
      tick, hand, abilities. Note 15.535.29 vs 16.635.1 differences.
      *(Level-11 parity: tower HP 4824/3052, deck ids/forms/costs and starting
      elixir match. Unit max HP matches for X-Bow 1600, Knight/Hero Knight
      1766, Archers/Evo Archers 304, Tesla/Evo Tesla 1182, Skeletons 81.
      **Electro Spirit differs: A51 16.635.1 = 215 vs native 15.535.29 = 217**
      (real balance delta; L10=195, L12=236 on A51). Hero Knight ability
      matches exactly: state 2/charges 1 -> state 6/charges 0, 2 elixir, second
      press refused. Evidence:
      `builds/native/parity-unit-hp-15.535.29-vs-16.635.1-L11.json`.)*
- [x] Decide the model: sandbox R0 elite-v3 (v1450 stays a benchmark).

**Gate:** 20 matches, 0 errors, X-Bow deck plays legally, deterministic reset.

### Phase 2 — Throughput (1 week)

- [x] Scale 4 → 8 → 12 workers; record engine-only and end-to-end rates.
      *(2026-11-14 active-worker sweep: 300.5 → 486.1 → 474.0 ticks/s
      aggregate at `decision_ticks=1`; per-worker p50 75.6 → 60.0 → 39.0.
      The knee is **8 workers** (`recommended_workers: 8`). The sweep's
      ~4.9k–7.1k-tick matches (bootstrap replay) put this decision-loop rate
      at ≈0.04 → 0.07 → 0.07 matches/s; it is RPC-bound, not engine-bound.)*
- [x] Watch CPU, per-process RSS, and the knee where adding workers stops
      helping. *(RSS flat in the soak; aggregate throughput falls at 12.)*
- [x] If engine-only clears 10 matches/s, lock the host size.
      *(2026-11-14 Phase 2 profile: fast-json + 100 ticks/RPC gives **36.4k
      ticks/s at 8 workers** (≈9.6 matches/s) and **40.2k at 12** (≈10.6).
      The 10 matches/s engine-only gate is met at 12 and is within 4 % at 8.
      Lock **8 workers for training**; 12 adds only ≈10 % engine-only and no
      RPC throughput.)*
- [x] If the full observation/policy loop is below target, profile the
      observation path before buying more hardware.
      *(`tools/native_bringup/profile_native_loop.py`; results in
      [`docs/native/PHASE2_BOTTLENECK.md`](docs/native/PHASE2_BOTTLENECK.md).
      At one tick/RPC the loop is **0.074 matches/s at 8 workers**, attributed
      48 % inference, 40 % transition wait, 10 % Python observation/mask. The
      worker spends ~90 % of its transition on JSON, not the engine.
      `fast-json` is 1.5–2.1× faster; a 4/8-tick decision interval lifts the
      same loop to **0.277 / 0.522 matches/s** with the real collector.)*
- [x] Compare with the published sandbox table (0.84–0.93 matches/s at 12–24
      processes with 4 matches/process).
      *(Published run used a 25-core host + RTX 5090. On this CPU-only laptop
      the 8-worker loop is 0.074 matches/s at one tick/RPC and 0.522 at an
      8-tick interval. The published number is therefore not a CPU baseline;
      it also confirms inference, not the engine, is the remaining gap.)*

**Gate:** ≥10 matches/s engine-only on the chosen host, or a written bottleneck
plan with a measured next step. **Both are now satisfied:** 10.6 matches/s
engine-only at 12 workers (9.6 at 8), and the plan in
[`docs/native/PHASE2_BOTTLENECK.md`](docs/native/PHASE2_BOTTLENECK.md) with a
measured next step (fast-json + decision interval 4–8; R0 inference needs a
GPU).

### Phase 3 — Training (2–3 weeks)

**R0 track — prerequisites before the long run:**

- [x] **Adopt the canonical upstream R0 tree** — vendored
      `.resource/r0-elite-training/training_r0` (MIT, 36,854,175 params, schema
      `825408c3…4d44`) into the sandbox and retired the old copy (36,853,407 /
      `07c183d2…7426`). The synthetic smoke, the regenerated model smoke and
      the CPU/synthetic batched-trainer equivalence check pass; tests are
      updated. See [`docs/native/R0_CANONICAL_TREE.md`](docs/native/R0_CANONICAL_TREE.md).
      Do not mix the two contracts.
- [ ] **Verify the R0 checkpoint.** A usable file is either a training
      checkpoint (`kind: r0-elite-training-checkpoint.v1`, with `model_config`,
      `model`, `optimizer`, `step`) or a policy checkpoint with the elite-v3
      `state_dict`; its `_contract` buffer must match the canonical upstream
      `r0-elite-relation-spatial-lstm.v3` config, `CardVocabulary`, semantic
      catalog and observation schema. Record the SHA-256, parameter count,
      step/epoch, deck, level and build. No checkpoint exists locally or in the
      upstream repo.
- [ ] **Provision the GPU host.** CUDA-enabled torch (the current `.venv` is
      CPU-only), 24 GB VRAM preferred, on the same host/low-latency network as
      the 8 rootless workers. Record the forward-pass and PPO-update timings.
- [ ] **Build the native R0 adapter.** `observe_train_v1` coherent frame →
      candidates/history/semantics → `PublicFrame` → `PublicBatch`; run one
      scripted X-Bow match end-to-end before PPO.
- [ ] **R0 smoke:** one forward pass, one PPO update, checkpoint save/restore,
      and one full match at decision interval 5 (`DECISION_TICKS = 5`).
- [x] **Rootless port plumbing:** `--worker-ports` on train/evaluate/v2 with
      `--skip-worker-start`; validated on two bwrap workers.

**If data, the checkpoint or the GPU is not available,** run the CPU-feasible
fallback (sandbox `RecurrentPolicyValueNet`, 8 workers, interval 4–8) to
stabilise the league/evaluation machinery; do not start a long R0 run on CPU.

- [ ] Self-play mirror on the X-Bow deck first; stabilize the deck plan.
- [ ] Then an opponent pool: scripted archetypes, past snapshots, crforge
      baselines, and any compatible checkpoints.
- [ ] PFSP/league selection; reward = result ± tower-HP potential, elixir
      overflow, own-King activation, illegal placement. No card-specific
      X-Bow shaping.
- [ ] Monitor entropy, KL, clip fraction, explained variance, game length,
      actions/game.
- [ ] Evaluate: 200 games vs scripted X-Bow, past snapshots, and the opponent
      pool; fixed seeds.
- [ ] Replay review for same-tile spam, passive openings, wasted spells, bad
      Tesla/X-Bow defense.

**Gate:** learned bot beats scripted X-Bow over 200 games; no degenerate loops;
`illegal: 0`.

### Phase 4 — Parity + live canary (1–2 weeks)

- [ ] Write the 15.535.29 → 16.635.93 parity report: cards, forms, balance,
      tower troops, heroes, abilities, content version.
- [ ] Use the A51/16.635.1 probe for live parity frames and HP/elixir checks.
- [ ] If the gap is acceptable, run 20 live games on Null's 16.635.1 with the
      existing probe/touch path.
- [ ] Human review: are the decisions X-Bow-competent?

**Gate:** 20 clean live games, 0 illegal taps, reconnect works. No main-ladder
run before this gate.

### Phase 5 — Scale + soak (ongoing)

- [ ] 24 h unattended run at target throughput.
- [ ] Restart recovery, worker replacement, log/monitor, disk cleanup.
- [ ] Freeze the reproducible artifact set: commit, bundle hashes, deck, levels,
      seeds, checkpoint hashes.

---

## 5. Training strategy

- **Deck:** fixed X-Bow 3.0, exactly FirstLight-Zero's eight cards. Verify every
  card/form is in the sandbox catalog and set slot flags explicitly.
- **Data:** self-play first, then opponent-pool PPO. `IL_Replay` stays retired
  as a training corpus; keep IL/General/Everlight checkpoints as opponents and
  baselines.
- **Model:** start with the sandbox's R0 elite-v3 stack. FirstLight-Zero v1450
  is a benchmark/opponent; do not blindly port weights across versions.
- **Levels:** v1450 was trained at level 11; the live account is maxed. Train
  and evaluate at one declared level setting and record it in every artifact.
- **Reward:** ±1 result, tower-HP potential, elixir-overflow penalty, own-King
  activation penalty, illegal-placement penalty. No card-specific shaping.
- **Evaluation:** per-archetype matrix, fixed seeds; never use mirror win rate
  as ladder evidence.

### 5.1 R0 is a model stack, not the engine

| Thing | What it is |
| --- | --- |
| **Engine** | `cr-native-linux-bionic`: Android 31 userland + the original x86_64 `libg.so` **15.535.29**, driven over JSON-line TCP by `NativeRoyaleEnv` |
| **R0 elite-v3** | The policy/value network in `.resource/cr-native-sandbox/training_r0/` (`R0Policy`), not an engine and not FirstLight |
| **FirstLight-Zero** | A different 1.17M self-play model/stack (15.535.13), kept as a benchmark/opponent |
| **cr-native-sandbox** | The Python training stack (`training/` recurrent PPO, `training_r0/` R0, `NativeRoyaleEnv`) |

R0 elite-v3 details:

- model schema `r0-elite-relation-spatial-lstm.v3`; observation schema
  `r0-public-observation.elite-v3`;
- default `ModelConfig`: width 384, 6 layers, 12 heads, hidden 768, spatial
  channels 128 → **36,854,175 parameters** in the canonical upstream
  `r0-elite-training` tree (adds `attack_locked` and cross-table `Base`
  resolution vs the obsolete sandbox copy);
- 20 Hz native clock, **decision every 5 ticks** (`DECISION_TICKS = 5`);
- consumes `PublicBatch` built by `training_r0/observation.py`, which needs a
  coherent `observe_train_v1` frame plus candidates, event history and public
  semantics; it does **not** speak the engine protocol directly;
- the model contract enforces an exact `_contract` hash over model config,
  vocabulary, semantic catalog and observation schema. It **cannot** load
  FirstLight weights or old 177M expert checkpoints.

**Canonical R0 tree — adopted 2026-11-14:** `.resource/r0-elite-training`
(IMAX9D, **MIT**) is the R0 source of truth and its `training_r0` +
R0 `data_pipeline` pieces are vendored into the sandbox. The old sandbox copy
(36,853,407 / `07c183d2…7426`) is retired. Adoption evidence:
[`docs/native/R0_CANONICAL_TREE.md`](docs/native/R0_CANONICAL_TREE.md).

Upstream facts:

- **36,854,175 parameters**, observation schema `825408c3…4d44`; the old
  sandbox copy is 36,853,407 / `07c183d2…7426` and is not interchangeable.
- **No weights and no data** in the repo. Training needs a
  `compiled-prod-v17` corpus (~772 GB, or a 3 GiB batch for B=1024) from the
  project's Baidu pan token, or data produced by `data_pipeline/r0_produce`
  from a game-side capturer. Data/token/build questions: issue
  <https://github.com/IMAX9D/r0-elite-training/issues/1> — assume no response.
- It is **IL (`forced=expert_actions`)**, not PPO. A native R0 PPO/rollout
  adapter is still ours to build.
- Needs Python 3.12 + **torch 2.8+cu128** + CUDA 12.8 (12.5 GiB VRAM at
  B=256, 23.7 GiB at B=512).

**Integration status (2026-11-14):** upstream `training_r0` and the R0
`data_pipeline` pieces are vendored into the sandbox; the synthetic smoke
reports 36,854,175 / `825408c3…4d44`, `smoke_r0_model.py` and its checkpoint are
regenerated, and the CPU/synthetic batched-equivalence check passes. The
Linux-specific `data_pipeline` path/env handling was preserved (the upstream
`--downloader-root` Windows default was not). Next: the data/checkpoint gate,
a GPU host, and the native live adapter.

**Rootless worker ports (2026-11-14):** `training/train.py`,
`training/evaluate.py` and `selfplay_v2/train.py` now accept
`--worker-ports 39031-39038` together with `--skip-worker-start`, so a run can
address the bwrap workers directly without AVD topology. Validated with two
workers: a 128-tick smoke iteration and a full paired-seed evaluation
(`normal_terminal_rate: 1.0`, `passed_integrity: true`). The explicit port list
is recorded in the run manifest as `worker_ports` / `worker_source: explicit`.

`.resource/royaleapi-downloader` is an optional source-only RoyaleAPI ladder
replay downloader (no LICENSE file; live 16.x data, not a 15.535.29 training
corpus). It is useful for expert replay mining and analysis, not for producing
`compiled-prod-v17` without a capturer.

**GPU:** R0 is the intended Phase 3 stack but is not usable on this CPU
(125–269 ms per forward pass). Phase 3 needs a CUDA host (24 GB VRAM preferred)
and a trained checkpoint that matches the canonical contract.

---

## 6. QA harness

- **Invariants:** `illegal: 0`, no pending leaks, action mask matches engine
  refusals. 100 seeded random-legal games.
- **Golden trace:** seed + actions → per-tick observation hash; diff after
  every runtime/tool change.
- **Parity:** same script on 15.535.29 native vs A51/16.635.1; compare entity
  counts, tower HP, deaths, play ticks.
- **Regression bank:** 200 games vs scripted X-Bow, past snapshots, and the
  opponent pool.
- **Exploit probes:** wait-only, spell-only, same-tile spam, tower rush.
- **Lethal-spell test:** enemy King at 1 HP, Fireball in hand, enough elixir →
  must cast.
- **Live canary:** one game/day, screen-vs-reader diff, tap confirmation.

---

## 7. Host and safety

- **x86_64 Linux with root, no KVM.** Laptop VM for bring-up; cloud x86 or the
  student credits for 10+ matches/s.
- The installer writes **`/apex`, `/system`, `/linkerconfig`,
  `/dev/__properties__`** and worker dirs under `/data/local/tmp`. These are
  Android root paths, not Linux boot files. They do not touch `/boot`, the
  kernel, GRUB or partitions, so they cannot brick the firmware — but they are
  absolute paths written as root, so a bug or a wrong bundle can damage the OS.
  **Use a dedicated VM/container with a snapshot; never the daily-driver.**
- Uninstall by stopping workers and removing the created roots; keep a snapshot
  so a bad install is a revert, not a repair.
- Runtime/assets are user-supplied; MIT covers the deployment code only.

---

## 8. Gates and kill criteria

| Gate | Fail condition | Response |
| --- | --- | --- |
| Phase 0 | `doctor`/`smoke` fails | Fix the VM/bundle; do not write a custom loader yet |
| Phase 1 | X-Bow deck not representable, or observations mismatch materially | Fix the deck/catalog/API before training |
| Phase 2 | Engine-only < 5 matches/s at 8 workers | Profile per-worker cost and host; adjust worker count |
| Phase 2 | End-to-end < 1 match/s after profiling | Optimize observations or split training across hosts |
| Phase 3 | Cannot beat scripted X-Bow after a full run | Training loop is broken; do not go live |
| Phase 4 | Any illegal tap, crash or unrecoverable disconnect | No-go; fix and repeat |
| Global | Simulator win rate used as ladder evidence | Stop; require live/human review |

---

## 9. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Version lock: 15.535.29 ≠ live 16.635.93 | Balance/card differences | Parity report; train on 15.535, validate on 16.635; port later only if needed |
| End-to-end throughput below 1 match/s | Slow training | Profile observation/feature path; leaner features; more workers/cores |
| Root install pollutes the host | Hard to undo | Dedicated VM/container + snapshot; stop/remove cleanly |
| Sandbox API differs from `batch-v1` | Adapter work | Use `NativeRoyaleEnv`; bridge to FirstLight only if it measures a win |
| Launcher duplicate-start on a busy port | Duplicate processes | Do not re-run `start` on a busy port; verify with `ss`/`/proc/PID/cmdline` |
| 48 workers exceed RAM/disk | OOM/disk full | Start at 4; scale to the measured knee; monitor RSS |
| Deck/card not in sandbox catalog | X-Bow blocked | Verify the catalog early; add the deck config before training |
| R0 checkpoint is from another revision | Weights cannot load (`_contract` mismatch) | Verify config/catalog/schema hashes first; do not bypass the contract; retrain if incompatible |
| R0 has no native observation adapter | Cannot train or evaluate on real engine | Build `observe_train_v1` → `PublicFrame`/`PublicBatch`; smoke one scripted match first |
| No GPU on the training host | R0 is 20–90× too slow on CPU | CUDA host (24 GB preferred) on the same host as the workers, or use the CPU fallback |
| Live account risk | Ban | Canary/alt account first; main ladder after Phase 4 |

---

## 10. Do not do

- Do not build an AVD, MuMu or phone cluster for training.
- Do not write a custom `libg` loader unless this runtime fails a gate.
- Do not train on `IL_Replay`.
- Do not make crforge the critical path.
- Do not run real-time matches as the training loop.
- Do not blindly port FirstLight-Zero weights across versions.
- Do not confuse R0 with the engine: R0 is the `training_r0/` policy/value
  model; the engine is `libg.so` 15.535.29 in `cr-native-linux-bionic`.
- Do not treat the random `builds/native/r0-elite-v3-smoke.pt` as a trained
  checkpoint.
- Do not bypass the R0 `_contract` hash to load a foreign checkpoint.
- Do not use the obsolete sandbox `training_r0` contract for new data,
  checkpoints or runs; `.resource/r0-elite-training` is canonical
  (36,854,175 params). Do not mix the two trees.
- Do not import code or assets from `.resource/Clash-Royale-v1-Client-Leak`;
  it is leaked, unlicensed v1 (2016) decompiled client pseudocode, not the
  current engine and not the server.
- Do not start a long R0 run on CPU; use a CUDA host beside the workers.
- Do not install the runtime on the daily-driver Linux.
- Do not use simulator win rates as ladder evidence.
- Do not start the main ladder before Phase 4.

---

## 11. Key paths

| Path | Purpose |
| --- | --- |
| `docs/native/LINUX_BIONIC_RUNTIME.md` | Runtime evaluation, hashes, exact install/smoke commands |
| `ROADMAP_CRFORGE.md` | Ultrafast simulator / CI oracle (reference only) |
| `.resource/cr-native-linux-bionic/` | Runtime CLI source |
| `.resource/cr-native-sandbox/` | `NativeRoyaleEnv`, card catalog, self-play/PPO |
| `.resource/cr-native-sandbox/training_r0/` | R0 elite-v3 policy/value model, observation contract, IL/PPO kernels |
| `.resource/cr-native-sandbox/data_pipeline/r0_train.py` | R0 training-checkpoint writer (`r0-elite-training-checkpoint.v1`) |
| `.resource/r0-elite-training/` | **Canonical** MIT R0 Elite IL chain + batched trainer (36,854,175 params, no weights/data) |
| `.resource/royaleapi-downloader/` | Optional live-replay downloader (16.x; source only, no LICENSE file) |
| `builds/native/r0-elite-v3-smoke.pt` | **Random-weight, old-contract** R0 smoke checkpoint; invalid after the tree swap |
| `.resource/FirstLight-Zero/` | X-Bow recipe, PFSP/league code (Apache-2.0), v1450 checkpoints, benchmark |
| `.resource/native/16.635.1/reference/libg.so` | A51 parity reference |
| `docs/native/16.635.1/FINDINGS.md` | 16.635.1 evidence log (parity/live) |
| `tools/native_analysis/` | A51 probe-side tools (parity, not training) |
| `native_runner/` | Existing transport/QA utilities; adapt, don't depend |

---

## 12. Side resources (reference only)

- **`.resource/clash-royale-research`** — build **160402020** approximate
  reimplementation: deterministic 20 Hz TypeScript engine (70 cards, ~3,500×
  real time per core), browser viewer with original art/audio, and a paused
  self-play RL stack. Valuable as a **fast pretraining/sim-to-real sandbox**, a
  **demo viewer**, and a **mechanics/QA reference** (`docs/FIDELITY.md`,
  `docs/MECHANICS_BRIEF.md`, native trace tests). It is **not** the real engine,
  it is a different build, and it is not a Track B replacement. Use it only if a
  measured sim-to-real check justifies it.
- **`.resource/Clash-Royale-v1-Client-Leak`** — decompiled **v1 (2016) client**
  pseudocode: 984 `.cpp` class dumps, **no headers, data, assets or build
  files**, client major version 2.0. It is client-side only (no server or
  authoritative simulation), not 15.535.29, not buildable, and has no license.
  Useful only as a historical protocol/class-structure reference; never import
  its code or assets. The current `libg.so` + card catalog remain the truth
  source.
- **`crforge`** remains the CI/differential oracle. `clash-royale-research` is
  the faster approximate sandbox, not the fidelity oracle.

---

## 13. Next action

Phases 0–2 are complete as of 2026-11-14. Soak passed (60 samples, zero ping
failures, flat RSS), the one-tick sweep knee is **8 workers** (300.5 → 486.1 →
474.0 ticks/s), and the A51/16.635.1 parity check is recorded (all X-Bow unit
HP matches except Electro Spirit 215 vs 217; Hero Knight ability matches).

The Phase 2 bottleneck plan is measured and written:
[`docs/native/PHASE2_BOTTLENECK.md`](docs/native/PHASE2_BOTTLENECK.md).
Engine-only at 100 ticks/RPC is 9.6 matches/s at 8 workers and 10.6 at 12.
The one-tick policy loop is 0.074 matches/s at 8 workers (48 % inference,
40 % transition wait, 10 % Python observation), and the two measured levers
are `fast-json` (1.5–2.1×) and a 4–8-tick decision interval (0.277 / 0.522
matches/s). R0 elite-v3 takes 125–269 ms per CPU forward pass, so Phase 3
needs a GPU for R0 or the CPU-feasible sandbox recurrent policy.

The loop fix is implemented: `decision_ticks` in both collectors,
`--transition-mode fast-json` as the training default, and the flag plumbed
through `training/train.py`, `training/evaluate.py` and
`selfplay_v2/train.py`. Re-measured at 8 workers / interval 8: **0.522
matches/s**.

Next session, in order:

1. **Adopt the upstream R0 tree + data/checkpoint gate.** Vendor
   `.resource/r0-elite-training/training_r0` (MIT, 36,854,175 params, schema
   `825408c3…4d44`) into the sandbox, retire the old copy, re-run the smoke and
   verification, and update `smoke_r0_model.py`/tests. Then find/verify a
   trained checkpoint against that contract. The upstream repository is a
   **trainer, not a weight release**; it has no checkpoint and no data. Data
   questions were filed in
   <https://github.com/IMAX9D/r0-elite-training/issues/1> — assume no response
   and plan own 15.535.29 captures if R0 IL is required.
2. **GPU host.** CUDA torch, 24 GB VRAM preferred, workers on the same host;
   benchmark R0 forward and one PPO update.
3. **R0 native adapter.** `observe_train_v1` → `PublicBatch`; one scripted match,
   then the PPO smoke.
4. **League/eval layer** (no GPU required, can run in parallel): port the
   FirstLight-Zero PFSP/snapshot/paired-seat design (Apache-2.0) and add
   `--worker-ports` to train/evaluate.

See [`HANDOFF.md`](HANDOFF.md) for the checkpoint checklist and exact paths.
