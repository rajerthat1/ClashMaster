# Architecture overview

ClashMaster is layered so that game-specific, build-specific code stays at the bottom and everything above it talks in versioned contracts. This page is the map; the two architecture sub-pages go deeper on the engine and the policy stack.

## Layers

| Layer | Entry points | Responsibility |
| --- | --- | --- |
| Probe (C++, in the game process) | `native_runner/probe/cr_replay_probe.cpp`, `probe_hook.h`, `build_*_layout.h` | Hook the engine, expose state, execute actions, serve the control port |
| Transport / host API | `native_runner/cr_native_env.py`, `resident_batch_channel.py`, `resident_batch_vector.py` | `NativeClashEnv` / `ResidentNativeClashEnv`, command encoding, responses, attestation |
| Battle environment | `native_runner/battle_env.py`, `contracts.py`, `arena.py`, `match_factory.py`, `ruleset.py` | `BattleEnvV1.reset/step`, FAIR observations, masks, execution timing, rewards, terminal |
| Semantic data | `native_runner/data/competitive*/`, `card_specs.py`, `card_logic.py`, `effect_catalog.py`, `projectile_catalog.py` | Card/mechanic/effect catalogs keyed to a competitive release |
| Policy | `native_runner/training/v4/{tensorizer,model,config,tensors,decoding}.py` | Observation → tensors → distribution → `ActionV1` |
| Learning | `training/v4/{cache,cache_builder,imitation,ppo,distributed_ppo,train_*}.py` | Replay reconstruction, IL, PPO, leagues, matchmaking |
| Serving / evaluation | `training/v4/{evaluate,serve_policy,policy_session,offline_agent,offline_duel}.py` | Stateful inference, match runs, metrics |
| Alternative engine | `crforge_bridge/`, `native_runner/crforge_resident.py` | crforge/JVM stand-in speaking the same commands, plus vectorized rollout research |
| Alternative sensor | `native_runner/live/`, `tools/live/` | Official-client frames → `ObservationV1` → ADB taps |
| Desktop UI / tools | `native_runner/user_interface.py`, `tools/` | Match setup, deck editor, replays, analysis and bring-up scripts |

## Processes and transport

The normal deployment is one Android guest and one host process per engine:

```
host                                   Android guest (MuMu / emulator / AVD)
────                                   ────────────────────────────────────
NativeClashEnv ── TCP 127.0.0.1:26789 ──(adb forward)──► probe control server
                                                          └─ game process / libg.so
```

- The probe binds `127.0.0.1:<port>` in the guest and answers exactly **one** line-delimited JSON command per connection, then closes (`cr_native_env.py`; command list in [native-engine.md](native-engine.md#control-protocol)).
- `adb forward tcp:<host> tcp:<guest>` is set up by `native_runner/start_offline.ps1` for a single engine, or per-engine by the cluster scripts.
- **Headless mode** (`configure`) attaches to a detached battle manager so `step N` is exact and fast. **Native-render mode** (`configure-native`) lets the client's own battle scene own and draw the manager, which is what the UI uses for live play.
- **Resident slots**: one guest process can host up to 16 battle slots (`env <id> <command>`); `ResidentNativeClashEnv(env_id)` and `ResidentBatchChannelV1` batch multiple lanes over one connection using a binary frame format (`CRBH`/`CRBQ`/`CRBS`). The Windows cluster script scales to 1–24 engines × 1–8 slots.
- Two drop-in substitutes exist on the same seam: `crforge_bridge` (JPype/JVM, wire or in-process) and, for the official client, the live adapter, which replaces the whole left-hand column with a memory reader plus `adb shell input tap`.

## One decision

1. `BattleEnvV1` reads native state and publishes the acting side's `ObservationV1` under the FAIR visibility contract (enemy hand, elixir and cycle are hidden).
2. `UniversalObservationTensorizerV4` combines that observation with tracked public events and the card/mechanic catalogs into `UniversalSemanticBatchV4`.
3. `UniversalCardPolicyV4` encodes entity/relation/spatial sections, advances the LSTM memory, and emits gate, candidate, location, delay, second-action and value outputs.
4. `decode_action_sequence_v4` resolves that into `ActionV1` requests; `action_window.separate_micro_action_ticks` de-conflicts execution ticks; `BattleEnvV1.step` submits them and records the outcome.

A single `PolicySessionV4.decide(...)` call performs steps 2–4 for one owner and keeps the recurrent state, so calling it out of order or twice per observation breaks the episode.

## Time model

| Quantity | Value | Source |
| --- | --- | --- |
| Native tick | 50 ms (`DEFAULT_TICK_MS`) | `contracts.py` |
| Match warmup / first playable tick | 90 | `cr_native_env.FIRST_PLAYABLE_TICK` |
| First policy decision | tick 90 (`FIRST_POLICY_DECISION_TICK`) | `training/v4/expert.py` |
| Decision cadence | every 5 ticks (`POLICY_DECISION_TICKS`) = 4 Hz | `expert.py`, frozen in `ModelConfigV4` |
| Micro-actions per decision | ≤ 2 | `ModelConfigV4.max_micro_actions` |
| Delay bins | 0/50/100/150/200 ms → `execute_offset_ticks = 1 + bin` | `config.py`, `contracts.ActionV1` |
| Engine actions per seat per tick | 1 | `probe` command loop; enforced by `action_window.py` |

`EpisodeConfigV1.decision_hz` defaults to 10.0 in the contract module, but V4 requires 4.0 and `PolicySessionV4` refuses anything else. Anything that constructs an `EpisodeConfigV1` for V4 must pass `decision_ticks=5`.

## Compatibility surfaces

These are the things that break silently if changed casually:

| Surface | Where | Failure mode |
| --- | --- | --- |
| `observation.v1` / `action.v1` field sets | `contracts.py` | Downstream consumers decode wrong fields; content hashes change |
| Raster v2 channels (20 planes) | `contracts.RASTER_V2_CHANNELS` | Tensorizer/training mismatch |
| Catalog order and card/form ids | `native_runner/data/competitive*/model_catalogs.json.gz`, `training/v4/catalog.py` | Checkpoint loads but logits index the wrong card |
| `ModelConfigV4` widths | `training/v4/config.py` + `tensors.py` validators | Loud failure by design — see the extension recipe |
| Competitive release binding | `native_runner/competitive_release.py`, `releases/*.json` | Evaluator refuses mixed releases |
| Probe layout / hook offsets | `probe/build_*_layout.h`, `docs/native/<build>/` | Silent wrong board on the live path; hook install refuses mismatched prologues |
| Build receipts (`.build.json`) | `native_runner/offline_build.py`, `offline_install.py` | Install refuses or rolls back |

## Artifacts

| Artifact | Produced by | Consumed by |
| --- | --- | --- |
| Parquet replay dataset (`royaleapi-battle-replay.v1`) | `datasets/royaleapi`, `tools/make_demo_data.py` | `cache_builder`, `royaleapi_replay.py` |
| IL cache (`.cril.zst` shards + `index.json`) | `training/v4/cache_builder.py` | `train_imitation_cache.py` |
| Checkpoint (`.pt`, `universal-card-actor-critic-checkpoint.v4`) | IL/PPO trainers | evaluation, serving, UI, PPO resume |
| `TrainingReplayV1` (`speed-xbow-training-replay.v1`) | `training/replay_archive.py`, `royaleapi_replay.py` | `replay_viewer.py`, UI replay page |
| Engine readiness / topology JSON | `launch_ppo_engine_node.sh`, `build_ppo_multinode_topology.py` | PPO trainer |
| Evaluation outputs (`identity.json`, `matches.jsonl`, `summary.json`) | `training/v4/evaluate.py` | Humans comparing models |

## Where changes belong

| You want to… | Start in | Also touch |
| --- | --- | --- |
| Add/rename an observation field | `contracts.py` | `battle_env.py` producers, `tensorizer.py`, cache hashes |
| Add a model feature | `config.py` `*_FEATURE_NAMES` | `tensors.py`, `tensorizer.py`, `components.py` |
| Change action space or decoding | `contracts.ActionV1`, `training/v4/decoding.py` | `native_actions.py`, `action_window.py`, `producer.py` |
| Change rewards | `BattleEnvV1._rewards` in `battle_env.py` | `training/v4/king_activation_reward.py`, `async_cluster_self_play.py` (hog/timing shaping), `learning.py` (GAE) |
| Support a new game build | `tools/native_analysis/gen_layout.py` | `probe/` layouts, `versions/`, `releases/`, `docs/native/<build>/` |
| Bypass the device | `crforge_bridge/server.py` | `native_runner/crforge_resident.py`, `crforge_bridge/vector_*` |
| Change how the UI launches things | `native_runner/user_interface.py` | `start_offline.ps1`, `offline_agent.py`, `offline_duel.py` |
