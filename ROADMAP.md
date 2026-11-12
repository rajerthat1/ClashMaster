# Roadmap: V4 Hog 2.6 → V4 X-Bow 3.0 → Ultimate Champion

Status: active — Null's Royale phase
Owner: Raj
Primary repo: this repository (fork of `FirstLight_CR`)
Model: **FirstLight_CR V4, 16.8M parameters** — one model family, no side quests

**End goal:** reach **Ultimate Champion (UC) on Supercell's main ladder** with **X-Bow 3.0**.
**Current focus:** get strong on **Null's Royale** first. The main-server bridge is deferred to Phase 8.

| Stage | Deck | Environment | Status |
| --- | --- | --- | --- |
| 1 | Hog 2.6 | Null's Royale | Live-loop validation with the shipped specialist |
| 2 | X-Bow 3.0 | Null's Royale | Train and validate the real UC deck |
| 3 | X-Bow 3.0 | Main server | Deferred: reader, bridge, UC push |

**Milestones**

- **M1 — Smoke tests pass:** models load, demo replay reconstructs, baselines reproduce.
- **M2 — Hog live:** shipped Hog specialist plays Null's ladder through a working reader/tap loop.
- **M3 — X-Bow trained:** V4 X-Bow beats Hog/General on Null's.
- **M4 — Readiness gate:** X-Bow climbs Null's and is judged UC-caliber.
- **M5 — Main bridge + UC:** official reader, then the climb.

---

## 0. Project constraints

| Constraint | Answer | Effect |
| --- | --- | --- |
| Model | **V4 16.8M only** | Use the five shipped checkpoints; fine-tune V4 for X-Bow |
| Main account | X-Bow 3.0 maxed; Hog 2.6 incomplete; Grand Champion / Master | X-Bow is the only main-ladder deck |
| Null's account | Everything maxed (lvl 16 + evo/hero) | Safe sandbox for both decks |
| Ban tolerance | Full automation accepted on main | Applies at Phase 9 only |
| Hardware | Windows PC; cloud budget only; no rooted device | MuMu for Null's; phone optional at Phase 9 |
| Time | 5–15 h/week hands-on, tens of hours idle training | Null's phase 3–6 months; through UC 9–18 |
| Budget | ~$500 cloud credits; no HPC | Fine-tunes + short cache VM, not 8-GPU self-play |
| Current focus | **Null's Royale** | Do not start the main bridge before Phase 8 passes |

### Model choice: V4, not FirstLight-Zero

| | FirstLight_CR V4 | FirstLight-Zero XbowNet |
| --- | --- | --- |
| Parameters | **16.8M** | 1.17M |
| CNN / recurrent | 128 ch, 5 blocks / LSTM 768 | 64 ch, 4 dilated / LSTM 256 |
| Input | full `ObservationV1` | pruned `firstlight_zero` schema |
| Training | IL + PPO | X-Bow self-play only |
| Use here | **Everything** | Reference only; not checkpoint-compatible |

The 1.17M model is a narrow X-Bow mirror specialist with a different observation contract. Its
weights cannot be loaded by `load_policy_v4`. Do not port it mid-project. Architecture is not the
bottleneck; data, opponents, and evaluation are.

---

## 1. Goal and staged path

**FirstLight_CR V4 is the right base.** It ships the only complete IL + PPO pipeline, five checkpoints,
252k top-ladder replays, a native engine, and a Hog 2.6 specialist the author used to reach a high
ladder rank. The V4 checkpoints are the ones to use:

| Checkpoint | Use |
| --- | --- |
| `2_6hog_expert/hog26-specialist2.pt` | Hog live-loop validation on Null's |
| `General/checkpoint-step-00000460.pt` | X-Bow initialization and zero-shot baseline |
| `IL/checkpoint-step-00029396.pt` | X-Bow IL fine-tune base |
| `active IL/...` | Reference opponent / alternate init |

**Null's is the place to start.** The engine is a patched **Null's Royale 15.535.13** client, isolated
from the network. It is a safe, maxed-account sandbox to prove the entire stack: cache → IL → PPO →
live reader → taps → ladder queue. Nothing about the main server needs solving until that works.

**The main server is a later project.** The official client cannot be reached from this engine. The
live-loop architecture exists in `daybreak-cr` (measured on official build 160402012), but its reader
is private. Phase 8 is a new reader plus the card-catalog and monthly maintenance work.

**Staged gates:**

1. **Engine + smoke tests** (Phases 1–3).
2. **Hog live loop** (Phase 5) — prove reader/taps with a known-good checkpoint.
3. **X-Bow trained** (Phase 6) — V4 specialist on Null's.
4. **Readiness gate** (Phase 7) — no main work until this passes.
5. **Main reader + UC** (Phases 8–9).

### The main-server treadmill (know it now, start it later)

Public tracking, Nov 2026:

- **Seasons:** monthly (Season 88 Oct 5; Season 89 Nov 2).
- **Balance changes:** 1–2/month (Sep 8 & 17; Oct 5 & 19).
- **Client builds:** roughly monthly branches plus patches within days:
  `160402012` (Oct 3) → `160635001` (Oct 27) → `160635007` (Oct 30) → `160635010` (Nov 8).
- **New evolutions/heroes:** most seasons (Evo Electro Giant + Hero E-Wizard; Evo Giant + Evo Mirror).

Three maintenance axes:

1. **Reader offsets** — break on client builds; re-validate monthly-ish.
2. **Card catalog** — V4 is pinned to Null's 15.535.13 (122 cards, 42 evos, 24 heroes). Official 16.635
   has forms V4 may not know; extend the catalog and fine-tune.
3. **Policy/meta** — balance changes shift matchups monthly.

**Reality check:** UC is a deployment, account, meta and operations problem as much as a model
problem. Null's gives you a strong V4 policy and a working loop before you pay that cost.

**The other hard truth:** the HF dataset alone does not start training. The cache builder replays every
match through the native engine. Phase 2 is a prerequisite for dataset training.

---

## 2. What you already have

| Asset | State |
| --- | --- |
| Repository | Fork of `FirstLight_CR` at the repo root |
| Model | V4 16.8M; five checkpoints |
| IL_Replay dataset | Complete: 252,238 matches, 17.8M actions, ~1.8 GB |
| Training code | IL cache builder, IL trainer, PPO, evaluator, policy service |
| Null's reader | `.resource/cr-memory-reader` |
| Live-loop blueprint | `.resource/daybreak-cr/play.py` (defer to Phase 8) |
| Timing rules | `.resource/daybreak-cr/docs/GAME_INTEGRATION.md` |
| X-Bow prior art | `.resource/ClashAI` (icebow), `.resource/FirstLight-Zero` (reference only) |
| Local machine | Linux, 12 vCPU, 14 GB RAM, no CUDA GPU, 90 GB free |
| Windows machine | MuMu host for engine build/run |
| Budget | ~$500 cloud credits; no HPC |
| Missing | Null's APK, GPU for training, official-client reader (Phase 8) |

---

## 3. Phase plan

### Where each phase runs (OS + compute)

Upstream guidance: **Windows supports cache building, inference, evaluation and the console. The
IL/PPO launchers target Linux with CUDA and NCCL.** Plan around that split.

| Phase | OS | Laptop (Linux, no GPU) | Cloud credits |
| --- | --- | --- | --- |
| 0 Decisions | any | ✓ | — |
| 1 Env + model smoke | Linux/Windows | ✓ | ✓ |
| 2 Engine build/install | **Windows + MuMu** | ✗ (use Windows PC) | Windows GPU VM (needs nested virt); test first |
| 3 Baselines/evaluations | host with engine | only against a LAN engine | Windows/Linux GPU |
| 4 Cache build | Windows + MuMu | ✗ (use Windows PC) | Windows VM (short burst) |
| 4 IL smoke/train | **Linux** (bash + CUDA) | CPU smoke only | **Linux GPU (best)** |
| 5 Hog live loop | Windows + MuMu / Android | ✗ on laptop; ✓ on Windows PC | Windows VM (if nested virt) |
| 6 X-Bow zero-shot/eval | host with engine | via LAN engine | Windows/Linux |
| 6 X-Bow IL/PPO | **Linux** for training | CPU smoke only | **Linux GPU (best)** |
| 7 Readiness gate | Windows + MuMu | ✗ on laptop; ✓ on Windows PC | Windows VM |
| 8 Main bridge | Android / Windows | ✓ local (Windows/Android) | Windows VM or physical device |
| 9 UC push | Android / Windows | ✓ local (Windows/Android) | — |
| 10 Improve | Linux training + local live | some | ✓ |

**Hard constraints**

- **No HPC access.** Every remote job runs on the **$500 cloud credits**; budget and monitor spend.
- Cloud Windows needs **nested virtualization** for MuMu; test on an hourly VM before committing.
- Prefer Linux GPU instances for IL/PPO. Windows VMs are for cache/engine work only.
- Cloud ingress/egress and storage cost money too; keep the cache on the instance or cheap object
  storage and delete it after upload.
- The laptop has no CUDA GPU. It is for code, smoke tests, small CPU IL runs, fast-sim experiments, and
  evaluation against an engine on the LAN.

**Recommended allocation**

- **Windows PC:** engine build, cache building, Hog/Null's live loop, main bridge.
- **Linux laptop:** repo work, `check_install`, small IL smoke tests, RoyaleSim experiments, evaluations
  against a LAN engine.
- **$500 cloud:** Windows cache-build VM (short burst), Linux GPU for IL/PPO, engine hosting fallback.
  Use spot/preemptible instances, checkpoint often, and stop instances when idle.

---

### Phase 0 — Decisions and focus

- [x] Model: V4 16.8M only; no FirstLight-Zero port.
- [x] Hog 2.6: Null's-only sandbox.
- [x] Main account: X-Bow 3.0 maxed.
- [x] Risk: full automation accepted on main.
- [x] Budget: ~$500 cloud credits; no HPC.
- [x] **Current focus: Null's Royale.**
- [ ] Set a Null's milestone target (e.g. trophies or win rate over 200 games).
- [ ] Treat the rooted phone as optional at Phase 8.

---

### Phase 1 — Environment + model smoke test (half a day)

```bash
cd /home/raj/Desktop/ClashMaster
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python torch==2.5.1 \
  --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python tools/check_install.py      # expect: Ready: 5 models
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest native_runner/tests -q
cp .env.example .env
```

**Smoke test:** all five V4 checkpoints load and hashes verify; tests pass/skip cleanly.

- [ ] `Ready: 5 models`.
- [ ] `.env` created.

---

### Phase 2 — Engine + replay smoke test (1–2 weeks)

Use the supported MuMu path: Android 12, root, ARM64, original Null's 15.535.13 APK, JDK 17 + NDK 27.3
+ build-tools 37.

```powershell
./check_original_game.ps1 -InputApk ./user-provided/nulls-royale.apk
./build_offline_engine.ps1 -InputApk ./user-provided/nulls-royale.apk -OutputApk ./build/cr-ai-offline.apk
./install_offline_engine.ps1 -Apk ./build/cr-ai-offline.apk
```

**Smoke tests:**

```bash
python tools/make_demo_data.py
python -m native_runner.training.v4.cache_builder runs/demo/dataset runs/demo/cache \
  --ports 26789 --source-count 1 --resident-slots 1 --verify-roundtrip
python -m native_runner.training.v4.serve_policy \
  --checkpoint ./checkpoints/General/checkpoint-step-00000460.pt
```

- [ ] One manual vs model match runs.
- [ ] Demo replay reconstructs with `--verify-roundtrip`.
- [ ] Evaluator gives `truncated: 0`, `rejected_actions: 0`.

**Exit:** one engine process completes a match. Do not train from parquet alone.

---

### Phase 3 — Baseline smoke test (2–3 days)

```bash
python -m native_runner.training.v4.evaluate \
  --checkpoint-a ./checkpoints/General/checkpoint-step-00000460.pt \
  --checkpoint-b ./checkpoints/IL/checkpoint-step-00029396.pt \
  --games 40 --output ./evaluations/general-vs-il
```

For Hog vs General, build a `MatchConfig` JSON with the Hog deck and explicit form availability
(fields in `native_runner/match_factory.py`); the repo does not ship a `configs/hog26.json`. Keep
decks, forms, levels, seed and decoding identical across comparisons.

- [ ] General beats IL in the expected ballpark.
- [ ] Hog 2 beats General clearly (>75% over 40 games).
- [ ] Rejections = 0; truncations ≈ 0.

**Exit:** the evaluator is trustworthy; record hashes/config/seed.

---

### Phase 4 — Training-pipeline smoke test (1–2 weeks)

This proves the HF dataset → cache → IL path. It is **not** a Hog PPO reproduction; the shipped Hog
specialist is the Hog bot.

1. Select 2,000–5,000 replay tags → `runs/train-tags.txt`.
2. Build the cache and measure replays/hour/engine:
   ```bash
   python -m native_runner.training.v4.cache_builder <dataset-dir> runs/il-subset-cache \
     --ports 26789 --source-count 1 --resident-slots 1 --verify-roundtrip
   ```
3. Run one IL group:
   ```bash
   bash native_runner/training/v4/launch_stateful_il.sh \
     --index runs/il-subset-cache/index.json --train-tags runs/train-tags.txt \
     --output-dir runs/il-smoke --epochs 1 --max-groups 1 --save-every-groups 1
   ```

**Smoke test:** loss finite/decreasing; checkpoint reloads and evaluates.

- [ ] Cache counts and expert/executed actions look sane.
- [ ] IL smoke checkpoint loads.
- [ ] Measured cache throughput and bytes/replay written down.

**Exit:** the training pipeline is proven. Hog PPO is optional; skip it unless you want the exercise.

---

### Phase 5 — Hog live loop on Null's (2–4 weeks)

Use the shipped **Hog specialist 2** to prove the live stack: reader → observation → policy → taps →
ladder queue → reconnect. No training needed for this phase.

Use `.resource/daybreak-cr/play.py` as the architecture reference and `.resource/cr-memory-reader` for
the Null's state reader. Read `docs/GAME_INTEGRATION.md` for touch/deal/elixir/server-turn timing.

- [ ] Reader produces elixir, hand, cycle, entities, tower HP, result at ≥20 Hz.
- [ ] Policy issues legal taps.
- [ ] End-to-end latency <100 ms observation→tap.
- [ ] Hog specialist plays N ladder games unattended on Null's.
- [ ] Reconnect/abort handling tested.

**Exit:** a working Hog bot on Null's. This is M2 and the riskiest infrastructure milestone.

---

### Phase 6 — V4 X-Bow 3.0 specialist on Null's (2–4 weeks)

1. **Zero-shot:** run General with the X-Bow deck on the offline engine/Null's; measure before
   training. Card representations are universal.
2. **Data:** filter IL_Replay for X-Bow 3.0 games; build a 5–20k cache; fine-tune V4 IL.
3. **PPO:** short deck-specific run from General/IL. X-Bow placement, spell cycle, defensive Tesla/
   Knight usage; remove shaping after the behavior appears.
4. **Prior art (ideas only):** `ClashAI` icebow rewards; `FirstLight-Zero` self-play lessons. Do not
   port its weights.
5. **Meta:** test common Null's matchups; adjust the build if X-Bow is not viable.

- [ ] X-Bow IL checkpoint trained on filtered data.
- [ ] X-Bow specialist beats General and trades evenly with Hog 2.6.
- [ ] Matchup table documented.

**Exit:** a V4 X-Bow policy good enough for the Phase 7 gate.

---

### Phase 7 — Null's readiness gate (1–2 weeks)

Do not start the main bridge until this passes:

- [ ] X-Bow wins >60% over 200+ mirrored games against the deck pool.
- [ ] X-Bow beats Hog specialist 2 over 100+ games.
- [ ] X-Bow reaches a self-defined Null's ladder target.
- [ ] 100+ unattended live games: no crashes, no illegal taps, reconnect works.
- [ ] Replay review shows proactive, human-like X-Bow play.
- [ ] You (Grand Champion/Master) judge the decisions UC-caliber.

**Exit:** written go/no-go. If no-go, improve on Null's; do not start Phase 8.

---

### Phase 8 — Main-server memory bridge (deferred to the end)

Only after Phase 7 passes. This is the critical R&D: a reader for the current official build plus the
observation/action adapter.

**8.1 Host**

| Host | Verdict |
| --- | --- |
| **Rooted ARM64 phone** (optional) | Best experience; buy used (~$60–100) only if MuMu is too painful |
| Windows + MuMu | No-purchase default; direct `/proc/pid/mem` reads; Frida breaks on x86_64+ARM translation |
| Vision | Fallback only: 100–300 ms, heavy |

**8.2 Reader**

1. Find package, version, `libg.so` base.
2. Find `holder`/player-state: elixir, hand, cycle, tower HP, result.
3. Find the entity graph (`hpComponentAccessor`): units, sides, card IDs, positions, HP.
4. Find the command queue (optional).
5. Map into `ObservationV1`; verify against recorded frames.
6. Add a build check and versioned parsing.

Use `daybreak-cr/play.py` for public chains (`battle = base + manager_global + 0x248 + 0x90`, roster
`+0xa8`, ticks `+0x60/+0x64`) and `cr-memory-reader` for the RE method.

**8.3 Card catalog**

Extend `native_runner/data/competitive` for official 16.635 forms; fine-tune V4. A perfect reader
feeding a model that does not know Evo Giant or Hero Electro Wizard is still weak.

**8.4 Action executor**

Two touches per play; use `GAME_INTEGRATION.md` (21-tick execution, hand refill, server turns R-8/R+13,
first-touch lockout). Use the `yes-delay` branch for the real 1 s card delay.

**8.5 Maintenance**

Pattern scanning, version registry, memory-dump tests; 1–3 days per major build, 1–2 weeks if structs
change. Frida only for RE; passive native reads for live play.

- [ ] Reader stable at ≥20 Hz.
- [ ] Observation matches live state on 50+ frames.
- [ ] Legal actions in friendlies; latency <100 ms.
- [ ] Ranked games: legality, disconnects, win rate.

**Exit:** the V4 X-Bow policy plays official matches with a stable reader and no illegal actions.

---

### Phase 9 — UC push on the main ladder (seasonal)

- [ ] Confirm deck forms and tower troop for the season.
- [ ] Plan around Path of Legends resets.
- [ ] Operate in sessions; monitor latency, disconnects, rejected taps, loss streaks.
- [ ] Automation approved; keep behavior plausible; accept residual detection risk.
- [ ] Track win rate by matchup; adjust weekly.
- [ ] Keep a rollback checkpoint and a ban plan.

**Exit:** UC reached and maintained, or best rank documented with the stop reason.

---

### Phase 10 — Improve beyond the baseline (ongoing)

- Expand the opponent league: self-play snapshots, deck pool, hard-deck weighting.
- Collect main-ladder replays; add to IL.
- Distill a smaller V4 if inference latency matters.
- Re-evaluate the deck each season.
- Port `yes-delay` timing once the loop is stable:
  `git fetch upstream yes-delay:yes-delay`.

---

## 4. Training loop and promotion rules

1. Start from V4 General/IL. No from-scratch.
2. IL fine-tune on a 5–20k deck subset (Hog or X-Bow).
3. PPO against a mixed pool: 40% IL / 60% General, current self-play, recent snapshots, hard decks.
4. Reward: terminal win/loss dominates; keep tower/elixir shaping small and remove it once the behavior
   appears.
5. Validate on the native engine, then a live Null's sample.

**Promotion gate for a V4 checkpoint**

- [ ] ≥60% over 200+ mirrored games against the deck pool.
- [ ] Beats the current champion (Hog specialist or General) over 100+ games.
- [ ] `rejected_actions == 0`; truncations explained.
- [ ] No 196-second openings, overflow spiral, or stall loop.
- [ ] Replay review shows intentional deck play, not exploit spam.
- [ ] 20–50 live Null's games without crashes or illegal taps.

**Avoid**

- Training only against a fixed passive opponent (fake win rate).
- Shaping that rewards chip/stall over winning.
- Single-strategy spam that beats the pool but not diverse decks.
- Sim bugs; validate on the native engine.

Use fast sim only for X-Bow placement/reward experiments; native engine for truth; live Null's for
validation.

---

## 5. Fast-simulator track (optional, parallel)

Use `ClashAI`/`RoyaleSim` for cheap algorithm iteration, especially X-Bow reward design. Do **not**
treat their results as main-server evidence: different physics, real sim-to-real gap, no UC evidence.
Promote only ideas that survive the native engine and Null's ladder.

RoyaleSim is Rust and fast (~60 ms per 3-minute battle raw, ~0.3 s with Python observations), but its
claim is 145 cards / 58 evolved forms and ~56.5% per-tick position agreement — approximate. Use it for
speed, not truth. `FirstLight-Zero` is reference only; do not port its weights.

---

## 6. Effort and budget

| Phase | Estimate |
| --- | --- |
| 1 (env) | 0.5 day, $0 |
| 2 (engine) | 1–2 weeks, $0 (Windows PC) |
| 3 (baselines) | 2–3 days, $0 |
| 4 (cache + training smoke) | 1–2 weeks; 5–20 GPU-hours + short Windows cache VM (~$10–40) |
| 5 (Hog live loop) | 2–4 weeks; $0 |
| 6 (X-Bow V4) | 2–4 weeks; 10–50 GPU-hours (~$10–60) |
| 7 (gate) | 1–2 weeks; $0 |
| 8 (main reader) | **4–12+ weeks**; $0–100 (phone optional) + monthly maintenance |
| 9 (UC push) | 1–3 seasons |
| **Cloud spend (1–6)** | **~$40–150 of the $500; keep the rest as buffer** |
| **Null's path (1–7)** | **~3–6 months part-time** |
| **Through UC (1–9)** | **~9–18 months part-time** |

---

## 7. Risk register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Scope creep into main bridge too early | Months lost | Phase 7 gate; defer Phase 8 |
| Model drift to FirstLight-Zero | Incompatible weights, wasted time | V4-only decision; no port mid-project |
| Hog PPO overfits passive IL | Fake progress | Use shipped Hog; replay review, first-action timing |
| Cache too large/slow | Weeks lost | 2–5k subset first; measure bytes/replay |
| Live loop reader/taps unstable | No ladder data | Phase 5 with known-good checkpoint; timebox |
| X-Bow not viable in meta | Weak main deck | Matchup analysis; be ready to switch control deck |
| Cloud credits exhausted | Training stops mid-run | Subset caches, spot instances, stop idle VMs, monitor spend |
| Windows VM lacks nested virtualization | No MuMu in cloud | Test hourly; use the local Windows PC for cache/live |
| Cloud storage/egress costs | Hidden spend | Keep cache on the instance, compress, delete after upload |
| **Phase 8:** automation ban | Lose account | Accepted risk; session hygiene; backup plan |
| **Phase 8:** build breaks offsets | Bot stops | Pattern scanning; version registry; monthly re-validation |
| **Phase 8:** new evos/heroes not in catalog | Model cannot represent them | Extend competitive data; fine-tune |
| **Phase 8:** sim-to-real gap | Offline wins don't transfer | Main replays; assist validation; retrain |
| **Phase 9:** season reset | Progress wiped | Plan within a season |

---

## 8. Do not do

- Do not port FirstLight-Zero's 1.17M weights; different architecture and observation contract.
- Do not reproduce Hog PPO; use the shipped Hog specialist.
- Do not start Phase 8 before the Phase 7 gate.
- Do not build a Hog 2.6 main account.
- Do not assume Null's results transfer to main.
- Do not train X-Bow from scratch; fine-tune V4.
- Do not train on the full 252k dataset with this budget; subset first.
- Do not plan around HPC access; it is not available.
- Do not leave cloud instances running while idle.
- Do not treat RoyaleSim/ClashAI results as main-server evidence.
- Do not switch to `yes-delay` until the baseline is reproducible.

---

## 9. Immediate next actions

1. [ ] Phase 1: env + `check_install.py` → `Ready: 5 models`.
2. [ ] Phase 2: engine + demo replay + evaluator smoke test.
3. [ ] Phase 3: General vs IL (and Hog vs General with a MatchConfig).
4. [ ] Phase 4: 2–5k replay cache + one-group IL smoke test.
5. [ ] Phase 5: Hog specialist live loop on Null's.
6. [ ] Phase 6: General zero-shot with the X-Bow deck; then V4 IL fine-tune.

---

## 10. Repo map

| Path | Purpose |
| --- | --- |
| `native_runner/` | V4 training + native engine + battle environment |
| `checkpoints/` | Five V4 models |
| `docs/getting_started.md` | Engine/APK installation |
| `docs/training.md` | Cache, IL, Linux AVD, PPO |
| `docs/training_history.md` | The five models and the Hog recipe |
| `checkpoints/README.md` | Checkpoint selection and evaluation protocol |
| `.resource/cr-memory-reader/` | Null's memory-reader method (Phase 5) |
| `.resource/daybreak-cr/` | Live-loop architecture; defer to Phase 8 |
| `.resource/daybreak-cr/docs/GAME_INTEGRATION.md` | Touch, hand, elixir, server-turn rules |
| `.resource/ClashAI/`, `.resource/FirstLight-Zero/` | X-Bow ideas only; no weight port |
| `.resource/` | Other clones, ignored by git |
