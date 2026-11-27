# Session handoff — 2026-11-12 (native-first, post-commit)

> **Read [`ROADMAP.md`](ROADMAP.md) first.** It is the single source for the plan.
> This file is the starting point for the next session: current state, environment decision,
> and the exact next actions.

## State

- **Static port: the 15.535 reference build is now available and the offset diff is done.**
  See [`docs/native/16.635.1/FINDINGS.md`](docs/native/16.635.1/FINDINGS.md) (evidence log)
  and [`docs/native/16.635.1/offset-diff.json`](docs/native/16.635.1/offset-diff.json).
  - **15.535.13 APK acquired** from APKPure (757.6 MB, version code 150535013). The APK
    hash differs because APKPure re-signs, but the extracted
    `.resource/native/15.535.13/reference/libg.so` matches the probe's required
    SHA-256 `110aa2b5…9783` exactly (build ID `90e6f351…5a83`, 27,554,368 bytes).
    This is the exact build the probe attests. `.resource/` is gitignored.
  - **16.635.1 libg.so** extracted to `.resource/native/16.635.1/reference/`
    (SHA-256 `0b580222…e4eaa`, build ID `179ac4bd…1c30`).
  - **New tools** (all under `tools/native_analysis/`, README has usage):
    `cr_elf.py` (info/strings/xrefs/calls/refs/imm/disasm/symbols, section-stripped
    ELF), `relocate_hooks.py` (all probe prologues), `diff_builds.py` (old→new offset
    diff with local-delta validation), `match_vtables.py` (vtable matching through
    `R_AARCH64_RELATIVE` relocations), and Ghidra export/decompile scripts.
  - **Offset diff result:** 59 `EXACT`, 32 `LIKELY` (11 delta-constrained), 40
    `AMBIGUOUS` (all vtable/data anchors), 16 `MISSING`. `offset-diff.json` has every
    row, old/new address, delta and flags.
  - **Verified / high-confidence 16.635 anchors:**
    - Touch JNI **`0x0156f790`** (prologue identical to `kGameAppTouchPrologue`);
      internal `GameApp::onTouch` `0x01552b14`.
    - GameState family: constructor `0x011d1e68`, setup `0x011d225c`, destroy
      `0x011d2388`, **load `0x011d2dc4`**, **step `0x011d3174`**, tick `0x011d3c90`,
      deserialize header/body `0x011d45c8`/`0x011d4b14`.
      - `load` was manually corrected: `diff_builds.py` first confused it with `step`
        (both share a near-identical body; the prologue changed length). Verified by
        the old load call site `0xc87f64` and body match.
      - `step` was found through its caller: old clock `0xc890bc+0xfc` calls step;
        new clock `0xd9c284+0xfc` calls **`0x011d3174`**. Its old body
        (`ldrb w20,[x0,#0x1e8]`) matches.
    - Replay controller: clock `0x00d9c284`, alternate update `0x00d9cc24` (`EXACT`);
      replay JSON setter `0x00da1f90`, manager singleton `0x00da0658`, getter
      `0x00da0670` (`LIKELY`, string-anchored).
    - `kJsonParse 0x0139f838`, `kByteStreamResetRead 0x01397568`,
      `kByteStreamFlipRead 0x01397578`, `kByteStreamResetChecksum 0x01399518`.
    - Hand model (16.635): `holder + 0x290` unchanged; hand array `model + 0x210`
      (15.535 `0x220`); next queue `model + 0x208` (15.535 `0x210`). Accessors
      `0x0109ae58`, `0x01094d1c`, queue helpers `0x0109c8b4/0x0109c8e8`.
  - **Vtable diff** (`vtable-diff.json`): Character `0x01a4fbd0`, Projectile
    `0x01a50348`, AreaEffect `0x01a4f9a0`, MovementComponent `0x01a55a10`,
    AttackComponent `0x01a558b0` (LIKELY), BuffComponent still unmapped. Runtime/action
    data vtables are mostly `LIKELY`; a few share a base and need manual review.
  - **Still open:** new replay `lifecycle`/`runner`/`fullUpdate` (old bodies do not
    match even 48-byte windows; the module was refactored). Old lifecycle and
    fullUpdate are vtable slots at old data offsets `0x186e748`/`0x186e758`; diff that
    vtable (and `0x186f6d0`) to recover all three. `GameStateNew/Delete` are PLT thunks
    to operator new/delete; `kEglSwapBuffersGot` is a GOT slot —
    both need import/relocation resolution, not code matching.
  - Ghidra 12.2 headless is installed at `.resource/ghidra_download/` (requires JDK 25 at
    `.resource/jdk25/`; JDK 21 is rejected). Project:
    `/tmp/opencode/ghidra_project`, program `libg.so` in project `cr16`. Check whether
    auto-analysis finished; if not, rerun with `-analysisTimeoutPerFile 7200`.
- **Commits on `origin/main`:**
  - `32d76f3` — `feat(crforge): add bridge, catalog, attestation, and F0 fixes`
  - `40d9a45` — `docs: refocus roadmap on native 16.635 engine and X-Bow specialist`
- The accidental root `LICENSE` deletion was restored and is not part of either commit.
- **Working tree is dirty with this session's uncommitted work** (see `git status`):
  `HANDOFF.md`, `docs/native/16.635.1/*`, `tools/native_analysis/*`. Commit before the
  next session if you want the anchors preserved in git.
- `ROADMAP.md` now has the **3a mirror bootstrap / 3b matchup pool** split.
- `ROADMAP_CRFORGE.md` is the optional fallback/CI roadmap. `ROADMAP_NULLS16_PROBE.md` was folded
  into `ROADMAP.md` Phase 1 and removed.

## Decision

- **Primary engine: E — the real game engine, headless via the FirstLight probe/runner.**
  Target client **16.635.1**. Frida/static analysis ports the probe; an extracted `libg.so`
  headless process is preferred, emulator-hosted headless as fallback.
- **crforge is fallback/CI only.** Do not make it the critical path or extend `observe-rich` while
  native is primary.
- **`IL_Replay` is retired as a training corpus.** Keep the IL/General/Everlight checkpoints as
  opponents, baselines, and fallbacks.
- **Model: X-Bow specialist first.** The deck is exactly FirstLight-Zero's: X-Bow, Hero Knight,
  Evo Archers, Evo Tesla, Skeletons, Electro Spirit, Fireball, The Log. Reuse the 1.17M
  CNN + LSTM recipe; v1450 is a direct benchmark/opponent and an initialization candidate.
- **Training order:** 3a mirror bootstrap → 3b matchup pool (General/IL/Hog/scripted/live).
- **First milestone:** scripted X-Bow plays live on 16.635.1, then a learned bot that beats it.

## Environment decision (important)

| Work | Environment | Why |
| --- | --- | --- |
| **Static analysis / offset diff** | **Linux now** | Ghidra/REA/LIEF/Capstone work on x86_64; no Frida needed |
| **Dynamic Frida / offset verification** | **Rooted ARM64 Android phone** (or ARM64 Linux host) | Windows x86_64 MuMu runs `libg.so` under ARM translation; Frida is unreliable. Linux x86_64 AVD has the same translation problem |
| **Running the ported probe / demos** | **Windows + MuMu** | The probe is a native library loaded by the game; FirstLight supports MuMu. This is the runtime/demo environment |
| **15.535 FirstLight-Zero lab** | **Windows MuMu** if the 15.535 APK is there | The 15.535 probe is already built; no Frida required |
| **PPO / heavy training** | **Cloud GPU** (student credits) | Laptop is for engines/demos; use GPU time for the learner and inference server |

**Recommendation:** start the probe port on **Linux with static analysis today**; do the dynamic
Frida work on a **cheap used rooted ARM64 phone**; run/validate the finished probe on **Windows
MuMu**; train on the **cloud GPU**. Do not spend days fighting Frida in Windows MuMu.

If Jason's 16.635 notes/code are available, use them first. A port from scratch is 2–4 months
part-time; with his notes it can be weeks.

## What is proven

- **FirstLight-Zero** is an X-Bow 3.0 mirror specialist trained from self-play only:
  1.17M parameters, **97.5%** against the 16.8M IL model in 40 games, **~1.5M matches in 43.3
  hours**. Published run: 1,024 episodes / 104 s ≈ **9.85 matches/s aggregate**; the shipped
  example uses 2 engine processes × 8 resident slots and one GPU.
- **The target deck is exactly FirstLight-Zero's**, so the recipe and v1450 are directly relevant.
- **crforge M1** stands: a full `BattleEnvV1` match completes through the bridge
  (`illegal: 0`, `truncated: 0`).
- **F0** reduced crforge rejected actions from **9 to 1**. The remaining reject is an open adapter
  bug, isolated to one card/owner sequence; not an engine failure.
- crforge raw engine: **8,371 decisions/s at 256 envs**. The bridge is **20–30 decisions/s**;
  the gap is `BattleEnvV1` observation assembly and JPype conversion, not crforge.
- The **16.635.1 APK** is local (`nr_16.635.1_release_951fe421.apk`, `libg` `0b5802…`), but the
  probe is **not ported**; `native_runner/supported_engine.json` is still 15.535.13.
- The 16.635 crforge tests pass: 14 attestation/catalog tests, 6 bridge tests.

## What is not proven

- The 16.635.1 probe port and its throughput on this hardware.
- The live reader/executor loop on 16.635.1.
- The 16.635 form/tower-troop mapping of the FirstLight-Zero deck, whether v1450 (trained at
  level 11) transfers across the 15.535 → 16.635 rule change, and how it behaves at maxed levels.
- That a learned X-Bow bot beats a competent scripted bot and then humans.
- That the V4/IL/Hog opponents behave correctly on 16.635 — validate before trusting the pool.

## Hardware / environment

- **Dual-boot laptop:** Linux (current) and Windows + MuMu. i5-1235U, 12 threads, 14 GB RAM,
  `/dev/kvm`, **no NVIDIA GPU**.
- Laptop: demos, live play, light training. Start with **1 engine × 8 resident slots**.
- Cloud GPU: student credits; use for the 15.535 lab and PPO.
- 15.535.13 APK: **not in this Linux workspace**. If it is on the Windows side, run the lab there.
- 16.635.1 APK: local in the repo root.
- `.venv` has `jpype1`, `gymnasium`, `pettingzoo`. `crforge_env` is not installed; use
  `PYTHONPATH=.resource/crforge/python` or install it editable.
- crforge tables: `~/.crforge/tables/16.635.8` and `.3`.

## Next session: start here

### 0. Continue the static port (new)

The 15.535.13 reference binary is present; the long pole is now matching the
remaining hooks, not acquiring the build.

1. Read `docs/native/16.635.1/FINDINGS.md` and `offset-diff.json`. Every verified
   claim has a reproduce command.
2. **Recover the replay controller trio** (`lifecycle`, `runner`, `fullUpdate`):
   - old lifecycle/fullUpdate/alternate are slots in the vtable at old
     `0x186e748` (second copy `0x186f6d0`). Run `match_vtables.py` logic on that
     data address (or expose it in the probe file list) and read the new slots.
   - `runner` is not a vtable slot. Its load call is old `0xc87f64` (runner+0x98);
     new load callers are `0xd9a590`, `0xd9ab18`, `0xd9aee4`. The function
     containing `0xd9aee4` and step call `0xd9b144` is the new runner; walk back to
     the previous `ret`/`b` to get its entry.
3. **Fix the diff tool's near-duplicate confusion.** `kGameStateStep` is still
   recorded as the load address in `offset-diff.json`; correct it to
   `0x011d3174`. Either add a manual override file or use caller-relative
   relocation (old call site + mapped caller) as the second pass.
4. **Generate the 16.635 probe layout header** from the verified rows: offset +
   16-byte prologue read from the new binary. Start with the 9 runner hooks +
   touch; do not emit offsets without a verified new prologue.
5. **Resolve the non-code anchors:** `GameStateNew/Delete` are PLT thunks to
   operator new/delete (map through dynsym/GOT); `kEglSwapBuffersGot` is a GOT
   slot; `kGameStateContextRootSlot`/`kGameStateContextA` are data slots.
6. **Then** verify the GameState constructor field changes (`+0xa0 → +0xa4`,
   new fields at `+0x228`) and do the entity/HP (`hpComponentAccessor`) work.
7. Check `/tmp/opencode/ghidra_project` for the finished `cr16` analysis. If it
   timed out, rerun with JDK 25 + `-analysisTimeoutPerFile 7200`. Use
   `ghidra_scripts/DecompileAt.java` on the anchors above to name them.
8. Commit this session's work before the next session (`HANDOFF.md`,
   `docs/native/16.635.1/`, `tools/native_analysis/`).

### 1. Linux — static probe port (original plan)

1. Confirm the repo is clean and the commits are present:
   ```bash
   git status
   git log --oneline -3
   ```
2. Extract `lib/arm64-v8a/libg.so` from `nr_16.635.1_release_951fe421.apk`; record SHA-256
   (`0b5802…`) and build ID.
3. Put the 15.535 and 16.635 `libg.so` side by side and diff functions in Ghidra/REA.
4. Start a knowledge file with every anchor, offset, prologue, and evidence. Do not rely on
   memory or chat context.
5. Use the existing tools:
   - `.resource/REA/` — Ghidra/Hopper/IDA MCP for static analysis and evidence.
   - `.resource/cr-memory-reader/tools/` — `find_string_xrefs.py`, `find_arm64_calls.py`,
     `disasm_range.py`.
   - `.resource/cr-memory-reader/My Own Findings.md` — 15.535 anchors and the Frida workflow.
   - `native_runner/probe/` — the actual probe source; port only the hooks/layouts it needs.
6. Find the 16.635 equivalents of: `holder`, `hpComponentAccessor`, battle object/tick, packet
   handlers (18411 card deploy, 26349 match start), elixir, hand + `0x290`-style array, tower HP,
   entity list, DataIDs/forms.
7. In parallel, run the crforge/QA harness work. It does not need the probe.

### 2. ARM64 phone — dynamic verification (when available)

- Install `frida-server`; attach to the game.
- Hook candidate functions; verify reads against the screen.
- Deploy known cards at known times; compare memory changes to human ground truth.
- Generate a verified prologue for every hook you keep.
- Only then rebuild the probe layouts/hook table and sign the APK.

### 3. Windows MuMu — run and validate

- If the 15.535 APK is available, run FirstLight-Zero for ~50 rounds on the cloud GPU learner and
  record matches/s plus the improvement curve.
- Once the 16.635 probe is built: one full match, 50-frame parity, `illegal: 0`, `truncated: 0`,
  then live/demo runs.

### 4. Cloud GPU — training

- FirstLight-Zero recipe on 15.535 for the lab; later the 16.635 X-Bow run.
- 3a mirror bootstrap, then 3b matchup pool.
- Keep the laptop at 1 engine × 8 slots; use remote engines or the cloud host for larger runs.

## Phase 0 measurement table

Fill this with numbers, not opinions:

| Engine / path | Hardware | Per-slot latency | Aggregate matches/s | Decisions/s | illegal / truncated / rejected | Notes |
| --- | --- | ---: | ---: | ---: | --- | --- |
| FirstLight-Zero, 15.535 native | Linux + GPU | ~1.6 s | ~9.85 | — | — | Published; extracted engine |
| FirstLight-Zero, 15.535 native | laptop engines + cloud GPU | ? | ? | ? | ? | 50-round lab run, if APK acquired |
| crforge end-to-end after F0 | this host | ? | ? | 20–30 (bridge) | 1 reject | Fallback baseline |
| native 16.635, 1 engine × 8 slots | Windows/MuMu or ARM64 phone/AVD | ? | ? | ? | ? | Probe port required |
| native 16.635, 2 engines × 16 slots | same | ? | ? | ? | ? | Target ~10 matches/s |

Decision rule: **≥5 matches/s aggregate → native primary. 1–5 → split. <1 → crforge primary.**

## Current crforge state (fallback)

- `evaluate --games 2`: General 2–0 IL, both terminated, ticks 5,425 and 6,218,
  `truncated: 0`; game 1 reports 1 rejected action after F0 (was 9).
- F0 in place: numpy conversion for JPype arrays, pending/`last_run_refusal`, loud `_giving`,
  typed `CARD_PENDING`, pending-slot mask.
- Open: close the last reject, then run 20 games with `illegal: 0`, `truncated: 0`.
- Do **not** extend `observe-rich` while native is primary.

## Key paths

| Path | Purpose |
| --- | --- |
| `ROADMAP.md` | The plan: decision, Phases 0–4, QA harness, gates |
| `ROADMAP_CRFORGE.md` | Optional crforge fallback/CI roadmap |
| `native_runner/probe/` | Native hook source; 16.635 port target |
| `native_runner/cr_native_env.py` | Engine TCP/JSON seam |
| `native_runner/battle_env.py` | `BattleEnvV1`, attestation, observation assembly |
| `.resource/FirstLight-Zero/` | X-Bow recipe, v1450 checkpoints, training loop |
| `.resource/cr-memory-reader/` | Frida reader, 15.535 findings, x86_64 fallback |
| `.resource/REA/` | Static reverse-engineering MCP/toolkit |
| `checkpoints/` | V4/IL/Hog opponents and baselines |
| `nr_16.635.1_release_951fe421.apk` | 16.635.1 client; probe port target |

## Guardrails

- Do not start dynamic Frida work on Windows MuMu or Linux x86_64 AVD; use an ARM64 environment.
- Do not train on `IL_Replay`.
- Do not make crforge the critical path.
- Do not blindly port FirstLight-Zero weights; verify the 16.635 mapping first.
- Do not treat mirror win rate as ladder evidence; use the per-archetype evaluation matrix.
- Do not delete `LICENSE`, `resources.txt`, or `jason_notes/`.
