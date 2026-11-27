# Session handoff — 2026-11-14 — Null's Royale 16.635.1

> **Read [`ROADMAP.md`](ROADMAP.md) first.** It is the single plan.
> This file is the next-session starting point.

## Decision — what changed

The previous plan optimized **engine throughput** by moving to the x86_64
`cr-native` 15.535.29 runtime. That was the right call for throughput, but it
optimized the wrong variable: **Null's runs 16.635.1 ARM64**, and the 15.535.29
x86 engine cannot connect to Null's ladder.

The repo already has the direct path:

- **FirstLight V4 General** — `checkpoints/General/checkpoint-step-00000460.pt`,
  12,684,339 params, `ppo-league-snapshot`, trained across decks.
- **IL_Replay** — `datasets/royaleapi/`, 252,238 replays / 17,836,160 actions,
  already local.
- **16.635.1 offline APK + cluster APK + probe** — `builds/cr-ai-16.635.1.apk`,
  `builds/cr-engine-cluster-16.635.1.apk`,
  `native_runner/probe/out/libcrprobe-16.635.1.so`.
- **A51 cluster/deploy tooling** — `deploy_a51.sh`, `launch_cluster_a51.sh`.
- **V4 IL/PPO stack** — `native_runner/training/v4/`.

**R0 is retired.** It is a third observation contract with no trained
checkpoint, no local corpus and no 16.x adapter. Its code/evidence is archived;
do not start another R0 run.

**Primary track:** 16.635.1 on the A51 with the V4 General model.
**Proxy lab:** cr-native 15.535.29 x86, for throughput/reward/parity only.

---

## Current state

| Item | State |
| --- | --- |
| A51 device | **not attached** at the time of writing (`adb devices` empty) |
| 16.635.1 client | `nullsroyale.rel.free`, `libg.so` SHA `0b580222…e4eaa` |
| 16.635.1 probe | built; build-selectable; **dynamic verification pending** |
| 16.635.1 APK | built (`builds/cr-ai-16.635.1.apk`, 841 MB) |
| Cluster APK | built (`builds/cr-engine-cluster-16.635.1.apk`) |
| Probe port evidence | `docs/native/16.635.1/FINDINGS.md` — in progress |
| General checkpoint | ready, 12.68M params |
| IL_Replay | ready locally, 252,238 replays |
| V4 evaluator | ready; connects to a control port over TCP |
| Cloud GPU | none on this host; ThunderCompute/GCP/Azure credits available |
| cr-native proxy | working, 15.535.29 only |

### 16.635.1 port status (the real blocker)

From `docs/native/16.635.1/FINDINGS.md`:

- Touch JNI hook is **port-ready** (`0x0156f790`).
- Hand/deck model offsets and the replay-controller trio are recovered.
- The probe is build-selectable for 16.635.1; required anchors fail closed.
- Relocation: **59 EXACT, 31 LIKELY, 10 MANUAL, 37 AMBIGUOUS, 10 MISSING**.
- **Rich telemetry is deliberately skipped** on the 16.635 build until the
  remaining core anchors land.
- Cold configure / live command injection remain closed on some anchors.
- Dynamic verification on the A51 is pending.

This is finite reverse-engineering work with existing tools, not a missing model
or dataset. Validate it before spending cloud credits.

---

## Next session — do this first

### 1. Attach the A51 and prove the 16.635.1 engine starts

```bash
adb devices -l
adb -s <serial> shell getprop ro.product.model
adb -s <serial> shell getprop ro.product.cpu.abi
```

Expected: `SM_A515U`, `arm64-v8a`. Then install the cluster APK if needed and
launch two engines:

```bash
./launch_cluster_a51.sh --engines 2 --cpu-map 4,5
adb forward tcp:26789 tcp:26789
```

Check that both engines report `coldReady`, the control ports answer, and the
client content fingerprint matches the 16.635.1 build.

### 2. Run the scripted X-Bow match

```bash
.venv/bin/python tools/native_analysis/scripted_xbow_match.py \
    --output /tmp/opencode/scripted_xbow_16.635.1.json
```

Gate: 0 problems, 0 ability rejections, match terminates normally.

### 3. Inspect `observe_rich`

The V4 model needs the full `universal-semantic-observation.v4` streams. If the
probe returns sentinel/`kUnavailableOffset` values, stop and fix the port before
training. Record the exact missing fields in the findings log.

### 4. Evaluate General vs IL

```bash
.venv/bin/python -m native_runner.training.v4.evaluate \
    --checkpoint-a checkpoints/General/checkpoint-step-00000460.pt \
    --checkpoint-b checkpoints/IL/checkpoint-step-00029396.pt \
    --games 2 --output /tmp/opencode/eval-general-nulls.json \
    --port 26789 --device cpu
```

Record win/loss/draw, rejected actions, match ticks, seconds/match and
`normal_terminal_rate`. If General works, you already have a Null's baseline
before any GPU spend.

### 5. Measure throughput

Time 10–20 matches with 1, 2 and 4 engines. Record matches/hour, rejections and
memory. This number decides whether the A51 path can feed training or whether a
cloud AVD cluster is needed.

---

## General baseline

`checkpoints/General/checkpoint-step-00000460.pt`

| Field | Value |
| --- | --- |
| Parameters | 12,684,339 |
| Contract | `universal-card-policy.v4` |
| Observation | `universal-semantic-observation.v4` |
| Action | `universal-candidate-action.v4`, 2 micro-actions, 50 ms offsets |
| Decision cadence | 5 ticks |
| Training stage | `ppo-league-snapshot`, step 460 |
| SHA-256 | `2fb7a94fa5df0f12bd72433874487bc7d84a4efb2a6dac827d7adff0a5eef083` |

Other checkpoints are opponents/baselines:

- `checkpoints/IL/checkpoint-step-00029396.pt`
- `checkpoints/active IL/checkpoint-step-00000030.pt`
- `checkpoints/2_6hog_expert/hog26-specialist1.pt`
- `checkpoints/2_6hog_expert/hog26-specialist2.pt`

The General model was trained on 252k official-game replays; Null's may have
custom balance/cards. Verify content compatibility before assuming transfer.

---

## Data — no scraping needed to start

`datasets/royaleapi/` is the full **IL_Replay** dataset:

- 252,238 replays, 17,836,160 actions, 1.8 GB
- `replays/part-*.parquet`, `actions/part-*.parquet`, `manifest.json`
- Format: `royaleapi-battle-replay.v1`

Sources and limits:

| Source | What it gives | Use |
| --- | --- | --- |
| **Local IL_Replay** | 252k replays / 17.8M actions | generalist pretraining/IL, already here |
| **Official Clash Royale API** | battle logs only (last ~25), no replays | meta/opponent stats, not IL |
| **RoyaleAPI downloader** (`.resource/royaleapi-downloader`) | live 16.x ladder replays, structured JSON | fresh official-game data; follow site terms |
| **Hugging Face** | IL_Replay (local), optional replay/checkpoint datasets | extras only |
| **Null's self-play/captures** | the actual target distribution | the data that matters for the ladder |

Do not mix 15.535.29 self-play labels with 16.x IL labels without an explicit
adaptation step. Do not call self-play actions “expert” for IL.

Deck diversity: use `native_runner/training/v4/build_ppo_deck_pool.py` on
`datasets/` (exact forms + tower troops), then the V4 league/opponent scheduler.
No scraping is required for the first fine-tune.

---

## Training pipeline

The local V4 stack:

| Step | Command |
| --- | --- |
| Build IL cache | `python -m native_runner.training.v4.cache_builder datasets/royaleapi datasets/cache --source-count 0 --ports 26789 --resident-slots 1 --verify-roundtrip` |
| IL smoke | `bash native_runner/training/v4/launch_stateful_il.sh --index … --train-tags … --output-dir runs/il-smoke --epochs 1 --max-groups 1 --save-every-groups 1` |
| Deck pool | `python -m native_runner.training.v4.build_ppo_deck_pool --dataset-root ./datasets --output ./runs/deck-pool.json --minimum-uses 5` |
| Policy service | `python -m native_runner.training.v4.serve_policy --checkpoint checkpoints/General/checkpoint-step-00000460.pt` |

PPO caveat: the upstream launchers assume AVD/engine-ready topology. The A51
cluster is not wired into that topology yet; adding an A51 engine-ready path is
the first N3 task. Until then, use the evaluator and a single-engine PPO smoke.

Training rules:

- Initialize from General; do not freeze it as a mirror opponent.
- Keep rejected actions at zero; reject the rollout otherwise.
- Evaluate paired seeds, both seats, fixed decks/levels.
- Record checkpoint SHA-256, dataset/tag selection, deck pool, seed and config.

---

## Cloud and credits

The GPU is not the bottleneck; the engine is.

- **ThunderCompute RTX A6000 48 GB — $0.35/hr.** Cheapest useful GPU for the
  12.7M model; ~57 h for $20. Use it for short validation/benchmark runs.
- **GCP $300 / Azure $100.** Useful only if you need a high-vCPU host with a GPU
  or a cloud Android/AVD cluster. New accounts often have **zero GPU quota**;
  check before planning. Free-trial terms apply per account.
- **A100/H100 are unnecessary** for 12.7M parameters. L4/T4/A6000 is enough.
- **Do not spend credits before N0/N1 pass.** The first useful spend is a GPU
  fine-tune after the A51 baseline is validated.

Scale options if the A51 cannot feed training:

1. More A51-class ARM64 devices (cheapest per engine, hardware-bound).
2. Cloud x86 host with KVM + ARM64 Android image/AVD cluster (upstream path;
   verify nested virtualization and throughput first).
3. Proxy track with a V4 observation adapter on the 15.535.29 x86 engine
   (fastest engine, but a new adapter and cross-build port).

---

## Risks

1. **16.x port completeness** — rich telemetry and several core anchors remain;
   N2 may take longer than N0/N1.
2. **Engine throughput** — one A51 is far below the upstream 1,528-match scale.
   Measure before committing credits.
3. **Null's content** — official-game 16.x data may not match Null's custom
   balance/cards. Verify the fingerprint.
4. **Ladder rules** — automated play may be prohibited; confirm before using a
   live account.
5. **Cloud quotas** — GCP/Azure GPU quota may be zero on new accounts.

---

## Retired / deferred

- **R0 elite-v3** — retired. Archived in
  `docs/native/R0_CANONICAL_TREE.md`,
  `tools/native_bringup/smoke_r0_model.py`,
  `tools/native_bringup/verify_r0_batched.py`. Do not start an R0 run.
- **R0 checkpoint/data/CUDA/adapter gates** — retired with R0.
- **cr-native 15.535.29 as critical path** — demoted to proxy lab.
- **Custom ARM64 `libg` loader** — retired; 16.635.1 uses the offline APK/probe.
- **“16.635.1 is parity only”** — reversed; it is the primary target.
- **Linux AVD/MuMu/phone clusters** — only a scale option if the A51 is
  insufficient.

---

## Key paths

| Path | Purpose |
| --- | --- |
| `ROADMAP.md` | The single plan |
| `docs/native/16.635.1/FINDINGS.md` | 16.635.1 probe port evidence log |
| `docs/native/16.635.1/*.json` | Relocation, field offsets, vtables, overrides |
| `tools/native_analysis/` | A51 parity/probe tools |
| `tools/native_analysis/scripted_xbow_match.py` | Scripted X-Bow match on the probe |
| `deploy_a51.sh`, `launch_cluster_a51.sh` | Install/launch the 16.635.1 cluster |
| `builds/cr-ai-16.635.1.apk` | 16.635.1 offline APK |
| `builds/cr-engine-cluster-16.635.1.apk` | 16.635.1 cluster APK |
| `native_runner/probe/out/libcrprobe-16.635.1.so` | 16.635.1 probe |
| `native_runner/training/v4/` | V4 IL/PPO stack, evaluator, deck pool, league |
| `checkpoints/General/checkpoint-step-00000460.pt` | Primary baseline |
| `datasets/royaleapi/` | IL_Replay corpus (252k/17.8M) |
| `.resource/royaleapi-downloader/` | Optional fresh 16.x replay downloader |
| `docs/native/R0_CANONICAL_TREE.md` | R0 archive (retired) |
| `.resource/cr-native-sandbox/` | 15.535.29 proxy lab |
| `docs/native/PHASE0_BRINGUP.md` | cr-native runbook (proxy only) |
| `docs/native/PHASE2_BOTTLENECK.md` | cr-native throughput evidence |

---

## Do not do

- Do not train R0 or start an R0 data/checkpoint/adapter run.
- Do not use the x86 15.535.29 engine as the source of deployable Null's
  weights without an explicit adaptation step.
- Do not spend cloud credits before the A51 engine and General baseline are
  validated.
- Do not assume one A51 matches the upstream 1,528-match training scale.
- Do not mix 15.535.29 labels with 16.x labels.
- Do not run automated ladder play without confirming the server's rules.
- Do not treat `IL_Replay` as retired: it is the generalist corpus now.
- Do not commit large APKs/datasets.

---

## Appendix — historical facts worth keeping

**cr-native 15.535.29 proxy (not the deployment path):**

- 1 h soak passed; 8 workers is the knee; 12 is oversubscribed on the 10-core
  laptop.
- Engine-only at 8 workers: 36.4k ticks/s ≈ 9.6 matches/s at 100 ticks/RPC.
- 516k loop at 8 workers: 0.074 matches/s (1 tick/decision), 0.277 (4),
  0.522 (8).
- R0 CPU inference: 125–269 ms/pass; not viable without a GPU.
- Evidence: `builds/native/phase0-20261114T000821Z/`,
  `docs/native/PHASE2_BOTTLENECK.md`.

**16.635.1 parity (A51):**

- Towers: King 4824, Princess 3052 at level 11.
- Deck ids/forms/costs and starting elixir match 15.535.29.
- Unit HP matches except Electro Spirit (A51 215 vs native 217).
- Hero Knight ability state/charges/elixir match.
- Evidence: `builds/native/parity-unit-hp-15.535.29-vs-16.635.1-L11.json`.

**R0 archive:** 36,854,175 params, schema `825408c3…4d44`, CPU
batched-equivalence PASS. Retired.
