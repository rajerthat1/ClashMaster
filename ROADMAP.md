# Roadmap: Hog 2.6 → X-Bow 3.0 → Ultimate Champion

Status: planning
Owner: Raj
Primary repo: this repository (fork of `FirstLight_CR`; upstream code lives at the repo root)

**End goal:** reach **Ultimate Champion (UC) on Supercell's main ladder** with **X-Bow 3.0**.
**Stepping stones:** Hog 2.6 → HF replay training → simulator work → Null's Royale ladder → main-server
Hog → main-server X-Bow.

| Deck | Role | Why |
| --- | --- | --- |
| Hog 2.6 | First specialist / proof of the whole loop | The author's HoF-tested deck; fastest path to a working end-to-end bot |
| X-Bow 3.0 | Final specialist | Your actual UC target; different win condition, needs its own data and PPO run |

> X-Bow 3.0 build: confirm the current meta forms (2 evolutions + hero + tower troop) when you start
> Phase 8. The card core is X-Bow, Tesla, Archers, Knight, Fireball, The Log, Skeletons, Ice Spirit;
> the evolution/hero slots are meta-dependent.

---

## 0. The goal, and the gap between here and it

**FirstLight_CR is still the right training base.** It ships the only complete imitation-learning +
PPO pipeline, five checkpoints, 252k top-ladder replays, a native engine, and a Hog 2.6 specialist
that the author used to reach a high ladder rank. It is far ahead of the pure simulators
(`RoyaleSim`, `ClashAI`, `clash-royale-simulator`), the forks (`FirstLight-Zero`, `FL-Royale`), and the
live-client tools (`cr-memory-reader`, `daybreak-cr`) for *policy training*.

**But it is not a path to the main ladder.** The engine is a patched **Null's Royale 15.535.13**
private-server client, isolated from the network. There is no ranked/online/account code in the repo.
The author's Hall of Fame result is author-reported on an account and is not reproduced by any script.
The model transfers to another client; the client integration does not.

**Three hard gates stand between this repo and UC:**

1. **Engine** (Phase 2) — get one native match running. Hard prerequisite for dataset training.
2. **Main-client bridge** (Phase 7) — observe the official client and act on it in real time. No repo
   here implements this. `daybreak-cr/play.py` shows the architecture (emulator, memory reader, taps,
   ladder queue), but its live readers are private and per-client. This is the largest unknown.
3. **Account + ToS** (Phase 0 / Phase 9) — UC needs an eligible account with level-15 cards,
   evolutions and a tower troop. Automating the official client violates Supercell's ToS and can get
   the account permanently banned. Decide **assist vs automation** explicitly before building.

**Reality check:** reaching UC is a deployment, account, meta and operations problem as much as a
model problem. The training stack is necessary, not sufficient. A perfect policy on an underleveled
account still loses; a strong policy with a 300 ms perception loop still loses to a fast player.

**The other hard truth:** the HF dataset alone does not start training. The cache builder replays every
match through the native engine to build observations and expert actions. Phase 2 is a prerequisite
for "dataset training", not an optional step.

---

## 1. What you already have

| Asset | State |
| --- | --- |
| Repository | Fork of `FirstLight_CR` at the repo root (upstream code + our additions) |
| Checkpoints | 5 models: IL, Active IL, General, Hog specialist 1, Hog specialist 2 |
| IL_Replay dataset | Complete: 252,238 matches, 17.8M actions, ~1.8 GB |
| Training code | IL cache builder, IL trainer, PPO cluster trainer, evaluator, policy service |
| Reference: Null's live play | `.resource/daybreak-cr/play.py` (emulator + memory reader + taps + ladder queue) |
| Reference: Null's reader | `.resource/cr-memory-reader` (Frida/memory state for Null's Royale) |
| Reference: X-Bow prior art | `.resource/ClashAI` (icebow, simulator), `.resource/FirstLight-Zero` (3.0 X-Bow self-play) |
| Machine | Linux, 12 vCPU, 14 GB RAM, **no CUDA GPU**, 90 GB free, `/dev/kvm`, `adb` |
| Python | System 3.15 only; repo wants **3.12** |
| Missing | Android SDK/emulator, Null's APK, Windows/MuMu host, **main-client bridge**, **UC-eligible account** |

---

## 2. Phase plan

### Phase 0 — Pin the goal and accept the risk (half a day)

Write these down and keep them current:

- [ ] End goal: **UC on Supercell's main ladder**, final deck **X-Bow 3.0**, season/date target.
- [ ] Stepping-stone success: Hog 2.6 wins on Null's ladder before touching the main account.
- [ ] Account audit: current King level, card levels (need 15 for the UC deck), evolutions, hero,
      tower troop, gold/wild cards needed. If it is not UC-eligible, add an acquisition plan.
- [ ] Deployment mode decision: **manual assist** (safe) vs **automation** (ban risk). If automation,
      write down the accepted risk and the account you are willing to lose.
- [ ] Budget cap for cloud GPU + engine host + account progression.
- [ ] Baseline checkpoint: Hog specialist 2 for strength, specialist 1 for proactive play.

**Exit:** target, account status, deployment mode and risk acceptance are written above.

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

**Exit:** all five checkpoints load and hashes verify on CPU.

---

### Phase 2 — Get a native engine running (1–2 weeks, biggest early risk)

You need an Android 12 ARM64-capable guest, the original Null's Royale APK, an offline APK build,
and a control channel. Choose one host:

| Option | Pros | Cons |
| --- | --- | --- |
| Windows + MuMu (author's path) | Documented, supported, scripts work as-is | Needs a Windows machine with virtualization; not this Linux box |
| Linux + Android emulator + prepared AVD | Repo documents it (`docs/training.md` §6) | Needs an **ARM64-capable** image on x86; stock x86 images cannot run the ARM64 libs |
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

**Exit:** a single engine process accepts commands and completes a match. Everything downstream is
engine-bound.

**Fallback:** do not try to "just train on parquet" — the observation contract comes from the native
engine. If the engine cannot run, rent a Windows box, buy a cheap rooted ARM device, or accept a
simulator-only project with a known sim-to-real gap.

---

### Phase 3 — Reproduce the published baselines (2–3 days after engine works)

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

- The published numbers used **argmax**, mirrored sides, and a forced first action for original IL if
  it had not acted by 15 s. Reproduce the conditions or the comparison is meaningless.
- Inspect `matches.jsonl`: wins, `truncated`, `rejected_actions`, `actions_by_owner`, first non-WAIT
  action time. A high win rate against a passive opponent is the documented trap.
- [ ] General beats IL in the expected ballpark.
- [ ] Hog 2 beats General clearly (>75% over 40 games).
- [ ] Rejections = 0, truncations ≈ 0.

**Exit:** you trust the evaluator. Record checkpoint hashes, match config, seed and decoding mode.

---

### Phase 4 — Imitation learning from the HF dataset (1–3 weeks)

**4a. Subset smoke run (first 2–3 days)**

1. Pick 2,000–5,000 replay tags (one per line → `runs/train-tags.txt`).
2. Build the cache on 1 engine and measure **replays/hour/engine**:
   ```bash
   python -m native_runner.training.v4.cache_builder <dataset-dir> runs/il-subset-cache \
     --ports 26789 --source-count 1 --resident-slots 1 --verify-roundtrip
   ```
3. Train 1–2 epochs on CPU to prove the pipeline and watch loss:
   ```bash
   bash native_runner/training/v4/launch_stateful_il.sh \
     --index runs/il-subset-cache/index.json --train-tags runs/train-tags.txt \
     --output-dir runs/il-subset --epochs 1 --max-groups 1 --save-every-groups 1
   ```
4. Evaluate against the shipped IL.

**4b. Full run (rented GPU host)**

- Use 4a's throughput to decide whether the full 252k reconstruction is days or months. Use multiple
  engine ports or `launch_cache_partition.sh`; monitor cache size (it can dwarf the 1.8 GB parquet).
- Fine-tune from the shipped IL checkpoint before trying from scratch.
- 1–4 GPUs, `IL_WORLD_SIZE` = GPU count, `CR_IL_SHARDS_PER_RANK` tuned to keep ranks busy.

- [ ] Subset cache counts and expert/executed actions look sane.
- [ ] Subset IL loss is finite and decreases; checkpoint reloads.
- [ ] Full cache size and wall-clock measured and written here.
- [ ] New IL ≥ shipped IL on held-out tags; sampled games look human, not passive.

**Exit:** a reproducible IL checkpoint with a measured cost per epoch.

---

### Phase 5 — Hog 2.6 specialist via PPO (2–6 weeks, GPU + engine cluster)

Follow the author's recipe from `docs/training_history.md`:

1. Initialize from **General**, not IL.
2. Fixed-opponent mix **40% original IL / 60% General**; opponent decks 30% focus / 40% weighted /
   30% hard.
3. Hog rewards + Hog demonstrations → **specialist 1**.
4. Remove all Hog-specific shaping, keep terminal outcome + tower-health + elixir-overflow penalties,
   fresh optimizer, continue PPO → **specialist 2**.
5. Author's scale was 1,528 concurrent matches and 8 GPUs. Start at 1 engine/guest and scale.
   ```bash
   python -m native_runner.training.v4.build_ppo_deck_pool \
     --dataset-root ./datasets --output ./runs/deck-pool.json --minimum-uses 5
   bash native_runner/training/v4/launch_ppo_engine_node.sh runs/node0 0 1
   bash native_runner/training/v4/launch_ppo_multinode_training.sh runs/ppo-hog runs/topology.json 1 40
   ```

Guardrails:

- [ ] `rejected_actions == 0` before accepting a rollout.
- [ ] First non-WAIT action time tracked — no 196-second openings.
- [ ] Elixir overflow and accidental King Tower activation tracked.
- [ ] Replays + hands-on play, not just win rate.

**Exit:** specialist beats starting General >60–65% over 200+ mirrored games with no execution errors,
and replays show Hog pressure.

---

### Phase 6 — Null's Royale ladder: the first live loop (sandbox, not the goal)

Null's is where you prove the full loop — perception, decisions, taps, ladder queue, reconnect — on an
account you do not care about. The training engine is offline; live play is a separate bridge.

Closest prior art: `.resource/daybreak-cr/play.py`. It boots an emulator, installs a per-client memory
reader, runs the model, taps, and `--queue` presses Battle on the ladder. The reader and addresses are
private per client (`~/cr-clients/<name>`); `.resource/cr-memory-reader` has a Null's state reader you
can adapt.

- [ ] Choose the bridge architecture: memory reader (low latency, per-build offsets) vs vision.
- [ ] Run Hog specialist 2 on Null's ladder, first as manual assist, then `--queue` automation.
- [ ] Log every match: decisions, latency, rejected taps, disconnects, result.
- [ ] Measure end-to-end latency; target <100 ms from observation to tap.
- [ ] Climb a meaningful number of games without crashes or illegal actions.

**Exit:** Hog policy plays Null's ladder unattended for N games with stable timing and a positive win
rate. This validates the loop; it does not prove main-server performance.

---

### Phase 7 — Main-server bridge (the critical unknown, 4–12+ weeks)

This is the phase that actually decides whether UC is reachable. Nothing in the cloned repos does it.

**Observation source — pick one:**

| Approach | Pros | Cons |
| --- | --- | --- |
| Memory reader on rooted Android | Lowest latency, exact elixir/hand/entities | Official-client obfuscation, offsets break every update, high maintenance, detection surface |
| Vision (screen capture + detection) | Works unrooted, client-agnostic | 100–300 ms latency, misreads in clutter, needs a labeled dataset |
| Hybrid (memory for hand/elixir, vision for arena) | Best accuracy/latency compromise | Two systems to maintain |

**Action source:** ADB/minitouch taps with the game's real timing. The **`yes-delay` branch**
("Everlight") is the model trained with the real 20-tick (1 s) card delay; use it for live play.

**Plan:**

1. Build the client folder (`client.json` + reader + build check) modeled on `daybreak-cr`.
2. Get a reader or vision pipeline that produces the same observation contract the policy expects;
   verify against recorded frames/memory dumps.
3. Measure decision latency end-to-end on the actual device/emulator. If >150 ms, optimize or stop.
4. Test in friendly/training battles and against a second account before ranked.
5. Validate manual assist first: the model recommends, a human taps. This has no ban risk and tells you
   whether the policy is actually UC-caliber.
6. Only after assist proves out, consider automation — with the account risk accepted in Phase 0.

- [ ] Policy consumes official-client observations and produces legal actions.
- [ ] Latency budget met; reconnect/abort handling tested.
- [ ] Assist mode produces UC-level decisions over a meaningful sample.
- [ ] Go/no-go recorded for automation.

**Exit:** either a working main-client bridge, or a written decision to stop at assist mode/offline.

---

### Phase 8 — Transfer to X-Bow 3.0 (2–6 weeks)

Hog and X-Bow share card/entity representations, but the win condition and macro plan are different.
Do not expect a zero-shot swap.

1. **Data:** filter IL_Replay for X-Bow 3.0 games (deck pool / tags); build a cache; fine-tune the IL
   model on that subset. If the deck is rare in the data, supplement with self-play.
2. **Prior art:** `.resource/ClashAI` trains icebow in RoyaleSim; `.resource/FirstLight-Zero` trained a
   1.17M-param 3.0 X-Bow model with self-play and beat a 16.8M IL model in the mirror. Reuse their
   reward and self-play lessons, but validate on the native engine.
3. **PPO:** start from General/IL, run a deck-specific specialist phase (X-Bow placement and
   spell-cycle rewards), then remove shaping.
4. **Meta:** X-Bow 3.0 must survive the current main-ladder meta (EQ/Royal Delivery, cycle decks,
   evolutions). Check matchup win rates; be ready to adjust the build or abandon the deck.
5. **Evaluation:** X-Bow vs Hog specialist, X-Bow vs General, X-Bow vs the deck pool; then Null's ladder.

- [ ] X-Bow IL checkpoint trained on filtered data.
- [ ] X-Bow specialist beats General and trades evenly with Hog 2.6.
- [ ] Matchup table on the target meta; bad matchups identified.
- [ ] Null's ladder result for X-Bow.

**Exit:** an X-Bow policy good enough to take to the main ladder, with a documented matchup profile.

---

### Phase 9 — UC push on the main ladder (seasonal, ongoing)

- [ ] Confirm account eligibility and deck levels before the season starts.
- [ ] Plan around Path of Legends resets; a push that spans a reset wastes progress.
- [ ] Operate in sessions: monitor latency, disconnects, rejected taps, and loss streaks.
- [ ] If automating: keep behavior human-plausible (timing, breaks); accept that detection is still
      possible. If assisting: the human executes, the model decides.
- [ ] Track win rate by matchup and adjust the build/policy weekly.
- [ ] Keep a rollback checkpoint and a fresh account strategy if a ban happens.

**Exit:** UC reached and maintained, or the best rank documented with the reason for stopping.

---

### Phase 10 — Improve beyond the baseline (ongoing)

- Port the `yes-delay` Everlight model (larger net, real 1 s delay):
  `git fetch upstream yes-delay:yes-delay`, then read that branch's docs before switching.
- Expand the opponent league: self-play snapshots, deck pool, hard-deck weighting.
- Distill a smaller/faster policy if inference latency matters on the deployment host.
- Collect your own main-ladder replays and add them to IL (the domain shift is real).
- Re-evaluate the deck choice each season; UC with X-Bow may require a different control deck.

---

## 3. Fast-simulator track (optional, parallel)

Use `ClashAI`/`RoyaleSim` for cheap algorithm iteration — especially for X-Bow reward design and
self-play experiments — because they run on CPU without an Android guest. Do **not** use their results
as evidence of main-server strength: they have a real sim-to-real gap, different physics, and no UC
evidence. Promote only ideas that survive the native engine and Null's ladder.

`FirstLight-Zero` is a useful reference for 3.0 X-Bow self-play and async PPO, but it still needs the
same native engine and a GPU; it is not a shortcut.

---

## 4. Effort and budget (rough)

| Item | Estimate |
| --- | --- |
| Phase 1 (env) | 0.5 day, $0 |
| Phase 2 (engine) | 1–2 weeks, $0–100 (device/VM) |
| Phase 3 (baselines) | 2–3 days, ~$10 |
| Phase 4 (IL) | 1–3 weeks; 20–100 GPU-hours (~$20–150) |
| Phase 5 (Hog PPO) | 2–6 weeks; 100–1,000+ GPU-hours (~$200–2,000) |
| Phase 6 (Null's ladder) | 2–6 weeks; mostly engineering |
| Phase 7 (main bridge) | **4–12+ weeks**, unbounded R&D |
| Phase 8 (X-Bow) | 2–6 weeks; 50–300 GPU-hours |
| Phase 9 (UC push) | 1–3 seasons, depending on account/meta |
| Cache storage | measure in 4a; reserve 50–100 GB |

Realistic total: **6–12 months part-time**, dominated by the main-client bridge and the UC climb, not
by model training.

---

## 5. Risk register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Main-client automation → permanent ban | Lose the account and the project | Manual assist first; explicit risk acceptance; separate account |
| Account not UC-eligible (levels/evolutions) | Cannot compete regardless of policy | Audit in Phase 0; budget acquisition or use a different account |
| No main-client bridge exists in any repo | Blocks the end goal | Treat Phase 7 as the primary R&D risk; timebox it; fall back to assist |
| Official client updates break offsets/reader | Bot stops working | Vision fallback; version pinning; fast re-calibration |
| Sim-to-real gap (Null's ≠ main) | Offline wins do not transfer | Main-ladder logs, assist validation, periodic fine-tune |
| X-Bow 3.0 not viable in the current meta | Cannot reach UC with the deck | Matchup analysis in Phase 8; be ready to switch control deck |
| Path of Legends season reset | Progress wiped | Plan pushes within a season; track reset dates |
| Passive policy / fake win rate | Looks good, loses on ladder | Replay review, first-action timing, non-WAIT counts |
| Full cache enormous/slow | Weeks lost | Subset first; measure replays/hour and bytes/replay |
| Private-server ladder unavailable/empty | No sandbox | Fall back to offline evaluation + friendlies |
| Model/repo version drift | Unreproducible runs | Pin commit, checkpoint hashes, `.env`, seeds, configs |

---

## 6. Do not do

- Do not assume Null's Royale results transfer to the main ladder.
- Do not automate the main account before assist mode proves the policy is UC-caliber.
- Do not train X-Bow from scratch; fine-tune and use the existing prior art.
- Do not ignore account levels; a good policy on level-12 cards is not a UC policy.
- Do not start PPO from scratch. `FirstLight-Zero` needed 1,450 rounds for one deck.
- Do not train on the full dataset before measuring cache throughput on a subset.
- Do not treat `ClashAI`/`RoyaleSim` results as main-server evidence.
- Do not switch to `yes-delay` until the baseline pipeline is reproducible.
- Do not optimize for win rate against a passive IL opponent.

---

## 7. Immediate next actions

1. [ ] Update Phase 0: UC target, account audit, assist-vs-automation decision, risk acceptance.
2. [ ] Run Phase 1: Python 3.12 env + `check_install.py` → `Ready: 5 models`.
3. [ ] Source the Null's Royale 15.535.13 APK and pick an engine host.
4. [ ] Build/install the offline engine and pass the demo-replay smoke test.
5. [ ] Reproduce General vs IL and Hog 2 vs General; write the numbers back into this file.
6. [ ] Start the 2–5k replay cache subset and measure throughput.
7. [ ] Read `daybreak-cr/play.py` and `cr-memory-reader` before designing the live bridge.

---

## 8. Repo map

| Path | Purpose |
| --- | --- |
| `native_runner/` | Training + native engine + battle environment |
| `checkpoints/` | Five released models |
| `docs/getting_started.md` | Engine/APK installation |
| `docs/training.md` | Cache, IL, Linux AVD, PPO |
| `docs/training_history.md` | The five models and the Hog recipe |
| `checkpoints/README.md` | Checkpoint selection and evaluation protocol |
| `.resource/daybreak-cr/` | Live-play architecture (emulator, memory reader, taps, ladder queue) |
| `.resource/cr-memory-reader/` | Null's Royale memory-state reader |
| `.resource/ClashAI/`, `.resource/FirstLight-Zero/` | X-Bow prior art |
| `.resource/` | Other cloned reference repos, ignored by git |
