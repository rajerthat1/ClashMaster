# Native binary analysis tools

Helpers for the `libg.so` probe port. They work on the shipped, section-stripped
ELF because all address translation goes through the program headers.

## Requirements

`capstone`, `numpy`, `pyelftools` (already in `.venv`).

## `cr_elf.py`

```bash
# identity: hashes, build id, LOAD segments
.venv/bin/python tools/native_analysis/cr_elf.py info .resource/native/16.635.1/reference/libg.so

# find strings and their adrp+add xrefs
.venv/bin/python tools/native_analysis/cr_elf.py strings LIBG "replay data passed as NULL" --xrefs

# all references to a VA (adrp+add and adr)
.venv/bin/python tools/native_analysis/cr_elf.py xrefs LIBG 0x32f041

# direct bl/b callers of a function
.venv/bin/python tools/native_analysis/cr_elf.py calls LIBG 0x109ae58

# disassemble a VA range (reads through PT_LOAD)
.venv/bin/python tools/native_analysis/cr_elf.py disasm LIBG 0x109ae58 0x109ae70

# surviving dynamic symbols (JNI exports)
.venv/bin/python tools/native_analysis/cr_elf.py symbols LIBG --grep Touch

# literal 8-byte pointers in data
.venv/bin/python tools/native_analysis/cr_elf.py refs LIBG 0x19f9f20
```

`xrefs` and `calls` use vectorised instruction-word matching, so they are fast
on the 25 MB code segment.

## `relocate_hooks.py`

Extracts every 16-byte hook prologue from `native_runner/probe` and searches a
target build for the same bytes, filtered to plausible function starts.

```bash
.venv/bin/python tools/native_analysis/relocate_hooks.py \
    .resource/native/16.635.1/reference/libg.so \
    --out docs/native/16.635.1/hook-relocation.json
```

Verdicts:

- `UNIQUE-START` — one function start matches. Candidate only; verify semantics.
- `CHANGED` — the prologue does not occur at all.
- `AMBIGUOUS(n)` — generic prologue, needs function-level matching.

## `diff_builds.py`

When an old build is available, this is far stronger than prologue matching. It
takes every `*Offset` from the probe source and searches the new build for exact
byte windows from the old function. Unchanged functions come back `EXACT` with a
new offset and delta; changed functions fall back to independent windows taken
at several relative positions (multi-window vote).

```bash
.venv/bin/python tools/native_analysis/diff_builds.py \
    .resource/native/15.535.13/reference/libg.so \
    .resource/native/16.635.1/reference/libg.so \
    --out docs/native/16.635.1/offset-diff.json
```

Verdicts: `EXACT`, `LIKELY`, `AMBIGUOUS`, `MISSING`. Manual corrections go in a
JSON file and are applied after the automatic passes, so the report stays
reproducible instead of being hand-edited:

```bash
.venv/bin/python tools/native_analysis/diff_builds.py \
    .resource/native/15.535.13/reference/libg.so \
    .resource/native/16.635.1/reference/libg.so \
    --overrides docs/native/16.635.1/manual-overrides.json \
    --out docs/native/16.635.1/offset-diff.json
```

An override has this shape:

```json
{"hook": "kGameStateStep", "new_offset": "0x011d3174", "evidence": "caller-relative match", "note": "..."}
```

Overridden rows get `confidence: MANUAL` and keep the evidence/note fields.

## `match_vtables.py`

Matches probe vtable anchors through `R_AARCH64_RELATIVE` relocations. It also
resolves an old *data* vtable through one function known to survive in the same
slot, which is how the replay controller was recovered:

```bash
.venv/bin/python tools/native_analysis/match_vtables.py OLD NEW --seeded-only \
    --seeded 'replayController=0x186e748:4:4:0xd9cc24' \
    --seeded 'replayControllerSubclass=0x186f6d0:4:4:0xd9cc24' \
    --out docs/native/16.635.1/replay-controller-vtables.json
```

`NAME=OLD_BASE:OLD_SLOT:NEW_SLOT:NEW_FUNCTION` finds every new data word holding
`NEW_FUNCTION`, derives the table base from `NEW_SLOT`, and reports the `-16`
sub-vtable marker shift and per-slot relocation mapping.

## `gen_layout.py`

Emits the build-pinned probe header from `offset-diff.json` +
`field-offsets.json`. It refuses an anchor whose 16-byte prologue cannot be read
from the target binary, so the header never contains an unverified offset. The
header carries the nine runner hooks + touch, the resolved core call offsets
(GameState/JSON/byte-stream/replay manager), and explicitly marked sentinels
(`kUnavailableOffset` + `*Resolved = false`) for the anchors that are still
`AMBIGUOUS`/`MISSING`. Required core anchors fail loudly instead of silently
falling back to a 15.535.13 address:

```bash
.venv/bin/python tools/native_analysis/gen_layout.py \
    .resource/native/16.635.1/reference/libg.so \
    --diff docs/native/16.635.1/offset-diff.json \
    --fields docs/native/16.635.1/field-offsets.json \
    --out native_runner/probe/build_16_635_1_layout.h
```

## Live content-ready capture (`capture_content_window.py`)

The probe gates cold `configure` on a single byte at
`content_context + kContentPostProcessReadyOffset`. That offset is a 15.535-era
literal and is wrong for 16.635 (the byte lands inside a heap pointer). The
probe exposes a bounded, read-only `content-window <hex offset> <hex length>`
command to locate the real byte without root; this script drives it across a
fresh boot and reports which bytes settle to `1`:

```bash
adb forward tcp:26789 tcp:26789
adb shell am force-stop nullsroyale.rel.free
adb shell monkey -p nullsroyale.rel.free -c android.intent.category.LAUNCHER 1
.venv/bin/python tools/native_analysis/capture_content_window.py \
    --seconds 120 --output /tmp/opencode/content_capture.json
```

`--watch` (default `0x6e8`) prints that byte over time; the summary lists every
byte in the window equal to `1` in the final sample with the time it flipped,
and the bytes that only ever take 0/1. Use it on 15.535.13 first to establish
the field's real neighbourhood, then repeat on 16.635.1.

## Ghidra scripts

`ghidra_scripts/ExportFunctions.java`, `DecompileAt.java` and
`DecompileAnchors.java` are for the headless project
(`/tmp/opencode/ghidra_project`, JDK 25). `DecompileAt` requires the function
to exist already; `DecompileAnchors` disassembles and creates it first, which
is useful when the project analysis is stale or incomplete:

```bash
GHIDRA=.resource/ghidra_download/ghidra_12.2_PUBLIC/support/analyzeHeadless
JAVA_HOME=.resource/jdk25/jdk-25.0.5+8 \
  $GHIDRA /tmp/opencode/ghidra_project cr16 -process libg.so -noanalysis \
  -scriptPath tools/native_analysis/ghidra_scripts \
  -postScript ExportFunctions.java /tmp/opencode/functions.csv
```
