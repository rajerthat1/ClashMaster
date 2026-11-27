# crforge bridge: the second engine

`crforge_bridge/` lets [crforge](https://github.com/voonhous/crforge) (a Java re-implementation of the battle simulation) stand in for the native runner **without changing anything above the wire**. It answers the same line-delimited JSON commands `NativeClashEnv` sends on `127.0.0.1:26789`, so `BattleEnvV1`, `ObservationV1`, the evaluator and the trainer are unchanged.

The project's own README is explicit about its standing: *optional fallback and CI path; the native engine is the primary training path* (`crforge_bridge/README.md`). Treat everything here as throughput research and parity tooling, not the production loop.

## Two entry points, one contract

| Entry | File | Use |
| --- | --- | --- |
| TCP server | `crforge_bridge/server.py` (`python -m crforge_bridge --port 26789 --tables ~/.crforge/tables/16.635.8`) | a real endpoint, CI parity, external clients |
| In-process env | `native_runner/crforge_resident.py` (`CrforgeResidentEnv`) | bulk collection without sockets: subclasses `NativeClashEnv`, overrides `_request`/`close_transport`, calls the same `dispatch()` |

`server.dispatch(engine, text)` is the single command router; both entry points go through it, so parity is structural rather than a second implementation.

## What the bridge does and refuses

- `configure` → `LiveBattle`, `inject`/`deploy` → `LiveBattle.play`/`useAbility`, `observe*` → facts decoded from crforge's `Schema`.
- It does **not** produce a second `ObservationV1`. Encoding stays in Python above the wire, exactly as with the native probe.
- It emits the minimal v3 rich envelope: entities, towers, HP, targets, buffs, hand, elixir, crowns. Projectiles, area effects, fog, champion and evolution runtime are explicit `null`s with provenance — the bridge's rule is "no lying".
- Refusals are explicit `{"ok": false, "error": ...}`: `configure-native`, snapshot `create`/`restore`/`release`, `multi-*`, `env <id> close`, unknown verbs. `pause`/`resume`/`speed`/`render`/`touch` are receipts, not behavior.
- One tick per decision (`EnvConfig.decisionTicks=1`) so a seat's observation is current after every step; a play goes through `LiveBattle.play`, so crforge's own 20-tick `PLAY_DELAY_TICKS` applies (the real card delay).
- Battle ids, not seat ids: rows are matched to the battle's `WorldEntity` by `(side, row, x, y)`.

`crforge_bridge/jvm.py` starts exactly one JVM per process from `.resource/crforge/env/build/install/env/lib/*.jar` (headless, `convertStrings=False`) and refuses a second distribution — a hard constraint when embedding.

## Vectorized path (C3) and the batched learner (C4)

The point of crforge is that all 256 lanes live in one process, so the Python side can read zero-copy buffers instead of decoding one observation at a time.

| Layer | Module | What it does |
| --- | --- | --- |
| Facts | `vector_observation.py` | `VectorObservationBatch` for every lane: both seats' headers/cards/cycle/pending, six towers, placement-mask views, derived `age_ms`/`velocity` |
| Rows | `vector_rows.py` | `VectorGroupBuilder`, `VectorCardBuilder`, `VectorTowerBuilder`, `VectorSpatialPlaneBuilder`, `VectorScalarBuilder`, `VectorCandidateBuilder`, `VectorRelationBuilder` — batched section fills |
| Assembly | `vector_tensorize.py` | builds the exact `UniversalSemanticBatchV4` in tensorizer order (lanes = batch dim); `lane_batch()` slices one lane back |
| Actions | `vector_actions.py` | `ActionSequenceV4` → crforge engine indices (`0` wait, `1 + slot·rows·cols + row·cols + col`, then abilities), including `plan_window_ticks` |
| Forward | `fast_forward.py` | fp16 + CUDA graphs per padded row count, one LSTM state table, `verify()` numerical gate |
| Serving | `decision_server.py` | one forward for many producers: live-width and row bucketing, identity/sampling/state/weight-push gates, `DecisionClient.push_weights` |
| Learning | `rollout.py`, `training.py`, `league.py` | `PotentialShapingReward` (mirrors `BattleEnvV1._rewards`), `CrforgeRolloutStore`, one persistent AdamW with `carry|reset|wave` state policy, league JSON with an optional crforge deck block |
| Diagnostics | `illegal_actions.py` | binds a refused action to lane, seat, tick, card and refusal code from cumulative observation headers |

**Equivalence is the whole safety story.** Every vectorized section ships an oracle: `reference_observation`, `reference_action_mask`, `reference_entities`, `oracle_groups`, plus `tests/test_vector_*.py` and the `tools/crforge/batched_*.py --verify-lanes` benchmarks. A new vectorized section is only correct when it matches the stock per-lane tensorizer on the same facts.

Performance numbers quoted in `crforge_bridge/README.md` (0.0317 ms/lane facts, 0.0539 vs 0.2207 groups, cards 44.5x, relations 5.30x) come from recorded benchmark artifacts under `runs/crforge-c3`, not from a run you can reproduce on any host; regenerate them before trusting them.

`tools/crforge/` is the experiment bench on top of the package: the C4 producer (`self_play.py`), league runner, decision-server/multiproc drivers, resident parity, encoder and cadence spikes, `cloud_capacity.py`, `cpu_half_micro.py`, `learning_horizon.py`. `tools/crforge/s51_run.sh` is a recorded session driver.

## Setup

```bash
# once, in the crforge checkout
cd .resource/crforge
./gradlew :env:installDist -Pcrforge.assetSource=cdn
./gradlew -q :tables:gameTables -Pcrforge.assetSource=cdn

# in this workspace
.venv/bin/pip install jpype1 gymnasium pettingzoo
.venv/bin/python -m crforge_bridge --port 26789 --tables ~/.crforge/tables/16.635.8
```

Then `NativeClashEnv()` (or `BattleEnvV1`, which detects the crforge endpoint and verifies `CrforgeAttestationV1` against `native_runner/versions/16.635.1.json` before the first mutation) talks to it unchanged.

## Gotchas

- Every crforge/vector test **skips silently** when jpype, the installed distribution or the tables are missing. CI does not run them at all — a prepared host is the only place they mean anything.
- Table/client versions move attestation digests: the bridge defaults to `16.635.8` while `Catalog.data_versions` accepts `16.635.3`/`16.635.8`.
- `crforge_bridge/engine.py` caches captures per `(generation, tick, sequence, done)` and only re-reads refusal headers on observed ticks; adding a field or command means checking that key.
- The bridge is a single-client server; a resident `CrforgeResidentEnv` in the same process is a different lifecycle (one engine, one battle).
- Do not "fix" an unavailable field by guessing it — the envelope's provenance status is what downstream code uses to decide whether a feature is real.

## Checks

`pytest crforge_bridge/tests -q` on a prepared host: `test_bridge.py` (catalog packing, attestation, configure/observe, short episode, native `dataGlobalId`, TCP dispatch), `test_resident.py`, `test_vector_{observation,rows,tensorize,actions}.py`, `test_rollout.py`, `test_league.py`, `test_decision_server.py`, `test_fast_forward.py`, `test_promotion_gate.py`, `test_illegal_actions.py`.
