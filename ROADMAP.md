# Roadmap: V4 X-Bow 3.0 → Ultimate Champion

Status: **active — Option D (crforge engine swap)**, revised 2026-11-12
Owner: Raj
Primary repo: this repository (fork of `FirstLight_CR`)
Model: **FirstLight_CR V4 16.8M / Everlight**, one model family, no side quests
Engine: **crforge** (Java, tick-exact on client 16.635.1) behind FirstLight's runner protocol

**End goal:** reach **Ultimate Champion (UC) on Supercell's main ladder** with **X-Bow 3.0**.
**Current focus:** make 16.635.1 trainable and playable. Null's is the sandbox; the main-server bridge stays deferred.

| Stage | Deck | Environment | Status |
| --- | --- | --- | --- |
| 1 | Hog 2.6 | crforge 16.635 → Null's live | Engine adapter is the critical path |
| 2 | X-Bow 3.0 | crforge 16.635 → Null's live | Train and validate the real UC deck |
| 3 | X-Bow 3.0 | Main server | Deferred: reader, bridge, UC push |

**Milestones**

- **M1 — Engine parity:** FirstLight's `BattleEnvV1` completes a match against crforge with `illegal: 0`, `truncated: 0`.
- **M2 — Observation parity:** `ObservationV1` from crforge matches a live 16.635 reader frame-for-frame on 50+ frames.
- **M3 — Captured corpus:** 500+ labelled 16.635 games from live Null's play.
- **M4 — X-Bow trained:** V4 X-Bow beats the IL/General baselines in crforge and wins on Null's.
- **M5 — Readiness gate:** passes Phase 9, then main bridge and UC.

---

## 0. Decision record: why Option D

Three paths were on the table. Only one keeps the model you have *and* gives a version-correct engine.

| Option | Live vs players | Trainable 16.635 | Reuses V4 | Ceiling | Weeks @10–15 h/wk |
| --- | --- | --- | --- | --- | --- |
| A. RoyaleGym + RoyaleBot | **1–2** | yes (its own policy) | no | medium | 1–2 |
| **D. crforge → FirstLight env** | via Track B | **yes, tick-exact** | **yes** | **highest** | 8–14 |
| E. Full FirstLight probe port | 10–16 | yes (real `libg.so`) | yes | high | 10–16 |
| B. RoyaleHarness Pro (live only) | 4–8 | no | yes | high (frozen weights) | 4–8 |

**D is chosen.** E costs more for no advantage at this stage: crforge already matches recorded
battles tick-for-tick on the client version you need, and you have no 16.635 corpus to train
`libg.so` on anyway. A is a *scout*, not a destination; B is a required **track**, not the plan.

**B is required by D.** `IL_Replay` (252,238 games, last modified 2026-09-06) predates the
ranges crforge models (16.402.12+ and 16.635.1). Battle rules only change on a required update,
so a 15.535 replay cannot be played by a 16.635 rule set. `crforge-data.lock` is
`version=16.635.8`, `compatible=16.635.3 16.635.8`, and its only release tag is
`v16.402.17-range-16.402.12-16.402.20`. **The old corpus is retired.** The IL prior has to be
re-captured on 16.635 — which is exactly what the live reader produces.

That coupling is the point: **D is the trainer, B is the data source and the reality check.**
D alone trains PPO in a simulator with no human data and no ground truth — the failure mode
Hasty-CR documents in its own README ("the simulator marking its own homework").

### Content reality (measured 2026-11-12)

| Content | Simulator + render | Training | Live vs real players |
| --- | --- | --- | --- |
| `15.535.3` (APK base pack) | yes | no | no |
| `15.535.86` (pinned) | yes | yes | no |
| `16.635.1` (current) | via crforge | via crforge | via Track B |

- The pinned `15.535.86` update is no longer served and is not worth hunting further.
- 15.535.13 no longer connects to Null's: **no players**. It is only good for offline replays.
- crforge removes the libg dependency for training entirely: it builds tables per data version
  and needs no APK.

### External assets evaluated (do not re-litigate)

| Asset | What it is | Verdict |
| --- | --- | --- |
| `VanguardX101/IL_Replay` | 252,238 replay parquet | **Retired** — wrong rule ranges; see above |
| `Proxv/clash-royale-ai-checkpoints` | self-play `.npz`, iter #600, 13,129 eps, Elo 1306 | **Not a corpus.** Weights only, foreign contract, self-referential Elo |
| `RoyaleGym` / `RoyaleSim` | Rust sim, MIT, card table `client160635007_20261103` | Scout only. 98.8%/99.3% on 15.535 basic cards + 1 hero; evos/heroes/champions unmeasured |
| `RoyaleHarness` | FirstLight→Null's reader/executor, pinned `libg` `110aa2b5…` | **Track B seed.** Exact `libg` match to the local 15.535.13 APK; single branch, no 16.x |
| `Hasty-CR` | Python sim + vision bridge | Reference. Vision cannot read HP; no policy has beaten a human |
| `daybreak-cr` | IL/PPO reference, official client | Architecture only. Needs private `clapha-engine`; readers are private |
| `crforge` | Java tick-exact sim, Apache-2.0 | **Chosen engine** |

---

## 1. Project constraints

| Constraint | Answer | Effect |
| --- | --- | --- |
| Model | V4 16.8M + Everlight (`upstream/yes-delay`) | Frozen weights are an asset; Everlight has the real 20-tick delay |
| Main account | X-Bow 3.0 maxed | X-Bow is the only main-ladder deck |
| Null's account | Everything maxed (lvl 16 + evo/hero) | Safe sandbox; also the hardest observation case |
| Ban tolerance | Full automation accepted on main | Applies at Phase 10 only |
| Hardware | Windows PC; cloud budget only; no rooted device | MuMu for Null's; crforge runs anywhere with JDK 21 |
| Time | 5–15 h/week hands-on, tens of hours idle training | Engine track 2–4 months; live track 4–8 weeks |
| Budget | ~$500 cloud credits; no HPC | Fine-tunes + short GPU bursts, not 8-GPU self-play |
| Current focus | **16.635.1 via crforge** | Do not start the main bridge before Phase 9 |

`docs/getting_started.md` and `README.md` still describe the 15.535.86 install. That path is
kept for offline replays only; it is no longer on the critical path.

---

## 2. Architecture: where crforge sits

FirstLight does **not** embed its engine. `NativeClashEnv` is a TCP client
(`127.0.0.1:26789`) speaking line-delimited JSON, and `ObservationV1` is assembled in Python by
`BattleEnvV1` + `rich_telemetry_adapter`. So the engine swap is a **new process that answers
commands**, not a rewrite of the model, trainer, or evaluator.

```
crforge (Java 21)                      FirstLight (Python 3.12)
┌──────────────────────┐               ┌─────────────────────────────┐
│ core/   battle core  │               │ NativeClashEnv   (TCP JSON) │
│ env/    LadderEnv    │◄── Runner ───►│ BattleEnvV1                 │
│ bridge/ RunnerServer │   26789       │   └─ ObservationV1          │
└──────────────────────┘               │        └─ V4 / Everlight    │
                                       │ tensorizer · evaluate · PPO │
                                       └─────────────────────────────┘
```

**Commands to implement** (the full surface `NativeClashEnv` issues):

```
configure / configure-native     set decks, levels, seed, deck seats
step N / advance-native N        advance ticks or decisions
deploy <player> …                play a card
activate-ability <player> …      champion / hero ability
observe / observe-atomic         basic state
observe-rich                     full raw facts (the expensive one)
attest                           content + build hashes  ← gate
status / pause / resume / speed
snapshot-create / restore / release
replay-schedule-* / inject       replay and fault injection
multi-*                          resident batch mode (perf only)
```

**What stays untouched:** `ObservationV1`, `tensorizer.py`, `decode`, the IL/PPO trainers,
`evaluate.py`, `serve_policy.py`, the arena, and every checkpoint.

**What changes:** `ruleset.py` / `attestation.py` gain a crforge attestation path (it reports
`version=16.635.8`, `content_sha`, `crforge-data.lock` commit instead of a `libg.so` hash); the
catalog gains a 16.635 → V4 vocabulary bridge.

---

## 3. Phase plan

### Phase 1 — Environment + model smoke (done)

- [x] `tools/check_install.py` → `Ready: 5 models`.
- [x] Tests green (1202 passed, 18 skipped, 2026-11-11).
- [x] Everlight available on `upstream/yes-delay`.

### Phase 2 — crforge build + tables (1–2 weeks)

```bash
export JAVA_HOME=$(/usr/libexec/java_home -v 21)   # or the Windows JDK 21
./gradlew build
./gradlew :core:fidelityReport
./gradlew :desktop:run -Pcrforge.assetSource=cdn   # opt-in
pip install -e python/
python -m crforge_env.bench
```

- [ ] JDK 21 build green; `fidelityReport` read.
- [ ] Tables built for `16.635.3` and `16.635.8`.
- [ ] `python -m sim.check` claims carried into a real match.
- [ ] Measured steps/s on this machine recorded (~127k claimed).

**Exit:** two crforge policies play a full battle from Python through `ClashGymEnv`.

### Phase 3 — Corpus decision (1–2 weeks) — **the gate**

Prove or kill the IL assumption before writing the adapter.

- [ ] Read `docs/environment.md` § `ReplayLabeller` / `label_replays`; identify the accepted replay schema.
- [ ] Try one real 16.635 replay end to end (`label_replays` → numpy rows).
- [ ] Confirm the IL_Replay retirement: attempt one 15.535 payload, record the refusal.
- [ ] Write the capture format: what the live reader must dump to become a crforge replay.

**Exit:** a written go/no-go. If no 16.635 replay can be labelled, training is self-play-first
and the IL fine-tune phase moves after the live loop, not before it.

### Phase 4 — RunnerServer skeleton (3–4 weeks)

New gradle module `bridge` depending on `core` + `env`. Text protocol, one battle per `env`.

- [ ] `configure` → `LadderEnv` with decks, levels, seed, `SideSetup`.
- [ ] `step` / `advance-native` with `PLAY_DELAY_TICKS=20` semantics.
- [ ] `deploy` / `activate-ability` through `PlayerCommands`.
- [ ] `observe` (basic): elixir, hand, cycle, towers, result.
- [ ] `attest` → version, content sha, lock commit.
- [ ] `status`, `pause`, `resume`.
- [ ] Bypass-and-record: `ruleset.py` accepts the crforge attestation.

**Exit:** `evaluate --games 2` runs crforge-vs-crforge through `BattleEnvV1` with
`illegal: 0`, `truncated: 0`.

### Phase 5 — `observe-rich` parity (4–6 weeks)

The hardest phase. One field group at a time, each with a unit test and a 20-game sanity run.

- [ ] Entities: characters, buildings, towers — id, side, card, form, position, HP.
- [ ] Attack targets and retargeting (`attack targets`).
- [ ] Projectiles and area effects.
- [ ] Visibility / fog / "what the player can see".
- [ ] Phase runtime (evolution, hero, champion, tower troop).
- [ ] Character state + action movement + special movement runtimes.
- [ ] Deck / forms / cycle exposure per side.

**Exit (M2):** `ObservationV1` from crforge matches live-reader frames on 50+ frames; all
`_runtime` parsers accept crforge output.

### Phase 6 — Arena, evaluator, cache builder (2–3 weeks)

- [ ] `arena.py` / `evaluate.py` reproduce a sane General-vs-IL ballpark.
- [ ] `cache_builder` runs against crforge without `--verify-roundtrip` failures.
- [ ] Zero-shot X-Bow: General and Everlight with the X-Bow 3.0 deck; record win rates.

**Exit:** the evaluator is trustworthy and the deck's zero-shot baseline is written down.

### Phase 7 — Live capture loop (Track B, parallel; 4–8 weeks)

Runs alongside Phases 2–6; see `ROADMAP_NULLS16_PROBE.md`. It is where the training data comes
from, and the only ground-truth check on whether the simulator is teaching the right game.

- [ ] Reader emits 20 Hz `ObservationV1` on Null's 16.635.1.
- [ ] Replays captured in a crforge-labellable format.
- [ ] 500+ labelled games with the decks you care about.
- [ ] V4/Everlight plays live matches without illegal taps.

**Exit (M3):** a corpus that is version-correct, plus a live oracle.

### Phase 8 — Train X-Bow 3.0 (3–6 weeks + GPU)

1. Start from General/Everlight. No from-scratch PPO.
2. IL fine-tune on the captured X-Bow subset.
3. Short PPO against a mixed pool (IL / General / snapshots / hard decks).
4. Validate in crforge, then on live Null's.

- [ ] X-Bow beats Hog 2.6 and General over 200+ mirrored games.
- [ ] `illegal: 0`, `truncated: 0`, no stall loops.
- [ ] Replay review shows intentional X-Bow play.

### Phase 9 — Readiness gate

- [ ] >60% over 200+ mirrored games against the deck pool.
- [ ] 100+ unattended live games: no crashes, no illegal taps, reconnect works.
- [ ] You (GC/Master) judge the decisions UC-caliber.
- [ ] Written go/no-go.

### Phase 10 — Main bridge + UC (deferred)

Unchanged in intent: reader for the current official build, catalog extension, executor,
maintenance, then the climb. Do not start before Phase 9 passes.

---

## 4. Timeline and budget

| Work | Estimate | Cost |
| --- | --- | --- |
| Phase 2 crforge build | 1–2 weeks | $0 |
| Phase 3 corpus gate | 1–2 weeks | $0 |
| Phase 4 runner skeleton | 3–4 weeks | $0 |
| Phase 5 `observe-rich` | 4–6 weeks | $0 |
| Phase 6 arena/eval/cache | 2–3 weeks | $0 |
| Phase 7 live capture (Track B) | 4–8 weeks, parallel | $0 |
| Phase 8 X-Bow training | 3–6 weeks | 20–60 GPU-h (~$20–70) |
| Phase 9 gate | 1–2 weeks | $0 |
| **Engine + first trained X-Bow** | **~4–6 months part-time** | **<$100** |
| Through UC (incl. Phase 10) | +3–9 months | keep the rest of the $500 |

Track B alone (live V4/Everlight, no training) is **4–8 weeks** if the 16.x structs are intact.

---

## 5. Kill criteria

Write these down now; they are the point of the plan.

| Gate | Fail condition | Response |
| --- | --- | --- |
| Phase 3 | No obtainable 16.635 replay can be labelled | Self-play-first; move IL after Phase 7 |
| Phase 4 | Runner cannot express a play the model emits | Extend codec, not the model |
| Phase 5 | >25 observation fields have no crforge source | Stop D-train; ship Track B frozen weights |
| Phase 6 | Evaluator disagrees with crforge's own env | Fix the adapter before any training |
| Phase 7 | 16.x structs gutted | Fall back to RoyaleGym live + crforge training |
| Phase 8 | Policy exploits sim quirks | `exploit_probe`-style audit; reward outcome only |

---

## 6. Risk register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Adapter maps semantics wrong, not just fields | Silent policy degradation | Unit test per field group; live-frame parity (M2) |
| No human data → sim-shaped policy | Wins offline, loses live | Track B capture; live eval each checkpoint |
| 16.635 catalog differs from V4's 122/62 | Model cannot represent new forms | Catalog bridge; fine-tune after capture |
| `observe-rich` balloons | Months lost | Basic-only path first; rich is additive |
| crforge moves version under work | Churn | Pin `crforge-data.lock`; fork the repo |
| GPU budget exhausted | Training stops | Subset caches, spot instances, checkpoint often |
| Phase 10 automation ban | Lose account | Accepted risk; session hygiene; rollback plan |
| Docs drift again | Wrong decisions | This file is the single source; update it in the same commit |

---

## 7. Do not do

- Do not hunt the `15.535.86` update any further.
- Do not try to train on `IL_Replay`; it is outside every rule range crforge models.
- Do not treat the Proxv checkpoints as data.
- Do not port FirstLight-Zero or Proxv weights.
- Do not reimplement `ObservationV1` in Java; emit raw facts and let Python encode.
- Do not train from scratch.
- Do not start Phase 10 before Phase 9.
- Do not treat simulator win rates as ladder evidence.
- Do not split the engine track from the live track; D needs B's data.

---

## 8. Immediate next actions

1. [ ] Build crforge (`./gradlew build`) and run `python -m crforge_env.bench`.
2. [ ] Phase 3: one `label_replays` attempt on a real 16.635 replay; one recorded refusal on 15.535.
3. [ ] Create the `bridge` module and answer `configure` + `status` + `attest`.
4. [ ] Start Track B in parallel: `ROADMAP_NULLS16_PROBE.md`.
5. [ ] Capture format spec: reader dump → crforge replay.

---

## 9. Repo map

| Path | Purpose |
| --- | --- |
| `native_runner/` | V4 training, runner protocol client, battle env |
| `native_runner/cr_native_env.py` | **The seam.** TCP JSON client, command surface |
| `native_runner/battle_env.py` | `BattleEnvV1`, attestation, observation assembly |
| `native_runner/rich_telemetry_adapter.py` | Raw facts → rich observation |
| `native_runner/training/v4/` | Cache builder, IL, PPO, evaluate, serve |
| `checkpoints/` | Five V4 models; Everlight on `upstream/yes-delay` |
| `ROADMAP_CRFORGE_ADAPTER.md` | Engineering checklist for the engine swap |
| `ROADMAP_NULLS16_PROBE.md` | Track B: live reader on 16.635.1 |
| `.resource/daybreak-cr/` | Live-loop and training architecture reference |
| `user-provided/nulls-royale.apk` | 15.535.13, `libg` `110aa2b5…`, offline replays only |
| `build/cr-ai-offline.apk` | Patched 15.535.13, base content, offline only |

**Removed from this roadmap** (never present locally): `.resource/cr-memory-reader`,
`.resource/ClashAI`, `.resource/FirstLight-Zero`, `native_runner/versions/`.
