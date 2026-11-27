# Roadmap: X-Bow 3.0 → live bot → Ultimate Champion

Status: **active — native-engine route (Option E)**, revised 2026-11-12
Owner: Raj
Primary repo: this repository (fork of `FirstLight_CR`)
Engine: **real Clash Royale engine, headless via the FirstLight probe/runner** (target client 16.635.1)
Training engine: native resident engines; **crforge is fallback/CI only** — see [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md)
Model: **X-Bow 3.0 specialist** (FirstLight-Zero recipe) primary; V4/Everlight as generalist, opponent, and fallback
Data: self-play + live captures. **`IL_Replay` is retired as a training corpus.**

**End goal:** reach **Ultimate Champion on Supercell's main ladder** with **X-Bow 3.0**.

**Next milestone (about 2–3 months after the 16.635 probe is usable):** a **scripted X-Bow bot playing live on Null's 16.635.1**, then a **learned X-Bow bot that beats it** and is ready for a main-ladder canary. The probe port is the long pole; the 15.535 lab and crforge fallback keep moving in parallel.

---

## 0. Decision record

### Why the native engine (E), not crforge (D)

| Route | What it is | Fidelity | Throughput | Main cost |
| --- | --- | --- | --- | --- |
| **E. Native engine** | Real `libg.so` battle engine stepped headless through the FirstLight probe/runner | **Exact** | Near FirstLight: ~10 matches/s aggregate (published) | Porting the probe to 16.635.1 |
| D. crforge | Java reimplementation behind the same runner | Small but real rule gaps | Raw ~8k decisions/s at 256 envs; bridge 20–30 decisions/s | Open-ended `observe-rich` fidelity |
| B. Live only | Reader/executor, frozen weights | Exact | 1× real time | No training signal |

**E is chosen.** It is the September video path: reverse-engineer the 16.635.1 binary, port the probe, drive the real engine headless. It removes the fidelity problem instead of managing it, and FirstLight-Zero already proves an X-Bow specialist can be trained this way.

crforge is **not deleted**. It is demoted to a deterministic Linux fallback and CI/parity oracle. Its remaining work is in [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md); it is not on the critical path.

### What “fast” means

A 5-minute match is about 6,000 ticks at 20 Hz. “Fast” is measured two ways:

| Measure | Meaning | Benchmark |
| --- | --- | --- |
| **Per-slot latency** | Wall-clock for one match in one resident slot | ~1.6 s (~190× real time) |
| **Aggregate throughput** | Matches completed per second across all slots | **~10 matches/s (~100 ms/match)** |

The last is the useful number. FirstLight-Zero's published run did 1,024 episodes per 104 s (~9.85 matches/s) and ~1.5M matches in 43.3 hours; its shipped example uses 2 engine processes × 8 resident slots.

- **Target:** at least half of FirstLight's published aggregate throughput (~5 matches/s) on the available hardware; ~10 matches/s is the goal.
- **Stretch:** ~50 ms aggregate per match (about 2× the published setup); not a requirement.
- **Not acceptable:** real-time 3–5 minute matches as the training loop, or a cloud fleet of Android emulators.
- A single match finishing in 50 ms is a 6,000× real-time target; that is cluster scale, not laptop scale.

**Measured on the A51, 2026-11-13** (16.635.1, single probe process):

| Path | Slots | Per match | Matches/s |
| --- | ---: | ---: | ---: |
| Engine only, one connection, one `step` | 1 | 0.54 s (6,068 ticks) | **1.84** |
| Ordinary control, one `step 20` per call | 1 | 5.81 s | 0.17 |
| Decision loop (observe + play both owners + advance 22) | 1 | 12.99 s | 0.077 |
| Resident batch channel, 8 slots × 600 ticks × 5 (empty) | 8 | — | **2.36** |
| Resident batch channel, 8 slots × 600 ticks × 5 + real actions | 8 | — | **1.73** (28 actions/s) |
| Scripted X-Bow self-play, decisions every 20 ticks | 1 | 13.5–14.4 s | ~0.07 |

The engine is not the bottleneck: per-command transport (~19 ms via `adb
forward`) and the per-batch observation envelope are. One phone process is a
~1.7–2.4 matches/s lane (empty vs real actions), so 5–10 matches/s aggregate
needs 3–6 engine lanes (wider PC/cloud hosts), not a single device. See FINDINGS
§3.17–3.18 and the real-action re-measurement in §3.23.

### Engine decision tree

Run this in Phase 0 with measurements, not opinions.

1. **Can the native engine step headless while paused?** (`step N`, `advance-native N`, no rendering.)
   - Yes → measure matches/s and decisions/s. Continue.
   - No → only real-time play is available; crforge becomes the trainer and native is the live oracle.
2. **Aggregate throughput ≥ 5 matches/s** → native is the primary trainer. crforge is CI only.
3. **1–5 matches/s** → native is the live/eval engine; crforge handles bulk PPO.
4. **< 1 match/s** → crforge is the primary trainer; native is the live oracle.
5. **Frida reads only, no stepping** → native is ground truth, not a trainer. Do not pretend otherwise.

### Hardware reality (2026-11-12)

- **Dual-boot laptop:** Linux (current) and Windows + MuMu. i5-1235U, 12 threads, 14 GB RAM,
  `/dev/kvm`, **no NVIDIA GPU**.
- Use the laptop for **demos, live play, and light training**; use the **cloud GPU credits**
  (student discount) for PPO and heavier training.
- Laptop engine budget: start with **1 engine × 8 resident slots**; add engines only if CPU/RAM
  allow. Heavy PPO belongs on the cloud GPU host, not the laptop.
- Windows/MuMu is the live/demo environment; Linux is the dev/CI and headless-engine environment.
- FirstLight-Zero targets the **15.535.13** native engine, but its APK is **not in this workspace**.
  If it is present on the Windows side, run the lab there; otherwise prioritize the 16.635.1 port.
- The 16.635.1 APK is local (`libg` `0b5802…`), but the probe is **not ported**;
  `native_runner/supported_engine.json` is still 15.535.13.
- Budget: student cloud GPU credits; keep the laptop for engines/demos and rent GPU time only for
  PPO/evals.

### What is proven / not proven

**Proven**

- FirstLight-Zero: 1.17M-parameter X-Bow specialist, self-play only, beats the 16.8M IL model **97.5%** in the X-Bow mirror; ~1.5M matches in 43.3 hours.
- **The target deck is exactly FirstLight-Zero's X-Bow 3.0 deck:** X-Bow, Hero Knight, Evo Archers, Evo Tesla, Skeletons, Electro Spirit, Fireball, The Log. The recipe and v1450 are directly relevant.
- FirstLight's native probe, resident batch, and PPO stack exist in this repo and target the 15.535.13 engine.
- crforge M1: a full `BattleEnvV1` match completes through the bridge (`illegal: 0`, `truncated: 0`).
- F0 reduced crforge rejected actions from **9 to 1**; the remaining reject is an adapter bug, not an engine failure.
- crforge raw engine: ~8,371 decisions/s at 256 envs.

**Not proven**

- The 16.635.1 probe port and its throughput on this hardware.
- The live reader/executor loop on 16.635.1.
- The 16.635 form/level/tower-troop mapping of the FirstLight-Zero deck, and whether v1450
  transfers across the 15.535 → 16.635 rule change.
- That a learned X-Bow bot beats a competent scripted bot and then humans.

---

## 1. Architecture

```
Real Clash Royale engine (libg.so, 16.635.1)
  ├─ live client / emulator ──► reader + executor (Track B, 20 Hz)
  └─ headless probe/control ──► FirstLight runner (TCP/JSON or resident batch)
                                      │
                                      ▼
                         BattleEnvV1 / ObservationV1 / features
                                      │
                                      ▼
                      X-Bow specialist policy · PPO · evaluation
                                      │
                                      ▼
                         QA harness · captures · checkpoints

crforge (optional) ──► same runner surface ──► CI / parity / fallback
```

**Engine route (E) in practice**

- Use Frida and static analysis to map 16.635.1 `libg.so`: offsets, prologues, object layouts, version key.
- Port the FirstLight probe/control server. Use Jason's `HPComponentAccessor` hook and 16.635 packet/offset findings as the starting hypothesis, then verify every offset against the binary.
- Prefer an **extracted `libg.so` headless** process for speed. Emulator-hosted headless is acceptable; measure it before committing.
- Frida is for reverse engineering and live reads. The fast training path is the **native control server stepping ticks**, not Frida polling.
- Never reimplement `ObservationV1` in Java. Emit raw facts; encode in Python.

**Deployment split.** The laptop (Windows/MuMu or Linux AVD) hosts the engine; the cloud GPU runs
the learner and inference server. Use FirstLight-Zero's remote `engines.json` (SSH + shared path)
for the light setup, or the repo's Slurm path (`launch_ppo_engine_node.sh`,
`build_ppo_multinode_topology`) for heavier runs. Keep the laptop at 1 engine × 8 slots unless
measurements say otherwise.

---

## 2. Phases

### Phase 0 — Measure + probe spike (1 week measurement; probe time-boxed)

The only phase that can change the engine decision.

- [ ] If the 15.535.13 APK is available (Windows/MuMu or Linux AVD), run FirstLight-Zero for ~50 rounds. Use the cloud GPU for the learner if the laptop is too slow. Record matches/s, decisions/s, CPU, RAM, and whether the X-Bow recipe improves. If the APK is not available, skip to crforge + the 16.635 probe.
- [ ] Benchmark crforge **end to end** after F0: `BattleEnvV1` decisions/s, not raw `VectorBattle`.
- [x] Port or verify the 16.635.1 probe when Windows/Android is available: offsets, prologues, `libg` hash, one full match. **Done 2026-11-12/13** — cold configure, repeated headless matches, the two-sided decision loop (FINDINGS §3.13–3.17), entity/HP (§3.19–3.20), the scripted X-Bow self-play gate (§3.21) and Hero ability actions with the Champion layout (§3.22).
- [ ] Fill the engine decision table: per-slot latency, aggregate matches/s, illegal/truncated/rejected, parity. **Partial** — one A51 probe process measures 1.84 matches/s engine-only, 2.36 matches/s with 8 resident slots empty, and 1.73 matches/s with real card actions (FINDINGS §3.18, §3.23); multi-engine aggregation is open.
- [ ] Write a one-page engine decision memo with the numbers.

**The probe is the long pole.** If Jason's 16.635 notes/code transfer, it is weeks. From scratch it is 2–4 months. Time-box the spike: keep the 15.535 lab and crforge fallback moving while it is in progress.

**Exit:** a measured go/no-go on native as the trainer.
**Gate:** native ≥ 5 matches/s aggregate → native primary. 1–5 → split. < 1 → crforge primary. Probe not usable within 4 weeks → train on 15.535/crforge; live stays deferred.

### Phase 1 — Live loop + scripted X-Bow (3–4 weeks)

Get a working bot on the live client before training. The scripted bot is the scaffold, not the final policy.

**Headless scripted v0 (done 2026-11-13 on 16.635.1):** `tools/native_analysis/scripted_xbow_match.py` plays FirstLight-Zero's exact deck and forms through the probe with defense/cycle/spell/offense modules, mirrored placements, a wait option and blocked-cell fallback. A full self-play match completed at tick 3601 with 0 validation problems (173 decisions / 66 actions without abilities; 174 / 60 with 10 Hero Knight abilities), deterministic for a fixed seed; see FINDINGS §3.21–3.22. The live-client executor below remains open.

**Reader**

- [ ] Freeze the 16.635.1 APK, `libg.so`, probe, and content hashes.
- [ ] Offset map: battle/tick, roster, elixir, hand + next + cycle, tower HP/result, entity id/side/card/form/position/HP.
- [ ] Card/form mapping for 16.635.
- [ ] Emit 20 Hz frames; cross-check 50 frames against the screen.

**Executor**

- [ ] Two-touch or native command; confirm by a hand change, not optimism.
- [ ] Use the real ~21-tick delay; reserve elixir for plays already pending.
- [ ] Log every tap; observation → tap under 100 ms.
- [ ] Reconnect/abort handling.

**Scripted X-Bow v0**

- [ ] Policy interface: `observe(state) -> action`, not a monolith.
- [ ] Modules: defense, cycle, offense, spells.
- [ ] Recognizable X-Bow 3.0 play: defensive X-Bow, bridge X-Bow, Tesla/Knight/Archers support, Fireball/Log logic.
- [ ] Use FirstLight-Zero's v1450 replays/style as the reference for v0 — the deck is identical.
- [ ] A “wait” option and a legal-placement guard in the action layer.

**Capture**

- [ ] One file per battle: reader frames + plays + result + version.
- [ ] 20 hand-played games first; verify the format before scaling.
- [ ] Start `--watch` collection; the 500+ game corpus is completed in Phase 2.

**Exit:** v0 plays 20 live games, **0 illegal taps**, 20 valid captures, reconnect works.
**Gate:** if the loop is unstable after 2 weeks, fix the executor before any training.

### Phase 2 — QA harness + corpus (2 weeks, parallel)

Build the referee before the learner.

- [ ] QA harness (see §3).
- [ ] 500 labelled games, tagged by deck, side, result, client/data version.
- [ ] Parity: 50 frames native vs live; crforge vs native only if crforge is active.
- [ ] Capture round-trip validation; <1% failure before scaling.

**Exit:** one command produces a QA report; the corpus validates.

### Phase 3 — Learn: mirror bootstrap + matchup pool (4–6 weeks)

Start from a proven X-Bow recipe, not from scratch. Two sub-phases, same model family.

#### 3a — Mirror bootstrap (1–2 weeks)

- [ ] **The deck is identical to FirstLight-Zero's.** Evaluate v1450 as a direct benchmark and opponent. Try it as an initialization after verifying the 16.635 card/form/level/tower-troop mapping; if the rule change breaks it, retrain with the same recipe (1.17M, CNN + LSTM, 18×32 grid). Do not blindly port weights.
- [ ] Self-play X-Bow mirror until the deck plan is stable: defend, cycle, build X-Bow, spell chip.
- [ ] Keep the mirror as an anchor in the opponent pool so the model does not forget the core plan.
- [ ] Gate: v2 is competitive with v1450 in the mirror, `illegal: 0`, `truncated: 0`.

#### 3b — Matchup generalization (3–4 weeks)

A deck-specific model **cannot pilot other decks**. Opponents must be other policies.

- [ ] Behavior cloning on live captures (v1).
- [ ] PPO against a fixed opponent pool (v2):
  - scripted archetypes: Hog 2.6, Golem, Log Bait, Bridge Spam, Graveyard, mirror;
  - General / Active IL piloting random decks from the deck pool;
  - Hog specialist 1/2;
  - past X-Bow snapshots;
  - live-captured policies and mined hard cases.
- [ ] Reuse the V4 matchmaking/league/deck-pool infrastructure: `build_ppo_deck_pool.py`, `matchmaking.py`, `league.py`, `focus_decks_20.json`.
- [ ] PFSP: keep the mirror, focus on opponents the model struggles with, add hard counters.
- [ ] Reward: ±1 result, tower-HP potential, elixir-overflow penalties, own-King activation penalty, illegal-placement penalty. No card-specific X-Bow shaping.
- [ ] Monitor entropy, approx-KL, clip fraction, explained variance, game length, actions/game.
- [ ] Replay review every checkpoint; look for same-tile spam, passive openings, wasted Fireballs, bad Tesla/X-Bow defense.
- [ ] Evaluation matrix: 200 games per archetype, fixed seeds. Report win rate, crown diff, tower damage/elixir, illegal taps. Do not use mirror win rate as ladder evidence.

**Exit:** v2 beats scripted v0 over **200+ mirrored games**, is competitive with FirstLight-Zero v1450 over 200 games, **>60% against the deck pool**, `illegal: 0`, `truncated: 0`, no stall loops, lethal-spell test passes.

### Phase 4 — Prove (2 weeks)

- [ ] 100+ unattended live games on Null's 16.635.1.
- [ ] >60% over 200+ mirrored games against the deck pool.
- [ ] No crashes, no illegal taps, reconnect works.
- [ ] Human review: are the decisions X-Bow-competent?
- [ ] Written go/no-go for the main ladder.

### Phase 5 — Main ladder + UC (deferred)

Unchanged in intent: reader for the current official build, catalog extension, executor, maintenance, then the climb. **Do not start before Phase 4 passes.**

**Timeline summary (part-time, after the probe is usable):** Phase 1 3–4 weeks, Phase 2 2 weeks parallel, Phase 3 4–6 weeks, Phase 4 2 weeks → **~3–4 months to the live gate**, plus the probe port (2–4 months from scratch, weeks if Jason's notes transfer). GPU cost: roughly $50–100 of GPU time for the small specialist, covered by the student cloud credits.

---

## 3. QA harness

A QA harness is the referee: a small, repeatable program that runs fixed battles through any engine or policy and prints a pass/fail report. It answers three questions:

1. Is the engine still legal and deterministic?
2. Does the observation match reality?
3. Is the policy actually improving against a fixed ladder?

| Part | Contents |
| --- | --- |
| **Fixtures** | Fixed seeds, decks, action scripts, saved live frames |
| **Adapters** | One `reset/step/observe` surface for native, live, and crforge |
| **Checks** | Invariants, golden traces, parity, regression bank |
| **Report** | JSON/HTML per run, saved as an artifact; CI fails on regression |

**Required checks**

- **Invariants:** `illegal: 0`, `truncated: 0`, no pending leaks, action mask consistent with engine refusals. 100 seeded random-legal-action games.
- **Golden trace:** seed + actions → per-tick observation hash. Diff after every engine/probe change. Catches lane flips and side swaps that win rates hide.
- **Parity:** same script on native vs live (and crforge if active). Compare entity counts, tower HP, deaths, play ticks. Semantics, not bytes.
- **Regression bank:** 200 games against a fixed opponent ladder — scripted X-Bow, General, IL, past checkpoints, Hog. Report win rate, crown diff, tower damage/elixir, match length, illegal taps.
- **Exploit probes:** wait-only, spell-only, same-tile spam, tower rush. The engine must not reward degeneracy.
- **Lethal-spell test:** enemy King at 1 HP, Fireball in hand, enough elixir → must cast.
- **Memory ablation:** shuffle/zero recurrent state. If win rate is unchanged, the recurrence is decorative.
- **Live canary:** one game/day, screen-vs-reader diff, tap confirmation. No unattended runs until 20 clean games.

**Engine decision metrics** (Phase 0 report): per-slot latency, aggregate matches/s, decisions/s, illegal/truncated/rejected, 50-frame parity, CPU/RAM.

**Cadence:** on every probe/environment change; nightly 20-game smoke; before every checkpoint promotion.

---

## 4. Data and model strategy

- **Retire `IL_Replay` as a training corpus.** It predates the 16.635 rules and is not needed for a single-deck specialist. Keep the IL/General/Everlight **checkpoints** as opponents, baselines, and fallbacks.
- **Self-play is the primary data source:** mirror bootstrap first, then opponent-pool generalization. Live captures supply behavior cloning, validation, and the reality check.
- **Primary model: X-Bow specialist.** The deck is exactly FirstLight-Zero's, so its 1.17M model and recipe are the starting point, not just a reference. v1450 is a direct benchmark/opponent; try it as an initialization after the 16.635 mapping check.
- **V4/Everlight: generalist, opponent, and fallback.** Do not force the X-Bow goal through a V4-only constraint.
- **Deck fidelity:** the eight cards are fixed (X-Bow, Hero Knight, Evo Archers, Evo Tesla, Skeletons, Electro Spirit, Fireball, The Log). Record the tower troop and exact 16.635 form mapping; verify every card/form is in the model vocabulary and set `SideSetup` slot flags explicitly.
- **Level caveat:** v1450 was trained at **level 11**; the target account is maxed. Decide whether to train/evaluate at level 16 or normalize levels, because HP/damage interactions change. Do not compare win rates across different level settings.
- **Optional, off the critical path:** use the old corpus for representation pretraining (freeze encoder, linear probe on a small 16.635 set). Only if it measures a win.
- **Every artifact is reproducible:** code commit, model hash, probe/`libg` hash, tables/content hash, client/data version, seed, deck, levels, tower troop.

---

## 5. Decision gates and kill criteria

| Gate | Fail condition | Response |
| --- | --- | --- |
| Phase 0 | Native < 1 match/s aggregate | crforge becomes primary trainer; native is the live oracle |
| Phase 0 | 16.635 probe blocked > 4 weeks | Train on 15.535 native; live stays deferred |
| Phase 1 | >5% illegal taps or unstable loop after 2 weeks | Fix executor/probe before training |
| Phase 2 | Capture round-trip failure >1% | Fix format before scaling the corpus |
| Phase 3 | v2 cannot beat v0 over 200 games after a full run | Training loop is broken; do not go live |
| Phase 4 | Any illegal tap, crash, or unrecoverable disconnect in 100 games | No-go; fix and repeat |
| Global | Simulator win rate used as ladder evidence | Stop; require live/human review |

---

## 6. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| 16.635 probe port is harder than expected (2–4 months from scratch) | Live and training slip | Time-box Phase 0; use Jason's 16.635 notes/code if available; fall back to 15.535 training + crforge |
| Emulator-hosted throughput is far below extracted-engine throughput | Training too slow | Extract `libg.so` headless; otherwise crforge for bulk PPO |
| v1450 does not transfer to 16.635, or the form/level mapping differs | Policy cannot represent the deck | Retrain with the same FirstLight-Zero recipe; verify the 16.635 catalog first |
| v1450 is level 11, the target account is maxed | Win rates do not transfer | Train/eval at the account's real levels; record levels and tower troop in every artifact |
| Laptop engines + cloud learner is slow to operate | Iteration stalls | Keep the 50-round lab small; use the repo's distributed PPO / remote-host path; checkpoint often |
| Policy overfits the scripted baseline | Beats v0, loses to humans | Opponent pool with General/IL/Hog/humans; live eval each checkpoint |
| Entropy collapse / same-tile spam | Degenerate play | Monitor entropy/KL/clip; replay review; illegal penalty |
| Live account risk | Ban | Canary/alt account first; main only after Phase 4 |
| Docs drift again | Wrong decisions | This file is the single source; update it in the same commit |

---

## 7. Do not do

- Do not train on `IL_Replay`.
- Do not make crforge the critical path.
- Do not run 3–5 minute real-time matches as the training loop.
- Do not build a full-fidelity simulator before training.
- Do not reimplement `ObservationV1` in Java.
- Do not blindly port FirstLight-Zero or Proxv weights; reuse the recipe and benchmark the checkpoint.
- Do not force the X-Bow goal through a V4-only model.
- Do not start the main ladder before Phase 4.
- Do not treat simulator win rates as ladder evidence.
- Do not use `locationId=15000199` on 15.535 base content; it only exists in `15.535.86`.

---

## 8. Immediate next actions

1. [ ] **Phase 0:** if the 15.535.13 APK is available, run FirstLight-Zero for ~50 rounds, using the cloud GPU for the learner; record matches/s and the improvement curve.
2. [ ] **Phase 0:** benchmark crforge F0 end to end; record the fallback number.
3. [ ] **Phase 0:** port/verify the 16.635.1 probe when Windows/Android is available; one full match.
4. [ ] **Phase 1:** build the reader + executor + scripted X-Bow v0.
5. [ ] **Phase 2:** build the QA harness before the learner.
6. [ ] Write the engine decision memo and update this roadmap in the same commit.

---

## 9. Repo map

| Path | Purpose |
| --- | --- |
| `native_runner/` | Native engine transport, `BattleEnvV1`, probe, resident batch, V4 training |
| `native_runner/cr_native_env.py` | **The seam.** TCP JSON client and command surface |
| `native_runner/battle_env.py` | `BattleEnvV1`, attestation, observation assembly |
| `native_runner/probe/` | Native hook source; port target for 16.635.1 |
| `native_runner/training/v4/` | V4 cache builder, IL, PPO, evaluate, serve |
| `checkpoints/` | Five V4 models; opponents/baselines, not the X-Bow primary |
| `.resource/FirstLight-Zero/` | **X-Bow specialist reference:** recipe, model, training loop, 15.535 engine |
| `.resource/FL-Royale/` | Offline Android app reference (native runtime, on-device inference) |
| `.resource/crforge/` | Optional fallback engine; see `ROADMAP_CRFORGE.md` |
| `crforge_bridge/` | Optional fallback/CI bridge |
| `docs/capture_format.md` | Live capture format and optional crforge conversion |
| `jason_notes/` | Source transcripts for the native/RE path |
| `nr_16.635.1_release_951fe421.apk` | 16.635.1 client; probe port target |
| 15.535.13 APK | **Not in this workspace**; needed for the FirstLight-Zero lab |
