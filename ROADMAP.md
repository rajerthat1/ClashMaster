# Roadmap: Null's Royale 16.635.1 ladder

Status: **active — primary target revised 2026-11-14**
Owner: Raj
Deployment target: **Null's Royale 16.635.1** (`nullsroyale.rel.free`, ARM64)
Primary model: **FirstLight V4 General** (12,684,339 params)
Training stack: **`native_runner/training/v4`** (IL + PPO)
Engine: **16.635.1 offline APK + probe on the A51**; cloud ARM64/AVD is a scale option
Proxy lab: **cr-native 15.535.29 x86_64** (fast, not deployable to Null's)
End goal: **top ladder on Null's, against any opponent/deck**

**Single source of truth.** [`HANDOFF.md`](HANDOFF.md) is the next-session starting
point. [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) is the simulator/CI reference.
[`docs/native/16.635.1/FINDINGS.md`](docs/native/16.635.1/FINDINGS.md) is the
probe-port evidence log.

---

## 0. Decision — why the target changed

The previous plan selected `cr-native` because it runs the original x86_64
`libg.so` on plain Linux with no AVD/MuMu. That was the right call for **engine
throughput**, but it optimized the wrong variable: **Null's runs 16.635.1
ARM64**, and the 15.535.29 x86 engine cannot connect to Null's ladder.

The repository already contains everything needed for the direct path:

- the **FirstLight V4 General** checkpoint (12.68M, trained across decks);
- the **IL_Replay** corpus locally (252,238 replays / 17,836,160 actions);
- the **16.635.1 offline APK, cluster APK and probe**;
- the A51 cluster/deploy tools and the 16.635.1 static-analysis notebook;
- the upstream **V4 IL/PPO stack**, deck-pool builder, league and evaluator.

So the primary path is the **actual deployment target**: 16.635.1 on the A51,
with the V4 General model. `cr-native` 15.535.29 stays as a fast proxy lab for
throughput, reward/league experiments and parity — not as the source of
deployable weights.

**R0 is retired.** It is a third observation contract with no trained
checkpoint, no local training corpus, no 16.x adapter, and no path to Null's.
The work is archived, not continued.

**What would reverse this:** if the 16.x probe cannot reach usable throughput
after N0–N2, revisit the proxy track with a V4-observation adapter on the x86
engine. Do **not** revive R0.

---

## 1. Success milestones

| Milestone | Definition | Gate |
| --- | --- | --- |
| **N0 Probe** | A51 attached; 16.635.1 cluster APK/probe starts; `coldReady`; `observe_rich` complete; 2 engines | 10 clean matches, 0 crashes, stable control ports |
| **N1 General baseline** | General vs IL and vs scripted X-Bow on 16.635.1 | 0 rejected actions; General ≥ IL; throughput recorded |
| **N2 Port complete** | Remaining core anchors and rich telemetry resolved; cold configure + live commands work | No `kUnavailableOffset`; 100-match soak |
| **N3 Scale** | A51 engine cluster measured; cloud ARM64/AVD option evaluated | Enough matches/hour to feed an update budget |
| **N4 Fine-tune** | PPO/IL from General with deck pool + league | Beats frozen General/IL over N games; no degenerate loops |
| **N5 Ladder** | Policy service + touch injection; live canary on Null's | 20 clean live games; win-rate trend; no rule violations |

Top ladder is the long-term outcome, not a single gate. The first meaningful
result is a General-based bot that plays Null's cleanly and beats the included
baselines.

---

## 2. Architecture

```
                A51 / 16.635.1 cluster APK + probe
                 (nullsroyale.rel.free, ARM64)
                              │ control port (26789+N)
                              ▼
              native_runner V4 environment (BattleEnvV1)
                              │
              ┌───────────────┴────────────────┐
              ▼                                ▼
     V4 General / PPO trainer          evaluation + league
     (cloud GPU; 12.7M model)          (paired seeds, deck pool)
              │
              ▼
      Null's ladder deployment
      (policy service + touch injection)

Proxy lab (not deployable): cr-native 15.535.29 x86 + sandbox 516k stack
```

The GPU trains the model; the **engine is the throughput bottleneck**. Do not
size the cloud budget around the GPU alone.

---

## 3. Current assets

| Asset | Path | State |
| --- | --- | --- |
| General checkpoint | `checkpoints/General/checkpoint-step-00000460.pt` | ready, 12,684,339 params, `ppo-league-snapshot` |
| IL / Active IL / Hog specialists | `checkpoints/` | ready, opponents/baselines |
| IL_Replay corpus | `datasets/royaleapi/` | 252,238 replays, 17,836,160 actions, 1.8 GB |
| V4 training stack | `native_runner/training/v4/` | IL + PPO + deck pool + league + evaluator |
| 16.635.1 probe source/artifact | `native_runner/probe/`, `native_runner/probe/out/libcrprobe-16.635.1.so` | built, needs dynamic verification |
| 16.635.1 APKs | `builds/cr-ai-16.635.1.apk`, `builds/cr-engine-cluster-16.635.1.apk` | built |
| A51 deploy/cluster | `deploy_a51.sh`, `launch_cluster_a51.sh` | ready; device not attached |
| 16.635.1 findings | `docs/native/16.635.1/FINDINGS.md` | in progress; anchors documented |
| A51 parity/probe tools | `tools/native_analysis/` | working for scripted/parity matches |
| cr-native proxy | `.resource/cr-native-sandbox`, `tools/native_bringup/` | working, 15.535.29 only |

---

## 4. Workstream A — 16.635.1 runtime and probe

**Goal:** a verified 16.635.1 engine that can reset, observe, command and report
the rich V4 semantics on the A51.

What is already true:

- The touch JNI hook is port-ready (`0x0156f790`).
- Hand/deck model offsets and the replay-controller trio are recovered.
- The probe is build-selectable for 16.635.1 via
  `build_16_635_1_layout.h`; required anchors fail closed.
- Relocation report: **59 EXACT, 31 LIKELY, 10 MANUAL, 37 AMBIGUOUS,
  10 MISSING**.
- The 16.635.1 build still skips rich telemetry; cold configure / live command
  injection remain closed until the remaining core anchors land.
- Dynamic verification on the A51 is pending.

Next actions, in order:

1. Attach the A51; confirm `adb devices`, `ro.product.model` (`SM_A515U` expected).
2. Install/launch the 16.635.1 cluster (`launch_cluster_a51.sh --engines 2`), forward
   `tcp:26789`.
3. Run `tools/native_analysis/scripted_xbow_match.py` and confirm 0 problems.
4. Inspect `observe_rich`: all V4-required streams present, no sentinel fields.
5. If incomplete, resolve the AMBIGUOUS/MISSING anchors in
   `docs/native/16.635.1/FINDINGS.md` (Ghidra + `cr_elf.py`/`diff_builds.py`).
6. Re-run the 100-match soak before training.

**Gate:** N0 and N2.

`crforge` (`ROADMAP_CRFORGE.md`) reimplements the 16.635.1 client rules without
Android. Use it as a deterministic CI/differential oracle for the probe; it is
not a trainer.

---

## 5. Workstream B — General baseline

Run the included General checkpoint on 16.635.1 before training anything.

```bash
adb forward tcp:26789 tcp:26789
.venv/bin/python -m native_runner.training.v4.evaluate \
    --checkpoint-a checkpoints/General/checkpoint-step-00000460.pt \
    --checkpoint-b checkpoints/IL/checkpoint-step-00029396.pt \
    --games 2 --output /tmp/opencode/eval-general-nulls.json \
    --port 26789 --device cpu
```

Record: win/loss/draw, rejected actions, match ticks, seconds/match,
`normal_terminal_rate`, and any observation-completeness errors. Then run
General vs the scripted X-Bow and the Hog specialist. This tells us whether the
pretrained generalist already works on Null's before spending cloud credits.

**Gate:** N1.

---

## 6. Workstream C — data

The generalist corpus is already local:

- `datasets/royaleapi/` — **IL_Replay**, 252,238 replays, 17.8M actions,
  `royaleapi-battle-replay.v1`.
- Use `native_runner/training/v4/cache_builder` to convert it to IL shards.
- Use `build_ppo_deck_pool` to build the exact-form deck pool for PPO.

Data sources and their limits:

- **Official Clash Royale API:** battle logs only (last ~25 battles), no
  tick-level replays. Not an IL corpus; useful for meta/opponent stats.
- **RoyaleAPI downloader** (`.resource/royaleapi-downloader`): live 16.x
  ladder replays, structured JSON, resumable. It collects **official-game**
  replays, not Null's; follow the site's terms.
- **Hugging Face:** IL_Replay (already local), plus optional
  `Cochon123/clash-royale-replays` and `Proxv/clash-royale-ai-checkpoints`.
- **Null's-specific data:** self-play on the 16.635.1 engine and any Null's
  replays that can be captured within the server's rules. This is the data that
  matters for the ladder.

Do not mix 15.535.29 self-play labels with 16.x IL labels without an explicit
adaptation step.

---

## 7. Workstream D — training

Two stages:

1. **IL cache / behavior cloning** from the local corpus to validate the V4
   pipeline and checkpoint compatibility.
2. **PPO fine-tune from General** with a deck pool, league and opponent pool.
   The upstream PPO launchers assume AVD/engine-ready topology; adding an
   A51 engine-ready path is an N3 task. Until then, use the evaluator and a
   single-engine PPO smoke.

Rules:

- General is the initialization, not a frozen mirror opponent.
- Deck diversity comes from the deck pool + league, not from a fixed X-Bow
  mirror.
- Evaluate on paired seeds, both seats, with fixed decks and levels.
- Keep rejected actions at zero; a rollout with rejections is invalid.
- Do not call self-play actions “expert” for IL.

**Gate:** N4.

---

## 8. Workstream E — deployment to Null's

- The A51 runs the 16.635.1 client with the probe; the policy service supplies
  actions over the control protocol.
- Start with offline/self-play matches, then a live canary on a low-stakes
  account.
- Monitor rejected actions, illegal placements, crashes and server responses.
- Automated ladder play may violate the server's rules; confirm what is allowed
  before running a bot on a real account.

**Gate:** N5.

---

## 9. Workstream F — cloud and scale

The GPU is not the bottleneck. The engine is.

- **ThunderCompute RTX A6000 48 GB — $0.35/hr.** Cheapest useful GPU for the
  12.7M model; ~57 hours for $20. Use it for short benchmark/validation runs.
- **GCP $300 / Azure $100:** useful only if you need a high-vCPU host with a GPU
  or a cloud Android/AVD cluster. New accounts often have **zero GPU quota**;
  check before planning. Free-trial terms apply per account.
- **A100/H100 are unnecessary** for a 12.7M model. L4/T4/A6000 is enough.
- **Do not spend credits before N0/N1 pass.** The first useful spend is a GPU
  fine-tune after the A51 baseline is validated.

If the A51 cannot feed training, the scale options are, in order:

1. Add more A51-class ARM64 devices (cheapest per engine, but hardware-bound).
2. Cloud x86 host with KVM + ARM64 Android image/AVD cluster (the upstream
   path; complex, verify nested virtualization and throughput first).
3. Proxy track with a V4 observation adapter on the 15.535.29 x86 engine
   (fastest engine, but a new adapter and a cross-build port).

---

## 10. Proxy track — cr-native 15.535.29 (retained, not critical path)

The x86 runtime remains valuable for:

- reward/league/PPO algorithm experiments with the 516k stack;
- throughput and transport profiling;
- parity checks against 16.635.1 (towers, cards, unit HP, abilities);
- a fallback if the 16.x probe stalls.

It is **not** the deployment path. Do not train a policy on 15.535.29 labels and
assume it transfers to Null's without an adaptation step.

Measured facts (kept for reference):

- 1 h soak passed; 8 workers is the knee; 12 is oversubscribed on the 10-core
  laptop.
- Engine-only at 8 workers: 36.4k ticks/s ≈ 9.6 matches/s (100 ticks/RPC).
- Full 516k loop at 8 workers: 0.074 matches/s at 1 tick/decision, 0.277 at 4,
  0.522 at 8.
- R0 CPU inference: 125–269 ms/pass; not viable for training without a GPU.
- A51 16.635.1 parity: towers, deck ids/forms/costs, starting elixir and all
  unit HP match at level 11 except Electro Spirit (215 vs 217).

Evidence lives under `builds/native/`.

---

## 11. Retired / deferred

- **R0 elite-v3** — retired from the Null's path. Code and evidence are archived
  in `docs/native/R0_CANONICAL_TREE.md`, `tools/native_bringup/smoke_r0_model.py`
  and `verify_r0_batched.py`. Do not start an R0 run.
- **R0 data/checkpoint gate, CUDA gate, R0 adapter** — retired with R0.
- **cr-native 15.535.29 as the critical path** — demoted to proxy lab.
- **Custom ARM64 `libg` loader** — still retired; the 16.635.1 path uses the
  offline APK/probe.
- **Linux AVD / MuMu / phone clusters as the primary** — only a scale option if
  the A51 is insufficient.
- **“16.635.1 is parity only”** — reversed: it is the primary target.

---

## 12. Risks and unknowns

1. **16.x port completeness.** Rich telemetry and several core anchors are still
   unresolved; N2 may take longer than N0/N1.
2. **Engine throughput.** One A51 is far below the upstream 1,528-match training
   scale. Measure before committing credits.
3. **Null's-specific content.** The General checkpoint and IL_Replay are
   official-game 16.x data; Null's may have custom balance/cards. Verify the
   content fingerprint and card compatibility.
4. **Ladder rules.** Automated play may be prohibited; confirm before a live
   account is used.
5. **Cloud quotas.** GCP/Azure GPU quota may be zero on new accounts; don't
   assume the credits are immediately spendable on GPUs.

---

## 13. QA harness

- `illegal: 0`, zero rejected actions per accepted rollout.
- Paired-seed, both-seat evaluation; fixed decks/levels.
- 100-match soak after any probe change.
- Replay review for same-tile spam, passive openings, wasted spells, bad
  Tesla/X-Bow defense.
- Live canary: 20 games, observation-vs-screen diff, no crashes.
- Record checkpoint SHA-256, dataset/tag selection, deck pool, seed and config
  with every run.

---

## Appendix A — cr-native 15.535.29 evidence index

| Item | Path |
| --- | --- |
| Phase 0 soak/sweep | `builds/native/phase0-20261114T000821Z/` |
| Phase 2 bottleneck | `docs/native/PHASE2_BOTTLENECK.md` |
| X-Bow determinism (20 seeds) | `builds/native/xbow-determinism-20.json` |
| A51 unit HP/ability parity | `builds/native/parity-unit-hp-15.535.29-vs-16.635.1-L11.json` |
| Runtime runbook | `docs/native/PHASE0_BRINGUP.md` |
| Linux runtime | `docs/native/LINUX_BIONIC_RUNTIME.md` |

## Appendix B — R0 archive

R0 adoption and verification are complete as an engineering exercise:
36,854,175 params, schema `825408c3…4d44`, CPU batched-equivalence PASS.
It is retired from this plan. See [`docs/native/R0_CANONICAL_TREE.md`](docs/native/R0_CANONICAL_TREE.md).
