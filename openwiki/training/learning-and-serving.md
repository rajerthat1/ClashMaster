# Learning and serving: IL, PPO, evaluation

Two learning paths share one policy stack ([policy stack](policy-stack.md)) and one checkpoint format.

## Data in: RoyaleAPI replays

Expert data is a Parquet dataset of replay rows: `replay_tag` plus a JSON `payload_json` with schema `royaleapi-battle-replay.v1` (decks, forms, tower troops, 20 Hz action times, sides, coordinates, replay end time). `native_runner/royaleapi_replay.py` reads it; `datasets/royaleapi/` is the local (gitignored) dataset root.

`training/v4/dataset.py::screen_replay_payload` runs a cheap preflight before any engine work and returns `ReplayPreflightV4` with a rejection reason: `no_expert_actions`, `missing_or_mixed_data_i`, `action_before_first_decision`, `more_than_two_actions_in_window`. Rejecting here is what keeps the cache builder from paying engine time on unusable replays.

## Imitation learning

```
replays/*.parquet
  -> producer.produce_il_replay_batch()   re-simulates in BattleEnvV1, two owner tensorizers
  -> cache.write_il_cache_shard()         <shard>.cril.zst + <shard>.json
  -> cache.write_il_cache_index()         index.json
  -> train_imitation_cache.py             checkpoint-step-NNNNNNNN.pt
```

- `training/v4/cache_builder.py` is the CLI (`runs/demo/dataset runs/demo/cache --ports 26789 ...`); it names outputs `part-XX-shard-NNNNNN` and writes `.failures.json` for rejected replays.
- Schemas: `v4-il-model-input-cache-shard.v1`, `...-summary.v1`, `...-index.v1`.
- The shard contract pins **everything that could change the numbers**: `source_sha256` of every `.py`/`.cpp`/`.h`/`.s` under `native_runner/`, `data/competitive/*.json` hashes, dataset snapshot (name/bytes/mtime), `model_config_sha256`, `decision_ticks=5`, fp16 storage, gamma. Editing any of those invalidates existing shards by design.
- `training/v4/imitation.py` computes the supervised loss; `expert.py` builds the teacher-forced timeline (`FIRST_POLICY_DECISION_TICK = 90`, `POLICY_DECISION_TICKS = 5`).
- Launcher: `launch_stateful_il.sh` (`IL_WORLD_SIZE`, `CR_IL_SHARDS_PER_RANK`), or `train_imitation_cache.py --index ... --train-tags ... --output-dir ... [--resume ...]`.

Because iteration is expensive, the practical loop is: screen → cache a small slice → train → evaluate → only then re-cache the full dataset.

## PPO self-play

`training/v4/train_ppo_self_play_cluster.py` is rank 0 (the learner). One wave looks like:

1. **Assign** — `matchmaking.DeckMatchmakerV4` mixes decks and matchups (current vs current, current vs IL, current vs history); `league.PolicyLeagueV4` supplies PFSP weights. The PFSP weight is `0.05 + 0.7·near_even + 0.3·weakness` on the learner win rate (`league.py`), i.e. prefer opponents near even and known weaknesses.
2. **Collect** — engine workers (`async_cluster_self_play.py` via `remote_ppo_worker_server.py`) run several `BattleEnvV1` lanes per guest, coalesce decisions into one GPU forward (`CR_PPO_INFERENCE_BATCH_WAIT_MS`), and return rollouts + per-match reports (hog deployments, first-hog timing, openings).
3. **Learn** — `learning.generalized_advantage_estimate` → `distributed_ppo.distributed_ppo_update_v4`, optional `ppo_expert_bc` auxiliary loss (BC on expert windows, optional hog-gate supervision).
4. **Reward shaping** — win/loss plus potential-based shaping (`BattleEnvV1._rewards`), king-activation attribution (`king_activation_reward.py`, reward-only, never policy input), and hog/opening shaping parameters in `async_cluster_self_play.py`.

Coordination files (all JSON, all checked before use):

| File | Written by | Consumed by |
| --- | --- | --- |
| `engine-node-ready.json` per node | `launch_ppo_engine_node.sh` | `build_ppo_multinode_topology.py` |
| `topology.json` | `build_ppo_multinode_topology.py` | `launch_ppo_multinode_training.sh` |
| `deck-pool.json` | `build_ppo_deck_pool.py` | producer / matchmaker (`CR_PPO_DECK_POOL`) |
| league JSON (`v4-ppo-policy-league.v1`) | trainer | matchmaking |

Readiness rules are strict: contiguous global engine indices, matching `attestation_digest`, uniform `decision_ticks`, non-empty rank→engine assignment; a partial assignment fails rather than silently shrinking the batch. The training launcher takes a single-trainer `flock`.

Metrics default to offline W&B (`training/tracking.py`); resume with `CR_PPO_RESUME_CHECKPOINT`.

## Checkpoints

`training/v4/checkpoint.py`, schema `universal-card-actor-critic-checkpoint.v4`:

- `contract` — policy/observation/action versions, `model_config` (the full `ModelConfigV4`), `catalog_ids` (which competitive release the vocabularies came from).
- Model / optimizer / scheduler / grad-scaler state, `update_step`, `training_stage`, `gamma_per_decision`, `gae_lambda`, PPO temperatures, torch + CUDA RNG, `extra`, `checkpoint_id`.
- Writes are atomic (temp file + rename).

`load_policy_v4` rebuilds the config **from the checkpoint**, rejects unknown keys, and resolves the competitive release from `catalog_ids`; a missing release fails closed. `contract_compatible()` requires every shared key to match and tolerates extra config keys only when declared additive (`ADDITIVE_CONFIG_KEYS`) — that is how 16.635 configs (21 runtime columns, `neutralize_pending_ticks_remaining`, `delay_aware_legality`) load in the same tree as 15.535 configs (16 columns).

Shipped models are listed with SHA-256 in `checkpoints/manifest.json` and described in `checkpoints/README.md` (General, IL, active IL, two Hog 2.6 specialists).

## Evaluation

```sh
python -m native_runner.training.v4.evaluate \
  --checkpoint-a checkpoints/General/checkpoint-step-00000460.pt \
  --checkpoint-b checkpoints/IL/checkpoint-step-00029396.pt \
  --games 20 --match-config tools/native_analysis/match-configs/xbow-vs-hog26.json \
  --output evaluations/comparison
```

- Two `PolicySessionV4` on one `BattleEnvV1`; seats **alternate per game** and each model's deck/forms/tower troop move with it.
- `MatchConfig` (`native_runner/match_factory.py`) defines decks, levels, seeds and locations.
- Refuses mixed competitive releases or differing `model_config`.
- Outputs: `identity.json` (checkpoint hashes, config, sampling mode), `matches.jsonl` (winner, seed, side assignment, terminal state, actions, rejections), `summary.json` (requested vs completed, A/B wins, draws, truncations).
- Report win rate with game count and opponent; check `truncated` and `rejected_actions` separately — an execution failure is not a loss.

## Local policy service

`python -m native_runner.training.v4.serve_policy --checkpoint model.pt` runs a JSON-lines loop on stdin/stdout with no network and no engine. Ops: `start` (episode + initial FAIR observation + `initial_elixir`), `observe` (warmup only), `act`, `end`. One process = one episode = one recurrent state.

The full protocol and client example live in [`docs/policy_service.md`](../../docs/policy_service.md); the invariants that bite are:

- Send `observe` before `FIRST_POLICY_DECISION_TICK`, then `act` every 5 ticks; submit returned `execute_offset_ticks` unchanged.
- Any contract or request error returns `{"ok": false}` **and discards episode state** — start again.
- Rendered interactive callers still apply the native deploy delay, as `offline_agent.py` does.

## Desktop agent and duels

`training/v4/offline_agent.py` drives one model seat against the rendered client; `offline_duel.py` runs AI-vs-AI. Both use `PolicySessionV4` + `separate_micro_action_ticks`, reset policy state after warmup, and advance exactly five ticks between decisions. They are what the UI's *Human VS AI* and *AI VS AI* pages launch.

## Gotchas

- Cache invalidation is source-hash based: any Python edit under `native_runner/` means re-cache.
- `Epoch/shard` counts are not comparable across dataset versions; record dataset, tags, config, seed, and starting checkpoint with every run.
- Evaluation on a rendered client requires stopping any other interface task first (one engine, one owner).
- `CR_COMPETITIVE_DATA_ROOT` swaps compiled tables; catalog order must still match the checkpoint.

## Checks

`tests/test_training_v4_{learning,cache,producer,ppo_padding,ppo_matchmaking,ppo_multinode,ppo_expert_bc,remote_worker_transport,offline_agent,offline_duel,policy_session}.py`, `tests/test_royaleapi_replay.py`, `tests/test_evaluate_and_serve.py`. Three learning tests and the async CUDA-graph tests skip without CUDA. None require a live engine. See [operations](../operations/runbooks.md) for the run order.
