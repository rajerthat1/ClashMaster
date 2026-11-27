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
- [ ] Measure **engine-only** ticks/s per worker at 4 / 8 / 12 workers.
- [ ] 1 h soak; record CPU/RSS and the knee.
      *(Running detached via `tools/native_bringup/run_phase0.py`; evidence
      lands under `builds/native/phase0-*/`.)*

**Gate:** `doctor` hard-pass, 4 workers healthy, one worker completes a match,
1 h with no crash.

### Phase 1 — Native API, X-Bow deck, determinism (3–5 days)

- [x] Install `cr-native-sandbox`; connect `NativeRoyaleEnv` to the workers.
- [x] Configure the X-Bow deck: X-Bow, Hero Knight, Evo Archers, Evo Tesla,
      Skeletons, Electro Spirit, Fireball, The Log. Record forms and levels.
- [x] Exercise `reset` / `observe` / `act` / `step`; 20 matches.
- [x] Same seed + same actions → same observations (determinism).
- [ ] Validate observations against A51/16.635.1 goldens: card IDs, HP, elixir,
      tick, hand, abilities. Note 15.535.29 vs 16.635.93 differences.
      *(First pass: level-11 tower HP 4824/3052, deck ids/forms/costs and
      starting elixir match; unit HP and abilities pending.)*
- [x] Decide the model: sandbox R0 elite-v3 (v1450 stays a benchmark).

**Gate:** 20 matches, 0 errors, X-Bow deck plays legally, deterministic reset.

### Phase 2 — Throughput (1 week)

- [ ] Scale 4 → 8 → 12 workers; record engine-only and end-to-end rates.
- [ ] Watch CPU, per-process RSS, and the knee where adding workers stops
      helping.
- [ ] If engine-only clears 10 matches/s, lock the host size.
- [ ] If the full observation/policy loop is below target, profile the
      observation path before buying more hardware.
- [ ] Compare with the published sandbox table (0.84–0.93 matches/s at 12–24
      processes with 4 matches/process).

**Gate:** ≥10 matches/s engine-only on the chosen host, or a written bottleneck
plan with a measured next step.

### Phase 3 — Training (2–3 weeks)

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
| Live account risk | Ban | Canary/alt account first; main ladder after Phase 4 |

---

## 10. Do not do

- Do not build an AVD, MuMu or phone cluster for training.
- Do not write a custom `libg` loader unless this runtime fails a gate.
- Do not train on `IL_Replay`.
- Do not make crforge the critical path.
- Do not run real-time matches as the training loop.
- Do not blindly port FirstLight-Zero weights across versions.
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
| `.resource/FirstLight-Zero/` | X-Bow recipe, v1450 checkpoints, benchmark |
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
- **`crforge`** remains the CI/differential oracle. `clash-royale-research` is
  the faster approximate sandbox, not the fidelity oracle.

---

## 13. Next action

Phase 0 is complete except the 1 h soak and 4/8/12-worker knee. Phase 1 is
complete except the A51/16.635.1 golden parity check (no device attached on
2026-11-13). See [`HANDOFF.md`](HANDOFF.md): attach the A51, capture the golden,
record the 15.535.29 vs 16.635.1 gap, then start Phase 2 host sizing.
