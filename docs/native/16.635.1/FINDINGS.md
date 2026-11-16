# libg.so 16.635.1 — static analysis notebook

Status: **in progress** (started 2026-11-12). This file is the evidence log for the
16.635.1 probe port. Every claim below names the tool command that reproduces it.
Do not port an offset from this file without the matching prologue fingerprint.

## 1. Binary identity

| Field | Value |
| --- | --- |
| Source | `nr_16.635.1_release_951fe421.apk` → `lib/arm64-v8a/libg.so` |
| Local copy | `.resource/native/16.635.1/reference/libg.so` (gitignored) |
| Size | 29,408,752 bytes |
| SHA-256 | `0b580222e5a3502cf153bb5e6297ec32d2b154b605b777c74873dd75722e4eaa` |
| Build ID | `179ac4bd38c205bc5041d372a79f1c2f929b1c30` |
| ABI | AArch64, PIE shared object, stripped, **section headers removed** |
| Package | `nullsroyale.rel.free` (client 16.635.1) |

Section headers are gone (5 sections only: build-id note, dynstr, dynamic, shstrtab),
so address translation must use the program headers. For the main code segment
`p_offset == p_vaddr`, which is why `libg + 0x…` offsets still work.

```
LOAD off=0x00000000 va=0x00000000 filesz=0x0198f5f0 RX   <- code + rodata
LOAD off=0x0198f5f0 va=0x019935f0 filesz=0x0010a250 RW
LOAD off=0x01a99840 va=0x01aa1840 filesz=0x000a9ce8 RW
LOAD off=0x01bd5000 va=0x01bdd000 filesz=0x0002bc24 RX
LOAD off=0x01c01000 va=0x01c09000 filesz=0x00004794 RW
LOAD off=0x01c08000 va=0x03c08000 filesz=0x00003cb0 R    <- .dynsym
```

The dynamic symbol table survived: 569 symbols, 86 of them `Java_com_supercell_*`
JNI entry points plus `JNI_OnLoad`. There is **no RTTI** for game classes
(no `_ZTV`/`_ZTI` names outside libc++ imports), so vtables must be found from
code/data patterns, not typeinfo.

Reproduce:

```bash
python3 tools/native_analysis/cr_elf.py info .resource/native/16.635.1/reference/libg.so
python3 tools/native_analysis/cr_elf.py symbols .resource/native/16.635.1/reference/libg.so --grep Touch
```

## 2. Tooling

| Tool | Purpose |
| --- | --- |
| `tools/native_analysis/cr_elf.py` | `info`, `strings`, `xrefs`, `calls`, `refs`, `imm`, `disasm`, `symbols` |
| `tools/native_analysis/relocate_hooks.py` | match all 15.535 probe prologues against a build |
| `tools/native_analysis/diff_builds.py` | old→new offset diff; `--overrides` applies verified manual corrections |
| `tools/native_analysis/match_vtables.py` | vtable matching; `--seeded` resolves a data vtable through one surviving slot |
| `tools/native_analysis/gen_layout.py` | emit the build-pinned probe header (offset + 16-byte prologue) |
| `docs/native/16.635.1/manual-overrides.json` | verified non-byte-diff anchors with evidence |
| `docs/native/16.635.1/field-offsets.json` | struct field shifts confirmed by disassembly |
| `docs/native/16.635.1/hook-relocation.json` | machine-readable relocation report |
| `.resource/ghidra_download/ghidra_12.2_PUBLIC` | Ghidra 12.2 headless (JDK 25 at `.resource/jdk25`) |

`cr_elf.py` resolves VAs through `PT_LOAD`, so it works on the section-stripped
binary. `xrefs`/`calls` are vectorised with numpy and take well under a second on
the 25 MB code segment.

## 3. Verified anchors

### 3.1 Touch entry (JNI) — **port-ready**

| | |
| --- | --- |
| 15.535.13 | `kGameAppTouchOffset = 0x013d0be0` |
| 16.635.1 | **`0x0156f790`** `Java_com_supercell_titan_GameApp_nOnTouchEvent` (size 0x70) |
| Prologue (identical) | `fd 7b bc a9 f8 5f 01 a9 f6 57 02 a9 f4 4f 03 a9` |

The 16.635 prologue is byte-for-byte the 15.535 `kGameAppTouchPrologue`, and the
address is an exported JNI symbol. This is the one hook that relocates with no
new reverse engineering.

The JNI thunk saves `w2..w5` and tail-branches to the internal handler at
**`0x01552b14`** with `x0 = GameApp singleton` (`[[0x1a9c0a8]]`):

```
0x156f7a4  adrp x24, #0x1a9c000
0x156f7b0  ldr  x24, [x24, #0xa8]
0x156f7bc  ldr  x0,  [x24]
0x156f7dc  mov  w1, w22          ; action
0x156f7e0  mov  w2, w21          ; x
0x156f7e4  mov  w3, w20          ; y
0x156f7e8  mov  w4, w19          ; pointer id
0x156f7fc  b    #0x1552b14       ; GameApp::onTouch(GameApp*, action, x, y, pointer)
```

Internal handler prologue: `ff 43 01 d1 fd 7b 02 a9 f5 1b 00 f9 f4 4f 04 a9`
(`sub sp, sp, #0x50; stp x29,x30,[sp,#0x20]; str x21,[sp,#0x30]; stp x20,x19,[sp,#0x40]`).

Reproduce:

```bash
python3 tools/native_analysis/cr_elf.py symbols … --grep Touch
python3 tools/native_analysis/cr_elf.py disasm … 0x156f790 0x156f800
python3 tools/native_analysis/cr_elf.py disasm … 0x1552b14 0x1552b60
```

### 3.2 Hand / deck model — **layout changed, functions located**

15.535 findings (`.resource/cr-memory-reader/My Own Findings.md` §11/§14):

```
holder + 0x290 -> hand model
model  + 0x220 -> visible hand deck-index array (4 x s32)
model  + 0x210 -> next-card queue
queue  + 0x20  -> array pointer, queue + 0x2c -> count
```

16.635.1 evidence:

| Item | 16.635.1 | Evidence |
| --- | --- | --- |
| holder → hand model | **`holder + 0x290`** (unchanged) | `0x939f54: ldr x0,[x19,#0x290]` then call below |
| visible hand array | **`model + 0x210`** | `0x109ae58` / `0x1094d1c` |
| hand deck-index accessor | **`0x0109ae58`** | `ldr x8,[x0,#0x210]; ldr w0,[x8,w1,sxtw #2]; ret` |
| hand card-by-slot | **`0x01094d1c`** | reads `[x0,#0x210][w1]`, rejects `-1` (`cmn w19,#1`) |
| next-card queue | **`model + 0x208`** | `0x109ae64: add x0,x0,#0x208; b 0x109c8b4` |
| queue count / array | **`+0x24` / `+0x18`** | `0x109c8e8: ldr w8,[x0,#0x24]; ldr x8,[x0,#0x18]; ldr w0,[x8]` |
| second queue view | **`+0x14` / `+0x08`** | `0x109c8b4: ldr w8,[x0,#0x14]; ldr x9,[x0,#0x08]` |

Function prologues:

```
0x0109ae58  08 08 41 f9 00 d9 61 b8 c0 03 5f d6
0x01094d1c  fd 7b bd a9 f6 57 01 a9 f4 4f 02 a9 fd 03 00 91
0x0109c8e8  08 24 40 b9 1f 05 00 71 8b 00 00 54
```

Hand slots are 4 and empty slots hold `-1`. The callers confirm slot semantics:
`0x12dfb48` computes `slot = (value % 4) - 1`; `0x939f5c` passes a hand index
after loading the model from `holder + 0x290`.

**Do not carry 15.535 offsets over.** In 16.635 the inner hand fields moved by
different amounts: array `0x220 → 0x210` (−0x10), queue base `0x210 → 0x208`
(−0x8), queue array/count `0x20/0x2c → 0x18/0x24` (−0x8).

Reproduce:

```bash
python3 tools/native_analysis/cr_elf.py disasm … 0x109ae40 0x109ae70
python3 tools/native_analysis/cr_elf.py calls  … 0x109ae58
python3 tools/native_analysis/cr_elf.py disasm … 0x109c8b4 0x109c910
```

### 3.3 Replay / game-state manager — **functions located, class not yet named**

Distinctive log strings anchor this path:

| String | VA | Xref | Meaning |
| --- | --- | --- | --- |
| `replay data passed as NULL to game state manager` | `0x32f041` | `0xda1fac` | manager method rejects null replay data |
| `replay json object is null` | `0x2ded62` | `0xd9a564` | replay JSON → battle conversion |
| `replay load without battle or plugin` | `0x335ad0` | `0x12d9434` | replay load precondition |
| `replay is missing battle log json` | `0x326885` | `0xd9aa50` | replay validation |

Manager method `0x010da1f90` (`stp x29,x30,[sp,#-0x20]!; stp x20,x19,[sp,#0x10]; mov x29,sp; mov x20,x1`):

```
0xda1f90  x0 = manager, x1 = replay JSON string
0xda1f9c  x20 = x1
0xda1fa4  if x1 == 0 -> "replay data passed as NULL to game state manager"
0xda1fb8  release old this+0x88 object
0xda1fd0  parse JSON (0x139f838) -> store at this+0x88
0xda1ff8  this+0x28 (battle/plugin) -> 0x12daf38(this+0x28, this+0x88)
0xda2048  this+0x5c = -1
0xda2064  this+0x3c = 9 (or 4 when no battle clock), this+0x40 = parsed clock, this+0x14 = 3
```

Related accessors on the same class:

| Address | Operation |
| --- | --- |
| `0x00da20b8` | `stp w1, w2, [x0, #0x3c]` (state + clock setter) |
| `0x00da20c0` | `ldr x0, [x0, #0x88]` (replay JSON getter) |
| `0x00da20c8` | `this+0x88 != 0` (has replay JSON) |
| `0x00da20d8` | `ldr x0, [x0, #0x90]` (second payload getter) |
| `0x00da0658` | global getter `[[0x1b7d210]]` (manager singleton) |

Caller `0xbb025c` builds the replay string and calls
`0xda1f90(x0=[[0x1b7d210]], x1=string)`. This is consistent with the 15.535
`GameStateManager::loadReplay` hook (`0x01061b1c`) but the 16.635 function has a
2-argument signature, so **do not assume it is the same hook**. It still has to
be correlated with the actual tick/step function before it can drive the probe.

Open: identify the class vtable, the `step`/`tick` function, and the 4-argument
load overload used by the 15.535 probe.

### 3.4 Battle message / action region

The 15.535 notes put the local action send paths at `0xbb6fe0` and `0xbb9038`.
In 16.635 the same neighborhood still holds battle-event code:

| Anchor | 16.635.1 |
| --- | --- |
| `Dropped BattleEventMessage, no consumer (GameMode:%i State:%i)` string | `0x2d128e` |
| its log formatter | `0xbb44a8` (xref `0xbb44bc`) |
| message-class vtable pointers referenced there | `0x19f9f20`, `0x19f9f68` |
| replay raw-data handler calling the manager | `0xbb025c` (inside `0xbb…` dispatcher) |

The region is stable enough to be a good place to resume the packet/action work,
but no 16.635 action-send offset has been proven yet.

### 3.5 Replay controller — **recovered**

The 15.535 probe hooks a replay controller class whose vtable sits at old
`0x186e748` (a second subclass copy sits at `0x186f6d0`). The old lifecycle /
fullUpdate / alternate bodies do not survive as byte windows, so the vtable is
the authoritative anchor.

`match_vtables.py --seeded` resolves the table through the alternate-update
function, which *is* an exact match (`0xd9cc24`, old `0xc899e0`, delta
`+0x113244`):

| Old table | New table | Evidence |
| --- | --- | --- |
| `0x186e748` | **`0x1a20370`** | same slot 4 (`0xd9cc24`); the `-16` sub-vtable marker shifts from slot 7 to slot 9 |
| `0x186f6d0` | **`0x1a21308`** | same slot 4; its slot 9/10 overrides match `0xdac268`/`0xdac294` |

New slot layout for the probe's class (`0x1a20370`), aligned to the old table:

| Old slot | Old function | New slot | New function |
| ---: | --- | ---: | --- |
| 0 | `0xc87620` lifecycle | 0 | **`0x00d9a2cc`** |
| 1 | `0xc87548` | 1 | `0x00d9a1e4` |
| 2 | `0xc88bbc` fullUpdate | 2 | **`0x00d9bba0`** |
| 3 | `0xc8b294` | 3 | `0x00d9f2f4` |
| 4 | `0xc899e0` alternate | 4 | `0x00d9cc24` (EXACT) |
| 5 | `0xc828cc` | 5 | `0x00d94fd8` |
| 6 | `0xc89a6c` | 6 | `0x00d9f7f8` |
| − | (none) | 7, 8 | `0x00d9dfc4`, `0x00d9ccb0` (new overrides) |
| 9 | `0xc868dc` | 11 | `0x00d991bc` |
| 10 | `0xc86908` | 12 | `0x00d991e8` |
| 11 | `0x1060b9c` | 13 | `0x11d1e5c` |

The **runner** is not a vtable slot. It is the function at **`0x00d9ae04`**:
it follows the previous `ret` at `0xd9ae00`, contains the GameState load call at
`0xd9aee4` and step call at `0xd9b144`, and calls the new GameState ctor/setup/
destroy/base-destroy (`0x11d1e68` / `0x11d225c` / `0x11d2388` / `0x9fe0b0`).
The other load call sites (`0xd9a590`, `0xd9ab18`) are inside the lifecycle
function.

```
kReplayBattleControllerLifecycle     0x00c87620 -> 0x00d9a2cc
kReplayBattleControllerFullUpdate    0x00c88bbc -> 0x00d9bba0
kReplayBattleControllerAlternate     0x00c899e0 -> 0x00d9cc24
kNativeReplayRunner                  0xc87ecc   -> 0x00d9ae04
kReplayClockGlobalPauseGuard         0xcb6694   -> 0x00dcda40
```

The pause guard is matched through fullUpdate: both call it immediately after
the suspended-byte check and share the same body (`bl [[global]]; ldr [vptr,#0x28];
blr`).

Reproduce:

```bash
python3 tools/native_analysis/match_vtables.py OLD NEW --seeded-only \
    --seeded 'replayController=0x186e748:4:4:0xd9cc24' \
    --out docs/native/16.635.1/replay-controller-vtables.json
python3 tools/native_analysis/cr_elf.py disasm NEW 0xd9a2cc 0xd9a320
python3 tools/native_analysis/cr_elf.py disasm NEW 0xd9bba0 0xd9bc00
python3 tools/native_analysis/cr_elf.py disasm NEW 0xd9ae04 0xd9ae60
```

### 3.6 Non-code anchors — **resolved**

These are not code matches. They are PLT thunks, GOT slots, or data slots and
are resolved through `DT_SYMTAB`/`.rela.plt` and the getter that reads them:

| Probe anchor | Old | 16.635.1 | How |
| --- | --- | --- | --- |
| `kGameStateNew` | `0x17e4b20` | **`0x0198db50`** | PLT stub whose GOT slot is `_Znwm`; new runner calls it at `0xd9ae64` |
| `kGameStateDelete` | `0x17e4b50` | **`0x0198db60`** | PLT stub whose GOT slot is `_ZdlPv`; new runner calls it at `0xd9af3c` |
| `kEglSwapBuffersGot` | `0x18e9d88` | **`0x01a9d6b0`** | JUMP_SLOT for `eglSwapBuffers` |
| `kGameStateContextA` | `0x80031c` | **`0x00878c00`** | runner call shape; loads `[[0x1b61d00]]`, returns `+0x20` |
| `kGameStateContextB` | `0xeaf1c8` | `0x00ff253c` | `adrp/add; ret` constant getter (already EXACT) |
| `kGameStateContextRootSlot` | `0x19ad878` | **`0x01b61d00`** | the new ContextA getter's `adrp #0x1b61000; ldr [x19,#0xd00]` |

### 3.7 Controller / GameApp field shifts

Read from the recovered functions themselves:

| Probe field | Old | 16.635.1 | Instruction |
| --- | ---: | ---: | --- |
| `kReplayBattleControllerStateOffset` | `0x30` | `0x30` | lifecycle `ldr w8,[x0,#0x30]` |
| `kReplayBattleControllerAccumulatorOffset` | `0x44` | `0x44` | fullUpdate `ldr s0,[x19,#0x44]`; clock load/store |
| `kReplayBattleControllerSpeedOffset` | `0x74` | `0x74` | fullUpdate `ldr s10,[x19,#0x74]` |
| `kReplayBattleControllerSuspendedOffset` | `0x18c` | **`0x17c`** | fullUpdate/alternate `ldrb w8,[x19,#0x17c]` |
| `kGameAppPendingEventFlagsOffset` | `0x1b8` | **`0x1c0`** | pending setter `ldrb w8,[x0,#0x1c0]` |
| `kGameAppPendingEventOffset` | `0x1bc` | **`0x1c4`** | pending setter `str w1,[x0,#0x1c4]` |

The pending-event setter also gained two new fields at `+0x24b` (byte) and
`+0x24c` (word); the old function had no equivalent.

### 3.8 GameState constructor — **store map recovered**

Constructor `0x11d1e68` (old `0x1060ba8`). The allocation grew `0x230 → 0x240`.
The runner call shape is unchanged:
`ctor(manager, 1, contextA, contextB, 0)` at `0xd9ae7c`, followed by
`setup(manager, 1)` at `0x11d225c`.

The full old→new store map (offset, instruction, inferred role) is
[`gamestate-constructor.json`](gamestate-constructor.json). The key result is
that the two contradictory handoff notes are **both** correct and refer to
different things:

| Item | Old | New | Evidence |
| --- | ---: | ---: | --- |
| initialised int | `0x9c` | **`0xa0`** | `str wzr` then `str w8=#0x46b` in both bodies |
| embedded sub-object base | `0xa0` | **`0xa4`** | `add x0,x19,#…; bl <sub-object ctor>` |
| ctor arg2 (context A) | `0x208` | **`0x210`** | `str x24,[x19,#…]`; runner passes contextA in `x2` |
| ctor arg3 (context B) | `0x210` | **`0x218`** | `str x23,[x19,#…]`; runner passes contextB in `x3` |
| ctor arg4 (optional player) | `0x218` | **`0x220`** | object created only when `w4 != 0` |
| zeroed pointer slot | `0x220` | **`0x210`** | `str q1` before arg2 is stored |
| sentinel `-1` int | `0x1fc` | **`0x200`** | `str w8=#-1` |
| zero halfword | `0x1ec` | **`0x204`** | `strh wzr` |
| new-only zero word | — | `0x208` | no old equivalent |
| new-only zero block | — | `0x228`..`0x237` | `str q1,[x19,#0x228]` |
| new-only zero qword | — | `0x238` | `str xzr,[x19,#0x238]` |

Unchanged fields: constant pair `{5000, -1}` at `+0x1b8`; `-1` qwords at
`+0x1c0`/`+0x1c8`; `LogicCommandQueue` at `+0x38`; world/plugin object at
`+0xa8` (allocation `0x348 → 0x378`).

Roles are inferred from the constructor arguments and the immediately following
accessors. They still need confirmation from named live goldens before the
entity/HP work relies on them.

Reproduce (`.venv` has Capstone):

```bash
.venv/bin/python tools/native_analysis/cr_elf.py disasm OLD 0x1060ba8 0x1060e90
.venv/bin/python tools/native_analysis/cr_elf.py disasm NEW 0x11d1e68 0x11d225c
```

The workspace Ghidra project `/tmp/opencode/ghidra_project` has **not**
finished auto-analysis: it has no function at `0x11d1e68` and mis-splits
`0x11d2250`/`0x11d225c`. `ghidra_scripts/DecompileAnchors.java` can force a
function at a known address, but the decompiler output loses the AArch64
argument registers until the project is re-analysed. Use the raw disassembly
for now; a full `-analysisTimeoutPerFile 7200` pass is still pending.

## 4. Hook relocation report

`relocate_hooks.py` matched all 56 prologues extracted from `native_runner/probe`.
Result summary (`docs/native/16.635.1/hook-relocation.json`):

- **1 hook is port-ready by symbol + prologue:** touch (see §3.1).
- **3 prologues occur at exactly one function start** (`kActionDispatch`,
  `kAreaDamageEligible`, `kPendingObjectAdd`) — candidates, not proofs. The
  candidate addresses are in a very different code region (`0x107xxxx`/`0x108xxxx`)
  and must be verified by call shape before use.
- The rest are generic 16-byte prologues that occur hundreds/thousands of times.
  They require function-level matching (Ghidra decompilation or call-site
  signatures), not prologue-only relocation.

This is the key finding for scheduling: **the port cannot be driven by the
existing 16-byte fingerprints alone.** The 15.535 body bytes are not in this
workspace, so every non-touch hook needs either (a) Jason's 16.635 notes, or
(b) a fresh semantic match in Ghidra.

The 15.535 reference build is now present, so `diff_builds.py` supersedes
prologue-only matching for the replay path: 59 `EXACT`, 31 `LIKELY`, 10
`MANUAL`, 37 `AMBIGUOUS` and 10 `MISSING` rows. The ten manual rows are the
verified replay-controller / GameState / import anchors above; see
`docs/native/16.635.1/manual-overrides.json`.

## 5. Next steps (in order)

1. ~~Recover the replay controller trio and the runner~~ — done, see §3.5.
2. ~~Fix the near-duplicate confusion for `kGameStateStep`~~ — done via
   `manual-overrides.json`; the report now records `0x011d3174`.
3. ~~Generate the 16.635 probe layout header~~ — done,
   `native_runner/probe/build_16_635_1_layout.h`. It now carries the 9 runner
   hooks + touch, the resolved core call offsets, the 6 verified field offsets,
   and explicit `kUnavailableOffset`/`*Resolved = false` sentinels for anchors
   that are still `AMBIGUOUS`/`MISSING`. Required core anchors fail loudly
   rather than silently emitting a 15.535.13 address.
4. ~~Resolve the non-code anchors~~ — done, see §3.6.
5. ~~Wire the 9 runner hooks + touch into `cr_replay_probe.cpp` behind the new
   build header~~ — done (see §3.9). The 16.635 build is selected with
   `-DCR_PROBE_TARGET_16_635_1` (or `build_probe.ps1 -TargetBuild 16.635.1`).
   The full APK path is
   `build_offline_engine.ps1 -TargetBuild 16.635.1`; it pins the engine through
   `docs/native/16.635.1/build.json` without promoting
   `supported_engine.json`. It still needs dynamic verification on the native
   A51.
6. Name the GameState constructor fields from their accessors (§3.8) and then
   do the entity/HP (`hpComponentAccessor`) work. This is now the blocking
   analysis item: `kJsonMemberFind`, `kLogicCommandParse`,
   `kLocationTileMapEnsure` and `kLocationTileMapLookup` are the remaining
   `AMBIGUOUS` core anchors that keep cold configure / live command injection
   closed on 16.635.
7. Map the gameplay/vtable/data anchors that are still `AMBIGUOUS`: the
   Character / Projectile / AreaEffect / component vtables in
   `vtable-diff.json`, then the runtime/action-data pairs that share a base.
   The rich telemetry installs are deliberately skipped on the 16.635 build
   until these land.
8. Check `/tmp/opencode/ghidra_project` for the finished `cr16` analysis. If it
   timed out, rerun with JDK 25 + `-analysisTimeoutPerFile 7200`. Use
   `ghidra_scripts/DecompileAt.java` on the anchors above to name them.

### 3.9 16.635.1 probe wiring — **build-selectable, fail-closed**

`cr_replay_probe.cpp` now has one generated include for the target build:

```cpp
#if defined(CR_PROBE_TARGET_16_635_1)
#include "build_16_635_1_layout.h"
#endif
```

Under that define:

- the 9 runner hooks + touch read their offset **and 16-byte prologue** from the
  header (`probe_target::…`), so `verify_native_hook_table` still fails closed;
- the 6 verified field offsets come from `field-offsets.json` via the header;
- resolved core calls (GameState ctor/setup/destroy/tick/load/step,
  deserialize, byte streams, JSON parse, snapshot, card selection, command
  queue, logic data, replay manager/getter, EGL GOT, context root) use the
  header offsets;
- `kJsonMemberFind`, `kLogicCommandParse`, `kLocationTileMapEnsure`,
  `kLocationTileMapLookup` and `kBuffAssetClassNameGetter` stay null/sentinel
  and are reported in `kUnresolvedAnchors`; every caller already treats a null
  pointer as capability-unavailable;
- the rich telemetry installs (combat/phase/visibility/remaining/tower-troop)
  are skipped, because their layout headers are still 15.535.13 tables;
- the translated-SHA256 shim is skipped (its call site is unresolved, and the
  supported dynamic target is a real AArch64 A51).

`native_touch_interceptor` now accepts the touch offset/prologue through
`NativeTouchBindings`, defaulting to the 15.535.13 values for the legacy build.

Guard test: `native_runner/tests/test_probe_build_layout.py` re-reads every
emitted prologue from the reference binary, checks the unresolved-anchor
flags, compiles the header as C++17, and asserts the probe consumes the header
instead of repeating literals.

### 3.10 16.635.1 on-device smoke test — **passes; tick getter corrected**

Run on 2026-11-12 against `builds/cr-ai-16.635.1.apk` (probe `01cd0986…755b`)
on the Samsung **SM-A515U**, Android 13 / SDK 33, `arm64-v8a`, installed with
`./deploy_a51.sh`.

Attested over `adb forward tcp:26789 tcp:26789`:

- `libg_sha256 0b5802…e4eaa`, `libg_build_id 179ac4bd…1c30` (exact target);
- `content_version 16.635.93`,
  `content_fingerprint_sha256 553c8f15…04b4`;
- `hook_install_ok true`; capabilities `headless`, `native_render`,
  `snapshot_restore`, `structured_observation` true,
  `two_sided_actions false`;
- `content_ready false`, `production_ready false` (expected: cold configure is
  still closed on the four ambiguous anchors).

The client boots to the Collection screen, all 9 runner hooks + touch install
with **no `prologue mismatch`**, the five `anchor unavailable` lines print, and
a live ladder battle runs without a crash.

**Tick bug found here and fixed.** `kGameStateTick` had been diffed to
`0x011d3c90`, which is a *pointer* getter:

```
0x011d3c90: ldr x0, [x0, #0xb0]   ; returns this->field_0xb0
0x011d3c94: ret
```

Called as `int32_t(*)(void *)` it returned the low 32 bits of a heap pointer:
constant inside one manager, different across managers (menu `1961712128`,
battle `-2050488320`). The real getter is **`0x011d32f0`**:

```
0x011d32f0: ldr w0, [x0, #0x60]   ; bytes 00 60 40 b9 c0 03 5f d6
0x011d32f4: ret
```

which is byte-identical to the 15.535 getter at `0x01062bc0` and begins right
after `GameStateManager::step` returns at `0x011d32ec`. The new step body still
reads the tick from `[x19, #0x60]` (`0x011d324c`) and passes `&this->0x60`
(`0x011d3260`), so the field did not move; only the accessor address did.
`0x011d3c90` has 650 callers versus 132 for `0x011d32f0` (114 in 15.535).

After rebuilding with the corrected offset, the live battle logs

```
GameStateManager::step … call=1 tick=1
GameStateManager::step … call=2 tick=2
GameStateManager::step … call=3 tick=3
GameStateManager::step … call=10 tick=10
GameStateManager::step … call=100 tick=100
```

at 20 Hz, confirming the getter and the manager field. The correction lives in
`manual-overrides.json` (`kGameStateTick → 0x011d32f0`) and is applied with
`diff_builds.py --overrides`; `offset-diff.json` and the layout header were
regenerated, not hand-edited.

Root cause: the diff's multi-window vote used windows *past* the anchor
(relative 64/128/192/256) that landed in neighbouring functions, and the
16-byte prologue guard straddled two tiny adjacent getters, so both accepted
the wrong base. `diff_builds.py` now sets `tiny_shape_mismatch` when a
two-instruction accessor maps to a different instruction shape, and
`gen_layout.py` refuses such a row unless a `MANUAL` override clears it. An
audit of every other tiny-getter anchor (`kProjectileKindGetter`,
`kBuffComponentTypeGetter`, `kAttackComponentTypeGetter`,
`kMovementComponentTypeGetter`, `kByteStreamResetChecksum`) found them
byte-identical and correct.

Note for live runs: the lab account used for this smoke test was later maxed to
card level 16. The engine does not normalise levels; replay/battle config must
pin level 11 for the v1450 baseline, and level 16 is a separate experiment.

### 3.11 The four core anchors — **resolved (MANUAL, caller-verified)**

All four anchors that kept cold configure and live command injection closed are
now resolved. Each is a `MANUAL` override in `manual-overrides.json`; the
evidence is structural, not a prologue match.

| Anchor | Old | New | How it was proved |
| --- | --- | --- | --- |
| `kJsonMemberFind` | `0x00d0e100` | **`0x00e38d60`** | Same hash-map bucket walk (popcount idiom, modulo, chain) and returns the entry node. Called through the new wrapper `0x139cadc`, which is the old `0x1217934` shape: `add x0,x0,#0x18`, lookup, `[entry+0x20]`, virtual call, `cmp #3`. |
| `kLogicCommandParse` | `0x00d2fe78` | **`0x00e553e4`** | Old has one caller, the command loop in `GameState::load` at `0x1061d58`. The same loop is at `0x12d9240` in 16.635: `ldr w24,[x21,#0x14]` → get element → parse `0xe553e4` → `LogicCommandAppend 0xe5443c`. Both parse the same `"ct"` key literal. |
| `kLocationTileMapEnsure` | `0x00857608` | **`0x008ae750`** | Instruction-for-instruction match; only call targets moved (`Lookup`, `0xf2ee20`, `0x7c6d80`, `0xf2ee30`). Caller sets correspond, including the tail branches at `alternateUpdate+0x90/+0x98`. |
| `kLocationTileMapLookup` | `0x00df8508` | **`0x00f2edec`** | Instruction-for-instruction match (`bl`, `bl`, `ldr [x0,#0x78]`, tail `b`), and new `Ensure` calls it. |

Two ABI notes for anyone touching these:

- `kJsonMemberFind` changed its **key** representation. 15.535 takes a
  `LibgString` (length at `+4`, data at `+8`); 16.635 takes a 16-byte
  `StringView {data, size}` (`ldp x8,x1,[x1]`). The map offset `+0x18` and the
  entry value offset `+0x20` are unchanged. `cr_replay_probe.cpp`'s
  `lookup_json_member` builds a `StringView` under
  `CR_PROBE_TARGET_16_635_1`.
- `kLogicCommandParse`'s entry is `0xe553e4`, **not** `0xe553c0`. The unique
  window search returned `0xe553c0`, which is the epilogue of the preceding
  function (`bl 0x11d2304; ldp...; add sp,sp,#0xf0; ret`). This is the same
  "window recovered a base, not necessarily a function start" failure mode as
  the tick anchor; always check for a preceding `ret`/epilogue.

After these, the only remaining unavailable anchor is `kBuffAssetClassNameGetter`
(rich buff telemetry), which does not gate cold configure.

### 3.12 Cold configure is gated by a content-ready byte, not the anchors

Resolving the four anchors did **not** open cold configure, which was an
unstated assumption in §3.9. On the A51, with the anchors resolved:

- bootstrap reports only `anchor unavailable: kBuffAssetClassNameGetter`;
- attestation now reports `two_sided_actions: true` (it was false);
- `configure {"a":1}` still fails with
  `native content runtime is not initialized yet`.

That error comes from `inspect_content_runtime`, which requires
`*(root + 0x20)` and a byte equal to `1` at
`context + kContentPostProcessReadyOffset`. The context getter is correct — the
new `0x878c00` is `ldr x8,[x19,#0xd00]; cbz; ldr x0,[x8,#0x20]; ret`, the same
`*(root + 0x20)` shape as old `0x80031c` — so only the flag offset is in doubt.

`kContentPostProcessReadyOffset = 0x6e8` is an empirical 15.535-era literal. It
does not appear as a displacement or immediate anywhere in either binary
(only unrelated classes use 0x6e8). Live capture with the new
`content-window <hex offset> <hex length>` diagnostic shows why it is wrong on
16.635:

- the content context is published at t≈3.1 s and its pointer stays stable;
- the `0x500..0x800` window is zeroed, then fills with heap pointers by t≈7 s;
- the byte at `0x6e8` becomes **`0xa0`** — it is inside an 8-byte pointer
  (`0x758c876da0`), never a boolean.

Within that window only three bytes settle to `1`: `0x619` and `0x639` (both
7.2 s) and `0x720` (9.2 s). The first two are pointer bytes
(`0x758c7f01`); `0x720` is a scalar `1` but it is a generic offset shared by
many unrelated classes (66 references, many SIMD stores), so it is not
trustworthy as a content flag.

Conclusion at the time: this looked like it needed a **semantic** readiness check
rather than another raw byte. The 15.535.13 capture resolved it as a raw byte
after all — see §3.13.

The `content-window` command is bounded (offset ≤ 0x2000, length ≤ 0x400) and
read-only; it exists to support this investigation. Driver:
`tools/native_analysis/capture_content_window.py` (usage in the tools README).

### 3.13 Content-ready byte resolved: `0x6e8 → 0x720` (MANUAL)

The 15.535.13 probe was built with
`./build_offline_apk.sh --input-apk
.resource/native/15.535.13/nr_15.535.13_apkpure.apk --target-build 15.535.13
--output-apk builds/cr-ai-15.535.13.apk` and installed on the A51. A full
`0x0..0x2000` window capture across a warm boot (content already downloaded)
shows exactly one byte flipping at `coldReady`:

```
t=7.21  coldReady=false   +0x06e8: 00
t=7.79  coldReady=true    +0x06e8: 01
```

Only `+0x6e8` changes from `0` to `1`; the other transitions at `+0xb48` and
`+0xb69` are pointer stores (they become non-zero with it). So the gate byte is
the field at `context + 0x6e8` and the old probe literal was right for 15.535.

That byte is initialized by the content-context constructor, which gives the
16.635 mapping:

| | 15.535.13 | 16.635.1 |
| --- | --- | --- |
| context constructor | `0xdcc92c` (alloc `0xb70`) | `0xef9ad8` (alloc `0x10c0`) |
| zero-init body | `0xdcc180` | `0xef92f8` |
| ready byte store | `strb wzr,[x0,#0x6e8]` at `0xdcc19c` | `strb wzr,[x19,#0x720]` at `0xef9314` |
| adjacent bool | `strb wzr,[x0,#0x6e9]` at `0xdcc95c` | `strb wzr,[x0,#0x721]` at `0xef8f98` |
| lookup array | `0xad8` (219 entries) | `0x1028` (226 entries) |

`0xef91cc` (called by `0xef8f80`) also writes `strb wzr,[x19,#0x720]`, matching
the second `0x6e8` write in the old constructor at `0xdcca00`. The rest of the
zero-init blocks line up one-for-one, so this is a structural mapping, not a
byte coincidence.

This also explains the earlier observation that `0x720` settles to `1` at 9.2 s
in the 16.635 capture: it was the real flag, not a generic offset. The old
`0x6e8` now holds a heap pointer on 16.635, which is why the gate could never
pass.

Reproduce:

```bash
# old ready-byte store
python3 tools/native_analysis/cr_elf.py disasm \
  .resource/native/15.535.13/reference/libg.so 0xdcc180 0xdcc1b0
# new ready-byte store
python3 tools/native_analysis/cr_elf.py disasm \
  .resource/native/16.635.1/reference/libg.so 0xef92f8 0xef9320
# new adjacent bool
python3 tools/native_analysis/cr_elf.py disasm \
  .resource/native/16.635.1/reference/libg.so 0xef8f80 0xef8fa0
```

The offset is recorded in `field-offsets.json` and emitted into
`native_runner/probe/build_16_635_1_layout.h` by `gen_layout.py`; the probe uses
the generated constant under `CR_PROBE_TARGET_16_635_1`.

**On-device verification (2026-11-12, SM-A515U).** The 16.635.1 probe was
rebuilt with the corrected offset, installed, and launched:

- the context publishes at `t≈2 s` and `coldReady` flips to `true` at `t≈8 s`;
- `content-window 6e8 8` reads a heap pointer (`807fb79475000000`), while
  `content-window 720 8` starts with `01` — the gate byte;
- `configure {"a":1}` now stops at `native replay configuration was rejected`
  (the expected shape rejection), not `native content runtime is not
  initialized yet`;
- a real fresh-match configure from `native_runner/data/native_match.json`
  returns `{"ok":true,"configured":true,"generation":1,"tick":0}`, status
  reports `ready: true`, `matchReady: true`, `configLocationId: 15000199`;
- 6,068 headless steps run in ~3 s, the match ends (`ended: true`), and
  `observe` returns a non-empty observation (`objects`, `players`,
  `crownsRaw`, …).

Cold configure on 16.635.1 is unblocked. The next gap is the action/executor
path (queued card deploys), not the content gate.

### 3.14 GameStateManager allocation size: `0x230 → 0x240`

The probe calls the engine allocator directly when it builds a fresh headless
manager, so the requested size must match the 16.635 object. The 15.535-era
literal was `0x230`. The 16.635 constructor writes an 8-byte zero at `+0x238`:

```
0x011d1f44: str xzr, [x19, #0x238]
```

so a `0x230` allocation overflows the block by 16 bytes. On the A51 this
surfaced as a delayed `SIGSEGV` (`fault addr 0x64000000dc`) on the *second*
headless reset, inside the GameState destroy path: the constructor had already
written past the allocation, and teardown then followed a corrupted field.

The allocation is now build-pinned as `kGameStateManagerAllocationSize`
(`0x230` old, `0x240` new) in `field-offsets.json`, emitted into
`build_16_635_1_layout.h`, and used by both the cold configure worker and the
resident multi-match constructor.

Reproduce:

```bash
python3 tools/native_analysis/cr_elf.py disasm \
  .resource/native/16.635.1/reference/libg.so 0x11d1e68 0x11d1f60
```

### 3.15 Command context and replay command ids: card `86 → 153`, ability `2 → 189`

Two independent 15.535-era constants blocked the action path:

1. **Command context field.** `parse_logic_command` read the manager's command
   context from `+0x208`. The 16.635 constructor stores it at `+0x210`
   (`str x24,[x19,#0x210]`; context B moves `0x210 → 0x218`). The probe now
   uses `kGameStateCommandContextOffset` from the generated header.
2. **Replay wire ids.** The harness and probe emitted `ct=86` for a card play
   and `ct=2` for an ability. For 16.635.1 the replay command types are
   **153** (card play) and **189** (ability), per `docs/capture_format.md` §2.3.
   The probe's deploy/mirror/ability formatters now use
   `kCardPlayCommandType`/`kAbilityCommandType`; `NativeClashEnv` selects the
   card type from the probe's `status.clientVersion` (`153` for 16.635,
   `86` otherwise).

The parse function's own signature is unchanged: `x0 = JSON object`,
`x1 = command context`, `x8 = &result`. Its 16.635 caller computes the context
through `0x011d48a4` (`ldr x0,[x0,#0x210]; ret`), confirming the field move.

Reproduce:

```bash
python3 tools/native_analysis/cr_elf.py disasm \
  .resource/native/16.635.1/reference/libg.so 0x11d48a4 0x11d48ac
python3 tools/native_analysis/cr_elf.py calls \
  .resource/native/16.635.1/reference/libg.so 0xe553e4
```

### 3.16 Live hand model: hand is `player+0x210`, not `player+0x220`

The probe's hand reader was still using the 15.535 std::vector at
`player+0x220`. On 16.635 that vector is the **next/cycle queue**, which is why
`observe` showed four plausible cards that were the complement of the engine's
actual hand.

The engine's command validator searches `player+0x210` for the played deck
slot (`0x109c8b4(player+0x208, deckSlot)`), and the hand accessor
`0x1094d1c(player, handIndex)` resolves `player+0x210[handIndex]` through the
deck. On the A51 with a fresh match:

| tick | engine hand (`+0x210`) | probe's old `+0x220` reading |
| ---: | --- | --- |
| 40 | `{0,2,3,7}` | `{1,4,5,6}` |
| 200 | `{0,2,3,7}` | `{1,4,5,6}` |

`read_player_state` now reads `data = *(player+0x210)`, `count = *(player+0x21c)`
under `CR_PROBE_TARGET_16_635_1`, and uses the `std::vector` at `0x220` as the
cycle queue. `resolve_hand_deploy_request`, `observe`, and
`play_immediate` then select the deck slot the engine actually offered.

A manual inject of the engine-hand slot 0 at tick 100 (card `26000000`,
`px=9000`, `py=10000`) deployed successfully: elixir `7.78 → 4.87` (cost 3) and
the object count `6 → 7`. Cards are rejected (engine code `0x16`) before the
opening playable tick, which is why an action at tick 40 is a no-op until the
command's boundary (`FIRST_PLAYABLE_TICK` = 90) is reached.

**On-device confirmation of the final hand reader (2026-11-13).** The rebuilt
APK (`builds/cr-ai-16.635.1.apk`, SHA-256 `16dcc47d…3da9`) was installed and a
fresh `MatchConfig` match produced:

```
tick 40   hand [(0,26000000),(3,28000001),(7,26000018),(2,26000005)]
tick 100  elixir 7.78, objects 6
play_immediate(hand_index=0, 9000,10000) -> ok
tick 101  hand [(1,26000001),(3,28000001),(7,26000018),(2,26000005)]
          elixir 4.80, objects 7
```

The retired slot 0 (`26000000`) was replaced by slot 1 (`26000001`), elixir
fell by the card's 3, and a new object appeared. The automatic
`observe`/`play_immediate` action path now works on 16.635.1.

Reproduce:

```bash
python3 tools/native_analysis/cr_elf.py disasm \
  .resource/native/16.635.1/reference/libg.so 0x109c8b4 0x109c8e8
python3 tools/native_analysis/cr_elf.py disasm \
  .resource/native/16.635.1/reference/libg.so 0x1094d1c 0x1094d40
```



### 3.17 Two-sided decision loop: 160 decisions, 56 actions, one full match

The first multi-decision match ran on the SM-A515U on 2026-11-13 with
`tools/native_analysis/multi_decision_match.py`. One fresh `MatchConfig`
match (standard deck, `endTick` 7200) is configured, then every decision
observes both hands, plays the first affordable slot for each owner, and
advances 22 ticks across the native command-consumption boundary. Validation
per action: the played slot must be replaced by the deck's next card, elixir
must pay the printed cost within the measured regen bound (single
~0.018/tick, double ~0.036/tick, triple ~0.054/tick), elixir must not go
negative, and no command may remain queued.

Short gate (600 ticks):

```
24 decisions, 10 actions, 1.864 s, 0 problems, tick 0 -> 617
```

Full match (runs to the engine's own end condition):

```
160 decisions, 56 actions, 13.109 s, 0 problems
start tick 0 -> final tick 3601, ended true, crowns [0, 1]
```

Two repeats produced identical results (160 decisions, 56 actions, crowns
[0, 1], tick 3601) in 13.042 s and 13.120 s. The match ends at 3601 ticks
because owner 1 takes a princess tower inside regulation; an idle match runs
the full 6068 ticks to a 0-0 end. `adb logcat -b crash` is empty after every
run.

The double-elixir transition is visible in the data: decisions before tick
2400 gain ~0.018/tick between observations and later decisions ~0.036/tick,
which matches the 2.8 s / 1.4 s per-elixir rates.

Reproduce:

```bash
adb forward tcp:26789 tcp:26789
.venv/bin/python tools/native_analysis/multi_decision_match.py \
    --min-ticks 7000 --max-decisions 400 \
    --output /tmp/opencode/multi_decision_match_full.json
adb logcat -b crash -d
```

### 3.19 Live entity/HP path: 6 towers, damage and unit HP verified

`observe` already exposes the engine object graph: each live object carries
`owner`, `cardId` (`-1` for towers), arena `x`/`y`, and the hitpoint component
(`hp`, `maxHp`). The field layout is `read_hitpoints()`: components vector at
`object+0x18`, count `+0x24`, component `[2]`, current HP `+0x10`, max HP
`+0x14` (see §3.20 for the 16.635 accessor anchor).

`tools/native_analysis/entity_hp_probe.py` deployed one affordable card per
owner at the first legal tick and sampled the graph every 40 ticks. On the
SM-A515U (2026-11-13), 8 samples, 0 problems:

```
tick 152  towers 6 hp [2400, 1512, 1512, 2400, 1512, 1512]
          units [(26000000, 690, 690), (26000001, 119, 119), (26000001, 119, 119)]
tick 192  Knight 690 -> 602
tick 232  Knight 602 -> 514
tick 272  Knight 514 -> 426;  Archer 119 -> 65
tick 312  princess tower 1512 -> 1468;  Knight 426 -> 230
tick 352  Knight 230 -> 122
tick 392  both deployed units gone, 6 towers still at their cells
tick 432  6 towers, princess tower 1468
```

All six native tower cells are present (king `2400` HP, princesses `1512` at
level 1). Unit HP decreases under tower fire and the objects leave the graph on
death. Two `cardId == -1` entries appeared at non-cell positions with no
hitpoint component (transient, no HP) and are correctly excluded from the tower
set; `read_native_crowns` only counts the fixed cells. `adb logcat -b crash` is
empty.

This closes the entity/HP *observability* question for 16.635. The 15.535-only
rich telemetry hooks (combat events, phase/visibility runtimes) remain disabled
on this build, so `observe-rich` is still unavailable.

Reproduce:

```bash
adb forward tcp:26789 tcp:26789
.venv/bin/python tools/native_analysis/entity_hp_probe.py \
    --output /tmp/opencode/entity_hp_probe.json
```

### 3.20 Hitpoint accessor anchor: `0xf2db90 -> 0x0108b474` (EXACT)

The 15.535.13 virtual hitpoint accessor is a 10-instruction leaf:

```
0x00f2db90: ldrb w9, [x0, #0x30]   ; component-type bitmask
            tbz  w9, #2, ret       ; HP component present?
            ldr  w9, [x8, #0x24]   ; component count
            cmp  w9, #3
            b.lt ret
            ldr  x8, [x8, #0x18]   ; component vector
            ldr  x0, [x8, #0x10]   ; components[2]
            ret
```

The same 44-byte body occurs exactly once in 16.635.1 at **`0x0108b474`**,
so the object tag field (`+0x30` bit 2), vector (`+0x18`), count (`+0x24`) and
component index (2) are unchanged. Its vtable references relocate with the
entity tables (old `0x1ebd60, 0x1ec330, …` → new `0x204ee8, 0x205500, …`).

`kHitpointsComponentAccessorOffset` is now a probe constant
(`0x00f2db90` → `0x0108b474`), resolved `EXACT` by `diff_builds.py` and emitted
into `native_runner/probe/build_16_635_1_layout.h`. The probe still reads the
component fields directly (`read_hitpoints`) instead of calling the accessor;
the anchor exists so future telemetry ports share one verified source.

Reproduce:

```bash
python3 tools/native_analysis/cr_elf.py disasm \
  .resource/native/15.535.13/reference/libg.so 0xf2db90 0xf2dbbc
python3 tools/native_analysis/cr_elf.py disasm \
  .resource/native/16.635.1/reference/libg.so 0x108b474 0x108b4a0
python3 tools/native_analysis/cr_elf.py refs \
  .resource/native/16.635.1/reference/libg.so 0x108b474
```

### 3.18 Throughput on the A51: transport is the bottleneck, not the engine

Measured on the SM-A515U (Android 13, arm64-v8a) on 2026-11-13 with
`tools/native_analysis/native_match_throughput.py` and
`resident_batch_throughput.py`. All runs report `adb logcat -b crash` empty.

**Single connection, ordinary control API** (one fresh match per `step`):

| mode | match | seconds/match | matches/s | notes |
| --- | ---: | ---: | ---: | --- |
| `raw` (one `step` to end) | 6068 ticks | 0.544 | **1.84** | engine-bound ceiling |
| `batch` (`step 20`) | 6068 ticks | 5.807 | 0.17 | 304 calls, ~19 ms RTT each |
| `decision` (both owners, 22-tick boundary) | 3601 ticks | 12.987 | 0.077 | 160 decisions, 56 actions, 12.3 decisions/s, 4.3 actions/s |

The engine itself steps 6068 ticks in ~0.54 s (~11.2k ticks/s). Every ordinary
control command costs ~19 ms through `adb forward`, so per-decision round trips
dominate: 480 commands explain the 13 s decision match.

**Resident batch channel** (`batch-v1`, 8 configured slots, one framed request
per batch, ordinary+atomic capture per slot):

| batch shape | total | slot-ticks/s | matches/s (per engine) |
| --- | ---: | ---: | ---: |
| 8 slots × 100 ticks × 20 | 4.65 s | 3,443 | 0.57 |
| 8 slots × 600 ticks × 5 | 4.47 s | 5,374 | **0.89** |

The channel profile attributes almost all of the wall time to receiving the
per-slot capture (4.43 s of the 4.47 s); send is 0.2 ms and decode 33 ms. The
batch path removes the per-command RTT but the observation envelope (ordinary
+ atomic JSON for 8 slots, every batch) is now the cost.

**Interpretation for Phase 0.** One A51 probe process sustains ~0.9 matches/s
with 8 resident slots and full observations, or 1.84 matches/s engine-only.
The 10 matches/s target therefore needs roughly 6–12 such engine lanes (wider
PC/cloud hosts, or several devices), not one phone. This is the measured
single-engine row; multi-engine aggregation remains to be measured. Actions
also need `ResidentBatchCoordinatorV1` (the `ResidentBatchChannelV1` path with
policy actions) — the batch step itself is proven here.

Reproduce:

```bash
.venv/bin/python tools/native_analysis/native_match_throughput.py --matches 5 --mode raw
.venv/bin/python tools/native_analysis/native_match_throughput.py --matches 3 --mode decision
.venv/bin/python tools/native_analysis/resident_batch_throughput.py --slots 8 --batches 5 --ticks 600
```

**Rebuilt deployable (2026-11-13).** After the hitpoint-anchor change,
`./build_offline_apk.sh --input-apk nr_16.635.1_release_951fe421.apk
--target-build 16.635.1 --output-apk builds/cr-ai-16.635.1.apk` produced
`19932f9e…0dd9` with probe `3726031a…cf14` and the reference
`libg.so 0b5802…e4eaa`. The probe bytes are identical to the previously
installed APK (the anchor is compile-time only), confirming a deterministic
probe build. The rebuilt APK was installed on the A51 and re-verified: a full
two-sided match (160 decisions / 56 actions, 0 problems, tick 3601) and the
entity/HP probe (8 samples, 0 problems), with an empty crash buffer.

### 3.21 Scripted X-Bow 3.0 self-play on 16.635.1

`tools/native_analysis/scripted_xbow_match.py` is the Phase 1 scripted-X-Bow
v0 scaffold. It configures FirstLight-Zero's exact deck and form masks::

    X-Bow, Hero Knight, Evo Archers, Evo Tesla, Skeletons,
    Electro Spirit, Fireball, The Log
    forms (0, 2, 1, 1, 0, 0, 0, 0)   # 2 = Hero, 1 = Evolution

The policy is split into `defense`, `cycle`, `spell` and `offense` modules.
Placements are owner-local (`own king (9000, 3000)`, river `y=16000`) and are
mirrored for owner 1, so both seats run the same policy. The action layer has a
wait option, keeps a blocked-cell memory, and skips validation for cards
submitted on the final match boundary (the engine finalizes the same tick, so
the command is intentionally never consumed). Hero runtime ids are normalized
back to the deck card (Hero Knight `203000000 -> 26000000`).

Full self-play matches on the SM-A515U (2026-11-13), one fresh `MatchConfig`
each, decisions every 20 ticks (~1 s), `adb logcat -b crash` empty after every
run:

| run | decisions | actions | seconds | match | crowns (winner) | problems | latency mean / median / p95 |
| --- | ---: | ---: | ---: | --- | --- | ---: | --- |
| seed 11 (repeat A) | 173 | 66 | 13.50 | tick 3601, ended | [1, 2] (owner 1) | 0 | 58.9 / 45.3 / 93.5 ms |
| seed 11 (repeat B) | 173 | 66 | 13.51 | tick 3601, ended | [1, 2] (owner 1) | 0 | 56.6 / 49.6 / 97.0 ms |
| seed 12 | 176 | 35 | 13.80 | tick 3601, ended | [2, 1] (owner 0) | 0 | 59.8 / 43.0 / 90.5 ms |
| seed 11 + abilities | 174 | 60 | 16.42 | tick 3601, ended | [2, 1] (owner 0) | 0 | 31.7 / 31.2 / 40.9 ms |

Seed 11 is deterministic across repeats (same decisions, actions, card counts
and result). The seed-11 card mix without abilities was X-Bow 6, Tesla 9,
Knight 11, Archers 6, Skeletons 13, Electro Spirit 12, Log 6, Fireball 2; seed
12 was a lower-action match (35 actions). The policy is recognizably X-Bow 3.0:
bridge X-Bow into the weaker princess lane, Tesla/Archers behind it, Knight in
front, cheap cards to cycle, Fireball/Log on stacked threats.

The scripted match is still transport-bound at ~0.06-0.07 matches/s (three
control round trips per decision), consistent with §3.18; it is a behaviour
baseline, not the training throughput path.

Reproduce::

```bash
adb forward tcp:26789 tcp:26789
.venv/bin/python tools/native_analysis/scripted_xbow_match.py \
    --seed 11 --output /tmp/opencode/scripted_xbow_match.json
.venv/bin/python tools/native_analysis/scripted_xbow_match.py \
    --seed 11 --no-abilities --output /tmp/opencode/scripted_xbow_match_no_ability.json
adb logcat -b crash -d
```

### 3.22 Hero ability actions and the 16.635 Champion layout

`AbilityAction` previously failed on 16.635 with `could not resolve one ready
native champion ability source`. The probe still compared the live controller
against 15.535 control-block vtables, and the static vtable diff could only
offer *sibling* LIKELY matches for the two data vtables. The probe now has a
`champion-diag` control command that dumps the live Champion controller,
action-data, selected-character and ability-data objects (vtables, names, field
words) without changing engine state. It is a diagnostic, not a training API.

Live dump from the SM-A515U after deploying Hero Knight (`203000000`):

| object | live vtable offset | notes |
| --- | --- | --- |
| Champion controller | **`0x01a51c30`** | matches the EXACT vtable-diff relocation |
| Champion action data | **`0x01a3fd60`** | name `KingTower_ChampionAbility` |
| Character ability data | **`0x01a348e0`** | name `Knight_hero_Ability` |
| Hero carrier | `0x01a4fbb8` | `cardId 203000000`, selected-character backlink matches |

The controller stays at `player+0x3a0/+0x3a8`; a scan of the owner object found
only those two slots (plus unrelated `0x7a0/0x7a8` pointers). The controller
fields kept their 15.535 offsets: cooldown `+0x78`, configured cooldown
`+0x7c`, charges `+0x80`, button state `+0x98` (2 = `Ready`, 6 =
`AllChargesConsumed`), action data `+0x18`, selected character `+0x90`,
champion vector `+0xa0`. `LogicCharacterAbilityData` did shift: live words put
`MaxCharges` (1) at **`+0xb0`** (was `+0xac`) and `ManaCost` (2) at `+0xb4`, so
the configured-cooldown twin is **`+0xb8`** (was `+0xb4`).

`tools/native_analysis/ability_action_probe.py` then verifies the engine
receipts. On the A51 (2026-11-13), 0 problems, empty crash buffer:

```
controller before  buttonState=2 Ready,  charges=1/1, championCount=1
activate-ability   owner 0, sourceEntityKey [0,1,0], executeTick = queued+1
controller after   buttonState=6 AllChargesConsumed, charges=0/1
elixir paid        1.644  (= 2 printed ManaCost - 0.36 regen over 20 ticks)
second activation  refused (no charge remains)
```

The three vtable anchors and the two field offsets are now emitted by
`gen_layout.py` from `champion-layout.json` into
`native_runner/probe/build_16_635_1_layout.h`, so the 16.635 probe never falls
back to a 15.535 Champion address. `scripted_xbow_match.py` polls the
diagnostic every five decisions and activates a ready Hero Knight as a separate
native command: the seed-11 full match used **10 abilities, 0 rejections** in
174 decisions.

Reproduce::

```bash
adb forward tcp:26789 tcp:26789
.venv/bin/python tools/native_analysis/ability_action_probe.py \
    --output /tmp/opencode/ability_action_probe.json
.venv/bin/python tools/native_analysis/scripted_xbow_match.py \
    --seed 11 --output /tmp/opencode/scripted_xbow_abilities.json
adb logcat -b crash -d
```

### 3.23 Resident batch channel with real card actions

The resident channel already carried `BatchPlayActionV1`; the throughput tool
only sent empty action sets. `resident_batch_throughput.py --actions` now reads
each slot's ordinary capture, chooses one affordable hand card per owner, and
sends real plays in the framed batch. The channel validates every receipt
(status 0, matching action index); the tool records card id, cost and execute
tick.

Re-measured on the SM-A515U (2026-11-13), 8 slots, one warm probe process,
empty crash buffer. The empty-step baseline was re-run in the same session
because the earlier §3.18 numbers were lower (0.57 / 0.89 matches/s):

| batch shape | actions | seconds | slot-ticks/s | matches/s | actions/s |
| --- | --- | ---: | ---: | ---: | ---: |
| 8 × 100 ticks × 20 | none | 2.054 | 7,789 | **1.284** | — |
| 8 × 100 ticks × 20 | every 3 batches | 3.196 | 5,006 | **0.825** | 30.0 |
| 8 × 600 ticks × 5 | none | 1.677 | 14,315 | **2.359** | — |
| 8 × 600 ticks × 5 | every batch | 2.283 | 10,512 | **1.732** | 28.0 |

Real actions cost ~27–36% of channel throughput and the batched capture grows
with the active object graph (receive time is 1.9 s empty vs 3.0 s with actions
in the 100-tick shape). The 600-tick shape with actions moved **64 valid
receipts** (8 slots × 2 owners × 4 action rounds; the first batch only
configures the capture baseline), all `status 0`, cards `26000000`/`26000001`
at the printed 3 elixir, executing on schedule (tick 601 onward).

A single warm A51 process is therefore a **~1.7 matches/s lane with real
actions** (2.36 empty). The 5–10 matches/s aggregate target still needs 3–6
lanes. On the A51 the single-process APK deliberately exposes one lane; a
second lane needs the engine-cluster APK (designed for MuMu) or a second
device/secondary Android user. That remains the open multi-engine work.

Reproduce::

```bash
adb forward tcp:26789 tcp:26789
.venv/bin/python tools/native_analysis/resident_batch_throughput.py \
    --slots 8 --batches 20 --ticks 100 --output /tmp/opencode/resident_8x100x20_noaction.json
.venv/bin/python tools/native_analysis/resident_batch_throughput.py \
    --slots 8 --batches 20 --ticks 100 --actions --action-every 3 \
    --output /tmp/opencode/resident_8x100x20_actions.json
.venv/bin/python tools/native_analysis/resident_batch_throughput.py \
    --slots 8 --batches 5 --ticks 600 --actions --action-every 1 \
    --output /tmp/opencode/resident_8x600x5_actions.json
adb logcat -b crash -d
```

### 3.24 Control-lane affinity, Android cpusets and the phone lane ceiling

The probe used to pin its control lane to `cpu = engine_id`
(MuMu-compatible, where each engine has one vCPU). On a heterogeneous phone
that is the **slow** cluster: the SM-A515U has 4× Cortex-A53 at 1.74 GHz on
cpu0–3 and 4× Cortex-A73 at 2.31 GHz on cpu4–7. Engine 0 therefore ran the
whole resident channel on a little core.

`engine_instance.inc` now supports two overrides:

- `debug.cr.engine_cpu_map` system property, read once when the control thread
  starts: `"4,5,6,7"` maps engine 0..3 to cpu 4..7; `"none"` leaves the thread
  unpinned. Unset keeps `engine N -> cpu N`.
- `engine-affinity <cpu|none>` control command, applied live to the control
  thread (no restart); `engine-status` now also returns `affinity`.

Measured on the A51 (16.635.1, one engine, 8 resident slots × 600 ticks × 5
batches, real actions):

| Control-lane affinity | slot-ticks/s | matches/s | actions/s |
| --- | ---: | ---: | ---: |
| `0` (A53 little core) | 3,774 | **0.622** | 10.1 |
| `7` (A73 big core) | 10,906 | **1.797** | 29.1 |
| none (scheduler) | 4,996 | **0.823** | 13.3 |

The big-core lane reproduces the earlier §3.23 1.73–1.80 numbers; the default
`cpu0` mapping was the reason a fresh run measured 0.62. **Pin the control lane
explicitly on any heterogeneous host.**

A Linux cluster APK builder (`build_cluster_apk.sh`, mirroring the Windows
script) and launcher (`launch_cluster_a51.sh`) were added. The cluster APK
installs and runs on the A51: `:engine0` reports
`processName=nullsroyale.rel.free:engine0`, `assignedCpu=-1`, and a live match.
`resident_batch_throughput.py --ports` now measures several engines concurrently
behind a start barrier.

Two Android limits block phone aggregation:

- **cpusets.** Only the top activity's process gets `/top-app` (cpu0–7). A
  background engine falls to `/background` (cpu0–1), so its effective
  `Cpus_allowed_list` shrinks regardless of the requested affinity:
  engine 0 = `top-app`/`0-7`, engine 1 = `background`/`0-1`.
- **memory.** One engine with 8 resident slots is ~690 MB RSS
  (`VmHWM` 726 MB); the A51 has 3.65 GB total and ~1 GB available. More than
  2–3 engines will hit the low-memory killer.

Operational note: a second engine launched while covered may never reach
`coldReady`; bringing its activity back to the front flips it. Launch engines
one at a time, wait for each `coldReady`, then launch the next. Once ready, a
background engine stays ready.

Reproduce:

```bash
./build_cluster_apk.sh --input-apk nr_16.635.1_release_951fe421.apk \
    --target-build 16.635.1 --engine-count 4 \
    --output-apk builds/cr-engine-cluster-16.635.1.apk
adb install -r builds/cr-engine-cluster-16.635.1.apk
./launch_cluster_a51.sh --engines 1 --cpu-map none
.venv/bin/python tools/native_analysis/resident_batch_throughput.py \
    --ports 26789 --slots 8 --batches 5 --ticks 600 --actions
# live A/B without a restart:
#   engine-affinity 0 | 7 | none   (control command)
```

### 3.25 Multi-engine aggregation on the A51 (2 and 3 lanes)

The cluster APK (`build_cluster_apk.sh --engine-count 4`) was installed on the
SM-A515U and three `:engineN` processes were launched one at a time with
`debug.cr.engine_cpu_map=1,7,6`. Android put only the last activity
(`:engine2`) in `/top-app`; the other two fell to `/background`, so their
effective `Cpus_allowed_list` is `0-1` no matter which CPU the probe requests:

| process | cpuset | `Cpus_allowed_list` | probe affinity |
| --- | --- | --- | ---: |
| `:engine0` | `/background` | `0-1` | 1 |
| `:engine1` | `/background` | `0-1` | 7 |
| `:engine2` | `/top-app` | `0-7` | 6 |

Every lane ran 8 resident slots, real `BatchPlayActionV1` actions
(`--actions --action-every 3`), and a full ordinary capture per batch. The
throughput tool now reports both aggregates:

- **capacity** = sum of the per-lane rates. This is the training-relevant
  number (FirstLight's 9.85 matches/s is 16 slots × ~0.62 matches/s) and stays
  correct when lanes have unequal speed.
  *(Corrected in §3.41: 16 slots × 0.62 would need ~3,700 slot-ticks/s, but the
  engine does ~490 raw. 9.85/s is consistent with ~128 slots — 16 collectors ×
  8 — at the raw per-slot rate. The capacity/makespan distinction above still
  holds; only the parenthetical slot count was wrong.)*
- **makespan floor** = total slot-ticks / slowest lane's window. This is a lower
  bound that pays for fast lanes finishing early; the previous implementation
  reported only this, which under-counted mixed-speed lanes by ~40%.

Single-lane baselines in the same session (8 slots × 600 ticks × 5 batches,
3,000 slot-ticks per lane):

| lane | placement | slot-ticks/s | matches/s | actions/s |
| --- | --- | ---: | ---: | ---: |
| `:engine0` | background, little core | 5,719 | **0.943** | 3.8 |
| `:engine1` | top-app, big core | 12,727 | **2.097** | 8.5 |

Concurrent lanes, same 5 × 600 shape:

| lanes | slot-ticks/s (capacity) | matches/s (capacity) | makespan floor | actions/s |
| --- | ---: | ---: | ---: | ---: |
| 2 (`:engine0` + `:engine1`) | 19,453–19,756 | **3.21–3.26** | 2.10–2.12 | 13.0–13.2 |
| 3 (throttled) | 26,300 | **4.34** | 2.69 | 17.5 |
| 3 (first, unthrottled) | 47,100 | **7.76** | 4.73 | 31.4 |

Sustained shape, 8 slots × 600 ticks × 10 batches (6,000 ticks ≈ one match):

| lanes | slot-ticks/s (capacity) | matches/s (capacity) | makespan floor | actions/s |
| --- | ---: | ---: | ---: | ---: |
| 1 (`:engine2`, top-app) | 12,897 | **2.13** | — | 12.9 |
| 2 (`:engine0` + `:engine2`) | 19,729 | **3.25** | 2.06 | 19.7 |
| 3, run 1 | 30,446 | **5.02** | 2.93 | 30.4 |
| 3, run 2 | 24,942 | **4.11** | 2.41 | 24.9 |

Aggregation is additive: each lane's rate holds when lanes run together, and
the 3-lane capacity is the sum of its lanes (e.g. `1.23 + 0.98 + 2.82 = 5.02`).
No shared channel or engine bottleneck appeared up to three lanes; the limit is
the phone's cpusets and memory.

**Thermal throttling dominates the variance.** The big cluster was at 1.25 GHz
during the 2-lane runs and had fallen to 936 MHz by the sustained 3-lane runs
(little cores 1.05 GHz; maxima are 2.31 / 1.74 GHz). The 7.76 and first 4.47
lane numbers were short unthrottled bursts; 2.1–2.8 matches/s is the realistic
sustained big-core lane, and 0.9–1.2 is the realistic background/little lane.
The earlier §3.24 1.80 was measured on a cool phone.

**Memory** is the hard ceiling for more lanes: with 3 engines and 24 resident
slots, `VmHWM` was ~570–680 MB per engine, `MemFree` fell to 83–125 MB and the
phone used ~1.5 GB of its 4 GB swap. Only ~540 MB was available; there was no
crash, but a fourth engine is not safe on this device. That matches §3.24's
2–4 lane estimate.

Implication for the A51-as-reference: one native ARM64 core is worth
**~2–2.8 matches/s sustained** (up to ~4.5 in short bursts), not 1.8. The phone's
3-lane 4–5 matches/s is a cpuset/memory artefact, not an engine limit. The
training host is the cr-native x86_64 runtime — see
[`ROADMAP.md`](../../../ROADMAP.md).

Reproduce:

```bash
./build_cluster_apk.sh --input-apk nr_16.635.1_release_951fe421.apk \
    --target-build 16.635.1 --engine-count 3 \
    --output-apk builds/cr-engine-cluster-16.635.1.apk
adb install -r builds/cr-engine-cluster-16.635.1.apk
./launch_cluster_a51.sh --engines 3 --cpu-map 1,7,6 --timeout 180
for pid in $(adb shell ps -A -o PID,NAME | awk '/nullsroyale.rel.free:engine/ {print $1}'); do
  adb shell cat /proc/$pid/cgroup | grep cpuset
  adb shell cat /proc/$pid/status | grep Cpus_allowed_list
done
.venv/bin/python tools/native_analysis/resident_batch_throughput.py \
    --ports 26789,26790,26791 --slots 8 --batches 10 --ticks 600 --actions \
    --output /tmp/opencode/resident_3x8x6000_actions.json
adb logcat -b crash -d
```

### 3.26 Native ruleset binding for the V4 evaluator — pinned device identity

`BattleEnvV1` builds a `RulesetManifestV1` from `native_runner/supported_engine.json`,
which is pinned to 15.535.13 / content 15.535.86. On the A51 the probe reports
16.635.1 / content 16.635.93, so the first evaluator run failed closed with:

```
native runner does not match RulesetManifest: libg_sha256: expected 110aa2b5…,
got 0b580222…; content_version: expected 15.535.86, got 16.635.93; …
```

The fix keeps the gate fail-closed instead of disabling it. A checked-in pin,
`native_runner/versions/native-16.635.1.json`, records the identities of bytes
that stay on the device:

| Field | Value |
| --- | --- |
| client / content | `16.635.1` / `16.635.93` |
| `libg.so` SHA-256 | `0b580222…e4eaa` |
| `libg` build id | `179ac4bd…1c30` |
| probe SHA-256 | `8721be93…8f81` |
| content fingerprint | `553c8f15…04b4` |
| content assets (`assets.scdb`) | `1bac8864…6d0b` |
| content manifest SHA-1 | `b54188c6…5c44` |

`ruleset.build_native_ruleset_manifest()` builds the normal local manifest
(catalogs, contracts, timeline) and then replaces the device-resident identity
groups with those pin values (`engine`, `data` fingerprint, `visual`), records
the whole pin under `config["native_build"]`, and states the known adaptation
in `content_adaptation`: the card/effect/projectile catalogs remain the pinned
15.535.86 release because the 16.635.93 `csv_logic` assets are on the device,
not in the workspace. Parity evidence: §3.16–§3.22 and the level-11 comparison
in `builds/native/parity-unit-hp-15.535.29-vs-16.635.1-L11.json`.

`verify_files()` skips only entries under the `device-resident/` prefix, so a
local file can never be masked by the new rule. The builder also refuses to
run if the local probe digest differs from the pin, so a rebuilt probe must be
re-pinned deliberately.

`training/v4/evaluate.py` now reads `status.clientVersion`, loads the matching
pin (if any), passes the manifest and an explicit environment config to
`BattleEnvV1`, and records `client_version`, `ruleset_id` and `native_build` in
`identity.json`. It also stops setting
`public_card_play_events_from_combat_ring` when the pin declares
`combat_ring_hooks: false` (16.635.1 rich hook rings are not installed); model
actions still emit action-executed events, so this only removes the overlay
feed.

Guard test: `native_runner/tests/test_native_ruleset_build.py` builds the
manifest, checks the device groups, runs `verify_files`, and verifies a
synthesized 16.635.1 attestation end to end.

Reproduce::

```bash
.venv/bin/python -m pytest native_runner/tests/test_native_ruleset_build.py -q
.venv/bin/python -m native_runner.training.v4.evaluate \
    --checkpoint-a checkpoints/General/checkpoint-step-00000460.pt \
    --checkpoint-b checkpoints/IL/checkpoint-step-00029396.pt \
    --games 2 --output /tmp/opencode/eval-general-nulls --port 26789 --device cpu
```

### 3.27 V4 evaluator: deterministic argmax is passivity-biased; use `--sample`

The evaluator's first full game on 16.635.1 (`BattleEnvV1` + General vs IL,
argmax decoding, seed 1) terminated normally but was degenerate:

```
game 1 seed 1: winner a (General, owner 0), tick 5279, rejected 0
actions_by_owner [9, 0]
```

Both checkpoints are valid `universal-card-policy.v4` contracts and load
strictly. The passivity is in the gate head, not the port:

- with the live opening state (tick 90, elixir 7.6, four legal deploy
  candidates), both models put only **~6%** probability on `ACT`
  (General gate logits `[0.329, -2.385]`, IL `[0.318, -2.368]`);
- `PolicySessionV4.decide(sample=False)` takes the argmax, so at <50% ACT
  probability it always chooses `WAIT`;
- the action path itself is fine: `act_after_preselected_act` selects a legal
  deploy candidate, and with `sample=True` both models play normally
  (200-decision diagnostic: General 5 plays, IL 6 plays, 0 rejections,
  elixir cycling rather than pinned at 10).

The IL labels are sparse at the 5-tick decision cadence (a human action in a
minority of decision windows), so argmax BC collapses to the majority class.
Evaluation therefore needs stochastic decoding, which `evaluate.py` already
supports:

```bash
.venv/bin/python -m native_runner.training.v4.evaluate \
    --checkpoint-a checkpoints/General/checkpoint-step-00000460.pt \
    --checkpoint-b checkpoints/IL/checkpoint-step-00029396.pt \
    --games 2 --sample --output /tmp/opencode/eval-general-nulls \
    --port 26789 --device cpu
```

`torch.manual_seed(match.seed)` is set per game, so sampled runs are
reproducible. `serve_policy.py` already exposes `--sample`; live deployment
should not use the argmax default without checking gate entropy, or it will
stand still like the evaluator did.

**Sampled paired evaluation (2026-11-13, SM-A515U, seeds 1–2, mirror default
deck, both seats, `--sample`).** Identity: `ruleset_id
0a98e8855d7ea5aff6265460386102a3b290ae3c9bb3207b22f909ef1327dc15`.

| game | seed | A seat | winner | final tick | actions A / B | rejected |
| ---: | ---: | ---: | --- | ---: | --- | ---: |
| 1 | 1 | 0 | **General** | 3601 | 26 / 27 | 0 |
| 2 | 2 | 1 | **General** | 3601 | 25 / 27 | 0 |

Summary: `a_wins 2`, `b_wins 0`, draws 0, truncated 0; normal-terminal rate
**2/2**. Each match ended on the engine's own `native_final_result` at tick
3601 (a princess tower inside regulation), 0 rejected actions, empty crash
buffer.

Wall time was **627 s for two games (~313 s/game)** on one CPU core:
~1,440 policy forwards per game at ~217 ms each. The engine can step the same
match in well under a second (§3.18), so **host inference dominates offline
evaluation**, not the phone. A GPU-backed evaluator is the next throughput
lever; the A51 itself is not the limiter here.

Artifacts: `/tmp/opencode/eval-general-nulls-sample/` (`identity.json`,
`matches.jsonl`, `summary.json`); the argmax run is
`/tmp/opencode/eval-general-nulls/`.

### 3.28 N0 scripted soak — 10/10 clean after fixing the soak checks

Final N0 soak on the SM-A515U (2026-11-13/14), ten seeds of
`scripted_xbow_match.py`, one fresh `MatchConfig` each, abilities on:

| seed | decisions | actions | abilities | rejected | problems | final tick |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 174 | 57 | 9 | 0 | 0 | 3601 |
| 2 | 174 | 56 | 8 | 0 | 0 | 3601 |
| 3 | 194 | 67 | 10 | 0 | 0 | 4024 |
| 4 | 174 | 57 | 9 | 0 | 0 | 3601 |
| 5 | 295 | 128 | 16 | 0 | 0 | 6148 |
| 6 | 174 | 60 | 8 | 0 | 0 | 3601 |
| 7 | 174 | 58 | 10 | 0 | 0 | 3601 |
| 8 | 182 | 55 | 6 | 0 | 0 | 3756 |
| 9 | 189 | 59 | 9 | 0 | 0 | 3899 |
| 10 | 174 | 52 | 8 | 0 | 0 | 3601 |

Totals: **10/10 matches ended normally, 0 problems, 93 abilities with 0
rejections, `adb logcat -b crash` empty.** Every match ended on the engine's own
result (5 at tick 3601, 3 inside regulation, 2 at full time).

Two soak-tool bugs produced the falses seen in the first pass; both are fixed
in the tools, not in the engine:

1. **Refused placements were validated as plays.** `evaluate_action()` ran even
   when the engine kept the card in hand, so seed 7 emitted "slot replaced by
   the same card / did not pay" rows for two refused placements. The tool now
   detects refusal from the **hand slot identity** (the played card must leave
   its slot) and skips payment/cycle validation for it. The old elixir
   heuristic was ambiguous for 1-cost cards at 2x elixir.
2. **Ability source identity was taken from an object-list scan.** A Hero
   Knight can be replaced while an older entity is still alive; a stale raw
   object index made the probe refuse activation. `ready_knight_ability()` now
   reads the controller's own `championEntityKeys` from the attested rich
   observation, requires `available`, charges and zero cooldown, and sends the
   tagged native object id. Seed 5 dropped from 4 rejections to 0 with the same
   16 activations.

The 1-cost underpayment check is also skipped when `cost <= regen_ceiling` or
the elixir cap can mask the payment, because that signal is mathematically
uninformative there; the hand-slot check remains authoritative.

Reproduce::

```bash
for seed in 1 2 3 4 5 6 7 8 9 10; do
  .venv/bin/python tools/native_analysis/scripted_xbow_match.py --seed "$seed" \
      --output "/tmp/opencode/soak_xbow_$seed.json"
done
adb logcat -b crash -d
```

**N0 is now satisfied** (10 clean matches, 0 crashes, stable control ports), and
**N1 is satisfied** by the sampled General vs IL result in §3.27 (2–0, 0
rejected, 2/2 normal terminals).

**Operational note (after ~1 h idle).** `:engine0` stopped answering `status`
while the process was still alive (`S`, not frozen; `:engine1` was the resumed
activity). The control thread appeared wedged, not the engine. `force-stop` +
`launch_cluster_a51.sh --engines 2` restored both ports. Keep
`adb shell svc power stayon usb` enabled for long sessions; bring the engine
you are driving back to the foreground if it has been covered/idle.

### 3.29 N1 opponent set — General vs IL and Hog 2.6

All runs on the SM-A515U, sampled decoding, mirror default deck, both seats,
`--sample`, 0 rejected actions, empty crash buffer.

| match | seeds | result (General as A) | ticks | actions A / B | normal terminal |
| --- | --- | --- | --- | --- | --- |
| General vs IL | 1–2 | **2–0 General** | 3601, 3601 | 26/27, 25/27 | 2/2 |
| General vs Hog26-1 | 1–2 | **0–2 Hog** | 6129, 3601 | 52/51, 23/24 | 2/2 |
| General vs Hog26-2 | 1–2 | **0–2 Hog** | 6120, 6118 | 46/47, 48/48 | 2/2 |

Game 2 of the Hog26-1 run is the informative one: Hog took a princess tower by
tick 3601 while General played 23 cards, so that specialist converts early.
General's long-game volume (52 actions) is healthy; the gap is
conversion/defense quality, not passivity.

Hog26-specialist2 is a much closer 0–2: both games went to full overtime
(6120 and 6118, `native_final_result`) with 46/48 General actions and 0
rejections. General now survives regulation against the second specialist and
loses only after the tower race reaches sudden death, which is the expected
direction of travel for a generalist against a specialist.

Wall time: General vs IL 627 s / 2 games; General vs Hog26-1 836 s / 2 games;
General vs Hog26-2 ~840 s / 2 games. Host CPU inference, not the phone.

Artifacts: `/tmp/opencode/eval-general-nulls-sample/`,
`/tmp/opencode/eval-general-hog1/`, `/tmp/opencode/eval-general-hog2/`.

### 3.30 N3 resident throughput — windowing is the scaling lever

Measured on the SM-A515U (`R58NA0QTHWV`, Android 13) with the 16.635.1 cluster
APK, four engine activities (`Engine0..3`), `tools/native_analysis/
resident_batch_throughput.py` (resident `batch-v1`, compact capture, one
affordable card per owner per action round). All runs completed with zero
failures and an empty `adb logcat -b crash`.

**The phone was the surprise.** In the default launch all engines except the
resumed activity land in Android's `background` cpuset:

| cgroup | schedtune | cpuset |
| --- | --- | --- |
| `top-app` | `/top-app` | `0-7` |
| `foreground` | `/foreground` | `0-6` |
| `background` | `/background` | **`0-1`** (little cores only) |

`launch_cluster_a51.sh --engines 2` produced one `top-app` engine (~4.7
matches/s burst) and one `background` engine (~1.1-1.4 matches/s): a ~3x
asymmetry that is a cgroup artifact, not thermals or a probe bug. The big
cores were only at 936 MHz-1.35 GHz, and `cpu5`'s governor never saw a
multi-threaded load.

Keep every engine out of `background` by making it visible:

- engine 0: fullscreen (`am start -f 0x18000000`);
- engine 1: `--windowingMode 4` (split-screen secondary);
- engines 2+: `--windowingMode 5` (freeform).

Android 13 needs `force_resizable_activities`, `enable_freeform_support` and
`freeform_window_management` set globally first. `launch_cluster_a51.sh
--window-mode visible` (default) now does all of this and verifies each
engine's schedtune cgroup. Split-screen/freeform changes the app window size;
the probe path is unaffected because actions and observations are native, not
screen coordinates.

| config | lanes | capacity matches/s | makespan matches/s | notes |
| --- | ---: | ---: | ---: | --- |
| 2 engines, default launch | 2×8 | 3.9-6.1 | 2.2-2.8 | one lane in `background`, unstable |
| 2 engines, split-screen | 2×8 | **5.33** | **5.32** | symmetric, 2.66 per lane |
| 4 engines, mixed window modes | 4×8 | 12.17 | 6.38 | lane 4 pinned to little core 3 |
| 4 engines, big cores 4-7 | 4×8 | **12.3-14.4** | **11.1-13.6** | 2.8-3.8 per lane; fresh launches sit at the top |
| 4 engines, big cores, 16 slots | 4×16 | **14.0-14.5** | **13.4-13.9** | 3.5-3.8 per lane |

Eight consecutive 4×16 runs: **14.0-14.4 matches/s** capacity, **12.1-13.9**
makespan, no downward drift (battery 22.7 °C, cores still boosting). A fresh
4×8 launch measured **14.42** matches/s / 13.56 makespan, so the PPO shape and
the 16-slot shape reach the same engine ceiling; the earlier 12.27 4×8 reading
was a cooler/boost-variance sample. The probe advertises `slotsPerEngine: 8`;
the resident controller accepted 16, but the V4 PPO topology pins eight lanes,
so use `--slots 8` for PPO and 16 for capacity measurements.

`cached_apps_freezer` is now disabled by the launcher as well. The §3.28 idle
control-socket wedge is consistent with Samsung's `FreecessController`
suspending a covered engine; the setting is the same one the emulator
configurator already uses.

**N3 decision.** One A51 supplies ~12–14 matches/s of engine capacity at the
PPO-required 4×8 shape (~14.5 at 4×16). That is far above host CPU inference
(~4.6 policy forwards/s, ~1 match/s), so the phone does **not** need a cloud
engine cluster: the next lever is GPU policy inference, not more engines. A
single A6000-class GPU plus this phone is the intended topology.

Tooling: `launch_cluster_a51.sh` (windowing + cgroup report),
`tools/native_analysis/a51_engine_node.py` (attested engine-node manifest) and
`launch_ppo_a51.sh` (cluster + manifest + remote-worker service) turn the phone
into a normal V4 PPO engine node. The attested manifest
(`/tmp/opencode/a51-node0/engine-node-ready.json`) builds a valid
`v4-ppo-multinode-topology.v1` topology and the worker service binds and
dispatches. Artifacts: `/tmp/opencode/resident_big4_4x16.json`,
`resident_split_2x8.json`, `resident_freeform_4x8.json`, `final_4x8.json`,
`sustain_1..8.json`.

### 3.31 Native-render gate crashes on 16.635.1 (N2)

The headless path is fine, but the **native-render** path is not. Running the
patched `offline_duel` (both seats = General, `native-render`, mirror default
deck) on a fresh engine crashes the process deterministically:

```
F libc  : Fatal signal 11 (SIGSEGV), code 1 (SEGV_MAPERR),
          fault addr 0x746d67227b39fa  tid 18941 (Thread-2) pid 18885 (:engine1)
#00 libg.so
#01 libcrprobe.so (cr_render_gate_swap_buffers+996)
#02 libg.so (0x154aa8c)
#03 libg.so (0x1586930)
#04 libg.so (0x1494248)
...
```

The fault address is ASCII data (`{"gmt...`), i.e. a screen/replay object is
being read as a pointer. The same crash reproduced on a second, freshly
launched engine (uptime 42 s), so it is not an idle/freezer artifact and not
the `render off` state: the manifest/worker `render off` leaves
`renderSuppressed=true` but the crash happens in the pre-suppression
`process_native_render_request_on_frame_thread()` path.

The frame `cr_render_gate_swap_buffers+996` inlines
`process_native_render_request_on_frame_thread()` and
`dismiss_native_dialogs_if_needed()`. The first submits the pending match JSON
through `g_original_replay_json_setter` after reading the replay manager at
`replay_manager+0x20`; on 16.635.1 one of those recovered replay-controller
anchors resolves to a non-replay object. This is the same family as the
`AMBIGUOUS`/`MISSING` replay-controller anchors in §5, not a Python bug.

**Consequences**

- `native_runner/training/v4/evaluate.py` and all resident/PPO training are
  **headless** and unaffected (§3.27–§3.30).
- `offline_agent.py` / `offline_duel.py` are now correctly bound to the live
  ruleset and no longer request the combat-ring feed, but they still cannot be
  used on 16.635.1 until the render gate is fixed.
- Do not use native-render on 16.635.1 for a soak or a demo; it will SIGSEGV
  the engine. Use the headless evaluator instead.

**N2 action:** resolve the replay JSON setter / replay-manager slot for
16.635.1 (the `+0x20` controller slot and the setter's `+0x90` manager read),
then re-attest `render_gate` with a rendered-match smoke. Evidence:
`/tmp/opencode/duel-smoke.log`, `/tmp/opencode/duel-smoke2.log`, and the
`adb logcat -b crash` tombstones above.

### 3.32 A51 parity corpus — deterministic, and the gate the x86 port must pass

Captured on the SM-A515U (`R58NA0QTHWV`) with the installed cluster APK
(`versionCode 160635001`, `versionName 16.635.1`) and probe
`libcrprobe.so` sha `8721be93…`. A single engine (`Engine0`, CPU 4,
`schedtune:/top-app`, `coldReady`, headless). Archive:
`runs/parity-a51/` (gitignored), manifest `runs/parity-a51/manifest.json`,
schema `v4-a51-parity-corpus.v1`.

Provenance recorded in the manifest:

| Artifact | SHA-256 |
| --- | --- |
| probe `libcrprobe.so` (installed) | `8721be936c9cb6218489080ac0b6dc94e0b446af62d6dd157aadd74524928f81` |
| checkpoint General | `2fb7a94fa5df0f12bd72433874487bc7d84a4efb2a6dac827d7adff0a5eef083` |
| checkpoint IL | `aafeb446fde7586d8815a5fcb98b024c67236e45927f6ce05c3fd4c54522d729` |
| cluster APK | `1f33ee4f1c7a33d4cc7d2d5fe54cf3a38dd695582209b2272bff807ae94d417e` |

Evaluator identity pinned `libg` `0b580222…`, content `16.635.93`,
ruleset `0a98e885…`, `combat_ring_hooks: false`.

**Scripted X-Bow, seeds 1–5** (`scripted_xbow_match.py`, 20 ticks/decision,
0 problems, 0 ability rejections):

| Seed | Decisions | Actions | Final tick | Crowns | Winner |
| --- | --- | --- | --- | --- | --- |
| 1 | 174 | 57 | 3601 | 1–0 | 0 |
| 2 | 174 | 56 | 3601 | 1–2 | 1 |
| 3 | 194 | 67 | 4024 | 1–2 | 1 |
| 4 | 174 | 57 | 3601 | 2–1 | 0 |
| 5 | 295 | 128 | 6148 | 3–2 | 0 |

**Determinism:** seed 1 was run twice (`a51-seed1.json`,
`a51-seed1-repeat.json`). Normalising away latency fields, **all 174 decisions
are identical** — same tick sequence, hand, elixir, actions and terminal. The
only per-decision difference is `milliseconds`. The scripted policy is
tick-for-tick reproducible on this build, so it is a valid diff target.

**Model games** (`evaluate.py --sample`, CPU, General vs IL, seeds 7/8, one
game per seat, 0 rejected actions, both `native_final_result`): **General 2–0
IL** (ticks 3774 and 3601), matching §3.27.

**Capacity** (`resident_batch_throughput.py --slots 8 --batches 10 --ticks 600
--actions`): 2.067 matches/s on one engine (12,545 slot-ticks/s, 48 action
receipts); makespan floor 2.066. This is the single-lane reference; the 4-engine
figure is §3.30.

**Gate:** the x86_64 16.635.1 engine (step 1.2–1.4) must reproduce these files.
Use the manifest tool:

```bash
python tools/native_analysis/parity_manifest.py \
    --corpus <x86-corpus> --compare runs/parity-a51/manifest.json \
    --output <x86-corpus>/manifest.json
```

Exit status 0 means every scripted seed, the evaluator rows and the capacity
run match the A51. Timing fields are excluded by design; a diverging tick,
action count, terminal or crown score fails the gate. Re-capture the A51 corpus
only when the probe or client changes.

### 3.33 RoyaleFish x86 relocation cross-check — 41 anchors recovered, 7 corrections

RoyaleFish's `probe_x86_64_16635.diff` (`sha256 ed56d4722fdca943…`) is a port
of **this client build** to x86_64 and carries a generated x86 → ARM table for
145 named anchors. We compared it, byte for byte, with our
`offset-diff.json`. Tool: `tools/native_analysis/crosscheck_royalefish.py`;
raw report: [`ROYALEFISH_CROSSCHECK.md`](ROYALEFISH_CROSSCHECK.md); adoption
table: [`royalefish-overrides.json`](royalefish-overrides.json).

| Bucket | Before | After |
| --- | ---: | ---: |
| AGREE (same offset) | 86 | — |
| CONFLICT (different offset) | 17 | — |
| RESOLVED (we had no offset) | 42 | — |
| `EXACT` / `LIKELY` / `MANUAL` / `AMBIGUOUS` / `MISSING` | 59 / 31 / 15 / 33 / 10 | **53 / 23 / 70 / 0 / 2** |

Both unresolved rows are real: `kTranslatedSha256InitializerCall` (already
missing) and `kCombatHealEffectReturn`.

**Method.** A name is adopted only when the old 15.535.13 anchor and the
candidate produce the same instruction sequence (not just the same 16-byte
prologue), and the candidate agrees with RoyaleFish's own
`logical_{funcs,offsets}.inc` table. The one exception is `kJsonMemberFind`:
the build contains two map-find implementations with identical bodies, so the
**caller chain** (`0x139cadc`, the equivalent of old `0x1217934`) decides which
one the game actually uses. A name-level match alone is not sufficient when the
build duplicates an operation. Vtable candidates were checked against
`R_AARCH64_RELATIVE` relocations: every adopted vtable sits in an RW cluster
with ~120 relocated words in the next `0x400` bytes, so an address in the
wrong object is detectable even though the file bytes are zero.

**Core anchors corrected (runner path, all previously wrong):**

| Anchor | Was | Now | Evidence |
| --- | --- | --- | --- |
| `kReplayManagerGetter` | `0xda0670` | **`0xda0658`** | old `0xc8c5b8` is `adrp; ldr x0,[global]; ret`; `0xda0658` is identical. The old value was a large unrelated function. |
| `kLibgStringFromUtf8` | `0x15267f0` | **`0x153de30`** | old `0x138a800` is a 3-instruction string initialiser; `0x153de30` matches it exactly. |
| `kJsonMemberFind` | `0xe38d60` (MANUAL) | **unchanged, `0xe38d60`** | RoyaleFish offers `0xc40d68`, whose body is instruction-identical to old `0xd0e100`. It is the wrong sibling: the 16.635 wrapper `0x139cadc` (the exact shape of old `0x1217934`) does `add x0,x0,#0x18; stp x1,x2,[sp]; bl 0xe38d60` and `0xe38d60` reads a 16-byte StringView. `0xc40d68` is a different map implementation with a LibgString key. Caller-chain evidence wins over a name-level instruction match. |
| `kStateSnapshotDelete` | `0x11cfb78` | **`0x11ccee8`** | old `0x105e8bc` is a delete function entry; the old value was a `bl` inside the snapshot serialiser. |
| `kGameAppGetter` | `0x7c27ac` | **`0x7c2808`** | old value was a bare `b` thunk; `0x7c2808` is `adrp; ldr x0,[global]; ret`. |
| `kReplayBattleControllerClock` | `0xd9c284` | **`0xd9c278`** | old `0xc890bc` is the `ldrb; cbz; ret; sub sp` guard entry; the old value started after the guard. The guard's `cbz` stays inside the 16-byte steal window, so the verbatim ARM trampoline is still correct. |
| `kBuffAssetClassNameGetter` | unresolved | **`0xebf530`** | now resolved; the 16.635 header has no unresolved core anchor. |

The `kGameAppGetter` adoption deserves one caveat: the old 15.535 anchor inlined
a lazy-init allocation, while `0x7c2808` is the bare `adrp; ldr; ret` accessor.
It sits in the same cluster (the following function is the familiar
`ldr s0,[x0,#0x1a8]` field accessor, old `0x755860` -> `ldr s0,[x0,#0x1a0]`),
so it is the same singleton with the init moved elsewhere. It is exercised by
the touch path, not by native configure, so gate it with a live-touch smoke
before ladder use.

**Four cases where RoyaleFish is not right**

| Anchor | RoyaleFish | Adopted | Why |
| --- | --- | --- | --- |
| `kJsonMemberFind` | `0xc40d68` | **`0xe38d60`** | caller chain `0x139cadc` (old `0x1217934`) proves `0xe38d60`; see the table above. |
| `kWarpPositionCommit` | `0xfc00a0` | **`0xfc019c`** | old `0xe82a34` opens with the full `sub sp,#0x70; stp x28,x27…; stp x20,x19,[sp,#0x60]` frame. `0xfc019c` matches 9/14 instructions including that frame; `0xfc00a0` is a different `0x30`-frame function, and our old value `0xfc01e4` was mid-body. |
| `kCombatHealAreaReturn` | `0x108f1b0` | **`0x108f1ac`** | the anchor is a *caller offset*, i.e. the return address. The `bl kCombatHeal` is at `0x108f1a8`, so the return is `0x108f1ac`; RoyaleFish annotated one instruction later. |
| `kCombatHealEffectReturn` | `0x107d99c` | **unresolved** | the old anchor is the return of a third `kCombatHeal` call. The 16.635 build has only two call sites (`0x108940c`, `0x108f1a8`), so the effect-site call was inlined; neither candidate is its return. |

Two other conflicts kept our value: `kActionDispatch` (`0x10809c0`, 16/20
instruction match) and `kCombatDeflect` (`0x1074190`, exact `0xa0` frame);
RoyaleFish points at a different overload in both cases. `kCombatImpactFunction`
went the other way: the agreed `kCombatImpactBoundary` `0x10870fc` lies inside
`0x1086dc0`, so RoyaleFish is right and our `EXACT` prologue match was a clone.

**What this does and does not do.** It fixes the 16.635 runner/core table and
gives every rich-telemetry subsystem a complete 16.635 anchor set. It does
**not** enable the rich rings yet: `cr_replay_probe.cpp` still compiles the
combat / phase / movement / character-state / remaining / visibility /
tower-troop hooks out under `CR_PROBE_TARGET_16_635_1`, because those layouts
are separate 15.535 tables. Porting each subsystem is now mechanical: take the
new offsets from `offset-diff.json`, emit a per-subsystem 16.635 header, and
drop its `#if !defined(...)` guard one subsystem at a time. The ARM inline hook
copies 16 bytes verbatim, so any new hook whose first 16 bytes contain a branch
target outside the steal window needs a relocated trampoline (N2).

Reproduce:

```bash
.venv/bin/python tools/native_analysis/diff_builds.py \
    .resource/native/15.535.13/reference/libg.so \
    .resource/native/16.635.1/reference/libg.so --probe native_runner/probe \
    --overrides docs/native/16.635.1/manual-overrides.json \
    --overrides docs/native/16.635.1/royalefish-overrides.json \
    --out docs/native/16.635.1/offset-diff.json
.venv/bin/python tools/native_analysis/gen_layout.py \
    .resource/native/16.635.1/reference/libg.so \
    --diff docs/native/16.635.1/offset-diff.json \
    --fields docs/native/16.635.1/field-offsets.json \
    --champion docs/native/16.635.1/champion-layout.json \
    --out native_runner/probe/build_16_635_1_layout.h
.venv/bin/python tools/native_analysis/crosscheck_royalefish.py
```

### 3.34 Corrected 16.635.1 core table — verified on the A51 (and one ABI trap)

The §3.33 corrections were built into the ARM64 probe and re-attested on the
SM-A515U.

| Artifact | SHA-256 |
| --- | --- |
| probe `libcrprobe-16.635.1.so` | `db9d560939cfdeae27617f3955919236827d28d051c0784ff42b5cb059b8a691` |
| cluster APK (`-rf`) | `9e345ad3b2db0cf565a215bd686653ce74463741eb6f80837961d9bb1e6ecf88` |
| ARM64 `libg.so` | `0b580222e5a3502cf153bb5e6297ec32d2b154b605b777c74873dd75722e4eaa` |

Bring-up: `launch_cluster_a51.sh --engines 1 --window-mode plain` reported
`coldReady` on port 26789. All **nine** runner hooks installed with **no
prologue mismatch**, and `kUnresolvedAnchors` is empty (`kBuffAssetClassNameGetter`
is now resolved). The clock hook installs at `libg+0xd9c278` — the guard entry,
one instruction ahead of the old body hook — and the trampoline is still valid
because the guard's `cbz` target stays inside the 16-byte steal window.

**Scripted X-Bow, seeds 1–2** (same harness and deck as §3.32):

| Seed | Decisions | Actions | Abilities | Rejected | Problems | Final tick | Crowns | Winner |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 1 | 174 | 57 | 9 | 0 | 0 | 3601 | 1–0 | 0 |
| 2 | 174 | 56 | 8 | 0 | 0 | 3601 | 1–2 | 1 |

Both rows are **identical to the §3.32 corpus** (decision count, action count,
terminal tick, crown score and winner), and the crash buffer is empty. The
corrected offsets therefore preserve the scripted decision stream; the existing
`runs/parity-a51/` corpus remains a valid x86 target (its manifest still records
the old probe sha — re-capture it before the x86 gate if provenance must be
exact).

**The ABI trap.** The first adoption also changed `kJsonMemberFind` to
`0xc40d68` (RoyaleFish's value). On device, `configure` then SIGSEGV'd:

```text
#00 pc 0x148672c libg.so            # string hash, x0 = FNV offset basis
#01 pc 0xc40da8 libg.so             # kJsonMemberFind+0x40
#02 pc 0x3f2dc   libcrprobe.so      # native_controlled_worker_main
```

`0xc40d68` is instruction-identical to the old `0xd0e100`, but it is the *other*
map implementation: it reads a `LibgString` (length at `+4`, data at `+8`). The
game's JSON path uses `0xe38d60`, which reads a 16-byte `{data,size}` StringView;
its caller `0x139cadc` is the exact shape of the old wrapper `0x1217934`
(`add x0,x0,#0x18; stp x1,x2,[sp]; bl finder`). Passing a LibgString to
`0xc40d68` interpreted the key pointer's high word as a length and the size as a
pointer. Reverting to `0xe38d60` and keeping the StringView ABI restored the
deterministic matches above.

Lesson for the remaining N2 port: when a build contains two implementations of
the same operation, adopt by **caller chain and ABI**, not by instruction
similarity alone. `royalefish-overrides.json` deliberately does not override
`kJsonMemberFind`.

---

### 3.35 x86_64 16.635.1 engine on this Linux host — **the A51 parity corpus reproduces**

HANDOFF step 1 (x86_64) is done, and it did **not** need the pinned Android
bionic userland from `LINUX_BIONIC_RUNTIME.md`.  `.resource/Clashback` already
ships the same client build as x86_64 `libg.so` (sha `c4721bd9…`) together with
`vendor/crx`, an ELF loader that maps Android x86_64 libraries into a plain
Linux process and resolves bionic imports against glibc.  The engine boots
headless in **0.3 s** on this laptop:

```text
libg base ------------------------------------------------------------------
JNI_OnLoad -> TitanApplication.nOnNativeLibrariesLoaded
           -> GameApp.nOnApplicationCreate -> GameApp.createGameMain
           -> GameApp.nOnCreate -> GameMain::init (data tables 0x720 ready)
```

**The probe attaches from the host.**  crx maps libg anonymously, so the probe's
`dlopen`/`/proc/maps` scanners cannot find it.  The x86 build exports
`cr_probe_attach(libg_base)`, and `crx_engine_host.py` passes the base plus the
on-disk identity the mapping hides (`CR_PROBE_LIBG_PATH`,
`CR_PROBE_SELF_PATH`, `CR_PROBE_CONTENT_ROOT`, `CR_PROBE_PACKAGE`,
`CR_PROBE_ASSETS_MAPPED`).  Everything after attach is the unchanged
`cr-native-control.v1` endpoint, so every A51 tool runs against it:

```bash
.venv/bin/python tools/native_analysis/crx_engine_host.py --seconds 1800 &
.venv/bin/python tools/native_analysis/scripted_xbow_match.py --seed 1 \
    --output /tmp/opencode/x86-seed1.json
```

**Port shape.**  `build_16_635_1_layout_x86.h` carries the x86_64 values of the
core anchors; the rich-ring layout tables stay ARM-only and are not installed on
16.635.1 (same as the A51).  `install_inline_hook` uses `x86_hook.h` (14-byte
absolute jump + relocating trampoline), `native_call_x86_64.cpp` replaces the
ARM assembly for by-value results and the remaining-runtime entries, and
`logical_{offsets,funcs,vtables}.inc` translate reported x86 offsets back to ARM
so JSON stays comparable with the A51.

**The mirror of §3.34's ABI trap.**  The first x86 `configure` SIGSEGV'd in
`lookup_json_member`:

```text
#0 libg+0x14f50d0  string hash (rdi = 6)
#1 libg+0xc76a92   kJsonMemberFind
#2 libcrprobe.so   lookup_json_member
```

On x86_64 the JSON path calls the **legacy** `LibgString` finder
(`0xc76a60`, ARM `0xc40d68`), not the `{data,size}` StringView finder
(`0xe38d60`) the A51 uses.  The 16.635 ARM build and the 16.635 x86_64 build
contain both implementations and dispatch to different ones.  The probe now
selects the key ABI per architecture; both paths stay documented so neither is
"simplified" back into the other.

**Attestation is exact, not bypassed.**  The x86_64 engine is pinned as its own
release: `native_runner/versions/native-16.635.1-x86_64.json` (package
`com.supercell.clashroyale`, ABI `x86_64`, libg `c4721bd9…`, probe
`cd303311f3b8edd6…` (was `b83e3d60…` before the §3.38 host-port fix), content
`16.635.8`).  `CR_NATIVE_BUILD_ABI=x86_64` selects
that pin; the ARM64 pin is untouched.  `attest` reports
`production_ready: true` with every hash populated.

**Parity gate — scripted X-Bow, `runs/parity-a51/` reference:**

| Seed | Decisions | Actions | Abilities | Rejected | Problems | Final tick | Crowns | Winner | vs A51 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |
| 1 | 174 | 57 | 9 | 0 | 0 | 3601 | 1–0 | 0 | **match** |
| 2 | 174 | 56 | 8 | 0 | 0 | 3601 | 1–2 | 1 | **match** |
| 3 | 194 | 67 | 10 | 0 | 0 | 4024 | 1–2 | 1 | **match** |
| 4 | 174 | 57 | 9 | 0 | 0 | 3601 | 2–1 | 0 | **match** |
| 5 | 295 | 128 | 16 | 0 | 0 | 6148 | 3–2 | 0 | **match** |
| 1 repeat | 174 | 57 | 9 | 0 | 0 | 3601 | 1–0 | 0 | deterministic |

`tools/native_analysis/parity_manifest.py --compare runs/parity-a51/manifest.json`
reports `first_difference: null` for all five seeds, and the repeat is
decision-identical.  One full scripted match is **~0.22 s** of engine time; the
0.7 ms mean decision latency is transport, not policy.

**What this changes.**  Training is no longer phone-bound: the 16.635 ruleset
runs on the host, and the A51 keeps its role as the ARM64 oracle for deployment
checks.  The evaluator's *policy* stream is still under investigation (below):
the scripted corpus proves rules and hand/elixir/placement parity, not yet the
full entity observation the policy tensorizer consumes.

**Evaluator parity — root cause found.**  General-vs-IL on x86_64 finishes
clean on both seats (`2/2` A, `0` rejected, `native_final_result`) but at ticks
3601 and 4794 versus the corpus's 3774 and 3601.  A raw trace on the A51 with
the corrected `db9d5609…` probe reproduces game 1 exactly (tick 3774, winner 0,
737 decisions), so the corpus is still valid for that probe.  Comparing the two
converted `ObservationV1` streams, the first 190 decisions are identical; from
decision 190 the only substantive difference is **projectile runtime**:

```text
entities[].entity_kind            A51: spell/troop/effect      x86: projectile
entities[].projectile_state       A51: null                    x86: native state
provenance.projectile_state       A51: unknown                 x86: native_derived
```

The first *action* divergence follows at decision 197 (tick 1080): A51 plays a
Knight, x86 waits.  The cause is the A51 probe's stale vtable table — on 16.635
it still compares `kProjectileVtable`/`kCharacterVtable`/`kBuffAssetVtable` and
the capture/threshold/periodic vtables against the 15.535 values, so projectile
classification fails closed, while the x86 build uses the corrected 16.635
values.  The A51 observation is therefore the degraded one; x86 restores the
15.535-shaped (training-shaped) runtime that the checkpoints were trained with.

The ARM 16.635 values are already resolved MANUAL in `offset-diff.json`
(`kProjectileVtable 0x01a50348`, `kCharacterVtable 0x01a4fbb8`,
`kBuffAssetVtable 0x01a349f0`, capture `0x01a3fab0`/`0x01a51a20`, threshold
`0x01a41da0`/`0x01a52fc0`, periodic `0x01a41998`/`0x01a52ca8`).  Emitting them
in `build_16_635_1_layout.h`, redeploying the ARM probe and re-capturing
`runs/parity-a51` closes the gap on both sides; mirroring the stale constants on
x86 freezes the current corpus instead.  Until one is chosen, the scripted
corpus is the parity evidence; the evaluator comparison is a known A51-side
observation regression, not an x86 rules difference.

**Resolved in §3.36** with option A: the ARM probe now emits and uses those
anchors, and the evaluator reproduces the x86 ticks/actions exactly.

### 3.36 ARM64 observation parity — the rich vtables are back, and the evaluator matches x86

§3.35 traced the evaluator divergence to a one-sided fix: the 16.635 rich
classification constants were emitted for x86_64 only.  On ARM the probe fell
through to the **15.535** literals, so `kProjectileVtable`,
`kCharacterVtable`, `kBuffAssetVtable` and the capture/threshold/periodic
pairings never matched a 16.635 object.  The policy therefore saw
`entity_kind: spell/troop/effect`, `projectile_state: null` and
`provenance: unknown` where x86 saw the native projectile runtime.

**Fix (option A from §3.35).** The nine ARM64 anchors resolved in
`offset-diff.json` are now emitted by `build_16_635_1_layout.h`, and
`cr_replay_probe.cpp` sources them from the generated header under
`CR_PROBE_TARGET_16_635_1` on **both** architectures (the four
`... && defined(__x86_64__)` guards are gone).  Names are identical in both
headers; `tests/test_probe_build_layout.py` now checks that the two lists agree
and that the ARM values still equal `offset-diff.json`.

| Artifact | SHA-256 |
| --- | --- |
| probe `libcrprobe-16.635.1.so` / `libcrprobe.so` | `b80f2bdb02fabc0562e89f9c74f68ec1abcdaed2f0b7dc452f698e6ccef8928a` |
| cluster APK (`-rf`, 4 engines) | `17893d1c606f7b02c07195d399e6763878b4ae86ff627a38f349fe28b34ca15d` |
| ARM64 `libg.so` | `0b580222e5a3502cf153bb5e6297ec32d2b154b605b777c74873dd75722e4eaa` |

The ARM pin (`native_runner/versions/native-16.635.1.json`) now records the new
probe; `native_runner/probe/out/libcrprobe.so` was refreshed so the default
`CR_PROBE` path and the pin agree.  `attest` reports production-ready and the
node manifest records `probe_sha256 b80f2bdb…`.

**Evaluator parity.** General vs IL, seed 7, two games, both seats, `--sample`
(identity `3e4e3425…`, 0 rejected actions, `native_final_result`):

| Game | Seat | A51 before (§3.32) | A51 after | x86_64 |
| ---: | --- | --- | --- | --- |
| 1 | General (owner 0) | tick 3774, actions 28/28 | **tick 3601, actions 25/27** | tick 3601, actions 25/27 |
| 2 | General (owner 1) | tick 3601, actions 27/26 | **tick 4794, actions 40/37** | tick 4794, actions 40/37 |

Both games now terminate at the same tick with the same action counts, winner
and crown score as the x86_64 engine.  The converted observation streams agree
from the first decision; the decision-197 projectile difference in §3.35 is
gone.  This is the gate that makes the x86 engine an evaluator oracle rather
than a scripted-only substrate.

**Scripted corpus is unchanged.** Seeds 1–5 and the seed-1 repeat were
re-captured on the new probe: decisions/actions/terminals are identical to
§3.32 (174/57/3601, 174/56/3601, 194/67/4024, 174/57/3601, 295/128/6148), and
the repeat is decision-identical.  The capacity run is structurally identical
(8 slots × 600 ticks × 10 batches, 48 receipts; 1.918 matches/s on the A51
versus 21.4 on the x86 host, which is expected and excluded by the comparer).
Timing, host ports, and per-process resident-slot `generation`/`stateEpoch`
counters are excluded; the slot count, steps, ticks and action receipts are
compared.

**Comparator correction.** `tools/native_analysis/parity_manifest.py` used to
compare the whole evaluator `identity.json`, including package, ABI,
libg/probe hashes and the ruleset id — fields that *must* differ between the
ARM64 oracle and the official x86_64 build — and keyed evaluator runs by their
directory name.  Evaluator entries are now keyed by experiment (checkpoint ids
+ seed + games) and compare only the match definition, catalog release,
`combat_ring_hooks` and every `matches.jsonl` row.  Scripted and capacity
comparisons are unchanged.  `tests/test_parity_manifest.py` covers the
cross-build match, row divergence and match-definition divergence cases.

**Gate result.** The x86_64 corpus reproduces the re-captured ARM corpus in all
eight entries — five scripted seeds, the seed-1 repeat, the General-vs-IL
evaluator, and the 1×8 capacity run:

```text
$ python tools/native_analysis/parity_manifest.py \
    --corpus runs/parity-x86 --compare runs/parity-a51/manifest.json \
    --output runs/parity-x86/manifest.json
8 entries, 1 determinism checks, deterministic=True
reproduces runs/parity-a51: True
```

The A51 corpus is `runs/parity-a51/`; both corpora are local (gitignored).

### 3.37 x86_64 PPO worker path — zlib resolution and 16.635 tensorization

The x86_64 engine already served the control API, the scripted matches and the
evaluator (§3.35/§3.36), but the PPO **worker** data plane failed on its first
batch:

```text
RunnerError: resident batch env 7 response decode failed:
Error -5 while decompressing data: incomplete or truncated stream
```

The failure was always the last framed slot only because `step_partitioned`
reports the first failing lane to reach the coordinator; in fact every
compressed entry was corrupt.

**Root cause.** `compress_json` is on for `capture_mode="compact"`. crx resolves
the probe's imports through `dlsym(RTLD_DEFAULT)` and substitutes a stub that
prints *"nothing provides"* and returns 0 for anything it cannot find.
`tools/native_analysis/crx_engine_host.py` preloaded `libz.so.1` with a plain
`ctypes.CDLL()`, i.e. `RTLD_LOCAL`, so `compress2`/`compressBound` were *not* in
the global scope. The probe then:

1. computed `compressBound(...) == 0` (stub), so the scratch buffer was 0 bytes;
2. called `compress2(...)` (stub), which returned `0 == Z_OK` and left
   `compressed_size` at 0;
3. passed `compressed_size < payload_size`, so it flagged a **zero-byte zlib
   entry** for the host to inflate.

**Fix.** Preload `libz.so.1` (and `libm.so.6`) with `ctypes.RTLD_GLOBAL`, and
make the host fail closed: after loading the probe it checks crx's `missing`
table for `compress2`/`compressBound` and exits 4 instead of hosting a probe
that will silently corrupt the data plane. That fix left the probe binaries
unchanged (`b80f2bdb…` ARM, `b83e3d60…` x86_64); a later host-identity fix moved
only the x86_64 hash to `cd303311…` (§3.38). A `compressed_size > 0` guard in
`resident_multimatch.inc` was written and reverted because it would change both
pinned hashes to protect only the one host that can lose zlib.

Verified with `tools/native_analysis/profile_cluster_collection.py` against one
local x86_64 engine, 8 lanes: 24 frames, 0.0 failures, **4.06 packets/s**,
49 ms median packet interval (the elapsed total includes the eight lane resets).

**The two schemas that blocked remote training.** With transport fixed, the real
loop exposed the two handoff blockers on the same x86 engine:

| Blocker | Symptom | Fix |
| --- | --- | --- |
| worker tensorizer hard-wired to the 16-column schema | `mat1 and mat2 shapes cannot be multiplied (128x16 and 21x128)` for a 16.635 checkpoint | `AsyncResidentEngineSpecV4` now carries `competitive_release` + `model_config` |
| 15.535 catalogs cannot resolve 16.635 entities | `active public effect resolved to UNKNOWN: buff:2523220774` | the worker builds the checkpoint's release bundle for `BattleEnvV1` **and** both tensorizers |

Mechanics:

- `collect_async_cluster_wave_v4` derives the release/config from the actor
  model and rewrites every engine spec with them, so the worker cannot disagree
  with the model the trainer is running. Anchor/history models are rejected up
  front when their contract differs — one engine tensorizes every actor row.
- `_engine_worker` binds `use_release(load_release(...))` around engine
  construction and around `_reset_engine`. Passing the release name alone is not
  enough: `BattleEnvV1` re-verifies its semantic subset against the *active*
  release's form evidence, and a named 16.635 release has non-default evidence.
- The trainer builds the learner and every frozen model from the **checkpoint
  contract** (`resolve_checkpoint_release` + `config_from_contract`) instead of
  the pinned default release, checks the IL anchor at startup, and records
  `competitive_release`/`model_config` in the run config.
- `evaluate.py` derives its environment bundle from the two checkpoints and
  refuses a mixed pairing. General-vs-IL stays on the 15.535 bundle, so the
  §3.36 parity numbers are unchanged.

**Result.** `B-general-v275` (17.5M params, LSTM 768, runtime schema 21,
`16.635.1` contract) now collects through the CPU PPO worker against the x86
engine with **0 UNKNOWN effects and 0 rejection/fallback counters**:

```bash
.venv/bin/python tools/native_analysis/profile_real_collection.py \
    --checkpoint .resource/RoyaleFish/models/B-general-v275.pt \
    --engines 1 --lanes 8 --steps 12
```

One 48-decision segment, 8 lanes × 2 owners = 16 actor rows: 768 owner frames,
`tensorize 0.96 s`, `native_step 2.34 s`, `model 16.48 s` of 25.0 s wall,
29.0 lane-decisions/s, `inference_mean_frame_packets 1.0`. The loop is still
one packet per forward and CPU model time dominates, exactly as profiled in the
handoff; the GPU box is what removes that term, and the transport now has
nothing left in it that fails on x86.

General (the 15.535-contract checkpoint) still fails on this engine on a 16.635
effect id. That is expected and is the correct behaviour: the x86 substrate
(content 16.635.8) trains 16.635-contract checkpoints, and a 15.535-contract
model needs the A51's content-matched run or an explicit adaptation step.

The collector's post-first-packet wait is now tunable with
`CR_PPO_INFERENCE_BATCH_WAIT_MS` (default 2 ms, validated 0–1000). It cannot be
exercised with more than one x86 engine on this host yet because the probe
hard-codes its control port to `26789` (fixed in §3.38); measure a widened window on the A51
cluster (`--engines 4`) and read `inference_mean_frame_packets` back from
`metrics.jsonl`.

### 3.38 Host engine ports and the learner batching window (2026-11-14)

**Host engine identity.** On Android `JNI_OnLoad` calls
`initialize_engine_instance()`, which reads the `:engineN` process name or the
per-user `cr_engine_id` file. crx loads the probe with `dlopen` and never calls
`JNI_OnLoad`, so every host engine stayed engine 0 on port `26789` even though
`crx_engine_host.py` already accepted `--port`. `cr_probe_attach` now calls the
initialiser before starting the control server, and the initialiser honours two
optional overrides:

| Variable | Range | Effect |
| --- | --- | --- |
| `CR_PROBE_ENGINE_ID` | `0..23` | engine id, and the control-lane CPU |
| `CR_PROBE_CONTROL_PORT` | `1..65535` | listen port; wins over `26789 + id` |

`crx_engine_host.py --engine-index N` exports both and defaults the port to
`26789 + N`. Verified with two concurrent crx processes — engine 4 on
`26793`/cpu4 and engine 5 on `26794`/cpu5, both `production_ready`, and one
`profile_cluster_collection.py --engines 2 --engine-base-port 26793` run
answered both ports (8 packets, 0 failures). The x86_64 probe pin is now
`cd303311…`; the ARM artifact remains `b80f2bdb…`, so a later ARM rebuild picks
up the same code and must update that pin.

**The batching window is a cohort effect.** The collector waits
`CR_PPO_INFERENCE_BATCH_WAIT_MS` after the first ready packet so the other
engines can land in the same forward. Measurements: 4 A51 engines, 8 lanes,
`B-general-v275`, seed 1, gate temperature 0.2, 32 decisions/lane
(`profile_real_collection.py --engines 4 --lanes 8 --steps 8`). Evidence:
`runs/batching-20261114/window-*.json` plus the two-engine x86 transport run.

| Window | Mean frame packets/forward | Forwards | Model s | Lane-decisions/s | Wall s |
| --- | --- | --- | --- | --- | --- |
| 2 ms (default) | 2.00 | 64 | 70.3 | 23.6 | 86.8 |
| 30 ms | 2.00 | 64 | 74.5 | 21.5 | 95.1 |
| 100 ms | 2.00 | 64 | 54.8 | 26.0 | 78.8 |
| 150 ms | 2.56 | 50 | 61.4 | 23.0 | 89.0 |
| **250 ms** | **3.88** | **33** | **54.5** | **24.7** | **82.8** |

A batch can only contain engines that already have an outstanding receive. At
2 ms the first fetch takes two frames, only those two receive actions, and the
other two sit parked in completed futures; every later forward re-batches the
same pair. A 20–40 ms window is shorter than one engine round-trip (~100–150 ms
at 8 lanes; the native step alone is ~60–70 ms), so it changes nothing. At 250 ms the
just-released pair finishes its step inside the window and all four coalesce:
**forwards fall from 64 to 33** and mean packets/forward reaches 3.88 (max 4).

CPU wall time is flat because the 17.5M model is the local bottleneck; the
saving is in per-forward fixed cost, which is what the 4 %-utilised A6000 paid
in the §3.37 smoke. The window has no single-engine penalty: at 250 ms and one
engine, `inference_mean_frame_packets` stayed 1.0 and `ready_wait` tracked the
engine step, not the window. The useful rule is therefore *window ≈ one engine
step*, not a fixed 20–40 ms; re-measure if lanes or engine count change.

**Action legality is the next blocker, not batching.** The same 4-engine
configuration at gate temperature 1.0 fails reproducibly at `resident batch env
5` with `resident batch step or atomic capture failed`; at gate 0.2 the same
seeded run completes with 0 failures. Both runs used B-general's 16.635
contract. That is the §3.37 caveat materialising: candidate legality is still
the 15.535 shadow rule and `delay_aware_legality` is carried but not branched
on, so a sampled deploy can be legal to the model and rejected by the 16.635
engine. Fail-closed is correct; the fix is to port delay-aware candidate
semantics before collecting at full ACT rate.

**Resolved in §3.39.** The port is in; gate temperature 1.0 now completes
cleanly in six segments, including the 4-engine shape above.

### 3.39 Delay-aware candidate legality — the 16.635 flag is honoured (2026-11-14)

`B-general-v275` declares `delay_aware_legality: true` in its contract, but
until now this tree carried the flag without branching on it: the decoder and
`BattleEnvV1` both applied the 15.535 decision-tick rule (`cost <= elixir at
d`). The rule is now ported from the reference 16.635 implementation
(FirstLight-Zero, Apache-2.0), and the environment and the policy agree:

    e(k) = max(e0, min(10, e0 + regen_per_tick * k)) - spent

for delay bin `k = 0..4` (0–200 ms), where `e0` is the live effective elixir
(raw minus in-flight reserved commands) and `spent` is the cost of earlier
micro-actions of the same decision. A candidate is legal when some reachable
bin (not before the previous micro-action, and inside the optional inference
delay cap) is affordable.

**Decoder** (`training/v4/decoding.py`). `ShadowCandidateLegality` gains
`elixir_multiplier` (from the `elixir_multiplier_over_3` match scalar × 3),
`allowed_delay_bins`, `spent_elixir`, per-bin `elixir_at_bins`,
`affordable_at_bins`, a candidate-aware `delay_offset_mask(step,
candidate_index)` and the `earliest` delay policy
(`UniversalCardPolicyV4.set_delay_policy`). Legacy checkpoints keep the old
rule because their contract omits the field (`ADDITIVE_CONFIG_KEYS`), so the
15.535 General/IL parity numbers are unchanged. The multiplier snaps to the
native segment table (1× 28 s, 2× 14 s, 3× 9.3 s), which also absorbs the fp16
`1/3`, `2/3` scalar precision.

**Environment** (`battle_env.py`). `action_mask` now exposes
`elixir_regen_per_tick`, `touch_window_elixir` and `affordable_from_bin`
(cards and abilities); ability candidates use the touch window;
`_validate_compound_actions` checks every prefix in touch order with the
earlier touches still reserved, and reads the bin from
`policy_delay_offset_bin` (falling back to
`execute_offset_ticks - base_latency_ticks`). Expert alignment validates the
stored delay bin under the same rule.

**Tests.** New unit tests cover regen per bin, the 3× multiplier, the
inference delay cap and compound reservation
(`test_training_v4_decoding.py`, `test_battle_env.py`). The four host C++
contract tests that were failing on the ARM logical-offset helpers now stub
the identity mapping (`test_action_movement_runtime_telemetry_compile.cpp`,
`test_phase_runtime_telemetry_compile.cpp`,
`test_movement_event_json.cpp`, `test_native_telemetry_json.py`), and the full
`native_runner/tests` suite passes.

**Full-rate collection gate (A51, B-general, gate temperature 1.0).**

| Shape | Seeds | Segments complete | Lane-decisions/s | Rejected |
| --- | --- | --- | --- | --- |
| 1 engine × 8 lanes, 40 decisions | 1–5 | **5/5** | 15.0–22.3 | 0 |
| 4 engines × 8 lanes, 40 decisions | 1 | **1/1** | **28.5** | 0 |
| 1 x86_64 engine × 8 lanes, 40 decisions | 1 | 1/1 | 23.9 | 0 |

The 4-engine run used the default 2 ms inference window, so it coalesced
**1.99 packets/forward** (32 actor rows); it is the same shape that failed at
`resident batch env 5` in §3.38. While it ran, `adb logcat -s CRNativeProbe`
recorded **3,979 `injected command parse=success` and 0 `parse=failure`**, so
no sampled command reached the native parser malformed. Evidence:
`runs/delay-aware-20261114/` (`profile-*.json`, `probe-logcat-4engine.txt`).

**One unreproduced failure.** The first A51 segment run immediately after this
port still failed once (`env 6`) at gate 1.0. Six later segments — including
the original 4-engine shape and a fresh 4-engine launch — completed cleanly,
and the failure could not be re-triggered. It was not captured with the new
diagnostics, so it is **not** claimed as fixed. Two additions make the next
occurrence cheap to identify:

- `CR_PPO_FAILURE_DUMP=<path>` writes the pre-step decoded actions, decks and
  live action-mask reasons for the aborted lanes
  (`async_cluster_self_play._write_decode_failure_dump`).
- `CRNativeProbe`'s `injected command parse=…` line in logcat separates a
  parse/injection rejection from a step-bound or capture failure; the current
  batch error string still collapses those three stages and should be split in
  the probe if the failure recurs.

### 3.40 The batching window is not a win; the closed loop is the critical path (2026-11-14)

Session 5 measured the window at gate temperature 0.2, before delay-aware
legality landed; session 6 measured gate 1.0 at the 2 ms default. This run is
the combination the GPU job would actually use: **gate 1.0 + delay-aware +
`CR_PPO_INFERENCE_BATCH_WAIT_MS`**, 4 A51 engines × 8 lanes × 40 decisions,
`B-general-v275`, seed 1–3, plus a 2 ms control on the same cluster. Evidence:
`runs/delay-aware-20261114/combined/`.

| Config | Wall s | Lane-dec/s | Packets/fwd | Forwards | Model s | Ready-wait s |
| --- | --- | --- | --- | --- | --- | --- |
| 250 ms, seed 1 | 287.8 | 35.6 | **4.00** | 160 | 223.0 | 60.9 |
| 250 ms, seed 2 | 309.8 | 33.1 | 3.98 | 161 | 233.2 | 72.5 |
| 250 ms, seed 3 | 331.2 | 30.9 | 3.98 | 161 | 251.6 | 74.4 |
| **2 ms control, seed 1** | **271.0** | **37.8** | 1.99 | 321 | 250.7 | **14.8** |
| §3.39 2 ms baseline | 359.5 | 28.5 | 1.99 | 321 | 337.2 | 16.1 |

**The window does exactly what it was designed to do and still loses.** Forwards
fall 321 → 160 and packets/forward reach 3.98–4.00, but the added wait (up to
0.25 s per forward, ~40 s, against an observed `ready_wait` increase of 46–60 s)
is larger than the saved per-forward cost: the model time only falls from 251 s
to 223–252 s. The reason is structural: the row count is fixed (10,240 frames)
and the CPU model is row-bound, so halving forwards cannot halve model time. The
per-forward fixed cost is ~0.17 s here, below the 0.25 s window. **Keep the 2 ms
default.**

The same logic rules the window out on a GPU: a batched forward there is ~2 ms,
so paying 250 ms to coalesce it can only add latency. The GPU smoke's 416 ms per
packet was **model 27 ms + closed-loop bookkeeping ~390 ms**, not forward count.
The next lever is therefore pipelining receive/decode/apply across engines, not
coalescing forwards.

Seed-to-seed variance is real (287.8–331.2 s for one config), so treat the ~6 %
delta as directional, not precise; the structural argument above is what decides
it. All three 250 ms gate-1.0 segments completed with no `resident batch`
failures.

**Also fixed in this session.** The 16.635 evaluator path had never been
exercised (the only prior model gate was 15.535 General-vs-IL). It failed
closed in three places, now fixed: `build_native_ruleset_manifest` reads
`card_logic`/runtime data outside the release scope, `BattleEnvV1` verifies its
subset against the active release's form evidence, and `runtime_scope` hard-pins
152/122 cards. The release scope now wraps both the manifest and the environment,
and `runtime_scope` takes its counts/partition/case scope from
`current_identity()` (153/123 on 16.635, 152/122 legacy). With that, seed 7
completed `B-general-v275` vs `A-hog26-v738` at tick 4703 with 0 rejected, and
seed 21 at tick 6112 (overtime) with 0 rejected.

One x86 engine death remains unexplained: the first pre-flight attempt lost the
`crx` host at `step 5` with no traceback; the identical rerun completed. Treat it
like the §3.39 `env 6` one-off — diagnostic, not claimed fixed.

**Stress gate (2 ms + gate 1.0 + delay-aware).** Ten consecutive 4-engine ×
8-lane × 40-decision segments (seeds 1–10) all completed: mean **311.5 s**
(270.7–344.2), **29.7–37.8 lane-decisions/s**, 1.99 packets/forward.
`adb logcat -s CRNativeProbe` recorded **4,097 `parse=success` and 0
`parse=failure`**, with no `resident batch` errors and no
`CR_PPO_FAILURE_DUMP` file written. The §3.39 `env 6` did not recur in these ten
segments (16 consecutive clean segments across both sessions), so the config is
accepted for the GPU gate — still not claimed fixed.

**Pre-flight baseline (x86_64, CPU).** Four games of `B-general-v275` vs
`A-hog26-v738`, seeds 31–34, both seats: **2–2**, ticks 4059/3601/6103/4514,
**0 rejected**, all `native_final_result`. Per-game wall is ~5.4 min on CPU, so
this is not the volume path; it verifies the evaluator, the release binding and
the pairing before a GPU run. Evidence:
`runs/baseline-16635/preflight-4games/`.



### 3.41 The learner loop is pipelined; the per-packet floor is observation work (2026-11-15)

Session 8 had one task: stop resolving **one packet per forward** and paying the
whole closed loop per packet. The loop is now pipelined — and the measurements
that gate it also correct the throughput ladder, because the ceilings the plan
used were measured **without observation decode or tensorization**, which is
~90 % of a real training packet.

**Loop change** (`training/v4/async_cluster_self_play.py`). The old loop was a
single-thread wave: receive → 2 ms window → assemble → forward (blocking) →
send → store. The forward therefore sat between two receive windows, and
nothing was staged while it ran. The collector now has:

- one **inference thread** (`ppo-inference`, `max_workers=1`) and at most one
  batch in flight, so the LSTM-state read/modify/write, the CUDA-graph replay
  and the forced-ACT re-forward stay ordered;
- the coordinator **stages** every completed receive while the forward runs,
  then hands the whole staged set to the inference thread (continuous
  batching); the 2 ms coalescing window is applied at the job boundary too, so
  a drain does not forward one engine at a time;
- segment **boundary bootstraps** run on the same inference thread (they share
  the model, the state table and the graph cache);
- per-actor ordering is preserved structurally: a lane cannot produce a new
  frame before its act was sent, so at most one packet per lane is in flight
  and the rollout row order is unchanged. `CR_PPO_FAILURE_DUMP`, the
  rejected-action accounting and the reward semantics are untouched.

**The transport floor, decomposed.** `profile_cluster_collection.py` now takes
`--diagnostic-timing`, which asks each worker for its per-round split. One
x86_64 16.635.1 engine, 8 lanes, 2 ms window, x86 host (i5-1235U, 2 P-cores +
8 E-cores):

| Component | Per packet |
| --- | --- |
| `advance` (worker round) | **38.2 ms** |
| — native `step` incl. observation build | **32.1 ms** |
| — tensorize 16 owner rows | **13.8 ms** |
| — action decode | 0.3 ms |
| batch channel total | 5.6 ms (send 0.4 / ack 1.5 / receive 2.5 / decode 2.8) |
| outside the channel (observation objects) | 26.4 ms |
| **measured packet interval** | **51.1 ms median / 81.6 ms mean** |

So one engine × 8 lanes runs **12.4 packets/s = 0.138 matches/s** (3,600-tick
matches) = **0.082 matches/s in the ladder's 6,068-tick units** — 12 % of the
0.61 "channel ceiling". Scaling: 2 engines 14.9 packets/s, 4 engines 22.5–25.1
(total, i.e. 5.5–6.3 each), 16 lanes on one engine 7.1 packets/s (95 ms). The
per-engine cost is per *slot*: 8→16 lanes raises the packet interval from 51 to
95 ms and matches/s only from 0.138 to 0.158.

**The ladder's ceilings are not engine ceilings.** `resident_batch_throughput`
without a policy gives 0.661 matches/s for one engine (3,922 slot-ticks/s), but
**0.736 aggregate for four engines**, not 2.6. During the four-engine run the
client process sat at 100 % CPU while every engine host was at 7–9 %: that
number is a single Python client's decode limit. It is a useful *transport*
ceiling and no more. Observation capture and tensorization add ~46 ms to the
raw 10 ms packet, so the usable per-engine training rate is the 0.14
packets-side number above, not 0.61.

**A/B of the loop itself.** With the real 17.5M checkpoint the CPU collector is
model-bound (92–93 % of wall), so the loop cannot be seen. `profile_real_collection.py`
therefore gained `--synthetic-model`, which builds the checkpoint's *contract*
(same release, same schema, same flags) at narrow widths. 4 x86 engines × 8
lanes × 32 decisions, seed 1, same host:

| Loop | Wall s (3 runs) | Model s | `ready_wait` s |
| --- | --- | --- | --- |
| old wave | 33.72 / 33.94 / 32.71 | 17.3 | 13.1 |
| **pipelined** | 28.67 / 30.68 / 33.77 | 17.0 | **11.5** |

Non-model wall falls from ~16 s to ~14 s (−14 %) and the idle `ready_wait` from
13.1 s to 11.5 s; model time is unchanged within noise. That is the whole
available win at this engine count when the forward is row-bound; with the real
(17.5M) model the forward is 98 % of the wall and the loop makes no measurable
difference (below). The gain is therefore for the fixed-cost forward, i.e. a
GPU, which is §3.40's argument extended to pipelining.

**The multi-segment steady state is model-bound in both loops.** With the real
checkpoint, 4 x86 engines, seed 7, three segments each:

| Loop | Seg 1 wall / model / wait | Seg 2 | Seg 3 |
| --- | --- | --- | --- |
| old wave | 95.4 / 77.7 / **15.37** s | 80.6 / 79.5 / 0.20 s | 67.1 / 66.2 / 0.19 s |
| **pipelined** | 81.4 / 66.7 / **12.93** s | 68.8 / 68.0 / 0.18 s | 56.4 / 55.6 / 0.19 s |

Only the **first** segment idles: the cluster is cold and the engines are
starting. From segment 2 on, *both* loops sit at 0.19–0.20 s of idle and
wall ≈ model, because the CPU forward is 98 % of the wall time. So the
pipelining gain is **not** visible with this model on this host; it shows only
when the forward is cheap relative to the worker (the synthetic A/B above:
−7.5 % wall, −14 % non-model), which is the GPU regime — and there only at
engine counts where the coordinator would otherwise be the serial resource
(~4 engines × 35 ms/packet of bookkeeping ≈ 28 packets/s cap). The rewrite is
therefore a prerequisite for the GPU/8-engine rig, not a CPU throughput win,
and it is neutral (within model noise) wherever it is not a win.

**Gates.**

- `native_runner/tests`: **1,293 passed / 18 skipped**, including the
  deterministic collector-protocol fingerprint test.
- Real checkpoint (`B-general-v275`), 4 x86 engines x 8 lanes x 32 decisions,
  gate 1.0, seed 7, **10 consecutive segments: 10/10, exit 0**, 20,480 owner
  frames, no rejected-action error and **no `CR_PPO_FAILURE_DUMP` file**.
  Wall 56.4-81.4 s per segment (mean **69.5 s** for segments 2-10) against
  **68.6 s of model time**, with `ready_wait` **0.18-0.20 s** — the steady state
  is 99 % forward, i.e. the coordinator is no longer the serial resource. Full
  packetisation stays at 2.00 packets/forward (4 engines on 2 physical
  performance cores).
- Scripted 1×8 floor is **0.138 matches/s**, 4×8 is **0.278** (profiler units).
  The plan's gates (1× x86 ≥0.45/s, 8× ≥2.5/s) are **not reachable** on this
  worker path: 0.45/s is 5.5× the single-engine floor, and 2.5/s at 8 engines is
  ~3.8× the 8-engine floor (assuming linear scaling on distinct cores), i.e. the
  per-packet worker cost must fall from ~80 ms mean to ~21 ms.
  Pipelining is necessary for scale but not sufficient.

**Where the field's 10 matches/s comes from (checked in `.resource`, 2026-11-15).**
Not a faster engine: a thinner per-decision data path and a far larger farm.

| | FirstLight-Zero | daybreak-cr | us |
| --- | --- | --- | --- |
| Concurrency | 8 resident per collector, many collectors; the 9.85/s figure needs ~16 collectors = **128 slots** | 13–50 collectors, **one game each** | 4 engines x 8 lanes |
| Per-decision data | compact fixed arrays: 26 scalars, 64 x 14 units, 5 options | the same arrays built by a **C extension** (`cr/native_features.c`; `CR_NATIVE_FEATURES=0` is the Python path) | `ObservationV1` objects + 21-column tensorizer in Python |
| Serving | one inference server for the whole farm, CUDA graph per (model, batch bucket) | one decision server per GPU, every waiting collector in one CUDA-graph call | local forward, ~2 packets/forward |
| Model | 1.17M params, pruned to one mirror | 1.26M / 3.4M / 8.0M | 17.5M |

Both call the same `env.step(..., policy_features_only=True)` we do, so the
engine and the environment contract are not the difference.

**The arithmetic.** 9.85 matches/s / 128 slots = **0.077 matches/s per slot**.
Our raw engine (no observation work) is **0.081**; our real loop is **0.017**.
They run at the engine's raw limit; we are ~5x above it. Their edge is that
observation and serving cost them almost nothing per decision.

That also corrects the §3.25 aside that "FirstLight's 9.85 matches/s is 16 slots
x ~0.62 matches/s": 16 slots at 0.62/s each would need ~3,700 slot-ticks/s,
while our engine does ~490 raw and ~150 with capture. The count that makes the
units consistent is 128 slots, which is what the project actually runs.

**Judgment and plan (session-9 decision).** Fix the data path before buying
compute or changing engines: it is the only work that multiplies every engine,
every run and every GPU hour at zero recurring cost.

| # | Step | Effort | Gate |
| --- | --- | --- | --- |
| 1 | Micro-profile `BattleEnvV1.step` (cProfile + per-owner split of the 26 ms); decide entities vs events vs relations vs beliefs before rewriting | 1/2 day | top-5 functions explain the 26 ms |
| 2 | Thin or native the observation path (fuse step->tensor and/or C/numpy; daybreak's *method*, not its code) | 3-5 days | **<=25 ms/packet at 8 lanes**, parity corpus unchanged |
| 3 | Parallelise tensorization across the worker's CPU group | 1 day | tensorize <=7 ms/16 rows |
| 4 | One short GPU session (~1 h): validate the pipelined inference thread on CUDA | 1 h | no graph/stream errors; >=0.5/s on 1x x86 |
| 5 | Move 8 engines to a real 8-core host (plain x86_64 ELF; this laptop has 2 P-cores) | 1 day | ~2/s aggregate |
| 6 | 400+ paired games on the native engine, then a bounded specialist fine-tune (X-Bow 3.0 or Hog 2.6) before the generalist league | days of GPU | Wilson intervals, 0 rejected |

If step 2 lands, 1 engine is ~0.26 ladder-matches/s and **8 engines ~2.1/s =
~180 k matches/day**, i.e. FirstLight's whole 1,450-round run in ~8 days. The
original 2.5/s goal survives; it just needs 8 engines and a thin path, not one
engine and a raw-channel ceiling.

**Not the plan.** crforge now (different ruleset 16.635.3/8, needs a V4
observation adapter, never a gate — revisit only if the native farm is still
uneconomical after step 5); phone farming (the A51 is the ARM64 oracle); a long
GPU fine-tune before the packet cost drops (the GPU idles 96 % of the time); and
chasing 2.5/s as a target rather than decisions/day at gateable quality.

**Cheap parallel experiment.** Rules permitting, ~20 ladder games of
`A-hog26-v738` / `B-general-v275` on Null's measure the one risk throughput
cannot: the official-16.635 -> Null's-balance domain gap.

**Unverified.** The CUDA path was not exercised in this session (no GPU): the
inference thread is CPU-verified only. Before a paid run, confirm on the box
that graph capture/replay, the autocast context and the `model_streams`
cross-stream waits behave from a non-main thread — the collector-protocol test
covers the eager/CPU sampler.

Evidence: `runs/loop-pipeline-20261115/` (`s8-*.json`, stress log).

### 3.42 `BattleEnvV1.step` attribution — contract plumbing, and lanes do not scale (2026-11-15)

§3.41 established the floor. This section says what the floor is made of and
what to remove. New tool:
`tools/native_analysis/profile_step_breakdown.py`. It drives the real resident
batch protocol in the worker's own shape (decode every lane, `begin_round`, one
lane thread per slot, `finish_round`) with a scripted policy that plays a card
every fourth decision when the mask allows it, and reports two views:

- **sections** — wall time inside every relevant `BattleEnvV1` method,
  accumulated per worker thread and divided by lane packets;
- **cProfile** — one lane, sequential, sorted by self time, with a per-module
  aggregate and an optional `--profile-dump` for `pstats.print_callers`.

Setup for every number below: x86_64 engine, release **16.635.1**, checkpoint
`B-general-v275`, `--play-every 4`, warmup discarded, profiler process pinned to
a core the engine does not use. The host is an i5-1235U (2 P-cores + 8 E-cores)
and a browser was running, so absolute rates move ±30 % between runs;
**CPU ms per packet** and **shares** are the stable quantities.

#### One lane, current tree

Median of three runs (min–max in parentheses):

| section | ms / lane-packet | share of `step` |
| --- | ---: | ---: |
| `step` | **17.1** (16.8–19.4) | 100 % |
| `observe` (both owners) | **10.7** (10.6–11.8) | 62 % |
| — `_entities` (rich projection per unit) | 4.7 | 27 % |
| — `_extract_towers` (rich projection per tower) | 2.7 | 16 % |
| — `action_mask` (two misses per packet) | 1.9 | 11 % |
| — `_players` | 0.9 | 5 % |
| — `_events_for` (visibility filter) | 0.8 | 5 % |
| `_advance_headless` (engine round + channel) | 5.0 | 29 % |
| post-advance bookkeeping (`_derive_events`, rewards, …) | 0.9 | 5 % |
| remainder (action normalisation, tracing, …) | 0.5 | 3 % |

The step-1 gate is met: `_entities`, `_extract_towers`, `action_mask`,
`_players` and `_events_for` *are* the observation build (they sum to 11.0 ms,
i.e. all of `observe`).

#### Where that CPU actually goes (cProfile, 60 sequential steps)

Per module, self time (top rows cover 64 % of profiled self time):

| origin | ms / step |
| --- | ---: |
| Python builtins (`isinstance`, `dict.get`, `getattr`, `any`) | 8.5 |
| `native_runner/contracts.py` (dataclass `__init__`/`__post_init__`, `_freeze`, `validate_fields`) | 5.7 |
| `native_runner/battle_env.py` | 5.3 |
| `native_runner/rich_telemetry_adapter.py` | 3.6 |
| `native_runner/arena.py` (placement legality) | 1.2 |
| `native_runner/phase_runtime.py` | 0.9 |

`pstats.print_callers` shows most of the builtin traffic coming from
`contracts._freeze` (167 k `isinstance` calls per 40 steps before the fix below)
and the rich adapter's typed field reads. The observation build is dominated by
**turning the native frame into validated, deeply frozen Python contract
objects**, not by game logic. `EntityStateV1` has 34 fields and
`TowerStateV1` 18; the tensorizer reads `internal`, `movement_target`, `effects`,
`capture_runtime`, `periodic_attack_modifier`, `shield_state` and
`runtime_provenance` **zero times**.

#### Fix shipped: scalar-first `contracts._freeze`

`FrozenMapping.__init__` runs `_freeze` on every leaf of every contract mapping,
and the old ordering paid up to four `isinstance` checks before returning a
plain string. Exact scalar types now return first (subclasses such as enums
still take the general path), and `str` keys skip a redundant `str()`.

Paired A/B, three alternating single-lane runs each:

| | median pkt/s | CPU ms/packet | `step` ms | `observe` ms |
| --- | ---: | ---: | ---: | ---: |
| stock `_freeze` | 48.1 | 19.0 | 20.5 | 13.2 |
| scalar-first | **57.4** | **15.7** | 17.1 | 10.7 |

Median **+19 %** packets/s for two lines, with no schema or behaviour change.

#### Fix shipped: one tower projection per tick

`step` projected the after-state towers *without* runtime telemetry, then
`observe` projected the same tick *with* it. A rich result is a superset of the
ordinary one, so `_extract_towers` now keeps a rich cache and `step` asks for the
rich projection once; `action_mask`, `_derive_events` and `observe` reuse it.
`--trace-towers` shows one computed projection per tick and every later call
served from cache (the remaining ordinary-path calls are hits). The removed
duplicate was ~0.5–1.8 ms/packet depending on the core — below this host's
run-to-run noise, but pure waste and now gone.

#### Lanes do not scale — the worker is GIL-bound

Same engine, `--lanes 1..8`, process CPU measured with `getrusage`:

| lanes | pkt/s | CPU ms/packet | process CPU / wall |
| ---: | ---: | ---: | ---: |
| 1 | 59.1 | 15.2 | 0.90 |
| 2 | 49.0 | 18.6 | 0.91 |
| 4 | 84.0 | 11.0 | 0.93 |
| 8 | 52.7 | 17.9 | 0.94 |

The process consumes **one core at every lane count** (0.90–0.94) and lanes buy
at most ~1.5× in the best run. The observation build is pure Python and
serializes on the GIL; lanes only overlap the engine wait. This is the same
number as the §3.41 training floor (8 lanes ≈ 70 lane-decisions/s ≈ 0.10
matches/s) — which had been read as "per-engine throughput".

Consequences:

- **Throughput comes from processes (engines), not lanes.** More lanes per
  engine is a tail-latency trick, not a multiplier. The worker server already
  forks one process per engine; that is the parallelism that measured as
  additive-ish in §3.41.
- **"Parallelise tensorization across the worker's CPU group" cannot work as
  written.** Threads in one process cannot overlap Python; only torch ops that
  release the GIL can. Measure before spending the day.
- The step-2 target should be **CPU per packet**, not wall per packet.

#### What step 2 should cut, in measured order

1. **Contract machinery (~30–35 % of the build).** Training observations never
   serialize, hash or compare provenance. `policy_features_only=True` should
   skip `runtime_provenance` for entities and towers, and internal training
   construction should bypass field validation (the wire constructors keep it).
2. **Rich runtime projection (~25–30 %).** `project_runtime` builds attack,
   phase, visibility, effect, shield and capture states per entity per tick.
   The tensorizer reads a subset; unused states should not be constructed in
   training mode.
3. **`action_mask` (~11 %).** Two misses per packet (one per owner), each over
   four hand slots, `_hand_runtime_contract` and placement masks; the hand
   runtime contract is re-derived 16×/packet.
4. **`_players` + `_events_for` (~10 %).** Per-owner event visibility filtering
   over the bounded window.

The fused native/C path (daybreak's `native_features.c` method) is still the end
state — build tensor rows directly from the native frame and never create the
contract objects. But items 1–4 are ordinary Python edits with measured targets,
and they shrink the parity surface any C path would have to reproduce, so they
come first.

Evidence: `runs/step-breakdown/` (`final-lane1-profiled.json`,
`ab-freeze-*.json`, `gil-lanes-*.json`, `towers-cached-*.json`,
`verify-towers.json`).

### 3.43 Lean training observations — contract machinery is gone from the policy path (2026-11-15)

§3.42 measured where a packet's CPU goes and named step 2: the observation build
is dominated by validated, deeply frozen contract objects, and none of that
machinery is information a policy forward pass consumes. This section removes it
from the in-process training path only.

#### What changed

- **`contracts.construct_contract`** — a non-validating constructor: it assigns
  dataclass fields directly and skips `__post_init__`, the `FrozenMapping` freeze
  walk, domain checks and the provenance coverage audit. Wire/persistence
  constructors (`cls(...)`, `from_mapping`, `to_dict`) are untouched and still
  fail closed. A `lean_contracts()` `ContextVar` scope marks construction as
  non-validating so nested helpers do not have to thread a flag.
- **`unknown_provenance`** — one shared all-unknown `SemanticProvenanceV1` per
  field universe (cached). It is immutable, and the training tensorizer never
  reads provenance.
- **`project_runtime` / `project_player_runtime`** — take `lean=True`; every
  typed runtime state and the phase/attack state built inside the scope goes
  through `construct_contract`, and `_field_provenance` returns the shared
  unknown record instead of building evidence/source tuples and a cache key.
- **`BattleEnvV1.observe(policy_features_only=True)`** now builds
  `EntityStateV1`, `TowerStateV1`, `PlayerStateV1` and `ObservationV1` through
  `construct_contract`, skips the entity/tower/player provenance trees, the
  projectile-velocity `replace()` (the tensorizer reads the entity velocity, not
  the projectile's) and the non-feature public metadata. `action_mask`,
  `_validate_action` and `_validate_compound_actions` take the same `lean` flag.
- **Cache hygiene.** Tower projections, the fair scene and the per-owner player
  runtime are keyed by `(native identity, lean)`, so a lean call can never serve
  a validated caller. `_native_form_aliases` is cached per snapshot identity and
  `_hand_runtime_contract` per validated hand selection (16 projections per
  packet before; both caches are dropped with the rich cache on every advance).

#### Measured

Paired A/B, eight **alternating** stock/lean runs on one x86 engine, one lane,
`--play-every 4`, warmup dropped, checkpoint `B-general-v275`. CPU ms/packet is
the stable quantity, but this host swung ~2x between runs during the session
(browser/background load), so only the adjacent pairs are comparable:

| measurement | stock | lean | delta |
| --- | ---: | ---: | ---: |
| CPU ms/packet (median of 8 pairs) | 11.75 | **7.81** | **−33.5 %** |
| per-pair ratio (median) | — | — | **0.72 (−28 %)** |
| lane-packets/s | 77.9 | **112.9** | **+45 %** |
| `step` | 12.66 | 8.70 | −31 % |
| `observe` | 6.73 | 3.96 | −41 % |
| `_entities` | 3.43 | 1.37 | −60 % |
| `_extract_towers` | 1.84 | 1.02 | −44 % |
| `_players` | 0.60 | 0.27 | −55 % |
| `_hand_runtime_contract` | 0.39 | 0.25 | −36 % |
| `_native_form_aliases` | 0.028 | 0.010 | −64 % |
| `action_mask` | 1.87 | 1.74 | −7 % |
| `_events_for` | 0.51 | 0.48 | flat |
| `_advance_headless` (engine round + atomic rich capture) | 3.33 | 3.15 | flat |

At the worker shape (8 lanes, two pairs): **10.55 → 8.23** and
**12.49 → 9.41** CPU ms/packet, i.e. 75–89 → 97–112 lane-packets/s.

**Gate.** The 8 ms target is met at the median of the quiet window on one lane
(7.8–7.9) and missed at 8 lanes (8.2–9.4), but it was a single absolute number
on a host whose load moves the number ~2x; the portable result is the pair ratio
(−28 %, four pairs −31…−37 %, best-and-worst −5 %). The shape is what matters:
the observation build is no longer the majority of a packet. One lane is now
`step` 8.7 = `_advance_headless` 3.1 (engine round + atomic rich parse) +
`observe` 4.0 + masks/bookkeeping ~1.6.

cProfile confirms the removal: in the training path there is no `_freeze`,
`validate_fields`, `SemanticProvenanceV1` or contract `__post_init__` row left in
the top 20. The remaining cost is `_rich_object`/`_phase_object` JSON binding,
the socket `recv`, `resolve_attack_runtime` and placement geometry — not
contracts.

#### Parity and tests

The validated path is unchanged: `observe()` still defaults to `lean=False`, so
the scripted parity seeds and the evaluator construct exactly what they did
before. `tests/test_training_v4_tensorizer.py` additionally rebuilds a full
observation (entities, towers, players, action mask) through
`construct_contract` and asserts the tensorizer emits bit-identical tensors;
`tests/test_contracts.py` pins that `construct_contract` skips validation while
`cls(...)` still rejects. Suite: **1,296 passed / 18 skipped** (+3 new tests).

A real-checkpoint segment also ran through the new path end to end:
`profile_real_collection.py --checkpoint B-general-v275 --lanes 8 --steps 20`
collected **1,280 owner frames over 80 inference batches**, with 16/16 boundary
bootstraps and no tensorizer/storage fallback. Evidence:
`runs/step-breakdown/lean-real-segment.json`.

#### Next

Step 3's per-lane pooling was already shown to be a small fraction (§3.42);
the measured remaining target is the rich telemetry binding (`_rich_object` +
`_phase_object`, ~1.5–2.5 ms/packet) and the native round trip (~3 ms). That is
the fused path — build tensor rows straight from the rich frame instead of
creating intermediate Python contracts.

Evidence: `runs/step-breakdown/ab-{stock,lean}-{1..8}.json`,
`ab8-{stock,lean}-{1,2}.json`.

### 3.44 The tensorizer is the per-decision packet, and it is Python ceremony (2026-11-16)

The C1 spike (§session 13) split the bulk packet into three parts: vectorized
observation facts ~0.03 ms/lane, `BattleEnvV1.observe` ~0.65 ms/lane, and
`UniversalObservationTensorizerV4` ~1.4 ms/lane. The tensorizer was then the
largest row, so C3's first question is *where inside it* the time goes, and the
second is whether that time can be tuned or only restructured.

#### The C1 tensorizer figure was measured on an empty board

`_scripted_commands` in `tools/crforge/encoder_spike.py` picked a legal cell with

```python
entry = mask.placement_masks.get(str(slot))
if isinstance(entry, dict):      # never true
```

`placement_masks` entries are `FrozenMapping` contracts, so the guard never
fired, the scripted policy stayed on `WAIT` for the whole measurement, and the
tensorizer was timed on a board with towers and no troops. Two consequences:

- the earlier "observe 0.647 + tensorize 1.413 = 2.060 ms/lane" understated the
  tensorizer, because entity rows, groups, children and relations were empty;
- it is a silent class of bug in exactly the tool that is supposed to catch
  silent differences, so it is now `isinstance(entry, Mapping)` in both tools.

Scripted plays also need the building anchor offset: a building placed with
`subcell_offset=None` is rejected (`building 27000006 requires native subcell
offset (0.5, 0.5)`), so the scripted policy mirrors the entry's
`model_subcell_offset` with the acting owner's sign.

#### A replay profiler instead of a live profile

`tools/crforge/profile_tensorizer.py` runs one live bridge episode with a real V4
policy (`B-general-v275`, the evaluator deck, both seats, sampled decoding),
captures each decision's observation *and* the `ActionSequenceV4` the policy
recorded, then replays only the tensorizer half of the decision — `tensorize`
plus `record_action` — under `cProfile`. Replaying removes the model forward and
the engine round trip, so the profile is the tensorizer's own cost.

A scripted play cannot be used for this: the public tracker only stays consistent
when every played card is recorded, and a replay of observations without their
actions trips the hand/cycle partition check. That is why the tool spies
`record_action` during the capture and replays the real action tensors.

The tool records three things that make the numbers checkable later:

- a capture digest (`decisions`, recorded actions, first/last tick, entity,
  pending and event totals) — the same sequence must be replayed;
- a per-decision SHA-256 over every tensor in the batch, aggregated into one
  `batch_digest` — an optimized path must be **bit-identical**, not "same shape";
- CPU ms/decision (`time.process_time`) next to wall time, because this host's
  wall and even CPU time shift ~1.5–2x with background load and frequency
  scaling (`HANDOFF.md` §3.43).

Shape of the measured run: **128 decisions** (the episode ends at tick 725),
entities median **8** (min 0, max 11), 20 pending commands and **12,625 events**
total (≈99 events per observation, which the public tracker walks on every
decision).

#### Where the packet goes (stock, 1.069 CPU ms/decision)

`cProfile` sections, cumulative ms/decision, 128 decisions:

| section | cumulative ms | self ms |
| --- | ---: | ---: |
| `tensorize` (total) | 2.227 | 0.036 |
| `_groups` | 0.725 | 0.241 |
| `_card_sets` | 0.390 | 0.037 |
| `_candidates` | 0.300 | 0.103 |
| `_card_runtime_features` | 0.189 | 0.046 |
| `tracker.update` | 0.172 | 0.038 |
| `_towers` | 0.145 | 0.048 |
| `_relations` | 0.142 | 0.085 |
| `_spatial_planes` | 0.117 | 0.034 |
| `_events` | 0.090 | 0.041 |
| `record_action` | 0.064 | 0.015 |
| `reconcile_owner_private_state` | 0.033 | 0.019 |

The section totals are only half the story. The call census is the other half —
**3,754 Python calls per decision**, and the top rows are not game logic:

| callee | calls/decision |
| --- | ---: |
| `isinstance` | 330 |
| contract mapping `__getitem__` | 217 |
| `tensorizer._finite` | 214 |
| `Mapping.get` (FrozenMapping) | 195 |
| `dict.get` | 212 |
| `max` / `min` / `sum` (builtins) | 117 / 101 / 85 |
| `torch.from_numpy` | 53 |
| `torch.tensor` | 16 |

Two structural facts follow. First, **34.8 % of profiled time is builtin call
overhead plus the frozen-mapping layer** (28.1 % builtins — `isinstance`, `len`,
`dict.get`, `max`/`min`/`sum` — 4.1 % `contracts.py`, 2.6 % `Mapping.get`), while
the builder's own bytecode is 50.5 %: the packet is Python bookkeeping around
small arrays, not array math. Second, `_groups` alone is a third of the packet
because it walks each group with a dozen separate generators (`sum`, `min`,
`max` over member lists) and allocates a full-capacity arena per decision.

#### What was shipped, and what it did **not** buy

Contained reductions, all bit-identical to the stock tensorizer
(same `batch_digest` on the same capture):

- **`_finite` fast path** — the common `int`/`float` case skips the
  `isinstance` chain and the `try/except`; `result - result == 0.0` keeps the
  NaN/inf behaviour. 214 calls per decision.
- **Cached actor deck layout** — the actor's deck order is episode-invariant, so
  the vocabulary sort and eight catalog lookups happen once per episode instead
  of once per decision.
- **Per-frame player lookup** — keyed by `(id(observation), owner)` so a
  perspective-transformed observation cannot serve a raw player row. Removes
  ~20 `next(...)` scans per decision.
- **Single-pass group aggregation** — one loop over a group's members replaces
  ~13 `sum` + 10 `min`/`max` generator passes, with the same arithmetic order.

Profiled effect on the same capture: `_groups` **0.725 → 0.543**, `_card_sets`
0.390 → 0.326, `_towers` 0.145 → 0.128, whole-section `tensorize`
2.227 → 1.875.

End-to-end effect: **not measurable on this host.** Two alternating stock/pairs
(240 decisions, 3 repeats each, CPU ms/decision):

| pair | stock | shipped | delta |
| --- | ---: | ---: | ---: |
| 1 | 1.190 | 1.124 | −5.6 % |
| 2 | 1.263 | 1.435 | +13.5 % |

Run-to-run variance (±15 %) exceeds the effect, so there is no throughput claim
here; the changes are kept because they are proven identical and do less work,
not because they moved the gate. A first attempt that replaced
`torch.tensor(list)` row builds with NumPy arenas was **reverted** for the same
reason: it was digest-identical and moved `torch.tensor` out of the profile, but
the end-to-end number was flat, so the extra allocation surface bought nothing.

#### End-to-end at the C1 shape (and why two numbers exist)

The C1 spike at its default shape (256 lanes × 200 × 3, tables 16.635.8,
content `062a11e0…`) re-measured the existing path with the corrected scripted
policy and the shipped tensorizer:

| measurement | threads 1 | threads 8 |
| --- | ---: | ---: |
| vectorized facts (ms/lane) | **0.0165** | 0.0177 |
| vectorized decisions/s | **60,702** | 56,495 |
| per-lane bridge control (ms/lane) | 0.315 | 0.353 |
| encode speedup | **19.1×** | 19.9× |
| frame equivalence | pass | pass |
| cold `observe` (ms/lane) | 0.400 | 0.400 |
| `tensorize` in-session (ms/lane) | **0.697** | 0.697 |
| projected end-to-end (ms/lane) | **0.713** | 0.714 |
| projected decisions/s | **1,402** | 1,400 |
| projected matches/s (1,214/match) | **1.16** | 1.15 |

So **C3's gate is met**: ~0.71 ms/lane end-to-end, 1.15 matches/s per process,
against a ≥0.8 matches/s gate. The 10–30× target (≤0.5 ms/lane) is not.

The two tensorizer numbers differ by measurement shape, not by code: the C1 tool
reports the **minimum** single decision over 100 decisions of a seed-1 game
(0.697), while the C3 profiler reports the **mean over a whole replay** of a
seed-7 game whose board carries a median of 8 entities (0.992). A packet's real
cost is the replay mean, so quote 0.99–1.07 ms/decision when sizing a run and
0.71 ms/lane only as the gate measurement.

**Open item found on the learner deck — resolved in §3.45.** Measuring the same
path with the X-Bow learner deck (with its Tesla/Archer/Knight forms) failed
closed in the tensorizer: `public entity resolved to UNKNOWN archetype:
entity_kind='effect', native_data_global_id=1`. The offending row was the Knight
hero with a signed Java global id, not an effect; §3.45 records the two bridge
fixes, the run-refusal namespace bug they exposed, and the clean 2-game
learner-deck mirror. The C1 measurement still stays on the evaluator deck.

#### Verdict

The tensorizer is no longer "slow numpy"; it is **per-decision Python object
ceremony over a 2,200-line semantic builder**. Micro-optimization has been
measured and does not move the packet. So the C3 gate is met at ~0.73–0.81
matches/s per process, but the ≤0.5 ms/lane target needs one of two structural
changes:

1. **Batch the row build across lanes.** One pass over 256 lanes filling the
   same fixed-size arrays turns 3,754 calls/decision into ~15 calls/lane —
   this is the 10–30× case, and it is the C3 work the roadmap budgets 1–2 weeks
   for.
2. **Port the hot row builders to C/Cython** (daybreak-cr's
   `cr/native_features.c` shape). `_groups`, `_candidates`, `_relations` and
   `_card_sets` are ~70 % of the profiled packet and are pure loops over small
   arrays; a C port keeps the semantic code single-sourced and removes the
   interpreter, without changing the observation contract.

Do not expect another round of Python tuning to cross 0.5 ms/lane.

Evidence: `runs/crforge-c1/encoder-spike.json` (the C1 default shape, above),
`runs/crforge-c3/tensorizer-profile.json` (shipped tree), `ab-stock.json` (stock),
`ab-optimized.json` (NumPy variant, reverted), `ab-pair{1,2}-{stock,python}.json`
(A/B pairs). All C3 runs: same capture digest, `batch_digest 0a59a11d…`. Tests:
`native_runner/tests/test_training_v4_tensorizer.py` (72, including the new
`_finite` guard table), `crforge_bridge/tests` (17 after §3.45).

Tooling notes for the next session: `tools/crforge/bridge_session.py` builds the
shared live episode (deck, tower troop, release scope, one session per seat) for
both tools, and `tools/crforge/profile_tensorizer.py --profile-decisions 0` is the
timing-only mode for A/B runs (cProfile costs several times the work it measures).

### 3.45 The learner deck's "effect" row was the Knight hero, and a run refusal is a match code (2026-11-17)

The C3 open item — the X-Bow learner deck failing closed with
`public entity resolved to UNKNOWN archetype: entity_kind='effect',
native_data_global_id=1` — was two encoding bugs in the bridge, not a missing
archetype. The evaluator deck never showed it because it carries no hero.

#### The row was `KnightHero`

`_nearest_card_id` stripped `_EV1` and `_hero`, but hero *unit* rows are
`<Card>Hero` (`KnightHero`), so the row kept `cardId=0`. `BattleEnvV1` maps a
non-positive card with no spec to `entity_kind='effect'`, which is why the row
looked like a spell. Fix: strip `Hero` too; the remainder must resolve in the
same catalog, so this cannot invent a card.

Identity was the second half. `Engine._bind_holder_identity` read
`entity.getData().globalId()` as a Java `int`; KnightHero is `-437504191` there,
the `0 < global_id` guard rejected it, and the placeholder `dataGlobalId=1` set
by `entity_rows` survived. The compiled archetype catalog and the native probe
key the unsigned bit pattern: `3857463105 → form:Knight_hero` (vocab 215). The
fix masks to `0xFFFFFFFF` before the range check.

Repro before the fix, 80 learner-deck decisions, both seats: fail-closed at
`entity_id=1005000007, entity_kind='effect', card_id=None,
native_data_global_id=1`. After: 90 decisions and a complete 2-game mirror with
`placeholder dataGlobalId rows: {}`.

#### A run refusal is a `LadderMatch` code, not a `PlayerCommands.Refusal`

The first clean rollout still counted one distinct reject in game 2 (seen 15
times across the 45 s event window). The event was a queued Electro Spirit
refused at its run tick; its payload had no `reason`, so `evaluate` reported
"unknown". The run refusal was `code 4`, which the bridge named from
`Schema.refusalNames()` — the *giving*-refusal enum, where 4 is `START_COOLDOWN`.
`H_LAST_RUN_REFUSAL` carries the *match gate's* code, where 4 is
`LadderMatch.OVER`. The play had lost a race with a decided match (crown
advantage; the end-screen delay puts the terminal tick 71 ticks later).

Two fixes:

- `crforge_bridge/engine.py` names run refusals from the `LadderMatch` constants
  (`OVER`, `KING_DEAD`, `NOT_IN_HAND`, `START_COOLDOWN`, `NOT_ENOUGH_ELIXIR`,
  `RUN_NOT_PLACED`), read from the pinned class rather than copied.
- `native_runner/battle_env.py` resolves a queued play refused with `OVER` as
  `action_canceled_terminal`, exactly as the give-stage `MATCH_OVER` path does.
  A play that loses the race with the end of a match is not an illegal action and
  must not invalidate a rollout.

`evaluate` now also names both rejection shapes (`data.reason` vs
`data.rejection_diagnostic.native_run_refusal_name`) and reports
`rejected_unique_total` next to the window total (one event stays visible for a
450-tick window, so the raw total over-counts).

#### Validation

`runs/crforge-c3/learner-deck/` — locked X-Bow deck, mirror, `B-general-v275`,
sampled, seeds 7–8, bridge transport:

| game | seed | terminal | tick | illegal | rejected |
| --- | ---: | --- | ---: | ---: | ---: |
| 1 | 7 | crown_advantage | 3,681 | 0 | 0 |
| 2 | 8 | crown_advantage | 4,032 | 0 | 0 |

Both games are decision-identical to the pre-fix run (same tick, same action
counts), so the fix removed the spurious rejection without changing play. The
learner deck is no longer a barrier to a C4 rollout. It also passes the C2 parity
gate on the same deck: `resident_parity.py --deck learner --decisions 60` is
**60/60 decisions identical** (resident 5.91 ms, bridge 7.33 ms, paired 1.29× —
parity pass; the 2× throughput gate remains C3).

Tooling: `tools/crforge/profile_tensorizer.py --resident --deck learner` and
`tools/crforge/resident_parity.py --deck learner`; the locked deck lives once in
`tools/crforge/bridge_session.py` (`LEARNER_DECK`, `LEARNER_FORM_AVAILABILITY`).
C1's recorded 0.71 ms/lane stays the evaluator-deck gate measurement; the
learner-deck tensorizer is not cheaper: **1.171 CPU ms/decision** (median 1.202,
90 decisions, entities median 5 / max 11) in
`runs/crforge-c3/learner-deck/tensorizer-profile.json`, against 0.992 on the
evaluator deck. More spells, evolutions and a hero row, not a regression.

### 3.46 The vectorized observation facts layer — C3-B's precondition (2026-11-18)

C3-B is "batch the row build across lanes", and the handoff calls a vectorized
`ObservationV1` its real precondition. C1's spike built a private `PolicyFrame`
for the *encoder* comparison only: it carried hand/deck/pending and the entity
rows, but no towers, no placement rows and none of the derived entity state the
observation layer supplies. A row build cannot consume that frame without
inventing fields, so the first session-17 step is a production facts layer.

#### What landed

`crforge_bridge/vector_observation.py` — `VectorObservationEncoder` fills a
fixed-size `VectorObservationBatch` from `VectorBattle.observations` /
`masks` for every lane at once. It carries every fact the bridge's per-lane
`decode_rows` / `player_facts` / `entity_rows` expose, plus:

- both seats' header scalars (elixir, crowns, refusals, pending count,
  `enemy_seen` / evolution columns, per-seat own/enemy tower HP);
- both seats' hand / deck / cycle / pending rows and the packed
  `card_parameter`, vectorized with the bridge's own slot resolution;
- all six towers as fixed rows selected by the arena's pinned entity ids (not
  by a row name), with the seat header's HP fallback when the shared scene
  reports an enemy health bar as unknown;
- the native placement masks as a `(lanes, seats, 4, 32, 18)` **view** of
  `VectorBattle.masks`, which is the candidate-placement half of the
  tensorizer;
- derived per-entity state the observation layer normally tracks:
  `age_ms = (tick - first_seen_tick) * 50` and
  `velocity = (position - previous_position) / delta_ticks`, with a slot whose
  entity id changed treated as a new entity.

`tools/crforge/vector_observation_spike.py` is the gate: it runs one vector,
encodes the same state with the batch encoder and with the bridge's three
per-lane decoders, asserts field equality, then times both paired over
`--steps` decisions × `--repeats` trials.

`crforge_bridge/tests/test_vector_observation.py` (4 tests) pins the
equivalence, the mask view (`np.shares_memory`), the fixed tower layout, and
the age/velocity rule against the frame's own position deltas. The bridge suite
is now **21 tests**; with the V4 tensorizer suite, **93 pass**.

#### Measured (256 lanes, 16.635.8, learner deck, 200 × 3)

`runs/crforge-c3/vector-observation.json`:

| threads | vectorized facts | decisions/s | bridge decoders | speedup | equivalence |
| ---: | ---: | ---: | ---: | ---: | --- |
| 1 | **0.0317 ms/lane** | 31,555 | 0.1518 ms/lane | **4.8×** | pass |
| 8 | **0.0342 ms/lane** | 29,282 | 0.1619 ms/lane | **4.7×** | pass |

Shape at the sampled decision: 9–13 policy entities per lane, all six towers,
and a peak of ~260k legal placement cells across the 256 lanes (of 589,824
possible). The control is the bridge's full fact decode, not C1's partial
per-lane encoder, which is why the ratio is 4.8× rather than C1's 19×; the
absolute number is still **~30× below the tensorizer**.

#### What this does not do

It does not build `ObservationV1` contracts (the contract layer is exactly what
C3 wants to skip) and it does not tensorize. The end-to-end gate is unchanged:
C1's **0.713 ms/lane** projection and the C3 profile's **0.99–1.07 CPU
ms/decision** replay mean still stand. C3-B's row build is the next step, and it
now has a verified input.

Two facts from this work matter for that row build:

1. **The bridge's causal groups are singleton per entity.** `_causal_group_by_entity`
   is fed only by rich combat events, and the bridge's rich envelope carries
   none. `build_causal_groups` therefore returns one group per entity, ordered by
   the same priority tuple. The batched group build is a sort, not a graph walk;
   the general (native) path stays the oracle.
2. **The bridge ignores the vector's revealed-card columns.** `player_facts`
   exposes `cycle` but not `enemy_seen`; the observation layer builds
   `revealed_cards` from events it never receives. The batch carries
   `enemy_seen_vocab` / evolution so the gap is visible, but the first batched
   row build must reproduce the bridge's current empty set — fixing the gap is a
   fidelity change and belongs behind its own digest gate.

The tracker's contribution in this envelope is equally small and exact: with no
events, opponent card states stay `UNKNOWN_INITIAL` and `revealed_cards` stays
empty, so the only tracker term the tensorizer reads is the enemy elixir
interval (`(0, min(10, start + generated))`). A batched tracker is therefore a
one-line regeneration schedule, not a state machine — provided the row build
keeps the no-event envelope as an explicit precondition.

Evidence: `runs/crforge-c3/vector-observation.json`,
`crforge_bridge/tests/test_vector_observation.py`,
`tools/crforge/vector_observation_spike.py`.

### 3.47 The batched causal-group section — C3-B's first row build (2026-11-19)

C3 attributed **0.543 of a 0.992 CPU ms/decision** packet to `_groups` plus its
children, the largest section, so C3-B starts there. `crforge_bridge/vector_rows.py`
now builds that section for every lane of a `VectorBattle` from the session-17
facts layer in one NumPy pass, and the per-lane tensorizer stays the oracle.

#### What the build covers

`VectorGroupBuilder.build(batch)` returns the `BattleGroupSetV4` tensors with
the lane count as their batch dimension, plus slot-keyed `group_index` /
`child_index` maps and the two overflow counts. The scope is the bridge's
no-event envelope:

- **singleton groups.** The bridge sends no causal-group reference, so
  `build_causal_groups` returns one group per entity; the batched build is a
  per-lane sort by the tensorizer's priority tuple, not a graph walk;
- **the raw `entity_kind` rule.** The bridge classifies a row from the card
  spec when it resolves a card, `effect` for a cardless row, `unknown`
  otherwise. That matters: a cardless spawned skeleton is an `effect` entity,
  so its singleton group type is `persistent_effect` even though its archetype
  metadata resolves to a `character` child;
- **no ability sources, projectiles, runtime attack/movement/visibility or
  events**, exactly as crforge's minimal rich player/object envelope supplies
  them;
- **seat 0** (`VectorObservationBatch` reads `VectorBattle.observations`
  seat 0). Towers are not entities: the six-row tower section stays separate.

Static policy semantics (card id, archetype, child type, footprint/radius,
runtime form, group type, source-card vocabulary id) are compiled once per
tensorizer into `VectorEntityTables`, so a decision does no catalog walk. The
archetype id resolves from the vector row's form name (`form:<Name>`, then
`CHARACTER.`/`BUILDING.`/`AREA.`/`AOE.`/`EXT.` node ids); a form with a
character global id carries that id into the oracle entity so the tensorizer's
own `_entity_archetype_id` takes the same path.

#### The oracle and the gate

`reference_entities(batch, lane)` projects `EntityStateV1` rows from the same
facts and calls the tensorizer's `_groups` through `oracle_groups`. The oracle
therefore resolves archetype, child kind, extent, radius and runtime form with
its own helper methods; the batched tables are the only thing under test.
`compare_group_section` compares every group/child tensor, both index maps and
both overflow counts.

`tools/crforge/batched_groups.py` runs the gate on real vector lanes with the
locked learner deck:

```
.venv/bin/python tools/crforge/batched_groups.py \
    --count 256 --steps 40 --repeats 3 --timing-samples 8 --verify-lanes 32 \
    --output runs/crforge-c3/batched-groups.json
```

`runs/crforge-c3/batched-groups.json`: **equivalence pass over 40 decisions**
(256 lanes at step 0, lanes 0–31 after), `mismatches: []`. At the sampled frame
the busiest lane held **14 entities** (all retained as groups and children;
overflow 0), mean per-step max **4.85**.

Paired timing, 256 lanes, 8 alternating samples:

| path | total ms | ms/lane |
| --- | ---: | ---: |
| batched group section | **13.81** | **0.0539** |
| per-lane `_groups` oracle | 56.50 | 0.2207 |

**4.09×** the per-lane oracle on this host and this entity density. The
section's live-profile cost was 0.543 ms/decision; replacing just that share
projects a **0.503 ms/decision** packet, before any other section moves. That is
a section arithmetic, not an end-to-end claim: the facts-only path still has to
build cards, candidates, relations, towers, scalars and spatial planes, and the
oracle here is the 0.22 ms/lane group function on minimal entities, not the
0.543 ms/decision live row (median 8 entities and richer runtime).

Two ordering rules are worth pinning because they are easy to get wrong and the
equivalence test caught both:

1. **Group relation is local; child relation is global.** A singleton group's
   second priority term is "this entity has no visible target". A child's is
   "this entity is not some entity's target" — the tensorizer builds that set
   from the whole observation. The first implementation reused the owner's
   target flag and reordered troops ahead of targeted skeletons.
2. **Targets can be towers.** A skeleton attacking a princess tower has a
   visible target that is not an entity; the lane-tagged membership test simply
   misses it, which is correct.

#### Tests

`crforge_bridge/tests/test_vector_rows.py` (5 tests): a 50-decision equivalence
run, a two-group/three-child capacity run that forces overflow, an empty-board
mask/neutral-owner check, the reference-entity fact projection, and the seat-0
guard. The bridge suite is **26 tests**, all passing. Ruff clean.

#### What remains for C3-B

The row build is not wired into `tensorize` yet. The remaining sections, in
C3's measured order, are `_card_sets` (0.33), `_candidates` / placement rows
(0.30), `_relations` (0.14), then `_towers` (0.145), `_spatial_planes` (0.117),
`_scalars` and the empty effects/events. They must consume the same facts and
reproduce the bridge's empty revealed-card set until a separate fidelity gate
moves it. Only then can a facts-only `tensorize` replace the per-lane build and
the ≤0.5 ms/lane target be claimed end to end.

Evidence: `runs/crforge-c3/batched-groups.json`,
`crforge_bridge/vector_rows.py`,
`crforge_bridge/tests/test_vector_rows.py`,
`tools/crforge/batched_groups.py`.
