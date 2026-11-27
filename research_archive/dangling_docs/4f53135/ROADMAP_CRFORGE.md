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

**Rate units.** Two conventions coexist and differ by 2×: **1,214 lane-decisions
per match** (one seat, 6,068 ticks) is this document's `matches/s`; **2,428**
(both seats) is what `self_play.py` writes into its artifact. The same 128-lane
run is **0.33 matches/s** here and **0.163** in
`runs/crforge-c4/gpu-forward-scaling-128-long.json`. Quote the convention with
the number. Every rate below is **per process** unless it says "per host": a
process owns one single-threaded `VectorBattle` and one GPU forward, so N
processes ≈ N × the per-process rate until the card saturates (§4).

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
| **C3** | Fast observation (F2) | `ObservationV1` straight from the NumPy seat row; native `masks`; tower/footprint cache per tick; `policy_features_only=True`; no per-lane dicts | 1–2 w, $0 | **≥ 0.8 matches/s per process**; `illegal: 0`; tensorizer parity to the native path. **Status 2026-11-16: gate met, target open.** Tensorizer profiled at **0.99 CPU ms/decision** replay mean (0.716 min-of-decisions), `batch_digest`-identical after the shipped reductions; end-to-end **0.71–1.02 ms/lane ≈ 0.8–1.2 matches/s per process**. Python tuning is measured out; ≤0.5 ms/lane needs the batched row build (or a C port of `_groups`/`_candidates`/`_relations`/`_card_sets`). **C2's 2× environment-throughput gate is measured here** — the resident adapter removed the wire (1.2–1.3×) and the rest of the step is this build. **Learner deck (hero/evo forms) is rollout-clean 2026-11-17** — 2/2 games, `illegal 0`, `rejected 0`; tensorizer **1.171 CPU ms/decision** (`runs/crforge-c3/learner-deck/`, `FINDINGS.md` §3.45). **Precondition landed 2026-11-18:** the vectorized observation facts layer (`crforge_bridge/vector_observation.py`) measures **0.0317 ms/lane**, 4.8× the per-lane decoders, equivalence pass (`runs/crforge-c3/vector-observation.json`, `FINDINGS.md` §3.46). The row build itself is still open. **First section landed 2026-11-19:** `crforge_bridge/vector_rows.py` builds `_groups`+children for all 256 lanes from the facts layer at **0.0539 ms/lane** vs the 0.2207 per-lane oracle (**4.09×**), equivalence pass over 40 decisions (`runs/crforge-c3/batched-groups.json`, `FINDINGS.md` §3.47). **Card and relation sections landed 2026-11-19:** `VectorCardBuilder` (including the 21-column pending/ability schema and the even-footprint target offset) is **0.00161 ms/lane** vs 0.0718 (**44.5×**) and `VectorRelationBuilder` is **0.00984** vs 0.0522 (**5.30×**), both equivalence pass (`runs/crforge-c3/batched-cards.json`, `FINDINGS.md` §3.48). **Candidate section landed 2026-11-19:** `VectorCandidateBuilder` (legal hand slots under the environment's rule, native 32×18 placement grid, static building geometry incl. the even-footprint subcell offset, UIDs) is **0.00086 ms/lane** vs 0.0531 (**61×**), equivalence pass over 40 decisions with 5,181 queued-slot and 1,171 building-row cases (`runs/crforge-c3/batched-candidates.json`, `FINDINGS.md` §3.49). **Tower section landed 2026-11-19:** `VectorTowerBuilder` (six rows, model-kind order under a flip, tile position/extent, HP ratio, active/target; static tower troop and empty `status` pinned) is **0.00104 ms/lane** vs 0.0349 (**33.5×**), equivalence pass over 40 decisions (`runs/crforge-c3/batched-towers.json`, `FINDINGS.md` §3.50). **Spatial planes landed 2026-11-19:** `VectorSpatialPlaneBuilder` (14 channels, per-side categories, footprint fill, float32-sequential accumulation, towers/neutral rows skipped) is **0.00392 ms/lane** vs 0.0499 (**12.7×**), equivalence pass over 40 decisions (`runs/crforge-c3/batched-spatial-planes.json`, `FINDINGS.md` §3.51). **Match scalars landed 2026-11-19:** `VectorScalarBuilder` (18 scalars, compiled mode timeline, no tracker/no events) is **0.000286 ms/lane** vs 0.00379 (**13.2×**), equivalence pass over 40 decisions (`runs/crforge-c3/batched-scalars.json`, `FINDINGS.md` §3.52). **Empty effects/events landed 2026-11-19:** `VectorEmptySectionBuilder` widens the stock zero-row/masked records with the validated padding sentinels, **0.000141 ms/lane** vs 0.0202 (**143×**), equivalence pass (`runs/crforge-c3/batched-empty-sections.json`, `FINDINGS.md` §3.53). The row build is complete; only the facts-only `tensorize` wiring (`batch_digest` identity) remains before the ≤0.5 ms/lane end-to-end claim. **C3-B closed 2026-11-19:** `crforge_bridge/vector_tensorize.py` assembles every section into one `UniversalSemanticBatchV4` with lanes as the batch dimension and carries the batched previous-action record; `tools/crforge/batched_tensorize.py` proves **1,504/1,504 lane-decisions digest-identical** to the per-lane tensorizer over 256 lanes × 40 decisions (values, shapes and dtypes, with sampled actions recorded on both paths) and measures **0.0807 ms/lane** stacked vs **0.625** per lane (**7.75×**), **0.0315** facts + **0.0060** record actions → **0.118 ms/lane end to end** (8,460 decisions/s), under the 0.5 gate (`runs/crforge-c3/batched-tensorize.json`, `FINDINGS.md` §3.54). **Both seats gated 2026-11-19:** the frame fix (game coordinates once per layer; per-seat pending/placement conversion; actor-seat-aware builders) is closed and proven against the x86_64 engine (`runs/crforge-c3/frame-crosscheck.json`, `FINDINGS.md` §3.55). `batched_tensorize.py --seats 0 1` is **3,008/3,008 lane-decisions** digest-identical (1,504 per seat), **0.0811 ms/lane-decision** stacked vs 0.605 oracle (**7.46×**), **0.1029 ms/lane-decision end to end** (~9,719 decisions/s), seed 2 **3,008/3,008**, 0.0787, 8.50×, 0.0992 (`runs/crforge-c3/batched-tensorize.json`, `-seed2.json`). The gate found and fixed two seat-1 defects: relation tower tokens now resolve through the tower model-row map, and group mean velocity reproduces the stock accumulator's signed zero. Bridge suite **55 tests**. **C4 is unblocked.** |
| **C4** | Batch + PPO (F3) | 8 lanes/process, 2–4 processes, `ResidentBatchCoordinatorV1`, `capture_mode="compact"`, pipelined inference | 1 w, ~$2 GPU | 10-segment run: 0 rejected, `eligible_act_rate > 0`, native eval not degrading; log env-steps/s, tensorize ms, inference ms. **Unblocked 2026-11-19:** both actor seats pass the whole-record digest gate at 0.103 ms/lane-decision (stacked tensorize + record + facts). **Action path landed 2026-11-19:** `crforge_bridge/vector_actions.py` decodes a stacked `ActionSequenceV4` into the engine index for both seats, **3,008/3,008 lane-decisions identical** to the stock per-lane decoder (0.000232 ms/lane-decision, 0 engine rejects, `runs/crforge-c4/vector-decode.json`, `FINDINGS.md` §3.56). **There is no native producer to reuse** — `producer.py` and the PPO worker are `ObservationV1` lanes — so C4 needed its own loop, and it is now **running 2026-11-19**: `tools/crforge/self_play.py` plays one `VectorBattle` of 8 lanes with both actor seats in one recurrent forward, decodes both seats back into engine actions, and steps the engine one tick at a time so the second micro-action keeps its delay bin (`plan_window_ticks`). **Gate shape 8 lanes × 10 segments × 64 decisions: 10,240 lane-decisions, 429 ACT rows / 9,877 eligible (4.34 %), `rejected 0` (0 illegal, 0 faults), 39.0 lane-decisions/s, 97.6 env-steps/s on CPU** (`runs/crforge-c4/self-play.json`, `FINDINGS.md` §3.57). The forward is 94 % of the timed packet (359.7 ms median at 16 rows vs 13.4 tensorize + 3.2 decode), so the remaining C4 work is GPU throughput and rollout storage, not the bridge. **Rollout storage landed 2026-11-19:** `crforge_bridge/rollout.py` rebuilds the native PPO reward (terminal result + potential-based shaping, gated against `BattleEnvV1._rewards`) and finalizes one `StoredRolloutV4` per segment with a boundary bootstrap; the producer stores every decision by default (`--no-rollout` keeps the probe).  The second micro-action is now exercised end to end by a timing probe (`--force-second-micro`), not only by unit tests.  **Update landed 2026-11-19:** `distributed_ppo_update_v4` splits the time axis at every episode boundary (`_episode_segments`/`_episode_chunks`), so a lane that autoresets mid-segment replays as contiguous slices and non-resetting lanes keep their state; `self_play.py --ppo-update` runs the update over the same `StoredRolloutV4`. **Gate shape 8 × 64: 1,024 lane-decisions, 45 ACT / 983 eligible (4.58 %), 0 rejected, one 1,024-frame update, `approx_kl` 2.9e-5, clip 0** (`runs/crforge-c4/self-play-update.json`, `FINDINGS.md` §3.59). **Multi-episode 8 × 2 × 60 with `--max-ticks 600`: 1,920 lane-decisions, 56 / 1,559 ACT (3.59 %), 0 rejected, 16 mid-rollout `episode_start` rows, 2 × 960-frame updates, behaviour hash changes after the first update** (`runs/crforge-c4/self-play-update-rollout.json`). **Training loop landed 2026-11-19:** `crforge_bridge/training.py` keeps **one AdamW for the whole run** and states the recurrence policy at an update boundary (`--update-state-policy reset|carry|wave`; wave freezes the behaviour weights for `--wave-segments` segments, updates at the wave end, then resets). The gate is **two consecutive finite updates** with a reward/return trace and a behaviour hash before/after, and `--save-checkpoint` writes the weights + optimizer + loop counters then reloads them to compare weight bytes. Reset policy, 8 × 4 × 64: **4,096 lane-decisions, 139 / 3,987 ACT (3.49 %), 0 rejected, 4 updates / 4 optimizer steps / 4 behaviour ids, `return_mean` 0.227 → 0.258 → 0.296 → 0.256, `approx_kl` ≤ 2.9e-5, checkpoint step 279 reload-identical** (`runs/crforge-c4/self-play-train-reset.json`, `FINDINGS.md` §3.60). Wave policy, 8 × 4 × 48: **3,072 lane-decisions, 103 / 2,944 ACT (3.50 %), 4 updates, each wave sampled under one frozen id, in-wave second-update clip 4.7 %** (`runs/crforge-c4/self-play-train-wave.json`). Autoreset + `carry`, 4 × 2 × 60 with `--max-ticks 600`: **960 lane-decisions, 8 mid-rollout `episode_start` rows, 2 updates, 0 rejected** (`runs/crforge-c4/self-play-train-autoreset-carry.json`). Finding: the first default (`--ppo-learning-rate 1e-4`, 10× the native) collapsed ACT 45 → 14 → 0 → 0; the default is now the native **1e-5**. **Resume landed 2026-11-19:** `--resume` loads a saved checkpoint's persistent optimizer and loop counters, refuses one that has no Adam state, no `crforge.loop` block or a weight-digest mismatch, rebases `base_update_step` so the saved `update_step` continues instead of double-counting (source 275 → 277, resume 277 → 279; `runs/crforge-c4/self-play-resume.json`, `FINDINGS.md` §3.61). **CUDA measured 2026-11-19** on a rented A6000: update **7.5x faster** (5.5 s vs 44.9 s per 1,024 frames, gate pass, checkpoint step 277 reload-identical, `--resume` continues to 279), collection **slower** at the 8-lane gate shape (26.3 vs 47.6 ld/s) and **395.8 ld/s at 128 lanes**; three device defects fixed (per-decision model validation under `--no-validate`, unsynchronised timing buckets, a CPU/CUDA rollout store) and cuDNN's eval-mode RNN-backward refusal. The shape is one process at 64-128 lanes, not 2-4 x 8; a 48 GB card OOMs a 2,048-frame update at the default minibatch. `runs/crforge-c4/self-play-gpu-*.json`, `FINDINGS.md` section 3.62. **Cadence measured 2026-11-19:** `tools/crforge/cadence_compare.py` runs reset/carry/wave at one shape and classifies every update on-policy or stale from the behaviour-id chain; wave's stale half clips **3.0 %** against **0** and costs **160x** the on-policy KL with no wall or reuse gain, so `reset` stays the training default, `wave` is a diagnostic, and `carry` is ranked on a learning horizon (`runs/crforge-c4/cadence-compare.json`, `FINDINGS.md` section 3.63). **Horizon measured 2026-11-19:** three seeds × 50 updates reverse the one-seed lead — `reset` last-10 return **0.483** vs `carry` **0.409**, `carry` wins **1/3** seeds and spans **0.274** against reset's **0.031** — so `reset` stays the default and `carry` is unresolved; the trace's `reward_sum` cancels by antisymmetry and `reward_abs_max` is now recorded (`runs/crforge-c4/learning-horizon.json`, `FINDINGS.md` section 3.66). **Reward share measured 2026-11-19:** observed reward is **0.2–0.8 %** of a typical update's return (median, six 50-update runs), only **5/300** updates are reward-dominated, and the one-seed `carry` leader contains no terminal and no tower in 50 segments; the 24/60/120-decision probe raises the share 3.1x from 24 to 60 and then flattens (2.5e-2 at 120, 19 % of updates reward-dominated), so the return is treated as a value-fit diagnostic, not a score (`runs/crforge-c4/learning-horizon.json`, `runs/crforge-c4/reward-share.json`, `FINDINGS.md` section 3.67). **Timing accounting fixed 2026-11-19:** the A6000's "unattributed 228 ms" is a **median-vs-mean** artifact — one cold first decision of ~5–7 s at every batch size; two 128-lane shapes solve **F ≈ 4.0 s + 523 ms/decision = 490 ld/s steady state** against the recorded 396, and every 8-decision GPU rate is 65–82 % one-time cost. Producer now records `ms_mean`/`ms_max`/`ms_first` and the residual, and `--warmup-forward` primes the sampled path with the RNG restored (`runs/crforge-c4/timing-accounting-gpu.json`, `FINDINGS.md` section 3.67). **Still open:** the native parity cadence, the GPU re-measurement with `--warmup-forward` at 8–128 lanes, and the A6000 overlapped-loop gate |
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
- **Coordinate frame:** the model input is **game coordinates** (the native
  probe's frame). Engine masks/actions/pending points are **seat-framed**
  (`Schema.toSeatX(0, x) = width - x`, `toSeatY(1, y) = height - y`); the
  perspective transform is what maps game -> model per actor. Crforge emitted
  the seat frame for observations until 2026-11-19, which mirrored every
  crforge board horizontally. **Fixed and gated 2026-11-19:** the facts layer,
  the bridge `entity_rows`, each seat's pending/placement conversion and every
  row builder now produce the game frame; `tools/crforge/frame_crosscheck.py`
  proves a native play and a crforge play spawn the same units at the same game
  points (`runs/crforge-c3/frame-crosscheck.json`, `FINDINGS.md` §3.55). Every
  vector-fact or bridge coordinate change re-runs that native cross-check
  before a bulk run.
- **Action space (C4):** the batched decode emits the engine's flat index —
  `0` wait, `1 + slot * rows * cols + row * cols + col` play — with the model ->
  game -> seat conversion applied once (`crforge_bridge/vector_actions.py`,
  `FINDINGS.md` §3.56). Gated at **3,008/3,008** lane-decisions against the stock
  per-lane decoder for both seats. Two envelope pins: **deploy candidates only**
  (no ability source, no Mirror runtime), and **one engine action per seat per
  decision step**, so the second V4 micro-action needs either a one-tick step
  loop (its execute offset is decoded) or an explicit envelope rule. Subcell
  offsets do not exist in the vector action index; the engine places
  even-footprint buildings at its own anchor. A decoded index is applied only
  if the engine mask is open, and a closed index fails the gate rather than
  being repaired. **The producer takes the one-tick loop (2026-11-19):** the
  engine runs `decision_ticks=1` and is stepped five times per policy decision,
  each micro-action is given at `offset - 1` ticks inside the window
  (`plan_window_ticks`), a collision shifts the later one a tick and a full
  window drops it — both counted on the artifact. A checkpoint whose window is
  shorter than `1 + max(delay_offset_ms) // 50` is refused, so a narrow policy
  cannot silently lose the second action. `FINDINGS.md` §3.57.
- **Row-build envelope (C3-B):** every batched section reproduces the bridge's
  no-event envelope — empty `revealed_cards`, no evolution/ability runtime,
  singleton causal groups, empty tower `status`, a static `tower_troop_id`, and
  the checkpoint's own `model_config` (21 card-runtime columns for
  `B-general-v275`). Each section is gated against the per-lane tensorizer on
  the same facts (`FINDINGS.md` §3.47–§3.55); widening the envelope is a
  separate fidelity gate, not a tuning change.

### 3.1 Outside dependencies (pinned; not on the bulk path)

| Dependency | Pin | Used for | Not used for |
| --- | --- | --- | --- |
| **Clashback** `game/engine/libg.so` + `vendor/crx` | `002aa8c`, libg `c4721bd9…`, build `160635001` | the **native gate** (`crx_engine_host.py`), replay/parity traces via `neo_replay` | bulk collection; it is a local-use-only game binary, never redistributed. **Upstream is unreachable as of 2026-11-15 (404: deleted or made private) — the local checkout is the only copy. Back it up; never `git clean`, re-clone or re-point it** |
| **RoyaleFish** `data_16635/` | `c42108d` (README-only since `da9c8cf`) | already adopted as the verified 16.635 catalog release | a second rules implementation |
| **RoyaleFish** `B-general-v275`, `A-hog26-v738` | `c42108d` (README-only since `da9c8cf`) | league seed, gate reference opponents, `A-hog26` as the strength bar (71.9 % ±2.6 / 1,147 games) | init weights for a 15.535 checkpoint |
| **RoyaleFish** `ever_*` | `c42108d` (README-only since `da9c8cf`) | **C4 throughput recipe**: fp16 + action equality, multi-size CUDA graphs, central LSTM state table, sparse/row-packed updates, loader process, **NVIDIA MPS**; ~2.5–3k games/h on a 5060 Ti + V100 | anything before C4; the adapter gates come first |
| **RoyaleFish** patches | `c42108d` (README-only since `da9c8cf`) | reference diff only | applying as-is (different base tree) |
| **RoyaleFish** Hog 2.6 replay set (HF) | **cancelled 2026-11-16** | nothing — RoyaleAPI closed replay access, the collector stopped at a few hundred games, upload postponed with no date | the Hog IL anchor and the meta snapshot |
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
| **crforge, measured A6000, 1 x 128 lanes (2026-11-19)** | **0.33** | 35 d | 842 | **$295** |

The measured row is `FINDINGS.md` section 3.62: the A6000 makes the *update* 7.5x faster
and the collection slower at 8 lanes, so the projection above was a projection of the
forward alone. The producer's per-decision Python packet is the ceiling.

Provisioning ratio: today ~1 A6000 per 100+ cores (the card idles at 4 %);
crforge with a vectorized encoder ~**1 A6000 per 8–16 vCPU**. That is the shape of
the box already rented. Scale by adding identical boxes (~4.3M matches/day each,
~$8/day), not bigger GPUs.

The **GPU is not the target**; matches/s per dollar at a gateable policy is. Do not
chase 100 % utilisation: the 12.7M/17.5M model is launch-bound, and forcing batch
size up to fill the card raises latency without buying match throughput.

### Scale-out arithmetic (from the 2026-11-19 C4 measurement)

The measured 0.33 matches/s is **one process** at 128 lanes, and it is CPU-side:
the forward is 157 ms of a 647 ms decision (**24 %**), and the other 490 ms is
tensorize + decode + engine steps + facts + **~228 ms of unattributed Python**.
Three rules follow:

1. **Multiproc before a bigger card.** A process is ~1 core + one forward, so N
   processes on an N-vCPU host ≈ N × 0.33, bounded by GPU share. At 24 % each,
   **8 processes ask for 194 % of one A6000**; run ~4 without change, or add
   **NVIDIA MPS + one batched decision server per GPU** (`HANDOFF.md` →
   "Judgment — fix the data path, then scale") so every waiting collector is
   served in one call. The forward is flat in rows
   (141 ms at 256 rows vs 88.5 at 16), so that batch costs far less than N
   separate calls.
2. **2.8 matches/s is per process, not per host.** The engine step is 75 ms and
   single-threaded (`VectorBattle(count, 1)`), so `256 / 0.075 ≈ 3,413
   lane-decisions/s ≈ 2.8 matches/s` is one process's floor once the Python
   packet is gone. N processes carry N engines; the host ceiling is N × 2.8
   minus GPU contention.
3. **A faster core is not a SKU; more vCPUs is.** The rented vCPUs are 2–13×
   slower than the laptop on the Python stages (`FINDINGS.md` §3.62), so
   "stronger CPU" means a different provider, while 8 vCPU vs 6 vCPU is 23 %
   more money for 33 % more cores.

Per instance at the current packet (list prices; verify at checkout):

| shape | matches/s | $/h | $ / 1M matches |
|---|---:|---:|---:|
| 1 process (today's measured row) | 0.33 | 0.35 | **295** |
| **6 vCPU, live A6000 (2026-11-16)** | **1.215 measured** (1.331 ceiling) | 0.35 | **80** |
| **6 vCPU, live A6000, new record (2026-11-17)** | **1.512 measured** (1.293 ceiling) | 0.35 | **64** |
| **6 vCPU, live A6000, full-call graph (2026-11-17)** | **2.221 measured** (1.842 ceiling) | 0.35 | **44** |
| 6–8 processes, no batching | 1.3–1.6 | 0.43 | **75–92** |
| 8 processes + MPS + batched server | 2.0–2.6 | 0.43 | **46–60** |
| packet ~200 ms, ~4 processes/GPU | ~4.2 | 0.43 | **~28** |

So the order at the current packet is **the full-call graph (measured) →
multiproc → de-Python the CPU half → more boxes**.  The card serves 3.24
producer processes at the full-graph terms, so a box is still one card plus a
handful of vCPUs, not a bigger GPU.  Buying a second GPU before the first is
fed buys idle time, and buying a bigger GPU buys the forward that is already
the smaller half at 128 lanes.

### Capacity equation (the provider decision, 2026-11-16)

`tools/crforge/cloud_capacity.py` makes the provider comparison executable:

```
matches/s ≈ 0.211 × min(N_proc, G × E / g) / T
```

- `0.211` is the **shape constant** — 128 lanes × 2 seats = 256 lane-decisions
  per round ÷ 1,214 lane-decisions per match. The tool recomputes it for any
  shape; hold the shape fixed when comparing providers.
- `N_proc` is the usable vCPU count (one producer owns one core, one
  `VectorBattle` and one forward), `G` the GPU count (fractional for a MIG
  slice), `E` the fraction of each card the workload may use, and
  `g = inference_ms / decision_ms` the forward's share of the decision.
  `G × E / g` is therefore **how many producer processes one card serves**.
- `T` is the steady-state decision wall; the tool uses the last warm segment and
  warns on a short or `--no-warmup-forward` artifact.
- The verdict is `N_proc` against `G × E / g`, independent of `0.211` and `T`.
  Rent vCPUs up to the crossover, then GPUs; below it the card is idle, above it
  the extra cores queue. `--sweep-procs 1 2 4 6` validates the crossover by
  running that many producers concurrently and separating a forward slowdown
  (GPU) from a CPU-half slowdown (cores).

Recorded A6000 terms at 128 lanes: `T = 0.6468 s`, `g = 0.2434`, so **one card
serves 4.11 processes**; one process is 0.326 matches/s (the 0.33 row above),
six vCPUs are **GPU-bound** at 1.34 matches/s, $72.6 / 1M at $0.35/h (1,214
convention; a physical match, 2,428, is twice that).

Live 6-vCPU A6000 (2026-11-16, Thunder `base`, `runs/crforge-c4/
cloud-thunder-a6000-6vcpu.json`): `T = 0.4287 s`, forward **158.4 ms**,
`g = 0.3695`, so **2.71 processes**; CPU ceiling 2.952, **GPU ceiling 1.331**.
One process is 0.492; six concurrent producers measured **1.215 (0.91× the
ceiling)**, **$73.0/M predicted, $80.0/M measured**. The recorded box and the
live box have different CPUs (`T` 0.6468 vs 0.4287) and different process
crossovers (4.11 vs 2.71), but the same forward (157–158 ms) and the same
**~1.33 matches/s ceiling**. **The ceiling belongs to the card; `G × E / g`
belongs to the CPU half.**

That resolves the SKU question at the pre-graph packet: **6 vCPU is the A6000
configuration.** The 8-vCPU best case was the same 1.331 ceiling, so at $0.43/h
it was **$89.7/M**, above the 6-vCPU measured $80.0/M; break-even needed 1.493
matches/s, which the card could not deliver at `g = 0.37`. The full-call graph
moves both sides (see the update below), so the 8-vCPU case is a measurement
again, not a closed question.  At the full-graph terms the card serves 3.24
processes, so the provider word remains **~1 A6000 per 4–6 vCPU**; the 8–16
crossover still needs a lower `g`.

**The new record moves the measured rate, not the ceiling (2026-11-17).**
`runs/crforge-c4/cloud-thunder-a6000-s38.json`: `T = 0.4176 s`, forward
**161.1 ms**, decode **11.7 ms** (the 68 ms sync storm is gone), CPU half
254.5 ms, `g = 0.390`, so **2.56 processes** and a **1.293** ceiling.  Six
concurrent producers measured **1.512 matches/s**, **1.17× the predicted
ceiling** and **+20.6 % over the 2026-11-16 1.215**, at **$64.3 / 1M**.  The
ceiling did not move (this box's forward is slower, 161 vs 158); the *measured*
aggregate did, because removing ~1,300 device syncs per decision removed a
contention amplifier.  The full-call graph on the same box is the next ceiling
move: one process **0.592 matches/s**, `g = 0.309`, 3.24 processes and a **1.842**
predicted ceiling; six producers measure **2.221 matches/s** (**1.21×** that
ceiling) at **$43.8 / 1M**.  At six the forward is 163 ms and the CPU half 408 ms
against the no-graph sweep's 290 / 547, so the graph removes work *and*
contention.  This is the provider row to beat; the binding is still shared, not
clean GPU serialization.

The 8-vCPU box is back to a measurement rather than a dominance proof: at
$0.43/h it needs **2.73 matches/s** to beat the 6-vCPU **$43.8 / 1M**.  That is
above the measured 2.221 but not obviously above the measured card ceiling, so
measure it before buying it.

### The 157 ms field and the `ever_server` inference port (2026-11-16)

Two different things, often conflated:

**The 157 ms is one field, not the packet.** In
`runs/crforge-c4/gpu-forward-scaling-128-long.json` — the recorded A6000,
128 lanes × 2 seats (256 rows) — the per-decision buckets are:

| bucket | ms |
|---|---:|
| tensorize | 81 |
| **inference** | **157** |
| decode | 97 |
| step | 75 |
| facts | 9 |
| unattributed (mean − median, §3.67) | ~228 |
| **total** | **647** |

`ms.inference_ms` is the model forward **plus its per-call overhead**, per
decision round. 157 → 21.5 ms is **7.3×**, the forward reduction needed for
**~$20 / 1M physical matches** at $0.35/h (the 2,428 convention; the exact
arithmetic gives 21.7 ms). Under the 1,214 convention the same $20 is **43.4 ms
(3.6×)** — quote the convention or the target moves 2×.

**The other ~490 ms is separate CPU-side work and is a second gate.** It is
tensorize 81 + decode 97 + engine steps 75 + facts 9 + the ~228 residual. A
21.5 ms forward alone does not raise throughput: the GPU ceiling becomes
~9.7 matches/s (1,214), but at the recorded 490 ms CPU half and 6 vCPU the CPU
ceiling is only **~2.5 matches/s**; on the live faster-CPU box (270 ms half) it
is **~4.3**. To let 6 vCPUs feed the 21.5 ms forward, the CPU half must fall to
**~108 ms** (`T = N_proc × F`), or `N_proc` must rise to ~14 on the live half
(~24 on the recorded one). The forward port is necessary, not sufficient.

**"Port ever_server" is four inference tricks, not the file or its wire
protocol.** Source: `.resource/RoyaleFish/royalefish/ever_server.py` (with
`ever_packed.py` and `net.py`). Target: `tools/crforge/self_play.py` →
`bridge_session.py` → `PolicySessionV4`, the in-process forward. Take:

1. fp16 with an fp32 action-equality check;
2. CUDA graphs captured at several batch sizes;
3. one GPU-side LSTM state table;
4. the layout/pack computed once and reused.

Do **not** port `net.py`'s TCP/pickle messages: crforge has no separate GPU
process, so the server's wire contract is discarded. The training-side
`ever_*` features (sparse trajectories, row-packed updates, loader process) are
a separate item; this port is only the forward path.

Gate the port on action equivalence and on both clocks: fp16/graph replay must
reproduce the fp32 sampled actions and log-probs on the same batch, and the
artifact must report `ms.inference_ms` at 128 lanes **with the CPU half beside
it**. A lower forward with a flat CPU half is not a throughput win.

**Status (2026-11-16, session 36).** The forward port is in:
`crforge_bridge/fast_forward.py` behind `self_play.py --fast-forward`. Autocast
fp16 (fp32 log-probs/values), one CUDA Graph per padded row count over the
**deterministic encode + core only**, one fp32 recurrent-state table, and a
padded staging layout built once per row count and refilled in place (pinned
host mirror + `non_blocking` on CUDA). Sampling is eager: torch 2.5 has no
graph-safe RNG, and one code path covers both versions. The gate is
`--verify-fast-forward` — sampled actions exactly equal to eager fp32,
log-prob/entropy/value inside 5e-3.

Measured on one 6-vCPU A6000 (128 lanes × 2 seats × 3 × 32, probe shape):
forward **182.1 → 132.9 ms** (−27 %), `g` 0.366 → 0.310, GPU ceiling
**1.33 → 1.563 matches/s**, predicted **$62.2/1M**. But **the graph does not
pay at this shape** (`--no-cuda-graphs` is 34 ms of wall faster; the pinned
staging lands in the CPU half while the decode stays outside), and six
concurrent producers measure **1.254 m/s / $77.5/1M**, only 3 % better than the
pre-port 1.215/$80.0. Rollout storage adds 188 ms/decision. **The CPU half is
the next gate:** tensorize 98 + decode 68 + step 113 + facts 12 = 291 ms must
reach ~108 ms for six vCPUs to feed the 133 ms forward. Do not rent more vCPUs
until it moves — the card is bound at 3.2 processes.

**Status (2026-11-17, session 37).** The half is now profiled rather than
estimated. `self_play.py --profile-collection` records a cProfile top list, and
every artifact carries a per-segment `cpu_half` block: the eight row-builder
sections, the two-seat interleave, and the decode bucket split into
`decode_only` / `record` / `plan`. `tools/crforge/cpu_half_micro.py` A/Bs the
Python shims in one process, so a faster shim is not confused with a quieter
host. Two results:

* **The box's 68 ms `decode` bucket was the previous-action record, not the
  decoder.** `record_action` wrote one field per lane per micro-action through
  tensor indexing — on CUDA that is one device sync per field per row, ~1,300 per
  decision at 128 lanes. It is now one NumPy pass (UID match, gather, write),
  with the candidate columns converted once per decision and shared with the
  decoder: **4.3 → 0.11 ms** on CPU at 128 lanes over 40 repeats
  (`cpu_half_micro.py`). The decoder itself is 0.6 ms.
* **The tensorize bucket is the row builders, and groups dominate.** At 128
  lanes × 2 seats on this laptop the sections are groups 17–30, relations 3–6,
  candidates 2.5–4.5, cards 1–2.4, towers 0.9–2.1, planes 1.1–1.7, empty 0.6,
  scalars 0.2–0.5, interleave 4–9 ms (the range is the board's live entity
  count, not noise); engine steps 31–37 and facts 8–12. The group row's own time
  is its feature assembly — one strided write per feature into a
  `(lanes, groups, channels)` array — not the gather: `take_along_axis` →
  advanced indexing measured **0.20 ms** one run and −0.07 the next, i.e. noise.
  The "one copy instead of two" interleave rewrite measured −0.08 ms (also
  noise: `torch.stack` already writes once into an interleaved destination) and
  was reverted.

**The sampled decode is now a flag, not an unknown.** `--full-cuda-graph`
captures encode + core + `select_action_from_context` in one graph and refuses to
run unless two replays of the same batch draw different log-probabilities — the
frozen-RNG signature. PyTorch registers the default generator at
`capture_begin`, so the gate is behavioural rather than a version claim; a build
without that cooperation fails loudly instead of sampling one frozen stream. The
wall is a box measurement, as is the re-run of the process sweep, and
`--verify-fast-forward` still gates the result on action equality.

**Status (2026-11-17, session 39).** The group section was building the record's
**reserved** width, not the board's live width: at 128 lanes a settled board has
~6 live entities while the record reserves 48 groups and 160 children, so
gathers, feature assembly and masked zeroing all ran 8–26× wider than needed.
`VectorGroupBuilder.build` now assembles `live_g`/`live_c` slots and pads to the
fixed capacity once. One-process A/B on the same batch: `build` **7.57 → 2.68
ms** at 128 lanes with 6 live entities (2.82×, records asserted identical); the
producer's section mean is **13.18 → 6.01 ms/seat** across runs. The digest gate
still passes **3,008/3,008** lane-decisions for both seats with **0
mismatches**, at **0.0546 ms/lane-decision** (12.95× the per-lane oracle; the
session-37 record was 0.0811, 7.46×). The gather-by-dtype rewrite is withdrawn
as unnecessary: after the width fix the gathers are small and `_entity_rows` is
the remaining term. See `FINDINGS.md` §3.72.

The 8-vCPU question is still arithmetic rather than a rental: at the full-graph
terms an 8-vCPU box at $0.43/h must beat the 6-vCPU **$43.8/M**, i.e. reach
**2.73 matches/s**, which is 1.23× the measured 2.221 and above the 1.842
predicted ceiling — while the sweep saturates at four producers. Measure it only
after a bounded pilot shows a native win-rate signal; a bigger box does not
change the policy.

**Status (2026-11-17, session 40).** The bounded pilot's native gate came back
negative. On 50 mirror-X-Bow games (seeds 101–150, sampled actions, both seats)
the pilot lost **19–30** with one draw and 0 illegal/rejected/truncated —
38.8 %, Wilson 95 % 26.4–52.8 %, p = 0.15 — to the seed it started from, so 16
on-policy updates at 1e-5 did not improve the policy. The session-39 4–0 smoke
was the deck matchup: `evaluate` keeps each policy on its own deck, so it was
pilot-on-X-Bow against seed-on-Hog. The 50-game baseline also showed
`A-hog26-v738` is not a bar in this rig (**49–1** for the seed), so the seed
mirror is the policy gate and the 8-vCPU measurement stays deferred: the next
spend is a training run with enough updates and seeds to move the policy.
See `FINDINGS.md` §3.74.

**Status (2026-11-17, session 41).** The last named CPU term is closed.
`_entity_rows` built every field over the reserved 200-row entity axis: nine
`np.where(valid, table[rv], 0)` gathers (NumPy re-converts the int32 row map to
`intp` on each call — six gathers alone were 0.42 ms), plus the masks and a
float64 velocity copy.  It now assembles at the live rows and scatters into one
zero row per field, which is exactly what the masks produced for the invalid
slots.  Same-process A/B at 128 lanes: **1.098 → 0.301 ms** (3.65×), group
`build` **2.684 → 1.976 ms** per seat
(`runs/crforge-c4/cpu-half-micro-s41.json`).  The digest gate is still
**3,008/3,008** lane-decisions, both seats, 0 mismatches on seeds 1 and 2, and
the bridge suite is 145 passed.  *(Session 42 corrected the fixture behind those
numbers — it had zero live entities; on a live board the same change is 1.297 →
0.762 ms.)*  See `FINDINGS.md` §3.75.

**Status (2026-11-17, session 42).** The group section's remaining dense passes
are gone, and the fixture that measured them was wrong.  `_targeted_mask` builds
its lane-tagged keys and `searchsorted` from the live rows (**0.356 → 0.083 ms**,
4.3×), and `_orders` / `_child_order` sort only the slots `build` reads
(**0.114 → 0.065**, **0.170 → 0.079 ms**); the padded tail repeats row 0 and is
masked off as the invalid tail was.  The three terms are **0.64 → 0.23 ms** per
seat.  `cpu_half_micro.py` had played at tick 61 while `FIRST_PLAYABLE_TICK` is
90, so every play was refused and its "settled board" was six towers: every
micro section number from sessions 37–41 is an empty-board number.  It now waits
past the cooldown (**11.5 live entities, max 17**); on that board group `build`
is **6.99 → 2.18 ms** (3.2×) and `_entity_rows` 1.297 → 0.762 ms (1.70×), so the
session-39/41 changes hold.  Digest **3,008/3,008** both seeds (11.2× / 11.8×),
bridge suite 145, producer probe 2/2 on-policy at |KL| 7.46e-5.  Next: the
fixed-capacity pad to 48/160 slots inside `build` — measure it before rewriting.
See `FINDINGS.md` §3.76.

**Status (2026-11-17, session 43).** The pad is measured and gone from the
producer.  Replicated field by field, copying the live 11/11 rows into the
record's 48/160 slots is **0.28–0.60 ms** of a **1.27–1.54 ms** build and
**7.5 MB** of fresh zeroed arrays per seat; the padded build is **1.20–1.43×**
the unpadded one (same process).  `VectorGroupBuilder.build` now returns the
live width on the producer path, and every consumer already sizes itself from
`groups.mask`/`child_mask`: the model's token count and group attention, record
validation, the relation builder, PPO's padded concatenation and `to_storage`.
`pad_group_section`/`pad_group_record` project back to the configured capacity
for the per-lane oracle and the digest gate (`lane_batch` takes the capacities).
The record width is also forward work: `encode_observation` at 128 CPU rows is
**1.63–1.77 s live against 2.38–2.49 s padded** (27–34 %).  Digest
**3,008/3,008** both seeds at **0.0476 / 0.0522 ms/lane-decision** (13.8× /
12.4×), end to end **0.0650 / 0.0673**; bridge suite 145; a 2-update producer
probe is 2/2 on-policy, 0 stale, 0 illegal/rejected, checkpoint
reload-identical.  See `FINDINGS.md` §3.77.

**Status (2026-11-17, session 44).**  The group build's last reserved-axis write
is gone.  `_entity_rows` scattered each live field into a zero-filled
`(lanes, max_entities)` array; it now returns lane-major
`(lanes, widest live count)` rows plus `counts`/`slots`, and `build` scatters the
dense `group_index`/`child_index` once at the end.  `build(phases=...)` attributes
the section: rows **0.217**, child features 0.156, group features 0.121, child
order/rank 0.112, zeroing 0.092, group gathers 0.081, `_orders` 0.082,
`_targeted_mask` 0.050, group rank 0.033, index scatter 0.036 (sum 0.991).
`_entity_rows` falls **0.43 → 0.18 ms** (2.4×; 5.5× the pre-session-41 dense
form at 0.995) and `build` **1.35 → 1.00 ms**.  `np.copyto(where=)` for the
masked zeroing measured slower (0.184 against 0.092) and was reverted.  Digest
**3,008/3,008** both seeds at **0.0345 / 0.0490 ms/lane-decision** (18.5× /
12.6×), end to end **0.0499 / 0.0638**; bridge suite 146; a 2-update producer
probe is 2/2 on-policy with the same KL trace as session 43.  See
`FINDINGS.md` §3.78.

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

# C3-B card + relation sections vs the same oracle (reads the checkpoint's
# own 21-column card runtime schema, not the default 16)
.venv/bin/python tools/crforge/batched_cards.py \
  --count 256 --steps 40 --timing-samples 8 --timing-repeats 3 --verify-lanes 32 \
  --output runs/crforge-c3/batched-cards.json

# C3-B candidate rows vs the same oracle; 60 WAIT warmup decisions clear the
# start cooldown so the comparison sees candidates instead of empty sets
.venv/bin/python tools/crforge/batched_candidates.py \
  --count 256 --steps 40 --timing-samples 8 --timing-repeats 3 --verify-lanes 32 \
  --output runs/crforge-c3/batched-candidates.json

# C3-B six-row tower section vs the same oracle; the artifact records the two
# envelope pins (empty status, static tower troop 159000000)
.venv/bin/python tools/crforge/batched_towers.py \
  --count 256 --steps 40 --timing-samples 8 --timing-repeats 3 --verify-lanes 32 \
  --output runs/crforge-c3/batched-towers.json

# C3-B explicit spatial planes vs the same oracle (per-frame caches cleared per
# lane, exactly as tensorize clears them per decision)
.venv/bin/python tools/crforge/batched_spatial_planes.py \
  --count 256 --steps 40 --timing-samples 8 --timing-repeats 3 --verify-lanes 32 \
  --output runs/crforge-c3/batched-spatial-planes.json

# C3-B match scalars vs the same oracle (the oracle's own group/candidate counts)
.venv/bin/python tools/crforge/batched_scalars.py \
  --count 256 --steps 40 --timing-samples 10 --timing-repeats 5 --verify-lanes 32 \
  --output runs/crforge-c3/batched-scalars.json

# C3-B empty effects/events records vs the same oracle (padding/sentinels)
.venv/bin/python tools/crforge/batched_empty_sections.py \
  --count 256 --steps 40 --timing-samples 10 --timing-repeats 5 --verify-lanes 32 \
  --output runs/crforge-c3/batched-empty-sections.json

# C3-B end-to-end, both actor seats: facts-only tensorize vs each seat's
# per-lane tensorizer (whole-record digest, sampled actions recorded on both
# paths, paired timing incl. facts once and both records)
.venv/bin/python tools/crforge/batched_tensorize.py \
  --count 256 --steps 40 --seats 0 1 --timing-samples 8 --timing-repeats 3 --verify-lanes 32 \
  --output runs/crforge-c3/batched-tensorize.json

# game-frame cross-check: one x86_64 native play and one crforge play at the same
# game point must spawn the same units; the facts-layer pending is game-framed
# (start the engine first: tools/native_analysis/crx_engine_host.py --seconds 600)
.venv/bin/python tools/crforge/frame_crosscheck.py \
  --native-port 26789 --output runs/crforge-c3/frame-crosscheck.json

# C4 action path: the batched engine action decode vs the stock per-lane decoder
# for both seats, then step the decoded indices into the engine
.venv/bin/python tools/crforge/vector_decode.py \
  --count 256 --steps 40 --seats 0 1 --verify-lanes 32 \
  --output runs/crforge-c4/vector-decode.json

# C4 producer: batched self-play over one VectorBattle — both seats, validated
# every decision; the gate shape is 8 lanes x 10 segments x 64 decisions, and it
# stores each decision's PPO packet (observation, action, log-prob, value, reward,
# episode boundary) and finalizes one StoredRolloutV4 per segment with a boundary
# bootstrap.  --no-rollout keeps the collection/throughput-probe shape.
.venv/bin/python tools/crforge/self_play.py \
  --count 8 --segments 10 --decisions-per-segment 64 \
  --output runs/crforge-c4/self-play-rollout.json

# same loop with a short episode cut, which exercises terminal/autoreset handling
.venv/bin/python tools/crforge/self_play.py \
  --count 4 --segments 2 --decisions-per-segment 60 --max-ticks 600 \
  --output runs/crforge-c4/self-play-autoreset.json

# C4 update gate: one PPO update over the stored gate shape (8 lanes x 64
# decisions, both seats, native reward/GAE), then the multi-episode path where a
# lane autoresets mid-segment (16 mid-rollout episode_start rows, 2 updates).
.venv/bin/python tools/crforge/self_play.py \
  --count 8 --segments 1 --decisions-per-segment 64 \
  --ppo-update --ppo-sequence-chunk 32 \
  --output runs/crforge-c4/self-play-update.json
.venv/bin/python tools/crforge/self_play.py \
  --count 8 --segments 2 --decisions-per-segment 60 --max-ticks 600 \
  --ppo-update --ppo-sequence-chunk 32 \
  --output runs/crforge-c4/self-play-update-rollout.json

# C4 timing probe: the sampled policy emits one micro-action, so this appends a
# second *different* legal play at the sampled action's own delay bin; the
# collision shifts it one tick, or drops it when the first play is already on the
# window's last tick.  Refused together with rollout storage, because the stored
# action would no longer be the action the engine ran.
.venv/bin/python tools/crforge/self_play.py \
  --count 8 --segments 1 --decisions-per-segment 40 \
  --force-second-micro alternate --no-rollout \
  --output runs/crforge-c4/self-play-two-micro.json

# CPU-half profile: every artifact carries ``cpu_half`` (the eight row-builder
# sections, the interleave, and decode_only/record/plan); --profile-collection
# adds a cProfile top list by cumulative and own time, in ms per decision.
OMP_NUM_THREADS=4 .venv/bin/python tools/crforge/self_play.py \
  --count 128 --segments 1 --decisions-per-segment 8 --no-rollout \
  --profile-collection --output runs/crforge-c4/cpu-half-profile-128.json

# CPU-half A/B: the interleave, the group gather, the entity-row build and
# record_action against the shims they replaced, in one process on one batch
# (a before/after across processes measures the host's load, not the change).
OMP_NUM_THREADS=4 .venv/bin/python tools/crforge/cpu_half_micro.py \
  --count 128 --repeats 40 --output runs/crforge-c4/cpu-half-micro-128.json

# full-call CUDA Graph (GPU box only): encode + core + sampled decode in one
# capture.  Refused unless two replays draw different log-probabilities (a frozen
# RNG), and --verify-fast-forward still requires fp32 action equality.
.venv/bin/python tools/crforge/self_play.py \
  --count 128 --segments 3 --decisions-per-segment 32 --no-rollout \
  --fast-forward --full-cuda-graph --verify-fast-forward \
  --output runs/crforge-c4/full-graph-128.json

# C4 cadence gate: reset vs carry vs wave at one shape, one checkpoint and one
# seed list.  Each update is classified on-policy or stale (a previous update in
# the same wave already replaced the sampling weights), so a wave's off-policy
# tax is measured against its own on-policy baseline.  Runs sequentially; --reuse
# re-aggregates the per-run artifacts under --run-dir without re-collecting.
.venv/bin/python tools/crforge/cadence_compare.py \
  --count 4 --segments 6 --decisions-per-segment 24 --seeds 1 2 \
  --output runs/crforge-c4/cadence-compare.json

# C4 pipeline gate: serial reset vs the overlapped loop at one shape and seed
# list.  The update runs on a second model in a worker thread while the next
# segment collects; the gate also requires the collection rate to survive
# (>= 90 % of serial), because an update paid for in CPU contention is not
# hidden.  --reuse re-aggregates the per-run artifacts without re-collecting.
.venv/bin/python tools/crforge/overlap_compare.py \
  --count 4 --segments 6 --decisions-per-segment 24 --seeds 1 2 \
  --output runs/crforge-c4/overlap-compare.json

# C4 learning horizon: reset vs carry for >=50 updates at one shape and >=2
# seeds.  Summarizes the reward/return curve, the accessible ACT rate and a
# paired per-seed block (the seed is the unit of a ranking).  Native parity is
# deliberately not run here: crforge is never a promotion source.  The artifact
# also reports the reward share of the return, so a return curve that is mostly
# the boundary bootstrap is not read as a score.
OMP_NUM_THREADS=4 .venv/bin/python tools/crforge/learning_horizon.py \
  --count 4 --updates 50 --decisions-per-segment 24 --seeds 1 2 3 \
  --output runs/crforge-c4/learning-horizon.json

# C4 reward-share probe: the same summary at 24/60/120 decisions per segment,
# same update count and seeds, to decide whether a longer window makes observed
# reward a large enough part of the return to rank it.
OMP_NUM_THREADS=4 .venv/bin/python tools/crforge/reward_share.py \
  --decisions 24 60 120 --updates 16 --seeds 1 2 \
  --output runs/crforge-c4/reward-share.json

# C4 timing accounting: reconcile a producer artifact's buckets with its wall.
# Pre-2026-11-19 artifacts have medians only; the residual is then the implied
# cold-first-decision cost.  Two shapes at one lane count solve the fixed cost
# and the steady-state per-decision cost.
.venv/bin/python tools/crforge/timing_accounting.py \
  runs/crforge-c4/gpu-forward-scaling-{8,16,32,64,128,128-long}.json \
  --output runs/crforge-c4/timing-accounting-gpu.json

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
