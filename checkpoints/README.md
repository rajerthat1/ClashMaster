# Models

Five V4 checkpoints are included. Select one in the match interface, or pass its
path to the evaluator or policy service.

| Model | Use | File |
| --- | --- | --- |
| General | Main baseline and PPO initialization | [`General/checkpoint-step-00000460.pt`](General/checkpoint-step-00000460.pt) |
| IL | Human-like imitation-learning baseline | [`IL/checkpoint-step-00029396.pt`](IL/checkpoint-step-00029396.pt) |
| Active IL | PPO-adjusted imitation-learning baseline | [`active IL/checkpoint-step-00000030.pt`](<active IL/checkpoint-step-00000030.pt>) |
| Hog 2.6 specialist 1 | More proactive Hog Cycle specialist | [`2_6hog_expert/hog26-specialist1.pt`](2_6hog_expert/hog26-specialist1.pt) |
| Hog 2.6 specialist 2 | More passive Hog Cycle specialist | [`2_6hog_expert/hog26-specialist2.pt`](2_6hog_expert/hog26-specialist2.pt) |

Choose General for a first match. To play the trained Hog specialist deck,
select Hog Rider, Hero Musketeer, Evolved Cannon, Fireball, The Log, Evolved
Skeletons, Ice Golem, and Ice Spirit, and set those forms explicitly.

## Local inference

```sh
python tools/check_install.py
python -m native_runner.training.v4.serve_policy \
  --checkpoint ./checkpoints/General/checkpoint-step-00000460.pt
```

The service waits for JSON-line requests. See the
[policy protocol](../docs/policy_service.md) for episode initialization and
action requests.

## Evaluation

Stop any active interface task and start the offline engine. Run the evaluator
with two checkpoints, using `--games` for the match count, `--match-config` for
a JSON match configuration, and `--seed` for the starting seed. Configuration
fields follow [`MatchConfig`](../native_runner/match_factory.py). Add `--sample`
for sampled actions; the default uses deterministic decoding.

```sh
python -m native_runner.training.v4.evaluate \
  --checkpoint-a ./checkpoints/General/checkpoint-step-00000460.pt \
  --checkpoint-b ./checkpoints/IL/checkpoint-step-00029396.pt \
  --games 20 --output ./evaluations/comparison
```

The evaluator alternates sides and moves each model's deck with it. Keep decks,
forms, tower troops, levels, seeds, and decoding mode consistent across
comparisons. For a specialist, provide its deck explicitly.

| Output | Contents |
| --- | --- |
| `identity.json` | Checkpoint hashes, match configuration, and sampling mode |
| `matches.jsonl` | Per-match winner, seed, side assignment, terminal state, actions, and rejections |
| `summary.json` | Requested and completed matches, A/B wins, draws, and truncations |

Use wins, losses, and draws from normally terminated matches to compare
performance. Report the game count and opponent with any win rate. Check
`truncated` and `rejected_actions` separately to distinguish execution problems
from lost matches. `actions_by_owner` counts card plays and abilities by arena
side; `a_owner` identifies model A's side.

## Training

Follow the [training guide](../docs/training.md) to build an IL cache and train,
or set `CR_PPO_CHECKPOINT` to a checkpoint path and run PPO. Record the dataset,
selected tags, configuration, seed, and starting checkpoint with each run.

The included files are identified by SHA-256 in
[`manifest.json`](manifest.json). Card and feature compatibility is described in
the [environment data](../native_runner/data/competitive/README.md).
