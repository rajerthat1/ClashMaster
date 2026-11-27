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
