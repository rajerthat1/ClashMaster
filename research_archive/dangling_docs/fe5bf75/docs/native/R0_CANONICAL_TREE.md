# R0 canonical tree adoption — 2026-11-14

> **Retired from the primary plan (2026-11-14).** The project target is Null's
> Royale 16.635.1; the V4 General checkpoint and `native_runner` stack are the
> deployment path. R0 has no trained checkpoint, no local corpus and no 16.x
> adapter. This document is kept as an engineering archive; do not start an R0
> run. See [`ROADMAP.md`](../../ROADMAP.md) and [`HANDOFF.md`](../../HANDOFF.md).

The R0 model stack in the sandbox is now the upstream
[`IMAX9D/r0-elite-training`](https://github.com/IMAX9D/r0-elite-training) tree
(MIT), not the older sandbox copy.

| Item | Before | After |
| --- | --- | --- |
| Source | `.resource/cr-native-sandbox/training_r0` | `.resource/r0-elite-training/training_r0` @ `a60db2b` |
| Parameters | 36,853,407 | **36,854,175** |
| Observation schema | `07c183d2…7426` | **`825408c38a3facdb8596c08397b739a38b83d8a5be0727cf6e9eecb5f5f04d44`** |
| Vocabulary | 212 | 212 |
| Changes | — | `attack_locked` feature, cross-table `Base` resolution, Royal Chef / tower-troop aliases |

A checkpoint from the old tree will **not** load in the new one: `current_contract()`
hashes `training_r0/*.py` and `training_r0/data/*.json`, and the `_contract`
buffer differs. The old `builds/native/r0-elite-v3-smoke.pt` was regenerated.

## What was vendored

Into `.resource/cr-native-sandbox`:

- `training_r0/` — all modules and `data/*.json` from upstream. The sandbox
  `training_r0/README.md` is kept and its contract numbers updated.
- `data_pipeline/` — upstream `r0_admission.py`, `r0_compile.py`,
  `r0_produce.py`, `r0_certify.py`, `r0_train.py`, plus the modules they import:
  `bounded_pipeline.py`, `claim_ledger.py`, `fast_json.py`,
  `fast_tensorizer.py`, `fused_verify.py`, `input_validator.py`.
- The sandbox's Linux adaptations were preserved: `r0_produce.py` still requires
  `--downloader-root` instead of the upstream Windows default, and the
  `CR_NATIVE_*` environment/path handling in `processing_contract.py`,
  `runtime_registry.py`, `r0_plan_survey.py` and `runtime_coverage_audit.py` is
  unchanged. Those files are not part of the hashed R0 contract.

## Verification

Synthetic CPU smoke, run from the sandbox:

```bash
.venv/bin/python -m training_r0.smoke --profile elite --steps 2
# parameters 36854175
# observation_schema_sha256 825408c3…4d44
# metrics.updated true
```

Model/checkpoint smoke (rich fixture, CPU, one PPO update + restore):

```bash
.venv/bin/python tools/native_bringup/smoke_r0_model.py --rich
```

Evidence `builds/native/r0-model-smoke.json`:

| Field | Value |
| --- | --- |
| parameters | 36,854,175 |
| checkpoint | `builds/native/r0-elite-v3-smoke.pt` (444,222,791 B) |
| restore bit-exact | `true` |
| state_dict SHA-256 | `1927d8c179facb9d3b6579f4f7aafc6a59fd1fa99943386a57bbb13dc8558bae` |
| second update | rejected by the KL guard (`updated: false`), expected |

Batched-trainer equivalence, CPU/synthetic
(`tools/native_bringup/verify_r0_batched.py` replaces the CUDA/real-data
upstream `verify_b1_equiv.py` / `verify_multilane.py` for this host):

```bash
.venv/bin/python tools/native_bringup/verify_r0_batched.py --steps 4 --lanes 4
.venv/bin/python tools/native_bringup/verify_r0_batched.py --steps 4 --lanes 4 --rich \
    --output builds/native/r0-batched-equiv-rich.json
```

Evidence `builds/native/r0-batched-equiv.json`:

| Check | Result |
| --- | --- |
| B=1 loss relative | 2.75e-08 |
| B=1 worst gradient difference | 3.49e-10 absolute (489 tensors) |
| B=1 worst parameter difference | 3.95e-07 absolute after one AdamW step |
| B=1 recurrent state | identical |
| Multi-lane lengths | 4, 5, 6, 7 |
| Multi-lane log-prob difference | 9.54e-07 |
| Multi-lane recurrent state | 1.52e-06 |
| Decision accounting | 22 labelled decisions, not `4 × 7 = 28` |
| Verdict | **PASS** (rich fixture also PASS) |

The tool uses capture-complete synthetic fixtures so the production
(`allow_incomplete_capture=False`) model and `update_imitation_sequence` can
run without pretending the fixture is native validation. A cold CPU kernel can
differ by ~2e-3; the tool warms the exact shapes before measuring.

Test evidence in the sandbox:

- `tests/test_training_r0.py`, `test_training_r0_rich.py`,
  `test_training_r0_elite.py`, `test_r0_compile.py`, `test_r0_il_dataset.py`,
  `test_r0_training_smoke.py`, `test_r0_resource_inheritance.py`:
  **65 passed**.
- `test_r0_admission_evidence.py`, `test_r0_capture_stream_admission.py`,
  `test_replay_capture_repairs.py`: **29 passed**.

## What is still missing (unchanged by this adoption)

1. **No trained checkpoint.** `builds/native/r0-elite-v3-smoke.pt` is still
   random-weight smoke data, now under the canonical contract.
2. **No `compiled-prod-v17` data or `R0_XPAN_TOKEN`** locally.
3. **No CUDA host on this machine** (Intel Iris Xe only). The `.venv` is
   `torch 2.5.1+cpu`; upstream wants Python 3.12 + `torch 2.8+cu128`.
4. **No native live adapter.** `observe_train_v1` exposes entities, effects,
   projectiles and towers, but R0 production input also needs `groups`,
   `relations`, `card_runtime`, `combat_events` and lifecycle attribution. The
   only honest producer of those streams today is the capture →
   `ReplayFeatureAssembler` path (`data_pipeline/r0_produce.py`). Do not mark
   those capabilities present just to satisfy `capture_complete`; the model
   would train on invented semantics.
