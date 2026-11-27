# Native engine: probe, protocol, and offline build

This page covers the bottom of the stack: the code that runs *inside* the game process, the protocol the host uses to drive it, and how a user-supplied APK becomes a runnable offline engine.

## The probe

`native_runner/probe/cr_replay_probe.cpp` (plus `logical_funcs.inc`, `logical_offsets.inc`, `logical_vtables.inc`, and the per-subsystem `*_runtime_telemetry.inc` files) compiles to `libcrprobe.so`. It is loaded into the Null's Royale process by the patched APK, finds the `libg.so` base, and installs inline hooks.

- Every hook is verified against a **16-byte prologue fingerprint** before it is written (`verify_native_hook_table`, `probe_hook.h`); a build mismatch fails the hook install instead of corrupting the process.
- Build-pinned addresses live in generated headers: `build_16_635_1_layout.h`, `build_16_635_1_layout_x86.h`, and the smaller `*_layout.h` files for touch, combat, phase, visibility, character state, tower troop, etc.
- The probe exposes structured observations, rich telemetry, combat/phase/visibility event rings, snapshot/restore, the touch interceptor, and the control server. It never interprets screenshots for the offline path.

Layout evidence is produced by tools in `tools/native_analysis/` (`cr_elf.py`, `relocate_hooks.py`, `diff_builds.py`, `match_vtables.py`, `gen_layout.py`). The JSON under `docs/native/16.635.1/` (`offset-diff.json`, `hook-relocation.json`, `field-offsets.json`, `champion-layout.json`, `build.json`) is **probe input data, not documentation** — keep it in sync with the headers when porting a build.

## Control protocol

TCP on loopback, line-delimited JSON, one command per connection: the probe reads one line, dispatches, writes one JSON object plus `\n`, and closes (long-lived sessions and the batch channel are the exceptions). The host refuses responses over 16 MiB (`MAX_RUNNER_RESPONSE_BYTES`).

Verified command families in `cr_replay_probe.cpp` and `cr_native_env.py`:

| Command | Purpose |
| --- | --- |
| `status`, `attest` | Client/build identity; `RunnerAttestationV1` or `CrforgeAttestationV1` |
| `configure <json>`, `configure-native <json>` | Create a headless or native-render match |
| `reset`, `step N`, `advance-native N` | Advance time; headless is exact, native-render respects the scene clock |
| `observe`, `observe-rich`, `observe-atomic` | Raw state, rich telemetry, and atomic capture variants |
| `inject <json>`, `deploy`, `activate-ability` | Submit card plays / abilities |
| `replay-schedule-*` | Schedule card/ability execution for replay reconstruction |
| `pause`, `resume`, `speed`, `render on/off/status`, `touch status` | Presentation and timing controls |
| `snapshot-create`, `restore <handle>`, `release-snapshot` | In-process state snapshots |
| `multi-status`, `multi-stop`, `multi-perf`, `env <id> <cmd>` | Resident multi-match slots in one process |
| `session-v1`, `session-close`, `batch-v1`, `batch-zlib-v1`, `batch-fast-v1`, `batch-fast-zlib-v1` | Long-lived and batched transports |
| `champion-diag`, `content-window` | Diagnostics used while porting a build |

Host reachability is `adb -s <serial> forward tcp:<host_port> tcp:<guest_port>`, set up by `native_runner/start_offline.ps1` (defaults 26789 → 26789, `CR_CONTROL_PORT` / `CR_GUEST_CONTROL_PORT`). The cluster scripts forward `base+i` → `26789+i` per engine.

Constants that shape everything above (`CR_CONTROL_HOST`, `CR_CONTROL_PORT` from `.env`; `FIRST_PLAYABLE_TICK=90`; the 20-tick live command age) are defined at the top of `cr_native_env.py`.

### Python bindings

- `NativeClashEnv` — one engine, one connection at a time. `create_match`, `create_native_match`, `step`, `observe*`, `deploy`, `activate_ability`, `snapshot*`, `set_render`, `pause/resume`.
- `ResidentNativeClashEnv(env_id)` — same surface, prefixes commands with `env <id>`, and adds `multi_status` / `multi_perf` for its lane group.
- `ResidentBatchChannelV1` (`resident_batch_channel.py`) — multiplexes up to 16 environments over one socket with a binary frame format (`CRBH` handshake, `CRBQ` request, `CRBS` response), up to 8 actions per env, optional zlib, and optional `CRTF` training capture. Use it for bulk collection; do not mix it with plain control commands on the same connection.

### Live tick mailbox (working tree only)

The probe in the current working tree includes `probe/live_tick_mailbox.inc`, wired into `game_state_step_hook`, for a third mode where the **stock online client** owns the `GameStateManager` and the probe only observes it. Commands: `live-arm`, `live-disarm`, `live-observe-status`, `live-observe`, `live-play OWNER HAND X Y CARD DUE`, `live-ability OWNER OBJ SECONDARY DUE`. It captures an exact post-step snapshot every 5 ticks and requires the host to feed every capture in order. No Python binding exists for these commands yet (`native_runner/` has none), so treat this as an in-progress capability rather than part of the host API.

## Offline build and install

No game binary is tracked: the user supplies the unmodified APK (any APK in the working tree is git-ignored).

```
check_original_game.ps1 ─► build_offline_engine.ps1 ─► install_offline_engine.ps1
   validates APK, Android 12,      compiles probe, patches APK,     verifies receipt, isolates
   downloaded resources            signs, writes .build.json        network, backs up app data
```

- `native_runner/offline_build.py` is the Python core: it verifies the input APK by whole-file SHA-256 **or** by the pinned `libg.so` payload hash (Null's re-signs every download, so whole-file hashes differ), validates the runtime update against `native_runner/data/competitive/manifest.json`, copies `assets/` and the engine into a workspace, and applies the smali patch that loads `crprobe`.
- The build receipt (`<apk>.build.json`, schema `firstlight-offline-build.v1`) records the APK, probe, and engine hashes. The installer refuses an APK whose receipt does not match, and keeps backup state so a failed start restores the previous install.
- The working tree adds `patch --probe-only`, which loads `crprobe` but keeps the original online-loader gate — i.e. instrument the stock online client instead of forcing the offline path. `build_offline_engine.ps1` does not pass it yet.
- `native_runner/supported_engine.json` pins package `nullsroyale.rel.free`, version `15.535.13` / code `150535013`, `arm64-v8a`, the APK and `libg.so` hashes, the resource directory name, and the pinned runtime content version `15.535.86`.

## Attestation: what is allowed to run

`attest` returns a `RunnerAttestationV1` containing package/ABI, engine and probe hashes, content fingerprints, per-hook results, capabilities, and `production_ready`. `native_runner/attestation.py` verifies it field-by-field; `native_runner/ruleset.py` builds a `RulesetManifestV1` that names every file contributing to identity. A manifested episode cannot `reset` without a matching attestation, and evidence compatibility is deliberately narrower than production compatibility.

Why it exists: training data is only meaningful if the engine, probe, and content are exactly the ones the checkpoint expects. Anything else is silently a different game.

## Version story

| Build | Role | Evidence |
| --- | --- | --- |
| 15.535.13 | Supported/default; every script and `supported_engine.json` assume it | `native_runner/supported_engine.json`, `build_offline_engine.ps1` |
| 16.635.1 | Research target, ported but not promoted | `docs/native/16.635.1/`, `native_runner/versions/native-16.635.1*.json`, `native_runner/releases/16.635.1.json`, `native_runner/data/competitive-16.635/` |

On 16.635.1 the rich/combat telemetry hooks are compiled out (guarded by `CR_PROBE_TARGET_16_635_1`), so observations are partly degraded and some semantic fields report `unavailable`. The replay wire id also differs (`ct` 86 vs 153) and is auto-selected from `status.clientVersion`.

## Fragile points

- **Offsets are the product.** A hook installed at the wrong address produces a plausible wrong board rather than a crash. After any client update, re-derive anchors with `tools/native_analysis/` and regenerate the header; `test_probe_build_layout.py` re-reads every emitted prologue from the reference `.so` so an unverified offset cannot ship.
- **Python/C++ struct drift.** The batch channel header layout, receipt fields, and response bounds are mirrored on both sides; change them together.
- **Attestation/receipt coupling.** Changing a manifest field, hash, or receipt key breaks `production_ready` and install rollback.
- **Session state.** Plain control commands and `batch-v1`/`session-v1` cannot interleave on one socket; close the batch before issuing control commands.
- **Script lifecycle.** `start_offline.ps1` and the cluster scripts own VM start, adb forward, PID discovery, and cleanup; they assume a dedicated MuMu instance. Do not run the UI and a cluster against the same VM.

## Checks

- `python -m pytest native_runner/tests/test_probe_build_layout.py native_runner/tests/test_linux_build_scripts.py -q`
- `python -m pytest native_runner/tests/test_resident_batch_channel.py native_runner/tests/test_resident_multimatch.py -q`
- `python -m pytest native_runner/tests/test_offline_build.py native_runner/tests/test_offline_install.py native_runner/tests/test_attestation.py native_runner/tests/test_native_hook_table.py -q`
- C++ layout tests need `g++`; tests with unavailable prerequisites skip rather than fail.

See [operations](../operations/runbooks.md) for the actual build/install/cluster commands.
