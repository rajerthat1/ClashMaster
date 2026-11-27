# Roadmap: Hog 2.6 → X-Bow 3.0 → Ultimate Champion

Status: active — Null's Royale phase
Owner: Raj
Primary repo: this repository (fork of `FirstLight_CR`; upstream code lives at the repo root)

**End goal:** reach **Ultimate Champion (UC) on Supercell's main ladder** with **X-Bow 3.0**.
**Current focus:** get strong on **Null's Royale** first. The main-server bridge and daybreak work are
deferred to Phase 9, behind a readiness gate.

| Stage | Deck | Environment | Status |
| --- | --- | --- | --- |
| 1 | Hog 2.6 | Null's Royale | Active: prove the training + live loop |
| 2 | X-Bow 3.0 | Null's Royale | Next: train and validate the real UC deck |
| 3 | X-Bow 3.0 | Main server | Deferred: reader, bridge, UC push |

**Milestones**

- **M1 — Engine + baselines:** one native match, published numbers reproduced.
- **M2 — Hog on Null's:** live memory loop, Hog bot climbs unattended.
- **M3 — X-Bow on Null's:** X-Bow specialist beats Hog/General and climbs.
- **M4 — Main bridge:** official-client reader stable at ≥20 Hz.
- **M5 — UC:** X-Bow reaches Ultimate Champion on main.

---

## 0. Project constraints (from the planning Q&A)

| Constraint | Answer | Effect |
| --- | --- | --- |
| Main account | X-Bow 3.0 maxed; Hog 2.6 incomplete; Grand Champion / Master | X-Bow is the only main-ladder deck; Hog is Null's-only |
| Null's account | Everything maxed (lvl 16 + evo/hero) | Perfect sandbox for both decks |
| Ban tolerance | Full automation accepted on main | Applies only at Phase 10; not a reason to rush the bridge |
| Hardware | Windows PC; cloud budget only; no rooted device | MuMu for Null's; rooted phone is optional at Phase 9, not now |
| Time | 5–15 h/week hands-on, tens of hours idle training | Null's phase is 3–6 months; main phase adds 3–6+ |
| Budget | ~$50–200 (student credits) | Shipped checkpoints + short fine-tunes; no 8-GPU PPO |
| Sequencing | Hog first, then X-Bow; Null's first, main last | Phases 5–8 are Null's; Phase 9 is the bridge |
| Current focus | **Null's Royale** | Do not start daybreak/main work until Phase 8 passes |

---

## 1. Goal, and the staged path

**FirstLight_CR is the right training base.** It ships the only complete imitation-learning + PPO
pipeline, five checkpoints, 252k top-ladder replays, a native engine, and a Hog 2.6 specialist the
author used to reach a high ladder rank. It is far ahead of the pure simulators (`RoyaleSim`, `ClashAI`,
`clash-royale-simulator`), the forks (`FirstLight-Zero`, `FL-Royale`), and the live-client tools
(`cr-memory-reader`, `daybreak-cr`) for *policy training*.

**Null's is the right place to start.** The engine is a patched **Null's Royale 15.535.13** client,
isolated from the network. That makes it a safe, maxed-account sandbox to prove the entire stack:
replay cache → IL → PPO → live memory loop → taps → ladder queue. Nothing about the main server needs
to be solved until that works.

**The main server is a separate, later project.** The official client cannot be reached from this
engine. The live-loop architecture exists in `daybreak-cr` (measured on official build 160402012), but
its reader is private. Reaching UC requires a new reader for the current official build, plus the
card-catalog updates and monthly maintenance described below. That is Phase 9, not now.

**The staged gates:**

1. **Engine** (Phase 2) — one native match. Hard prerequisite for dataset training.
2. **Null's live loop** (Phase 6) — reader → observation → policy → taps.
3. **X-Bow readiness** (Phase 8) — the X-Bow policy is strong enough to justify the main bridge.
4. **Main reader** (Phase 9) — official-client state at ≥20 Hz.
5. **UC push** (Phase 10) — seasonal climb.

### Memory reading is the primary path, not vision

Applies when Phase 9 starts, but the architecture matters now:

| Source | Latency | Compute | Main-server feasibility | Notes |
| --- | --- | --- | --- | --- |
| **Memory reader** (native / Frida / rooted guest) | ~20–50 ms | Tiny | **Proven** by `daybreak-cr` on official build 160402012 | Per-build offsets; monthly maintenance |
| Vision (screen capture + detection) | ~100–300 ms | Heavy | Possible, unrooted | Fallback only; clutter misreads |
| Hybrid | 20–100 ms | Medium | Possible | Memory for hand/elixir, vision for arena |

- `cr-memory-reader` targets **Null's Royale only** (package `nullsroyale.rel.free`, lead version
  13.300.6). Its offsets do not work on main; its **method** does: find `libg.so`, `holder`, and
  `hpComponentAccessor`, then build pointer chains.
- **Cheat Engine on the Windows host is the wrong layer.** MuMu is a VM; a host scan sees emulator
  memory, not the guest's address space. GameGuardian inside a rooted guest can discover values, but a
  production bot needs stable structures, not value scans.
- **Detection:** passive `/proc/pid/mem` reads are stealthier than Frida injection; root/emulator and
  behavioral patterns remain risks. No guarantees.

### The main-server treadmill (plan for it now, start it later)

Public tracking, Nov 2026:

- **Seasons:** monthly. Season 88 Oct 5; Season 89 Nov 2.
- **Balance changes:** 1–2 per month (Sep 8 & 17; Oct 5 & 19; none at the start of November).
- **Client builds:** roughly monthly major branches, plus patches within days:
  `160402012` (Oct 3) → `160635001` (Oct 27) → `160635007` (Oct 30) → `160635010` (Nov 8).
- **New evolutions/heroes:** almost every season (S88 Evo Electro Giant + Hero Electro Wizard; S89 Evo
  Giant + Evo Mirror).

Three maintenance axes, only one of which is the reader:

1. **Reader offsets** — break on client builds; re-validate monthly-ish.
2. **Card catalog/features** — FirstLight is pinned to Null's 15.535.13 (122 cards, 42 evos, 24 heroes).
   Official 16.635 has forms the model may not know. Extend the catalog and fine-tune.
3. **Policy/meta** — balance changes shift matchups monthly; reader unaffected, policy affected.

A main-server reader is a subscription, not a one-time build. Budget ~1–3 days per major build for
re-validation, more when battle structs change.

**Reality check:** UC is a deployment, account, meta and operations problem as much as a model
problem. The X-Bow account is ready; the reader and the climb are the hard parts. The Null's phase
gives you a strong policy and a working loop before you pay that cost.

**The other hard truth:** the HF dataset alone does not start training. The cache builder replays every
match through the native engine. Phase 2 is a prerequisite for "dataset training", not optional.

---

## 2. What you already have

| Asset | State |
| --- | --- |
| Repository | Fork of `FirstLight_CR` at the repo root |
| Checkpoints | 5 models: IL, Active IL, General, Hog specialist 1, Hog specialist 2 |
| IL_Replay dataset | Complete: 252,238 matches, 17.8M actions, ~1.8 GB |
| Training code | IL cache builder, IL trainer, PPO cluster trainer, evaluator, policy service |
| Null's reader | `.resource/cr-memory-reader` (memory/Frida state capture for Null's) |
| Live-loop blueprint | `.resource/daybreak-cr/play.py` (defer until Phase 9; useful earlier for tap timing) |
| Timing rules | `.resource/daybreak-cr/docs/GAME_INTEGRATION.md` (touch, hand, elixir, server turns) |
| X-Bow prior art | `.resource/ClashAI` (icebow, simulator), `.resource/FirstLight-Zero` (3.0 X-Bow self-play) |
| Local machine | Linux, 12 vCPU, 14 GB RAM, **no CUDA GPU**, 90 GB free, `/dev/kvm`, `adb` |
| Windows machine | MuMu host for engine build/run (the supported path) |
| Cloud budget | ~$50–200, student credits |
| Missing | Null's APK, GPU for training, official-client reader (Phase 9 only) |

---

## 3. Phase plan

### Phase 0 — Decisions and focus (keep this current)

- [x] End goal: UC on main, final deck X-Bow 3.0.
- [x] Hog 2.6: Null's-only sandbox; do not build it on main.
- [x] Main account: X-Bow 3.0 maxed; eligible for a UC push.
- [x] Risk: full automation accepted on main; ban risk acknowledged.
- [x] Budget: $50–200; shipped checkpoints before GPUs.
- [x] **Current focus: Null's Royale. Main bridge deferred to Phase 9.**
- [ ] Set a Null's milestone target (e.g. trophies or win rate over 200 games).
- [ ] Treat the rooted phone as optional: decide at Phase 9, and only if MuMu direct reads prove too painful.

---

### Phase 1 — Local Python environment (half a day, no GPU)

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

- [ ] `check_install.py` prints `Ready: 5 models`.
- [ ] Tests pass or skip only for missing CUDA/native prerequisites.
- [ ] `.env` created.

**Exit:** five checkpoints load and hashes verify on CPU.

---

### Phase 2 — Native engine on Windows (1–2 weeks)

You have a Windows PC, so use the supported MuMu path: Android 12 instance with root and ARM64
support, the original Null's Royale APK, JDK 17 + NDK 27.3 + build-tools 37.

1. Obtain `nulls-royale.apk` 15.535.13 arm64-v8a (user-supplied). Let it update to content
   15.535.86 in the lobby, then close it.
2. Build + install:
   ```powershell
   ./check_original_game.ps1 -InputApk ./user-provided/nulls-royale.apk
   ./build_offline_engine.ps1 -InputApk ./user-provided/nulls-royale.apk -OutputApk ./build/cr-ai-offline.apk
   ./install_offline_engine.ps1 -Apk ./build/cr-ai-offline.apk
   ```
3. Smoke test:
   ```bash
   python tools/make_demo_data.py
   python -m native_runner.training.v4.cache_builder runs/demo/dataset runs/demo/cache \
     --ports 26789 --source-count 1 --resident-slots 1 --verify-roundtrip
   python -m native_runner.training.v4.serve_policy \
     --checkpoint ./checkpoints/General/checkpoint-step-00000460.pt
   ```

- [ ] One manual vs model match runs end-to-end.
- [ ] Demo replay reconstructs with `--verify-roundtrip`.
- [ ] Evaluator produces `summary.json` with `truncated: 0` and `rejected_actions: 0`.

**Exit:** one engine process accepts commands and completes a match.

**Fallback:** do not train from parquet alone; the observation contract comes from the engine. If MuMu
fails, rent a Windows host or use a rooted ARM device.

---

### Phase 3 — Reproduce the published baselines (2–3 days)

```bash
python -m native_runner.training.v4.evaluate \
  --checkpoint-a ./checkpoints/General/checkpoint-step-00000460.pt \
  --checkpoint-b ./checkpoints/IL/checkpoint-step-00029396.pt \
  --games 40 --output ./evaluations/general-vs-il

python -m native_runner.training.v4.evaluate \
  --checkpoint-a ./checkpoints/2_6hog_expert/hog26-specialist2.pt \
  --checkpoint-b ./checkpoints/General/checkpoint-step-00000460.pt \
  --games 40 --match-config ./configs/hog26.json \
  --output ./evaluations/hog2-vs-general
```

- Published: General 73.30% vs IL; Hog specialist 2 88.03% vs General (760 mirrored games).
- Use argmax, mirrored sides, and the author's first-action rule.
- Inspect `matches.jsonl`: `truncated`, `rejected_actions`, `actions_by_owner`, first non-WAIT time.

- [ ] Numbers in the expected ballpark.
- [ ] Rejections = 0, truncations ≈ 0.

**Exit:** the evaluator is trustworthy; record hashes, config, seed, decoding mode.

---

### Phase 4 — Imitation learning from the HF dataset (1–3 weeks)

**4a. Subset smoke run**

1. Pick 2,000–5,000 tags (one per line → `runs/train-tags.txt`).
2. Build the cache and measure replays/hour/engine:
   ```bash
   python -m native_runner.training.v4.cache_builder <dataset-dir> runs/il-subset-cache \
     --ports 26789 --source-count 1 --resident-slots 1 --verify-roundtrip
   ```
3. Train 1–2 epochs:
   ```bash
   bash native_runner/training/v4/launch_stateful_il.sh \
     --index runs/il-subset-cache/index.json --train-tags runs/train-tags.txt \
     --output-dir runs/il-subset --epochs 1 --max-groups 1 --save-every-groups 1
   ```

**4b. Budget-aware run**

- Filter for the deck you are training: Hog first, then X-Bow 3.0.
- The full 252k cache is likely too large for this budget; use a 5–20k subset.
- Fine-tune from the shipped IL checkpoint; do not train from scratch.
- 1 GPU (rented) is enough; `IL_WORLD_SIZE=1`.
- Cache storage: monitor; reserve 50–100 GB if you scale.

- [ ] Subset cache counts and expert/executed actions look sane.
- [ ] Loss finite and decreasing; checkpoint reloads.
- [ ] New IL ≥ shipped IL on held-out tags; samples look human.

**Exit:** a reproducible IL checkpoint with measured cost per epoch.

---

### Phase 5 — Hog 2.6 specialist via PPO (Null's sandbox)

Loop-validation exercise, not a main-ladder investment. Follow the author's recipe:

1. Initialize from **General**.
2. Opponent mix **40% original IL / 60% General**; decks 30% focus / 40% weighted / 30% hard.
3. Hog rewards + demonstrations → **specialist 1**.
4. Remove shaping, keep outcome + tower-health + overflow penalties → **specialist 2**.
5. Budget: one short PPO run on rented/free credits; skip the author's 8-GPU scale.

- [ ] `rejected_actions == 0` before accepting a rollout.
- [ ] First non-WAIT action time tracked.
- [ ] Elixir overflow and King Tower activation tracked.
- [ ] Replays reviewed, not just win rate.

**Exit:** specialist beats General >60–65% over 200+ mirrored games with no execution errors.

---

### Phase 6 — Null's live loop (memory reader + taps)

Prove the whole live stack on the maxed Null's account: reader → observation → policy → taps → ladder
queue → reconnect.

Use `.resource/daybreak-cr/play.py` as the architecture reference and `.resource/cr-memory-reader` for
the Null's state reader:
- `client.json` per client, a C reader built with the NDK, a build check, taps.
- `--queue` presses Battle on the ladder; `--watch` records human play.
- Read `docs/GAME_INTEGRATION.md` for touch/deal/elixir/server-turn timing.

- [ ] Reader produces elixir, hand, cycle, entities, tower HP, battle result at ≥20 Hz.
- [ ] Policy consumes the observation and issues legal taps.
- [ ] End-to-end latency measured; target <100 ms observation→tap.
- [ ] Hog specialist 2 plays N ladder games unattended on Null's.
- [ ] Reconnect and abort handling tested.

**Exit:** Hog bot climbs on Null's with stable timing and a positive win rate.

---

### Phase 7 — X-Bow 3.0 specialist on Null's

The main account is already maxed for X-Bow; this phase is a policy problem, not a card problem.

1. **Zero-shot first:** run General with the X-Bow deck on Null's ladder. The card representations are
   universal; measure before training anything.
2. **Data:** filter IL_Replay for X-Bow 3.0 games; build a cache; fine-tune IL on that subset. If the
   deck is rare, supplement with self-play.
3. **Prior art:** `.resource/ClashAI` (icebow in RoyaleSim), `.resource/FirstLight-Zero` (3.0 X-Bow
   self-play, 1.17M params, beat a 16.8M IL model in the mirror). Reuse reward/self-play lessons, but
   validate on the native engine.
4. **PPO:** deck-specific specialist phase (X-Bow placement, spell cycle, defensive Tesla/Knight
   usage), then remove shaping. Budget one short run.
5. **Meta:** test against common Null's matchups (EQ/Royal Delivery, cycle decks, evolutions). Be ready
   to adjust the build or switch control deck if X-Bow is not viable.

- [ ] X-Bow IL checkpoint trained on filtered data.
- [ ] X-Bow specialist beats General and trades evenly with Hog 2.6 on Null's.
- [ ] Matchup table on the target meta.
- [ ] X-Bow result on Null's ladder.

**Exit:** an X-Bow policy that beats the Hog specialist on Null's, with a documented matchup profile.

---

### Phase 8 — Null's readiness gate (do not skip)

Before spending on the main bridge, prove the policy and loop are worth porting:

- [ ] X-Bow wins >60% over 200+ mirrored games against the deck pool.
- [ ] X-Bow beats Hog specialist 2 over 100+ games.
- [ ] X-Bow climbs to a self-defined Null's ladder target (trophies or rank).
- [ ] Live loop stable for 100+ unattended games: no crashes, no illegal taps, reconnect works.
- [ ] Replay review shows proactive, human-like X-Bow play, not passive waiting.
- [ ] You (Grand Champion/Master) judge the decisions UC-caliber in assist/observation mode.

**Exit:** a written go/no-go. If no-go, improve the policy on Null's; do not start Phase 9.

---

### Phase 9 — Main-server memory bridge (deferred to the end)

Only after Phase 8 passes. This is the critical R&D: a reader for the **current** official build, plus
the observation/action adapter.

**9.1 Host**

| Host | Verdict |
| --- | --- |
| **Rooted ARM64 phone** (optional) | Best experience: native ARM, no translation, low latency, easy `/proc/pid/mem`. Buy used (~$60–100) only if the budget allows and MuMu is too painful |
| Windows + MuMu | The no-purchase default. x86_64 + ARM translation breaks Frida for `libg.so`; direct reads are the workaround |
| Vision | Fallback only: 100–300 ms, heavy |

**9.2 Reader**

1. Identify the official package, version, and `libg.so` base.
2. Find the `holder` / player-state chain: elixir, hand, cycle, tower HP, battle result.
3. Find the entity graph (`hpComponentAccessor` equivalent): units, sides, card IDs, positions, HP.
4. Find the command queue (optional; own taps can be tracked client-side).
5. Map into FirstLight's `ObservationV1`; verify field-by-field against recorded frames.
6. Add a build check and versioned parsing; fail closed on updates.

Use `daybreak-cr/play.py` for the public chains (`battle = base + manager_global + 0x248 + 0x90`,
roster `+0xa8`, ticks `+0x60/+0x64`) and `cr-memory-reader` for the RE method.

**9.3 Card catalog**

- FirstLight is pinned to Null's 15.535.13. Official 16.635 has newer evolutions/heroes.
- Extend `native_runner/data/competitive` for the new card/form IDs; fine-tune the policy.
- A perfect reader feeding a model that does not know Evo Giant or Hero Electro Wizard is still weak.

**9.4 Action executor**

- Two touches per play (hand slot → tile), minitouch/ADB.
- Use `GAME_INTEGRATION.md`: 21-tick execution, hand refill, server turns (R-8/R+13), first-touch lockout.
- Use the **`yes-delay`** branch (“Everlight”) for the real 20-tick card delay.

**9.5 Maintenance**

- Pattern scanning, not hardcoded offsets: anchor on function prologues/strings/vtables.
- Version registry + recorded memory dumps; diff between builds.
- Budget 1–3 days per major build; 1–2 weeks if battle structs change.
- Frida only for RE; passive native reads for live play.

**9.6 Validate**

- [ ] Reader stable at ≥20 Hz on the current official build.
- [ ] Observation matches live state on 50+ frames.
- [ ] Legal actions in friendly battles against a second account.
- [ ] End-to-end latency <100 ms; hard stop >150 ms.
- [ ] Reconnect/crash/update handling tested.
- [ ] Ranked games: legality, disconnects, win rate.

**Exit:** the X-Bow policy plays official matches with a stable reader and no illegal actions.

---

### Phase 10 — UC push on the main ladder (seasonal)

- [ ] Confirm deck forms and tower troop for the current season.
- [ ] Plan around Path of Legends resets.
- [ ] Operate in sessions; monitor latency, disconnects, rejected taps, loss streaks.
- [ ] Automation is approved; keep behavior plausible and accept residual detection risk.
- [ ] Track win rate by matchup; adjust weekly.
- [ ] Keep a rollback checkpoint and a plan if the account is banned.

**Exit:** UC reached and maintained, or the best rank documented with the stop reason.

---

### Phase 11 — Improve beyond the baseline (ongoing)

- Port the `yes-delay` Everlight model:
  `git fetch upstream yes-delay:yes-delay`, then read that branch's docs before switching.
- Expand the opponent league: self-play snapshots, deck pool, hard-deck weighting.
- Distill a smaller/faster policy if inference latency matters.
- Collect main-ladder replays and add them to IL.
- Re-evaluate the deck each season.

---

## 4. Fast-simulator track (optional, parallel)

Use `ClashAI`/`RoyaleSim` for cheap algorithm iteration, especially X-Bow reward design, because they
run on CPU without an Android guest. Do **not** treat their results as main-server evidence: different
physics, a real sim-to-real gap, no UC evidence. Promote only ideas that survive the native engine and
Null's ladder.

`FirstLight-Zero` is a useful X-Bow self-play/async-PPO reference, but it still needs the native engine
and a GPU; it is not a shortcut.

---

## 5. Effort and budget (adjusted to $50–200)

| Phase | Estimate |
| --- | --- |
| 1 (env) | 0.5 day, $0 |
| 2 (engine) | 1–2 weeks, $0 (Windows PC) |
| 3 (baselines) | 2–3 days, $0 |
| 4 (IL subset) | 1–2 weeks; 5–20 GPU-hours (~$5–20) |
| 5 (Hog PPO) | 1–3 weeks; free/student credits |
| 6 (Null's loop) | 2–4 weeks; $0 |
| 7 (X-Bow specialist) | 1–3 weeks; 10–50 GPU-hours (~$10–60) |
| 8 (readiness gate) | 1–2 weeks; $0 |
| 9 (main reader) | **4–12+ weeks**; $0–100 (phone optional) + monthly maintenance |
| 10 (UC push) | 1–3 seasons |
| **Null's path (1–8)** | **~3–6 months part-time** |
| **Through UC (1–10)** | **~9–18 months part-time** |

Skip the author-scale PPO cluster; this budget buys fine-tunes, not 8-GPU self-play.

---

## 6. Risk register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Scope creep into the main bridge too early | Months lost before a strong Null's bot | Phase 8 gate; defer Phase 9 explicitly |
| Null's ladder is dead/empty | No sandbox validation | Offline eval + friendlies; still train the policy |
| Hog PPO overfits a passive IL opponent | Fake progress | Replay review, first-action timing, non-WAIT counts |
| X-Bow not viable in the meta | Weak main deck | Matchup analysis; be ready to switch control deck |
| Budget overrun | Stop mid-project | Shipped checkpoints + subsets; free credits |
| **Phase 9:** main-client automation → ban | Lose the account | Accepted risk; session hygiene; backup plan |
| **Phase 9:** official build breaks offsets | Bot stops | Pattern scanning; version registry; monthly re-validation |
| **Phase 9:** new evolutions/heroes not in catalog | Model cannot represent them | Extend competitive data; fine-tune |
| **Phase 9:** reader takes longer than expected | Delays UC | Timebox; rooted ARM phone; daybreak anchors; vision fallback |
| **Phase 9:** sim-to-real gap (Null's ≠ main) | Offline wins do not transfer | Main replays; assist validation; retrain |
| **Phase 10:** Path of Legends reset | Progress wiped | Plan pushes within a season |

---

## 7. Do not do

- Do not start Phase 9 until the Phase 8 gate passes.
- Do not buy the rooted phone or read daybreak end-to-end before Null's works.
- Do not build a Hog 2.6 main account; Hog is a Null's sandbox.
- Do not assume Null's results transfer to the main ladder.
- Do not use vision as the primary main-server path.
- Do not expect `cr-memory-reader` to work on main; reuse its method, not its offsets.
- Do not use Cheat Engine on the Windows host expecting guest memory.
- Do not train X-Bow from scratch; fine-tune and use the prior art.
- Do not train on the full 252k dataset with this budget; subset first.
- Do not start PPO from scratch; `FirstLight-Zero` needed 1,450 rounds for one deck.
- Do not treat `ClashAI`/`RoyaleSim` results as main-server evidence.
- Do not switch to `yes-delay` until the baseline pipeline is reproducible.

---

## 8. Immediate next actions (Null's phase)

1. [ ] Run Phase 1: Python 3.12 env + `check_install.py` → `Ready: 5 models`.
2. [ ] Source the Null's Royale 15.535.13 APK and build the engine on Windows/MuMu.
3. [ ] Reproduce General vs IL and Hog 2 vs General; record the numbers.
4. [ ] Start the 2–5k replay cache subset and measure throughput.
5. [ ] Get the Hog live loop running on Null's with `cr-memory-reader`.
6. [ ] Run General zero-shot with the X-Bow deck on Null's before training it.
7. [ ] Set the Phase 8 readiness target (win rate + ladder rank).

---

## 9. Repo map

| Path | Purpose |
| --- | --- |
| `native_runner/` | Training + native engine + battle environment |
| `checkpoints/` | Five released models |
| `docs/getting_started.md` | Engine/APK installation |
| `docs/training.md` | Cache, IL, Linux AVD, PPO |
| `docs/training_history.md` | The five models and the Hog recipe |
| `checkpoints/README.md` | Checkpoint selection and evaluation protocol |
| `.resource/cr-memory-reader/` | Null's memory-reader method (active in Phase 6) |
| `.resource/daybreak-cr/` | Live-loop architecture; defer to Phase 9 (timing doc useful earlier) |
| `.resource/daybreak-cr/docs/GAME_INTEGRATION.md` | Touch, hand, elixir, server-turn rules |
| `.resource/ClashAI/`, `.resource/FirstLight-Zero/` | X-Bow prior art |
| `.resource/` | Other cloned reference repos, ignored by git |
