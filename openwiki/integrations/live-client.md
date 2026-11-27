# Live official client (experimental)

A separate sensor/actuator path for running a V4 checkpoint on the **official Supercell client** (`com.supercell.clashroyale`) instead of the patched offline Null's client. It does not train, and it shares no model format with the readers: RoyaleFish/V4 weights load only under `native_runner/training/v4`.

```
reader (root, read-only)  ->  LiveFrame  ->  ObservationV1  ->  V4 policy (torch)
        ^                                                          |
        |                                                          v
  official client  <--------- adb input tap <--------- decoded ActionV1
```

Design doc with the full rationale: `docs/live_official_client.md`. This page explains how the code is arranged and what to check before trusting it.

## Components

| Piece | Path | Role |
| --- | --- | --- |
| Device diagnostic | `native_runner/live/device.py`, CLI `tools/live/probe_device.py` | Checks serial, ABI, client build, and whether root/`adb root` exists |
| Reader build | `tools/live/build_reader.py` | Compiles Hasty-CR's read-only `hcr_reader.c` for `arm64-v8a` and `x86_64` into `build/live/` |
| Frame parsers | `native_runner/live/frames.py` | `parse_r0` / `parse_hasty` / `parse_frame` → `LiveFrame` |
| Frame → observation | `native_runner/live/observation.py` | `build_live_observation`, `LiveObservationConfig` → FAIR `ObservationV1` |
| Tap planner / sender | `native_runner/live/taps.py` | `ScreenLayout`, `taps_for_action(s)`, `AdbTapper` |
| Live runner | `native_runner/live/runner.py` | `LivePolicyRunner`: warm up, one `PolicySessionV4.decide` per turn, send taps |
| End-to-end CLI | `tools/live/run_policy.py` | JSONL replay (`--dry-run`) or live serial |
| Tests | `native_runner/tests/test_live_adapter.py` | 10 tests replaying synthetic r0 and Hasty frames, including a real V4 forward |

`native_runner/live/__init__.py` deliberately exports only the reader/observation/tap layer — it does not import torch. `runner.py` is the only module that does.

## Hard prerequisite: root, or no live path

The reader calls `pread()` on `/proc/<pid>/mem` of the game process. That needs either the app running as root or `adb root` (MuMu's ADB Root toggle). A retail phone image has neither, so `device.py` reports `root: none` and memory reading is impossible — ADB taps alone are not enough, because the policy has no state. This is why MuMu is the shortcut for the live path, not a different client.

The reader binary only reads memory; it never writes, never ptraces, and never sends input (`tools/live/build_reader.py` docstring).

## Frame formats

`frames.py` normalizes two shapes:

* **r0 / `mumu_live_probe`**: `{"battle_active", "game_tick", "players": [...], "entities": [...]}`, world-unit coordinates, and `deck_card_ids` on players.
* **Hasty / `hcr_reader`**: `{"t": tick, "me": [side, elixir_raw, hand, cycle], "u": [[...], ...]}` — hand **indexes** only, so the deck must be supplied out of band.

Details that matter:

- World coordinates stay in game units (18 000 × 32 000 for the 18×32 arena, 1 000 per tile); the V4 tensorizer does its own scaling.
- `elixir_raw` is divided by 10 000 and clamped to 0..10 (`LivePlayer.elixir`).
- `TOWER_LAYOUT` mirrors `battle_env.TOWER_LAYOUT` in world units; `normalize_frame` fills the six towers with full default HP when the reader omits them. That is visibly optimistic, not observed state.
- Elixir is only exposed for the actor; the opponent's hand and elixir are unavailable by design.

Note: the `frames.py` docstring points at `tests/test_live_frames.py`, which does not exist — the tower-layout assertion actually lives in `test_live_adapter.py`.

## Observation fidelity

From a live frame the adapter materializes:

- actor elixir, hand, deck, cycle;
- the six fixed towers (observed HP when the reader exposes tower entities, default HP otherwise);
- troops/buildings with owner, card, world position, HP/max HP;
- a fully materialized 18×32 placement mask per hand slot;
- `WAIT` / `WAIT_UNTIL_AFFORDABLE` / `PLAY_CARD` legality.

Deliberately absent (unknown on the live client): opponent hand and elixir, combat/projectile/area-effect events, champion ability and evolution runtime state, and the exact enemy deck. The V4 tensorizer treats these as absent rather than guessing — so a live observation is a strict subset of a native one.

## Actuation limits

`taps.py` maps a decoded action to a hand point plus an arena cell using 1080×1920 fractional calibration (`ScreenLayout.from_size`), and `AdbTapper.send` batches the pair into one device-side command with a 50 ms gap. Two consequences:

- `ACTIVATE_ABILITY` cannot be tapped: it needs a screen point for the source entity, which the readers do not expose. The planner refuses it explicitly.
- The hand and arena fractions are hard-coded, so a layout change needs a new calibration.

## Version pinning is the real work

Both upstream readers are pinned to older client builds (r0 → 16.402.002, Hasty → 16.402.012, per `docs/live_official_client.md`) while the installed official client is newer. The parsers and the V4 adapter are version-agnostic; the pointer chain, `libg` base and field offsets are not. Before a live read means anything you must re-derive:

1. `libg` base and build id (`tools/native_analysis/cr_elf.py info` on the pulled `libg.so`).
2. The manager RVA / battle global.
3. Player record fields: hand vector, cycle vector, elixir, deck count.
4. Entity list and HP fields.

Useful tools: `tools/native_analysis/{diff_builds,relocate_hooks}.py`, `entity_hp_probe.py`, `capture_content_window.py`, and `native_runner/live/device.py` for the pre-flight. The acceptance test is the reader summarizer showing elixir decreasing, hand transitions, moving entities and HP changes across a full battle. **A wrong offset does not crash — it produces a plausible wrong board.** Always run the coherence check before the policy.

The build script warns about the same thing: a reader compiled for the wrong build compiles and runs, and reads the wrong addresses.

## Running

```bash
# offline replay of captured frames, no device
python tools/live/run_policy.py \
  --jsonl runs/live/frames.jsonl --dry-run \
  --match-config tools/native_analysis/match-configs/xbow-vs-hog26.json \
  --checkpoint .resource/RoyaleFish/models/B-general-v275.pt

# live, reader in its own process
python tools/live/run_policy.py \
  --checkpoint .resource/RoyaleFish/models/B-general-v275.pt \
  --serial 127.0.0.1:16480 \
  --reader-cmd "adb -s 127.0.0.1:16480 shell su -c '/data/local/tmp/hcr_reader <pid> <libg_base> 50'" \
  --match-config tools/native_analysis/match-configs/xbow-vs-hog26.json
```

Use `--dry-run` first: it prints the exact taps and sends nothing. The deck in the match config must equal the in-game equipped deck (r0 frames can carry card ids; Hasty frames cannot).

The runner warms up the tensorizer below `FIRST_POLICY_DECISION_TICK`, runs one recurrent decision per policy turn, and keeps the episode's recurrent state. A CPU forward measured 25–105 ms after warmup on the development host, against a 250 ms turn.

## Status and risks

- **Repository status**: `native_runner/live/`, `tools/live/`, `native_runner/tests/test_live_adapter.py` and `docs/live_official_client.md` are untracked at commit `f636a96`. The design doc reports the components working and the 10 adapter tests passing; history does not yet record it.
- Automating the official client violates Supercell's Terms of Service. Use a throwaway account and expect a ban.
- Root plus a memory reader is the detectable part; ADB taps are ordinary input events.
- Client updates move offsets. Re-derive after every update.

## Checks

`python -m pytest native_runner/tests/test_live_adapter.py -q` runs without a device (synthetic frames + a V4 forward). CI does **not** run it; see [operations](../operations/runbooks.md#7-tests-ci-and-release-checks).
