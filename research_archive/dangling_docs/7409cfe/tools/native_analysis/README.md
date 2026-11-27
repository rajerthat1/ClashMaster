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
`field-offsets.json` + `champion-layout.json`. It refuses an anchor whose 16-byte prologue cannot be read
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
    --champion docs/native/16.635.1/champion-layout.json \
    --out native_runner/probe/build_16_635_1_layout.h
```

`champion-layout.json` carries the Champion controller/action/ability vtables
and the `LogicCharacterAbilityData` field shift. The controller is an EXACT
static relocation match; the data vtables and field offsets were confirmed from
live controller objects with the probe's `champion-diag` command (FINDINGS
§3.22). Passing `--champion ""` skips that block.

## Live content-ready capture (`capture_content_window.py`)

The probe gates cold `configure` on a single byte at
`content_context + kContentPostProcessReadyOffset`. The 15.535.13 value is
`0x6e8`; on 16.635.1 the field moved to `0x720` (FINDINGS §3.13), so the byte is
build-pinned through the generated layout header. The probe exposes a bounded,
read-only `content-window <hex offset> <hex length>` command to locate or verify
the byte without root; this script drives it across a fresh boot and reports
which bytes settle to `1`:

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

## Live match drivers

These tools drive a configured match over the probe control socket. They are
diagnostics, not training APIs; forward the port first
(`adb forward tcp:26789 tcp:26789`) and check `adb logcat -b crash -d` after a
run.

### `multi_decision_match.py`

The Phase 0 multi-decision gate: configures one fresh `MatchConfig` match, then
observes both hands, plays one affordable card per owner, and advances across
the command boundary. It validates every action against the engine state (the
played slot is replaced by the deck's next card, elixir pays the cost within
the measured regen bound, no command is left queued):

```bash
.venv/bin/python tools/native_analysis/multi_decision_match.py \
    --min-ticks 600 --output /tmp/opencode/multi_decision_match.json
```

Pass `--min-ticks 7000` to run the whole regulation match (the engine ends at
tick 3601 when a tower falls). Recorded results are in FINDINGS §3.17.

### `native_match_throughput.py`

Times N fresh matches in one of three modes and reports matches/s,
decisions/s and actions/s for one probe connection:

```bash
.venv/bin/python tools/native_analysis/native_match_throughput.py \
    --matches 5 --mode raw --output /tmp/opencode/throughput_raw.json
```

`raw` is one `step` per match (engine-bound ceiling), `batch` repeats
`step --batch-ticks`, and `decision` runs the `multi_decision_match` loop.
Recorded numbers are in FINDINGS §3.18. One connection is one slot; do not
extrapolate resident-slot throughput from it.

### `entity_hp_probe.py`

Verifies the live entity/HP path: `observe` already returns every live object
with `owner`, `cardId` (`-1` for towers), arena position and the hitpoint
component values. This tool deploys a card for each owner and samples the graph,
checking towers are at the native tower cells with sane HP and that units keep
`0 <= hp <= maxHp`:

```bash
.venv/bin/python tools/native_analysis/entity_hp_probe.py \
    --output /tmp/opencode/entity_hp_probe.json
```

Recorded results are in FINDINGS §3.19.

### `scripted_xbow_match.py`

Runs one deterministic scripted X-Bow 3.0 self-play match with FirstLight-Zero's
exact deck and form masks. The policy is split into defense / cycle / spell /
offense modules, uses owner-local placements mirrored for owner 1, and has a
wait option plus blocked-cell fallback. With `--abilities` (default) it polls
the Champion controller every five decisions and plays a ready Hero Knight
ability as a separate native command:

```bash
.venv/bin/python tools/native_analysis/scripted_xbow_match.py \
    --seed 11 --output /tmp/opencode/scripted_xbow_match.json
```

Use `--no-abilities` for the card-only baseline. Recorded results are in
FINDINGS §3.21.

### `ability_action_probe.py`

Verifies one Hero Knight ability through `AbilityAction`: deploys the hero,
dumps the live Champion controller with `champion-diag`, activates the ready
ability, and checks the receipts (injection, `AllChargesConsumed`, zero
charges, elixir paid, second activation refused):

```bash
.venv/bin/python tools/native_analysis/ability_action_probe.py \
    --output /tmp/opencode/ability_action_probe.json
```

The recovery of the 16.635 Champion vtables and ability-data field shift is in
FINDINGS §3.22. `champion-diag` is also available directly on the control
socket for a read-only controller dump.

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
