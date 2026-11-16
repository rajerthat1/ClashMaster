# crforge — bulk-training backend, CI oracle, parity lab

Status: **bulk-training plan of record, activated 2026-11-15**
Role: **bulk PPO / self-play throughput** (highest of any option), deterministic CI
oracle, differential-test reference, and the parity lab that keeps a fast trainer
honest. **Never the promotion gate.**
Gate: the **16.635.1 native engine** (x86_64 on this host, A51 as the parity/deploy
oracle). It stays running: on demand, in CI, and on a fixed parity cadence.

Primary plan: [`ROADMAP.md`](ROADMAP.md). Session state: [`HANDOFF.md`](HANDOFF.md).

---

## 0. Decision (2026-11-15) — crforge is the bulk trainer; the engine is the gate

The native path works and is measured, but its **per-packet Python data path**, not
its engine, sets the ceiling: ~8–10 ms per lane (`step` 8.7 ms = engine round 3.1 +
observe 4.0 + masks 1.6, plus tensorize), i.e. **0.08 matches/s per process** and
**4 % GPU utilisation** on the rented A6000. crforge replaces the engine round with
a ~0.1–0.3 ms share of one batched JVM call and returns every lane's observation
zero-copy as NumPy, so the same Python work can be vectorized across 256 lanes.

Decision rule, and the only number that decides the project:

- The win comes from **vectorizing the observation/tensorizer**, not from the
  simulator alone. A straight per-lane port buys ~1.4×; a batched encoder at
  ≤0.5 ms/lane buys ~10–30×.
- **Stage C1 measures it before any scale spend.** If the vectorized encoder is
  not under ~1 ms/lane, stop and fix the encoder; do not add lanes or GPUs.

**What would reverse this:** the encoder cannot be vectorized below ~1 ms/lane
after three iterations, or continuous parity (C5) shows unexplained divergence that
cannot be attributed to the adapter. Then return to the native path, which stays
warm and measured.

**What does not change:** promotion, fine-tune acceptance and deploy go through the
native engine. A policy that wins inside a simulator has found a simulator, not the
game.

---

## 1. Measured baseline (2026-11-15)

One match = 6,068 ticks ÷ 5-tick decisions ≈ **1,214 lane-decisions** (the repo's
convention; a real 3-minute match is ~720, so real matches/s are ~1.7× higher).

**crforge `VectorBattle`, 256 lanes, this host** (`crforge_env.bench`, ladder,
16.635.8 tables; load ~11, `powersave`, so a floor):

| threads | decisions/s | matches/s | ticks/s |
|---:|---:|---:|---:|
| 1 | 3,154 | 2.6 | 15,766 |
| 4 | 7,304 | 6.0 | 36,499 |
| 8 | 15,791 | 13.0 | 78,907 |

Recorded elsewhere: **8,371 decisions/s** at 256/1 on this host (short re-run),
**22,405** at 64/1 and **63,694** at 64/4 on an M4 Pro (crforge's own benchmark),
**127k ticks/s** for the bare Java core on one thread.

**Native 16.635.1 extracted engine** (`HANDOFF.md` §3.41–§3.43):

| shape | decisions/s | matches/s | note |
|---|---:|---:|---|
| training floor, 1 process, 8 lanes | ~100 | 0.082 | lean observation, 7.81 ms/packet |
| raw channel, 1 process, 8 lanes | ~740 | 0.61 | excludes observe + tensorize |
| 8 processes on 10 cores (raw) | ~5,950 | 4.9 | CPU-bound, no GPU |
| today's 4×A51 learner loop | 19–55 | 0.04–0.05 | one packet per forward |

**GPU** (A6000, $0.35/h, 6 vCPU): 4 % utilisation measured during collection;
plan budget **~2 ms per batched forward**, **15.6k decisions/s** from 4×A51,
**~62k decisions/s** at batch 128 ≈ **51 matches/s** per card.

**The packet budget.** At 256 lanes and 8.4k decisions/s the whole per-batch
budget is ~30 ms (≈120 µs/lane). The C1 spike measured the vectorized observation
frame at **~0.03 ms/lane** and the real `BattleEnvV1.observe` at **~0.65 ms/lane**.
Session 17 extended that frame to the full observation facts (both seats' headers
and cards, six towers, placement-mask views, derived age/velocity):
**0.0317 ms/lane** at 256 lanes/1 thread, **4.8×** the bridge's per-lane decoders
(`crforge_bridge/vector_observation.py`,
`runs/crforge-c3/vector-observation.json`). The C3 profiler (2026-11-16) measured
`UniversalObservationTensorizerV4` at
**0.99–1.07 CPU ms/decision** — the tensorizer is the per-lane packet, not the
observation facts. That cost is **3,754 Python calls per decision** (330
`isinstance`, 217 frozen-mapping `__getitem__`, 214 `_finite`, 195
`FrozenMapping.get`), i.e. interpreter ceremony over a 2,200-line semantic
builder, not array math; ~35 % of profiled time is builtin calls plus the
frozen-mapping layer. The old
"13 ms/lane bridge encoder" was the TCP/JSON + full-assembly path. See
`runs/crforge-c1/encoder-spike.json` and `runs/crforge-c3/tensorizer-profile.json`
(`docs/native/16.635.1/FINDINGS.md` §3.44, §3.46).

---

## 2. Plan — stages, gates, cost

Each stage is small enough to abandon. **Do not start a stage until the previous
gate is recorded.**

| # | Stage | Work | Cost | Gate |
|---|---|---|---|---|
| **C0** | Close legality | Fix the last MiniPekka `26000018` reject; map `CARD_PENDING`; 20-game run | 1–2 d, $0 | `illegal: 0`, `truncated: 0`, no unexplained rejects; rejected-actions counter is a first-class gate |
| **C1** | **Encoder spike** | 256-lane `VectorBattle` + `ObservationV1` built two ways: per-lane and vectorized. Time both end-to-end (observe + masks + tensorize) | 1 d, $0 | **measured 2026-11-16 at the default shape** (256 × 200 × 3): vectorized facts **0.0165 ms/lane = 60,702 decisions/s** vs per-lane 0.315 (**19.1×**), equivalence pass; projected end-to-end **0.713 ms/lane = 1.16 matches/s per process**. Bootstrap caveat: the earlier 0.028 ms/lane figure came from a 40 × 1 smoke, and the earlier "tensorize 1.4 ms/lane" from a scripted policy that never played (both fixed) |
| **C2** | Resident adapter (F1) | `crforge_resident.py` over `LiveBattle`/`VectorBattle`, presenting the `NativeClashEnv` surface (`create_match`, `observe`, `observe_atomic`, `observe_rich`, `step`, `queue_*_at`, `attest`, `status`, `close_transport`) | 3–5 d, $0 | **parity measured 2026-11-16:** 60/60 decisions identical to the bridge (ordinary/rich/atomic envelopes, masks, rewards, terminals); **throughput gate MISSED at 1.2–1.3×** (`tools/crforge/resident_parity.py`). The transport is ~1.2–1.7 ms/decision of a ~4–6 ms step; the rest is the observation/mask build, i.e. C3, not the socket |
| **C3** | Fast observation (F2) | `ObservationV1` straight from the NumPy seat row; native `masks`; tower/footprint cache per tick; `policy_features_only=True`; no per-lane dicts | 1–2 w, $0 | **≥ 0.8 matches/s per process**; `illegal: 0`; tensorizer parity to the native path. **Status 2026-11-16: gate met, target open.** Tensorizer profiled at **0.99 CPU ms/decision** replay mean (0.716 min-of-decisions), `batch_digest`-identical after the shipped reductions; end-to-end **0.71–1.02 ms/lane ≈ 0.8–1.2 matches/s per process**. Python tuning is measured out; ≤0.5 ms/lane needs the batched row build (or a C port of `_groups`/`_candidates`/`_relations`/`_card_sets`). **C2's 2× environment-throughput gate is measured here** — the resident adapter removed the wire (1.2–1.3×) and the rest of the step is this build. **Learner deck (hero/evo forms) is rollout-clean 2026-11-17** — 2/2 games, `illegal 0`, `rejected 0`; tensorizer **1.171 CPU ms/decision** (`runs/crforge-c3/learner-deck/`, `FINDINGS.md` §3.45). **Precondition landed 2026-11-18:** the vectorized observation facts layer (`crforge_bridge/vector_observation.py`) measures **0.0317 ms/lane**, 4.8× the per-lane decoders, equivalence pass (`runs/crforge-c3/vector-observation.json`, `FINDINGS.md` §3.46). The row build itself is still open. **First section landed 2026-11-19:** `crforge_bridge/vector_rows.py` builds `_groups`+children for all 256 lanes from the facts layer at **0.0539 ms/lane** vs the 0.2207 per-lane oracle (**4.09×**), equivalence pass over 40 decisions (`runs/crforge-c3/batched-groups.json`, `FINDINGS.md` §3.47). `_card_sets`, `_candidates`/placement, `_relations`, `_towers`, `_spatial_planes` and `_scalars` remain before a facts-only `tensorize` can claim ≤0.5 ms/lane end to end |
| **C4** | Batch + PPO (F3) | 8 lanes/process, 2–4 processes, `ResidentBatchCoordinatorV1`, `capture_mode="compact"`, pipelined inference | 1 w, ~$2 GPU | 10-segment run: 0 rejected, `eligible_act_rate > 0`, native eval not degrading; log env-steps/s, tensorize ms, inference ms |
| **C5** | Parity curve (continuous) | Every adapter change and every N updates: golden-trace hashes + **50–150 native matches**; compare win rate, tick/action counts, tower/elixir trajectories | ~$0.3/w, $0 CPU | win-rate gap inside the Wilson interval; divergence attributable to the adapter, not the policy |
| **C6** | Native promotion gate | ≥150 games, paired seeds, both seats, held-out decks | ~1 h, $0 | the `ROADMAP.md` N1/N4 gate; **never crforge** |
| **C7** | Live canary | Low-stakes Null's account, 50–100 matches; monitor rejects, illegal placements, crashes, server responses | $0 | 20 clean games first, then a slow ladder push; official servers are **out of scope** |

**Stop conditions:** C1 misses its gate twice → fix the encoder, do not scale.
C5 shows a persistent win-rate gap → freeze the crforge checkpoint and diagnose;
do not "train through it". Any parity change that cannot be explained → native.

---

## 3. Fidelity contract (pin before C4, not after)

Every item below is a known place where a fast trainer can silently learn a
different game. Record the value in the run artifact and add a test where possible.

- **Timing:** `decision_ticks=5`, `play_delay_ticks=20`
  (`Standard1v1Battle.PLAY_DELAY_TICKS`). Verify the native env uses the same.
- **Rules/levels:** `LadderConfig(level=16, tower_level=16)`; tower troops and forms
  via `SideSetup`; the deck pool's exact forms, not just names. **Learner deck is
  fixed to X-Bow 3.0** (`x-bow · tesla-ev1 · archers-ev1 · knight-hero ·
  skeletons · electro-spirit · fireball · the-log`, Princess tower); the
  **opponent's** deck is sampled. The tower troop is verified as
  `tower-princess` (`159000000`) by the clean learner-deck rollout on 2026-11-17
  (`FINDINGS.md` §3.45).
- **Cards:** `crforge_catalog.json` is 152/152 cards, 118 forms, `unmapped: []`;
  keep it matched to the release pin and re-check on every table bump.
- **Legality:** delay-aware elixir, pending/`last_run_refusal`, typed
  `CARD_PENDING`, pending hand slots in the action mask; **rejected actions are
  zero**, and a reject invalidates the rollout.
- **Determinism/attestation:** `attest` on every run; pin `crforge-data.lock` and
  the tables; write the lock commit and table hash into the checkpoint artifact.
- **Version:** 16.635.8 tables on the 16.635.1 client pin. Do not mix 15.535 and
  16.635 contracts in one run; the trainer fails closed on a mismatch.

### 3.1 Outside dependencies (pinned; not on the bulk path)

| Dependency | Pin | Used for | Not used for |
| --- | --- | --- | --- |
| **Clashback** `game/engine/libg.so` + `vendor/crx` | `002aa8c`, libg `c4721bd9…`, build `160635001` | the **native gate** (`crx_engine_host.py`), replay/parity traces via `neo_replay` | bulk collection; it is a local-use-only game binary, never redistributed |
| **RoyaleFish** `data_16635/` | `da9c8cf` | already adopted as the verified 16.635 catalog release | a second rules implementation |
| **RoyaleFish** `B-general-v275`, `A-hog26-v738` | `da9c8cf` | league seed, gate reference opponents, `A-hog26` as the strength bar (71.9 % ±2.6 / 1,147 games) | init weights for a 15.535 checkpoint |
| **RoyaleFish** `ever_*` | `da9c8cf` | **C4 throughput recipe**: fp16 + action equality, multi-size CUDA graphs, central LSTM state table, sparse/row-packed updates, loader process, **NVIDIA MPS**; ~2.5–3k games/h on a 5060 Ti + V100 | anything before C4; the adapter gates come first |
| **RoyaleFish** patches | `da9c8cf` | reference diff only | applying as-is (different base tree) |
| **RoyaleFish** Hog 2.6 replay set (HF) | announced 2026-11-15 | opponent IL anchor / meta snapshot (~5–10k top-player games) | X-Bow learner IL; check the licence when it lands |
| **RoyaleFish** 15.535→16.635 converter | announced 2026-11-15 | remap card rows for **same-architecture** V4 checkpoints | FirstLight-Zero's 1.17M mirror model (different architecture) |

Record both commits in every checkpoint/run artifact. If either repo moves, fork
and pin; do not float.

---

## 4. Economics (what the budget decision turns on)

1M matches at $0.35/h for the A6000 box:

| path | matches/s | wall clock | GPU-hours | GPU $ |
|---|---:|---:|---:|---:|
| today's loop | 0.045 | 257 d | 6,173 | **$2,161** |
| native, 1 process, training floor | 0.082 | 141 d | 3,387 | $1,185 |
| native, 6 processes on 6 vCPU | 0.49 | 24 d | 567 | $198 |
| crforge 1 thread, this host | 2.6 | 107 h | 107 | $37 |
| crforge, straight port (1 m/s core) | 1.0 | 278 h | 278 | $97 |
| crforge, vectorized (20 m/s) | 20 | 14 h | 14 | $4.9 |
| crforge, GPU-saturated | ~51 | 5.6 h | 5.6 | **$1.9** |

Provisioning ratio: today ~1 A6000 per 100+ cores (the card idles at 4 %);
crforge with a vectorized encoder ~**1 A6000 per 8–16 vCPU**. That is the shape of
the box already rented. Scale by adding identical boxes (~4.3M matches/day each,
~$8/day), not bigger GPUs.

The **GPU is not the target**; matches/s per dollar at a gateable policy is. Do not
chase 100 % utilisation: the 12.7M/17.5M model is launch-bound, and forcing batch
size up to fill the card raises latency without buying match throughput.

---

## 5. CI and parity role (always on)

- **Golden trace:** seed + actions → per-tick observation hash; diff on every
  adapter/bridge change.
- **Differential parity:** same deck/actions on crforge and native; compare entity
  counts, target choices, death ticks, tower HP, elixir.
- **Contract tests:** `configure`/`attest`/`observe` shape, catalog mapping,
  action legality, refusal mapping.
- **Nightly 20-game smoke:** `illegal: 0`, `truncated: 0`, no hangs.
- **Freeze the data:** pin `crforge-data.lock`, record the lock commit in every
  artifact. A silently different engine must not produce a checkpoint you cannot
  reproduce.

---

## 6. Proven commands

```bash
# bridge (evaluation only; ~20–30 decisions/s with full BattleEnvV1 assembly)
.venv/bin/python -m crforge_bridge --port 26789 --tables ~/.crforge/tables/16.635.8

# raw benchmark (the C1 baseline)
PYTHONPATH=.resource/crforge/python .venv/bin/python -m crforge_env.bench \
  --install .resource/crforge/env/build/install/env \
  --tables ~/.crforge/tables/16.635.8 \
  --engine ladder --counts 1 16 64 256 --steps 200 --repeats 3 --threads 1

# encoder spike target: same vector, observations built both ways
.venv/bin/python tools/crforge/encoder_spike.py \
  --count 256 --threads 1 8 --steps 200 --repeats 3 \
  --per-lane-lanes 16 --tensorize-port 26789 \
  --output runs/crforge-c1/encoder-spike.json

# C3 attribution: tensorizer half of a live decision, section split + batch digest
.venv/bin/python tools/crforge/profile_tensorizer.py \
  --port 26789 --decisions 240 --profile-decisions 150 --repeats 5 \
  --output runs/crforge-c3/tensorizer-profile.json

# C3-B precondition: vectorized observation facts vs the bridge's per-lane decoders
.venv/bin/python tools/crforge/vector_observation_spike.py \
  --count 256 --threads 1 8 --steps 200 --repeats 3 --per-lane-lanes 16 \
  --output runs/crforge-c3/vector-observation.json

# C3-B first section: batched causal groups vs the per-lane tensorizer oracle
.venv/bin/python tools/crforge/batched_groups.py \
  --count 256 --steps 40 --repeats 3 --timing-samples 8 --verify-lanes 32 \
  --output runs/crforge-c3/batched-groups.json

# C2 gate: resident adapter vs the socket bridge — parity digests then paired timing
.venv/bin/python tools/crforge/resident_parity.py \
  --decisions 60 --output runs/crforge-c2/resident-parity.json

# end-to-end evaluation (native gate; never a promotion source on crforge)
.venv/bin/python -m native_runner.training.v4.evaluate \
  --checkpoint-a checkpoints/General/checkpoint-step-00000460.pt \
  --checkpoint-b checkpoints/IL/checkpoint-step-00029396.pt \
  --games 2 --output evaluations/native-gate

# native parity oracle (kept warm, on demand)
.venv/bin/python tools/native_analysis/crx_engine_host.py --seconds 1800
```

Tests: `.venv/bin/python -m pytest crforge_bridge/tests -q`

---

## 7. Non-goals

- **Official Supercell servers.** Different client/version/protocol, ToS and legal
  exposure; nothing in this repo targets it. Null's 16.635.1 is the only target.
- **crforge as a promotion gate or evidence source.** It is a bulk backend.
- Reimplementing `ObservationV1` in Java.
- Full `observe-rich` parity before C3 shows the minimal field set is insufficient.
- Training on `IL_Replay` as the main path (it is the generalist corpus).
- Porting FirstLight-Zero or Proxv weights blindly; reuse the recipe, not the weights.
- A 4–6 month adapter project. If C1–C3 cannot land in ~4 weeks, stop at C1's
  measured result and reassess.
- Deleting or retiring the native engine. It is the gate and the deploy rig.

---

## 8. Maintenance rules

- Pin `crforge-data.lock` and the tables; fork if upstream moves.
- Do not let crforge "fix" state in the adapter. If crforge is wrong, fix crforge or
  report the field as `null`.
- Every new field group needs a unit test and a 20-game sanity run.
- Every checkpoint artifact records: adapter commit, table lock, bench result
  (decisions/s, ms/packet), and the native parity run it was checked against.
- If the native path becomes competitive again (fused rich frame + C encoder), keep
  this document and re-evaluate on measured matches/s per dollar, not on raw ticks.
