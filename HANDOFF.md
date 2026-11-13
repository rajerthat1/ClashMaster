# Session handoff — 2026-11-12 (native-first revision)

> Read [`ROADMAP.md`](ROADMAP.md) first. It is the single source for the plan.
> This file records current state, measured facts, and the next actions.

## Decision

- **Primary engine: E — the real game engine, headless via the FirstLight probe/runner.**
  Target client **16.635.1**. Use Frida/static analysis to port the probe; prefer an extracted
  `libg.so` headless process, emulator-hosted headless as fallback.
- **crforge is demoted** to deterministic fallback / CI oracle. See [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md).
- **`IL_Replay` is retired as a training corpus.** Keep the IL/General/Everlight checkpoints as
  opponents, baselines, and fallbacks.
- **Model: X-Bow specialist first.** The deck is **exactly FirstLight-Zero's** (X-Bow, Hero Knight,
  Evo Archers, Evo Tesla, Skeletons, Electro Spirit, Fireball, The Log). Reuse its recipe (1.17M,
  CNN + LSTM, 18×32 grid) and use v1450 as a direct benchmark/opponent. V4/Everlight are
  generalists, opponents, and fallbacks — not the critical path.
- **First milestone: scripted X-Bow plays live on 16.635.1.** Then behavior cloning, then PPO:
  **3a mirror bootstrap → 3b matchup pool** (General/IL/Hog/scripted/live). Then a live gate.
  The script is the scaffold, not the final bot.

## What is proven

- **FirstLight-Zero** is an X-Bow 3.0 mirror specialist trained from self-play only:
  1.17M parameters, **97.5%** against the 16.8M IL model in 40 games, **~1.5M matches in 43.3
  hours**. Published run: 1,024 episodes / 104 s ≈ **9.85 matches/s aggregate**; the shipped
  example uses 2 engine processes × 8 resident slots and one GPU.
- The **target deck is exactly FirstLight-Zero's**, so the recipe and v1450 are directly relevant,
  not just a reference.
- The **15.535.13 native stack** is supported by FirstLight-Zero and is the development lab for the
  X-Bow recipe and the QA harness. Its APK is **not in this workspace**; if it is on the Windows
  side, run the lab there, otherwise prioritize the 16.635.1 port.
- **crforge M1** stands: a full `BattleEnvV1` match completes through the bridge
  (`illegal: 0`, `truncated: 0`).
- **F0** reduced crforge rejected actions from **9 to 1**. The remaining reject is an open adapter
  bug, isolated to one card/owner sequence; not an engine failure.
- crforge raw engine: **8,371 decisions/s at 256 envs**. The bridge is **20–30 decisions/s**;
  the gap is `BattleEnvV1` observation assembly and JPype conversion, not crforge.
- The **16.635.1 APK** is local (`nr_16.635.1_release_951fe421.apk`, `libg` `0b5802…`), but the
  probe is **not ported**; `native_runner/supported_engine.json` is still 15.535.13.

## What is not proven

- The 16.635.1 probe port and its throughput on this hardware.
- The live reader/executor loop on 16.635.1.
- The 16.635 form/tower-troop mapping of the FirstLight-Zero deck, whether v1450 (trained at
  level 11) transfers across the 15.535 → 16.635 rule change, and how it behaves at the maxed
  account's levels.
- That a learned X-Bow bot beats a competent scripted bot and then humans.

## Phase 0 — the measurement that decides everything

Fill this table with numbers, not opinions:

| Engine / path | Hardware | Per-slot latency | Aggregate matches/s | Decisions/s | illegal / truncated / rejected | Notes |
| --- | --- | ---: | ---: | ---: | --- | --- |
| FirstLight-Zero, 15.535 native | Linux + GPU | ~1.6 s | ~9.85 | — | — | Published; extracted engine |
| FirstLight-Zero, 15.535 native | laptop engines + cloud GPU | ? | ? | ? | ? | 50-round lab run, if APK acquired |
| crforge end-to-end after F0 | this host | ? | ? | 20–30 (bridge) | 1 reject | Fallback baseline |
| native 16.635, 1 engine × 8 slots | Windows/MuMu or Linux AVD | ? | ? | ? | ? | Probe port required |
| native 16.635, 2 engines × 16 slots | same | ? | ? | ? | ? | Target ~10 matches/s |

Decision rule: **≥5 matches/s aggregate → native primary. 1–5 → split. <1 → crforge primary.**

## Current crforge state (fallback)

- `evaluate --games 2`: General 2–0 IL, both terminated, ticks 5,425 and 6,218,
  `truncated: 0`; game 1 reports 1 rejected action after F0 (was 9).
- F0 in place: numpy conversion for JPype arrays, pending/`last_run_refusal`, loud `_giving`,
  typed `CARD_PENDING`, pending-slot mask.
- Open: close the last reject, then run 20 games with `illegal: 0`, `truncated: 0`.
- Bridge latency: `observe_atomic` was 20.5 ms, dominated by `list()` over 7,024-int JPype arrays
  (~7.2 ms × 3); `np.asarray` is ~0.002 ms. The F0 fix should bring `observe_atomic` to ~3–4 ms.
- Do **not** extend `observe-rich` while native is primary.

## Hardware / environment

- **Dual-boot laptop:** Linux (current) and Windows + MuMu. i5-1235U, 12 threads, 14 GB RAM,
  `/dev/kvm`, **no NVIDIA GPU**.
- Use the laptop for **demos, live play, and light training** (start with 1 engine × 8 resident
  slots); use the **student cloud GPU credits** for PPO and heavier training. Linux is the dev/CI
  and headless-engine environment; Windows/MuMu is the live/demo environment.
- Local APK: **16.635.1** (`libg` `0b5802…`), the live/probe target. The 15.535.13 APK
  (`libg` `110aa2b5…`) is **not in this workspace**; if it is on the Windows side, run the lab
  there.
- `.venv` has `jpype1`, `gymnasium`, `pettingzoo`. `crforge_env` is not installed; use
  `PYTHONPATH=.resource/crforge/python` or install it editable.
- crforge tables: `~/.crforge/tables/16.635.8` and `.3`.
- Budget: student cloud GPU credits; rent GPU time only for PPO/evals. Do not build a permanent
  emulator fleet.

## QA harness (build before the learner)

See `ROADMAP.md` §3. Minimum viable version:

- `fixtures/` — fixed seeds, decks, action scripts, saved live frames.
- `adapters/` — one `reset/step/observe` surface for native, live, crforge.
- `checks/` — invariants, golden traces, parity, regression bank, exploit probes, lethal-spell,
  memory ablation, live canary.
- `report.json` — per-run metrics and pass/fail; CI fails on regression.

## Exact next actions

1. **Phase 0:** if the 15.535.13 APK is available, run FirstLight-Zero for ~50 rounds, using the
   cloud GPU for the learner if the laptop is too slow. Record matches/s and the improvement curve.
   This answers “can it improve?” independently of 16.635.
2. **Phase 0:** benchmark crforge F0 end to end; record the fallback number.
3. **Phase 0:** port/verify the 16.635.1 probe when Windows/Android is available; one full match,
   50-frame parity, throughput table.
4. **Phase 1:** reader + executor + scripted X-Bow v0; 20 live games, 0 illegal taps.
5. **Phase 2:** QA harness + 500 labelled games.
6. Write the engine decision memo and update `ROADMAP.md` in the same commit.

## Files touched in this revision

- Rewrote `ROADMAP.md` (native-first, collapsed to Phases 0–4 + gate).
- Renamed `ROADMAP_CRFORGE_ADAPTER.md` → `ROADMAP_CRFORGE.md` (optional fallback/CI only).
- Folded `ROADMAP_NULLS16_PROBE.md` into `ROADMAP.md` Phase 1; removed the separate file.
- Rewrote this handoff.
- Updated `README.md`, `README.zh-CN.md`, `docs/training.md`, `docs/capture_format.md`,
  `checkpoints/README.md`, and the dataset card for the native-first / retired-IL direction.
- Updated `crforge_bridge/README.md` to point at the fallback roadmap.

## Not mine — do not delete

`AIFeed.txt`, `resources.txt`, `Jason_Notes/`, and the root `LICENSE` deletion that currently
shows in `git status`. Verify with the owner before restoring or committing any of those.
