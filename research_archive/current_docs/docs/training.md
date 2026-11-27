# Training and simulation

Run commands from the repository root with the Python dependencies installed.
Windows supports cache building, inference, evaluation, and the console. The
IL and PPO launchers target Linux with CUDA and NCCL.

## 1. Demo data

Generate a small synthetic replay, Parquet dataset, tag list, and deck pool:

```sh
python tools/make_demo_data.py
```

Outputs go to `runs/demo`. Use `--output` to choose another directory. The
console can open the generated dataset from its collected-replay page. This
example checks the workflow; use real matches to study agent behavior.

The cache builder reads `replays/part-*.parquet`. Each row contains a string
`replay_tag` and a JSON `payload_json` with schema `royaleapi-battle-replay.v1`:
decks, forms, tower troops, 20 Hz action times, sides, coordinates, and the
replay end time. See the generated `runs/demo/payload.json` for a complete
example.

## 2. Replay reconstruction and IL cache

Start an idle offline engine first. On Windows, use
`./native_runner/start_offline.ps1`; set `--ports` to its control port.

```sh
python -m native_runner.training.v4.cache_builder runs/demo/dataset runs/demo/cache \
  --ports 26789 --source-count 1 --resident-slots 1 --verify-roundtrip
```

Outputs include `index.json`, sequence shards, and shard manifests. Check the
completed and failed replay counts and the expert and executed action counts
before training. For a full dataset, pass the dataset directories and use
`--source-count 0`. Multiple engine ports are comma-separated;
`--resident-slots` must fit the configured capacity.

Linux partitioned conversion is available through
`native_runner/training/v4/launch_cache_partition.sh`.

## 3. Recurrent imitation learning

On the Linux training host, set `CR_AI_PYTHON` to the training interpreter and
`IL_WORLD_SIZE` to the GPU count (default 1). `CR_IL_SHARDS_PER_RANK` defaults
to 1. Copy the complete cache directory to the host. The training tag file
contains one selected replay tag per line.

```sh
bash native_runner/training/v4/launch_stateful_il.sh \
  --index runs/demo/cache/index.json --train-tags runs/demo/train-tags.txt \
  --output-dir runs/il-smoke --epochs 1 --max-groups 1 --save-every-groups 1
```

For a real run, remove `--max-groups 1` and use your dataset and tag selection.
To resume an interrupted run, pass its checkpoint with `--resume`. Check the
loss, completed groups, and saved checkpoints before evaluating the model.

## 4. Native trajectory playback

To create a complete native trajectory, start an idle offline engine and run:

```sh
python tools/make_demo_data.py --output runs/native-demo --native
```

Open `runs/native-demo/training_replays` in the console's training-replay page.
In a custom runner, export `TrainingReplayV1` with
`capture_training_replay(environment, ...)` from a trace-enabled environment;
see [`replay_archive.py`](../native_runner/training/replay_archive.py). The V4
trainers do not save a visual replay for every match.

## 5. Windows engine cluster

A dedicated VM supports 1 to 24 engine processes and 1 to 8 resident slots per
process. Set `CR_BUILD_ENGINE_COUNT` before building the APK, `CR_ENGINE_COUNT`
to no more than that value, and `CR_SLOTS_PER_ENGINE` to the desired slot count.
Set `CR_VM_MEMORY_GB`; use `CR_CONFIGURE_VM_RESOURCES=false` to keep the VM's
existing resource configuration.

```powershell
./native_runner/start_engine_cluster.ps1
./native_runner/stop_engine_cluster.ps1
```

The start command reports control ports and capacities for the cache builder.
Stop the cluster before starting a console task, and vice versa.

## 6. Linux AVD and distributed PPO

Android Emulator hosts need `/dev/kvm`, root adbd, and an Android system image
that can run ARM64 libraries. In the base AVD, install the original APK, open it
to finish the matching update, and exit. Copy the offline APK and its
`.build.json` receipt to Linux. Place the compiled probe at
`native_runner/probe/out/libcrprobe.so`, or set `CR_PROBE`.

```sh
python tools/prepare_training_guest.py --serial emulator-5554 --apk /path/to/cr-ai-offline.apk
```

ADB uses `CR_TRAINING_ADB`, with `--adb` as an override. Prepare the guest with
one vCPU, then convert its userdata image to raw format:

```sh
python -m native_runner.training.v4.configure_emulator_engine_guests \
  --serials emulator-5554 --host-bases 26789 --engines 1 --output runs/guest-ready.json
qemu-img convert -O raw <userdata-source> <prepared-userdata.img>
```

Set `CR_EMULATOR_INITDATA` to the prepared image, `CR_SOURCE_AVD` and
`CR_SOURCE_AVD_INI` to the base AVD, and `CR_TRAINING_APK` to the signed APK.

For PPO, configure the Linux resource section of `.env`: SDK, emulator, prepared
AVD, APK, and probe. Start with `CR_PPO_ENGINES_PER_GUEST=1`; capacity is 1 to 6
engines per guest with eight resident slots per engine. Allocate at least one
CPU per engine plus control CPUs. `CR_EMULATOR_COMPAT` points to the emulator
compatibility-library directory; use a valid empty directory if none are needed.

Within an existing Slurm allocation, launch a simulation node:

```sh
bash native_runner/training/v4/launch_ppo_engine_node.sh runs/node0 0 1
```

This command holds the engines and writes `runs/node0/engine-node-ready.json`.
Global engine indices start at zero and must be contiguous across nodes. Each
node uses its own output directory; create `STOP_ENGINES` there to stop it.

Build the trainer topology from the readiness files:

```sh
python -m native_runner.training.v4.build_ppo_multinode_topology \
  --node-ready runs/node0/engine-node-ready.json --output runs/topology.json \
  --trainer-job-id <job-id> --trainer-host <host> --gpus 1 \
  --worker-cpus <worker-cpu-ids> --control-cpus <control-cpu-ids>
```

Build a deck pool from the parent of your dataset directories, or use
`runs/demo/deck-pool.json` for a workflow check:

```sh
python -m native_runner.training.v4.build_ppo_deck_pool \
  --dataset-root ./datasets --output ./runs/deck-pool.json --minimum-uses 5
```

Set `CR_PPO_DECK_POOL` and `CR_PPO_CHECKPOINT` to absolute paths on the training
host. The included `checkpoints/General/checkpoint-step-00000460.pt` is one
starting point. On the trainer host, run one update with 40-second rollouts:

```sh
bash native_runner/training/v4/launch_ppo_multinode_training.sh runs/ppo-smoke runs/topology.json 1 40
```

Inspect match completion, rejected actions, optimizer metrics, and output
checkpoints. Configure longer runs through the launcher arguments and PPO
settings in `.env`; run the launcher with `--help` for the available options.
Metrics default to local offline W&B. Resume with `CR_PPO_RESUME_CHECKPOINT`.

## 7. Evaluation

Stop any active interface task and start the offline engine, then compare two
checkpoints:

```sh
python -m native_runner.training.v4.evaluate \
  --checkpoint-a ./checkpoints/General/checkpoint-step-00000460.pt \
  --checkpoint-b ./checkpoints/IL/checkpoint-step-00029396.pt \
  --games 20 --match-config ./tools/native_analysis/match-configs/xbow-vs-hog26.json \
  --output ./evaluations/comparison
```

The evaluator alternates sides and moves each model's deck with it. Keep decks,
forms, tower troops, levels, seeds, and decoding mode consistent across
comparisons. See [Models](../checkpoints/README.md) for the output files and
metrics. Check engine readiness and training outputs with the
[validation guide](validation.md).
