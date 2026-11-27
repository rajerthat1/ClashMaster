# Roadmap: Hog 2.6 bot → Hall of Fame

Status: planning
Owner: Raj
Primary repo: this repository (fork of `FirstLight_CR`; upstream code lives at the repo root)
Target deck: Hog 2.6 — Hog Rider, Hero Musketeer, Evolved Cannon, Fireball, The Log, Evolved Skeletons, Ice Golem, Ice Spirit

---

## 0. Read this before writing any code

**FirstLight_CR is the right base.** It is the only repo in `.resource/` that ships a complete
imitation-learning + PPO pipeline, five trained checkpoints, a full 252k-match replay dataset, and a
credible Hall of Fame result with the exact single deck you want (Hog 2.6). Everything else is either
a simulator (`RoyaleSim`, `ClashAI`, `clash-royale-simulator`), a fork/derivative (`FirstLight-Zero`,
`FL-Royale`), or a tool for the live client (`cr-memory-reader`, `daybreak-cr`).

**But "Hall of Fame" needs a precise definition.** From the repo's own docs:

- The HoF claim is **author-reported on an account** using the Hog specialist. It is not reproduced by
  any script in the repo.
- The repo trains and evaluates **offline**. A grep of the whole repo finds no ranked/ladder/online
  play loop — no trophies, no matchmaking against real players, no account handling.
- The engine is a patched copy of **Null's Royale 15.535.13** (a private-server client), isolated from
  the network. The original APK is *not* included and must be supplied by you.
- Official Supercell ranked ladder is **out of scope** for this stack: it needs a level-15 account,
  real-time client control, and it violates Supercell's ToS (ban risk). Do not plan around it.

**Assumption for this roadmap:** the target is the Null's Royale / private-server ladder, where the
author's HoF result happened, and the deliverable is a bot that can play ranked matches on that
ladder. Phase 6 treats the missing deployment bridge as explicit R&D. If you actually meant official
Supercell HoF, stop and re-plan — this repo cannot get you there.

**The other hard truth:** the HF dataset alone is not enough to start training. The cache builder
replays every match through the native engine to produce observations and expert actions. So the
engine bootstrap (Phase 2) is a hard prerequisite for "HF dataset training", not an optional step.

---

## 1. What you already have

| Asset | State |
| --- | --- |
| Repository | Fork of `FirstLight_CR` at the repo root (upstream code + our additions) |
| Checkpoints | 5 models present (~48 MB each): IL, Active IL, General, Hog specialist 1, Hog specialist 2 |
| IL_Replay dataset | Complete: 52 parquet + 52 action shards, 252,238 matches, 17.8M actions, ~1.8 GB |
| Training code | IL cache builder, IL trainer, PPO cluster trainer, evaluator, policy service |
| Machine | Linux, 12 vCPU, 14 GB RAM, **no CUDA GPU**, 90 GB free, `/dev/kvm`, `adb` |
| Python | System 3.15 only; repo wants **3.12** |
| `.venv-hf` | HuggingFace CLI venv only (no torch) — not the project env |
| Missing | Android SDK/emulator, Null's Royale APK, Windows/MuMu host, ladder deployment code |

---

## 2. Phase plan

### Phase 0 — Pin the target (half a day)

Decide and write down, in this file:

- [ ] Ladder: Null's Royale account (recommended / only supported path) vs official (re-plan).
- [ ] Success metric: e.g. "top 1000 on Null's ladder" or "HoF trophy threshold", plus a date.
- [ ] Account, card levels, and evolution ownership on that account.
- [ ] Baseline checkpoint: **Hog specialist 2** for strength, specialist 1 for proactive play.
- [ ] Budget cap for cloud GPU + engine host.

**Exit:** target + metric + budget written above, no ambiguity about which ladder.

---

### Phase 1 — Local Python environment (half a day, no GPU needed)

```bash
cd /home/raj/Desktop/ClashMaster
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
uv venv --python 3.12 .venv
# CPU-only torch keeps the download sane on this machine
uv pip install --python .venv/bin/python torch==2.5.1 \
  --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python tools/check_install.py      # expect: Ready: 5 models
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest native_runner/tests -q
cp .env.example .env
```

- [ ] `check_install.py` prints `Ready: 5 models`.
- [ ] Tests pass or skip only for missing CUDA/native prerequisites.
- [ ] `.env` created (engine fields stay blank for now).

**Exit:** all five checkpoints load and hashes verify on CPU. This proves the model/data half works
before spending any time on Android.

---

### Phase 2 — Get a native engine running (1–2 weeks, biggest risk)

You need an Android 12 ARM64-capable guest, the original Null's Royale APK, an offline APK build,
and a control channel. Choose one host:

| Option | Pros | Cons |
| --- | --- | --- |
| Windows + MuMu (author's path) | Documented, supported, `build_offline_engine.ps1` works as-is | Needs a Windows machine with virtualization; not this Linux box |
| Linux + Android emulator + prepared AVD | Repo documents it (`docs/training.md` §6) | Needs an **ARM64-capable** system image on x86; stock x86 images cannot run the ARM64 libs |
| Rooted physical ARM64 Android device | Cheap, real ARM, adb works | Needs root adbd, manual APK install, slower I/O |
| Cloud Windows GPU VM | One-stop for build + run | Expensive per hour; nested virtualization may be blocked |

Steps:

1. Obtain `nulls-royale.apk` 15.535.13 arm64-v8a (user-supplied; the repo does not ship it). Confirm
   the content update 15.535.86 after first launch.
2. Install JDK 17 + Android NDK 27.3 + build-tools 37 on whichever host builds the APK.
3. Build + install the offline engine:
   ```powershell
   ./check_original_game.ps1 -InputApk ./user-provided/nulls-royale.apk
   ./build_offline_engine.ps1 -InputApk ./user-provided/nulls-royale.apk -OutputApk ./build/cr-ai-offline.apk
   ./install_offline_engine.ps1 -Apk ./build/cr-ai-offline.apk
   ```
4. On Linux, prepare the guest per `docs/training.md` §6 (`prepare_training_guest.py`,
   `configure_emulator_engine_guests.py`, then convert userdata to the prepared initdata image).
5. Smoke test the whole loop:
   ```bash
   python tools/make_demo_data.py                 # synthetic replay + dataset
   python -m native_runner.training.v4.cache_builder runs/demo/dataset runs/demo/cache \
     --ports 26789 --source-count 1 --resident-slots 1 --verify-roundtrip
   python -m native_runner.training.v4.serve_policy \
     --checkpoint ./checkpoints/General/checkpoint-step-00000460.pt
   ```

- [ ] One manual vs model match runs end-to-end.
- [ ] Demo replay reconstructs with `--verify-roundtrip`.
- [ ] Evaluator produces `summary.json` with `truncated: 0` and `rejected_actions: 0`.

**Exit:** a single engine process accepts commands and completes a match. Do not proceed until this
is true. Everything downstream is engine-bound.

**Fallback if the engine cannot be bootstrapped:** stop the HoF plan. Do not try to "just train on
parquet" — the observation contract comes from the native engine, and a hand-built parquet-only
pipeline would not match the checkpoints. The realistic alternatives are (a) rent a Windows box,
(b) buy a cheap rooted ARM device, or (c) switch to a simulator project (`ClashAI`/`RoyaleSim`) and
accept that HoF-level real-game fidelity is not attainable.

---

### Phase 3 — Reproduce the published baselines (2–3 days after engine works)

This validates the engine, the checkpoint wiring, and the evaluation protocol before any training.

```bash
# General vs IL — published: General 73.30% vs original IL
python -m native_runner.training.v4.evaluate \
  --checkpoint-a ./checkpoints/General/checkpoint-step-00000460.pt \
  --checkpoint-b ./checkpoints/IL/checkpoint-step-00029396.pt \
  --games 40 --output ./evaluations/general-vs-il

# Hog specialist 2 vs General — published: 88.03% (760 games, mirrored)
python -m native_runner.training.v4.evaluate \
  --checkpoint-a ./checkpoints/2_6hog_expert/hog26-specialist2.pt \
  --checkpoint-b ./checkpoints/General/checkpoint-step-00000460.pt \
  --games 40 --match-config ./configs/hog26.json \
  --output ./evaluations/hog2-vs-general
```

Notes:

- The published numbers used **argonmax**, mirrored sides, and a forced first action for original IL
  if it had not acted by 15 s. Reproduce the same conditions or the comparison is meaningless.
- Inspect `matches.jsonl`: wins, `truncated`, `rejected_actions`, `actions_by_owner`, and first
  non-WAIT action time. A high win rate with a passive opponent is the trap the author documents.
- [ ] General beats IL in the expected ballpark (60–75% over 40 games is fine; exact numbers need
  the full protocol and 382+ games).
- [ ] Hog 2 beats General clearly (>75% over 40 games).
- [ ] Rejections = 0, truncations ≈ 0.

**Exit:** you can trust the evaluator. Record checkpoint hashes, match config, seed, and decoding
mode for every run.

---

### Phase 4 — Imitation learning from the HF dataset (1–3 weeks)

The dataset is already on disk. Start small, measure throughput, then scale.

**4a. Subset smoke run (first 2–3 days)**

1. Pick 2,000–5,000 replay tags (one tag per line → `runs/train-tags.txt`).
2. Build the cache on 1 engine and measure **replays/hour/engine**:
   ```bash
   python -m native_runner.training.v4.cache_builder <dataset-dir> runs/il-subset-cache \
     --ports 26789 --source-count 1 --resident-slots 1 --verify-roundtrip
   ```
3. Train 1–2 epochs on CPU first, mainly to prove the pipeline and watch loss:
   ```bash
   bash native_runner/training/v4/launch_stateful_il.sh \
     --index runs/il-subset-cache/index.json --train-tags runs/train-tags.txt \
     --output-dir runs/il-subset --epochs 1 --max-groups 1 --save-every-groups 1
   ```
4. Evaluate the checkpoint against the shipped IL.

**4b. Full run (on a rented GPU host)**

- Decide from 4a's throughput whether the full 252k reconstruction is days or months. Use multiple
  engine ports or `launch_cache_partition.sh`; budget disk for the cache (size unknown until 4a —
  monitor it, it can dwarf the 1.8 GB of parquet).
- Fine-tune from the shipped IL checkpoint before trying from scratch. The author's IL is the
  initialization for General; you are unlikely to beat it with a small budget.
- 1–4 GPUs, `IL_WORLD_SIZE` = GPU count, `CR_IL_SHARDS_PER_RANK` tuned to keep ranks busy.

- [ ] Subset cache: completed/failed counts and expert/executed action counts look sane.
- [ ] Subset IL loss is finite and decreases; checkpoint reloads.
- [ ] Full cache size and wall-clock measured and written here.
- [ ] New IL ≥ shipped IL on a held-out tag set; sampled games look human, not passive.

**Exit:** a reproducible IL checkpoint trained on the HF data, with a measured cost per epoch.

---

### Phase 5 — Hog 2.6 specialist via PPO (2–6 weeks, GPU + engine cluster)

Follow the author's recipe from `docs/training_history.md` rather than inventing one:

1. Initialize from **General**, not from IL (the specialist attempts from IL struggled).
2. Fixed-opponent mix **40% original IL / 60% General**; opponent decks 30% focus / 40% weighted /
   30% hard.
3. Add Hog-specific rewards + Hog replay demonstrations for a short phase → **specialist 1**
   (proactive, but can still open passively).
4. Remove all Hog-specific shaping, keep terminal outcome + tower-health + elixir-overflow
   penalties, fresh optimizer, continue PPO → **specialist 2** (stronger measured results, more
   passive).
5. Infrastructure notes from the author: 1,528 concurrent matches, 40-game-second rollout segments,
   8 GPUs. You will not match that locally; start at 1 engine/guest and scale.
   ```bash
   python -m native_runner.training.v4.build_ppo_deck_pool \
     --dataset-root ./datasets --output ./runs/deck-pool.json --minimum-uses 5
   bash native_runner/training/v4/launch_ppo_engine_node.sh runs/node0 0 1
   # then topology + one-update smoke, then the real run
   bash native_runner/training/v4/launch_ppo_multinode_training.sh runs/ppo-hog runs/topology.json 1 40
   ```

Guardrails (these caught real regressions in the author's runs):

- [ ] `rejected_actions == 0` before accepting a rollout.
- [ ] Track first non-WAIT action time — no 196-second openings.
- [ ] Track elixir overflow and accidental King Tower activation.
- [ ] Always evaluate with replays + hands-on play, not just win rate.

**Exit:** specialist beats the starting General >60–65% over 200+ mirrored games with no execution
errors, and replays show Hog pressure rather than passive waiting. (The published 88% is a
760-game result after a long, well-tuned run — treat it as an upper bound, not a first target.)

---

### Phase 6 — Ladder deployment / HoF push (R&D, unbounded)

This is the gap the repo does not fill. Options, in order of practicality:

1. **Manual assist first:** run the policy service and use it as an advisor/overlay while a human
   plays. Validates decision quality on the real ladder with zero ban/automation risk.
2. **Build an online bridge:** port the probe/action channel to a client that can log into Null's
   ladder instead of the loopback offline engine. This is real engineering: session lifecycle,
   reconnect, server timing, anti-cheat surface, and account safety.
3. **Accept offline HoF as the deliverable:** if the private-server ladder is dead or inaccessible,
   define success as beating the shipped specialist/General and publish that instead.

Timing matters: the main repo plays with the engine's native timing, while the **`yes-delay` branch**
("Everlight") is a larger network that adds the real game's 20-tick (1 s) card delay. If you deploy
against a live server, evaluate Everlight before committing; do not switch branches mid-pipeline.

- [ ] Decision recorded: manual assist / online bridge / offline-only.
- [ ] If online: reconnect + failure handling tested, and account-risk accepted explicitly.
- [ ] Ladder win rate and trophy trajectory logged.

**Exit:** either HoF threshold reached, or a written decision to stop with the offline benchmark.

---

### Phase 7 — Improve beyond the baseline (ongoing)

- Port the `yes-delay` Everlight model (larger net, real 1 s delay) once the loop is stable:
  `git fetch upstream yes-delay:yes-delay`, then read that branch's docs before switching.
- Expand the opponent league: self-play snapshots, deck pool, hard-deck weighting.
- Distill a smaller/faster policy if inference latency matters on the deployment host.
- Collect your own replays on the target ladder and add them to IL (domain shift is real).
- Consider `FirstLight-Zero`'s async PPO framework (1.5× throughput claim) only after the main
  pipeline is reproducible; it is a research fork, not a shortcut.

---

## 3. Effort and budget (rough)

| Item | Estimate |
| --- | --- |
| Phase 1 (env) | 0.5 day, $0 |
| Phase 2 (engine) | 1–2 weeks, $0–100 (device/VM) |
| Phase 3 (baselines) | 2–3 days, ~$10 GPU/engine time |
| Phase 4 (IL) | 1–3 weeks; 20–100 GPU-hours (~$20–150 on a 4090-class rental) |
| Phase 5 (PPO) | 2–6 weeks; 100–1,000+ GPU-hours + engine host (~$200–2,000) |
| Phase 6 (ladder) | unbounded R&D |
| Cache storage | measure in 4a; reserve 50–100 GB |

Timeline is 8–16 weeks part-time, dominated by engine access and PPO iteration, not by code.

---

## 4. Risk register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Cannot obtain/build the Null's Royale APK | Blocks everything | Source it first; test on a Windows box before buying GPUs |
| No ARM64 Android guest on this x86 Linux host | Blocks engine | Use a rooted ARM device, a Windows+MuMu host, or cloud |
| No local GPU | IL/PPO too slow | Rent by the hour; keep local for eval only |
| Full cache is enormous / slow to build | Weeks lost | Subset first, measure replays/hour and bytes/replay |
| Win rate looks good but policy is passive | Fake progress | Replay review, first-action timing, non-WAIT counts |
| Private-server ladder unavailable or empty | No HoF | Fall back to offline benchmark; document it |
| Botting a live account | Ban / account loss | Manual-assist first; explicit risk acceptance before automation |
| Model/repo version drift | Unreproducible runs | Pin commit, checkpoint hashes, `.env`, seeds, configs |

---

## 5. Do not do

- Do not start PPO from scratch. `FirstLight-Zero` needed 1,450 rounds for one deck, and still needs
  the same native engine.
- Do not train on the full dataset before measuring cache throughput on a subset.
- Do not use `ClashAI`/`RoyaleSim` results as evidence of HoF-level play; they are fast simulators
  with a sim-to-real gap and no HoF claim.
- Do not switch to the `yes-delay` branch until the baseline pipeline is reproducible.
- Do not optimize for win rate against a passive IL opponent.

---

## 6. Immediate next actions

1. [ ] Confirm the HoF target (private-server ladder vs official vs offline benchmark).
2. [ ] Run Phase 1: Python 3.12 env + `check_install.py` → `Ready: 5 models`.
3. [ ] Source the Null's Royale 15.535.13 APK and pick an engine host.
4. [ ] Build/install the offline engine and pass the demo-replay smoke test.
5. [ ] Reproduce General vs IL and Hog 2 vs General; write the numbers back into this file.
6. [ ] Start the 2–5k replay cache subset and measure throughput.

---

## 7. Repo map

| Path | Purpose |
| --- | --- |
| `native_runner/` | Training + native engine + battle environment |
| `checkpoints/` | Five released models |
| `docs/getting_started.md` | Engine/APK installation |
| `docs/training.md` | Cache, IL, Linux AVD, PPO |
| `docs/training_history.md` | The five models and the Hog recipe |
| `checkpoints/README.md` | Checkpoint selection and evaluation protocol |
| `.resource/` | Other cloned repos (simulators, forks, live-client tools) |
