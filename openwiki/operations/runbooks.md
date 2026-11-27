# Operations and runbooks

How to set up, build, run, and check the project. Commands are copied from the source docs (`docs/getting_started.md`, `docs/training.md`, `docs/validation.md`) and the scripts themselves; where a script has the final word, the script wins.

## 1. Prerequisites and configuration

| Need | Notes |
| --- | --- |
| Windows desktop | Python 3.12 with Tkinter; the UI is Windows/MuMu-bound |
| MuMu Player | Android 12, root enabled, ARM64, dedicated instance |
| Null's Royale APK | Your own copy; 15.535.13 by default, 16.635.1 with `-TargetBuild 16.635.1` |
| JDK 17 + Android NDK + SDK build-tools | Only to build the offline APK |

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe tools/check_install.py
```

`.env` is machine-local and git-ignored; `.env.example` lists every key with blank values (never commit filled values). Effective precedence is process environment → `.env` → default (`native_runner/local_config.py`). Groups of keys: Python paths, control/ADB endpoints (`CR_CONTROL_PORT`, default 26789), MuMu VM identity, build toolchain, Linux training resources, and PPO/IL launcher knobs. `CR_WORKSPACE_ROOT` lets assets live outside the repo (`native_runner/paths.py`).

## 2. Build and install the offline engine (Windows)

```powershell
./check_original_game.ps1 -InputApk ./user-provided/nulls-royale.apk
./build_offline_engine.ps1 -InputApk ./user-provided/nulls-royale.apk -OutputApk ./build/cr-ai-offline.apk
./install_offline_engine.ps1 -Apk ./build/cr-ai-offline.apk
```

`build_offline_engine.ps1` runs four steps: verify the input APK against the pinned engine payload, compile `libcrprobe.so`, patch/sign the offline APK, then write the receipt `<apk>.build.json`. Keep the receipt next to the APK — the installer verifies it.

Content caveat: base APK content supports every match but cannot satisfy the ruleset manifest, so attested training/evaluation needs the pinned update. With base content pick a location that exists there (e.g. `CR_NATIVE_LOCATION=15000002`); the default `15000199` only exists in the 15.535.86 update.

See [native engine](../architecture/native-engine.md) for what the probe and attestation actually do.

## 3. Run the desktop interface

```powershell
./launch_interface.cmd             # -> native_runner/launch_interface.ps1 -> user_interface
./launch_interface.cmd --diagnose  # check the configured MuMu instance first
```

Pages: `1 Human VS AI`, `2 AI VS AI`, `3 手动 native 对局` (manual native match), `4 训练回放` (training replays), `5 采集回放` (collected replays). The UI starts the VM + native runner on demand, launches model seats as child processes (`offline_agent`, `offline_duel`, `native_overlay`), and writes per-match artifacts under `runs/interface/model-<nanos>/{match.json,console.log,ready.json,stop}` plus the log `runs/interface/firstlight-interface.log`.

The UI holds the engine exclusively: stop it before starting an engine cluster or a PPO engine node.

## 4. Windows engine cluster (bulk collection)

```powershell
# .env: CR_BUILD_ENGINE_COUNT, CR_ENGINE_COUNT, CR_SLOTS_PER_ENGINE, CR_VM_MEMORY_GB
./native_runner/start_engine_cluster.ps1
./native_runner/stop_engine_cluster.ps1
```

One VM, 1–24 engine processes, 1–8 resident slots per process; `CR_ENGINE_COUNT` must not exceed the `CR_BUILD_ENGINE_COUNT` baked into the APK. The start command prints control ports and capacities — that is the `--ports` / `--resident-slots` input for `cache_builder`. Set `CR_CONFIGURE_VM_RESOURCES=false` to keep the VM's existing resources.

## 5. Linux AVD and distributed PPO

Android Emulator guests need `/dev/kvm`, root adbd, and an ARM64-capable system image. Prepare once, then convert the userdata image for reuse:

```sh
python tools/prepare_training_guest.py --serial emulator-5554 --apk /path/to/cr-ai-offline.apk
python -m native_runner.training.v4.configure_emulator_engine_guests \
  --serials emulator-5554 --host-bases 26789 --engines 1 --output runs/guest-ready.json
qemu-img convert -O raw <userdata-source> <prepared-userdata.img>
```

Then, per simulation node (inside a Slurm allocation):

```sh
bash native_runner/training/v4/launch_ppo_engine_node.sh runs/node0 0 1
# holds the engines, writes runs/node0/engine-node-ready.json

python -m native_runner.training.v4.build_ppo_multinode_topology \
  --node-ready runs/node0/engine-node-ready.json --output runs/topology.json \
  --trainer-job-id <job-id> --trainer-host <host> --gpus 1 \
  --worker-cpus <ids> --control-cpus <ids>

bash native_runner/training/v4/launch_ppo_multinode_training.sh runs/ppo-smoke runs/topology.json 1 40
```

Global engine indices start at zero and must be contiguous across nodes; each node owns its output directory and stops when a `STOP_ENGINES` file appears. Capacity guidance (1–6 engines per guest, 8 resident slots each, one CPU per engine plus control CPUs) is in `docs/training.md`.

## 6. Validation ladder

Run in this order; each rung catches a different class of failure.

```sh
python -m pip check
python tools/check_install.py                # data hashes + every shipped checkpoint loads
python -m pytest native_runner/tests -q
python -m pytest crforge_bridge/tests -q     # needs jpype + jars + tables, else skips
python tools/check_release.py                # release file set, hashes, .env template
```

Manual acceptance (needs the VM): launch with `--diagnose`, start a level-11 match, verify manual and model actions execute, then run a standard, a 7x, a Ramp Up and an All Evolution match; inspect action rejections and five-tick decision spacing; stop/restart and switch modes. Keep the VM exclusive.

## 7. Tests, CI and release checks

`.github/workflows/source.yml` runs `tools/check_release.py`, `compileall`, and a subset of tests on Ubuntu (two dependency installs: CPU torch first, then `requirements.txt`), plus `test_visual_deck.py` on Windows. Notably **not** in CI: the crforge bridge/vector suites (need the JVM distribution and tables), the live-adapter tests, and any native engine path. Those only run on a prepared host — do not assume CI proves them.

### Which checks to run when changing X

| Area | Minimum |
| --- | --- |
| Contracts / observation / action | `test_contracts.py`, `test_battle_env.py`, `test_training_v4_action_contracts.py` |
| Tensorizer / model / decoding | `test_training_v4_{tensorizer,model,decoding,padding,catalog_regressions}.py` |
| IL cache / producer | `test_training_v4_{cache,producer,padding}.py`, `test_royaleapi_replay.py` |
| PPO / league / topology | `test_training_v4_{learning,ppo_matchmaking,ppo_multinode,ppo_padding,remote_worker_transport}.py` (CUDA tests skip without a GPU) |
| Probe / layouts / build | `test_probe_build_layout.py` (needs `.resource/native/16.635.1/reference/libg.so`), `test_offline_build.py`, `test_offline_install.py`, `test_native_hook_table.py` |
| Transport / resident batch | `test_resident_batch_channel.py`, `test_resident_multimatch.py`, `test_resident_batch_throughput.py` |
| UI / duels / deck editor | `test_user_interface.py`, `test_duel_modes.py`, `test_visual_deck.py`, `test_training_v4_{offline_duel,offline_agent}.py` |
| crforge bridge / vectorization | `pytest crforge_bridge/tests -q` on a host with jpype, jars and tables |
| Live adapter | `test_live_adapter.py` (synthetic frames, no device) |
| Anything shipped | `tools/check_release.py` last |

## 8. Common failure modes

| Symptom | Likely cause |
| --- | --- |
| Episode reset refused | Attestation failed: engine/libg or probe hash, content fingerprint, or `production_ready=false` |
| Match starts but nothing is deployable | Wrong battle location for the installed content (`15000199` vs base pack) |
| `configure` hangs or returns empty | Content-ready gate byte never settled — see `tools/native_analysis/capture_content_window.py` |
| Port already in use / stale socket | Previous app process or cluster still owns 26789; `start_offline.ps1` has conflict checks |
| Control commands time out during bulk collection | A `batch-v1` session is open; close it before ordinary commands |
| Installer rolled the app back | Startup failure after install; state and backup live under `/data/local/tmp/firstlight-install-*` |
| Card stays in hand after a decision | Two micro-actions landed on one tick — check `separate_micro_action_ticks` / `micro_actions_dropped` |
| Cache builder rejects everything | Dataset preflight: early actions, >2 actions per window, or mixed `data_i` |
