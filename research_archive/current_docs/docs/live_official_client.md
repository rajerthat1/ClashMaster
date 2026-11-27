# Official client: live RF deployment

This is the sensor/actuator path for running a **RoyaleFish V4 checkpoint** on
the official Supercell client (`com.supercell.clashroyale`). It does not train
and it does not use the offline Null's client.

The stack is deliberately split:

```
reader (root, read-only)  ->  LiveFrame  ->  ObservationV1  ->  RF (torch)
        ^                                                         |
        |                                                         v
   official client  <--------- adb input tap <--------- decoded ActionV1
```

The reader and the policy never share a model format. RoyaleFish weights load
only in `native_runner.training.v4`; r0/Hasty readers know nothing about V4.

## What is implemented

| Piece | Path | State |
|---|---|---|
| Device diagnostic | `tools/live/probe_device.py` | Working |
| Reader build (ARM64 + x86_64) | `tools/live/build_reader.py` | Working |
| Frame parsers (r0 + Hasty) | `native_runner/live/frames.py` | Working |
| Frame -> FAIR observation | `native_runner/live/observation.py` | Working |
| Tap planner + ADB sender | `native_runner/live/taps.py` | Working |
| RF live runner | `native_runner/live/runner.py` | Working |
| End-to-end CLI | `tools/live/run_policy.py` | Working |
| Tests | `native_runner/tests/test_live_adapter.py` | 10 passing |

The tests replay synthetic r0 and Hasty frames through the same code the live
loop uses, including a real RoyaleFish forward pass.

## The blocker on a retail phone

`/proc/<pid>/mem` of another app requires root (or `adb root`). A production
Samsung image has neither:

```
$ python tools/live/probe_device.py --serial R58NA0QTHWV
device      : SM-A515U (13, SDK 33, arm64-v8a)
official    : 160635013 (code 160635013), abi arm64-v8a
root        : none (uid=2000, adb_root=False)
note        : no root: /proc/<pid>/mem of another app is unreadable
```

ADB taps still work. **Memory reading does not.** That is why MuMu is the
shortcut: MuMu exposes a root toggle, so the same official client inside MuMu
becomes readable. It is an environment shortcut, not a client shortcut.

## Reader builds

Both ABIs compile from Hasty-CR's read-only `hcr_reader.c`:

```bash
python tools/live/build_reader.py --abi arm64-v8a --abi x86_64
# -> build/live/hcr_reader-arm64-v8a
# -> build/live/hcr_reader-x86_64
```

The binary only calls `pread()` on `/proc/<pid>/mem`; it never writes, never
ptraces, and never sends input.

## Version pinning is the real work

Both source readers are pinned to older builds:

| Reader | Client | libg SHA |
|---|---|---|
| r0 `mumu_live_protocol.py` | 16.402.002 | `d2c8efc…` |
| Hasty `memread.py` / `hcr_reader.c` | 16.402.012 | `79c4e48…` |

The installed official client is **16.635.13** (data 16.635.8). The frame
parser and the V4 adapter are version-agnostic, but the pointer chain and field
offsets are not. Before a live read is meaningful you must re-derive:

1. `libg` base and build id (`cr_elf.py info` on the pulled `libg.so`).
2. The battle global / manager RVA (`MANAGER_RVA`, `BATTLE_GLOBAL`).
3. Player record fields: hand vector, cycle vector, elixir, deck count.
4. Entity list and HP fields.

Tools that help:

* `r0-elite-training/native_core/mumu_player_scan.c` — structure scan.
* `r0-elite-training/native_core/mumu_player_chain_scan.c` — DFS pointer chain.
* `tools/native_analysis/diff_builds.py` — relocate known anchors between builds.
* `tools/native_analysis/relocate_hooks.py` — 16-byte prologue search.
* `native_runner/live/device.py` — confirm the build/ABI/root before starting.

Acceptance test: `mumu_live_probe.py`'s summarizer must show elixir decreases,
hand transitions, moving entities, and HP changes for a full battle. If it does
not, the offsets are wrong; do not run the policy.

## Running the policy

Offline replay of captured frames (no device):

```bash
python tools/live/run_policy.py \
  --jsonl runs/live/frames.jsonl --dry-run \
  --match-config tools/native_analysis/match-configs/xbow-vs-hog26.json \
  --checkpoint .resource/RoyaleFish/models/B-general-v275.pt
```

Live, with the reader in its own process:

```bash
python tools/live/run_policy.py \
  --checkpoint .resource/RoyaleFish/models/B-general-v275.pt \
  --serial 127.0.0.1:16480 \
  --reader-cmd "adb -s 127.0.0.1:16480 shell su -c '/data/local/tmp/hcr_reader <pid> <libg_base> 50'" \
  --match-config tools/native_analysis/match-configs/xbow-vs-hog26.json
```

Use `--dry-run` for the first live match: it prints the exact taps and sends
nothing.

The deck must match the equipped deck. r0 frames carry `deck_card_ids`; Hasty
frames carry only hand indexes, so provide `--deck0` / `--deck1` (or a match
config) that matches the in-game deck.

## Observation fidelity

Implemented from the live frame:

* actor elixir, hand, deck, cycle;
* six fixed towers with observed HP when the reader exposes tower entities;
* troops/buildings with owner, card, world position, HP/max HP;
* a fully materialized 18x32 placement mask per hand slot;
* WAIT / WAIT_UNTIL_AFFORDABLE / PLAY_CARD legality.

Deliberately absent (unknown on the live client):

* opponent hand and elixir;
* combat/projectile/area-effect events;
* champion ability runtime and evolution runtime state;
* exact enemy deck (only observed cards).

The V4 tensorizer treats these as absent, not guessed. A spell's on-field area
effect has no character record and is skipped.

## Timing

A CPU forward measured 25–105 ms after warmup on this host. The policy turn is
250 ms, so a GPU is recommended for live play. The reader batches a whole frame
on-device; the tap pair is sent in one Android-side command with a 50 ms gap.
The game's own card deploy delay is roughly 1 s, which the model already
represents at its decision boundary.

## Risks

* Automating the official client violates Supercell's Terms of Service; use a
  throwaway account and expect a ban.
* Root plus a memory reader is the detectable part. ADB taps are ordinary
  input events.
* A wrong offset does not crash: it silently produces a plausible wrong board.
  Always run the coherence check before the policy.
* Client updates move offsets. Re-run the derivation after each update.
