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

Conclusion: this needs a **semantic** readiness check rather than another raw
byte. Options for the next session, in order:

1. Build the 15.535.13 probe (`--input-apk
   .resource/native/15.535.13/nr_15.535.13_apkpure.apk --target-build
   15.535.13`), capture the same window, and see what the real ready byte is
   and which neighbours identify its field. Then map that field into 16.635.
2. Gate on a content lookup instead: call `g_logic_data_by_global_id` for a
   known-good id and require non-null (this is what `prewarm_location_tilemap`
   already does after the gate).
3. Find the engine's own content-ready function in the loading path.

The `content-window` command is bounded (offset ≤ 0x2000, length ≤ 0x400) and
read-only; it exists to support this investigation.



