# Session handoff — 2026-11-13 (16.635.1 two-sided match, throughput and entity/HP verified on the A51)

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
  - **Probe header generated and wired:** `native_runner/probe/build_16_635_1_layout.h`
    (9 runner hooks + touch, each offset paired with a 16-byte prologue read
    from the target; plus the resolved core call offsets, verified field
    offsets, and explicit `kUnavailableOffset` sentinels for anchors that are
    still `AMBIGUOUS`/`MISSING`). Produced by
    `tools/native_analysis/gen_layout.py`, which refuses required unresolved
    anchors. The header is consumed by `cr_replay_probe.cpp` behind
    `CR_PROBE_TARGET_16_635_1` (`build_probe.ps1 -TargetBuild 16.635.1` or
    `build_probe.sh --target-build 16.635.1`).
  - **Linux build toolchain installed; 16.635.1 APK built and verified:**
    `.resource/android-tools/` has NDK r27c, build-tools r34 and apktool 2.12.1
    (all hashes verified against Google/the pinned apktool SHA-256).
    `builds/cr-ai-16.635.1.apk` (841 MB, SHA-256 `d5563f87…13ea`, signed with
    the local `CN=Firstlight CR Local Build` key) bundles
    `libcrprobe.so 9012e19f…9551` and the reference `libg.so 0b5802…e4eaa`.
    `builds/` is gitignored; rebuild from the APK if it is cleaned. This is a
    **single-process** APK (no engine-cluster Application/activities), the A51
    no-root path.
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
  - **GameState constructor store map recovered** (FINDINGS §3.8): allocation
    `0x230 → 0x240`; int `0x9c → 0xa0`; embedded sub-object `0xa0 → 0xa4`;
    context A `0x208 → 0x210`; context B `0x210 → 0x218`; optional player
    `0x218 → 0x220`; new zero fields at `0x208`, `0x228`, `0x238`. Full table in
    `docs/native/16.635.1/gamestate-constructor.json`. The *roles* still need
    live-golden confirmation.
  - **On-device smoke test passed (FINDINGS §3.10).** `builds/cr-ai-16.635.1.apk`
    installs and runs on the A51 (**SM-A515U**, Android 13 / SDK 33, arm64-v8a):
    all 9 runner hooks + touch install with **no `prologue mismatch`**, the five
    `anchor unavailable` lines print, and a live battle runs without a crash.
    On-device attestation matches the target `libg.so` (`0b5802…e4eaa`,
    build ID `179ac4bd…1c30`) and reports `content_version 16.635.93`,
    `content_fingerprint_sha256 553c8f15…04b4`, `hook_install_ok true`,
    `content_ready false`, `production_ready false`.
  - **Tick getter corrected (FINDINGS §3.10).** `kGameStateTick` was mapped to
    the pointer getter `0x011d3c90` (`ldr x0,[x0,#0xb0]; ret`), so the step log
    reported the low 32 bits of a heap pointer. The real getter is
    **`0x011d32f0`** (`ldr w0,[x0,#0x60]; ret`), byte-identical to the 15.535
    getter and immediately after `step`'s `ret`. After rebuild, a live battle
    logs `call=1 tick=1 … call=100 tick=100` at 20 Hz. The fix is a MANUAL
    override in `manual-overrides.json`; `offset-diff.json` and the layout
    header were regenerated. `diff_builds.py` now flags tiny-getter
    `tiny_shape_mismatch` and `gen_layout.py` refuses such a row unless a
    MANUAL override clears it.
  - **Still open:** confirm the constructor field roles from named live
    goldens and do the entity/HP (`hpComponentAccessor`) work. The
    Character/Projectile/AreaEffect/component vtables still need manual review.
  - **The four core anchors are resolved** (FINDINGS §3.11), all `MANUAL`:
    `kJsonMemberFind 0x00e38d60`, `kLogicCommandParse 0x00e553e4`,
    `kLocationTileMapEnsure 0x008ae750`, `kLocationTileMapLookup 0x00f2edec`.
    Each is proved by caller/loop structure, not a byte window. Only
    `kBuffAssetClassNameGetter` (rich buff telemetry) is still unavailable.
    On-device attestation now reports `two_sided_actions: true`.
    `kJsonMemberFind` changed its key ABI to a
    16-byte `StringView {data,size}`; the probe builds it under
    `CR_PROBE_TARGET_16_635_1`.
  - **Cold configure is open** (FINDINGS §3.13). The content-ready byte moved
    `0x6e8 → 0x720`; the mapping is proved structurally through the
    content-context constructor (`0xdcc180` old vs `0xef92f8` new, with the
    adjacent bool `0x6e9 → 0x721`) and confirmed live on 15.535.13, where a boot
    capture flips only `+0x6e8` (`00 → 01`) at `coldReady`. The byte is now
    build-pinned through the generated layout header. On the A51 the 16.635
    probe reports `coldReady` at `t≈8 s`, `configure {"a":1}` reaches the shape
    rejection (not the content gate), a real `native_match.json` configure
    succeeds (`matchReady: true`), 6,068 headless steps complete the match in
    ~3 s, and `observe` returns a non-empty observation.
  - **GameStateManager allocation size fixed** (FINDINGS §3.14): the probe asked
    for `0x230` bytes, but the 16.635 constructor stores at `+0x238`, so the
    second headless reset corrupted the heap and crashed in the destroy path.
    The size is now build-pinned (`0x230 → 0x240`) and used by both the cold
    configure worker and resident slots. Three consecutive 6,068-tick matches
    then ran on the A51 without a crash.
  - **Command context and replay ids fixed** (FINDINGS §3.15): the command
    context moved `0x208 → 0x210`; card plays use `ct=153` (was 86) and
    abilities `ct=189` (was 2). The probe and `NativeClashEnv` now select the
    ids from the target/client version.
  - **Live hand offset fixed and verified** (FINDINGS §3.16): the authoritative
    hand is `player+0x210` / count `player+0x21c`; the old `player+0x220`
    vector is the next/cycle queue. The final APK
    (`builds/cr-ai-16.635.1.apk`, SHA-256 `16dcc47d…3da9`) was installed on the
    A51: `observe` reports the engine hand `{0,3,7,2}`, and
    `play_immediate(hand_index=0, 9000/10000)` at tick 100 deploys (slot 0
    `26000000` leaves, slot 1 enters; elixir `7.78 → 4.80`; objects `6 → 7`).
  - **Two-sided multi-decision match verified** (FINDINGS §3.17). A fresh
    `MatchConfig` match with a 22-tick decision loop for both owners completed
    160 decisions / 56 actions in 13.1 s with **0 validation problems**, ended
    at tick 3601 with crowns `[0,1]` (a princess tower fell in regulation).
    The 600-tick gate run is 24 decisions / 10 actions in 1.9 s. Every action
    rotated the played slot to the deck's next card and paid the printed cost
    within the measured elixir regen bound; `adb logcat -b crash` is empty.
    Three runs were identical (deterministic seed).
  - **Throughput measured** (FINDINGS §3.18): engine-only 1.84 matches/s
    (6,068 ticks in 0.54 s); ordinary control `step 20` 0.17 matches/s;
    decision loop 0.077 matches/s (12.3 decisions/s, 4.3 actions/s); resident
    batch channel with 8 slots 0.89 matches/s (5,374 slot-ticks/s). The ~19 ms
    per-command `adb forward` round trip and the per-batch observation envelope
    are the bottleneck, not the engine. One A51 process is a ~0.9–1.8
    matches/s lane; 5–10 matches/s aggregate needs several engine lanes.
  - **Entity/HP path verified live** (FINDINGS §3.19). `observe` exposes every
    live object with owner, cardId (`-1` = tower), position, `hp`/`maxHp`.
    On the A51: all 6 native tower cells at 2400/1512 HP, a deployed Knight
    tracked 690 → 602 → 514 → 426 → 230 → 122 → gone, Archers 119 → 65, a
    princess tower 1512 → 1468. 8 samples, 0 problems, no crash.
  - **Hitpoint accessor anchored** (FINDINGS §3.20): the 15.535.13 virtual
    accessor `0x00f2db90` occurs byte-identically once in 16.635.1 at
    **`0x0108b474`** (EXACT; 7 vtable references relocate too). Added as
    `kHitpointsComponentAccessorOffset`, emitted in the generated layout header;
    `read_hitpoints()` still reads the component fields directly.
  - **New live tools** under `tools/native_analysis/` (README updated):
    `multi_decision_match.py`, `native_match_throughput.py`,
    `entity_hp_probe.py`, `resident_batch_throughput.py`.
  - Ghidra 12.2 headless is installed at `.resource/ghidra_download/` (requires JDK 25 at
    `.resource/jdk25/`; JDK 21 is rejected). Project:
    `/tmp/opencode/ghidra_project`, program `libg.so` in project `cr16`. The
    project has a stale lock and was last modified at 19:32; **auto-analysis
    did not finish** — there is no function at `0x11d1e68` and
    `0x11d2250`/`0x11d225c` are mis-split. `DecompileAnchors.java` can force a
    function at a known address, but the decompiler loses AArch64 argument
    registers until the project is fully re-analysed. Rerun with
    `-analysisTimeoutPerFile 7200`.
- **Commits (pushed to `origin/main`):**
  - `22dbef1` — `docs(native): record the 16.635.1 APK version code`
  - `0293a34` — `feat(build): add Linux probe/APK build and A51 deploy scripts`
  - `f255b35` — `feat(build): support a pinned 16.635.1 probe APK build`
  - `cecf1a6` — `docs(native): fix Ghidra project wording`
  - `06fd53c` — `docs: clarify recovered GameState constructor fields`
  - `df0e388` — `fix(native): log the selected probe target build`
  - `27519de` — `docs: record 16.635 probe wiring and Ghidra analysis status`
  - `63b8a93` — `feat(native): wire 16.635 probe hooks and recover GameState store map`
  - `3a7d308` — `feat(native): recover replay controller and generate 16.635 layout header`
  - `ca6994f` — `feat(native): diff 15.535.13 against 16.635.1 and add analysis tooling`
- The accidental root `LICENSE` deletion was restored and is not part of any commit.
- **Working tree is clean; `main` is pushed to `origin/main`.** The replay-controller
  recovery, verified overrides, field offsets, seeded vtable report, layout header,
  build-selectable probe wiring, GameState constructor store map, Linux toolchain and
  the built 16.635.1 APK are committed.
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
| **Probe + APK build/sign** | **Linux now** | NDK r27c, build-tools r34 and apktool live under `.resource/android-tools/`; `build_offline_apk.sh` and `build_probe.sh` mirror the PowerShell path |
| **Dynamic Frida / offset verification** | **User's Samsung A51 (SM-A515U, verified online 2026-11-12), arm64-v8a — no root needed** | Native ARM64, so no translation layer. Bootloader is locked and cannot be rooted; use Frida Gadget baked into the already re-signed APK, or instrumented `libcrprobe` + `adb logcat`. `frida-server` and Shizuku both need root and will **not** work on this device |
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
- The **16.635.1 probe compiles** on Linux (NDK r27c), and the patched APK
  **builds, signs, verifies, installs and runs on the A51**:
  `builds/cr-ai-16.635.1.apk` carries the exact `libg.so 0b5802…e4eaa`. All ten
  hooks (9 runner + touch) install with no prologue mismatch, and a live battle
  steps at 20 Hz with a correct tick (`call=N tick=N`) — FINDINGS §3.10. The 9
  runner hooks + touch are wired behind the generated build header (§3.9) and
  the GameState constructor store map is recovered (§3.8).
  `native_runner/supported_engine.json` is still 15.535.13 by design; the
  16.635 build is pinned separately in `docs/native/16.635.1/build.json`.
- **16.635.1 cold configure and repeated headless matches work on the A51**
  (§3.13–3.14): `coldReady` at `t≈8 s`, a real `native_match.json` configure
  reports `matchReady: true`, and three consecutive 6,068-tick matches run in
  ~4 s each with no crash.
- **The automatic action path works on 16.635.1** (§3.15–3.16): after
  `create_match`, `observe` reports the engine hand, and
  `play_immediate(HandAction(0, hand_index=0, 9000, 10000))` at tick 100
  replaces deck slot 0 (`26000000`) with slot 1 (`26000001`), drops elixir
  `7.78 → 4.80`, and raises the object count `6 → 7`.
- **A two-sided multi-decision match runs on 16.635.1** (§3.17): 160 decisions,
  56 actions for both owners, 0 validation problems, a full regulation match
  ending at tick 3601 (crowns `[0,1]`), deterministic across three runs, no
  crash. Every action rotates the hand slot, pays the printed cost (within the
  measured single/double-elixir regen bounds) and changes the object graph.
- **Entity/HP and tower HP are live** (§3.19): `observe` returns the object
  graph with `owner`, `cardId` (`-1` = tower), position and `hp`/`maxHp`; the
  six native tower cells and damage to units and a princess tower were tracked
  on-device with 0 problems.
- **One-probe throughput** (§3.18): engine-only 1.84 matches/s; 8 resident
  slots 0.89 matches/s with full observations; the ~19 ms control round trip
  and the per-batch observation envelope dominate.
- **The four core anchors are resolved on 16.635** (FINDINGS §3.11) and
  on-device attestation reports `two_sided_actions: true`; only
  `kBuffAssetClassNameGetter` (rich telemetry) remains unavailable.
- **The hitpoint accessor is pinned** (§3.20): `0x00f2db90 → 0x0108b474`, an
  EXACT byte-identical match, now in the generated layout header.
- The 16.635 crforge tests pass: 14 attestation/catalog tests, 6 bridge tests.

## What is not proven

- **Ability actions.** Card plays are proven two-sided; `AbilityAction` /
  `queue_ability_action_at` has not been exercised on 16.635 in this loop.
- **Multi-engine aggregate throughput.** One A51 process is measured; 5–10
  matches/s aggregate across several engine lanes has not been demonstrated.
- **The rich telemetry envelopes** (`observe-rich`: combat events, phase,
  visibility, remaining, tower-troop) stay disabled on 16.635 because their
  hook tables are still 15.535 layouts. The Character/Projectile/AreaEffect/
  component vtables still need manual review, and the recovered GameState
  constructor field *roles* still need live-golden confirmation.
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
- Linux build tools: `.resource/android-tools/` (NDK r27c, build-tools r34,
  apktool 2.12.1). `.resource/` and `builds/` are gitignored.
- Current deployable: `builds/cr-ai-16.635.1.apk` (single-process, local signing
  key). Rebuild with `./build_offline_apk.sh --target-build 16.635.1`.
- `.venv` has `jpype1`, `gymnasium`, `pettingzoo`. `crforge_env` is not installed; use
  `PYTHONPATH=.resource/crforge/python` or install it editable.
- crforge tables: `~/.crforge/tables/16.635.8` and `.3`.

## Next session: start here

### NEXT ACTION — scripted X-Bow match, then multi-engine throughput   [FINDINGS §3.17–3.20]

The multi-decision gate, the throughput baseline and the entity/HP path are
done on the A51. The next session:

1. **Scripted X-Bow on 16.635.** Build the FirstLight-Zero deck as a
   `MatchConfig` (verify the 16.635 form availability and tower-troop IDs
   first), add a deterministic placement policy (X-Bow at the bridge, Tesla
   behind it, Fireball/Log on value, Skeletons/Spirit cycle), and run one full
   self-play match through the decision loop. Record the match result and
   per-decision latency. This is the first milestone in `ROADMAP.md`.
2. **Ability actions.** Extend the decision loop with `AbilityAction` for a
   ready champion (Hero Knight) and confirm the engine receipts and any
   cooldown/charge telemetry on 16.635.
3. **Multi-engine throughput.** Start a second probe/engine instance and
   measure aggregate matches/s with the resident batch channel (currently
   0.89 matches/s per A51 process). Decide whether the training host is the
   laptop, cloud, or a device pool. Also drive real actions through
   `ResidentBatchCoordinatorV1`, not just empty steps.
4. **Static, non-blocking:** port the rich telemetry hook tables (15.535 →
   16.635) using the vtable diff, or explicitly retire `observe-rich` for
   16.635. The four core anchors and the hitpoint accessor are already done.

`command-check <json>` is available on the probe control socket: it parses one
command and returns the engine's execute code (0 = deployed, 9 = not in hand,
13 = insufficient elixir, 22 = placement/opening blocked). It is a diagnostic,
not a training API.

Known transient: `coldReady` can read `false` during the content
post-process/teardown window right after a match ends, then flips back to `true`.
If `configure` is issued in that window it returns the stale content error;
retry once the gate is `true` (a short wait/retry in the harness is reasonable).

Device note (2026-11-13): the A51's adbd dropped to `offline` twice under load
and recovered after the user disabled USB authorization timeout, disabled
"Verify apps over USB"/"Verify bytecode", and set a 10-minute display timeout.
If `adb shell` hangs while `adb devices` works, force-stop and relaunch the
app; a stale probe control thread can answer with an empty frame
("invalid native runner response") until the process restarts.

Also note: the probe control server is **single-client**. Close the resident
batch channel before issuing ordinary commands; an ordinary command issued
while a batch channel is open times out.

### 0. Run the built 16.635.1 APK on the A51 — **DONE 2026-11-12**

Passed; see FINDINGS §3.10. Keep this checklist for the next probe rebuild.

`builds/cr-ai-16.635.1.apk` is built, signed and verified. Enable USB debugging
on the A51, connect it, accept the host key, then:

```bash
./deploy_a51.sh
```

Success gate: `adb devices` shows the phone; the install succeeds; logcat shows
`probe target client=16.635.1 build16=1`, the ten `hook installed` lines (9
runner hooks + touch) and the `anchor unavailable` lines, with **no**
`prologue mismatch` and no crash. If `adb install -r` fails with
`INSTALL_FAILED_UPDATE_INCOMPATIBLE`, uninstall the old nulls build first and
let the game re-download its update data.

**Result (2026-11-12):** all gates passed on the SM-A515U (Android 13). A live
ladder battle ran; `GameStateManager::step` logged `tick=1,2,3,10,100` for
`call=1,2,3,10,100` at 20 Hz after the `kGameStateTick` correction. The lab
account was then maxed to card level 16 — live runs are at 16, so pin level 11
in the engine config for the v1450 baseline.

### 0b. Continue the static port

The 15.535.13 reference binary is present and the replay controller path is
recovered; the probe hooks are wired. What remains is on-device verification,
the four ambiguous core anchors, and naming the entity fields.

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
   `native_runner/probe/build_16_635_1_layout.h` now carries the 9 runner hooks
   + touch, the resolved core call offsets, the 6 verified field offsets, and
   `kUnavailableOffset`/`*Resolved = false` sentinels for the still-unresolved
   anchors. Regenerate with `gen_layout.py` after any anchor change; it refuses
   a required unresolved row.
5. **Non-code anchors are resolved** (FINDINGS §3.6): GameStateNew/Delete PLT
   stubs, the EGL GOT slot, ContextA, and the context root slot.
6. **The 9 runner hooks + touch are wired** into `cr_replay_probe.cpp` behind
   the build header (FINDINGS §3.9). The 16.635 probe and APK are built and the
   A51 smoke test passed (§0 / FINDINGS §3.10); the default build is still
   15.535.13. Next is confirming the recovered GameState constructor field roles
   (FINDINGS §3.8) and the entity/HP (`hpComponentAccessor`) work.
6b. **Tiny-getter anchors need shape checks.** `kGameStateTick` was one function
   off; the fix is MANUAL. `diff_builds.py` now emits `tiny_shape_mismatch` and
   `gen_layout.py` refuses it without an override. When adding an anchor that is
   a two-instruction getter, disassemble it and compare shape, not just bytes.
7. **Core anchors resolved** (FINDINGS §3.11): `kJsonMemberFind`
   `0x00e38d60`, `kLogicCommandParse` `0x00e553e4`, `kLocationTileMapEnsure`
   `0x008ae750`, `kLocationTileMapLookup` `0x00f2edec`. On the A51 the
   bootstrap now reports only `kBuffAssetClassNameGetter` unavailable and
   attestation flips `two_sided_actions` to **true**.
7b. **Cold configure is resolved** (FINDINGS §3.13). The content-ready byte
   moved `0x6e8 → 0x720` and is now build-pinned in the generated layout header.
   The 15.535 boot capture flips only `+0x6e8` at `coldReady`; the 16.635
   content-context constructor proves the new offset (`0xdcc180` vs `0xef92f8`,
   adjacent bool `0x6e9 → 0x721`). The same session also fixed the
   GameStateManager allocation size `0x230 → 0x240` (FINDINGS §3.14), which had
   overflowed the heap and crashed the second reset. Next: repeated matches and
   the queued-action path (see NEXT ACTION above).
8. Check `/tmp/opencode/ghidra_project` for the finished `cr16` analysis. The
   lock is stale; remove it only if no Ghidra process is running. If analysis
   timed out, rerun with JDK 25 + `-analysisTimeoutPerFile 7200`. Use
   `ghidra_scripts/DecompileAt.java` on the anchors above to name them.
9. Run `native_runner/tests/test_probe_build_layout.py` after any header
   regeneration; it re-reads every emitted prologue from the reference build.
   Keep the tree clean and re-run the tool commands above after any anchor
   change.

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

- Build/sign the patched 16.635.1 APK (Linux or Windows; both paths exist), then
  `adb install -r` it to the A51 over USB.

  **Linux (this workspace)** — NDK r27c, build-tools r34 and apktool 2.12.1 are under
  `.resource/android-tools/`. This builds a single-process APK (no engine-cluster
  Application/activities), which is the A51 no-root path:

  ```bash
  ./build_offline_apk.sh \
      --input-apk nr_16.635.1_release_951fe421.apk \
      --target-build 16.635.1 \
      --output-apk builds/cr-ai-16.635.1.apk
  ```

  The probe is compiled by `native_runner/probe/build_probe.sh` (same flags as the
  PowerShell script). The build verifies `libg.so` against
  `docs/native/16.635.1/build.json` and does **not** promote `supported_engine.json`;
  that stays on 15.535.13 until the on-device run passes.

  **Windows:**

  ```powershell
  .\build_offline_engine.ps1 `
      -InputApk .\nr_16.635.1_release_951fe421.apk `
      -TargetBuild 16.635.1 `
      -OutputApk .\builds\cr-ai-16.635.1.apk
  ```

- First smoke test on the A51: `./deploy_a51.sh` installs the APK, launches it, and streams
  `CRNativeProbe` for 60 seconds (or run `adb install -r` / `adb logcat -s CRNativeProbe`
  manually). Expect `probe target client=16.635.1 build16=1`, the ten `hook installed` lines
  (9 runner hooks + touch), and `anchor unavailable` lines for the four unresolved core
  anchors — but no `prologue mismatch` and no crash.
- If `adb install -r` fails with `INSTALL_FAILED_UPDATE_INCOMPATIBLE`, the local signing key
  differs from the installed nulls build. Uninstall first; that wipes
  `/data/data/nullsroyale.rel.free/update/`, so let the game re-download its update before
  trusting the probe's content fingerprint.
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
| native 16.635, engine-only (1 step) | A51 arm64 phone | 0.54 s | 1.84 (1 lane) | — | 0 | FINDINGS §3.18 |
| native 16.635, ordinary control, decision loop | A51 arm64 phone | 13.0 s | 0.077 | 12.3 | 0 | 160 decisions / 56 actions; RTT-bound (§3.18) |
| native 16.635, 1 engine × 8 resident slots | A51 arm64 phone | — | 0.89 | — | 0 | 5,374 slot-ticks/s, full observations (§3.18) |
| native 16.635, 2 engines × 16 slots | same | ? | ? | ? | ? | Target ~10 matches/s; open |

Decision rule: **≥5 matches/s aggregate → native primary. 1–5 → split. <1 → crforge primary.**
One A51 process is a ~0.9–1.8 matches/s lane, so the aggregate target needs
several engine lanes; the single-lane number alone does not decide it.

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
| `tools/native_analysis/capture_content_window.py` | Live content-context window capture (content-ready gate, FINDINGS §3.12) |
| `tools/native_analysis/multi_decision_match.py` | Two-sided decision loop + per-action validation (FINDINGS §3.17) |
| `tools/native_analysis/native_match_throughput.py` | Single-connection match/decision throughput (FINDINGS §3.18) |
| `tools/native_analysis/resident_batch_throughput.py` | 8-slot resident batch-channel throughput (FINDINGS §3.18) |
| `tools/native_analysis/entity_hp_probe.py` | Live entity/tower HP verification (FINDINGS §3.19) |
| `native_runner/probe/build_probe.sh` | Linux probe build (`--target-build`) |
| `build_offline_apk.sh` | Linux single-process patched APK build/sign |
| `deploy_a51.sh` | Install on the A51 and stream probe logs |
| `.resource/android-tools/` | NDK r27c + build-tools r34 + apktool (gitignored) |
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
