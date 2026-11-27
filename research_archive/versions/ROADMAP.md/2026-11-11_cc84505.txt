# Roadmap: Hog 2.6 → X-Bow 3.0 → Ultimate Champion

Status: planning
Owner: Raj
Primary repo: this repository (fork of `FirstLight_CR`; upstream code lives at the repo root)

**End goal:** reach **Ultimate Champion (UC) on Supercell's main ladder** with **X-Bow 3.0**.
**Stepping stones:** Hog 2.6 on Null's Royale → HF replay training → main-server X-Bow.

| Deck | Role | Account state |
| --- | --- | --- |
| Hog 2.6 | Proves the full training + live-play loop | Null's account: everything maxed (lvl 16 + evo/hero). Main: only Hog Rider is maxed; the rest of 2.6 is not. **Hog is a Null's-only sandbox.** |
| X-Bow 3.0 | Final UC deck | Main account: fully maxed (lvl 16 + evo/hero). Ready today. |

> Confirm the current X-Bow 3.0 build (2 evolutions + hero + tower troop) when Phase 8 starts. The
> card core is X-Bow, Tesla, Archers, Knight, Fireball, The Log, Skeletons, Ice Spirit; the
> evolution/hero slots are meta-dependent.

---

## 0. Project constraints (from the planning Q&A)

| Constraint | Answer | Effect on the plan |
| --- | --- | --- |
| Main account | X-Bow 3.0 maxed; Hog 2.6 incomplete; Grand Champion / Master | No account-building phase; X-Bow can go to ladder immediately after the bridge works |
| Null's account | Everything maxed, evolutions and heroes | Hog 2.6 is a safe, fully powered sandbox |
| Ban tolerance | **Full automation accepted on main** | Build the automation bridge; assist mode is validation, not the endpoint |
| Hardware | Windows PC; cloud budget only; no rooted Android device | MuMu is the engine host. A used rooted ARM phone (~$60–100) is the best Phase 7 purchase |
| Time | 5–15 h/week hands-on, tens of hours idle training | Timeline 6–12 months; favor the shipped checkpoints and short fine-tunes |
| Budget | ~$50–200 (student credits) | Zero-shot General/X-Bow first; one X-Bow IL fine-tune; PPO optional |
| Sequencing | Hog first, then X-Bow | Hog on Null's; X-Bow in parallel for the main ladder |

**Immediate implication:** do not spend money or weeks building a Hog 2.6 main account. Hog exists to
validate the loop on Null's. The main ladder is an X-Bow project.

---

## 1. Goal, and the gap between here and it

**FirstLight_CR is still the right training base.** It ships the only complete imitation-learning +
PPO pipeline, five checkpoints, 252k top-ladder replays, a native engine, and a Hog 2.6 specialist
that the author used to reach a high ladder rank. It is far ahead of the pure simulators
(`RoyaleSim`, `ClashAI`, `clash-royale-simulator`), the forks (`FirstLight-Zero`, `FL-Royale`), and the
live-client tools (`cr-memory-reader`, `daybreak-cr`) for *policy training*.

**But it is not a path to the main ladder by itself.** The engine is a patched **Null's Royale
15.535.13** private-server client, isolated from the network. There is no ranked/online/account code in
the repo. The model transfers to another client; the client integration does not.

**Three hard gates stand between this repo and UC:**

1. **Engine** (Phase 2) — get one native match running. Hard prerequisite for dataset training.
2. **Main-client bridge** (Phase 7) — observe the official client and act on it in real time. The
   **loop** is proven: `daybreak-cr`'s `play.py` and `docs/GAME_INTEGRATION.md` were measured on
   **official client build 160402012** in MuMu, and the script supports multiple clients. The **reader**
   (per-client `client.json`, addresses, build check) is private and not in any repo. Building that
   reader for the current official build is the main R&D task.
3. **Account + ToS** (Phase 0 / Phase 9) — the X-Bow account is UC-eligible, so this gate is risk, not
   progression. Automating the official client violates Supercell's ToS and can get the account
   permanently banned. Full automation is accepted here; the risk is explicit.

### Memory reading is the primary path, not vision

This is the corrected position after checking the reference repos:

| Source | Latency | Compute | Works on main? | Notes |
| --- | --- | --- | --- | --- |
| **Memory reader** (native / Frida / rooted guest) | ~20–50 ms sampling | Tiny (C reader) | **Yes, proven** by `daybreak-cr` on official build 160402012 | Needs per-build offsets and pointer chains; updates break it |
| Vision (screen capture + detection) | ~100–300 ms | Heavy (GPU/CPU) | Possible, unrooted | Fallback only; misreads in clutter; needs labeled data |
| Hybrid | 20–100 ms | Medium | Possible | Memory for hand/elixir, vision for arena if needed |

- `cr-memory-reader` targets **Null's Royale only** (package `nullsroyale.rel.free`, lead version
  13.300.6). Its offsets will not work on the official client. Its **reverse-engineering method** is
  the useful part: find `libg.so`, the `holder`, and the `hpComponentAccessor`, then build pointer
  chains.
- **Cheat Engine on the Windows host is the wrong layer.** MuMu is a VM and the game runs in the guest;
  a host-side CE scan sees emulator memory, not the guest's virtual address space. GameGuardian inside
  a rooted guest can discover values, but a production bot needs stable structures, not value scans.
  Budget for a custom reader.
- **Detection:** passive `/proc/pid/mem` reads are much stealthier than Frida injection or hooks;
  root/emulator may itself be detectable, and behavioral patterns (24/7 play, perfect timing) are the
  larger risk. Nothing here is guaranteed undetectable.

**Reality check:** reaching UC is a deployment, account, meta and operations problem as much as a
model problem. The training stack is necessary, not sufficient. The X-Bow account is ready; the
reader and the climb are the hard parts.

**The other hard truth:** the HF dataset alone does not start training. The cache builder replays every
match through the native engine. Phase 2 is a prerequisite for "dataset training", not optional.

---

## 2. What you already have

| Asset | State |
| --- | --- |
| Repository | Fork of `FirstLight_CR` at the repo root (upstream code + our additions) |
| Checkpoints | 5 models: IL, Active IL, General, Hog specialist 1, Hog specialist 2 |
| IL_Replay dataset | Complete: 252,238 matches, 17.8M actions, ~1.8 GB |
| Training code | IL cache builder, IL trainer, PPO cluster trainer, evaluator, policy service |
| Live-play blueprint | `.resource/daybreak-cr/play.py` + `docs/GAME_INTEGRATION.md` (official client measurements, taps, ladder queue) |
| Null's reader | `.resource/cr-memory-reader` (memory/Frida state capture for Null's) |
| X-Bow prior art | `.resource/ClashAI` (icebow, simulator), `.resource/FirstLight-Zero` (3.0 X-Bow self-play) |
| Local machine | Linux, 12 vCPU, 14 GB RAM, **no CUDA GPU**, 90 GB free, `/dev/kvm`, `adb` |
| Windows machine | MuMu host for engine build/run (the supported path) |
| Cloud budget | ~$50–200, student credits |
| Missing | Null's APK, official-client reader/offsets, GPU for training |

---

## 3. Phase plan

### Phase 0 — Decisions already made (keep this current)

- [x] End goal: UC on Supercell's main ladder, final deck X-Bow 3.0.
- [x] Hog 2.6: Null's-only sandbox; do not build it on main.
- [x] Main account: X-Bow 3.0 maxed; eligible for a UC push.
- [x] Risk: full automation accepted on main; ban risk acknowledged.
- [x] Budget: $50–200; use shipped checkpoints before renting GPUs.
- [ ] Season/date target for the UC push.
- [ ] Buy a used rooted ARM64 phone for Phase 7 (~$60–100), or commit to MuMu direct reads.

---

### Phase 1 — Local Python environment (half a day, no GPU needed)

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

You have a Windows PC, so use the supported path: Android 12 MuMu instance with root and ARM64
support, the original Null's Royale APK, JDK 17 + NDK 27.3 + build-tools 37.

1. Obtain `nulls-royale.apk` 15.535.13 arm64-v8a (user-supplied; the repo does not ship it). Let it
   update to content 15.535.86 in the lobby, then close it.
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

- Published General 73.30% vs IL; Hog specialist 2 88.03% vs General (760 mirrored games).
- Use argmax, mirrored sides, and the same first-action rule as the author.
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

**4b. Budget-aware full run**

- The dataset is top-ladder; filter for X-Bow 3.0 games when you start Phase 8. The full 252k cache is
  likely too large for this budget; use a 5–20k subset first.
- Fine-tune from the shipped IL checkpoint; do not train from scratch.
- 1 GPU (rented) is enough for a light fine-tune; `IL_WORLD_SIZE=1`.
- Cache storage: monitor; reserve 50–100 GB if you go larger.

- [ ] Subset cache counts and expert/executed actions look sane.
- [ ] Loss finite and decreasing; checkpoint reloads.
- [ ] New IL ≥ shipped IL on held-out tags; samples look human.

**Exit:** a reproducible IL checkpoint with measured cost per epoch.

---

### Phase 5 — Hog 2.6 specialist via PPO (Null's sandbox only)

This is a loop-validation exercise, not a main-ladder investment. Use the author's recipe:

1. Initialize from **General**.
2. Opponent mix **40% original IL / 60% General**; decks 30% focus / 40% weighted / 30% hard.
3. Hog rewards + demonstrations → **specialist 1**.
4. Remove shaping, keep outcome + tower-health + overflow penalties → **specialist 2**.
5. Budget: one short PPO run on rented/free GPU credits; do not chase the author's 8-GPU scale.

- [ ] `rejected_actions == 0` before accepting a rollout.
- [ ] First non-WAIT action time tracked.
- [ ] Elixir overflow and King Tower activation tracked.
- [ ] Replays reviewed, not just win rate.

**Exit:** specialist beats General >60–65% over 200+ mirrored games with no execution errors.

---

### Phase 6 — Null's Royale live loop (memory reader)

The goal is to prove the whole live stack on a maxed sandbox account: reader → observation → policy →
taps → ladder queue → reconnect.

Use `daybreak-cr/play.py` as the blueprint:
- `client.json` per client, a C reader built with the NDK, a build check, and tap injection.
- `.resource/cr-memory-reader` has a Null's state reader you can adapt.
- `--queue` presses Battle on the ladder; `--watch` records human play.

- [ ] Reader produces elixir, hand, cycle, entities, tower HP, battle result at ≥20 Hz.
- [ ] Policy consumes the observation and issues legal taps.
- [ ] End-to-end latency measured; target <100 ms observation→tap.
- [ ] Hog specialist 2 plays N ladder games unattended on Null's.
- [ ] Reconnect and abort handling tested.

**Exit:** Hog bot climbs on Null's with stable timing and a positive win rate.

---

### Phase 7 — Main-server memory bridge (the critical R&D, 4–12+ weeks)

Memory reading is the primary path. The loop is proven on the official client; your job is the reader
and the observation/action adapter for the **current** official build.

**7.1 Decide the host**

| Host | Verdict |
| --- | --- |
| **Rooted ARM64 phone** | Best. Native ARM, no translation, low latency, easy `/proc/pid/mem` reads. Buy a used one (~$60–100) |
| Windows + MuMu | Possible. x86_64 + ARM translation breaks Frida for `libg.so`; direct memory reads are the workaround (as `cr-memory-reader` does on Windows). Accept higher setup pain |
| Vision on any device | Fallback only: 100–300 ms, heavy, misreads |

**7.2 Build the reader**

1. Identify the official package, version, and `libg.so` base per process.
2. Find the `holder` / player-state chain: elixir, hand, cycle, tower HP, battle result.
3. Find the entity graph (`hpComponentAccessor` equivalent): units, sides, card IDs, positions, HP.
4. Find the command queue: own and opponent plays, issue/execute ticks.
5. Map everything into FirstLight's `ObservationV1` contract; verify field-by-field against recorded
   frames and replays.
6. Add a build check and versioned parsing; fail closed when the client updates.

**7.3 Build the action executor**

- Two touches per play (hand slot → tile), minitouch/ADB injection.
- Use `docs/GAME_INTEGRATION.md` for the measured rules: 21-tick execution, hand refill, touch timing,
  server turns (R-8 / R+13), first-touch lockout.
- The **`yes-delay`** branch ("Everlight") is trained with the real 20-tick (1 s) card delay; use it
  for live play.

**7.4 Validate**

- [ ] Reader runs on the current official build at ≥20 Hz with stable addresses.
- [ ] Observation matches live state on 50+ recorded frames.
- [ ] Policy issues legal actions in friendly battles against a second account.
- [ ] End-to-end latency measured: reader + inference + tap. Target <100 ms; hard stop >150 ms.
- [ ] Reconnect, crash, and client-update handling tested.
- [ ] Ranked games: monitor legality, disconnects, and win rate.

**Exit:** the X-Bow policy plays official matches with a stable reader and no illegal actions.

---

### Phase 8 — X-Bow 3.0 specialist (main deck)

The main account is already maxed for X-Bow, so this is a policy problem, not a card problem.

1. **Zero-shot first:** run General with the X-Bow deck on Null's ladder. The card representations are
   universal; measure before training anything.
2. **Data:** filter IL_Replay for X-Bow 3.0 games; build a cache; fine-tune IL on that subset. If the
   deck is rare, supplement with self-play.
3. **Prior art:** `.resource/ClashAI` (icebow in RoyaleSim), `.resource/FirstLight-Zero` (3.0 X-Bow
   self-play, 1.17M params, beat a 16.8M IL model in the mirror). Reuse reward/self-play lessons, but
   validate on the native engine.
4. **PPO:** deck-specific specialist phase (X-Bow placement, spell cycle, defensive Tesla/Knight
   usage), then remove shaping. Budget one short run.
5. **Meta:** test against the current main-ladder matchups (EQ/Royal Delivery, cycle decks, evolutions).
   Be ready to adjust the build or switch control deck if X-Bow is not viable.

- [ ] X-Bow IL checkpoint trained on filtered data.
- [ ] X-Bow specialist beats General and trades evenly with Hog 2.6 on Null's.
- [ ] Matchup table on the target meta.
- [ ] X-Bow result on Null's ladder.

**Exit:** an X-Bow policy good enough for the main ladder, with a documented matchup profile.

---

### Phase 9 — UC push on the main ladder (seasonal)

- [ ] Confirm the deck forms and tower troop for the current season.
- [ ] Plan around Path of Legends resets.
- [ ] Operate in sessions: monitor latency, disconnects, rejected taps, loss streaks.
- [ ] Automation is approved; keep behavior plausible (timing variance, breaks) and accept residual
      detection risk.
- [ ] Track win rate by matchup; adjust the build/policy weekly.
- [ ] Keep a rollback checkpoint and a plan if the account is banned.

**Exit:** UC reached and maintained, or the best rank documented with the stop reason.

---

### Phase 10 — Improve beyond the baseline (ongoing)

- Port the `yes-delay` Everlight model:
  `git fetch upstream yes-delay:yes-delay`, then read that branch's docs before switching.
- Expand the opponent league: self-play snapshots, deck pool, hard-deck weighting.
- Distill a smaller/faster policy if inference latency matters.
- Collect main-ladder replays and add them to IL (domain shift is real).
- Re-evaluate the deck each season.

---

## 4. Fast-simulator track (optional, parallel)

Use `ClashAI`/`RoyaleSim` for cheap algorithm iteration, especially X-Bow reward design, because they
run on CPU without an Android guest. Do **not** treat their results as main-server evidence: different
physics, a real sim-to-real gap, and no UC evidence. Promote only ideas that survive the native engine
and Null's ladder.

`FirstLight-Zero` is a useful X-Bow self-play/async-PPO reference, but it still needs the native engine
and a GPU; it is not a shortcut.

---

## 5. Effort and budget (adjusted to $50–200)

| Item | Estimate |
| --- | --- |
| Phase 1 (env) | 0.5 day, $0 |
| Phase 2 (engine) | 1–2 weeks, $0 (Windows PC) |
| Phase 3 (baselines) | 2–3 days, $0 |
| Phase 4 (IL subset) | 1–2 weeks; 5–20 GPU-hours (~$5–20) |
| Phase 5 (Hog PPO) | 1–3 weeks; use free/student credits |
| Phase 6 (Null's loop) | 2–4 weeks; $0 |
| Phase 7 (main reader) | **4–12+ weeks**; $0–100 (used rooted phone) |
| Phase 8 (X-Bow) | 1–3 weeks; 10–50 GPU-hours (~$10–60) |
| Phase 9 (UC push) | 1–3 seasons |
| Rooted ARM phone | $60–100 (recommended) |

Realistic total: **6–12 months part-time**, dominated by the reader and the climb. Skip the
author-scale PPO cluster; this budget buys fine-tunes, not 8-GPU self-play.

---

## 6. Risk register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Main-client automation → permanent ban | Lose the account | Full automation accepted; use session hygiene; keep a backup plan |
| Official client update breaks offsets | Bot stops | Versioned parser; build check; fast recalibration; vision fallback |
| Reader reverse-engineering takes longer than expected | Delays UC | Timebox Phase 7; consider a rooted ARM phone; reuse `daybreak-cr` measurements |
| MuMu x86_64 + ARM translation breaks Frida | No reader | Direct `/proc/pid/mem` reads; or buy a rooted ARM device |
| X-Bow 3.0 not viable in the current meta | Cannot reach UC with the deck | Matchup analysis; be ready to switch control deck |
| Sim-to-real gap (Null's ≠ main) | Offline wins do not transfer | Assist/ranked validation; collect main replays |
| Path of Legends season reset | Progress wiped | Plan pushes within a season |
| Passive policy / fake win rate | Looks good, loses on ladder | Replay review, first-action timing, non-WAIT counts |
| Budget overrun | Stop mid-project | Shipped checkpoints + subsets; free credits |
| Account rank already Grand Champion/Master | Close to UC; expectations high | Validate decisions in assist mode before automating |

---

## 7. Do not do

- Do not build a Hog 2.6 main account; Hog is a Null's sandbox.
- Do not assume Null's results transfer to the main ladder.
- Do not use vision as the primary path; memory reading is faster, cheaper, and proven on the official client.
- Do not expect `cr-memory-reader` to work on main; it targets Null's. Reuse its method, not its offsets.
- Do not use Cheat Engine on the Windows host expecting guest memory; use an in-guest reader or a native ARM device.
- Do not train X-Bow from scratch; fine-tune and use the existing prior art.
- Do not train on the full 252k dataset with this budget; subset first.
- Do not start PPO from scratch; `FirstLight-Zero` needed 1,450 rounds for one deck.
- Do not treat `ClashAI`/`RoyaleSim` results as main-server evidence.
- Do not switch to `yes-delay` until the baseline pipeline is reproducible.

---

## 8. Immediate next actions

1. [ ] Run Phase 1: Python 3.12 env + `check_install.py` → `Ready: 5 models`.
2. [ ] Source the Null's Royale 15.535.13 APK and build the engine on Windows/MuMu.
3. [ ] Reproduce General vs IL and Hog 2 vs General; record the numbers.
4. [ ] Read `daybreak-cr/docs/GAME_INTEGRATION.md` end-to-end before writing the reader.
5. [ ] Decide Phase 7 host: used rooted ARM phone vs MuMu direct reads.
6. [ ] Run General zero-shot with the X-Bow deck on Null's; measure before training.
7. [ ] Start the 2–5k replay cache subset and measure throughput.

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
| `.resource/daybreak-cr/` | Live-play architecture and official-client timing measurements |
| `.resource/daybreak-cr/docs/GAME_INTEGRATION.md` | Touch, hand, elixir, server-turn rules |
| `.resource/cr-memory-reader/` | Null's memory-reader method (not main-compatible) |
| `.resource/ClashAI/`, `.resource/FirstLight-Zero/` | X-Bow prior art |
| `.resource/` | Other cloned reference repos, ignored by git |
