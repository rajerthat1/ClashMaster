# Policy stack: contracts, tensors, model, decoding

The model-facing half of the environment. Everything here consumes only `ObservationV1` and emits `ActionV1` — no engine internals, no screenshots.

## 1. Contracts (`native_runner/contracts.py`)

`contracts.py` is the stable, NumPy/Gym-free boundary between the engine and everything downstream. It defines versioned immutable records, canonical JSON, and content hashing. Versions are explicit constants (`observation.v1`, `action.v1`, `card-spec.v1`, `raster.v2`, …).

Key records:

| Record | Role |
| --- | --- |
| `ObservationV1` | One side's full view at a tick: `time`, `players`, `towers`, `entities`, `events`, `raster`, `action_mask`, `terminal`, provenance |
| `EntityStateV1` | A troop/building/hero with status, shields, effects, attack/projectile/capture/visibility/ability/evolution runtime |
| `TowerStateV1`, `PlayerStateV1` | Six towers; hand, elixir, cycle, deck, tower troop |
| `ActionMaskV1` | Per-hand-slot placement masks (`row_major`, 32×18) plus affordability and legality |
| `ActionV1` | `wait`, `wait_until_affordable`, `play_card`, `activate_ability`; target kinds `none`/`grid`/entity/direction/area_center |
| `EpisodeConfigV1`, `EnvironmentConfigV1` | Decks, forms, tower troops, levels, seed, decision cadence, limits |
| `RasterV2` | 20 fixed channels (unit/HP/shield density per side, building/tower planes, legal-placement planes) |

Two design rules are load-bearing:

1. **FAIR visibility.** `ObservationTier` has `oracle`, `fair`, `human`, `rgb`; `BattleEnvV1` only serves FAIR. Events are filtered per owner (`_event_visible_to`), and hidden enemy hand/elixir/cycle become *public estimates* in `training/tracking.py` (`DeterministicPublicTracker`), never ground truth.
2. **Unknown is a value.** `SemanticProvenanceV1` and `SemanticEvidenceLevel` (`native_authoritative`, `native_derived`, `static_declared`, `not_applicable`, `unknown`) record how each runtime field was obtained. `CardSpecV1.unknown_fields` keeps unmodelled mechanics instead of guessing.

## 2. Environment (`native_runner/battle_env.py`)

`BattleEnvV1` wraps `NativeClashEnv` / `ResidentNativeClashEnv` in a PettingZoo-style parallel API **without importing PettingZoo**:

```python
obs, info = env.reset(episode_config)        # -> dict[int, ObservationV1], info
obs, rewards, terminated, truncated, info = env.step(
    {0: [ActionV1, ...], 1: ActionV1}, observation_owners=(0,)
)
```

`reset` also accepts `match_config=`, `seed=` and `options=`; passing both `config` and `match_config` raises.

- Two agents, owners `0`/`1`; each `step` may carry a sequence of micro-actions per owner.
- `_validate_compound_actions` enforces legality, `observe_all(FAIR)` builds the observations, `_rewards` applies potential-based shaping (`shaping_beta`) on top of win/loss, `_terminal` ends or truncates.
- A decision that arrives before its deadline is coerced into a `wait` marked `synthetic_until_existing_deadline`.
- `episode_config` must be passed to offline recomputation callers (`serve_policy`, the IL producer) so both decks' public trackers exist.

## 3. Tensorizer (`training/v4/tensorizer.py`, `tensors.py`, `config.py`)

`UniversalObservationTensorizerV4.tensorize(...)` turns an `ObservationV1` plus tracked public events and the card/mechanic catalogs into `UniversalSemanticBatchV4`. One batch row = one decision.

| Section | Shape (per `ModelConfigV4`) | Notes |
| --- | --- | --- |
| `match_scalars` | `[B, len(MATCH_SCALAR_NAMES)]` | Elixir, time, tower state, match scalars |
| `own_cards` | `[B, 8]` + features | Eight deck rows; `own_card_row` is an *address*, not an embedding |
| `opponent_cards` | `[B, ≤9]` | Eight zero rows plus one masked unknown row |
| `towers` | `[B, 6]` | Six fixed towers |
| `groups` / children | `[B, 48]` / `[B, 160]` | Battle groups plus causal children |
| `active_effects` | `[B, M]` | Effect semantics |
| `events` | `[B, 32]` | Recent public events |
| `relation_edges` | `[B, M]` | Token-to-token relations |
| `explicit_spatial_planes` | `[B, 14, 32, 18]` | Spatial layout is always `[B,C,H,W]` |
| `previous_action` | `[B, 2]` | Last candidate + target cell |
| `candidates` | `[B, 6]` | Four hand slots + two ability-controller slots |

`ModelConfigV4.__post_init__` freezes the invariants: 18×32 board, `decision_ticks=5`, `max_micro_actions=2`, 8 own / 9 opponent card rows, delay offsets `(0, 50, 100, 150, 200)` ms, known `card_runtime_feature_dim` profiles. It raises instead of tolerating a half-applied schema change.

`catalog.py` / `mechanics.py` own the vocabularies (cards, forms, archetypes, abilities, effect profiles). Catalog order is part of checkpoint compatibility.

## 4. Model (`training/v4/model.py`, `components.py`)

`UniversalCardPolicyV4` is a relation/spatial recurrent actor-critic:

- `encode_observation` → token memory (towers + cards + groups) through `RelationTransformerV4` (4 layers, 8 heads, 256-d), plus a spatial encoder (`learned scatter` + 3 residual blocks) and summaries for scalars, events, previous action.
- `advance_core` → one `LSTMCell(512 → 512)`; `RecurrentPolicyStateV4 {hidden, cell}` is the only persistent neural state.
- Heads: gate (WAIT/ACT), **candidate pointer** over the six candidate rows (not per-card heads), location logits `[B, 32, 18]`, delay-offset bin, second-micro-action `continue`, and `value`.
- `ActionSequenceV4` carries `gate`, `micro_action_count`, `candidate_index`, `candidate_uid`, `target_cell`, `delay_offset_bin` — note `validate()` requires nondecreasing delay bins and `-1` padding on inactive slots.

`ActionEvaluationComponentsV4` exposes per-head log-probs/entropies. PPO scoring and teacher forcing both use it; that is why sampling legality and scoring must stay separate paths.

## 5. Decoding and execution

`training/v4/decoding.py` resolves the sampled sequence into commands:

- `DELAY_POLICY_EARLIEST` / `DELAY_POLICY_SAMPLE`; `execute_offset_ticks = 1 + delay_ms/50`.
- Delay-aware legality (elixir at the *execution* tick) is gated by `ModelConfigV4.delay_aware_legality`; legacy checkpoints keep it `False` or offline/online behavior diverges.
- `native_actions.DecodedActionSequenceV4` is the boundary type handed to `BattleEnvV1`.

`training/v4/action_window.py::separate_micro_action_ticks` gives each tick-consuming micro-action its own engine tick inside the 5-tick window and reports `micro_action_tick_shifts` / `micro_actions_dropped`. **Any new decode path must call it** — the engine takes one action per seat per tick, so a collision silently leaves a card in hand. The crforge producer has the equivalent in `crforge_bridge/vector_actions.plan_window_ticks`.

## 6. Inference session

`training/v4/policy_session.py::PolicySessionV4` is the online wrapper used by the UI, the live runner and `serve_policy`:

- `load_policy_v4()` reads the checkpoint contract, builds the matching `ModelConfigV4`, and selects the competitive release from `catalog_ids` (fails closed on an unknown release).
- `decide(observation)` tensorizes → forward (optional CUDA graph, capacities 512 effects / 2048 relations) → decodes → records `previous_action` and recurrent state.
- Requires 4 Hz decisions (`decision_ticks=5`); it raises on a mismatched episode config.

## Extending a feature (recipe)

1. Name the feature in `config.py` (`*_FEATURE_NAMES`) — that fixes the width.
2. Add the value in `tensors.py` validators/padding and the matching `tensorizer._*` builder.
3. Encode it in `components.py`; if it changes a summary width, update `config.py` dimensions.
4. If it changes what is legal, touch `decoding.py` **and** the shadow/legality path that training scores.
5. Run `tests/test_training_v4_{tensorizer,model,decoding,padding,action_contracts}.py`; a mismatch between steps 1–3 fails loudly in `__post_init__` or `validate()`.
6. Any change to `native_runner/**.py` invalidates IL cache shards (they pin source hashes) — plan a rebuild.

## Gotchas

- **FP16 elixir.** `UniversalSemanticBatchV4.to_storage` keeps `candidates.elixir/cost` in fp32; packing 4.999 to 5.0 makes the decoder admit an unaffordable play.
- **Candidate UID is authoritative** on replay; `uid == -1` is padding, duplicates raise.
- **Cadence has two sources.** `EpisodeConfigV1.decision_hz` defaults to 10 Hz while V4 needs 4 Hz; producers must pass `decision_ticks=5`.
- **WAIT is exactly one policy turn** (5 ticks), not an arbitrary sleep, unless raised as a synthetic pre-deadline wait.

## Checks

`tests/test_contracts.py`, `test_battle_env*.py`, `test_training_v4_{tensorizer,model,decoding,padding,action_contracts,action_window,grouping,mechanics,deployment_timing}.py` — all CPU-only. See [learning and serving](learning-and-serving.md) for the training side.
