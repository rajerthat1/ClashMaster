# ClashMaster quickstart

ClashMaster is a Clash Royale AI research workspace. It runs the **real ARM64 battle engine** from a patched offline copy of the Null's Royale client inside an Android emulator, exposes that engine through versioned Python contracts, and trains/evaluates a recurrent actor-critic policy ("V4") on the resulting matches.

Two clients are targeted, for different reasons:

| Client | Status | Why |
| --- | --- | --- |
| Null's Royale, offline APK (`nullsroyale.rel.free`) | Primary. Pinned **15.535.13**, research target **16.635.1** | Root + APK patch gives an exact, fast, headless engine with structured state |
| Official Supercell client (`com.supercell.clashroyale`) | Experimental (`native_runner/live/`) | Same policy, but state comes from a memory reader and actions from ADB taps |

No game code or game assets are tracked here: you supply your own Null's Royale APK (a copy may sit in your working tree as `nr_*.apk`, but APKs are git-ignored and not part of the release). See [operations/runbooks.md](operations/runbooks.md) for the setup chain.

## The one idea

```
                 native probe (libcrprobe.so, in the game process)
                          │  line-delimited JSON over 127.0.0.1:26789
                 NativeClashEnv  →  BattleEnvV1
                          │            │  FAIR ObservationV1 (2 players)
                          │            ▼
                          │      V4 tensorizer → V4 model → decoding → ActionV1
                          │            │
                          └────────────┴──► deploy / activate-ability / wait
```

The native engine is the only source of game dynamics. `BattleEnvV1` adds the model-facing protocol: immutable versioned observations, fair-state filtering, legal-action masks, execution timestamps, rewards and terminal state. Training, evaluation, the policy service and the desktop UI are all consumers of that one interface.

Decisions happen every **5 native ticks** (50 ms per tick, so a 250 ms turn). One decision can carry up to **two micro-actions**, each with its own `execute_offset_ticks`.

## Repository map

| Path | Contents |
| --- | --- |
| `native_runner/` | Environment, contracts, probe host API, training/eval, serving, desktop UI |
| `native_runner/probe/` | C++ probe injected into the game: hooks, layouts, telemetry, control server |
| `native_runner/training/v4/` | V4 tensorizer, model, decoding, IL, PPO, league, evaluation |
| `native_runner/live/` | Live official-client adapter (frames → observation → taps) |
| `crforge_bridge/` | Optional crforge/JVM stand-in for the native runner, plus vectorized rollout work |
| `tools/` | Analysis, build bring-up, perf research, dataset/demo generators |
| `checkpoints/` | Five shipped V4 checkpoints plus `manifest.json` hashes |
| `docs/` | The project's own guides (setup, architecture, training, validation, serving) |
| `native_runner/data/competitive*` | Compiled card/mechanic catalogs per competitive release |
| `docs/native/<build>/` | Probe layout evidence JSON (not prose docs) |

Local, git-ignored artifact directories you will see after running anything: `runs/` (matches, caches, checkpoints, logs), `builds/` and `build/` (APKs, probe binaries, bundles), `datasets/` (RoyaleAPI replay datasets), `evaluations/`, and `.resource/` (local clones of crforge / sandbox / RoyaleFish, all outside this repo's license).

## Start here, by goal

| Goal | Read |
| --- | --- |
| Understand how the pieces fit | [architecture/overview.md](architecture/overview.md) |
| Run a match in the desktop UI | [operations/runbooks.md](operations/runbooks.md) |
| Change the model, tensors, or action space | [training/policy-stack.md](training/policy-stack.md) |
| Train with replays (IL) or self-play (PPO) | [training/learning-and-serving.md](training/learning-and-serving.md) |
| Port a probe to a new game build | [architecture/native-engine.md](architecture/native-engine.md) |
| Run without a device (crforge) | [integrations/crforge-bridge.md](integrations/crforge-bridge.md) |
| Drive the official client | [integrations/live-client.md](integrations/live-client.md) |
| Know which tests to run | [operations/runbooks.md](operations/runbooks.md#7-tests-ci-and-release-checks) |

## Documentation map

- [architecture/overview.md](architecture/overview.md) — layers, processes, one decision, time model, compatibility surfaces.
- [architecture/native-engine.md](architecture/native-engine.md) — probe, control protocol, resident slots, attestation, APK build/install, version pinning.
- [training/policy-stack.md](training/policy-stack.md) — contracts, tensorizer, model heads, decoding, policy sessions, how to add a feature.
- [training/learning-and-serving.md](training/learning-and-serving.md) — replay dataset → IL cache → IL/PPO training, evaluation, serving, checkpoints.
- [operations/runbooks.md](operations/runbooks.md) — setup, build, clusters, tests/CI, release checks, common failures.
- [integrations/crforge-bridge.md](integrations/crforge-bridge.md) — the JVM stand-in and the vectorized performance track.
- [integrations/live-client.md](integrations/live-client.md) — the official-client sensor/actuator path.

The existing `docs/` tree is still authoritative for step-by-step setup and command syntax; these pages explain how and why the code is arranged and link into `docs/` where a procedure already exists.

## Invariants worth internalizing

1. **Versioned contracts, never ad-hoc dicts.** `native_runner/contracts.py` defines `observation.v1`, `action.v1`, catalog, attestation and episode-config schemas with content hashes. Changing a field is a schema change.
2. **No guessing about game state.** Unknown mechanics are recorded as `unknown_fields`/`unavailable` with provenance rather than filled in. The FAIR observation tier is the only tier `BattleEnvV1` will serve.
3. **Attestation gates play.** Episodes only start when the engine, probe, content and ruleset manifest match the pinned release (`native_runner/attestation.py`, `ruleset.py`).
4. **Offsets are build-pinned.** The probe's hooks and field offsets are generated per game build and verified against 16-byte prologues; `docs/native/` holds the evidence, not documentation prose.
5. **Checkpoints are strict.** A checkpoint records its `model_config` and catalog ids; loading refuses a config or competitive release it was not trained against.
6. **Code-hash-bound caches.** IL cache shards bind source and dataset hashes, so touching engine/env code invalidates them by design.

## Caveats for the current tree

The docs describe the source as it stands at commit `f636a96` plus the uncommitted working-tree changes below. Where the working tree and `HEAD` disagree, this wiki follows the working tree.

- **Untracked live adapter**: `native_runner/live/`, `tools/live/`, `native_runner/tests/test_live_adapter.py` and `docs/live_official_client.md`. Treat it as verified by its tests and design doc, not by history. See [integrations/live-client.md](integrations/live-client.md).
- **Uncommitted engine work**: `probe/live_tick_mailbox.inc` (a probe-side capture/action mailbox for the stock online client, wired into `game_state_step_hook` but not yet bound in Python — commands `live-arm`/`live-observe`/`live-play`), and `offline_build.py patch --probe-only` (load the probe without forcing the offline loader).
- **Uncommitted model plumbing**: `offline_agent.py` / `offline_duel.py` now bind the checkpoint's competitive release to the engine catalog and environment, so 16.635 checkpoints can run beside the 15.535 defaults.
- Running matches and training need hardware that CI does not have (MuMu/Android emulator, root ADB, CUDA, NCCL). Unit tests run without any of that; see the test matrix in [operations/runbooks.md](operations/runbooks.md#7-tests-ci-and-release-checks).
