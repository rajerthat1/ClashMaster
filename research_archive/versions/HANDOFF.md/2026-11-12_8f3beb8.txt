# Session handoff — 2026-11-12 (native-first, post-commit)

> **Read [`ROADMAP.md`](ROADMAP.md) first.** It is the single source for the plan.
> This file is the starting point for the next session: current state, environment decision,
> and the exact next actions.

## State

- **Static port: the 15.535 reference build, the offset diff, and the replay-runner
  anchor set are now available.**
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
    diff with local-delta validation and `--overrides` corrections),
    `match_vtables.py` (vtable matching through `R_AARCH64_RELATIVE` relocations,
    including `--seeded` data-vtable recovery), `gen_layout.py` (build-pinned probe
    header from verified rows), and Ghidra export/decompile scripts.
  - **Offset diff result:** 59 `EXACT`, 31 `LIKELY`, 10 `MANUAL` (verified
    manual corrections), 37 `AMBIGUOUS` (mostly vtable/data anchors), 10
    `MISSING`. `offset-diff.json` has every row, old/new address, delta and
    flags. Manual corrections live in `manual-overrides.json` with evidence and
    are applied with `diff_builds.py --overrides`. The tool now ignores the
    generated 16.635 layout header so it never re-parses new offsets as "old".
  - **Replay controller trio + runner: resolved** (see FINDINGS §3.5):
    - Lifecycle `0xc87620 → 0x00d9a2cc`, FullUpdate `0xc88bbc → 0x00d9bba0`,
      Alternate `0xc899e0 → 0x00d9cc24` (EXACT). The old vtable `0x186e748`
      maps to new `0x1a20370`; the subclass copy `0x186f6d0` maps to
      `0x1a21308` (slot 2 `0xdac54c`).
    - Runner `0xc87ecc → 0x00d9ae04` (entry after `ret` at `0xd9ae00`; load call
      `0xd9aee4`, step call `0xd9b144`).
    - Clock global pause guard `0xcb6694 → 0x00dcda40` (same body and call
      position in fullUpdate).
  - **Non-code anchors resolved** (see FINDINGS §3.6): `kGameStateNew`
    `0x0198db50`, `kGameStateDelete` `0x0198db60` (PLT stubs for
    `_Znwm`/`_ZdlPv`), `kEglSwapBuffersGot` `0x01a9d6b0`, `kGameStateContextA`
    `0x00878c00`, `kGameStateContextRootSlot` `0x01b61d00`.
  - **Field shifts verified** (see FINDINGS §3.7): controller suspended
    `0x18c → 0x17c`, GameApp pending flags `0x1b8 → 0x1c0`, pending event
    `0x1bc → 0x1c4`. State `0x30`, accumulator `0x44`, speed `0x74` unchanged.
  - **Probe header generated:** `native_runner/probe/build_16_635_1_layout.h`
    (9 runner hooks + touch, each offset paired with a 16-byte prologue read
    from the target; plus verified field offsets). Produced by
    `tools/native_analysis/gen_layout.py`, which refuses unresolved anchors.
  - **Verified / high-confidence 16.635 anchors:**
    - Touch JNI **`0x0156f790`** (prologue identical to `kGameAppTouchPrologue`);
      internal `GameApp::onTouch` `0x01552b14`.
    - GameState family: constructor `0x011d1e68`, setup `0x011d225c`, destroy
      `0x011d2388`, **load `0x011d2dc4`**, **step `0x011d3174`**, tick `0x011d3c90`,
      deserialize header/body `0x011d45c8`/`0x011d4b14`.
      - `step` was manually corrected: `diff_builds.py` first confused it with
        `load` (both share a near-identical body; the prologue changed length).
        Verified by the old load call site `0xc87f64` and body match.
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
  - **Still open:** name the GameState constructor fields (FINDINGS §3.8; the
    old `+0xa0 → +0xa4` note does not match the disassembly — observed
    `0x9c → 0xa0`), then do the entity/HP (`hpComponentAccessor`) work. The
    Character/Projectile/AreaEffect/component vtables still need manual review.
  - Ghidra 12.2 headless is installed at `.resource/ghidra_download/` (requires JDK 25 at
    `.resource/jdk25/`; JDK 21 is rejected). Project:
    `/tmp/opencode/ghidra_project`, program `libg.so` in project `cr16`. The
    project has a stale lock and was last modified at 19:32; check whether
    auto-analysis finished before using it. If not, rerun with
    `-analysisTimeoutPerFile 7200`.
- **Commits (local until pushed):**
  - `3a7d308` — `feat(native): recover replay controller and generate 16.635 layout header`
  - `ca6994f` — `feat(native): diff 15.535.13 against 16.635.1 and add analysis tooling`
  - `98d94ad` — `docs: update handoff for the next session`
  - `40d9a45` — `docs: refocus roadmap on native 16.635 engine and X-Bow specialist`
  - `32d76f3` — `feat(crforge): add bridge, catalog, attestation, and F0 fixes`
- The accidental root `LICENSE` deletion was restored and is not part of any commit.
- **Working tree is clean.** The replay-controller recovery, verified overrides,
  field offsets, seeded vtable report, and layout header are committed.
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
| **Dynamic Frida / offset verification** | **User's Samsung A51 (SM-A515F), arm64-v8a — no root needed** | Native ARM64, so no translation layer. Bootloader is locked and cannot be rooted; use Frida Gadget baked into the already re-signed APK, or instrumented `libcrprobe` + `adb logcat`. `frida-server` and Shizuku both need root and will **not** work on this device |
| **Running the ported probe / demos** | **Windows + MuMu** | The probe is a native library loaded by the game; FirstLight supports MuMu. This is the runtime/demo environment |
| **15.535 FirstLight-Zero lab** | **Windows MuMu** if the 15.535 APK is there | The 15.535 probe is already built; no Frida required |
| **PPO / heavy training** | **Cloud GPU** (student credits) | Laptop is for engines/demos; use GPU time for the learner and inference server |

**Recommendation:** start the probe port on **Linux with static analysis today**; do the dynamic
work on the **user's Samsung A51** (already available, unrooted — see below); run/validate the
finished probe on **Windows MuMu**; train on the **cloud GPU**.

**Do not brute-force the translation layer.** Do not spend days making Frida, ptrace, or the
probe work under MuMu/AVD ARM translation, and do not try to root the A51 or route around it with
Shizuku. If dynamic verification is actually needed, **stop and ask the user to plug in the A51**.
The user can attach it on request; the only cost is waiting for them, versus days of dead-end
translation work.

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
- The **16.635.1 APK** is local (`nr_16.635.1_release_951fe421.apk`, `libg` `0b5802…`), and the
  static port now has every replay-runner anchor plus the non-code import/data
  anchors resolved (FINDINGS §3.5–§3.7). The probe is still **not rebuilt** for
  16.635; `native_runner/supported_engine.json` is still 15.535.13.
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
- 15.535.13 APK: **reference `libg.so` is in this Linux workspace** at
  `.resource/native/15.535.13/reference/libg.so` (SHA-256 `110aa2b5…9783`).
  `.resource/` is gitignored; re-extract from the APK if it is cleaned.
- 16.635.1 APK: local in the repo root; reference `libg.so` at
  `.resource/native/16.635.1/reference/libg.so` (`0b5802…`).
- `.venv` has `jpype1`, `gymnasium`, `pettingzoo`. `crforge_env` is not installed; use
  `PYTHONPATH=.resource/crforge/python` or install it editable.
- crforge tables: `~/.crforge/tables/16.635.8` and `.3`.

## Next session: start here

### 0. Continue the static port

The 15.535.13 reference binary is present; the replay controller path is now
recovered. What remains is porting it into the probe and naming the entity
fields.

1. Read `docs/native/16.635.1/FINDINGS.md` and `offset-diff.json`. Every verified
   claim has a reproduce command.
2. **Replay controller trio + runner are resolved** (FINDINGS §3.5): lifecycle
   `0x00d9a2cc`, fullUpdate `0x00d9bba0`, alternate `0x00d9cc24`, runner
   `0x00d9ae04`. Regenerate the vtable report if needed:
   `match_vtables.py OLD NEW --seeded-only --seeded 'replayController=0x186e748:4:4:0xd9cc24'`.
3. **The diff tool's near-duplicate confusion is fixed.** `kGameStateStep` is
   `0x011d3174`; `diff_builds.py --overrides docs/native/16.635.1/manual-overrides.json`
   applies it and the other verified manual anchors. Do not edit
   `offset-diff.json` by hand.
4. **The 16.635 probe layout header exists:**
   `native_runner/probe/build_16_635_1_layout.h` (9 runner hooks + touch, with
   prologues read from the target). Regenerate with `gen_layout.py` after any
   anchor change; it refuses unresolved rows.
5. **Non-code anchors are resolved** (FINDINGS §3.6): GameStateNew/Delete PLT
   stubs, the EGL GOT slot, ContextA, and the context root slot.
6. **Next actual work:** port the 9 runner hooks + touch into
   `cr_replay_probe.cpp` using the new build header, then verify on the native
   A51. Before trusting live play, name the GameState constructor fields
   (FINDINGS §3.8) and do the entity/HP (`hpComponentAccessor`) work.
7. Check `/tmp/opencode/ghidra_project` for the finished `cr16` analysis. The
   lock is stale; remove it only if no Ghidra process is running. If analysis
   timed out, rerun with JDK 25 + `-analysisTimeoutPerFile 7200`. Use
   `ghidra_scripts/DecompileAt.java` on the anchors above to name them.
8. This session's work is committed as `3a7d308`. Keep the tree clean and
   re-run the tool commands above after any anchor change.

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

### 2. ARM64 phone — dynamic verification (user's Samsung A51; ask them to connect it)

Do this only once §0–1 has candidate offsets worth checking. **Ask the user to plug in the A51**;
do not substitute an emulator or translation layer.

- Build/sign the patched 16.635.1 APK (Windows side, where the PowerShell build scripts and
  NDK/apktool/apksigner live), then `adb install` it to the A51 over USB.
- Instrument in one of two no-root ways:
  - **Frida Gadget:** add `libfrida-gadget.so` (arm64) plus an embedded config to the APK and load
    it from the same smali bootstrap that loads `libcrprobe`, ideally before `libg.so`. Connect
    with `frida -U Gadget`.
  - **Instrumented probe:** add logging/asserts to `libcrprobe`, rebuild, sign, reinstall, and read
    `adb logcat`. Slower loop, but no extra tooling.
- Hook candidate functions; verify reads against the screen.
- Deploy known cards at known times; compare memory changes to human ground truth.
- Generate a verified prologue for every hook you keep.
- Only then rebuild the probe layouts/hook table and sign the APK.
- `frida-server` and Shizuku are **not** options on this device: locked bootloader, no root, and
  ADB shell (UID 2000) cannot ptrace a non-debuggable app.

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

- Do not start dynamic Frida work on Windows MuMu or Linux x86_64 AVD, and do not attempt to make
  the translation layer work. Use the user's native ARM64 Samsung A51; ask them to connect it.
- Do not train on `IL_Replay`.
- Do not make crforge the critical path.
- Do not blindly port FirstLight-Zero weights; verify the 16.635 mapping first.
- Do not treat mirror win rate as ladder evidence; use the per-archetype evaluation matrix.
- Do not delete `LICENSE`, `resources.txt`, or `jason_notes/`.
