# ClashMaster

ClashMaster is an offline Clash Royale AI research workspace, forked from
[FirstLight CR](https://github.com/Jaasssoooonnnnn/FirstLight_CR). It runs
Null's Royale without Supercell servers, exposes the battle as a Python
environment, and trains and evaluates V4 actor-critic agents. The primary
research target is **16.635.1**; the build script defaults to the pinned
**15.535.13** engine.

The repository includes the native probe, the battle environment, imitation and
self-play training, evaluation, a local policy service, the desktop interface,
and five pretrained checkpoints.

## Repository layout

| Path | Contents |
| --- | --- |
| `native_runner/` | Native battle environment, probe, training, evaluation, and desktop UI |
| `crforge_bridge/` | Vectorized bulk-simulation bridge |
| `tools/` | Data, analysis, bring-up, and crforge utilities |
| `checkpoints/` | Pretrained V4 models and their manifest |
| `docs/` | Installation, architecture, training, validation, and policy guides |
| `docs/native/16.635.1/` | Probe layout JSON used by the build and analysis tools |

## Requirements

- Windows desktop with Python 3.12 and Tkinter for the interface
- MuMu Player with an Android 12, root-enabled, ARM64 instance for offline play
- Your own copy of the Null's Royale APK (15.535.13 by default, 16.635.1 with `-TargetBuild`)
- JDK 17, Android NDK, and SDK build-tools only if you build the offline APK

Python dependencies are listed in `requirements.txt`. The Linux training
launchers additionally need CUDA and NCCL.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe tools/check_install.py
```

Fill in the paths in `.env` for your machine, then follow
[Getting started](docs/getting_started.md) to install the original game, build
the offline APK, and open the interface.

## Models

| Model | File |
| --- | --- |
| General | [`checkpoints/General/checkpoint-step-00000460.pt`](checkpoints/General/checkpoint-step-00000460.pt) |
| IL | [`checkpoints/IL/checkpoint-step-00029396.pt`](checkpoints/IL/checkpoint-step-00029396.pt) |
| Active IL | [`checkpoints/active IL/checkpoint-step-00000030.pt`](<checkpoints/active IL/checkpoint-step-00000030.pt>) |
| Hog 2.6 specialist 1 | [`checkpoints/2_6hog_expert/hog26-specialist1.pt`](checkpoints/2_6hog_expert/hog26-specialist1.pt) |
| Hog 2.6 specialist 2 | [`checkpoints/2_6hog_expert/hog26-specialist2.pt`](checkpoints/2_6hog_expert/hog26-specialist2.pt) |

See [Models](checkpoints/README.md) for selection, loading, and evaluation
details. File hashes are recorded in `checkpoints/manifest.json`.

## Train and evaluate

Generate a small demo dataset and compare two checkpoints:

```powershell
python tools/make_demo_data.py

python -m native_runner.training.v4.evaluate `
  --checkpoint-a ./checkpoints/General/checkpoint-step-00000460.pt `
  --checkpoint-b ./checkpoints/IL/checkpoint-step-00029396.pt `
  --games 2 --sample --output ./evaluations/comparison
```

Start an offline engine first with `./native_runner/start_offline.ps1`. The
[training guide](docs/training.md) covers replay caches, IL, PPO, and engine
clusters.

For local inference, run the JSON-lines policy service:

```powershell
python -m native_runner.training.v4.serve_policy `
  --checkpoint ./checkpoints/General/checkpoint-step-00000460.pt
```

See the [policy service protocol](docs/policy_service.md).

## Documentation

| Guide | Contents |
| --- | --- |
| [Getting started](docs/getting_started.md) | Environment setup, original game, offline APK, and interface |
| [Architecture](docs/architecture.md) | Source map and the decision loop |
| [Training](docs/training.md) | Replay caches, IL, PPO, and engine clusters |
| [Validation](docs/validation.md) | Installation checks, tests, and manual acceptance |
| [Policy service](docs/policy_service.md) | Request and response protocol |
| [Models](checkpoints/README.md) | Checkpoint selection and evaluation |

## License

ClashMaster is released under the [MIT License](LICENSE). It builds on FirstLight
CR, which is Apache-2.0; the upstream copyright notice and attribution are
preserved in this repository's history.
