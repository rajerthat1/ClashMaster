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

### 3.48 The batched card and relation sections — C3-B continues (2026-11-19)

The next two sections in C3's measured order are `_card_sets` (0.33 CPU
ms/decision) and `_relations` (0.14). Both are now batched over a whole
`VectorBattle` from the session-17 facts layer, with the per-lane tensorizer as
the oracle, in the same shape as §3.47.

#### Card sets

`VectorCardBuilder.build(batch)` fills the actor's eight-row card set and the
opponent's nine-row set for every lane:

- the actor's deck order, roles and vocabulary come from the tensorizer's own
  `_actor_card_layout()`, so rows cannot drift from the per-lane path;
- `runtime_form` is the hand form for a card in hand, `FORM_NORMAL` otherwise.
  crforge carries no evolution runtime, so a ready evolution outside the hand
  does not exist in this envelope;
- `revealed_cards` stays empty: the bridge's rich players are the `empty_player`
  records and it receives no combat events, so the opponent set is eight zero
  rows plus the masked unknown-card row carrying
  `unknown_opponent_card_fraction = 1.0`;
- the actor's ability runtime is empty for the same reason;
- the **21-column** 16.635 schema adds the actor's in-flight command:
  `pending_execution`, the target cell and `ability_pending`. A pending row's
  point is in the **seat's frame** (`Schema.toSeatX(0, x) = width - x`), so the
  model cell is `(18000 - pending_x) // 1000 - offset` for x and
  `pending_y // 1000 - offset` for y, clamped to the 18×32 board. `offset` is the
  card's footprint parity (`NativeBuildingFootprintV1.model_subcell_offset`:
  0.5 per even dimension), because an even-sized building's command point is
  half a tile past the cell center — a 2×2 Tesla at cell (15, 12) is at world
  (16000, 13000), which a naive floor would read as (16, 13). The test pins both
  the centered (troop) and offset (Tesla) cases. `pending_ticks_remaining` is
  written and then zeroed for a checkpoint that declares
  `neutralize_pending_ticks_remaining` (B-general-v275 does), because a live
  receipt cannot supply it;
- a **waiting champion ability** sets `ability_pending` on the champion's card
  row. crforge keeps ability commands in their own header block, not in the
  pending-card rows, so `VectorObservationBatch` now carries both seats'
  `ability_champion` / `ability_unit` / `ability_state` / `ability_cooldown_ms`
  / `ability_charges` / `ability_command_ticks`; a non-zero tick count is a
  command that has been given and has not run. The oracle matches it by
  `ability_id` against the card's static ability set.

This is a real fidelity item, not just a table: the gates before this one built
`ModelConfigV4()` with its default **16-column** card runtime schema, while
B-general-v275's contract declares **21**. The new gate reads the checkpoint's
own `contract.model_config`, so the pending columns are exercised (the 40-step
run produced up to four pending rows per lane). Only
`card_runtime_feature_dim` differs between the two profiles, so the group
section measured in §3.47 is unchanged.

#### Relation edges

`VectorRelationBuilder.build(batch, cards, groups, towers)` reproduces the
bridge's edge set exactly:

- `entity -> visible target` and the reverse, when both ends have a token;
- `tower -> visible target` and the reverse — a tower can target an entity or
  another tower, and a tower's token is its **model row**
  (`VectorTowerSection.tower_index`), not its native entity id;
- `card -> group` for every retained group whose `(owner, source card)` is one
  of the actor's cards or the opponent's unknown row.

The bridge's envelope has no attack/projectile/capture/source runtime and no
causal-group references, so no other edge type can occur. Edges are
de-duplicated and sorted with one `np.unique` over a packed
`(lane, source, target, relation)` key that orders exactly like the
tensorizer's `sorted(edges)`; `VectorRelationSection.lane(n)` slices a lane back
to the variable-length `(1, count)` record, so a future digest comparison is
row-for-row, not padded.

#### The oracle and the gate

`reference_observation(batch, lane)` projects the player rows (hand, next card,
hand-slot and hand-runtime metadata), the fixed tower rows (kind, owner, HP,
target), the `PendingActionV1` rows and the reference entities from the same
facts. The stock `_card_sets` / `_towers` / `_relations` then decide the
section from those rows; `compare_card_section` and
`compare_relation_section` compare every tensor field.

One oracle bug mattered: the tensorizer caches players by `id(observation)`, and
per-lane reference views are short-lived, so a recycled id could answer with
another lane's player row. The comparison clears `_frame_players` per lane; the
production path clears it once per decision in `tensorize`.

`tools/crforge/batched_cards.py` is the gate. It reads the learner-deck
`B-general-v275` contract for the 21-column schema, steps 256 real vector lanes,
compares cards on every step and relations every fourth step, then times both
paths paired.

`runs/crforge-c3/batched-cards.json` (256 lanes × 40 decisions, 32 lanes
verified after step 0):

| section | batched ms/lane | oracle ms/lane | speedup |
| --- | ---: | ---: | ---: |
| cards | **0.00161** | 0.0718 | **44.5×** |
| relations | **0.00984** | 0.0522 | **5.30×** |

**Equivalence: pass**, `card_mismatches: []`, `relation_mismatches: []`. The
frame held up to **14 entities**, **4 pending rows** and **40 relation edges**
per lane.

Run-to-run spread on this host is real (four full runs measured cards
0.00122–0.00161 vs 0.0718–0.0835, i.e. 45–68×; relations 0.0098–0.0108 vs
0.0522–0.0612, i.e. 5.2–5.7×); quote the artifact of record and compare paired
runs, not absolute values from an hour earlier.

These are section arithmetic, not an end-to-end claim. The card oracle here is
the 0.072 ms/lane pure row build (the live profile's 0.33 includes contract
projection around it), and the relation oracle is `_relations` alone with its
token maps prebuilt. Replacing the live profile's 0.33 + 0.14 shares with
0.00161 + 0.00984 projects a further **0.46 ms/decision**, before towers,
candidates, scalars and spatial planes.

#### Tests

`crforge_bridge/tests/test_vector_rows.py` is now 10 tests: the five group tests
plus a 50-decision 21-column card equivalence (which asserts a pending play was
actually seen), a 50-decision relation equivalence, the pending-cell rule for
a centered troop and a 2×2 Tesla (which carries the half-tile footprint
offset), the waiting ability's `ability_pending` column, and the tracker guard.
`test_vector_observation.py` adds a champion-slot header projection test. The
bridge suite is **32 tests**, all passing. Ruff clean.

#### What remains for C3-B

`_candidates` / placement rows (0.30) are next: the facts layer already carries
the native placement-mask view, but a candidate row also needs the hand-slot
runtime (`effective_cost`, `form_code`, `footprint_*`, `model_subcell_offset`),
which the bridge computes in `BattleEnvV1.action_mask` and the current facts
layer does not yet expose. Then `_towers` (0.145), `_spatial_planes` (0.117),
`_scalars` and the empty effects/events, and only then a facts-only `tensorize`
returning one `UniversalSemanticBatchV4` per lane, `batch_digest`-identical to
the per-lane path.

Evidence: `runs/crforge-c3/batched-cards.json`,
`crforge_bridge/vector_rows.py`,
`crforge_bridge/tests/test_vector_rows.py`,
`tools/crforge/batched_cards.py`.

### 3.49 The batched candidate section — C3-B continues (2026-11-19)

`_candidates` is 0.30 of the 0.992 CPU ms/decision packet: one row per legal
hand slot with the card token, its runtime form and cost, the deck row it
addresses, and a 32×18 native placement grid. §3.48 listed it as blocked on
facts the layer did not yet expose (`effective_cost`, `form_code`,
`footprint_*`, `model_subcell_offset`). The audit for this section found that
almost all of it was already there, and the rest is **card-static**:

| candidate field | where it comes from |
| --- | --- |
| card id, runtime cost, runtime form | `hand_card` / `hand_cost` / `hand_form` header columns (already in the batch) |
| slot is in flight | `hand_pending`, the same per-slot flag `player_facts` exports as `pendingHandSlots` |
| placement grid | `VectorBattle.masks`, the `(lanes, seats, 4, 32, 18)` view §3.46 landed |
| footprint, subcell offset, `is_building` | static per card: `native_building_footprint` + `building_placement_profile` |
| effective elixir | `max(0, elixir - elixir_reserved) / 10_000` from the seat header |
| `source_native_hand_slot` | the slot index (crforge's hand is dense and slot-indexed) |

So the work is `VectorCandidateBuilder`: one compiled table of per-deck-card
geometry, vocabulary id and UID, then a four-slot vector pass per batch.

#### The slot rule is the environment's, not the tile block's

crforge's mask keeps a slot open while its play is still waiting to run — the
card stays in hand and a second try can be *given*. `BattleEnvV1.action_mask`
closes it instead (`pending_execution`), and the batch must reproduce the
bridge, so a slot is a candidate only when the card is in hand, its play is not
queued, its runtime cost fits this decision's effective elixir, and the native
mask opens at least one tile. The measured frame confirms the distinction: after
queueing a 4-cost Tesla with 10 elixir, the native slot still showed 222 legal
tiles while the correct candidate count dropped from 4 to 3.

#### Geometry is a fact, and the decoder depends on it

`CandidateGeometry` carries the 2×2 Tesla's half-tile `model_subcell_offset`
(0.5, 0.5) and 3×3 X-Bow's (0, 0), exactly as `card_placement_mask` derives them
from `NativeBuildingFootprintV1.model_subcell_offset`. This is the value
`BattleEnvV1` validates against `action.subcell_offset`, so a candidate row that
omitted it would decode to a command the native engine rejects.

Scope stays the bridge's envelope: **deploy rows only**. `players.abilityRuntime`
is `null` in the bridge's rich player record, so `ability_sources` is empty on
both paths and no ABILITY row exists to reproduce. Mirror is refused explicitly
— it needs the actor-private hand/placement runtime contract to name the copied
card, which the frame does not carry.

#### The oracle and the gate

`reference_action_mask(batch, lane)` projects the mask `_candidates` reads —
open slots, one entry per hand card with `row_major` as the `[32][18]` sequence
the tensorizer validates, and the exact effective elixir — and
`compare_candidate_section` runs the stock `_card_sets` / `_groups` /
`_candidates` against it. `tools/crforge/batched_candidates.py` is the gate: 256
learner-deck lanes, the checkpoint's own `model_config`, `ActionCandidatesV4`
validation on every step, and paired timing.

`runs/crforge-c3/batched-candidates.json` (256 lanes × 40 decisions, 60 WAIT
warmup decisions past the start cooldown, 32 lanes verified after step 0):

| path | total | ms/lane |
| --- | ---: | ---: |
| batched candidate section | **0.221 ms** | **0.00086** |
| per-lane `_candidates` oracle | 13.60 ms | 0.0531 |

**Equivalence: pass**, `mismatches: []` over 40 decisions. The fixture produced
up to 4 candidates per lane, 5,181 queued-play slots (the pending-rule case) and
1,171 building rows (the geometry case). **61×** the per-lane oracle (two full
runs: 0.00084–0.00086 vs 0.0524–0.0531, i.e. 61–62×); the ratio moves with how
loaded the frame is, so quote the paired run.

Section arithmetic only: replacing the live profile's 0.300 share with 0.00086
projects **0.693 ms/decision** with groups, cards and relations also batched,
before towers, spatial planes and scalars.

#### Tests

`test_vector_rows.py` adds 4 tests (14 in the file, bridge suite **36**):
a 50-decision candidate equivalence that also validates the batch record and
asserts a queued play and a building candidate were actually seen; the slot rule
against a hand-built frame (pending, no elixir, a fully promised balance, an empty
tile block); the 2×2 / 3×3 building geometry; and the Mirror refusal. `compare_lane_facts` now also
checks `hand_pending` against the bridge's `pendingHandSlots`. Ruff clean.

#### What remains for C3-B

`_towers` (0.145), then `_spatial_planes` (0.117), `_scalars` and the empty
effects/events, and only then a facts-only `tensorize` returning one
`UniversalSemanticBatchV4` per lane, `batch_digest`-identical to the per-lane
path. The towers section has two envelope facts to carry (empty `status`, and
`tower_troop_id` as a static build input).

Evidence: `runs/crforge-c3/batched-candidates.json`,
`crforge_bridge/vector_rows.py`,
`crforge_bridge/tests/test_vector_rows.py`,
`tools/crforge/batched_candidates.py`.

### 3.50 The batched tower section — C3-B continues (2026-11-19)

`_towers` is 0.145 of the 0.992 CPU ms/decision packet: six rows, sorted
actor-first and then by model kind (king, princess left, princess right) and
native id, each carrying a tile position, a kind-sized extent (half-extent 2.0
for a king, 1.5 otherwise), a health ratio, an active flag, a visible-target
flag and the two `/5000` health scales. §3.49 listed two envelope facts to
carry; the audit for this section found no new per-frame fact was needed at all:

| tower field | where it comes from |
| --- | --- |
| ownership, kind, health, maximum | `tower_owner` / `tower_kind` / `tower_hp` / `tower_max_hp` (already in the batch) |
| model row order and type code | `tower_owner != actor`, then the model kind (a horizontal flip swaps the princesses) |
| tile position | the shared entity row's `entity_x` / `entity_y`, then the tensorizer's flip/scale/clamp |
| visible target | the shared entity row's `entity_target` |
| tower troop | **static build input** — `tower_troop_id=159000000` (Tower Princess) for the learner deck |
| `king_activated` | **always zero** — the bridge sends no `tower_activate` events |

So the work is `VectorTowerBuilder`: one gather over a per-lane six-row view,
one sort, and the health/target block. `VectorTowerSection` also carries
`tower_index`, the `entity id -> model row` map the tensorizer's `_relations`
consumes as `tower_token_by_entity`; `lane(n)` slices a lane back to the exact
`TowerSetV4` + dict the per-lane path emits.

#### The model view flips the princess kinds, not just the coordinates

`_model_tower_kind` swaps `princess_left` and `princess_right` under
`flip_x`, and `_towers` sorts by that model kind. The batched build therefore
maps native kind code `[0, 1, 2]` to model code `[0, 2, 1]` when the perspective
flips, and uses the same map for the emitted `tower_type` column. A build that
only mirrored the coordinates would put the princess rows in the wrong order and
mislabel their types — a mistake the mirror test pins.

#### A destroyed tower is a zero-HP row; an absent one is padding

The vector scene keeps a destroyed tower's row with zero HP and its original
maximum, so the row stays present and `hitpoints_ratio` reads 0. A row that
leaves the scene drops out of `_tower_slots`, and the section pads it with
`tower_type 0`, `tower_troop_type 0`, `owner_type 2` (neutral), zero vectors and
`mask False`, exactly as the oracle pads. "Destroyed" and "absent" are therefore
different states, and the test asserts both.

#### The oracle and the gate

`compare_tower_section` projects the per-lane observation from the same facts and
runs the stock `_towers`, comparing all seven tensors and the row index for every
native tower id. `tools/crforge/batched_towers.py` is the gate: 256 learner-deck
lanes, `TowerSetV4` validation every step, paired timing, and the two pins
recorded on the artifact.

`runs/crforge-c3/batched-towers.json` (256 lanes × 40 decisions, 60 WAIT warmup
decisions, 32 lanes verified after step 0):

| path | total | ms/lane |
| --- | ---: | ---: |
| batched tower section | **0.267 ms** | **0.00104** |
| per-lane `_towers` oracle | 8.92 ms | 0.0349 |

**Equivalence: pass**, `mismatches: []` over 40 decisions, all six towers present
on every lane, 61,440 active tower rows and 61,263 rows with a visible target
exercised. **33.5×** the per-lane oracle (three full runs: 0.00104–0.00113 vs
0.0349–0.0372, i.e. 32–33.5×); quote the paired artifact rather than the ratio alone.

Section arithmetic only: replacing the live profile's 0.145 share with 0.00113
projects **0.848 ms/decision** with groups, cards, relations and candidates also
batched, before spatial planes and scalars.

#### Tests

`test_vector_rows.py` adds 4 tests (18 in the file, bridge suite **40**): a
50-decision equivalence over four lanes; the model-view flip (row order, type
codes and `tower_index` under `horizontal_mirror=True`); the static tower-troop
pin (pinned, absent, a non-competitive id, a non-zero seat); and the
destroyed/absent row rules against the oracle. `_tower_slots` is now vectorized
(the scan that fed both the tower and relation builds was a per-lane Python
loop). Ruff clean.

#### What remains for C3-B

`_spatial_planes` (0.117), `_scalars` and the empty effects/events, and only
then a facts-only `tensorize` returning one `UniversalSemanticBatchV4` per lane,
`batch_digest`-identical to the per-lane path. `_spatial_planes` is the larger
one: it needs airborne, spatial extent and threat facts the batch does not carry
yet, and `_scalars` needs the match clock/phase/elixir-multiplier reconstruction
that `BattleEnvV1.observe` derives from the tick and section.

Evidence: `runs/crforge-c3/batched-towers.json`,
`crforge_bridge/vector_rows.py`,
`crforge_bridge/tests/test_vector_rows.py`,
`tools/crforge/batched_towers.py`.

### 3.51 The batched explicit spatial planes — C3-B continues (2026-11-19)

`_spatial_planes` is 0.117 of the 0.992 CPU ms/decision packet: a 14-channel
board per decision. Per side (own base 0, enemy base 7) it carries four
occupancy categories — ground, airborne, building, projectile — plus a hitpoint
channel (`/10 000`), a shield channel and a threat channel (`/5 000`). Every
entity marks its category at its cell; a building fills the whole footprint;
hitpoints, shield and threat always go to the entity's own cell.

The audit found the facts were already there or archetype-static:

| plane input | source |
| --- | --- |
| position, side, hitpoints, shield | the entity row (already in the batch) |
| category (child kind, airborne) | archetype metadata, compiled into `VectorEntityTables` |
| threat | archetype `static_damage_basis`, compiled into the tables |
| building footprint | `native_building_footprint` half extents, already in the tables |
| persistent area root | `child_kind in {area, effect}`; the envelope carries no causal groups |

So the work is `VectorSpatialPlaneBuilder`: flatten the kept entities once, then
one gather for the category/health planes and one scatter per footprint cell.

#### Two rules decide membership, and one is easy to miss

Towers are a separate six-row section and are never drawn into the planes —
a board of towers alone is an all-zero plane tensor. An **ownerless** row is
skipped too, exactly as the per-lane loop skips `owner is None`; the batched
build filters `side in {0, 1}` before anything else. A neutral row and a removed
row must produce bit-identical planes.

#### Float32 accumulation is part of the contract

Two entities can share a cell. The per-lane loop adds each entity's value to a
float32 plane one at a time, so the result is a sequence of float32 roundings,
not the correctly rounded sum. `np.add.at` with float64 operands accumulates in
float64 and narrows once — a one-ULP difference that the digest comparison
caught (threat 81/5000 + 112/5000: `0.038599998` vs `0.0386`). Casting the addends
to float32 before the scatter reproduces the loop exactly. Category values are
`1.0`, so footprint/point order is free.

#### Flatten first: 2.4× becomes 12.7×

The first version masked over the whole `(lanes, max_entities)` grid (~15k
cells) for every expression and cost **0.0188 ms/lane (2.44×)**. Flattening once
to the kept entities (a few hundred) and working in one dimension cut it to
**0.00392 ms/lane (12.7×)** — the perf cProfile put 75 % of the time in
`build`'s own boolean indexing, not in `np.add.at`.

`compare_spatial_plane_section` clears the six per-frame caches per lane, exactly
as `tensorize` clears them per decision (they are keyed by entity id and vector
lanes reuse ids), then runs the stock `_spatial_planes`.

#### The gate

`tools/crforge/batched_spatial_planes.py` — 256 learner-deck lanes, full-plane
equivalence every step, paired timing. `runs/crforge-c3/batched-spatial-planes.json`
(256 lanes × 40 decisions, 60 WAIT warmup decisions, 32 lanes verified after step
0):

| path | total | ms/lane |
| --- | ---: | ---: |
| batched explicit planes | **1.004 ms** | **0.00392** |
| per-lane `_spatial_planes` oracle | 12.78 ms | 0.0499 |

**Equivalence: pass**, `mismatches: []` over 40 decisions; 35 frames carried a
building, 28,224 ground cells and 24,866 building cells exercised. **12.7×** the
per-lane oracle. Section arithmetic: replacing the live 0.117 share projects
**0.879 ms/decision** with groups, cards, relations, candidates and towers also
batched, before `_scalars` and the empty effects/events.

#### Tests

`test_vector_rows.py` adds 3 tests (21 in the file, bridge suite **43**): a
50-decision plane equivalence with a **forced Tesla** (the random policy alone
never fielded a building, so the footprint fill is pinned deterministically);
the archetype projection behind airborne/threat/persistent-root; and the
neutral-row/removed-row equivalence plus the towers-only zero board. Ruff clean.

#### What remains for C3-B

`_scalars` (the clock, phase, elixir multiplier, crowns, counts and the tracker's
`[0, 10]` fallback) and the empty effects/events, then a facts-only `tensorize`
returning one `UniversalSemanticBatchV4` per lane, `batch_digest`-identical to
the per-lane path on a captured sequence.

Evidence: `runs/crforge-c3/batched-spatial-planes.json`,
`crforge_bridge/vector_rows.py`,
`crforge_bridge/tests/test_vector_rows.py`,
`tools/crforge/batched_spatial_planes.py`.

### 3.52 The batched match scalars — C3-B's last named section (2026-11-19)

`_scalars` is the smallest named section of the packet: **0.0140 CPU
ms/decision** in the shipped profile (19 named sections; `tensorize` 1.875
cumulative). It writes 18 match-wide numbers per decision — clock, phase, elixir
multiplier, both elixirs, the reserved balance, both crown counts, the visible
entity count, five overflow ratios and the static actor identity.

Every per-frame input was already in the batch except the mode clock:

| scalar input | source |
| --- | --- |
| remaining clock | `max(0, NATIVE_GAMEPLAY_END_TICK - canonical_tick) * 50` |
| phase + elixir multiplier | the mode timeline at the **native** tick (a static asset, compiled once) |
| own elixir (`elixir_exact`) | `elixir_raw / 10 000` from the seat header |
| reserved balance | `elixir_reserved_raw / 10 000` |
| crowns | the seat's `crowns` / `crowns_enemy` header columns |
| visible entities | `entity_valid & ~entity_is_tower` count |
| group/child overflow | the group section |
| candidate count | the candidate section |
| producer overflow | the vector's `entity_overflow` header |
| enemy elixir bound | no tracker → `(upper 1.0, uncertainty 1.0)` |
| events | none in the envelope → both columns zero |

So the work is `VectorScalarBuilder`, plus two view extensions: `_PlayerView`
now carries `crowns` / `elixir_exact` and `_ObservationView` carries
`time` / `phase` / `metadata`, which the per-lane oracle reads.

#### Two clocks, one timeline

`BattleEnvV1.observe` takes the **phase and multiplier from the native tick** and
the **remaining clock from the canonical tick** (`native - episode_start`). The
projection keeps that split: `batch.tick` is canonical and the builder takes a
`native_tick_offset` (the encoder's first raw tick; 0 in the vector gate and for
a fresh match). `BattleTimelineV1.phase` is a per-tick scan, so the builder
compiles the whole 6,000-tick schedule into three arrays once: normal → double
(2,400) → overtime (3,600) → 3× (4,800) → tiebreak (6,000).

#### The gate has to run the whole oracle chain

`_scalars` *takes* the group/child overflow and the candidate count as
arguments, so a comparison that fed it the batched counts would check nothing.
`compare_scalar_section` runs the stock `_card_sets` → `_groups` → `_candidates`
chain per lane and passes the oracle's own counts, exactly as the candidate gate
does.

`tools/crforge/batched_scalars.py` — 256 learner-deck lanes, full scalar
equivalence every step, paired timing.
`runs/crforge-c3/batched-scalars.json` (256 lanes × 40 decisions, 60 WAIT warmup
decisions, 32 lanes verified after step 0):

| path | total | ms/lane |
| --- | ---: | ---: |
| batched match scalars | **0.0733 ms** | **0.000286** |
| per-lane `_scalars` oracle | 0.970 ms | 0.00379 |

**Equivalence: pass**, `mismatches: []` over 40 decisions; own elixir reached
10.0, up to 4 candidates and 14 visible entities per lane. **13.2×** the
per-lane oracle. Section arithmetic: replacing the live 0.0140 share projects
**0.978 ms/decision** with all seven sections batched.

A 40-decision window stays in the normal phase, so the gate records
`phases_seen: ["normal"]` honestly; the overtime/tiebreak/double-elixir columns
are pinned by the unit test's synthetic ticks (0, 2,400, 3,600, 4,800, 6,000)
on both the batched and oracle paths.

#### Tests

`test_vector_rows.py` adds 3 tests (24 in the file, bridge suite **46**): a
50-decision scalar equivalence that also asserts elixir and candidates were
generated; the standard timeline schedule at five canonical ticks; and the
tracker refusal. Ruff clean.

#### What remains for C3-B

The empty effects/events section, then a facts-only `tensorize` returning one
`UniversalSemanticBatchV4` per lane, `batch_digest`-identical to the per-lane
path on a captured sequence — only then can the ≤0.5 ms/lane end-to-end claim be
made.

Evidence: `runs/crforge-c3/batched-scalars.json`,
`crforge_bridge/vector_rows.py`,
`crforge_bridge/tests/test_vector_rows.py`,
`tools/crforge/batched_scalars.py`.

### 3.53 The empty effects/events records — C3-B's row build is complete (2026-11-19)

The bridge's no-event envelope carries no active effects and no combat events.
Both sections still exist in every packet, and in the shipped profile they are
**0.0983 CPU ms/decision** (`_active_effects` 0.0177 + `_events` 0.0806). The
per-lane result is fully determined and has no content: effects are a zero-row
`ActiveEffectSetV4`, events are the fixed 32-row record with every row masked
off.

`VectorEmptySectionBuilder` widens those stock records to the lane count in one
allocation. The only detail is the padding the tensorizer validates:

| events column | padding value |
| --- | --- |
| `event_type`, `source_card_vocab_id`, `source_form`, `features` | 0 |
| `owner_type` | **2** (neutral) |
| `source_group_index`, `target_token_index` | **-1** |
| `mask` | False |

Effects have zero rows but keep the parent-index `-1` sentinel. The builder also
returns the zero `event_count` / `event_overflow` the section carries.

This meant two small view fixes for the oracle path: `_TowerView` now exposes an
empty `effect_states` and `_ObservationView` an empty `events` sequence, which
`_active_effects` / `_events` iterate.

#### The gate

`tools/crforge/batched_empty_sections.py` — 256 learner-deck lanes, both records
validated (`ActiveEffectSetV4.validate`, `EventSetV4.validate`) and compared field
for field every step, paired timing.

`runs/crforge-c3/batched-empty-sections.json` (256 lanes × 40 decisions):

| path | total | ms/lane |
| --- | ---: | ---: |
| batched empty records | **0.0362 ms** | **0.000141** |
| per-lane `_active_effects` + `_events` oracle | 5.17 ms | 0.0202 |

**Equivalence: pass**, `mismatches: []` over 40 decisions; zero effect rows and
zero event rows, 32 padded event rows per lane. **143×** the per-lane oracle.
Section arithmetic: replacing the live 0.0983 share projects **0.894
ms/decision**.

#### Tests

`test_vector_rows.py` adds 2 tests (26 in the file, bridge suite **48**): the
20-decision equivalence with record validation, and the non-zero-seat refusal.
Ruff clean.

#### Where C3-B stands

Every named row section now has a batched build and a gate against the per-lane
tensorizer: groups, cards, relations, candidates, towers, spatial planes, scalars
and the empty effects/events. What remains is **the wiring**: a facts-only
`tensorize` that assembles one `UniversalSemanticBatchV4` per lane and proves
`batch_digest` identity against the per-lane path on a captured sequence. Only
that measurement can support an end-to-end ≤0.5 ms/lane claim.

Evidence: `runs/crforge-c3/batched-empty-sections.json`,
`crforge_bridge/vector_rows.py`,
`crforge_bridge/tests/test_vector_rows.py`,
`tools/crforge/batched_empty_sections.py`.

### 3.54 The facts-only tensorize — C3-B closed (2026-11-19)

The row sections were done; what was missing was the packet assembly. A
per-lane port of the stock tensorizer buys ~1.4×, and the session-16 record said
exactly why: `UniversalObservationTensorizerV4.tensorize` is ~1.0 CPU
ms/decision of contract construction, frozen-mapping walks and 3,754 Python
calls, not array math. `crforge_bridge/vector_tensorize.py` replaces that entry
point with one call over the facts layer:

* `VectorTensorizer` compiles the entity tables and every builder once, then
  `tensorize(batch)` calls cards → towers → groups → effects/events →
  candidates → relations → spatial planes → scalars, in the stock order, and
  returns one `UniversalSemanticBatchV4` whose batch dimension is the lane
  count. The sections already carry the lane axis; the assembly is a reference
  join. Relations keep one common padded width and mask; everything else is
  fixed V4 capacity.
* The previous-action record is **batched**: `record_action(sequence, batch)`
  takes one `ActionSequenceV4` with lanes as rows, resolves each UID against
  that lane's candidate rows and replaces the record wholesale, which is the
  stock snapshot semantics. A gate that never played would leave
  `previous_action` empty on both sides, so the gate records a real sampled
  deploy action every step.
* `lane_batch(batch, lane)` is the digest projection: fixed sections sliced to
  one row, relations trimmed to that lane's live edge count. The training path
  consumes the stacked record; the slice exists only to compare against the
  per-lane oracle.
* `reference_observation` / `reference_scalar_observation` now carry
  `episode_id` / `owner` / `tier` and pass `tower_troop_id`, so the stock
  `tensorize` entry point — not just its section methods — can be driven from
  the same facts. The section oracles are unchanged.

#### The gate

`tools/crforge/batched_tensorize.py` steps 256 learner-deck lanes through 60
WAIT warmup decisions and 40 sampled-play decisions. Step 0 checks all 256
lanes; later steps check 32. For each checked lane it drives a per-lane stock
tensorizer with `reference_scalar_observation` (same facts, same action mask,
same tower troop), compares the whole record with `compare_semantic_batches`
(values, shapes **and dtypes**) plus `batch_digest`, and records the same
sampled micro-action on both paths.

`runs/crforge-c3/batched-tensorize.json` (256 lanes × 40 decisions):

| path | total | ms/lane |
| --- | ---: | ---: |
| stacked facts-only `tensorize` | **20.65 ms** | **0.0807** |
| per-lane stock `tensorize` (prebuilt references) | 159.95 ms | 0.6248 |

**Equivalence: pass, 1,504/1,504 lane-decisions** (`256 + 39 × 32`), all
`batch_digest`-identical, no field mismatches. The stacked record validated with
the stock `UniversalSemanticBatchV4.validate` at step 0. Busiest lane: 14 active
groups, 42 relation edges, 4 candidates. Speedup on the tensorizer half:
**7.75×**.

Timing the other two halves on the same final frame:

| piece | ms/lane |
| --- | ---: |
| vectorized facts (`VectorObservationEncoder.encode`) | 0.0315 |
| stacked tensorize | 0.0807 |
| batched `record_action` | 0.0060 |
| **end to end** | **0.1182** |

That is ~8,460 decisions/s per process against the **0.5 ms/lane** gate. Two
shapes, one number: this is the packet *with the facts layer and recording
included*, so the C1/C3 end-to-end ≤0.5 ms/lane claim is now supported by
equivalence, not by section arithmetic.

A second seed (`--seed 2`) re-ran the same shape: **1,504/1,504** digest
matches, 0.0803 ms/lane tensorize, **8.0×**, **0.117 ms/lane** end to end, so
the result is not one lucky play sequence.

One implementation detail worth keeping: `record_action` is a per-lane Python
loop over sampled micro-actions, but it measured 0.006 ms/lane at 256 lanes
(~1.5 ms total), so it is not the lever; vectorizing it would be tidying, not a
throughput change.

#### Scope and tests

Scope is unchanged — seat 0 and the bridge's no-event envelope: singleton
groups, empty `revealed_cards`, no effects/events, no ability runtime, empty
tower `status`, static tower troop. Widening any of it is a separate fidelity
gate.

`crforge_bridge/tests/test_vector_tensorize.py` adds 4 tests: a 10-decision
played-sequence digest equivalence (recorded actions included), relation-only
trimming at fixed V4 capacity, per-lane previous-action resolution, and a
broken-lane comparison guard. Bridge suite **52 tests**, all passing; ruff clean
on the new files.

#### Where C3-B lands, and what C4 inherits

C3-B is closed. The remaining scope item before the C4 self-play loop is
**seat 1**: the facts layer and every builder read seat 0, while self-play
tensorizes both owners of each lane. That is a parameter-through-builders
change, gated by the same digest tool. C4 then replaces the producer's
per-lane `tensorize`/`record_action` pair with the stacked call.

Evidence: `runs/crforge-c3/batched-tensorize.json`,
`crforge_bridge/vector_tensorize.py`,
`crforge_bridge/tests/test_vector_tensorize.py`,
`tools/crforge/batched_tensorize.py`.

### 3.55 The game-frame fix and the two-seat digest gate (2026-11-19)

The native model consumes **game** coordinates. The crforge bridge and the
vector facts layer were feeding the **seat frame**, so every crforge board was
horizontally mirrored relative to what the model sees on the native path. Seat 1
exposed it, but the defect applied to seat 0 too.

Measured with the x86_64 16.635.1 engine and a one-lane `VectorBattle`:

| path | play | observed unit |
| --- | --- | --- |
| native (`crx_engine_host.py`, `CR_NATIVE_BUILD_ABI=x86_64`) | Skeletons at game (6500, 10500) | **(6499, 11079)**, (5997, 10212), (6998, 10212) |
| crforge before the fix | same play via its seat mask | x ≈ 11002–12003 (the seat frame) |
| crforge after the fix | same play | **the same three game points** |

The engine's internal action/mask/pending space stays seat-framed. For seat 0
the mask cell `(11, 10)` addresses the game point `(6500, 10500)`. The facts
layer now converts each seat's pending point once and exposes it in game
coordinates (`pending_x = 6499`), which is what the model reads.

#### Where the conversion lives

- `crforge_bridge/vector_observation.py` writes game coordinates for entity
  `x`/`targetX` (seat-1 rows convert `y`/`targetY`), derives velocity from the
  converted points, converts each seat's pending point once, and
  `placement_rows(seat)` returns the seat's mask flipped into the game frame
  (still a view).
- `crforge_bridge/observe.py` is the bridge's one conversion point:
  `entity_rows(..., seat=...)`; `engine.py` `_bind_holder_identity` consumes
  those rows directly instead of un-mirroring again.
- `crforge_bridge/vector_rows.py` builders are actor-seat aware. Card pending
  cells apply the owner-signed subcell offset and the actor perspective;
  candidate placement applies `flip_x`/`flip_y`; scalars, crowns, elixir and
  overflow read the actor seat; towers sort actor-first and swap the model
  princess kinds under a flip.
- `reference_observation(..., seat=...)` and
  `reference_action_mask(..., seat=..., model_frame=False)` drive the stock
  actor-1 tensorizer from the same facts. `model_frame=False` is deliberate:
  `tensorize` applies the actor perspective itself.

#### The seat-1 gate

`tools/crforge/batched_tensorize.py --seats 0 1` builds one `VectorTensorizer`
and one stock per-lane tensorizer per actor, checks each seat's whole record
against its own stock actor tensorizer, and reports combined timing per
lane-decision (one lane = two actor decisions).

`runs/crforge-c3/batched-tensorize.json` (256 lanes × 40 decisions):
**3,008/3,008 lane-decisions digest-identical** — **1,504/1,504 per seat** —
with no field mismatches, 1,101 decisions carrying candidates and 5,196 sampled
micro-actions recorded on both paths.

| measurement | combined | seat 0 | seat 1 |
| --- | ---: | ---: | ---: |
| stacked tensorize, ms/lane-decision | **0.0811** | 0.0823 | 0.1180 |
| per-lane stock oracle, ms/lane-decision | 0.605 | 0.600 | 0.684 |
| speedup | **7.46×** | 7.29× | 5.79× |
| batched `record_action`, ms/lane-decision | 0.0058 | 0.0057 | 0.0070 |
| facts layer, ms/lane | 0.0320 | shared | shared |
| **end to end, ms/lane-decision** | **0.1029** | | |

That is **~9,719 decisions/s per process** with both owners tensorized, under
the 0.5 ms/lane gate. Seat 1 was slower in this run (0.118 vs 0.082); the same
machine's oracle was also slower (0.684 vs 0.600), and a second seed put both
seats at ~0.079, so quote paired runs rather than a fixed seat ratio.

`runs/crforge-c3/batched-tensorize-seed2.json` re-ran the same shape:
**3,008/3,008**, 0.0787 ms/lane-decision stacked, **8.50×**, **0.0992 ms/lane
end to end**, both seats at ~0.079. The result is not one play sequence.

#### Two seat-1 defects the gate found

1. **Relation tower tokens were native ids, not model rows.** The stock
   `_relations` receives `tower_token_by_entity` from `_towers`, whose token is
   the tower's **model row**. Under seat 1 (and any mirror) that row differs
   from the native id, so every tower edge was rewritten. `VectorRelationBuilder`
   now takes the `VectorTowerSection` and resolves both tower sources and tower
   targets through `tower_index`. Seat 0 happened to have an identity map, which
   is why the earlier section gate never saw it.
2. **Group mean velocity kept a negative zero.** The stock accumulator starts at
   `0.0` and adds each member's model velocity, so `0.0 + -0.0` is `+0.0`. The
   batched build divided the entity's `-0.0` directly. `torch.equal` agrees
   (values are numerically equal) but the digest hashes bytes, so the gate
   reported a digest-only mismatch at the first flipped frame with a
   stationary unit. The batched build now adds `0.0` before the scale divide,
   reproducing the accumulator's signed-zero rule.

#### Native cross-check

`tools/crforge/frame_crosscheck.py` plays the same card at the same game point
in the x86_64 engine and in a one-lane `VectorBattle`, advances one tick at a
time, and compares the units on their **first spawned frame** (later frames
differ because the units move). `runs/crforge-c3/frame-crosscheck.json`:
**status pass**, native and crforge both at
`(5997, 10212)`, `(6499, 11079)`, `(6998, 10212)`; the facts-layer pending is
`(6499, 10500)`, i.e. game-framed, while the play was addressed with seat cell
`(11, 10)`. The tool records `unavailable` when no engine is listening unless
`--require-native` is passed.

#### Tests

`test_vector_tensorize.py` parameterizes the played-sequence digest over seats
0 and 1. `test_vector_rows.py` adds the actor-1 relation/tower gate, the tower
token map test, and the signed-zero velocity test; the section comparators now
accept `seat`. The bridge suite is **55 tests**, all passing; ruff clean.

#### What C4 inherits

The producer still builds a per-lane pair, but it may now replace that with one
stacked `tensorize` per decision and one stacked `record_action`, for both
owners, without a frame or token translation. The next scope is C4's batched
self-play loop, not another observation contract.

Evidence: `runs/crforge-c3/batched-tensorize.json`,
`runs/crforge-c3/batched-tensorize-seed2.json`,
`runs/crforge-c3/frame-crosscheck.json`, `crforge_bridge/vector_tensorize.py`,
`crforge_bridge/vector_rows.py`, `tools/crforge/batched_tensorize.py`,
`tools/crforge/frame_crosscheck.py`.

### 3.56 The batched action decode — C4's missing half (2026-11-19)

C3-B stacks the observation into one `UniversalSemanticBatchV4` per decision, but
the model's sampled `ActionSequenceV4` still had no path back into a
`VectorBattle`. The stock `decode_action_sequence_v4` is a **per-lane** function
over `ObservationV1` contracts, which is exactly the layer the bulk path
removed. `crforge_bridge/vector_actions.py` is the batched replacement: one NumPy
pass turns a stacked sequence into the flat action index the engine steps.

#### Three frames, one conversion

The decode is a coordinate problem before it is an action problem:

* the **model** frame is the tensorizer's output — seat 0 reads the game frame
  unchanged, seat 1 reads it rotated 180° (`PerspectiveTransformV1.flip_x` and
  `flip_y` are both true for owner 1);
* the **game** frame is the facts layer's output and the native probe's frame
  (§3.55);
* the **engine seat** frame is the vector action index: crforge mirrors x for
  seat 0 and y for seat 1 (`Schema.toSeatX/toSeatY`).

Composing the two legacy conversions gives one x flip for **both** seats under
the default perspective: `model -> game` follows the tensorizer, `game ->
engine` follows `Schema`. The decoder derives both flips from the tensorizer's
own `perspective`, so a non-default `horizontal_mirror` still decodes. The
engine index is `0` for wait and
`1 + slot * rows * cols + row * cols + col` for a play, the formula
`Schema.play` uses; the game cell `(6, 10)` of the native cross-check decodes to
seat cell `(11, 10)` for seat 0 and `(6, 21)` for seat 1, which the unit test
pins.

#### What it decodes

* **Every micro-action.** Slot `k` carries its own engine index and the execute
  offset the stock decoder puts in `metadata` (`1 + delay_offset_ms // 50`
  ticks), so a producer can either apply the first play per decision or step
  one tick at a time and honour the second one.
* **Deploy candidates only.** The bridge envelope emits no ability source and no
  Mirror runtime, so an ABILITY/Mirror candidate and a non-GRID target raise
  instead of guessing. That is the same envelope C3-B pins, not a new gap.
* **The engine mask is authoritative, and the decode reports it.** `masked`
  says whether the chosen index is open in `VectorBattle.masks`; a closed index
  is returned as-is and flagged, because silently repairing a policy's action
  would hide the producer bug.

Neither subcell offsets nor the second play's timing exist in the vector action
index; both are bridge-side concepts. That is a **fidelity pin for C4**, not a
decode defect: the engine places an even-footprint building at its own anchor,
and a second play in the same decision needs either a one-tick step loop or a
rule that closes the delay until the next decision.

#### Gate

`tools/crforge/vector_decode.py` steps 256 real learner-deck lanes (WAIT past
the start cooldown, then sampled play), builds a sampled sequence per seat — one
micro-action for most lanes, two for lanes with a second open candidate, the
second at the 100 ms bin — and compares every decoded index, offset and
candidate row against the stock per-lane decoder driven by the same facts. It
then **steps the decoded indices into the engine**, so a refusal or an illegal
count fails the run.

`runs/crforge-c4/vector-decode.json` (256 lanes × 40 decisions, `--seats 0 1`):

| measurement | combined | seat 0 | seat 1 |
| --- | ---: | ---: | ---: |
| lane-decisions identical | **3,008/3,008** | 1,504/1,504 | 1,504/1,504 |
| decoded deploy rows | 1,417 | 710 | 707 |
| two-micro-action rows | 629 | 317 | 312 |
| …of which the stock oracle accepted both | 567 | 281 | 286 |
| mask-closed indices | **0** | 0 | 0 |
| engine illegal / refusals | **0 / 0** | | |
| batched decode, ms/lane-decision | **0.000232** | 0.000213 | 0.000222 |
| per-lane stock decode, ms/lane-decision | 0.758 | | |
| speedup on the decode half | **3,266×** | | |

The 62 oracle refusals are `illegal under V4 shadow state`, i.e. the live
shadow contract rejecting a sampled two-play pair; they are counted, not
compared, and they are not decode failures. Every lane the oracle did accept
matched on index **and** offset, and every applied index was open in the engine
mask.

The absolute number matters more than the ratio: at 0.23 µs/lane-decision the
decode half is no longer a packet cost. It joins facts (0.032 ms/lane),
tensorize (0.081 ms/lane-decision) and `record_action` (0.006) in the stacked
path, which is what lets C4 spend its budget on the forward.

#### Tests

`crforge_bridge/tests/test_vector_actions.py` (7 tests) pins the seat frame,
compares both seats against the stock decoder over a played sequence, decodes a
two-micro-action row with its delay offset, decodes WAIT rows, rejects an
unknown UID, and proves a closed engine mask is reported rather than repaired.
The bridge suite is **62 tests**, all passing; ruff clean.

Evidence: `runs/crforge-c4/vector-decode.json`,
`crforge_bridge/vector_actions.py`, `crforge_bridge/tests/test_vector_actions.py`,
`tools/crforge/vector_decode.py`.

### 3.57 The C4 producer loop — batched self-play over the vector engine (2026-11-19)

C3-B stacks the facts and §3.56 decodes a stacked action row, but nothing yet
closed the loop: there was no crforge self-play producer at all. Session 25
established that `producer.py` and the PPO worker are native `ObservationV1`
lanes, so this is a new file, not a port. `tools/crforge/self_play.py` plays one
`VectorBattle` of `--count` matches in lockstep: per policy decision it encodes
the facts for every lane, tensorizes both actor seats, stacks the `count * 2`
rows into one batch, runs **one** recurrent forward, decodes both seats'
sampled actions back into engine indices, and steps the vector.

#### Shape

| element | value |
| --- | --- |
| engine cadence | `LadderConfig(decision_ticks=1)`, five `vector.step()` per policy decision |
| policy cadence | the checkpoint's own `decision_ticks` (5 for `B-general-v275`) |
| actor rows | `count * 2` in `lane * 2 + seat` order, one `RecurrentPolicyStateV4` |
| `episode_start` | per row, from the vector's own `reset` info flag |
| previous action | `record_action` every decision, `reset_rows` on exactly the autoreset lanes |
| warmup | 90 ticks (`FIRST_PLAYABLE_TICK`), no actions |
| candidates | deploy only (the §3.56 envelope) |
| rollout storage | none — this is the collection/throughput probe |

#### The second micro-action keeps its own tick

The vector action index carries **one action per seat per step**, so a five-tick
engine step would execute the first micro-action and silently discard the
second. The producer steps one tick at a time and `plan_window_ticks`
(`crforge_bridge/vector_actions.py`) places each micro-action: its execute offset
is `1 + delay_offset_ms // 50`, so the action is given at `offset - 1` ticks
inside the window (bins 0–4 map onto ticks 0–4 of the five-tick window). Two
actions that would share a tick push the later one one tick forward; an action
that would leave the window is dropped. Both events are counted per segment and
in the totals, so a timing difference can never be silently absorbed. The
producer also refuses a checkpoint whose window is shorter than
`1 + max(delay_offset_ms) // 50` ticks.

#### Two defects found by running it

* **The warmup loop never exited.** `int(batch.tick[:, 0].min(initial=0))` looks
  like "minimum, or zero when empty", but `initial` seeds the reduction: with
  non-negative ticks it returns `0` every time. The producer stepped whole
  matches (the counter passed 346,000 iterations, ~57 matches) instead of 90
  ticks, which is what the first "hang" was. Plain `min()` fixes it.
* **The previous-action record was an inference tensor.** `record_action` copied
  the sampled `gate`/`micro_action_count` with `.clone()` inside the collection's
  `inference_mode`, so the record inherited that mode and `reset_rows` — and any
  PPO write — raised *inplace update to inference tensor outside InferenceMode*.
  `VectorTensorizer._empty_previous_action` and those two copies now build under
  `torch.inference_mode(False)`: the record is owned training data, not model
  output. A test samples under `inference_mode` and then resets rows outside it.

The autoreset plumbing that the loop needs also landed here: the encoder tracks
its tick offset **per lane** (a self-play vector restarts one lane at a time, so
a single barrier made every other lane jump backwards) and clears that lane's
derived `age_ms`/velocity, and `VectorTensorizer.reset_rows(mask)` clears only the
lanes that started a new episode.

#### Gate

`tools/crforge/self_play.py --count 8 --segments 10 --decisions-per-segment 64`
(both seats, validated every decision), `runs/crforge-c4/self-play.json`:

| measurement | value |
| --- | ---: |
| decisions / lane-decisions | 640 / **10,240** |
| ACT lane-decisions / eligible | **429 / 9,877 (4.34 %)** |
| illegal / faults / rejected | **0 / 0 / 0** |
| lane-decisions per second | **39.0** |
| env steps per second (lane-ticks) | **97.6** |
| wall (10 segments) | 262.4 s |
| facts + reset rows, ms/decision | 1.73 |
| stacked tensorize (both seats), ms/decision | 13.41 |
| **recurrent forward, ms/decision** | **359.7** |
| both decodes + `record_action`, ms/decision | 3.19 |
| five engine steps, ms/decision | 6.36 |

Per actor row the forward costs ~22.5 ms, so it is **94 % of the timed packet**
and 88 % of the 410 ms wall per decision: facts, tensorize, decode and stepping
together are ~25 ms. A first run of the same shape measured 43.4
lane-decisions/s (235.8 s), so the run-to-run spread is ~10 % on this host. That is
the C4 conclusion the loop was built to produce — the bridge half is no longer
the cost, and further CPU tuning cannot help. The comparison that matters is the
native PPO worker's **29 lane-decisions/s** (1 engine × 8 lanes, §3.37) against
**39.0** here on the same 17M model and the same CPU.

A second run (`runs/crforge-c4/self-play-autoreset.json`, 4 lanes, 2 × 60
decisions, `--max-ticks 600`) exercises the reset path: **4 autoresets, 29/790
eligible lanes acted (3.67 %), 0 illegal, 0 faults**. The gate-shape run starts
at tick 90 and covers 3,200 ticks, so it sees no terminal; the short-episode run
is the one that proves an autoreset mid-window does not send the old episode's
actions into the new one.

The sampled policy produced **no two-micro-action decisions** in either run, so
the collision/drop counters are 0: the rule is gated by unit tests, not by the
sampled policy. That is recorded here rather than presented as exercised.

#### Fidelity pins (unchanged, and now on the artifact)

* deploy candidates only — no ability source, no Mirror runtime;
* one extra `vector.step()` when a terminal lands on the window's last tick, so
  the next decision reads a live episode (shifts the cadence by one tick; the
  terminal case is the autoreset run, not the gate shape);
* no rollout storage, returns or PPO update yet — the artifact says so;
* CPU device, `seed 1`, checkpoint `B-general-v275`
  (`fd756235…f781c`), tables 16.635.8, release 16.635.1.

#### Tests

`plan_window_ticks` is gated in `test_vector_actions.py` (delay bins, a
collision shift, a full-window drop, bin order, the window/offset guards) with
the rest of the decode, and the per-lane clock, `reset_rows` and the
inference-mode record each have a test in their own file. Bridge suite **66
tests**, all passing; the touched files are ruff clean.

Evidence: `runs/crforge-c4/self-play.json`,
`runs/crforge-c4/self-play-autoreset.json`, `tools/crforge/self_play.py`,
`crforge_bridge/vector_actions.py` (`plan_window_ticks`),
`crforge_bridge/vector_observation.py` (per-lane clock),
`crforge_bridge/vector_tensorize.py` (`reset_rows`, owned record).

### 3.59 The C4 rollout goes through the PPO update (2026-11-19)

§3.58 stored a correct `StoredRolloutV4`, but the distributed update refused any
rollout with an `episode_start` after step 0 ("distributed PPO lanes must contain
one episode each"). A crforge lane cut at `max_ticks` autoresets mid-segment, so
the store and the trainer did not meet. This section closes that: the update now
replays a multi-episode lane, and the producer runs the update over the stored
record itself.

#### The update splits at episode boundaries

`evaluate_encoded_action_sequence` fuses the whole `[T,B]` slice through
`torch._VF.lstm` and zeroes a lane's hidden/cell only at **row 0**; a reset after
step 0 would carry the dead episode's state into the new one, which is why the
old guard existed. The fix keeps that invariant instead of weakening it:

* `_episode_segments(episode_start)` returns contiguous `[start, end)` slices,
  split at every row where any lane starts a new episode;
* `_episode_chunks` cuts each slice into TBPTT chunks of `sequence_chunk_steps`,
  so no chunk straddles a boundary;
* the update carries the state across chunks and across slices, and passes each
  slice's boundary row as chunk row 0, where the fused kernel zeroes exactly the
  lanes that reset. Lanes that did not reset keep their carried state.

GAE needed no change: `generalized_advantage_estimate` already stops its trace at
`terminated | truncated`, and the producer marks the cut + the next-episode
`episode_start` consistently. The fused replay's own `validate` guard is still
there: a caller that hands it a mid-reset slice still fails, which is what the
new test checks it does not do.

#### The store had to leave inference mode for autograd

The producer samples under `inference_mode`, and `torch.as_tensor(...).detach()
.reshape(-1)` on an inference tensor returns an inference tensor. The store's
shape/finiteness checks passed, but the first PPO backward failed with
`Inference tensors cannot be saved for backward`. `_rollout_float` now clones the
stored log-probability and value outside inference mode, exactly as
`TensorRecordV4.to_storage` does for observations and actions. A regression test
builds the store inside `inference_mode` and backpropagates through its tensors.

#### Behaviour identity is the weights

Each segment binds `_policy_state_id(model, checkpoint_id)`, a SHA-256 over the
checkpoint id and every parameter/buffer. When `--ppo-update` changes the weights
between segments, the next segment's id changes; the autoreset run records
`3835a22f…` then `899ef703…`. Without this, a later update could replay
behaviour sampled by different weights under the old checkpoint id.

#### Measured

Gate-shape update — `tools/crforge/self_play.py --count 8 --segments 1
--decisions-per-segment 64 --ppo-update --ppo-sequence-chunk 32`,
`runs/crforge-c4/self-play-update.json`:

| measurement | value |
| --- | ---: |
| lane-decisions | **1,024** (64 decisions × 16 rows) |
| ACT lane-decisions / eligible | **45 / 983 (4.58 %)** |
| illegal / faults / rejected | **0 / 0 / 0** |
| reward vs engine-terminal mismatches | **0** |
| update frames / epochs | **1,024 / 1** |
| update wall | **51.9 s** |
| collection wall / rate | 27.0 s / **37.9 lane-decisions/s** |
| full wall | 78.9 s |
| `approx_kl` / `clip_fraction` | **2.9e-5 / 0** |
| gradient norm | 1.147 |

Multi-episode lane — `--count 8 --segments 2 --decisions-per-segment 60
--max-ticks 600 --ppo-update`, `runs/crforge-c4/self-play-update-rollout.json`:

| measurement | value |
| --- | ---: |
| lane-decisions | **1,920** |
| ACT lane-decisions / eligible | **56 / 1,559 (3.59 %)** |
| illegal / faults / rejected | **0 / 0 / 0** |
| mid-rollout `episode_start` rows | **16** (8 lanes × 2 seats, segment 1) |
| mid-rollout truncations | **16 rows** (8 lanes) |
| updates | **2**, 960 frames each |
| update wall (segment 0 / 1) | **42.8 / 45.1 s** |
| collection wall / rate | 39.5 s / 48.6 lane-decisions/s |
| `approx_kl` (segment 0 / 1) | **2.8e-5 / 1.0e-6** |
| `clip_fraction` | 0 / 0 |
| gradient norm | 1.298 / 1.247 |
| behaviour ids | **2** (`3835a22f…`, `899ef703…`) |

The collection rate in the second run is recorded as measured, not as a new
throughput claim; the plain gate-shape run is 37.9, matching §3.58's 37.8.

#### Pins

* `--ppo-update` builds a fresh `AdamW` per segment: it is a mechanics gate, not
  the training loop, so momentum does not span segments.
* It updates between segments while the producer carries the recurrent state and
  previous-action record across the boundary. The stored record is internally
  consistent (the next segment's initial state is what the behaviour actually
  used), but a real trainer must decide explicitly whether to reset the
  recurrence at an update or collect a whole wave with a frozen policy.
* One epoch from the behaviour policy is not a learning result:
  `approx_kl ≈ 1e-5`, `clip_fraction 0`, and segment 1's
  `value_explained_variance` is −0.59. The gate is that reward, GAE, replay and
  backward are correct.
* CUDA was not attempted: this host has CPU-only torch (`2.5.1+cpu`) and no
  `nvidia-smi`. The gate-shape measurement is CPU.

#### Tests and evidence

`native_runner/tests/test_training_v4_learning.py`:
`test_distributed_ppo_replays_a_mid_rollout_episode_start` (two lanes, reset at
step 2, `sequence_chunk_steps=3`, `validate=True`, `approx_kl 0`).
`crforge_bridge/tests/test_rollout.py`:
`test_store_escapes_inference_mode_for_the_ppo_update`. Native learning suite
**37 passed / 4 skipped**; bridge suite **74 passed**; the new bridge/producer
modules and `distributed_ppo.py` are ruff clean (the native test and `model.py`
files keep their pre-existing import-sort warnings).

Evidence: `native_runner/training/v4/distributed_ppo.py`,
`crforge_bridge/rollout.py`, `tools/crforge/self_play.py`,
`runs/crforge-c4/self-play-update.json`,
`runs/crforge-c4/self-play-update-rollout.json`.

### 3.58 The C4 rollout store and the native-shape reward (2026-11-19)

§3.57's producer measured the packet but stored nothing, so it could not be a
trainer. A PPO transition needs the observation batch, the sampled action, the
behaviour log-probability, the behaviour value, and a reward with its episode
boundary. `crforge_bridge/rollout.py` adds them without inventing a second
rollout format: `CrforgeRolloutStore.finalize()` returns the shared
`StoredRolloutV4`, so C4 uses the same GAE
(`learning.generalized_advantage_estimate`), the same validation and the same
per-lane minibatch contract the native path uses.

#### The reward is the native reward, rebuilt from the facts

`LadderEnv.reward` is the sparse terminal result (±1/0); the native PPO path
adds potential-based shaping (`BattleEnvV1._rewards`, `shaping_beta=0.05`,
`gamma_per_decision=0.9997`, i.e. `tau = 5 / -ln(0.9997) ≈ 16,665` ticks).
`PotentialShapingReward` rebuilds it from the vectorized facts: crowns difference
plus weighted tower loss (king 3, princess 1), own losses negative, enemy losses
positive, the two seats averaged so the value is exactly antisymmetric, and the
potential forced to zero at a terminal. The unit test drives the **native**
`_potential` / `_rewards` on the same tower states and compares the stored reward
bit for bit; the producer also checks the engine's own reward against the
terminal result header every time a lane ends (`reward_result_mismatches`).

Two semantics are recorded rather than guessed:

* **a terminal zeroes the potential**, so the closing shaping term is counted.
  That is what makes the shaping policy-invariant;
* **a time-limit cut carries no shaping term.** `max_ticks` ends the episode and
  the vector autoresets the lane on the next step, so the producer cannot read
  the potential of the cut state. The decision is stored as `truncated` with the
  terminal result (0) and a zero bootstrap instead of manufacturing a drop from
  the old potential to the new episode's zero.

Rewards are kept as `(lanes, seats)` and flattened to `lane * seats + seat`, the
producer's interleave order; the artifact checks every pair is exactly zero-sum.

#### Segment boundaries, and lanes with more than one episode

`rollout_next_values` bootstraps each segment's last transition from a **boundary
forward** on the post-window observation and marks it truncated, so GAE never
propagates a trace across the segment cut. Inside a segment a lane may contain
more than one episode (the vector autoresets it one lane at a time);
`episode_start` carries that boundary into the recurrence and the GAE trace stops
at it. The rollout's `initial_state` is the recurrent state at the segment start,
not a zeroed state, so a segment boundary is a continuation and only the return
trace is cut.

**One constraint was open at this point, and §3.59 closes it:**
`distributed_ppo_update_v4` refused a rollout with an `episode_start` after step
0 ("distributed PPO lanes must contain one episode each"). The update now splits
the time axis at every boundary row and replays each contiguous episode slice,
so a crforge segment that autoresets a lane goes straight into the update.

#### The second micro-action, exercised end to end

The sampled policy emits one micro-action (429 of 429 active rows in the gate
shape), so §3.57's collision/drop counters were never exercised by a run.
`--force-second-micro` appends a **different** legal play from the open
candidates at the first play's own delay bin: on a lane whose first play runs
before the last window tick the collision shifts the second play one tick; on a
lane whose first play already runs on the last tick, the second has no free tick
and is counted as dropped.

The first version duplicated the sampled play itself, and **the engine refused
it**: one tick later the same action's slot was closed (the queued play, or a
tile the play invalidated). The probe therefore picks a different hand slot,
requires both plays to be payable from the decision's effective elixir, and
takes a tile its own placement grid opens. It is refused
together with rollout storage, because the stored sampled action would no longer
be the action the engine ran.

#### Measured

Gate shape with storage — `tools/crforge/self_play.py --count 8 --segments 10
--decisions-per-segment 64`, `runs/crforge-c4/self-play-rollout.json`:

| measurement | value |
| --- | ---: |
| decisions / lane-decisions | 640 / **10,240** |
| ACT lane-decisions / eligible | **429 / 9,877 (4.34 %)** |
| illegal / faults / rejected | **0 / 0 / 0** |
| reward vs engine-terminal mismatches | **0** |
| lane-decisions per second | 37.8 (probe-only run: 39.0) |
| wall (10 segments) | 270.7 s (probe-only: 262.4 s) |
| tensorize (both seats) / forward / decode / five steps | 19.2 / **355.0** / 3.4 / 6.7 ms |
| post-window facts encode / reward + store write | 1.83 / 3.18 ms |
| stored per segment | 64 × 16 rows, 57.2–57.4 MiB (fp16) |
| stored in total | **573.1 MiB** |
| `episode_start` rows / terminals / boundary truncations | 16 / 0 / 16 |
| GAE / returns | finite; advantage mean −0.068, std 0.110 |
| shaping reward rows | 8,780 of 10,240 (max \|r\| 0.00101, zero-sum deviation 0) |

The rollout adds ~3.2 ms to a ~400 ms decision, so the forward is still the
packet (94 %). Storage is the new resource: 57 MiB per 64-decision segment at 16
rows, i.e. ~0.9 MiB per decision.

Autoreset store — 4 lanes, 2 × 60 decisions, `--max-ticks 600`
(`runs/crforge-c4/self-play-autoreset-rollout.json`): **4 autoresets**, **8
mid-rollout truncations** (4 lanes × 2 seats, zero shaping), **8 `episode_start`
rows at the following decision**, 0 terminals, 0 illegal, 0 faults, GAE finite.
That is the path a multi-episode lane takes through the store.

Two-micro timing probe — 8 lanes × 40 decisions, `--force-second-micro alternate
--no-rollout` (`runs/crforge-c4/self-play-two-micro.json`): **22 forced second
micro-actions, 18 shifted, 4 dropped, 0 illegal, gate pass**. The timing path is
now exercised by a run, not only by `plan_window_ticks`'s unit tests.

#### Tests

`crforge_bridge/tests/test_rollout.py` (7 tests): the reward against the native
`_potential`/`_rewards` (non-terminal and terminal), the time-limit cut, the
segment-boundary bootstrap, GAE not leaking across a terminal, store
shape/validation, and a **live** stacked record tensorized under
`inference_mode` stored as owned training data (the §3.57 failure mode). Bridge
suite **73 tests**, all passing; the touched files are ruff clean.

Evidence: `crforge_bridge/rollout.py`, `tools/crforge/self_play.py`,
`crforge_bridge/tests/test_rollout.py`, `runs/crforge-c4/self-play-rollout.json`,
`runs/crforge-c4/self-play-autoreset-rollout.json`,
`runs/crforge-c4/self-play-two-micro.json`.

### 3.60 The C4 loop becomes a trainer: one optimizer, a stated state policy (2026-11-19)

§3.59 ran one PPO update per stored segment with a **fresh AdamW each time** and
carried the recurrent state across the update. Both choices were gate shortcuts:
momentum never spanned a segment, and the LSTM state was produced by weights the
update had already replaced. This section turns the gate into a loop:
`crforge_bridge/training.py` owns one persistent optimizer, an explicit
recurrence policy at an update boundary, a reward/return trace, and the
two-update gate; `tools/crforge/self_play.py` only collects, steps and asks the
trainer to advance.

#### The optimizer is created once

`CrforgeTrainer` builds `AdamW` (`lr`, `eps`, `weight_decay`; native defaults
`1e-5 / 1e-5 / 0`) in its constructor and never rebuilds it. The pinned
`B-general-v275` seed carries `optimizer_state_dict: None`, so a fresh Adam *is*
the honest continuation point; a checkpoint saved by this loop stores the
optimizer state, and `--save-checkpoint` records both the loop counters and the
weight digest. Every update is one `distributed_ppo_update_v4` call, so a
single-minibatch update is one optimizer step; `training.optimizer_steps` counts
the steps, `training.updates` counts the calls.

#### The boundary policy is a flag, not an accident

`--update-state-policy` names what happens to the recurrent state when the
weights move:

* `carry` — keep it. This is the deployed inference shape (one state for a whole
  match) and what §3.59 measured; the cost is that the state was computed by the
  pre-update weights.
* `reset` (default) — zero it after every update. The game continues on the
  engine; only the model's memory is cut, so the LSTM never holds state from
  weights it no longer has.
* `wave` — keep the behaviour weights frozen for `--wave-segments` segments,
  apply their updates at the wave end, then reset. This is the native trainer's
  collect-then-update cadence and the only mode where every stored segment in a
  group was sampled by the same weights.

The wave is about *when* the optimizer steps, not what one update sees: each
segment still updates separately, because each ends in a truncation with its own
boundary bootstrap and GAE must not cross it. A run that ends mid-wave flushes
the partial wave and records `training.partial_wave_segments_flushed`.

#### The gate is two consecutive finite updates

`training.trace` holds one row per update: `reward_sum`, `reward_mean`,
`return_mean`/`return_std`, `value_explained_variance`, `approx_kl`,
`clip_fraction`, `gradient_norm`, the optimizer step index and the behaviour
hash before and after. `training.pass` needs at least **two** updates, every
trace row finite, every rollout identity check passed and — when a checkpoint is
saved — its reload digest to match. One update proves the mechanics; two show
the loop advances a policy and keeps collecting after it
(`distinct_behaviour_ids`).

#### Measured — reset policy, `runs/crforge-c4/self-play-train-reset.json`

8 lanes × 2 seats × 4 segments × 64 decisions, `--ppo-update
--update-state-policy reset --save-checkpoint`, CPU:

| measurement | value |
| --- | ---: |
| lane-decisions | **4,096** |
| ACT lane-decisions / eligible | **139 / 3,987 (3.49 %)** |
| illegal / faults / rejected | **0 / 0 / 0** |
| updates / optimizer steps | **4 / 4** |
| distinct behaviour ids | **4** |
| per-segment update wall | 44.6 / 46.0 / 45.1 / 44.9 s |
| collection wall / rate | 86.0 s / **47.6 lane-decisions/s** |
| full wall | 266.7 s |
| `return_mean` trace | 0.227 → 0.258 → 0.296 → 0.256 |
| `value_explained_variance` trace | −0.001, −1.03, 0.258, 0.304 |
| `approx_kl` trace | 2.9e-5, −4e-6, −3e-6, 6e-6 |
| `clip_fraction` | 0 on every update |
| checkpoint update step / reload | **279** / **digest match** |

The first chosen default was `--ppo-learning-rate 1e-4`, ten times the native
trainer's `1e-5`. Four updates at that rate collapsed the gate: ACT fell
45 → 14 → 0 → 0 of ~1,000 eligible lane-decisions per segment
(`runs/crforge-c4/self-play-train-reset-lr1e-4.json`). The loop was correct; the
learning rate was not the native one. At 1e-5 the ACT rate stays at the seed
policy's level (3.49 % vs §3.58's 4.34 %) and the value head starts explaining
returns by segment 2. That is a stability observation on one 4-update run, not a
learning claim.

#### Measured — wave policy, `runs/crforge-c4/self-play-train-wave.json`

8 lanes × 4 segments × 48 decisions, `--update-state-policy wave
--wave-segments 2`:

| measurement | value |
| --- | ---: |
| lane-decisions | **3,072** |
| ACT lane-decisions / eligible | **103 / 2,944 (3.50 %)** |
| illegal / faults / rejected | **0 / 0 / 0** |
| updates / optimizer steps | **4 / 4** |
| distinct behaviour ids | **4** |
| `policy_state_id_before` per wave | **2 ids** (`fb4acf…` updates 1–2, `b85a91…` updates 3–4) |
| update wall (at the wave ends) | 66.9 s (seg 1) + 65.9 s (seg 3) |
| collection wall / rate | 67.9 s / **45.2 lane-decisions/s** |
| full wall | 200.8 s |
| `return_mean` trace | 0.253 → 0.198 → 0.263 → 0.278 |
| `value_explained_variance` trace | 0.014, −0.166, 0.034, −0.333 |
| `approx_kl` trace | 4.1e-5, **−7.8e-4**, 9e-6, **−1.4e-3** |
| `clip_fraction` trace | 0, **0.047**, 0, **0.048** |

That is the wave's proof and its cost in one table. Both segments of a wave were
sampled by the same weights — updates 1 and 2 share `policy_state_id_before`, as
do updates 3 and 4 — and the optimizer did not step until the wave closed
(segments 0 and 2 record `updates: []`, a 17.9 s / 16.4 s collection wall). But
the second update in a wave replays data the first update has already moved away
from: KL goes from 4.1e-5 to −7.8e-4 and the clip fraction from 0 to 4.7 % within
wave 1. The drift is small, and it is the actual price of one optimizer over a
batch of segments; a trainer that wants strict on-policy data has to update once
per wave over the concatenated record (which the truncation boundary forbids) or
keep waves short. Recorded, not hidden.

#### Measured — autoreset lanes with `carry`, `runs/crforge-c4/self-play-train-autoreset-carry.json`

4 lanes × 2 segments × 60 decisions, `--max-ticks 600 --ppo-update
--update-state-policy carry`:

| measurement | value |
| --- | ---: |
| lane-decisions | **960** |
| ACT lane-decisions / eligible | **28 / 795 (3.52 %)** |
| mid-rollout `episode_start` rows | **8** (4 lanes × 2 seats) |
| mid-rollout truncations / resets | **8 / 4** |
| updates / distinct behaviour ids | **2 / 2** |
| `return_mean` trace | 0.248 → 0.159 |
| `value_explained_variance` trace | −0.194, 0.109 |
| `approx_kl` / `clip_fraction` | 6.1e-5 / 4e-6, 0 / 0 |
| collection wall / rate | 29.2 s / 32.9 lane-decisions/s |
| update wall | 38.9 s |
| rejected | **0** |

This is the multi-episode lane through the *loop*: the engine autoresets four
lanes, the store records eight truncations and eight `episode_start` rows, the
update replays each contiguous episode slice, and `carry` keeps the recurrent
state across the segment boundary. `rollout.update.multiple_episodes_per_lane`
is true, and both updates are finite.

#### The forced probe stays in the gate set

`--force-second-micro alternate --no-rollout`, 8 lanes × 40 decisions
(`runs/crforge-c4/self-play-two-micro-probe.json`): **22 forced second
micro-actions, 18 shifted, 4 dropped, 0 illegal, 0 rejected**, gate pass. The
sampled policy still emits one micro-action, so this remains the only run that
exercises the collision/drop path end to end; it is a timing probe and stores no
rollout (`training.enabled` false).

#### Tests

`native_runner/tests/test_training_v4_crforge_trainer.py` (7 tests): the config's
wave/policy validation, two consecutive updates with one optimizer object and an
advancing weight digest, the one-update gate failing, a wave that does not step
the optimizer until both segments are collected (and both bind the same frozen
behaviour id), a partial wave at the run end, `carry`/`reset` on a non-zero
recurrent state, and the digest's stability/weight sensitivity. Native learning
suite plus the new file **40 passed / 4 skipped**; bridge suite **74 passed**; the touched files
are ruff clean.

#### Pins

* **One update per stored segment, even in wave mode.** A segment cut is a
  truncation with its own boundary value; concatenating two segments into one
  update would either lose the bootstrap or let GAE cross a cut. The wave's
  second update is therefore mildly off-policy (see the clip fraction above).
* **A stored rollout's identity is the weights, not the checkpoint file.** The
  trace records `policy_state_id_before` / `_after` per update, so a run can show
  both that a wave was collected under frozen weights and that the optimizer
  moved the policy (`distinct_behaviour_ids`).
* **`--save-checkpoint` writes `training_stage="crforge-c4-ppo"`,
  `update_step = source update step + updates`, and the persistent optimizer.**
  The save is followed by a `load_policy_v4` reload and a weight-byte comparison;
  a mismatch fails `training.pass`. The payload is ~203 MB (weights + Adam
  moments) per save, so it is a local `runs/` artifact, not a committed file.
* **CUDA is unchanged:** this host is CPU-only (`torch 2.5.1+cpu`, no
  `nvidia-smi`), so the loop's device flag is exercised only on CPU. The forward
  is still the packet (§3.57), so GPU throughput stays the next lever.
* **The learning rate is the native one.** `1e-5` AdamW with `eps 1e-5`,
  `weight_decay 0`, clip `0.1`, value coefficient `0.5`, entropy `0.0` — the
  native trainer's PPO config, not a crforge invention. `1e-4` is recorded above
  as the rate that collapsed ACT.

Evidence: `crforge_bridge/training.py`, `tools/crforge/self_play.py`,
`native_runner/tests/test_training_v4_crforge_trainer.py`,
`runs/crforge-c4/self-play-train-reset.json`,
`runs/crforge-c4/self-play-train-reset-lr1e-4.json`,
`runs/crforge-c4/self-play-train-wave.json`,
`runs/crforge-c4/self-play-train-autoreset-carry.json`,
`runs/crforge-c4/self-play-two-micro-probe.json`.

### 3.61 A C4 run resumes from its own checkpoint (2026-11-19)

§3.60 wrote the persistent AdamW into `--save-checkpoint` and nothing read it
back: the next run could only restart from the seed, which silently throws the
momentum away and labels the result a continuation. A paid GPU run makes that
unacceptable — the expensive part is the loop, not the seed — so the checkpoint
became a starting point.

#### The optimizer state is training state

`optimizer_signature(optimizer)` hashes the AdamW `param_groups` (lr, eps, weight
decay) **and every moment tensor**, and reports their bytes. For the 12.7M model
that is **134,996,348 bytes** of moments — the number a resume either restores or
does not. It is the training analogue of `weights_digest`: two runs with the same
weights and different moments diverge on the next update.

`load_resume_state(path, model, optimizer)` then:

* loads the weights and the optimizer in one `load_actor_critic_checkpoint` call,
  so the moments and the weights come from the same file and the same strict
  contract check;
* recomputes `weights_digest(model)` and compares it with the digest the run
  recorded (`extra.weights_digest`);
* **refuses** a checkpoint with `optimizer_state_dict: None`, no
  `extra.crforge.loop` block, or a weight-digest mismatch. The seed
  `B-general-v275` is refused by name:
  `--resume refused: … carries no crforge loop counters`.

#### The step counter had to be rebased

The first wiring added the resumed counters on top of `policy.update_step`, which
double-counts the previous run: resume a step-277 checkpoint that already has two
updates and the next save would have claimed 282, not 279. The rule is now
`update_step = base_update_step + updates`, where a fresh run takes
`base_update_step = policy.update_step`, and `CrforgeTrainer.resume` sets
`base_update_step = saved_step - prior_updates`. A resumed wave run keeps its
cadence or is refused: `--update-state-policy` / `--wave-segments` are CLI
settings, so a mismatch with the recorded `state_policy` is a `SystemExit`, not a
silent change of when the optimizer steps.

The gate also had to say *whose* updates it counts. `training.updates` is
cumulative and `training.updates_this_run` is what `two_consecutive_updates`
reads, so a resume that runs one update is still not a training loop.

#### Measured — one loop across two processes

4 lanes × 2 seats × 2 segments × 24 decisions, CPU,
`runs/crforge-c4/self-play-resume-source.json` then
`-resume.json` with `--resume --save-checkpoint`:

| measurement | source run | resumed run |
| --- | ---: | ---: |
| lane-decisions | 384 | 384 |
| ACT / eligible | 18 / 370 | 21 / 379 |
| illegal / faults / rejected | 0 / 0 / 0 | 0 / 0 / 0 |
| updates (cumulative) / this run | 2 / 2 | **4 / 2** |
| saved `update_step` | 277 | **279** |
| `base_update_step` after resume | 275 | 275 |
| Adam moments restored | — | **134,996,348 B** |
| `resume.update_step_continued` | — | **true** |
| checkpoint reload digest | match (203,041,896 B) | match (203,042,024 B) |
| `return_mean` trace | 0.330 → 0.240 | 0.341 → 0.208 |
| `approx_kl` | 7.0e-5, −8.0e-5 | 3.8e-5, 2.7e-5 |
| gradient norm | 4.199, 2.544 | 4.694, 3.294 |
| gate | pass | **pass** |

The trace's `update_index` continues at 3 and 4, `distinct_behaviour_ids` is 2
for the resumed run, and the resumed run's behaviour ids are new even before its
first update — `policy_state_id` mixes the checkpoint's file id with the weight
digest, so `weights_digest` is the value that compares across runs.

#### The forward is linear in rows — the GPU lever, measured

4 lanes to 64 lanes, 8 decisions, `--no-rollout --no-validate`, this host
(i5-1235U, `torch 2.5.1+cpu`), median ms per decision:

| lanes | actor rows | forward | ms/row | tensorize | facts | decode | 5 engine steps | lane-dec/s |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8 | 16 | 248.9 | 15.6 | 11.3 | 1.5 | 1.2 | 4.8 | 55.2 |
| 16 | 32 | 420.9 | 13.2 | 13.7 | 1.9 | 1.3 | 6.6 | 69.8 |
| 32 | 64 | 805.2 | 12.6 | 16.7 | 2.6 | 1.5 | 9.5 | 71.8 |
| 64 | 128 | 1,945.7 | 15.2 | 20.3 | 3.6 | 2.4 | 15.8 | 63.6 |

The forward costs ~12.6–15.6 ms per actor row at every batch size: dense compute
with no batching discount, which is the shape a GPU collapses. The CPU half
(facts + tensorize + decode + steps) grows only ~2× from 16 to 128 rows, so once
the forward moves it becomes a cheap, batch-friendly floor — and on a rented box
that floor runs on the box's **vCPU**, not on this host, because the crforge
engine is an in-process single-threaded JVM and the tensorizer is NumPy in the
same process. With the recorded §3.60 split (21.8 s collection vs 44.9 s update
per 1,024 frames), the update is now the larger wall and the GPU's home turf.

#### Pins

* **`--resume` requires `--ppo-update`**, and the resumed model is the checkpoint
  named by `--checkpoint`; `substrate.checkpoint` records that provenance while
  `training.resume.path` records it again next to the counters.
* **The RNG state is not restored** (`restore_rng=False`), so a resumed run is a
  continuation of the *weights and momentum*, not a bit-identical replay of an
  uninterrupted one. Quote `update_step` and the trace, not equality of samples.
* **`policy_state_id` is per-file.** It hashes `checkpoint_id:weights_digest`, so
  a resumed run's first rollout cannot share an id with the source run's last
  one even at identical weights; `weights_digest` is the comparable value.
* **A resume is refused when the cadence disagrees.** Recorded `state_policy` /
  `segments_per_wave` are compared with the CLI; the wave cadence is not read
  back into the config because it changes *when* the optimizer steps.

Evidence: `crforge_bridge/training.py` (`optimizer_signature`,
`CrforgeResumeState`, `load_resume_state`, `CrforgeTrainer.resume`),
`tools/crforge/self_play.py` (`--resume`, `training.resume`),
`native_runner/tests/test_training_v4_crforge_trainer.py` (3 resume tests),
`runs/crforge-c4/self-play-resume-source.json`,
`runs/crforge-c4/self-play-resume.json`.

### 3.62 The A6000 measures the producer, not the model (2026-11-19)

The C4 plan assumed the forward was the wall and a GPU would remove it (§3.57:
"the forward is 94 % of the packet"). One hour on a rented **RTX A6000 (48 GB,
6 vCPU, 48 GB RAM, $0.35/h)** says otherwise: the GPU is real and idle, and the
per-decision Python path is the ceiling. It also found three defects the CPU
could not.

#### The box, and what it took to run

Thunder Compute `base` template (Ubuntu 22.04, `torch 2.12.1+cu130`, numpy 2.5.0,
Python 3.12.13), plus `openjdk-21-jre-headless`, `jpype1`, `gymnasium`,
`pettingzoo`, `orjson`, `zstandard`, `sc-compression`, `pyarrow`, `safetensors`,
`tzdata`. Payload: 76 MB — `tools/`, `crforge_bridge/`, `native_runner/`, the
prebuilt crforge install dist (`env/build/install/env`, 4.3 MB of jars), the
`16.635.8` tables (6 MB) and the 67 MB `B-general-v275` seed. Upload 1.1 MB/s;
provisioning to first gate under 10 minutes. `nvidia-smi` and a 4096² matmul
confirm the device: 9.0 TFLOP/s fp32, and `next(model.parameters()).device` is
`cuda:0`.

#### Three defects the CPU hid

1. **The model's own contract validation ran every decision.** The producer
   passed no `validate=` to `sample_for_ppo_rollout`, which defaults to `True`.
   That is pure Python on CPU (expensive but silent); on a GPU every value it
   inspects syncs the device. The first measurement was **372.9 ms at 16 rows**,
   slower than the laptop's 217.2. Threading `--no-validate` into the call took
   it to **88.5 ms**.
2. **The timing buckets around the forward did not synchronise.** Without
   `torch.cuda.synchronize()`, `inference_ms` is a kernel-launch time and the
   queued work lands in the *decode* bucket (which starts with
   `.cpu().numpy()`). The first run reported decode 12.7 ms at 16 rows against
   1.2 ms on CPU — a tell. Both the forward boundary and the trainer's update
   wall now sync on CUDA.
3. **The rollout store mixed devices.** `CrforgeRolloutStore._row_mask` and the
   reward vector were built on CPU while `log_prob`/`value` came from the CUDA
   forward, so `rollout_next_values` raised
   `Expected all tensors to be on the same device` on the first update. The store
   now takes the producer's device and pads masks/rewards there.
4. **cuDNN refuses an RNN backward in `eval()` mode.** The producer collects in
   `model.eval()` (as the native trainer does), which is legal on CPU only:
   `RuntimeError: cudnn RNN backward can only be called in training mode`. The
   trainer now runs each update in `train()` and restores the collection mode;
   with `dropout=0.0` and no batch norm that changes nothing but the gate.

A fifth is a limit, not a bug: an update over **64 rows × 32 decisions (2,048
frames)** with the defaults (`lane_minibatch 0`, `sequence_chunk 32`) exhausts
48 GB during the backward. The gate shape (16 rows × 64 decisions) fits and the
CPU path never noticed because it ran half the frames.

#### The forward is a latency problem on GPU, a throughput problem on CPU

Same probe on both machines: 8 decisions, `--no-rollout --no-validate`, median ms
per decision (`runs/crforge-c4/gpu-forward-scaling-*.json`):

| lanes | rows | laptop forward | ms/row | laptop ld/s | A6000 forward | ms/row | A6000 ld/s | GPU vs laptop |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8 | 16 | 217.2 | 13.6 | 63.0 | 88.5 | 5.53 | 21.3 | **0.34×** |
| 16 | 32 | 351.5 | 11.0 | 82.4 | 95.8 | 2.99 | 38.6 | 0.47× |
| 32 | 64 | 749.1 | 11.7 | 76.2 | 92.2 | 1.44 | 81.0 | 1.06× |
| 64 | 128 | 1,901.2 | 14.9 | 65.2 | 107.0 | 0.84 | 140.2 | **2.15×** |
| 128 | 256 | — | — | — | 141.0 | 0.55 | 251.3 | — |

The GPU forward is **flat in rows** from 16 to 128 (88.5 → 107.0 ms): it is
launch- and dependency-latency bound, not matmul bound. The CPU forward grows
~linearly. So the GPU's per-row advantage only appears as the batch grows, and
the *end-to-end* producer rate follows the CPU half, which is **2–13× slower on
the box's six vCPUs** (tensorize 26–34 ms vs 10–12; decode 13–17 ms vs 1.0–1.2;
engine steps 7.8–9.9 vs 5.5–6.1). At the C4 gate shape (8 lanes), the A6000 is
**three times slower than the laptop**.

Amortised (128 lanes × 32 decisions, `gpu-forward-scaling-128-long.json`): wall
20.7 s for 8,192 lane-decisions = **395.8 lane-decisions/s**, with tensorize
81 ms, forward 157 ms, decode 97 ms and engine steps 75 ms per decision — the
forward is 24 % of the wall. The remaining ~228 ms of the 647 ms decision is not
in any bucket and is the next thing to profile.

#### The update is where the GPU actually pays

8 lanes × 64 decisions = 1,024 frames, 16 rows — the §3.59 gate shape
(`runs/crforge-c4/self-play-gpu-update.json`):

| measurement | laptop (CPU) | A6000 |
| --- | ---: | ---: |
| update wall | 44.9 s | **5.50 / 6.14 s** |
| speedup | 1× | **7.5×** |
| collection wall (validated + stored) | 21.8 s | 38.9 / 47.4 s (26.3 / 21.6 ld/s) |
| gate | pass | **pass** (63 ACT / 1,960 eligible, 0 rejected) |
| checkpoint / reload | step 277, match | **step 277, match** (203,056,929 B) |

And the resume path works on CUDA (`self-play-gpu-resume.json`): resumed at step
277 with 2 prior updates, ran 2 more, saved **279** with
`update_step_continued: true` and the moments restored — a checkpoint written on
one device resumes on either.

#### What it means for the plan

* **"8 lanes per process, 2–4 processes" is the wrong shape for a GPU.** The box
  wants **one process at 64–128 lanes**; at 128 lanes it does **395.8
  lane-decisions/s ≈ 0.33 matches/s**, which is 6× the laptop's 65.2 at 64 lanes.
* **The roadmap's "GPU-saturated ~51 matches/s" is not reachable from this
  producer.** At 0.33 matches/s per process, even six processes on the box's six
  vCPUs is ~2 matches/s, not 51: the ceiling is the per-decision Python packet
  (tensorize + decode + engine steps), not the model.
* **Store device choice is a real cost on GPU.** Keeping the store on the model
  device made `rollout_ms` **21.6 ms/decision** (it copies every observation into
  CUDA) against 3.2 ms on CPU. The observation tensors are the bulk; the likely
  fix is to store observations on CPU and keep the value/log-prob on the GPU —
  unmeasured, and the update already moves what it needs.
* **Batch memory is the new constraint on the update.** Minibatch the lanes and
  shorten the TBPTT chunk on a 48 GB card before scaling rows.

Evidence: `crforge_bridge/rollout.py` (device-safe store),
`crforge_bridge/training.py` (train-mode update, CUDA sync),
`tools/crforge/self_play.py` (`--no-validate` reaches the model, CUDA sync),
`native_runner/tests/test_training_v4_crforge_trainer.py` (train-mode test),
`runs/crforge-c4/self-play-gpu-update.json`,
`runs/crforge-c4/self-play-gpu-resume.json`,
`runs/crforge-c4/gpu-forward-scaling-{8,16,32,64,128,128-long}.json`.

### 3.63 The cadence decision: `reset` trains, `wave` is a diagnostic (2026-11-19)

Session 29 left the update-boundary policy as "pick one from measurement", but the
two runs it compared were different shapes (wave 48 decisions, reset 64), so their
KL/clip numbers could not be set against each other. `tools/crforge/cadence_compare.py`
removes that excuse: it runs `reset`, `carry` and `wave` at one shape, checkpoint
and seed list, and classifies every update by whether the weights that sampled its
rollout are the weights the update starts from
(`policy_state_id_before == the previous update's policy_state_id_after`).

#### What a cadence choice actually changes

All three policies run **one optimizer step per stored segment**: 6 segments → 6
updates, 1,152 lane-decisions. The difference is *what each update reads*:

* `reset` — segment → update → zero the recurrence.
* `carry` — segment → update → keep the recurrence.
* `wave(2)` — two segments sampled under one frozen behaviour id, then their two
  updates in order; the second reads data that the first update already moved
  away from.

#### Measured

4 lanes × 6 segments × 24 decisions, 2 seeds, CPU, `OMP_NUM_THREADS=4`,
`runs/crforge-c4/cadence-compare.json` (per-run artifacts under
`runs/crforge-c4/cadence/`):

| policy | updates / stale | abs KL on-policy | abs KL stale | clip on-policy | clip stale | wall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| reset | 6 / **0** | 4.63e-5 | — | **0** | — | 105.9 s |
| carry | 6 / **0** | 2.77e-5 | — | **0** | — | 106.5 s |
| wave | 6 / **3** | 3.36e-5 | **5.38e-3** (160×) | 0 | **3.04 %** | 105.7 s |

Wave's stale half, per seed: |KL| **1.02e-2** / **5.88e-4** and clip **1.91 %** /
**4.17 %**; neither seed's stale updates ever clip 0. The classifier's control is
the run's first update: it is identical to `reset`'s and `carry`'s first update at
the same seed (|KL| 7.46e-5 at seed 1, 4.93e-5 at seed 2), because all three
sampled the same first segment with the same weights.

#### What it says

1. **The wave tax is structural and it sits on the stale half.** The first update
   of a wave is on-policy by construction; every later one replays data whose
   behaviour weights a previous update in the same wave replaced. That is where
   the KL and the clipping live, and it is why the second update of wave 1 moved
   furthest (clip 4.2–5.7 %).
2. **Wave buys neither throughput nor sample reuse.** Every policy runs 6 updates
   for 6 segments; `updates_per_sampling_id` is 2.0 for wave against 1.0 for the
   others, which is the age of the data, not reuse. The walls are 105.7 vs 105.9
   and 106.5 s — noise. The cadence is not a wall decision as implemented.
3. **`reset` and `carry` are both on-policy and neither clips.** Carry's absolute
   KL is if anything lower (2.8e-5 vs 4.6e-5), which is a property of the traced
   updates, not a quality claim: 6 updates at the native 1e-5 cannot rank learning.
   Nothing in the trace disqualifies carry, and nothing in it prefers carry.
4. **Decision:** `reset` stays the training default (clean behaviour identity, and
   the recurrent cut is at a segment boundary, which the rollout already treats as
   a truncation). `carry` remains the deployment-shape candidate for a
   learning-horizon comparison. `wave` is a **diagnostic**, not a trainer — it is
   accepted by `--resume` and useful for exercising the wave path, but it pays a
   measured off-policy tax for no measured gain.

#### The lever this exposes

On this shape collection is ~40 s and the update ~66 s of a 106 s run: the
optimizer is **62 % of the wall on CPU** (and 12 % on the A6000, §3.62). No
cadence overlaps them today — every policy calls `trainer.enqueue` → `flush`
synchronously. The wave is the only policy whose *structure* allows the next wave
to be collected while the previous one updates, but that needs a frozen weights
copy (or a second model), a worker thread and a device-safe handoff. That is a
throughput change, not a state policy, and it is the follow-up — not something to
hide inside `--update-state-policy`.

#### Pins

* **The classifier is a gate, not a heuristic.** `summarize_run` refuses an
  artifact whose trace and per-segment update counts disagree, and the tool
  refuses runs that do not share one `substrate` block.
* **Shape:** the first attempt at 8 lanes × 40 decisions put this 14 GB host into
  swap and killed the session twice (the producer's JVM + torch peak ~5.8 GB
  against 4 GB of swap). The recorded shape is 4 lanes × 6 × 24 at 4 threads. KL
  and clip are fractions, so the classification does not depend on lane count; the
  wall numbers are this host's and this shape's.
* **One update per segment in every policy**, partial waves included (a 5-segment
  wave(2) run flushes its last segment and marks it). The wave changes *when* the
  optimizer steps, not how many times.

Evidence: `tools/crforge/cadence_compare.py`,
`crforge_bridge/tests/test_cadence_compare.py`,
`runs/crforge-c4/cadence-compare.json`, `runs/crforge-c4/cadence/*.json`.
