# Session handoff — 2026-11-15 — Null's Royale 16.635.1

> **Read [`ROADMAP.md`](ROADMAP.md) first.** It is the single plan.
> [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) is the bulk-training plan of record.
> This file is the next-session starting point.

## Decisions — what changed

### 2026-11-19 — the game-frame fix is closed; both actor seats pass the digest gate

The native model consumes **game** coordinates; crforge fed the **seat frame**,
so every crforge board was horizontally mirrored relative to the native path.
The conversion now happens once per layer: the facts layer writes game
coordinates, the bridge converts entity rows at one point, each seat's pending
point and placement mask are converted once, and every row builder is
actor-seat aware. The old evidence and the new proof:

| path | play | observed unit |
| --- | --- | --- |
| native x86_64 16.635.1 | Skeletons at game `(6500, 10500)` | **(6499, 11079)**, (5997, 10212), (6998, 10212) |
| crforge before the fix | same play | `18000 - x` (the seat frame) |
| crforge after the fix | same play | **the same three game points** |

The engine's internal action/mask space stays seat-framed (the play was
addressed with seat cell `(11, 10)`), while the facts-layer pending point is
game-framed (`pending_x = 6499`). `tools/crforge/frame_crosscheck.py` runs that
comparison against a live engine and records it in
`runs/crforge-c3/frame-crosscheck.json` (**status pass**); the result is repeated
in `FINDINGS.md` §3.55.

`batched_tensorize.py --seats 0 1` gates both owners of every lane against their
own stock tensorizer: **3,008/3,008 lane-decisions digest-identical** (1,504 per
seat), **0.0811 ms/lane-decision** stacked vs 0.605 per lane (**7.46×**),
**0.1029 ms/lane-decision end to end** (~9,719 decisions/s); seed 2 reproduces
it at **3,008/3,008**, 0.0787, 8.50×, 0.0992. The seat-1 gate found and fixed
two defects: relation tower tokens now resolve through the tower model-row map,
and group mean velocity reproduces the stock accumulator's signed zero.
**C4 is unblocked.** Evidence: `runs/crforge-c3/batched-tensorize.json`,
`-seed2.json`, `FINDINGS.md` §3.55.

### 2026-11-19 — C3-B closed: the batched packet is 7–8.5× the stock tensorizer

The facts-only path produces the whole `UniversalSemanticBatchV4` for every
lane: `crforge_bridge/vector_tensorize.py` (stacked record + batched previous
action), gated by `tools/crforge/batched_tensorize.py` on 256 learner-deck lanes
× 40 decisions, now for **both actor seats**. **3,008/3,008 lane-decisions are
`batch_digest`-identical** to the per-lane tensorizer, field by field, with
sampled actions recorded on both paths. Combined timing per lane-decision:
**facts 0.0160 (0.0320 ms/lane, shared by both actors) + tensorize 0.0811 +
record 0.0058 = 0.1029 ms** — ~9,719 decisions/s per process, under C1's
0.5 ms/lane gate and a **7.46×** speedup on the tensorizer half (seed 2:
8.50×, 0.0992 ms). The original
single-seat C3-B record was **1,504/1,504** at 0.118 ms (7.75×).
Evidence: `runs/crforge-c3/batched-tensorize.json`, `-seed2.json`,
`FINDINGS.md` §3.54–§3.55.

### 2026-11-15 — crforge is the bulk trainer; the native engine is the gate

The native path is not the throughput path, and the reason is the **Python
per-packet data path**, not the engine: one lane is ~8–10 ms
(`step` 8.7 = engine round 3.1 + observe 4.0 + masks 1.6, plus tensorize), which
is **0.082 matches/s per process** and **4 % GPU utilisation** on the A6000. The
engine, the loop and the model are done; the data path is the lever.

crforge replaces the engine round with a share of one 256-lane JVM call and
returns observations zero-copy as NumPy. Measured today on this laptop
(`crforge_env.bench`, ladder, 16.635.8 tables, load ~11 — a floor):

| threads (256 lanes) | decisions/s | matches/s |
|---:|---:|---:|
| 1 | 3,154 | 2.6 |
| 4 | 7,304 | 6.0 |
| 8 | 15,791 | 13.0 |

That is **4–21× the native raw channel per process** (0.61 matches/s) and
**32–159× the native training floor** (0.082) before the observation adapter is
counted. The recorded M4 run is 18.5 at 1 thread and 52.5 at 4 (30–86× raw). So:
**bulk-train on crforge, promote on native.**

**But the simulator is not the whole win.** A straight per-lane port of the
existing observation builder is ~1.4× (≈7 ms/lane). The 10–30× exists only if the
`ObservationV1`/mask/tensorizer build is vectorized across the 256-lane batch:
**≤0.5 ms/lane is ≤128 ms per batch** (≈2,000 decisions/s); at 1 ms/lane it is
1,000 decisions/s. Today's bridge encoder is ~13 ms **per lane** → ~77
decisions/s — slower than the native floor.

So the plan of record is gated on one measurement (C1), and it does not delete or
idle the engine:

1. **C0 (1–2 d, $0):** close the last MiniPekka `26000018` reject; 20-game run,
   `illegal: 0`, `truncated: 0`.
2. **C1 (1 d, $0):** 256-lane encoder spike, per-lane vs vectorized. Gate:
   **≥1,000 decisions/s per process** (≤1 ms/lane at 256 lanes); target
   ≤0.5 ms/lane for the full 10–30×. If it misses twice, fix the encoder; do not
   add lanes or GPUs.
3. **C2–C4 (3–4 w, ~$2):** resident adapter → vectorized observation → batch/PPO;
   gate ≥0.8 matches/s per process and a 10-segment run with 0 rejected.
4. **C5 (continuous):** 50–150 native matches per N updates and after every
   adapter change; a persistent win-rate gap freezes the checkpoint.
5. **C6–C7:** ≥150 native paired games, then a 20-game live canary on Null's.

Two hard rules: **never promote on crforge**, and **parity is continuous, not a
final exam**. The extracted engine stays warm as the gate and the deploy rig; the
A51 stays as the ARM64 oracle and deploy target. **Official servers are out of
scope.**

Full gates, stop conditions and fidelity pins:
[`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §0–§4.

### 2026-11-15 — learner deck: X-Bow 3.0; opponent pool varies

The learner plays one deck; variety comes from the **opponent pool**, not from
training a generalist. `B-general-v275` is the init and a frozen opponent.

Locked deck (highest-use X-Bow in `runs/deck-pool.json`, 2,983 uses):

```
x-bow · tesla-ev1 · archers-ev1 · knight-hero · skeletons · electro-spirit · fireball · the-log
tower: tower-princess (159000000)
```

**Support check: pass.** All eight are in the verified 16.635 release
(`card_support.json`, 123 cards) and in `crforge_catalog.json`; the forms exist
as `Archer_EV1`, `Tesla_EV1`, `Knight_hero`. The open item is the **tower troop**
(`SideSetup` at C2).

Opponent pool: frozen `B-general-v275` (deck-pool sampling), `A-hog26-v738`
(71.9 % ±2.6 / 1,147), mirror X-Bow, 2–3 historical learner checkpoints
(PFSP-weighted), and the hard decks (Mortar, Evo Witch, Minion Giant,
Golem/beatdown, Log Bait, Graveyard). Fixed levels, both seats, paired seeds.

**This does not change C0–C3** (deck-agnostic). Hog 2.6 is not the learner; it is
the published bar and the fallback canary.

**Data (see `ROADMAP.md` §6.1):** the 252k corpus already contains ~**5,700 X-Bow
games** (2.25 % of deck uses). Expert replays are a tier-2 input, in this order:
drills/reward mining (hours), then a BC warm-start (2–5k games) **only if** a
100–200-game native eval of `B-general-v275` on the locked X-Bow deck is **<40 %**;
always meta/opponent modelling. Mass scraping is not worth it.

### 2026-11-15 — RoyaleFish upstream (`c42108d`; README + media only since `da9c8cf`)

- **The November 2026 Hog 2.6 replay set is cancelled at the source.** RoyaleAPI
  closed replay access for everyone on **2026-11-16** (Discord login plus a
  hand-reviewed request form, no stated turnaround). RoyaleFish's collector stopped
  at a few hundred games and the Hugging Face upload is **postponed with no date**.
  Treat the set as unavailable: no tier-2 Hog IL anchor and no meta snapshot from
  it, and an X-Bow collector is blocked by the same wall. The local 252k corpus is
  unaffected.
- **A v1881 learned defense from real human attacks.** 52,632 human attacks replayed
  card-for-card as "ghosts", four attempts each, rewarded for beating its own
  earlier attempts; 2,666 ghosts held out. On those unseen attacks: defense score
  0.53 → **0.75**, princess towers lost per game 0.47 → **0.25**, fully clean
  defenses 11 % → **19 %**; ~1,900 updates and 99 % of the 17.5M weights changed.
  Open: it under-attacks and rarely commits the Hog, and its Fireball leads moving
  troops. **The checkpoint is not uploaded yet.** A template for the Hog opponent,
  not a change to the X-Bow learner.
- **No code, data, model or table change** in either commit, so the `data_16635/`
  catalogs and the `B-general-v275` / `A-hog26-v738` pins are unchanged. `Blade` is
  no longer mentioned in the README.
- **15.535 → 16.635 model converter is coming.** It remaps **card rows for the
  same architecture**, so it helps older V4-family checkpoints — not
  FirstLight-Zero's **X-Bow 3.0** (1.17M, mirror-only, 75-tensor custom
  architecture), which stays a reference/opponent. Verify forms + a native parity
  sample before trusting any conversion.
- **The league is no longer frozen.** RoyaleFish now co-trains a learning
  generalist B + older versions + 10 % mirror + focus decks + drills. A frozen
  opponent can be memorized; adopt this shape for X-Bow.
- **New models (not X-Bow):** A v1290 (Hog) uploading, **Frost** (Knight-EV1 /
  Hero Ice Wizard / Tesla-EV1 / Bats / Minion Giant / Skeletons / Barbarian
  Barrel / Fireball) in 1–2 days, anti-2.6 at ~81 %.
- **Throughput:** ~2,500–3,000 games/h on a 5060 Ti + V100, ~55k decisions from
  ~64 games per update; **NVIDIA MPS** nearly doubled games/h. That is the
  `ever_*` recipe working — include MPS in C4.

### 2026-11-14 — why the target changed

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

> **2026-11-14 (late): the x86_64 16.635.1 engine now runs on this host**
> through `.resource/Clashback` + `crx`, with a native probe port and scripted
> parity seeds 1–5 matching the A51 corpus. The A51 remains the ARM64
> deployment oracle. See §3.35 and "Next session" step 1.

> **2026-11-14 (session 2): first GPU PPO update ran; the A51 is the wrong
> training engine.** 1×A6000 (ThunderCompute) completed a full update with
> **0 rejected actions** and a saved checkpoint, but the learner only reached
> **19 lane-decisions/s (0.04 matches/s)**. Measured at the PPO 5-tick decision
> cadence, four A51 engines cap at **0.26 matches/s**, while **one x86_64
> process does 0.61 matches/s** and is CPU-bound. Two config bugs also blocked
> the gate: the flat `gate_temperature 0.2` drove `ACT` to 0 over 5,120 ticks,
> and the trainer hard-requires a 7,695/20 deck pool the local corpus cannot
> produce. The GPU box was deleted after the smoke (~$0.23). See
> "GPU smoke 2026-11-14 — results and what they mean".

> **2026-11-14 (session 4): the x86_64 PPO worker path works, and 16.635
> checkpoints now train through it.** The `Error -5 … truncated stream` decode
> failure was a **host-side zlib resolution bug** (`libz` preloaded
> `RTLD_LOCAL`, so crx's missing-symbol stub answered `compress2` with `Z_OK`
> and a zero-byte output). The engine spec now carries the checkpoint's
> `competitive_release` + `model_config`, so engine-node tensorizers build the
> 16.635 catalogs and the 21-column schema instead of the pinned 15.535/16-column
> default. `B-general-v275` collects through the CPU worker on one x86 engine
> with 0 UNKNOWN effects (29 lane-decisions/s, model-time bound). See
> `FINDINGS.md` §3.37. The next lever is still the learner loop's one-packet-per-
> forward batching, not the transport.

> **2026-11-14 (session 5): batching measured; host engines are multi-process.**
> The 20–40 ms window advice was wrong for the A51: a window shorter than one
> engine round-trip (~100–150 ms) re-batches the same two-engine cohort every
> time. At `CR_PPO_INFERENCE_BATCH_WAIT_MS=250`, four A51 engines coalesce
> **3.88 packets/forward and 33 forwards instead of 64**; CPU wall time stays
> flat (the 17.5M model is the local bottleneck), which is exactly the
> per-forward cost the 4 %-utilised GPU was idling on. `cr_probe_attach` now
> initialises engine identity, so `crx_engine_host.py --engine-index N` gives
> each x86 host process its own port (`26789 + N`) and control CPU — verified
> with two concurrent engines. The same session confirmed the §3.37 caveat:
> B-general at gate temperature **1.0** reproducibly trips a native action
> failure (env 5), while the same seeded run at **0.2** is clean. See
> `FINDINGS.md` §3.38. *(Superseded by session 6 below: the delay-aware port is
> in and gate 1.0 now collects cleanly in six segments.)*

> **2026-11-14 (session 6): delay-aware candidate legality is ported, and
> full-rate ACT collection passes at gate temperature 1.0.** `B-general-v275`
> declares `delay_aware_legality: true`, but the decoder and `BattleEnvV1` were
> still applying the 15.535 decision-tick rule. Both now check elixir at the
> touch delay bin (`max(e0, min(10, e0 + regen*tick)) - spent`) with the match
> multiplier, the environment exports `touch_window_elixir` /
> `affordable_from_bin`, and compound validation runs in touch order. Gates:
> **1×A51 8 lanes × 5 seeds: 5/5 segments**, **4×A51 8 lanes (the §3.38 failing
> shape): 28.5 lane-decisions/s, 1/1**, x86 1 engine: 23.9/s, 0 rejected and
> **3,979 native command parses with 0 failures**. One post-port failure
> (`env 6`) did not reproduce in six later segments and is not claimed fixed;
> `CR_PPO_FAILURE_DUMP` now captures the failing actions if it returns. See
> `FINDINGS.md` §3.39.

> **2026-11-14 (session 7): the batching window is a dead end; the closed loop is
> the critical path.** The combined config the GPU job would use — gate 1.0 +
> delay-aware + 250 ms — halves forwards (321 → 160) and reaches 3.98–4.00
> packets/forward, but is **net slower** than the 2 ms default (271 s vs
> 288–331 s): the added wait exceeds the saved per-forward cost because the CPU
> model is row-bound, not forward-bound. **Keep 2 ms.** On a GPU the forward is
> ~2 ms, so a fixed 250 ms window can only lose; the paid run's 416 ms/packet was
> ~390 ms of loop bookkeeping, not forward count. The 16.635 evaluator was also
> fixed (release scope for the ruleset manifest and `BattleEnvV1`, release-aware
> `runtime_scope` counts); seeds 7 and 21 ran `B-general-v275` vs
> `A-hog26-v738` to native terminals with 0 rejected. A **10-segment stress gate**
> at the chosen config is 10/10 clean (4,097 native parses, 0 failures, no
> `env 6`), so the GPU gate is unblocked. See `FINDINGS.md` §3.40.

> **2026-11-15 (session 8): the learner loop is pipelined, and the ladder's
> ceilings were measured against the wrong workload.** A single `ppo-inference`
> thread with one batch in flight now stages receives during the forward, and
> boundary bootstraps share that thread (10/10 segments, 20,480 owner frames,
> clean). With a cheap forward the non-model wall falls 14 %; with the real
> 17.5M checkpoint the loop is neutral, because the forward is 98 % of wall and
> both loops idle only 0.19 s/segment once warm. The important result is
> diagnostic: the per-packet floor is **32 ms native step + observation build,
> 14 ms tensorize, 6 ms channel**, so the raw channel "ceilings" in the old
> ladder (0.61/s per engine, 2.44/s for four) overstate the training rate ~7x —
> four engines measured 0.736 aggregate because one Python client decodes every
> lane. The gates (1x >=0.45/s, 8x >=2.5/s) are not reachable on this worker
> path; the next lever is the worker's per-packet cost, not the loop. See
> `FINDINGS.md` §3.41.

> **2026-11-15 (session 11): the training observation is lean, and contract
> machinery is gone from the policy path.** `policy_features_only` now builds
> every contract through a non-validating `construct_contract` inside a
> `lean_contracts()` scope: no validation, no `FrozenMapping` freeze walk, no
> provenance trees, no projectile-velocity rewrite. The hand runtime contract
> and native form aliases are cached per tick, and every observation cache is
> keyed by `(native identity, lean)` so validated callers never see a lean
> object. Paired A/B (8 alternating runs): **CPU 11.75 → 7.81 ms/packet
> (median −28…−34 %), 78 → 113 lane-packets/s**; `_entities` −60 %,
> `_extract_towers` −44 %, `_players` −55 %. At 8 lanes: 10.6/12.5 →
> 8.2/9.4 ms. The validated path is unchanged (parity seeds and
> evaluator default to `lean=False`), and a new tensorizer test asserts the lean
> observation produces bit-identical tensors. Next: fuse the rich frame straight
> into tensor rows — the remaining cost is the native round trip (~3 ms) and
> `_rich_object`/`_phase_object` binding, not contracts. See `FINDINGS.md`
> §3.43.

---

## Session 24 results (2026-11-19) — frame fix closed; both seats gated; C4 unblocked

**Read first:** this section, the top entries, and `FINDINGS.md` §3.55.

### What changed

- **`tools/crforge/batched_tensorize.py`** now takes `--seats 0 1`: one
  `VectorTensorizer` and one stock tensorizer per actor, per-seat
  `reference_scalar_observation(..., seat=...)` and
  `reference_action_mask(..., seat=..., model_frame=False)`, and combined timing
  (facts once + both tensorizes + both record calls) per lane-decision. The
  artifact is schema `v2` with `by_seat` equivalence and timing blocks.
- **`crforge_bridge/vector_rows.py`** fixes two seat-1 defects the gate found:
  - `VectorRelationBuilder.build(...)` now takes the `VectorTowerSection` and
    resolves tower sources/targets through `tower_index`. The stock relation
    token for a tower is its **model row**, not its native id; under a flip
    they differ, so every seat-1 tower edge was wrong. The section comparators
    also gained `seat`, and the relation/tower tests now gate actor 1.
  - Group mean velocity now adds `0.0` before the scale divide, reproducing the
    stock accumulator's signed-zero rule (`0.0 + -0.0 == +0.0`). `torch.equal`
    accepts the two; `batch_digest` hashes bytes, which is how the gate caught
    it.
- **`tools/crforge/frame_crosscheck.py`** — the native cross-check. It plays
  the same card at the same game point in the x86_64 engine and in a one-lane
  `VectorBattle`, advances one tick at a time, and compares the first spawned
  frame. `runs/crforge-c3/frame-crosscheck.json` is **status pass**:
  native and crforge both at `(5997, 10212)`, `(6499, 11079)`, `(6998, 10212)`;
  facts-layer pending `(6499, 10500)` (game frame) for a play addressed with
  seat cell `(11, 10)`.
- `test_vector_tensorize.py` parameterizes the played-sequence digest over both
  seats; `test_vector_rows.py` adds the actor-1 relation/tower gate, the tower
  token-map pin and the signed-zero velocity test. Bridge suite **55 tests**,
  ruff clean.

### Measured (256 lanes × 40 decisions, `--seats 0 1`)

`runs/crforge-c3/batched-tensorize.json`:

| measurement | combined | seat 0 | seat 1 |
| --- | ---: | ---: | ---: |
| lane-decisions digest-identical | **3,008/3,008** | 1,504/1,504 | 1,504/1,504 |
| stacked tensorize, ms/lane-decision | **0.0811** | 0.0823 | 0.1180 |
| per-lane oracle, ms/lane-decision | 0.605 | 0.600 | 0.684 |
| speedup | **7.46×** | 7.29× | 5.79× |
| batched `record_action` | 0.0058 | 0.0057 | 0.0070 |
| **end to end** | **0.1029 ms/lane-decision** | | |

Facts are 0.0320 ms/lane (shared by both actors); ~9,719 decisions/s per
process. Seed 2 reproduces it: **3,008/3,008**, 0.0787 ms/lane-decision,
**8.50×**, **0.0992 ms/lane end to end**, both seats at ~0.079. Seat 1's slower
first run tracked the oracle's slower timing on the same loaded host; quote
paired runs, not a fixed seat ratio.

### Next session (session 25) — C4

1. **Wire the stacked batch into the C4 producer.** `producer.py` calls
   `tensorize`/`record_action` per lane per owner today; replace that pair with
   one call per decision using two `VectorTensorizer`s (one per actor) or extend
   `VectorTensorizer` to both seats. The batched `ActionSequenceV4` is the row
   source.
2. **C4 gate, unchanged:** 8 lanes/process, 2–4 processes, 10 segments,
   `rejected 0`, `eligible_act_rate > 0`, native eval not degrading; log
   env-steps/s, tensorize ms, inference ms. Include NVIDIA MPS (the RoyaleFish
   `ever_*` recipe).
3. **Run the native cross-check** (`tools/crforge/frame_crosscheck.py`, engine
   first) after every adapter change and alongside the 256-lane gate.
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 23 (committed as 2234c79) — seat 1 and the game-frame fix

**Read first:** this section, the top entries, and `FINDINGS.md` §3.55.

The session started on seat 1 and immediately found a frame divergence that had
to land first. The changes below were a coherent checkpoint; session 24 finished
the gate and the measurement (see the session-24 section above).

### The finding

The native model input is **game coordinates**; the crforge bridge and the
vector facts layer were feeding the **seat frame**. Measured:

| path | play | observed unit |
| --- | --- | --- |
| native x86_64 (`CR_NATIVE_BUILD_ABI=x86_64`) | Skeletons on game cell `(6,10)` | **(6500, 10500)** |
| crforge resident | same play via `BattleEnvV1` | **(11002–12003, ~10212–11079)** |

The vector pending header for the same play is `pending_x = 11501` for game
`6500`, and the engine's action/mask space is seat-framed
(`Schema.toSeatX(0, x) = 18000 - x`). `BattleEnvV1.action_mask` builds model masks
in game coordinates, so the crforge model was seeing a mirrored board with an
unmirrored mask. C0/C2's bridge-vs-bridge parity is unaffected as a legality
record; its model-frame claim was never valid.

### What changed (in the working tree)

- **`crforge_bridge/observe.py`** — `entity_rows(..., seat=0)` converts each
  seat's rows to game coordinates (`x`, `targetX`; seat 1 converts `y`,
  `targetY`). There is one conversion point for the bridge.
- **`crforge_bridge/engine.py`** — `_bind_holder_identity` uses the now-game
  rows directly (the old `toSeatX/toSeatY` un-mirror was the workaround for the
  wrong frame).
- **`crforge_bridge/vector_observation.py`** — the facts layer writes **game
  coordinates**: entity `x` (and target x) are `WORLD_WIDTH - seat0_x`; derived
  velocity follows; each seat's pending point is converted once (seat 0 x, seat
  1 y); `placement_rows(seat)` returns the seat's mask flipped to the game frame
  (still a view).
- **`crforge_bridge/vector_rows.py`** — all row builders are actor-seat aware
  (`_actor_seat(tensorizer)`, no more seat-0 refusals). Card pending cells apply
  the owner-signed subcell offset and the actor perspective; candidate placement
  applies `flip_x` / `flip_y`; scalars read the actor seat's elixir, crowns and
  overflow. `reference_*` take `seat`; `reference_action_mask(...,
  model_frame=False)` leaves the native game grid for the stock `tensorize`
  (which applies the perspective itself), while the section oracles keep the
  default model-frame grid. `_PlayerView` / `_ObservationView` / `_MaskView` are
  now dataclasses so the stock perspective transform can copy them for a
  seat-1 digest oracle.
- **`tools/crforge/encoder_spike.py`** — the per-lane control converts pending
  and entity/target x too, so the C1 spike still compares like with like.
- **tests** — the seat-0-only refusal tests became seat-mismatch/invalid-seat
  tests; the pending-cell fixture is game-frame; the Tesla play helper converts
  its chosen game cell back to the engine's seat-frame action index.

### Status at session 23 end

- `crforge_bridge/tests`: **52 passed** at that point.
- Seat-0 digest spot-check after the change (64 lanes × 12 decisions, verify 8):
  **152/152 digest-identical**, 0.105 ms/lane tensorize, **0.148 ms/lane** end to
  end, 6.65× (`/tmp/opencode/seat0-after-frame.json`).
- **Left open at that point:** the 256 × 40 artifact predated the frame change,
  `batched_tensorize.py` had no `--seats`, and `FINDINGS.md` §3.55 was not
  written. **Session 24 closed all three** (see the session-24 section).

### Session 24 carried these (now done)

1. `--seats 0 1` with one tensorizer per actor and combined end-to-end timing.
2. Seat 1 gated against the stock actor-1 tensorizer; it found the relation
   tower-token and signed-zero velocity defects.
3. 256 × 40 re-run for both seats; native cross-check
   (`tools/crforge/frame_crosscheck.py`).
4. **Next:** C4, with the producer's per-lane pair replaced by the stacked
   `VectorTensorizer` call. Do not scale lanes/GPUs before C4's own gate.
5. **Parity cadence:** unchanged — 50–150 native matches every N updates and
   after every adapter change; never promote on crforge.

## Session 22 results (2026-11-19) — C3-B closed: the facts-only tensorize clears the gate

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §2 (C3),
`FINDINGS.md` §3.54.

### What changed

The last C3-B piece was the wiring: a `tensorize` that assembles the per-lane row
sections into the record the model consumes, with no contracts, no observation
projection and no per-lane Python. `crforge_bridge/vector_tensorize.py`:

- **`VectorTensorizer`** compiles `VectorEntityTables` and every section builder
  once (cards, groups, towers, effects/events, candidates, relations, spatial
  planes, scalars) and `tensorize(batch)` calls them in the tensorizer's own
  order, returning one `UniversalSemanticBatchV4` with **lanes as the batch
  dimension**. Sections already carry the lane axis, so the join is a reference,
  not a stack; only relations keep their common padded width and mask.
- **The previous-action record is batched.** `record_action(sequence, batch)`
  takes lanes as rows, resolves each sampled UID against that lane's candidate
  rows and fills one `PreviousActionV4` for every lane, replacing it wholesale
  (the same snapshot semantics as the stock method).
- **`lane_batch(batch, lane)`** slices the stacked record back to the exact
  one-row tensors the per-lane path emits (relations trimmed to the live count,
  every fixed section at V4 capacity). That slice is the digest projection, not
  a training path.
- **`batch_digest`** now lives here (one definition for the gate and the C3
  profiler) and `compare_semantic_batches` names the first differing field when a
  digest differs.
- `reference_observation` / `reference_scalar_observation` gained
  `episode_id` / `owner` / `tier` and pass `tower_troop_id` through, so the stock
  `tensorize` entry point can be driven from the same facts; the section oracles
  are unchanged.
- `VectorGroupSection.lane` / `VectorCardSection.lane` complete the lane slice
  set (towers, planes, scalars, effects/events, candidates and relations already
  had one).

Scope is unchanged: seat 0 and the bridge's no-event envelope. This is the
packet assembly, not a widened envelope.

### Measured (256 lanes, learner deck, 40 decisions, verify 32 after step 0)

`runs/crforge-c3/batched-tensorize.json`:

| path | total | ms/lane |
| --- | ---: | ---: |
| stacked facts-only `tensorize` | **20.65 ms** | **0.0807** |
| per-lane stock `tensorize` (prebuilt references) | 159.95 ms | 0.6248 |

**Equivalence: pass, 1,504/1,504 lane-decisions** (256 at step 0 + 39 × 32
after), compared field by field including dtype and shape, and
`batch_digest`-identical. A sampled deploy action was recorded on both paths
every step, so `previous_action` was exercised, and the stacked record ran the
stock `UniversalSemanticBatchV4.validate` at step 0. Busiest lane: 14 active
groups, 42 relation edges, 4 candidates.

**End to end: 0.118 ms/lane** — facts layer 0.0315 + tensorize 0.0807 + batched
`record_action` 0.0060 — i.e. **~8,460 decisions/s per process** against the
0.5 ms/lane gate, a **7.75×** speedup on the tensorizer half. A second seed
(`--seed 2`) reproduced it: 1,504/1,504, 0.0803 ms/lane, 8.0×, 0.117 ms/lane
end to end. This is the measurement the C1/C3 record was waiting for: the
≤0.5 ms/lane claim is now supported by a digest gate, not by section
arithmetic.

`crforge_bridge/tests/test_vector_tensorize.py` adds 4 tests (10-decision played
digest equivalence with recorded actions, relation-only trimming at fixed V4
capacity, per-lane previous-action resolution, and a broken-lane comparison
guard). The bridge suite is **52 tests**, all passing; ruff clean on the new
files.

### Next session (session 23) — C4's batched self-play loop

> **Closed in session 24:** the frame fix landed (commit `2234c79`) and both
> seats now pass the whole-record digest gate (**3,008/3,008**), with the
> x86_64 native cross-check passing. See the session-24 section above and
> `FINDINGS.md` §3.55. The remaining work is C4.

1. **Seat 1 is the open scope item.** The facts layer and every builder are
   seat 0 only, but self-play tensorizes both owners of each lane. Add a seat
   parameter (or a second `VectorTensorizer` with `actor_owner=1`) and gate its
   digest the same way before the loop uses it.
2. **Wire the stacked batch into the C4 producer.** `producer.py` calls
   `tensorize`/`record_action` per lane per owner today; the batched
   `VectorTensorizer` replaces that pair with one call per decision, and the
   batched `ActionSequenceV4` is the natural row source.
3. **C4 gate, unchanged:** 8 lanes/process, 2–4 processes, 10 segments,
   `rejected 0`, `eligible_act_rate > 0`, native eval not degrading; log
   env-steps/s, tensorize ms, inference ms. Include NVIDIA MPS (the RoyaleFish
   `ever_*` recipe).
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 21 results (2026-11-19) — C3-B's row build is complete

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §2 (C3),
`FINDINGS.md` §3.50–§3.53.

### What changed

C3's next section after candidates is `_towers` (0.145 of a 0.992 CPU
ms/decision). `crforge_bridge/vector_rows.py` now builds it for every lane of a
`VectorBattle`:

- **`VectorTowerBuilder`** — one gather over a per-lane six-row view, one sort
  (`owner != actor`, then model kind, then native id), then the health/target
  block: `hitpoints_ratio`, `shield_over_max_hitpoints`, `active`,
  `has_visible_target`, `king_activated`, `hitpoints_over_5000` and
  `max_hitpoints_over_5000`, plus the tile position and the kind-sized extent
  (half-extent 2.0 king, 1.5 otherwise). Attack-state and tower-troop-runtime
  columns stay zero, as the per-lane envelope leaves them.
- **The model view flips the kinds, not just the coordinates.** `_towers` sorts
  by `_model_tower_kind`, which swaps the princesses under `flip_x`; the batched
  build maps native kind `[0, 1, 2]` to model `[0, 2, 1]` under a flip and uses
  the same map for the emitted `tower_type`. The mirror test pins it.
- **Destroyed vs absent are different states.** A destroyed tower keeps its row
  with zero HP and the original maximum (ratio 0, `active` 0); a row that leaves
  the shared scene drops out and is padded exactly as the oracle pads
  (`tower_type` 0, troop 0, `owner_type` 2, zero vectors, `mask False`).
- **`VectorTowerSection.tower_index`** is the `entity id -> model row` map the
  tensorizer's `_relations` consumes as `tower_token_by_entity`; `lane(n)`
  slices one lane back to the exact `TowerSetV4` + dict the per-lane path emits.
- `_tower_slots` is now vectorized — the scan that fed both the tower and
  relation builds was a per-lane Python loop.

Scope is unchanged: seat 0 and the bridge's no-event envelope. Two facts are
pinned rather than derived: **`status` is always empty** (no `tower_activate`
events, so `king_activated` is 0), and **`tower_troop_id` is a static build
input** (`159000000` Tower Princess for the learner deck, `None` = no tower
troop), recorded on the gate artifact.

### Measured (256 lanes, learner deck, 40 decisions)

`runs/crforge-c3/batched-towers.json`:

| path | total | ms/lane |
| --- | ---: | ---: |
| batched tower section | **0.267 ms** | **0.00104** |
| per-lane `_towers` oracle | 8.92 ms | 0.0349 |

**Equivalence: pass**, `mismatches: []` over 40 decisions, all six towers on
every lane, **61,440 active tower rows** and **61,263 rows with a visible
target**. **33.5×** the per-lane oracle (three runs: 0.00104–0.00113 vs
0.0349–0.0372, so 32–33.5×). Section arithmetic only: replacing the live 0.145 share projects
**0.848 ms/decision** with groups, cards, relations and candidates also
batched, before spatial planes and scalars.

`crforge_bridge/tests/test_vector_rows.py` adds 4 tower tests (18 in the file at
that point): the 50-decision equivalence, the model-view flip, the static troop
pin (pinned, absent, a non-competitive id, a non-zero seat) and the
destroyed/absent row rules.

### Spatial planes — landed in the same session

`_spatial_planes` (0.117) is next in the section order and landed right after the
towers: `VectorSpatialPlaneBuilder` in the same module, with the archetype
`is_airborne` / `static_damage_basis` / persistent-root facts compiled into
`VectorEntityTables` (the tiles' child kinds were already there).

- **Towers and ownerless rows are not plane entities.** A towers-only board is an
  all-zero tensor; a neutral row produces the same planes as a removed row.
- **Float32 accumulation is part of the contract.** Two entities can share a
  cell; `np.add.at` with float64 operands narrows once and differs by one ULP
  from the per-lane loop's sequential float32 adds. The addends are cast to
  float32 before the scatter.
- **Flatten first.** The first version masked the full `(lanes, entities)` grid
  for every expression: **0.0188 ms/lane (2.44×)**. Working on the kept entities
  in one dimension is **0.00392 ms/lane (12.7×)**.

`runs/crforge-c3/batched-spatial-planes.json` (256 lanes × 40 decisions):

| path | total | ms/lane |
| --- | ---: | ---: |
| batched explicit planes | **1.004 ms** | **0.00392** |
| per-lane `_spatial_planes` oracle | 12.78 ms | 0.0499 |

**Equivalence: pass** over 40 decisions; 35 frames carried a building, 28,224
ground cells and 24,866 building cells exercised. Section arithmetic: replacing
the live 0.117 share projects **0.879 ms/decision** with the other five sections
batched, before `_scalars` and the empty effects/events.

3 more tests (`test_vector_rows.py` now 21, bridge suite **43**, ruff clean),
including a **forced Tesla** so the footprint fill is pinned deterministically —
the random policy alone never fielded a building.

### Match scalars — landed in the same session

`_scalars` is the smallest named section (**0.0140 CPU ms/decision** in the
shipped profile). `VectorScalarBuilder` writes the 18 match-wide numbers from the
batch plus the mode timeline; `_PlayerView` gained `crowns`/`elixir_exact` and
`_ObservationView` gained `time`/`phase`/`metadata` so the per-lane oracle reads
the same projection.

- **Two clocks, one timeline.** `BattleEnvV1` takes phase and multiplier from the
  native tick and the remaining clock from the canonical tick. `batch.tick` is
  canonical and the builder takes a `native_tick_offset` (0 for the vector gate
  and a fresh match). The 6,000-tick schedule is compiled once:
  normal → double (2,400) → overtime (3,600) → 3× (4,800) → tiebreak (6,000).
- **The gate runs the whole oracle chain.** `_scalars` takes the group/child
  overflow and candidate count as arguments, so `compare_scalar_section` runs the
  stock `_card_sets` → `_groups` → `_candidates` chain and passes the oracle's own
  counts.

`runs/crforge-c3/batched-scalars.json` (256 lanes × 40 decisions):

| path | total | ms/lane |
| --- | ---: | ---: |
| batched match scalars | **0.0733 ms** | **0.000286** |
| per-lane `_scalars` oracle | 0.970 ms | 0.00379 |

**Equivalence: pass** over 40 decisions; own elixir reached 10.0, up to 4
candidates and 14 visible entities. **13.2×** the per-lane oracle. Section
arithmetic: replacing the live 0.0140 share projects **0.978 ms/decision** with
all seven sections batched. The window stays in the normal phase, recorded
honestly as `phases_seen: ["normal"]`; a unit test pins the
double/overtime/3×/tiebreak columns at synthetic ticks on both paths.

3 more tests (file now 24, bridge suite **46**, ruff clean).

### Empty effects/events — landed in the same session

The bridge envelope carries no active effects and no combat events, so both
sections are the stock empty records widened to the lane count:
`VectorEmptySectionBuilder` (zero effect rows; events keep their 32-row capacity
with `owner_type` neutral, group/token addresses `-1` and `mask` False). Two
small view fixes were needed for the oracle path (`_TowerView.effect_states`,
`_ObservationView.events`).

`runs/crforge-c3/batched-empty-sections.json` (256 lanes × 40 decisions):

| path | total | ms/lane |
| --- | ---: | ---: |
| batched empty records | **0.0362 ms** | **0.000141** |
| per-lane `_active_effects` + `_events` oracle | 5.17 ms | 0.0202 |

**Equivalence: pass** over 40 decisions with both records validated; zero effect
and event rows, 32 padded event rows per lane. **143×** the per-lane oracle;
replacing the live 0.0983 share projects **0.894 ms/decision**. 2 more tests
(file now 26, bridge suite **48**, ruff clean).

Every named row section now has a batched build and a gate. Only the wiring
remains.

### Next session (session 22) — C3-B's last piece

> **Session 22 did item 1:** `crforge_bridge/vector_tensorize.py` assembles the
> stacked `UniversalSemanticBatchV4` and
> `tools/crforge/batched_tensorize.py` proves **1,504/1,504 lane-decisions
> digest-identical** (values, shapes, dtypes) at **0.118 ms/lane end to end**.
> C3-B is closed; the next work is C4. See the session-22 section above and
> `FINDINGS.md` §3.54.

1. **Wire it up:** a facts-only `tensorize` returning one
   `UniversalSemanticBatchV4` per lane, `batch_digest`-identical to the per-lane
   path on a captured sequence; only then claim ≤0.5 ms/lane end to end.
3. **Do not** widen the batch input, add lanes or GPUs while the row build is
   incomplete; the facts layer is 0.03 ms/lane and is not the cost.
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 20 results (2026-11-19) — C3-B's candidate section

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §2 (C3),
`FINDINGS.md` §3.49.

### What changed

C3's next section after cards and relations is `_candidates` (0.30 of a 0.992
CPU ms/decision). `crforge_bridge/vector_rows.py` now builds it for every lane of
a `VectorBattle`:

- **`VectorCandidateBuilder`** — one row per legal hand slot: card token, runtime
  form and cost, own-card row, the native 32×18 placement grid, the building
  half-footprint and `model_subcell_offset` the decoder needs, and the `(variant,
  visible, effective, slot)` UID. Rows are packed in hand order, which is the
  order `_candidates` walks `player.hand`.
- **`CandidateGeometry`** — the per-card static placement facts
  (`native_building_footprint` + `building_placement_profile`), compiled once per
  deck. They are the same facts `BattleEnvV1._cached_placement_entry` derives
  from `card_placement_mask`; the live legality of a tile stays the vector's own
  mask view.
- **The slot rule is the environment's**, not crforge's tile block: in hand, play
  not queued (`hand_pending`, the flag the bridge exports as `pendingHandSlots`),
  cost fits this decision's effective elixir, and at least one legal tile.
  crforge keeps a slot open while its play waits to run; `BattleEnvV1` closes it.
  Measured: after queueing a 4-cost Tesla with 10 elixir, the native slot still
  showed 222 legal tiles while the candidate count correctly dropped 4 → 3.
- **`reference_action_mask`** projects the mask `_candidates` reads (open slots,
  one entry per hand card with `row_major` as the `[32][18]` sequence, the exact
  effective elixir), and `compare_candidate_section` runs the stock `_card_sets`
  → `_groups` → `_candidates` chain against it.
- **`tools/crforge/batched_candidates.py`** is the gate: 256 learner-deck lanes,
  the checkpoint's own `model_config`, `ActionCandidatesV4` validation every
  step, 60 WAIT warmup decisions past the start cooldown, paired timing.

Scope is unchanged: **deploy rows only**. `players.abilityRuntime` is `null` in
the bridge's rich player record, so `ability_sources` is empty on both paths;
Mirror is refused because it needs the actor-private runtime contract the frame
does not carry.

### Measured (256 lanes, learner deck, 40 decisions)

`runs/crforge-c3/batched-candidates.json`:

| path | total | ms/lane |
| --- | ---: | ---: |
| batched candidate section | **0.221 ms** | **0.00086** |
| per-lane `_candidates` oracle | 13.60 ms | 0.0531 |

**Equivalence: pass**, `mismatches: []` over 40 decisions; up to 4 candidates per
lane, **5,181 queued-play slots** and **1,171 building rows** exercised (the two
cases that matter). **61×** the per-lane oracle (two full runs: 0.00084–0.00086 vs
0.0524–0.0531, 61–62×). Section arithmetic only: replacing the live 0.300 share
projects **0.693 ms/decision** with groups, cards and relations also batched,
before towers, spatial planes and scalars.

`crforge_bridge/tests/test_vector_rows.py` adds 4 tests (candidate equivalence
with record validation and coverage asserts, the slot rule against a hand-built
frame, the 2×2/3×3 building geometry, the Mirror refusal);
`compare_lane_facts` now checks `hand_pending` against `pendingHandSlots`. The
bridge suite is **36 tests**, all passing, ruff clean.

### Next session (session 21) — C3-B's remaining sections

> **Session 21 finished the row build:** `_towers`, `_spatial_planes`,
> `_scalars` and the empty effects/events landed in
> `crforge_bridge/vector_rows.py` with their own gates, equivalence pass (33.5×,
> 12.7×, 13.2× and 143× the per-lane oracles). Only the facts-only `tensorize`
> wiring carries to session 22; see the session-21 section above and
> `FINDINGS.md` §3.50–§3.53.

1. **`_towers` (0.145).** Two envelope facts to carry: `status` is always empty
   because `tower_activate` events are not supplied, and `tower_troop_id` is not
   in the facts — pass it as a static build input (`159000000` for the learner
   deck) and record it as a fidelity pin. Destroyed towers need the initial max
   HP carried across frames.
2. **Then `_spatial_planes` (0.117), `_scalars` and the empty effects/events.**
3. **Wire it up:** a facts-only `tensorize` returning one
   `UniversalSemanticBatchV4` per lane, `batch_digest`-identical to the per-lane
   path on a captured sequence; only then claim ≤0.5 ms/lane end to end.
4. **Do not** widen the batch input, add lanes or GPUs while the row build is
   incomplete; the facts layer is 0.03 ms/lane and is not the cost.
5. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 19 results (2026-11-19) — C3-B's card and relation sections

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §2 (C3),
`FINDINGS.md` §3.48.

### What changed

C3's next two sections after `_groups` are `_card_sets` (0.33 of a 0.992 CPU
ms/decision) and `_relations` (0.14). `crforge_bridge/vector_rows.py` now builds
both for every lane of a `VectorBattle` from the session-17 facts layer:

- **`VectorCardBuilder`** — the actor's eight-row card set (layout, roles and
  vocabulary from the tensorizer's own cached layout; runtime form from the hand
  form) and the opponent's nine-row set (eight zero rows plus the masked
  unknown-card row, because the bridge sends no events and `revealed_cards`
  stays empty). The **21-column** 16.635 schema also fills the actor's in-flight
  command: a pending row sets `pending_execution` and its target cell, with the
  seat-frame mirror `(18000 - pending_x) // 1000 - offset` and
  `pending_y // 1000 - offset`, where `offset` is the even-footprint shift
  (0.5 for a 2×2 Tesla); `pending_ticks_remaining` is zeroed because
  `B-general-v275` declares `neutralize_pending_ticks_remaining`. A **waiting
  champion ability** sets `ability_pending` on the champion's card row from the
  new facts-layer champion-slot block (`ability_command_ticks`, …), because
  crforge keeps ability commands out of the pending-card rows.
- **`VectorRelationBuilder`** — `entity -> target` / reverse,
  `tower -> target` / reverse (a tower can target an entity, and a tower is a
  target token) and `card -> group`, sorted and de-duplicated with one
  `np.unique` over a packed `(lane, source, target, relation)` key.
  `VectorRelationSection.lane(n)` slices a lane back to the exact
  `(1, count)` record for a future digest comparison.
- `reference_observation` projects player/tower/pending rows from the same
  facts, so the stock sections remain the oracle (the tests are not a shared
  shortcut).
- `tools/crforge/batched_cards.py` is the gate. It reads the **checkpoint's own**
  `contract.model_config` — 21 card-runtime columns for `B-general-v275`, not the
  default 16 — so the pending columns are exercised.

### Measured (256 lanes, learner deck, 40 decisions)

`runs/crforge-c3/batched-cards.json`:

| section | batched ms/lane | oracle ms/lane | speedup |
| --- | ---: | ---: | ---: |
| cards | **0.00161** | 0.0718 | **44.5×** |
| relations | **0.00984** | 0.0522 | **5.30×** |

**Equivalence: pass** — `card_mismatches: []`, `relation_mismatches: []`; 256
lanes at step 0 and lanes 0–31 after, up to 14 entities, 4 pending rows and 40
edges per lane. Four full runs measured 0.00122–0.00161 / 0.0718–0.0835 and
0.0098–0.0108 / 0.0522–0.0612, so quote the artifact and compare paired runs.
These are section arithmetic: replacing the live profile's 0.33 + 0.14 shares
projects **0.46 ms/decision** further, before towers, candidates, scalars and
spatial planes.

`crforge_bridge/tests/test_vector_rows.py` adds 5 tests (21-column card
equivalence over 50 decisions, relation equivalence over 50 decisions, the
pending-cell mirror rule for a troop and a 2×2 Tesla offset, the waiting
ability's `ability_pending` column, the tracker guard);
`test_vector_observation.py` adds the champion-slot projection. The bridge suite
is **32 tests**, all passing, ruff clean.

### Next session (session 20) — C3-B's remaining sections

> **Session 20 did item 1:** `_candidates`/placement landed in
> `crforge_bridge/vector_rows.py` with `tools/crforge/batched_candidates.py`,
> equivalence pass (61× the per-lane oracle). Items 2–6 carry to session 21; see
> the session-20 section above and `FINDINGS.md` §3.49.

1. **`_candidates` / placement (0.30).** The facts layer carries the native
   placement-mask view, but a candidate row also needs the hand-slot runtime
   (`effective_cost`, `form_code`, `footprint_width_tiles`,
   `footprint_height_tiles`, `model_subcell_offset`,
   `source_native_hand_slot`) that `BattleEnvV1.action_mask` builds today. Add
   those facts (still per-lane mask metadata, not a per-lane contract) and
   batch the rows; keep the per-lane `_candidates` as the oracle.
2. **Then `_towers` (0.145).** Note two envelope facts: `status` is always
   empty because `tower_activate` events are not supplied, and
   `tower_troop_id` is not in the facts — pass it as a static build input
   (`159000000` for the learner deck) and record it as a fidelity pin. Destroyed
   towers need the initial max HP carried across frames.
3. **Then `_spatial_planes` (0.117), `_scalars` and the empty effects/events.**
4. **Wire it up:** a facts-only `tensorize` returning one
   `UniversalSemanticBatchV4` per lane, `batch_digest`-identical to the per-lane
   path on a captured sequence; only then claim ≤0.5 ms/lane end to end.
5. **Do not** widen the batch input, add lanes or GPUs while the row build is
   incomplete; the facts layer is 0.03 ms/lane and is not the cost.
6. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 18 results (2026-11-19) — C3-B's first section: batched causal groups

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §2 (C3),
`FINDINGS.md` §3.47.

### What changed

C3's largest section is `_groups` + children (0.543 of a 0.992 CPU
ms/decision). `crforge_bridge/vector_rows.py` now builds that section for every
lane of a `VectorBattle` from the session-17 facts layer in one NumPy pass:

- `VectorEntityTables` compiles the per-vocabulary policy semantics once:
  base card, archetype (`form:<Name>`, then character/building/area node ids),
  child type, footprint/radius, runtime form, singleton group type and the
  source-card vocabulary id;
- `VectorGroupBuilder.build(batch)` fills the `BattleGroupSetV4` group/child
  tensors with the lane count as the batch dimension, plus slot-keyed
  `group_index` / `child_index` maps and both overflow counts;
- `reference_entities` projects `EntityStateV1` rows from the same facts and
  `oracle_groups` runs the stock `_groups` on them. The oracle resolves
  archetype, child kind, extent, radius and runtime form through its own
  helpers, so only the batched tables are under test.

The scope is the bridge's no-event envelope: **singleton groups** (the bridge
sends no causal-group reference), the bridge's raw `entity_kind` rule
(cardless rows are `effect`), no ability sources, projectiles, attack/movement/
visibility runtime or events, and **seat 0**. Towers are the separate six-row
section and stay out of the entity groups. `tools/crforge/batched_groups.py` is
the gate.

### Measured (256 lanes, learner deck, 40 decisions)

`runs/crforge-c3/batched-groups.json`:

| path | total | ms/lane |
| --- | ---: | ---: |
| batched group section | **13.81 ms** | **0.0539** |
| per-lane `_groups` oracle | 56.50 ms | 0.2207 |

**Equivalence: pass**, 40 decisions, 256 lanes at step 0 and lanes 0–31 after,
`mismatches: []`. **4.09×** the per-lane oracle; the busiest lane held 14
entities, all retained (overflow 0). This is the section arithmetic only:
replacing the live profile's 0.543 ms share with 0.0539 projects **0.503
ms/decision**, but the facts-only path still has to build cards, candidates,
relations, towers, scalars and spatial planes.

Two ordering rules were easy to get wrong and the test caught both:

1. **Group relation is local, child relation is global.** A singleton group's
   term is "this entity has no target"; a child's is "this entity is not
   anyone's target" — and it is built from the whole lane.
2. **Targets can be towers.** A skeleton attacking a princess tower names a
   target id that is not an entity; the lane-tagged membership test must miss
   it.

`crforge_bridge/tests/test_vector_rows.py` adds 5 tests (50-decision
equivalence, forced two-group/three-child overflow, empty-board mask/neutral
owner, the reference-entity projection, the seat-0 guard). The bridge suite is
**26 tests**, all passing; ruff clean.

### Next session (session 19) — finish C3-B's row build

> **Session 19 did part of item 1:** `_card_sets` and `_relations` landed in
> `crforge_bridge/vector_rows.py` with their own gate
> (`tools/crforge/batched_cards.py`), equivalence pass. `_candidates`/placement,
> `_towers`, `_spatial_planes`, `_scalars` and the payload write remain. See the
> session-19 section above and `FINDINGS.md` §3.48.

1. **`_card_sets` (0.33), then `_candidates`/placement (0.30), `_relations`
   (0.14)** from the same facts, each against its own oracle slice. Keep the
   bridge's empty `revealed_cards` until a separate fidelity gate changes it.
2. **Then `_towers` (0.145), `_spatial_planes` (0.117), `_scalars`** and the
   empty effects/events. Together with the group section these are the whole
   packet.
3. **Wire it up:** a facts-only `tensorize` returning one
   `UniversalSemanticBatchV4` per lane, `batch_digest`-identical to the per-lane
   path on a captured sequence; only then claim ≤0.5 ms/lane end to end.
4. **Do not** widen the batch input, add lanes or GPUs while the row build is
   incomplete; the facts layer is 0.03 ms/lane and is not the cost.
5. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 17 results (2026-11-18) — the vectorized observation facts layer landed

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §2 (C3),
`FINDINGS.md` §3.46.

### What changed

C3-B's named precondition was a vectorized `ObservationV1`; what existed was the
C1 spike's private `PolicyFrame`, which carries no towers, no placement rows and
none of the derived entity state. `crforge_bridge/vector_observation.py` now
fills a fixed-size `VectorObservationBatch` from `VectorBattle.observations` /
`masks` for every lane:

- both seats' header scalars (elixir, crowns, refusals, pending count,
  `enemy_seen` / evolution, per-seat own/enemy tower HP);
- both seats' hand / deck / cycle / pending rows and the packed
  `card_parameter` with the bridge's own slot resolution;
- all six towers as fixed rows selected by the pinned arena entity ids, with a
  header HP fallback for an enemy health bar the shared scene reports unknown;
- the native placement masks as a `(lanes, seats, 4, 32, 18)` **view** of
  `VectorBattle.masks` (the candidate-placement half of the tensorizer);
- derived `age_ms` and `velocity`, restarting when a row's entity id changes.

`tools/crforge/vector_observation_spike.py` is the gate and
`crforge_bridge/tests/test_vector_observation.py` pins it (equivalence, mask
view, fixed tower layout, age/velocity rule). See `FINDINGS.md` §3.46.

### Measured (256 lanes, 16.635.8, learner deck, 200 × 3)

`runs/crforge-c3/vector-observation.json`:

| threads | vectorized facts | decisions/s | bridge decoders | speedup | equivalence |
| ---: | ---: | ---: | ---: | ---: | --- |
| 1 | **0.0317 ms/lane** | 31,555 | 0.1518 ms/lane | **4.8×** | pass |
| 8 | **0.0342 ms/lane** | 29,282 | 0.1619 ms/lane | **4.7×** | pass |

The control is the bridge's full per-lane fact decode, so the ratio is 4.8×,
not C1's 19× for the smaller frame; the absolute cost is ~30× below the
tensorizer. 9–13 policy entities per lane and all six towers; ~260k legal
placement cells across 256 lanes at the peak.

### The gate has not moved

This is the precondition, not C3-B. Nothing here builds `ObservationV1`
contracts or tensorizes, so C1's **0.713 ms/lane** projection and the C3
profile's **0.99–1.07 CPU ms/decision** replay mean still describe the packet.

Two findings make the next step smaller than it looked:

1. **crforge's causal groups are all singletons.** The bridge's rich envelope
   carries no combat events, and `_causal_group_by_entity` is fed only by them,
   so `build_causal_groups` returns one group per entity ordered by the existing
   priority tuple. The batched group build is a sort, not a graph walk.
2. **The tracker collapses to an elixir schedule.** With no events, opponent
   card states stay `UNKNOWN_INITIAL` and `revealed_cards` stays empty, so the
   only tracker term the tensorizer reads is the enemy interval
   `(0, min(10, start + generated))`.

### Next session (session 18) — the batched row build, now on a verified input

> **Session 18 started it:** the batched group/children section landed and is
> 4.09× the per-lane oracle (0.0539 vs 0.2207 ms/lane at 256 lanes), equivalent
> over 40 decisions. The rest of this list is still the plan. See the session-18
> section above and `FINDINGS.md` §3.47.

1. **C3-B:** batch `_groups` + children first (0.54 ms/decision, the largest
   section) from `VectorObservationBatch`; keep the per-lane tensorizer as the
   digest oracle. Then `_card_sets` (0.33), `_candidates`/`placement_rows`
   (0.30) and `_relations` (0.14). Target ≤0.5 ms/lane, `batch_digest`-identical
   row-for-row.
2. **Do not** widen the batch input or add lanes until the row build is under
   the gate; the facts layer is 0.03 ms/lane and is not the cost.
3. **Fidelity note:** the bridge ignores the vector's `enemy_seen` columns
   (`revealed_cards` comes from events it never receives). Reproduce today's
   empty set in the first row build; fixing the gap is a separate digest gate.
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 16 results (2026-11-17) — the learner-deck blocker is closed; a run refusal is a match code

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §0–§4,
`FINDINGS.md` §3.45.

### The X-Bow tensorizer failure was the Knight hero

The C3 open item (`entity_kind='effect'`, `native_data_global_id=1`) was not a
missing archetype. Two bridge encoding bugs:

- hero **unit** rows are `<Card>Hero` (`KnightHero`), not `_hero`, so
  `_nearest_card_id` left `cardId=0` and `BattleEnvV1` classified the row as
  `effect`;
- `_bind_holder_identity` read the LogicData global id as a signed Java `int`
  (`-437504191`), rejected it as non-positive, and left the placeholder
  `dataGlobalId=1`. The catalog keys the unsigned bit pattern
  (`3857463105 → form:Knight_hero`).

Fixes: strip `Hero` in `crforge_bridge/observe.py`; mask the global id to
`0xFFFFFFFF` in `crforge_bridge/engine.py`. Tests: a live hero-deploy test, a
pure form-row classification test, and the match-code test below.

### The one remaining reject was a decided match, mislabelled

The first post-hero-fix 2-game mirror still had one distinct reject in game 2 (15
window counts): a queued Electro Spirit refused at its run tick. `H_LAST_RUN_REFUSAL` is
a **`LadderMatch`** code, but the bridge named it from the *giving*-refusal enum:
code 4 is `LadderMatch.OVER` (`START_COOLDOWN` in the wrong namespace). The play
lost a race with a crown-advantage end.

Fixes: name run refusals from the `LadderMatch` constants (read from the pinned
class), and resolve a queued play refused with `OVER` as
`action_canceled_terminal` — the give-stage `MATCH_OVER` path already does this.
`evaluate` now names both rejection shapes and adds `rejected_unique_total`.

### Result: learner deck is rollout-clean

`runs/crforge-c3/learner-deck/` — locked X-Bow deck, mirror, `B-general-v275`,
sampled, bridge transport:

| game | seed | terminal | tick | illegal | rejected |
| --- | ---: | --- | ---: | ---: | ---: |
| 1 | 7 | crown_advantage | 3,681 | 0 | 0 |
| 2 | 8 | crown_advantage | 4,032 | 0 | 0 |

Both games are decision-identical to the pre-fix run, so the fix changed no play.
Tests: `crforge_bridge` 17, `test_battle_env_rich_telemetry` 86 (+55 other
battle-env), tensorizer 72, evaluate 2 — all pass.

The learner deck also passes the C2 parity check: `resident_parity.py --deck
learner --decisions 60` is **60/60 identical** (resident 5.91 ms, bridge 7.33 ms,
paired 1.29×; parity pass, C2's 2× gate still belongs to C3) in
`runs/crforge-c2/learner-parity.json`.

The learner-deck tensorizer is **1.171 CPU ms/decision** (median 1.202, 90
decisions, entities median 5 / max 11, resident transport) in
`runs/crforge-c3/learner-deck/tensorizer-profile.json` — slightly above the
evaluator deck's 0.992 because the X-Bow list carries more spells, evolutions and
a hero, not because of the fix.

### Next session (session 17) — C3-B is still the throughput gate

1. **C3-B, unchanged:** a batched row build across lanes from a vectorized
   `ObservationV1` (which does not exist yet — that is the real precondition),
   `batch_digest`-identical to the per-lane path. Target ≤0.5 ms/lane.
   **Precondition landed in session 17:** `crforge_bridge/vector_observation.py`
   is the vectorized facts layer (0.032 ms/lane, equivalence pass); the row
   build itself is still open. See the session-17 section above.
2. **C3-A:** only if a compiled helper is cheap in this tree; there is no
   Cython/build extension today, so treat it as a design task, not a quick win.
3. **C4** can now use the learner deck: `profile_tensorizer.py --resident
   --deck learner` and `resident_parity.py --deck learner` are the entry points.
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 15 results (2026-11-16) — C2 landed: parity holds, the 2× gate was never the socket

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §0–§4.

### What landed

- **`native_runner/crforge_resident.py`** — `CrforgeResidentEnv`, the full
  `NativeClashEnv` surface (`create_match`, `observe`, `observe_atomic`,
  `observe_rich`, `step`, `queue_*_at`, `attest`, `status`, `close_transport`)
  over an **in-process** `crforge_bridge.engine.Engine`. Only `_request` changes:
  it calls the bridge's own `dispatch()`, so parity is a property of
  construction, not a test result. `status()` adds `resident: true`, `lanes: 1`,
  `transport: "in-process"` so no artifact confuses it with the 256-lane vector.
  `BattleEnvV1` detects it exactly as it detects the bridge (`engine: crforge`).
- **`tools/crforge/resident_parity.py`** — the C2 gate. The resident leg decides
  with a seeded policy, the bridge leg **replays those exact actions**, and the
  tool compares per-decision native envelope, per-owner observation, mask,
  entity/tower/event counts, rewards and terminals; then it re-runs both on fresh
  episodes and times `env.step` in **one alternating loop** (this host's absolute
  ms move ~2×, so the per-decision paired ratio is the estimator).
- **Two shared engine cost fixes** (`crforge_bridge/engine.py`, both apply to the
  bridge too, both bit-identical):
  - **run-refusal reads are windowed.** `step` copied both 7,024-int seat
    observations on *every* tick to read one header index. crforge clears
    `last_run_refusal` when it opens a decision, so a code can only be observed on
    the post-step tick a *given* play's run produced; the bridge now watches
    `{runTick, runTick + 1}` per given command. All other ticks read nothing.
  - **one row decode per capture.** `_capture` decoded seat 0 twice
    (`decode_rows` + again inside `entity_rows`) and `observe` decoded it a third
    time for `result`; `entity_rows` now accepts the decoded rows, and the capture
    is memoized by `(generation, tick, command sequence, done)` so the two
    pre-decision observes of one tick share one decode.
- **Tests:** `crforge_bridge/tests/test_resident.py` (50-step receipt-for-receipt
  equality against a second engine, stale-cache guard, close/reconfigure, the
  shared-row `entity_rows` equivalence) and a `resident_env` fixture.

### Parity: pass

**60/60 decisions identical** in every run whose parity artifact was kept (six
60-decision runs). One earlier run in a three-run loop diverged at decision 0
(`owners` only, native envelope matched) and was **not reproduced** in the next
eight runs; that run had no artifact, so it stays a recorded open question, not a
known defect. The comparison covers the ordinary, rich and atomic envelopes
(validated on the resident side, raw on the bridge side) plus masks, rewards and
terminals.

### Throughput: 1.2–1.5×, the 2× gate is missed

Paired `env.step` on the evaluator deck, `policy_features_only=True`, seed 1:

| run | resident | bridge (same process) | paired ratio |
| --- | ---: | ---: | ---: |
| artifact of record | 3.46 ms (289/s) | 5.15 ms (194/s) | **1.33×** |
| 1 | 4.80 ms (209/s) | 5.97 ms (168/s) | **1.27×** |
| 2 | 3.94 ms (254/s) | 6.30 ms (159/s) | **1.53×** |
| 3 | 4.23 ms (236/s) | 5.18 ms (193/s) | **1.21×** |
| earlier loop | 4.55 / 3.48 / 4.70 | 5.87 / 4.21 / 6.07 | 1.32 / 1.19 / 1.23 |

Controls on the same host: the **HEAD bridge** in the same harness is **7.08 ms**
(141 decisions/s), and the bridge in its **deployed shape** (own process,
`python -m crforge_bridge`) is **6.70 ms** (149/s). So the socket + JSON round
trip is **~1.2–1.7 ms of a ~4–6 ms decision**. The other 3–4 ms is
`BattleEnvV1`: observation build, action masks, placement-mask validation and the
atomic envelope — exactly C3's row build.

**Verdict:** the resident adapter is correct and worth keeping (it removes the
wire, the duplicate decode, and a dependency on a listening process), but it
cannot be 2× while the step is Python-bound. **C2's throughput gate moves to C3.**
Do not add lanes, processes or GPUs to close it.

### Next session (2026-11-17, session 16) — C3's row build, then C4

1. **C3-B (the throughput gate, now owned here):** fill the fixed-size rows for
   all lanes in one pass from the VectorBattle frame — groups, children,
   candidates, relations, cards, towers, effects, events — and keep the batch
   `batch_digest`-identical to the per-lane path on a captured sequence. Target
   **≤0.5 ms/lane**; the C2 2× gate is met when `BattleEnvV1.step` on the
   resident adapter drops under ~2.5 ms/decision.
2. **C3-A (only if it is the cheap win):** port `_groups` to a Cython/C helper
   over plain arrays; gate ≥4× on that section before porting more.
3. ~~**Learner-deck blocker:** the X-Bow deck fails closed in the tensorizer on
   `entity_kind='effect'`, `native_data_global_id=1`. Classify or filter
   bridge-only effect rows before any learner-deck rollout.~~ **Done
   2026-11-17:** the row was the Knight hero with a signed Java global id; see
   the session-16 section above and `FINDINGS.md` §3.45. The learner deck
   finishes 2/2 games with `illegal 0`, `rejected 0`.
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

### Session 14 record (previous)

**Read next:** the session-14 record below, then `FINDINGS.md` §3.43–§3.44.

## Session 14 results (2026-11-16) — C0 closed; C3 says the tensorizer is Python ceremony

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §0–§4,
`FINDINGS.md` §3.43–§3.44.

### C0 — closed, 20/20 clean

`runs/crforge-c0-20261116/soak20-rerun/`: **20/20 games terminated** (seeds 7–26,
both seats alternating), **illegal 0 / truncated 0 / rejected 0**, every game a
`king_tower_destroyed` terminal. With the 2-game `evaluate2` re-run and the
9-of-20 partial soak, the MiniPekka `26000018` class has not returned since the
give-tick legality fix. **C0's gate is met: crforge may bulk-train once C3
lands.**

### C1 record corrected — the spike's scripted policy never played

`tools/crforge/encoder_spike.py` picked a legal cell with
`isinstance(entry, dict)` on `placement_masks` values, which are
`FrozenMapping`: the guard never fired, the scripted policy stayed on `WAIT`,
and the recorded tensorizer number was measured on a board with no troops.
Fixed to `Mapping`, plus the owner-signed `model_subcell_offset` that building
placements require (`building 27000006 requires native subcell offset`). The
encoder half of C1 is unaffected — vectorized facts are 0.0145 ms/lane vs 0.289
per-lane at the default shape; the tensorizer half is re-measured below.

### C3 attribution — the packet is 3,754 Python calls per decision

New tool `tools/crforge/profile_tensorizer.py` drives a live policy episode
(`B-general-v275`, evaluator deck, both seats, sampled), captures each decision's
observation **and** the `ActionSequenceV4` the policy recorded, then replays only
the tensorizer half — `tensorize` + `record_action` — under `cProfile`. It proves
an A/B with a capture digest plus a per-decision tensor hash, and reports CPU
ms/decision next to wall time.

| measurement | value |
| --- | --- |
| stock `tensorize` + `record_action` | **1.069 CPU ms/decision** |
| shipped tree | **0.992 CPU ms/decision** (median 1.000) |
| decisions in the run | 128 (terminal at tick 725) |
| entities per decision | median 8, max 11 |
| events per observation | ≈99 — the tracker walks all of them |
| Python calls per decision | **3,754** |

Sections (stock, cumulative ms/decision): `_groups` 0.725, `_card_sets` 0.390,
`_candidates` 0.300, `_card_runtime_features` 0.189, `tracker.update` 0.172,
`_towers` 0.145, `_relations` 0.142, `_spatial_planes` 0.117, `_events` 0.090.
Per-decision ceremony: 330 `isinstance`, 217 contract-mapping `__getitem__`,
214 `_finite`, 195 `FrozenMapping.get`, 117/101/85 `max`/`min`/`sum`.
**~35 % of profiled time is builtin call overhead plus the frozen-mapping layer**
(28 % builtins, 4 % `contracts.py`, 3 % `Mapping.get`); the builder's own bytecode
is the other half. This is bookkeeping around small arrays, not array math.

Shipped, all bit-identical (same `batch_digest` on the same capture): `_finite`
fast path, cached actor deck layout, per-frame player lookup, single-pass group
aggregation. Profiled: `_groups` **0.725 → 0.543**, `tensorize` 2.227 → 1.875.
**End-to-end: within noise** — two alternating pairs gave −5.6 % and +13.5 %,
and the host's run-to-run spread is ±15 %, so there is no throughput claim. A
NumPy row-build variant was measured and **reverted**: digest-identical, flat
end-to-end, extra allocation surface for nothing.

### C1 record re-run at the default shape — C3's gate is met

`runs/crforge-c1/encoder-spike.json` (256 lanes × 200 × 3, tables 16.635.8,
content `062a11e0…`): vectorized facts **0.0165 ms/lane = 60,702 decisions/s**
(19.1× the per-lane control's 0.315), frames equivalent. Existing path with the
shipped tensorizer: cold `observe` **0.400**, in-session `tensorize` **0.697**;
**projected end-to-end 0.713 ms/lane = 1,402 decisions/s = 1.16 matches/s per
process**, against C3's ≥0.8 gate. All C1 gates pass. threads 8 is the same
(0.0177 ms/lane, 19.9×, 0.714 ms/lane).

Two numbers, two shapes — quote the right one: the C1 tool reports the *minimum*
single decision over 100 decisions of a seed-1 game (0.697 ms), the C3 profiler
the *mean over a whole replay* of a busier game (0.992 ms, median 8 entities).
Size runs with **0.99–1.07 ms/decision**; use 0.71 ms/lane only as the gate
measurement.

**Open item for C2:** the same path on the X-Bow learner deck fails closed in the
tensorizer — `public entity resolved to UNKNOWN archetype: entity_kind='effect',
native_data_global_id=1`. Twenty evaluator-deck games never produced it. The
resident adapter must classify or filter bridge-only effect rows before a
learner-deck rollout is trusted; C1 stays on the evaluator deck.

**Verdict.** The C3 gate is met; the ≤0.5 ms/lane target is not. Tuning is
exhausted — the packet is 3,754 Python calls/decision. The next lever is
structural, not incremental (`FINDINGS.md` §3.44).

## Next session (2026-11-17, session 15) — superseded by session 16 above

Session 15 did item 3 (C2) and measured it; items 1–2 carry into session 16's
plan. Kept for the original wording of the C3-A/C3-B gates:

1. **C3-A, probe the ceiling cheaply:** port **one** section, `_groups`, to a
   Cython/C helper over plain arrays and measure it against the Python row on the
   same captured decisions (the profiler replays them). Gate: **≥4× on that
   section** before porting the rest. If it cannot beat the row build by 4×, go
   straight to C3-B.
2. **C3-B, the real win:** batch the row build across lanes — one pass over 256
   lanes filling the same fixed-size arrays (groups, children, candidates,
   relations, cards, towers, effects, events) from the vectorized frame. Target
   ≤0.5 ms/lane; keep the batch `batch_digest`-identical to the per-lane path on
   a captured sequence before trusting it.
3. ~~**C2 resident adapter:** `native_runner/crforge_resident.py` over
   `LiveBattle`/`VectorBattle` with the `NativeClashEnv` surface; deck pool +
   `SideSetup`; acceptance = 50+ identical observations/masks/rewards and 2× env
   throughput.~~ **Done 2026-11-16:** parity 60/60, throughput 1.2–1.5× — the
   remaining 2× is C3.
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 13 results (2026-11-16) — C0 fix landed; C1 says the tensorizer is the packet

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §0–§4,
`FINDINGS.md` §3.42–§3.43.

### C0 — the MiniPekka reject was a delay-bin mismatch

The soak reproduced the last reject class immediately: MiniPekka `26000018`
(owner 1) with `policy_delay_offset_bin: 3` at native tick 840; crforge refused
`NOT_ENOUGH_ELIXIR` (`givenTick 840`, `runTick 860`) with 3.952 elixir against a
cost of 4.

Root cause: `BattleEnvV1` applied the native five-bin touch window to crforge.
The native probe validates a touch when it arrives, so bins 0–4 may spend the
next four ticks of regeneration. crforge's `PlayerCommands.covers` reads
`wholeElixir() - promised` at the tick the command is given, with no future
regeneration. The mask opened the slot on bin 3; the bridge injected it at the
give tick.

Fix in `native_runner/battle_env.py`:

- `BattleEnvV1.runner_engine` is resolved once (`engine=` or `status().engine`);
  a crforge endpoint gets a **one-bin** window (`_touch_window_elixir_raw` now
  takes `bins=`). The native engine keeps five bins.
- `_action_touch_delay_bin` returns 0 on crforge, so compound decisions validate
  each touch at the give tick.
- The mask reserves `max(this process's pending, player.elixirReservedRaw)`,
  i.e. crforge's own promise accounting.
- Regression tests: `test_crforge_validates_at_the_give_tick_not_a_future_touch_bin`,
  `test_crforge_uses_the_engines_reserved_elixir_header`.

Evidence: `runs/crforge-c0-20261116/soak20/` — **9 of 20 games completed at
wrap-up, all clean**: `illegal_total: 0`, `truncated: 0`, `rejected_total: 0`,
`games_with_rejections: 0`, seeds 7–15. `summary.json` records the interruption
honestly; **finish the remaining 11 games before a bulk run**. The 2-game
`evaluate` re-run is clean: **2/2 completed, illegal 0, truncated 0, rejected
0** (`runs/crforge-c0-20261116/evaluate2/`). `evaluate` now reports
`illegal_total`, `rejected_total`, `games_with_rejections` and
`rejected_reasons`; rejected commands are never folded into "illegal".

### C1 — vectorized facts are cheap; the V4 tensorizer is the packet

`tools/crforge/encoder_spike.py` runs one 256-lane `VectorBattle`, encodes the
same seat rows with the bridge's per-lane path and with a NumPy frame built from
`v.observations` + `v.masks`, and asserts field equality on a tick. Artifact:
`runs/crforge-c1/encoder-spike.json`.

| shape | vectorized ms/lane | decisions/s | per-lane ms/lane | speedup |
|---|---:|---:|---:|---:|
| 256 lanes, 1 thread | 0.028 | 35,780 | 0.542 | 19.4× |
| 256 lanes, 8 threads | 0.031 | 32,258 | 0.573 | 18.5× |

Existing bridge path on the same deck (cold `BattleEnvV1.observe` +
`UniversalObservationTensorizerV4`): **observe 0.647, tensorize 1.413,
observe+tensorize 2.060 ms/lane**. Projected end-to-end (vectorized frame +
tensorizer): **1.44 ms/lane = 694 decisions/s** (1.4× today).

**Verdict.** The observation builder is no longer the lever: vectorized facts
are ~0.03 ms/lane (35k decisions/s), so C1's encoder gate passes by >30×. The
end-to-end 1 ms/lane gate does **not** pass yet, because
`UniversalObservationTensorizerV4` is ~1.0–1.4 ms/lane and now dominates. Do not
add lanes, processes or GPUs; the next work is feeding the tensorizer from the
frame (C3).

Caveat: these are a 40-step × 1-repeat smoke under session load, not the
200 × 3 quiet-host run; re-run the tool with its default shape before quoting
them as the C1 record. The qualitative split is unambiguous.

The old "~13 ms/lane bridge encoder" was a transport/JSON + full-assembly
number, not the in-process observation build; measured `observe` is ~0.6 ms and
the bridge's `observe_atomic` `_capture` ~0.8 ms.

### Session 13's next-session plan — superseded by session 14 (above)

1. ~~**Finish C0**: run the remaining 11 games~~ — **done, 20/20 clean**
   (session 14).
2. ~~**C3 (blocking the C1 record)**: feed `UniversalObservationTensorizerV4`
   from the vectorized frame~~ — **attributed and partly reduced**; see
   `FINDINGS.md` §3.44. The end-to-end gate now sits at this host's noise floor,
   so C3 continues structurally (C3-A/C3-B in the session-15 plan above).
3. **C2 resident adapter** — still open, unchanged.
4. **Parity cadence** — unchanged.

### Stop conditions

- C1's **end-to-end** gate misses after two iterations → fix the tensorizer, do
  not scale.
- Any crforge/native divergence that cannot be attributed to the adapter → stop
  and use the native gate. Do not "train through it".
- The engine is not deleted, not idled, and not the fallback for evidence. It is
  the evidence.

### Superseded — session 12 plan (fuse the rich frame into tensor rows)

This remains useful **native-path** work (the gate and the fallback), but it is no
longer the bulk path. Do it only if the native engine must become competitive
again, or opportunistically while C1/C2 land, since the vectorized tensorizer is
shared.

### What session 11 changed

Step 2 of the data-path plan is done. `policy_features_only` builds every
contract through `construct_contract` inside a `lean_contracts()` scope, so the
training path no longer validates, freezes or records provenance; the hand
runtime contract and form aliases are cached, and observation caches are keyed
by `(identity, lean)`. Paired A/B: **11.75 → 7.81 CPU ms/packet** (median
−28…−34 %), **78 → 113 lane-packets/s**; `_entities` −60 %, `_extract_towers`
−44 %, `_players` −55 %. The full suite and the tensorizer-parity test pass; the
validated evaluator/parity path is unchanged. See `FINDINGS.md` §3.43 for the
per-section table and the gate caveat (this host's absolute numbers move ~2x
with load, so use paired runs).

### The shape of the remaining packet

One lane, quiet host: `step` 8.7 = `_advance_headless` 3.1 (engine round +
atomic rich capture), `observe` 4.0 (`_entities` 1.4, `_extract_towers` 1.0,
`_players` 0.3, events 0.5, mask ~1.7), and ~1.6 masks/bookkeeping. cProfile has
no contract machinery left in the top 20: the hot rows are `_rich_object` /
`_phase_object` JSON binding, socket `recv`, `resolve_attack_runtime` and
placement geometry. That is the fused-path target.

### What session 8 changed (historical)

`async_cluster_self_play.py` is now pipelined: one `ppo-inference` thread with
at most one batch in flight, the coordinator stages every completed receive
while the forward runs, and segment-boundary bootstraps use the same thread.
The 2 ms coalescing window is kept and also applied at the job boundary. All
1,293 tests pass and a 10-segment real-checkpoint run on 4 x86 engines completed
cleanly. With a cheap (synthetic) forward the loop is 7 % faster on wall and
14 % on non-model time; with the real 17.5M checkpoint it is neutral — the
forward is 98 % of wall. It is a prerequisite for the GPU/8-engine rig, not a
CPU throughput win.

### The corrected throughput reality

The old gate ladder used ceilings measured on the raw resident channel, which
does no observation decode and no tensorization. Those are ~90 % of a real
packet:

| Rig (8 lanes, 2 ms window) | Old "ceiling" | **Measured training floor** |
| --- | --- | --- |
| 1× x86 | 0.61/s | **0.082/s** (0.138 in profiler units) |
| 2× x86 | 1.22/s | 0.10/s |
| 4× x86 (only 2 real cores here) | 2.44/s | 0.15–0.16/s |
| 4× A51 | 0.26/s | 0.05/s (unchanged) |

Per packet on one x86 engine: **32 ms native step + observation build, 14 ms
tensorize, 6 ms channel** — 51 ms median / 82 ms mean. The 2.5/s goal at 8
engines needs ~21 ms/packet, i.e. the worker cost must roughly halve.
Pipelining cannot do that; it removes ~12 ms/packet of coordinator idle.

**After §3.43 (lean training observation), paired on this host:** one lane
**78 → 113 lane-packets/s** (stock → lean), i.e. ~0.11 → ~0.16 ladder-matches/s
per process at 720 decisions/match; the 8-lane engine shape is 97–112
lane-packets/s. This is the observation half of the packet; the native round
trip (~3 ms) and rich telemetry binding are the remaining thin-path target.

### Judgment — fix the data path, then scale

The engine, the loop and the model are done. The single thing that is ~5× behind
the field is the per-decision **data path**, and it is the only fix that
multiplies every engine, every future run and every GPU hour at zero recurring
cost. Checked in `.resource` on 2026-11-15:

| | FirstLight-Zero | daybreak-cr | us |
| --- | --- | --- | --- |
| Concurrency | 8 resident per collector, many collectors (the 10/s figure needs ~16 collectors = **128 slots**) | 13–50 collectors, **one game each** | 4 engines × 8 lanes |
| Per-decision data | compact fixed arrays (26 scalars, 64×14 units, 5 options) | same arrays built by a **C extension** (`cr/native_features.c`); `CR_NATIVE_FEATURES=0` is the Python path | `ObservationV1` objects + 21-column tensorizer in Python |
| Serving | one server for the farm, CUDA graph per batch bucket | one decision server per GPU, all waiting collectors per call | local forward, ~2 packets/forward |
| Model | 1.17M, pruned to one mirror | 1.26M / 3.4M / 8.0M | 17.5M |

**The arithmetic.** 9.85 matches/s ÷ 128 slots = **0.077 matches/s per slot**.
Our raw engine (no observation work) is **0.081**; our real loop is **0.017**.
So they run at the engine's raw limit and we are ~5× above it. Their engine is
not faster — their per-decision overhead is nearly zero. (The §3.25 note that
"9.85 matches/s is 16 slots × 0.62" is wrong by ~7× and is superseded here.)

If 40 ms of observation+tensor work becomes ~10 ms, a packet is ~25 ms and one
engine goes **0.082 → ~0.26 ladder-matches/s**. **8 engines on real cores ≈
2.1/s ≈ 180 k matches/day**, i.e. FirstLight's whole 1,450-round run in ~8 days.
That is the goal reached with 8 engines and a thin path, not with one engine and
a fantasy ceiling.

### Session 10 — attribution done, two fixes shipped (2026-11-15)

Step 1 is finished. New tool
`tools/native_analysis/profile_step_breakdown.py` runs the worker's own batch
shape (decode lanes → `begin_round` → lane thread pool → `finish_round`) with a
scripted play-every-4 policy and reports per-method wall time plus a sequential
cProfile with per-module totals.

**One lane, 16.635.1, current tree:** `step` **17.1 ms**, of which `observe`
**10.7 ms (62 %)** — `_entities` 4.7, `_extract_towers` 2.7, `action_mask` 1.9,
`_players` 0.9, `_events_for` 0.8 — and the engine/channel round 5.0 ms.
cProfile puts ~34 % of self time in `contracts.py` plus builtin type checks and
~11 % in `rich_telemetry_adapter.py` + `phase_runtime.py`: the build is dominated
by validated, deeply frozen Python objects, not game logic. The tensorizer never
reads entity/tower `runtime_provenance`, `effects`, `shield_state`,
`capture_runtime`, `periodic_attack_modifier`, `internal` or `movement_target`.

Shipped (both measured):
- **scalar-first `contracts._freeze`** — paired A/B, median single-lane
  **48.1 → 57.4 pkt/s (+19 %)**, CPU **19.0 → 15.7 ms/packet**.
- **one tower projection per tick** — `step` now asks for the rich projection
  and `observe`/`action_mask`/`_derive_events` reuse it (was two projections of
  the same tick). `--trace-towers` confirms one computation per tick.

**Lanes do not scale.** Same engine at `--lanes 1/2/4/8`, process CPU measured
with `getrusage`: the process consumes **0.90–0.94 of one core at every lane
count**; lanes buy at most ~1.5×. The worker is GIL-bound, so lanes only overlap
the engine wait. Throughput is **processes (engines)**, not lanes — the earlier
"8 lanes ≈ 70 lane-decisions/s ≈ 0.082 matches/s" is one Python core, not the
engine.

### Session 11 — lean training observations shipped (2026-11-15)

Step 2 is done; see `FINDINGS.md` §3.43.

- `policy_features_only` constructs every contract with `construct_contract`
  inside a `lean_contracts()` scope: no `__post_init__` validation, no
  `FrozenMapping` freeze walk, no provenance trees, no projectile-velocity
  rewrite.
- `project_runtime` / `project_player_runtime` take `lean=True` and route every
  typed runtime state (and the phase/attack state) through the same constructor;
  `unknown_provenance` is one cached shared record per field universe.
- `_native_form_aliases` is cached per snapshot identity and
  `_hand_runtime_contract` per validated hand selection (16 → 1 projections per
  packet); all observation caches are keyed by `(identity, lean)`.
- Paired A/B, 8 alternating runs: CPU **11.75 → 7.81 ms/packet** (per-pair
  median −28 %, up to −37 %), **78 → 113 lane-packets/s**. `_entities` 3.43 →
  1.37, `_extract_towers` 1.84 → 1.02, `_players` 0.60 → 0.27.
  `_advance_headless` is flat — it is engine + rich capture, not observation.
- Validated path unchanged: `observe()` defaults to `lean=False`; the scripted
  parity seeds and evaluator take exactly the old path. New tests pin that a
  lean observation tensors bit-identically and that `cls(...)` still validates.

### Task order — native fallback (superseded for bulk, kept for the gate)

| # | Step | Effort | Gate |
| --- | --- | --- | --- |
| 1 | ~~Micro-profile `BattleEnvV1.step`~~ (**done, §3.42**) | — | top-5 functions = the whole observation build |
| 2 | ~~Thin the observation for training~~ (**done, §3.43**) — non-validating constructor, no provenance/unused states under `policy_features_only`, cached hand contract, lean-keyed caches. | done | paired **11.75 → 7.81 CPU ms/packet (−28…−34 %)**; validated parity path untouched; the absolute 8 ms number depends on host load |
| 3 | **Fuse the rich frame into tensor rows** — the remaining packet is native round trip (~3 ms) plus `_rich_object`/`_phase_object` binding; skip the intermediate Python contracts (daybreak's `native_features.c` shape). Per-lane pooling is deprioritised: lanes are GIL-bound and now a small fraction. | 3–5 days | **CPU ≤ 5 ms/packet** and tensorizer parity |
| 4 | **One short GPU session** (~1 h, ~$1): validate the pipelined inference thread on CUDA and record the real end-to-end number | 1 h | no graph/stream errors, ≥0.5/s on 1× x86 |
| 5 | **Move to a real 8-core host** for 8 engines (the engine is a plain x86_64 ELF; this laptop has 2 P-cores). Eight engines = eight processes, which is the parallelism that actually scales. | 1 day | 8 processes ≈ 2/s aggregate |
| 6 | **400+ paired games on the native engine**, then a **bounded specialist fine-tune** (X-Bow 3.0 or Hog 2.6) before the generalist league | days of GPU | Wilson intervals, 0 rejected |

**In parallel, rules permitting:** play ~20 games of `A-hog26-v738` /
`B-general-v275` on Null's ladder and record the results. It costs almost
nothing and measures the one risk throughput cannot answer — how far official
16.635 training transfers to Null's custom balance. Learn that this week, not
after a month of training.

### Do not (native-path notes; the crforge line is superseded)

- Do not chase 2.5 matches/s as a target in itself. Decisions/day at a gateable
  quality is the metric; 2/s with the 17.5M model is already a serious budget.
- ~~Do not start crforge now.~~ **Superseded 2026-11-15:** crforge is the bulk
  path after the C1 encoder gate. The original objection stands as a risk, not a
  prohibition: it needs a vectorized V4 observation adapter and is never a valid
  gate. See the top decision and `ROADMAP_CRFORGE.md`.
- Do not farm phones. The A51 stays the ARM64 parity/deploy oracle.
- Do not run a long GPU fine-tune before steps 1–3: at today's packet cost the
  GPU idles 96 % of the time waiting on Python.
- Do not quote `resident_batch_throughput` matches/s as a training rate. Four
  engines gave 0.736 aggregate because one Python client decodes every lane.
- Do not expect loop pipelining to lift a row-bound CPU forward (§3.40); it
  pays only when the forward is fixed-cost (GPU) and there are enough engines
  that the coordinator would be the serial resource.
- Do not raise `CR_PPO_INFERENCE_BATCH_WAIT_MS` — still a measured loss.
- Do not add lanes expecting throughput: the worker is GIL-bound (0.90–0.94 of
  one core at 1–8 lanes, best run 1.5×). Add processes (engines) instead.
- Do not measure the data path with wall time on this laptop while a browser is
  running; use CPU ms/packet and the section shares, and compare **paired
  runs** — absolute CPU ms/packet moved ~2x during 2026-11-15 (§3.43), so a
  stock number taken an hour earlier is not a baseline.
- Do not build a validated/evaluator observation through `construct_contract`
  or inside `lean_contracts()`: the training shortcut is only valid for
  in-process policy rows, and the observation caches are keyed by
  `(native identity, lean)` precisely so the two cannot mix.
- Do not run the new inference thread on CUDA until graph capture/replay,
  autocast and the `model_streams` waits are confirmed there.
- Everything else in "Do not do" below still applies.

### Start state

```bash
# x86 engines, one per core, ports 26789+N.  Kill by PID: `pkill -f` also
# matches the shell that runs it.
for i in 0 1 2 3; do
  setsid .venv/bin/python tools/native_analysis/crx_engine_host.py \
      --engine-index "$i" --seconds 3600 > /tmp/opencode/engine$i.log 2>&1 &
done
# Worker processes take the FIRST N allowed CPUs, so pin the profiler's group
# away from the engine hosts when both run on this laptop.
taskset -c 4-11 .venv/bin/python tools/native_analysis/profile_cluster_collection.py \
    --engines 4 --lanes 8 --steps 120 --diagnostic-timing --allow-cpu-sharing

# §3.42 attribution: one lane on one core, cProfile of 60 sequential steps.
# Engine 0 owns CPU 0, so pin the profiler to an E-core and leave the engine alone.
taskset -c 4 .venv/bin/python tools/native_analysis/profile_step_breakdown.py \
    --checkpoint .resource/RoyaleFish/models/B-general-v275.pt \
    --engines 1 --lanes 1 --steps 100 --warmup 5 --profile-steps 60 \
    --play-every 4 --profile-dump /tmp/opencode/step.prof \
    --output runs/step-breakdown/lane1.json
# scaling proof: repeat with --lanes 1/2/4/8 and read cpu_ms_per_packet
# §3.43 pairing: this host's absolute CPU ms moves ~2x with background load, so
# alternate stock/lean (`git checkout` the four source files vs this tree) and
# compare each pair.  runs/step-breakdown/ab-{stock,lean}-*.json is the 8-pair
# one-lane set, ab8-*.json the 8-lane worker shape.

A51, if needed for a parity comparison only:
`./launch_cluster_a51.sh --engines 1 --base-port 26799`.

---

## Ecosystem audit — 2026-11-14 (`CRkonwledagebase`)

`.resource/CRkonwledagebase` indexes **58 projects, 13 capabilities, 90
project×capability relations** with R0–R4 evidence grades. Its own headline is
that **nothing reaches R3/R4**: most entries are author-documented (R1) and a few
are fixed-commit static reviews (R2). Read it as a map, not a parts bin.
(`.resource/REA` is a generic reverse-engineering MCP, not a CR project.)

### Scoreboard for "X-Bow 3.0 beats top players, any deck, Null's or official"

| Component | Best public asset | Evidence | Where we stand |
| --- | --- | --- | --- |
| 16.635 rules at speed | **crforge** 16.635.3/8: 127k steps/s/thread, held tick-for-tick to recorded battles; **RoyaleFish** x86_64 16.635.1 probe, 16 matches/process | author conformance set (43,807 battles); R2 interface audit | **behind** — training runs on 4 phone engines |
| Native ARM64 16.635.1 | our A51 probe: touch hook, 32 resident lanes, attested manifest | verified on device | **ahead of the field** |
| Deck-general policy | RoyaleFish `B-general-v275` — 17.5M params, **16.635 data**, 275 updates | paired evals of its sibling | **behind** — ours is 12.7M on 15.535.86 data, step 460 |
| Specialist policy | RoyaleFish `A-hog26-v738` **71.9% ±2.6** vs its start; Log Bait and anti-2.6 exploiters | 1,000+ game gates | behind, but the recipe is public |
| X-Bow 3.0 | FirstLight-Zero `v1450`: **1.17M params, self-play only, 97.5% vs a 16.8M IL model** | author-reported, 40 games, **mirror only, 15.535.13** | not started |
| Live ladder play | daybreak-cr `play.py --queue --losses N` (Null's); ClashAI vision+taps; Hasty-CR; RoyaleBot `rb-build 0.1.2` for Null's 16.635.1 | all author-documented | **ahead** on in-process control, behind on ladder orchestration |
| Ladder win rate | **ClashAI**: 14,060 trophies / 4,499 matches; Ranked **32.7% → 42.7%** over 150–629 games | author-published | — |

### The honest answer

**Nobody has published a bot that beats top players.** The best public Ranked
number is ~43%, i.e. it is still losing. FirstLight-Zero's 97.5% is an X-Bow
*mirror* result. Hasty-CR states it plainly: *"No trained policy has beaten a
human. One match played by a person found the pathing bug thirty million
training steps never did."* The goal is unclaimed, and the field sits at roughly
"loses narrowly to strong humans".

### The unlock we were missing

The community already ported **this exact client build to x86_64**, and the
artifacts are already in `.resource`:

| Thing | Value |
| --- | --- |
| Official x86_64 `libg.so` 16.635.1 | `.resource/Clashback/game/engine/libg.so`, sha `c4721bd9…fd6539`, BuildID `4b3bf061…` |
| Our ARM64 `libg.so` 16.635.1 | sha `0b580222…e4eaa`, BuildID `179ac4bd…`, `apk_version_code 160635001` |
| Probe port | `RoyaleFish/patches/probe_x86_64_16635.diff`: x86 hooks, `native_call_x86_64.cpp`, x86↔ARM `logical_{funcs,offsets,vtables}.inc`, resident 16-match engine, NDK bionic build |
| 16.635 catalogs | `RoyaleFish/patches/native_runner_16635.diff` + `RoyaleFish/data_16635/` (123 cards, 453 archetypes, 26 abilities) |
| 16.635 models | `RoyaleFish/models/B-general-v275.pt`, `A-hog26-v738.pt` (17,506,419 params each) |

Their tables and ours agree independently: their patch annotates
`0x10f76b0 /* arm 0x10a7230 */`, and our `offset-diff.json` resolves
`kGoldenKnightHop` to `0x010a7230` at **EXACT** confidence. Same client build,
two ports, same answer.

**What that buys:**

1. A **16.635.1 native x86_64 engine on this host** — no phone, no Android UI,
   16 matches per process — the target ruleset at training scale.
2. A **second relocation table** that closed our 37 AMBIGUOUS + 8 of 10 MISSING
   anchors (N2) without new reverse engineering — **done, §3.33**.
3. **Published 16.635 baselines** instead of our stale General, once the
   framework patch is in. Their model config differs (LSTM 768 vs our 512, 5 vs
   3 spatial res blocks, 16.635 catalogs), so it is a patch, not a drop-in.

### X-Bow 3.0 specifics

- `runs/deck-pool.json` holds **111 X-Bow decks, 2.25% of 443,381 uses**. The
  current meta list (`archers-ev1, knight-hero, tesla-ev1, skeletons,
  electro-spirit, fireball, the-log, x-bow`) is the top one at 2,983 uses.
- FirstLight-Zero plays the classic no-evo list (`XBow, Knight, Archers, Tesla,
  Skeletons, ESpirit, Fireball, Log`) and proves the archetype is learnable from
  **self-play alone**, without human data. It does not prove ladder strength.
- The transfer recipe is daybreak-cr's: one-deck specialist, PFSP league, focus
  + hard decks, an IL anchor (`bc_index`, `bc_coef 0.2`), 55% promotion gate.

### Consequence for the plan

Training on the A51 is now the weakest link, not the strongest. Reorder:
**x86 16.635 → adopt 16.635 catalogs/models → GPU eval volume → X-Bow
specialist → ladder.** The A51 keeps its job as the deploy/parity rig, which is
still the only public in-process ARM64 16.635.1 path.

---

## Adopt from the field — gap → source → action

Take the artifact where it is licensed and version-matched; take the **method**
where it is not. Effort: S < 1 day, M 2–5 days, L 1–2 weeks.

| Gap | Source (license) | Take exactly | Effort / viability | Step |
| --- | --- | --- | --- | --- |
| 16.635 catalogs & model config | RoyaleFish `data_16635/` + `native_runner_16635.diff` (Apache-2.0) | data root + framework patch; 123 cards, 453 archetypes, 26 abilities | S — mechanical, already validated upstream | 1.1 |
| Native 16.635.1 engine at scale | RoyaleFish `probe_x86_64_16635.diff` (Apache-2.0) + `Clashback/game/engine/libg.so` (license unknown, **game asset — local use only**) | x86 call path, `x86_hook.h`, `logical_*.inc`, resident multimatch | M — our tree is already 16.635.1 ARM | 1.2–1.3 |
| 37 AMBIGUOUS / 10 MISSING anchors | RoyaleFish `logical_{funcs,offsets,vtables}.inc` | **done** — `crosscheck_royalefish.py` + `royalefish-overrides.json`; 0 AMBIGUOUS left (§3.33) | S — free second opinion, `0x10a7230` agrees | 6 |
| Modern baselines | `B-general-v275`, `A-hog26-v738` (Apache-2.0) | load + run as eval opponents and as the starting point | S — after 1.1 | 1.1 / 4 |
| Training throughput | RoyaleFish `ever_server/collector/learner/packed/loader` (Apache-2.0) | fp16 with action-equality check, CUDA graphs at several batch sizes, central LSTM state table, sparse trajectories, row-packed updates, loader process | M — **C4 deliverable**; the GPU is the limiter now | C4 |
| Bulk-training backend | crforge `python/` env (Apache-2.0) | `VectorBattle(256, 8)` + tick-exact 16.635.3/8 core | **plan of record** — needs the C1 vectorized adapter; never the final gate | C0–C4 |
| Specialist / league recipe | daybreak-cr settings + `loop.py` (license unknown → **reimplement**) | PFSP, focus + hard-deck shares, `bc_index`/`bc_coef 0.2` anchor, 55% promotion gate, rollback loop | M | 4 / X-Bow |
| X-Bow 3.0 policy | FirstLight-Zero (Apache-2.0) | self-play recipe, feature/model shape, eval protocol; **checkpoints are 15.535.13 — retrain on 16.635, do not load** | M | after 5 |
| Evaluation discipline | ClashAI `RESULTS.md` (license unknown → method) | Wilson intervals, ≥150-game samples per gate, held-out decks, σ reported per comparison | S — pure protocol | 4 |
| Anti-self-deception | Hasty-CR (MIT) | parameter-perturbation probes: flag wins that depend on one card, tile or timing | S — good gate for crforge-trained policies | 4 |
| Ladder orchestration | daybreak-cr `play.py --queue --losses N`, RoyaleBot `rb-build 0.1.2` (MIT), ClashAI | queue/pending-elixir budget, receipt handling, anti-blind-retry | M — reference only, we control the client in-process | N5 |
| Snapshots / lookahead | Clashback `native_snapshots.py` (license unknown) | per-tick encode/load for MCTS or teacher search | L — deferred until the policy is strong | deferred |
| Remaining RE tooling | REA MCP (MIT) | Ghidra/IDA workflows for the leftover anchors | S — optional convenience | 6 |

**Do not take:** leaked clients or redistributed APKs/assets; `rb-build`
binaries as a dependency; any 15.535-labelled weights as 16.635 weights;
in-engine win rates as ladder evidence.

### Keep the A51 connected?

Yes — but as a **parity oracle**, not the training engine.

- It is the only ARM64 in-process 16.635.1 rig we have, and the x86 port needs a
  ground truth from the *same* client build (`160635001`).
- It does **not** need to be running. Leave it plugged in and authorised
  (`adb`, `svc power stayon usb`); launch engines on demand with
  `./launch_cluster_a51.sh --engines 1` for single-match parity, `--engines 4`
  only for capacity comparisons.
- Before starting the port, capture a **parity corpus** on the A51 (step 1.0
  below) so most of the x86 validation can run offline. That is the highest-value
  use of the phone this week.
- Keeping four engines hot 24/7 buys nothing and re-exposes the §3.28 idle
  control-socket wedge and thermal drift.

---

## Current state

| Item | State |
| --- | --- |
| A51 device | **attached** — `SM-A515U`, Android 13, `arm64-v8a`, serial `R58NA0QTHWV` |
| 16.635.1 client | `nullsroyale.rel.free`, `libg.so` SHA `0b580222…e4eaa`, content `16.635.93` |
| 16.635.1 probe | **`b80f2bdb…`** — RoyaleFish-corrected core table **plus the ARM 16.635 rich-vtable anchors**; 9/9 runner hooks install, no mismatch; evaluator now matches x86 (§3.36) |
| 16.635.1 APK | built (`builds/cr-ai-16.635.1.apk`, 841 MB) |
| Cluster APK | `builds/cr-engine-cluster-16.635.1-rf.apk` (`17893d1c…`) built **and installed**; 1 engine attested with the new probe; 4-engine cold-ready path unchanged |
| Probe port evidence | `docs/native/16.635.1/FINDINGS.md` — §3.26 ruleset binding, §3.27 evaluation, §3.28 N0 soak, §3.30 N3 throughput, §3.31 render-gate crash, §3.33 RoyaleFish cross-check, §3.34 corrected core table, §3.36 ARM observation parity |
| General checkpoint | ready, 12.68M params |
| IL_Replay | ready locally, 252,238 replays |
| V4 evaluator | working on 16.635.1; **use `--sample`** (argmax stands still) |
| N0 soak | **10/10 clean**, 0 problems, 93 abilities / 0 rejections, 0 crashes |
| General vs IL baseline | **General 2–0**, 0 rejected, 2/2 normal terminals |
| General vs Hog 2.6 #1 | **General 0–2**, 0 rejected, 2/2 normal terminals (Hog converts by tick 3601 in game 2) |
| General vs Hog 2.6 #2 | **General 0–2**, both in overtime (6120/6118), 0 rejected, 2/2 normal terminals (§3.29) |
| N3 A51 throughput | **4×8: 12.3–14.4 matches/s** at 600 ticks/RPC (a transport ceiling; **0.26** at the PPO 5-tick cadence); 4×16: 14.0–14.5; stable, no crashes (§3.30) |
| A51 → PPO wiring | `a51_engine_node.py` + `launch_ppo_a51.sh` landed; manifest accepted by the topology builder |
| A51 parity corpus | **re-captured on `b80f2bdb…`** — `runs/parity-a51/` + `manifest.json`; scripted seeds unchanged, evaluator now 3601/4794 like x86, capacity 1.918 matches/s, deterministic, 0 rejected (§3.36) |
| A51 node manifest | re-attested on `b80f2bdb…`: `runs/a51-node0/engine-node-ready.json`, 4 engines, `parallel_matches: 32`, digest `ce9d4f85…`; topologies rebuilt |
| PPO deck pool | built: `runs/deck-pool.json`, 7,089 decks / 443,381 uses |
| PPO topology | validated for 1 GPU / 6 vCPU (`worker-cpus 0,1,2,3`, `control-cpus 4`) |
| PPO trainer on this host | **CPU-blocked** (collector is CUDA/NCCL-only), but a **full update ran on a rented A6000** with 0 rejected actions (`runs/gpu-smoke-20261114/`); throughput is the open problem, not correctness |
| x86 PPO worker path | **working** — zlib is preloaded `RTLD_GLOBAL` in `crx_engine_host.py` (a `RTLD_LOCAL` preload made crx's stub return `Z_OK` with a zero-byte payload); the host now fails closed if the probe's zlib imports are unresolved (§3.37) |
| 16.635 remote training | **working on CPU** — engine specs carry `competitive_release` + `model_config`; `B-general-v275` collects through the worker with 0 UNKNOWN effects, 29 lane-decisions/s, 1 engine × 8 lanes (§3.37) |
| Learner-loop batching | **measured; not a win** — 250 ms + gate 1.0 coalesces **3.98–4.00 packets/forward, 321 → 160 forwards**, but the added wait makes it **slower than 2 ms** (271 s vs 288–331 s, §3.40). Keep 2 ms; session 8 then pipelined the loop instead of widening the window (§3.41) |
| Full-rate ACT collection | **working** — `delay_aware_legality` ported (decoder + env + expert, §3.39); gate 1.0 is clean on 5/5 single-engine seeds, the 4-engine shape, and a **10/10 stress gate** (4,097 native parses / 0 failures, 16 consecutive clean segments). The one post-port `env 6` did not recur; `CR_PPO_FAILURE_DUMP` stays armed |
| 16.635.1 x86_64 engine | **running on this host** — `.resource/Clashback/game/engine/libg.so` (sha `c4721bd9…`) booted headless by the crx ELF loader in 0.3 s; no Android/bionic bundle needed |
| 16.635.1 x86_64 probe | **built** — `native_runner/probe/out/x86_64/libcrprobe.so` (`cd303311…`), `--abi x86_64`; all runner hooks install, `attest` reports `production_ready: true` |
| x86_64 multi-engine host | **working** — `crx_engine_host.py --engine-index N` binds engine N to port `26789+N` and CPU N; two concurrent engines verified with one worker run (§3.38) |
| x86_64 parity corpus | **scripted seeds 1–5 and the General-vs-IL evaluator both reproduce `runs/parity-a51`** — identical ticks/actions on both seats after the A51 vtable fix (§3.36) |
| 16.635 catalogs | **adopted as a verified release** — `native_runner/data/competitive-16.635/` + `native_runner/releases/16.635.1.json`; all 5 catalog ids match `B-general-v275` |
| 16.635 models | **`B-general-v275` and `A-hog26-v738` load and play on the A51** (17.5M, LSTM 768, schema 21; 0 rejected in the play smoke) |
| Cloud GPU | **1× A6000 @ $0.35/h ran and was deleted** (~$0.23). Re-rent only for C4/C5 and promotion runs, after C1; the 2026-11-14 smoke sat at **4 %** utilisation |
| Decision-rate reality | native: A51 4×8 @ 5 ticks = **0.26 matches/s** (raw channel); one x86_64 process = **0.61** raw, **0.082** measured *training* floor; learner loop = **0.04–0.05**. crforge: **2.6 / 6.0 / 13.0** matches/s at 1/4/8 threads, 256 lanes, this laptop (2026-11-15 bench); 18.5 / 52.5 on an M4 Pro. Raw ceilings exclude observe + tensorize |
| **crforge bulk path** | **activated 2026-11-15** — `VectorBattle` is the bulk trainer after C0–C3; the native engine is the gate, the parity oracle and the deploy rig. **C0 closed 2026-11-16 (20/20 clean)**. The 10–30× is conditional on the C3 row build (≤0.5 ms/lane); a per-lane port is ~1.4× |
| **Learner deck** | **X-Bow 3.0, locked 2026-11-15** — `x-bow · tesla-ev1 · archers-ev1 · knight-hero · skeletons · electro-spirit · fireball · the-log`, Princess tower `159000000`. All cards/forms pass the 16.635 + crforge catalog check; **rollout-clean 2026-11-17** (2/2 games, `illegal 0`, `rejected 0`) after the Knight-hero global-id fix. Deck constants live in `tools/crforge/bridge_session.py` |
| **Substrate pins** | Clashback `002aa8c` / libg `c4721bd9…` (build `160635001`, local use only) = **the gate**; RoyaleFish `c42108d` (README-only since `da9c8cf`) = catalogs + `B-general-v275`/`A-hog26-v738` + `ever_*` (C4 recipe); crforge tables 16.635.8 + data lock. Record all four in every checkpoint artifact |
| Training observation | **lean — done (§3.43)** — non-validating `construct_contract` + `lean_contracts()` scope under `policy_features_only`, no provenance/freeze/validation, cached hand contract and form aliases, `(identity, lean)`-keyed observation caches. Paired A/B: **11.75 → 7.81 CPU ms/packet**, **78 → 113 lane-packets/s** on one x86 engine; 8 lanes 10.6/12.5 → 8.2/9.4 ms. Validated path and parity unchanged |
| Loop pipelining | **done, CPU-verified** — one `ppo-inference` thread, one batch in flight, receives staged during the forward (§3.41). Non-model wall −14 % with a cheap forward, neutral with the 17.5M model (forward = 98 % of wall). 10/10 segments clean. CUDA path unverified until a GPU run |
| Training-floor gates | **superseded by §3.43** — the remaining packet is ~3 ms native round trip + rich binding + placement, not observation contracts. The 0.45/2.5 gates assumed raw-channel ceilings that skip ~90 % of a real packet (§3.41); the observation half of that gap is now closed |
| cr-native proxy | working, 15.535.29 only |
| crforge simulator | `.resource/crforge/` — 16.635.3/8 tables, `VectorBattle` 1–256 lanes, zero-copy NumPy; core 127k ticks/s/thread. Bench: `PYTHONPATH=.resource/crforge/python .venv/bin/python -m crforge_env.bench …` |
| crforge bridge | `crforge_bridge/` — 152/152 cards, 118 forms, `unmapped: []`; evaluation only (20–30 decisions/s with full `BattleEnvV1` assembly) |
| crforge resident adapter | **`native_runner/crforge_resident.py`** — `CrforgeResidentEnv` presents the whole `NativeClashEnv` surface over an in-process `Engine` (bridge `dispatch()`, no socket); `status()` reports `resident: true` / `lanes: 1` |
| C2 parity + throughput | **parity pass, throughput missed** — `tools/crforge/resident_parity.py`: **60/60 decisions identical** (native/rich/atomic envelopes, masks, rewards, terminals); paired `env.step` **1.21–1.53×** the bridge (resident 3.9–4.8 ms, bridge 4.2–7.3 ms). The wire is 1.2–1.7 ms of a 4–6 ms decision; the 2× gate is C3. **Learner deck 2026-11-17: 60/60 identical, paired 1.29×** (`runs/crforge-c2/learner-parity.json`) |
| crforge engine cost | **two shared fixes (2026-11-16)** — run-refusal header reads windowed to `{runTick, runTick+1}` instead of both seats every tick, and one row decode per capture (memoized by generation/tick/command sequence). Both bit-identical to the bridge |
| C0 legality soak | **20/20 clean, 2026-11-16** — `runs/crforge-c0-20261116/soak20-rerun/`: illegal 0, truncated 0, rejected 0, all `king_tower_destroyed` (seeds 7–26) |
| C1 encoder spike | **measured at the default shape** — `runs/crforge-c1/encoder-spike.json`, 256 lanes × 200 × 3: vectorized facts **0.0165–0.0177 ms/lane** (56–61k decisions/s) vs per-lane 0.32–0.35 (**19–20×**), equivalence pass; projected end-to-end **0.713 ms/lane = 1.16 matches/s per process** (**C3 gate met**) |
| C3 tensorizer profile | **measured, 2026-11-16** — `runs/crforge-c3/tensorizer-profile.json`; `tensorize`+`record_action` **0.992 CPU ms/decision** (stock 1.069), entities median 8, **3,754 Python calls/decision**, ~35 % of profiled time in builtin calls plus the frozen-mapping layer (`FINDINGS.md` §3.44) |
| Learner-deck rollout | **clean 2026-11-17** — `runs/crforge-c3/learner-deck/`: 2/2 games, `illegal 0`, `rejected 0`, terminals at ticks 3,681 / 4,032. The tensorizer failure was the Knight hero's signed LogicData global id, not an effect row (`FINDINGS.md` §3.45) |
| Run-refusal naming | **fixed 2026-11-17** — `H_LAST_RUN_REFUSAL` is a `LadderMatch` code (4 = `OVER`), not a `PlayerCommands.Refusal` ordinal; queued plays refused with `OVER` now resolve as `action_canceled_terminal` (`FINDINGS.md` §3.45) |
| `tools/crforge/profile_tensorizer.py` | **new** — replays a live policy episode's tensorizer half under cProfile; capture digest + per-decision `batch_digest` prove an A/B is bit-identical |
| Vectorized observation facts | **new 2026-11-18, extended 2026-11-19** — `crforge_bridge/vector_observation.py` fills a fixed-size batch from `VectorBattle` for all lanes: both seats' headers/cards, all six towers, placement-mask views, champion ability slots (command ticks = a waiting ability), derived `age_ms`/`velocity`. **0.0317 ms/lane** (31,555 decisions/s) at 256 lanes/1 thread, **4.8×** the bridge's per-lane decoders, equivalence pass (`runs/crforge-c3/vector-observation.json`, `FINDINGS.md` §3.46). It is the precondition for C3-B, not the row build |
| Batched group row build | **new 2026-11-19** — `crforge_bridge/vector_rows.py` builds `_groups`+children for all lanes from the facts layer; `VectorEntityTables` compiles card/archetype/child/extent/runtime-form semantics once. **Equivalence pass** over 40 decisions (256 lanes step 0, 32 after), `mismatches: []`; **0.0539 ms/lane** vs 0.2207 per-lane oracle (**4.09×**) at 256 lanes, max 14 entities/lane (`runs/crforge-c3/batched-groups.json`, `FINDINGS.md` §3.47). Section only — the rest of C3-B is open |
| Batched card row build | **new 2026-11-19** — `VectorCardBuilder` fills the actor's eight-row set and the opponent's nine-row set for all lanes, including the **21-column** pending-execution schema (`pending_execution`, target cell with the even-footprint offset, ability pending; tick column neutralised by the checkpoint). **0.00161 ms/lane** vs 0.0718 oracle (**44.5×**), equivalence pass, 4 pending rows max (`runs/crforge-c3/batched-cards.json`, `FINDINGS.md` §3.48) |
| Batched relation row build | **new 2026-11-19** — `VectorRelationBuilder` fills target/reverse-target and card→group edges; tower sources/targets resolve through `VectorTowerSection.tower_index` (the model row, not the native id — the seat-1 fix); `VectorRelationSection.lane(n)` slices back to the per-lane `(1, count)` record. **0.00984 ms/lane** vs 0.0522 oracle (**5.30×**), equivalence pass, 40 edges max (`FINDINGS.md` §3.48, §3.55) |
| Batched candidate row build | **new 2026-11-19** — `VectorCandidateBuilder` fills one row per legal hand slot: card/form/cost, own-card row, the native 32×18 placement grid, building half-footprint + `model_subcell_offset`, UID; `CandidateGeometry` compiles the card-static placement facts once. Slot rule is `BattleEnvV1`'s (in hand, not queued, affordable, ≥1 legal tile), not crforge's tile block. **0.00086 ms/lane** vs 0.0531 oracle (**61×**), equivalence pass over 40 decisions, 5,181 queued slots and 1,171 building rows exercised (`runs/crforge-c3/batched-candidates.json`, `FINDINGS.md` §3.49) |
| Batched tower row build | **new 2026-11-19** — `VectorTowerBuilder` fills the six-row tower section per lane: actor-first/model-kind/native-id order, tile position, kind-sized extent, HP ratio, active and visible-target flags; the model view swaps the princess kinds under a flip. `VectorTowerSection.tower_index` is the `_relations` tower-token map. Pins: `status` empty, `tower_troop_id` static (`159000000`). **0.00104 ms/lane** vs 0.0349 oracle (**33.5×**), equivalence pass over 40 decisions, 61,440 active rows and 61,263 targeted rows (`runs/crforge-c3/batched-towers.json`, `FINDINGS.md` §3.50) |
| Batched spatial planes | **new 2026-11-19** — `VectorSpatialPlaneBuilder` fills the 14-channel board per lane: per-side ground/airborne/building/projectile occupancy, hitpoints, shield and threat; a building fills its footprint, the health/threat channels always mark the entity's cell, and towers/ownerless rows are skipped. Archetype `is_airborne` / `static_damage_basis` / persistent-root facts are compiled into `VectorEntityTables`. Float32 addends preserve the per-lane loop's sequential rounding. **0.00392 ms/lane** vs 0.0499 oracle (**12.7×**), equivalence pass over 40 decisions, 35 building frames (`runs/crforge-c3/batched-spatial-planes.json`, `FINDINGS.md` §3.51) |
| Batched match scalars | **new 2026-11-19** — `VectorScalarBuilder` fills the 18 match scalars per lane: remaining clock, phase and elixir multiplier from the compiled mode timeline, own/enemy elixir and crowns, visible-entity count, group/child/candidate/event ratios, reserved elixir and the static actor identity; no tracker (public `[0,10]` bound) and no events. `_PlayerView` gained `crowns`/`elixir_exact`, `_ObservationView` gained `time`/`phase`/`metadata`. **0.000286 ms/lane** vs 0.00379 oracle (**13.2×**), equivalence pass over 40 decisions (`runs/crforge-c3/batched-scalars.json`, `FINDINGS.md` §3.52) |
| Batched empty records | **new 2026-11-19** — `VectorEmptySectionBuilder` widens the stock zero-row `ActiveEffectSetV4` and masked `EventSetV4` to the lane count; the event padding keeps `owner_type` 2 and group/token `-1`, the sentinels the tensorizer validates. **0.000141 ms/lane** vs 0.0202 oracle (**143×**), equivalence pass over 40 decisions (`runs/crforge-c3/batched-empty-sections.json`, `FINDINGS.md` §3.53) |
| Batched-candidate gate | **new 2026-11-19** — `tools/crforge/batched_candidates.py`; steps 256 real learner-deck lanes past the start cooldown, validates `ActionCandidatesV4` every step, compares every candidate tensor against the stock `_card_sets` → `_groups` → `_candidates` chain, then times both paired |
| Batched-tower gate | **new 2026-11-19** — `tools/crforge/batched_towers.py`; 256 learner-deck lanes, `TowerSetV4` validation and full section equivalence every step, and the two fidelity pins recorded on the artifact |
| Batched-spatial-plane gate | **new 2026-11-19** — `tools/crforge/batched_spatial_planes.py`; 256 learner-deck lanes, full-plane equivalence every step with the per-frame caches cleared per lane exactly as `tensorize` does, paired timing, building/ground cell coverage counters |
| Batched-scalar gate | **new 2026-11-19** — `tools/crforge/batched_scalars.py`; 256 learner-deck lanes, the stock `_card_sets` → `_groups` → `_candidates` chain supplies the oracle's own overflow/candidate counts, phases seen recorded honestly, paired timing |
| Batched-empty gate | **new 2026-11-19** — `tools/crforge/batched_empty_sections.py`; 256 learner-deck lanes, both empty records validated and compared field for field every step, paired timing |
| Batched-group gate | **new 2026-11-19** — `tools/crforge/batched_groups.py`; steps real learner-deck vector lanes, compares every group/child tensor against the stock `_groups` oracle on facts-projected `EntityStateV1` rows, then times both paired over all 256 lanes |
| Batched-card gate | **new 2026-11-19** — `tools/crforge/batched_cards.py`; reads the checkpoint's own `model_config` (21 card columns for `B-general-v275`), compares card and relation tensors against the stock sections every step, then times both paired. This is the gate that proves the 16.x schema, not the default 16-column one |
| Batched-row tests | `crforge_bridge/tests/test_vector_rows.py` (26 tests): 50-decision group equivalence, forced overflow, empty-board masks, reference-entity projection, seat-0 guard, 21-column card equivalence, relation equivalence, pending-cell mirror rule, waiting-ability column, tracker guard, 50-decision candidate equivalence with record validation, the candidate slot rule (pending / unaffordable / empty tile block), the 2×2 vs 3×3 building geometry, the Mirror refusal, tower equivalence, the tower model-view flip, the static tower-troop pin, destroyed/absent tower rows, spatial-plane equivalence with a forced Tesla, the archetype projection for airborne/threat, towers-only/neutral rows, scalar equivalence, the standard phase schedule, the tracker refusal, empty effect/event equivalence with record validation, the empty-section seat guard. Bridge suite **48 tests**, all pass |

### State at handoff (processes are ephemeral)

No long-lived probe endpoint is needed. Both oracles start on demand:

```bash
# x86_64 engine + probe on this host (port 26789)
.venv/bin/python tools/native_analysis/crx_engine_host.py --seconds 1800
# independent host engines: 26789+N and control cpu N
for i in 0 1 2 3; do
  .venv/bin/python tools/native_analysis/crx_engine_host.py \
      --engine-index "$i" --seconds 1800 &
done
# A51 ARM64 oracle (phone attached; engine 0 fullscreen)
./launch_cluster_a51.sh --engines 1 --window-mode plain --base-port 26799
```

The x86 host boots in ~0.3 s and can be restarted at will. The A51 waits for
`adb`; re-run the launcher if the phone slept. The parity gate is reproducible
without the phone:

```bash
.venv/bin/python tools/native_analysis/parity_manifest.py \
    --corpus runs/parity-x86 --compare runs/parity-a51/manifest.json \
    --output runs/parity-x86/manifest.json   # exit 0 / "reproduces: True"
```

`runs/parity-a51/` is the archived ARM corpus, `runs/parity-x86/` the matching
x86_64 candidate; both are gitignored but local. The reproducible tools are
`crx_engine_host.py`, `scripted_xbow_match.py`,
`resident_batch_throughput.py`, `multi_decision_match.py`,
`parity_manifest.py` and `tests/test_parity_manifest.py`.

The 4-engine A51 cluster used for re-attestation was stopped at the end of the
session. To resume GPU prep:

```bash
./launch_cluster_a51.sh --engines 4
.venv/bin/python tools/native_analysis/a51_engine_node.py \
    --serial R58NA0QTHWV --engines 4 --output runs/a51-node0/engine-node-ready.json
.venv/bin/python -m native_runner.training.v4.build_ppo_multinode_topology \
    --node-ready runs/a51-node0/engine-node-ready.json \
    --output runs/topology-a6000-1gpu.json --trainer-job-id 1 \
    --trainer-host "$(hostname -s)" --gpus 1 \
    --worker-cpus 0,1,2,3 --control-cpus 4
```

The 4-engine attestation digest is `ce9d4f85…`; the 2-GPU topology needs
`--control-cpus 4,5` (one control CPU per rank).

### N3 — the windowing bug that capped throughput

> **Unit caveat (2026-11-15):** the 12–14 matches/s below is a **600-tick
> transport ceiling**, not a PPO training rate. At the 5-tick cadence the same
> 4×8 A51 shape is **0.26 matches/s**, and its learner loop **0.04–0.05**. It is
> kept as evidence for the cpuset fix, not as a throughput claim.

Every engine except the resumed activity lands in Android's `background`
cpuset, which is pinned to **CPUs 0–1** (little cores). The default
`launch_cluster_a51.sh` therefore gave one fast lane and one ~3x slower lane.
Keeping all engines visible fixes it:

- engine 0 fullscreen, engine 1 split-screen-secondary, engines 2+ freeform
  (plus `force_resizable_activities` / freeform settings);
- big-core CPU map `4,5,6,7`;
- `cached_apps_freezer disabled` (the §3.28 idle control-socket wedge).

`launch_cluster_a51.sh --window-mode visible` (default) does this and reports
each engine's schedtune cgroup. One A51 now supplies ~12–14 matches/s at the
PPO shape (4 engines × 8 lanes) and ~14.5 at 4×16 — well above host CPU
inference (~1 match/s), so **no cloud engine cluster is needed**.


### 16.635.1 port status (verified on the A51)

From `docs/native/16.635.1/FINDINGS.md`:

- Touch JNI hook is **port-ready** (`0x0156f790`).
- Hand/deck model offsets and the replay-controller trio are recovered.
- The probe is build-selectable for 16.635.1; required anchors fail closed.
- Relocation: **53 EXACT, 23 LIKELY, 70 MANUAL, 0 AMBIGUOUS, 2 MISSING**
  (2026-11-14 cross-check with RoyaleFish, §3.33). The two remaining rows are
  `kTranslatedSha256InitializerCall` and `kCombatHealEffectReturn`.
- **Cold configure is open**: the content-ready byte is `0x720` (§3.13), and a
  fresh `MatchConfig` reaches `ready/matchReady=true`.
- **Live command injection works**: two-sided card plays, abilities, entity/HP
  observation and the scripted X-Bow policy all run with 0 rejections
  (§3.14–§3.22).
- **Rich telemetry is mixed**: object/phase/ability/HP streams are live, but the
  native hook rings (combat events, phase/special-movement/action-movement,
  character state, visibility, remaining, tower troops) report `unavailable` on
  this build. `observe_rich` still validates because those streams fail closed.
- The V4 evaluator now binds the live 16.635.1 identity explicitly
  (`native_runner/versions/native-16.635.1.json`, §3.26); the local card catalog
  remains the pinned 15.535.86 release, with the level-11 parity caveat.

This is finite reverse-engineering work with existing tools, not a missing model
or dataset. The relocation table is now closed (0 AMBIGUOUS, 2 genuinely
missing — §3.33/§3.34); the remaining N2 items are installing the rich hook
rings from the recovered 16.635 offsets and fixing the render gate. They do not
block N1 evaluation or a CPU baseline.

---

## Session status — N0, N1 and N3 passed

Done on the attached `SM-A515U` (`R58NA0QTHWV`):

1. **Engine bring-up.** Two engines cold-ready for N0/N1
   (`nullsroyale.rel.free:engine0/1`, ports `26789`/`26790`), later four for
   N3 (`26789`–`26792`), identity verified against the 16.635.1 pin.
   Operational: keep `adb shell svc power stayon usb` on. After long idle a
   background engine's control socket can stop answering while the process is
   still alive (seen on `:engine0`); `launch_cluster_a51.sh` force-stops and
   relaunches both engines, which restores it. The launcher now also disables
   `cached_apps_freezer`, which is the likely cause.
2. **Scripted soak.** 10/10 matches, 0 problems, 93 abilities with 0 rejections,
   empty crash buffer (`FINDINGS.md` §3.28).
3. **`observe_rich`.** Validates; object/HP/phase/ability streams live, native
   hook rings `unavailable` as expected (§3.26/§3.27).
4. **N1 baseline set.** General **2–0 over IL**, **0–2 to Hog26-specialist1**,
   both seats, sampled, 0 rejected actions, all normal terminals (§3.27,
   §3.29). General's volume is healthy; the Hog specialist converts earlier.
5. **N3 throughput.** Four engines on big cores in visible windows give
   **12.3 matches/s** at 4×8 lanes and **14.0–14.4** at 4×16, stable over
   eight runs, no crashes. A backgrounded engine is pinned to CPUs 0–1 and
   loses ~3x; `--window-mode visible` fixes it (§3.30).
6. **A51 → PPO wiring.** `tools/native_analysis/a51_engine_node.py` attests the
   cluster and writes a `v4-ppo-engine-node.v1` manifest; `launch_ppo_a51.sh`
   launches the cluster, attests it and starts `remote_ppo_worker_server`. The
   manifest builds a valid `v4-ppo-multinode-topology.v1` topology.
7. **`offline_agent.py` / `offline_duel.py`** now bind the live 16.635.1
   ruleset and honour `combat_ring_hooks: false` (they no longer request the
   combat-ring feed). **They still crash on the native-render path**: the probe
   SIGSEGVs in `cr_render_gate_swap_buffers+996` (§3.31). Use the headless
   evaluator; fixing the render gate is an N2 item.

**Blocker for a local PPO smoke:** the production collector is CUDA-graph-only
(`_persistent_rollout_graph_v4` raises when a graph is unavailable; the trainer
uses NCCL and `torch.cuda.*` unconditionally). There is no CPU path, so the
first real PPO segment/update needs the rented GPU. The A51 node plumbing is
ready for it.

## GPU smoke 2026-11-14 — results and what they mean

Run: ThunderCompute 1× RTX A6000 (48 GB), 6 vCPU, 48 GB RAM, driver 610,
`torch 2.5.1+cu121` in a venv; A51 over a reverse SSH tunnel on `29900`
(`-R 127.0.0.1:29900:127.0.0.1:29900`). Evidence: `runs/gpu-smoke-20261114/`
(`metrics.jsonl`, `config.json`, `ppo-smoke.log`).

| Gate | Result |
| --- | --- |
| Trainer ↔ worker ↔ engine wiring | **pass** — 4 engines, 32 lanes, tunnel transparent |
| `native_rejected_actions` | **0** |
| Checkpoint | `update-00000001.pt`, 145,702,430 B, sha `9566a110…02654` |
| Optimizer / update step | written at a strict boundary (resume contract saved) |
| GPU peak allocation | **6.33 GiB** (not the <2 GB estimated; still trivial for 48 GB) |
| GPU utilisation during collection | **4 %** — the engine feeds it |
| All 32 matches reaching terminal | **no** — 32/32 truncated by the segment budget |
| `eligible_act_rate` | **0.0** — 5,120 eligible ticks, all `WAIT` |
| Wall time for one 40 s segment | 266 s collection + 11 s update |
| Cost | ~$0.23 for the whole session |

### Two configuration bugs, both fixed

1. **Gate temperature.** `DEFAULT_GATE_TEMPERATURE_START = 0.20` is flat and
   sharpens an already-passive gate (General takes `ACT` ≈ 6 % at 5 ticks).
   Result: 0 actions in 5,120 eligible ticks, i.e. no policy signal at all.
   The launcher now honours `CR_PPO_GATE_TEMPERATURE_{START,END,ANNEAL_UPDATES}`;
   collect at `1.0` and anneal to `0.2`.
2. **Deck pool.** The trainer requires **exactly 7,695 decks / 20 focus decks**.
   The local corpus yields 7,089 at `--minimum-uses 5` and 8,530 at 4 — the
   upstream number is unreachable locally. Added `--allow-unverified-deck-pool`
   (still requires ≥5,000 decks + exactly 20 focus decks) and
   `native_runner/training/v4/select_focus_decks.py`, which derives a
   deterministic focus list from observed usage.

Also fixed: `CR_PPO_SEED` defaults to 0, which trips the positivity check — set 1.

### Why the A51 cannot train this policy

The resident channel costs ~150 ms per framed request regardless of payload, and a
PPO decision is only 5 ticks. Measured with `resident_batch_throughput.py`:

| Engine | Config | Capacity | Notes |
| --- | --- | --- | --- |
| 4× A51 | 8 slots, 5 ticks | **0.26 matches/s** | 0.042 / 0.040 / 0.092 / 0.085 per lane; makespan 0.161 |
| 4× A51 | 8 slots, 600 ticks (old §3.30) | 12–14 matches/s | amortises the same fixed cost 120× |
| 1× x86_64 | 8 slots, 5 ticks | **0.61 matches/s** | 296 actions/s |
| 1× x86_64 | 16 slots, 5 ticks | **0.616 matches/s** | 299 actions/s, CPU-bound at ~3,700 slot-ticks/s |

So the §3.30 number was a coarse-tick transport ceiling, not a training rate.
Inside the learner loop the A51 is worse still: **19 lane-decisions/s**, i.e. a
21× gap on top of the 0.26 ceiling. Per-iteration accounting for one packet:
~416 ms wall, of which policy forward is 27 ms and assembly 4 ms; the rest is the
closed loop (engine round + worker decode + action application). The receive
executor already runs one thread per engine, so parallel reception is not the
problem; the loop still resolves **one packet per forward**
(`inference_mean_frame_packets: 1.0`, batch ≈16 rows, `_INFERENCE_BATCH_WAIT_SECONDS = 0.002`).

### What to do next (CPU-only first — no GPU spend)

> **Historical (2026-11-14).** Items 1–4 below are done; item 3's 250 ms window
> was later measured as a net loss (§3.40) and the loop was pipelined instead.
> The live next-session plan is the session-13 section at the top of this file.

**Done 2026-11-14 (session 3):** the CPU profiling harness exists and the real
loop runs without a GPU. See "Learner-loop profiling" below for numbers and for
the three new blockers it exposed.

**Done 2026-11-14 (session 4):** all three blockers are closed (`FINDINGS.md`
§3.37). The x86 engine is now a *training* substrate, not just a control/eval
oracle: `profile_real_collection.py --checkpoint B-general-v275.pt` collects a
48-decision, 8-lane segment through the CPU PPO worker with 0 UNKNOWN effects,
0 fallbacks, and the 16.635 catalogs/21-column schema in place.

1. **Profile the learner loop without a GPU — done.** Transport floor and the
   real loop are both measured; remaining cost is the CPU forward.
2. **Make the x86 engine the training substrate — done.** `B-general-v275`
   collects on one local engine. The official x86_64 build is content 16.635.8,
   so it trains **16.635-contract** checkpoints (B-general/A-hog26); a
   15.535-contract checkpoint still needs the A51 or an adaptation step.
3. **Learner-loop batching — done (`FINDINGS.md` §3.38).** The window must be
   about one engine round-trip, not 20–40 ms. With four A51 engines and 8 lanes,
   `CR_PPO_INFERENCE_BATCH_WAIT_MS=250` gives **3.88 packets/forward and 33
   forwards instead of 64**; 2/30/100 ms stay at 2.0, and 150 ms only reaches
   2.56. CPU wall time is flat (model-bound), so the win is per-forward fixed
   cost — the thing the GPU paid while idle. Use 250 ms for the GPU smoke and
   re-measure if engine count or lanes change. Multi-engine x86 now works:
   `crx_engine_host.py --engine-index N` → port `26789+N`, CPU N.
4. **Delay-aware candidate legality — done (`FINDINGS.md` §3.39).** The
   decoder, environment action mask and compound validator now check elixir at
   the touch delay bin with the match multiplier, matching the 16.635 engine.
   Gate temperature 1.0 is clean: 5/5 single-engine seed segments, the 4-engine
   shape at 28.5 lane-decisions/s, 3,979 native command parses and 0 failures.
   One failure seen immediately after the port did not reproduce in six later
   segments; `CR_PPO_FAILURE_DUMP` now records the actions if it returns, and
   the probe's `CRNativeProbe` parse log separates injection from capture.
5. **Re-measure before renting.** Rent only when a local segment completes in
   <10 min; the GPU is idle 96 % of the time at the current feed rate.

### Learner-loop profiling — 2026-11-14 (session 3)

Two tools, both CPU-only and both committed:

| Tool | What it measures |
| --- | --- |
| `tools/native_analysis/profile_cluster_collection.py` | transport floor — speaks the worker protocol, answers every frame with a scripted all-WAIT batch; no model, no storage |
| `tools/native_analysis/profile_real_collection.py` | the real `collect_async_cluster_wave_v4` loop on CPU (model + tensorizers + rollout storage) |

`async_cluster_self_play.py` now falls back to `_EagerRolloutSamplerV4` on a
non-CUDA device instead of requiring a CUDA graph, so the collector runs locally.
Covered by `native_runner/tests/test_training_v4_cpu_sampler.py`.

| Measurement | Packets/s | Per packet | Notes |
| --- | --- | --- | --- |
| Scripted, 1× A51, 8 lanes | **4.25** | 235 ms | transport + worker only |
| Scripted, 4× A51, 8 lanes | **10.6** | 94 ms | 2.66/engine, not 4 — already serialising |
| Scripted, 4× A51 + 30 ms model | **9.75** | 103 ms | model cost is *not* the problem |
| Scripted, 1× x86_64, 8 lanes | **13.3** | 75 ms | 3× the A51 per decision |
| Real loop, 1× A51, CPU model | **1.68** | 596 ms | model 256 ms/packet on CPU |
| Real loop, 4× A51, CPU model | **1.54** | 649 ms | trainer-bound: `ready_wait` is only 22 % |
| **Real loop, 4× A51, A6000 (the paid run)** | **2.4** | **416 ms** | `ready_wait` 91 %, GPU 4 % |

Subtracting model time, the non-model cost is **~340 ms/packet on CPU and ~390 ms
on the GPU box** — the same number, so the CPU harness reproduces the paid run's
bottleneck. A scripted reply costs 235 ms/packet, so ~100–150 ms is loop
bookkeeping on top of the engine.

**Diagnosis:** the loop keeps exactly one packet in flight per engine and
replies serially, so per-engine latency is paid end to end. `ready_wait` grows
with engine count and `_INFERENCE_BATCH_WAIT_SECONDS = 0.002` never lets ready
packets coalesce at engine speed. With four engines the trainer is the serial
resource; the GPU is idle while it waits.

### Three new blockers found while profiling

1. **The x86 worker path is broken — FIXED 2026-11-14 session 4
   (`FINDINGS.md` §3.37).** `profile_cluster_collection.py` against the local
   x86 engine failed on the first batch with `Error -5 while decompressing data:
   incomplete or truncated stream`. It was not the engine: crx resolves probe
   imports through `dlsym(RTLD_DEFAULT)`, and `crx_engine_host.py` had preloaded
   `libz.so.1` **`RTLD_LOCAL`**. crx's missing-symbol stub then returned
   `0 == Z_OK` for `compress2`, so every "compressed" batch entry shipped as a
   zero-byte zlib payload. Fixed by preloading zlib `RTLD_GLOBAL`; the host now
   also refuses to attach the probe if `compress2`/`compressBound` are
   unresolved. Probe hashes are unchanged.
2. **The worker was hard-wired to the 16-column schema — FIXED.** `build_episode_tensorizers_v4` was called without a model config, so a 16.635
   checkpoint failed with `mat1 and mat2 shapes cannot be multiplied (128x16 and
   21x128)`. `AsyncResidentEngineSpecV4` now carries `competitive_release` and
   `model_config`, derived in `collect_async_cluster_wave_v4` from the actor
   model so a remote worker cannot disagree with it. Mixed anchor/history
   releases are rejected at startup.
3. **The 15.535 catalogs cannot tensorize some 16.635 entities — FIXED for
   16.635-contract checkpoints.** `active public effect resolved to UNKNOWN:
   buff:…` on the 16.635.8 x86 engine. The worker now binds the checkpoint's
   release (`use_release`) around engine construction and reset, because
   `BattleEnvV1` re-verifies the semantic subset against the *active* release's
   form evidence. `B-general-v275` now collects with 0 UNKNOWN effects. A
   15.535-contract checkpoint (General) still fails on 16.635 effect ids — that
   is correct and means the x86 substrate needs a 16.635 checkpoint.

---

## Next session — start here

> **Historical (2026-11-14).** Steps 1–5 in this section are done; the live
> next-session plan is the **session-13** section at the top of this file
> (C0 legality, then the C1 encoder spike). Kept for the runbooks and reference
> numbers.

### 0. Already done at this handoff (no compute spent)

| Step | Result |
| --- | --- |
| **RoyaleFish cross-check (§3.33)** | **done** — 145 named x86→ARM rows compared; **0 AMBIGUOUS** left (was 33), 8 of 10 MISSING recovered; 7 core corrections, 56 adopted overrides |
| **Corrected core table on device (§3.34)** | **done** — probe `db9d5609…`, APK `9e345ad3…`; 9/9 hooks install, scripted seeds 1–2 identical to §3.32, 0 rejected, 0 crashes |
| **ARM observation parity (§3.36)** | **done** — probe `b80f2bdb…`, APK `17893d1c…`; 16.635 rich vtables emitted for ARM; General-vs-IL evaluator now **3601 / 4794** on both seats, identical to x86; `runs/parity-a51` re-captured and re-pinned |
| Hog26-2 result | **0–2**, both overtime (6120/6118), 0 rejected — in `FINDINGS.md` §3.29 |
| **A51 parity corpus (1.0)** | **done** — `runs/parity-a51/` + `manifest.json`; seeds 1–5 + seed-1 repeat, General 2–0 IL (`--sample`), 1×8 capacity; deterministic, 0 rejected — `FINDINGS.md` §3.32 |
| Deck pool | `runs/deck-pool.json` — **7,089 decks**, 443,381 player uses from 252,238 replays |
| A51 cluster | 4 engines launched and attested on `26789`–`26792` this session; **stopped at handoff** (idle phone, launch on demand) |
| Node manifest | `runs/a51-node0/engine-node-ready.json`, probe `b80f2bdb…`, `parallel_matches: 32`, digest `ce9d4f85…`; topologies rebuilt |
| Worker service | `remote_ppo_worker_server` on `127.0.0.1:29900` |
| Topology (1 GPU / 6 vCPU) | builds and validates: `runs/topology-a6000-1gpu.json` |
| Topology (2 GPU / 6 vCPU) | builds and validates: `runs/topology-a6000-2gpu.json` |

All of it is CPU/phone work. Build the deck pool and IL cache **before** the
meter starts.

### 1. x86_64 16.635.1 — **done** (see §3.35 and §3.36)

The engine runs on this host through the crx ELF loader; the probe builds with
`--abi x86_64`; scripted X-Bow seeds 1–5 **and** the General-vs-IL evaluator
reproduce `runs/parity-a51` decision-for-decision. Reproduce with:

```bash
.venv/bin/python tools/native_analysis/crx_engine_host.py --seconds 1800 &
.venv/bin/python tools/native_analysis/scripted_xbow_match.py --seed 1 \
    --output runs/parity-x86/x86-seed1.json
CR_PROBE=native_runner/probe/out/x86_64/libcrprobe.so CR_NATIVE_BUILD_ABI=x86_64 \
  .venv/bin/python tools/native_analysis/parity_manifest.py \
    --corpus runs/parity-x86 --compare runs/parity-a51/manifest.json \
    --output runs/parity-x86/manifest.json
# evaluator gate: must print the same 3601 / 4794 rows as runs/parity-a51
CR_PROBE=native_runner/probe/out/x86_64/libcrprobe.so CR_NATIVE_BUILD_ABI=x86_64 \
  .venv/bin/python -m native_runner.training.v4.evaluate \
    --checkpoint-a checkpoints/General/checkpoint-step-00000460.pt \
    --checkpoint-b checkpoints/IL/checkpoint-step-00029396.pt \
    --games 2 --sample --seed 7 --port 26789 \
    --output runs/parity-x86/x86-model-general-vs-il.json
```

**RESOLVED — evaluator parity (§3.36).** The root cause was the one-sided
vtable fix described in §3.35: on ARM the rich-classification constants were
still gated to x86_64, so the probe fell back to the 15.535 literals
(`entities[].entity_kind`, `projectile_state`, `runtime_provenance`). The ARM
16.635 header now emits the nine `offset-diff.json` vtable anchors, the probe
selects them on both ABIs, and the A51 runs probe `b80f2bdb…` / cluster APK
`17893d1c…`.

General-vs-IL, seed 7, both seats, `--sample`: game 1 **tick 3601, actions
25/27**, game 2 **tick 4794, actions 40/37** — identical to x86_64. Scripted
seeds 1–5 are unchanged; `runs/parity-a51` was re-captured and the ARM pin now
records `b80f2bdb…`; `out/libcrprobe.so` was refreshed to match. Do not
resurrect option B (mirroring the stale table on x86).

1. **1.0 — Parity corpus from the A51 — DONE (`FINDINGS.md` §3.32).**
   `runs/parity-a51/` holds seeds 1–5, a seed-1 repeat (decision-identical
   after dropping latency), two `evaluate.py --sample` games (General 2–0 IL)
   and one `resident_batch_throughput.py` run (2.07 matches/s, 1×8).
   `runs/parity-a51/manifest.json` archives the probe/checkpoint/APK hashes and
   the determinism check. The phone may now idle; re-capture only if the probe
   or client changes. The x86 port must reproduce the corpus:

   ```bash
   .venv/bin/python tools/native_analysis/parity_manifest.py \
       --corpus <x86-corpus> --compare runs/parity-a51/manifest.json \
       --output <x86-corpus>/manifest.json   # exit 0 == gate passed
   ```
2. **1.1 — 16.635 catalogs adopted; `B-general-v275` loads and plays — DONE.**
   Do **not** apply `RoyaleFish/patches/native_runner_16635.diff`: it is based on
   a different 15.535 tree and gets 16.635 data through by disabling SHA checks.
   Instead, the 16.635.1 catalogs are now a first-class, verified release:

   - descriptor `native_runner/releases/16.635.1.json`, data
     `native_runner/data/competitive-16.635/` (1.5 MB);
   - `native_runner/competitive_release.py` selects releases with a
     `contextvars` context, so 15.535 and 16.635 bundles coexist in one process;
   - the compiled catalogs were adopted from `RoyaleFish/data_16635`
     (Apache-2.0) and verified against `B-general-v275`'s contract. Three repair
     steps made the bundle self-consistent without changing catalog semantics:
     stale `model_catalogs.input_catalog_ids`, stale 15.535 hashes inside
     `form_evidence`, and an incorrect manifest. Full detail:
     `docs/native/COMPETITIVE_RELEASES.md`.
   - all five built catalog ids equal the published checkpoint contract
     (`card 8c0baac9…`, `ability 128b6679…`, `entity_archetype 140e32c1…`,
     `effect a5bf4db4…`, `mechanic_profile 00b73f61…`).

   `load_policy_v4()` now reads the checkpoint contract: it resolves the release
   by catalog ids, builds `ModelConfigV4` from `contract.model_config`, and loads
   strictly. `B-general-v275` and `A-hog26-v738` load as **17,506,419 params,
   LSTM 768, spatial 128/5, runtime schema 21**; General/IL still load as
   12,684,339 params on the 16-column schema.

   **Play smoke passed (A51, seed 11, one game):** B-general vs General, both
   loaded in one process, `native_final_result` at tick 6089, 56/55 actions,
   **0 rejected**, empty crash buffer. General won this single game — that is a
   plumbing gate, not a strength result. `contract_compatible()` tolerates only the declared
   additive config keys, so an older checkpoint still loads and a real dimension
   change still fails.

   The two schema additions this required:
   - `CARD_RUNTIME_FEATURE_NAMES_V2` (16 + 5 pending-execution columns) and the
     tensorizer block that fills them from `observation.pending_actions`;
   - the additive `ModelConfigV4` fields (`neutralize_pending_ticks_remaining`,
     `delay_aware_legality`, `card_id_gate`, `ablate_*`).

   **Caveat (updated 2026-11-14 session 6):** `delay_aware_legality` is now
   branched on in the decoder, the environment action mask and compound
   validation (§3.39). Only the ablation/gate switches are still carried for
   exact round-tripping. B-general collects at full ACT rate with 0 rejected
   actions; its *strength* is still unvalidated until a paired gate runs.

   Note for future catalog work: recompiling from the local `Clashback` assets
   does **not** reproduce these ids — those assets are a different 16.635
   content revision (all 258 source-file hashes differ, and only 25 abilities
   bind). The compiled 16.635 set is the version-matched source for now.
3. **1.2 — x86 probe — DONE.** `native_runner/probe/out/x86_64/libcrprobe.so`
   builds with `native_runner/probe/build_probe.sh --target-build 16.635.1
   --abi x86_64`. Added the x86 call path, `x86_hook.h`, the logical
   offset/function/vtable tables, and `build_16_635_1_layout_x86.h`. The rich
   rings stay ARM-only exactly as on the A51; the JSON path needed its own key
   ABI (see §3.35 — the mirror of §3.34's trap).
4. **1.3 — host — DONE, without the bionic bundle.** `.resource/Clashback/vendor/crx`
   maps the x86_64 engine into a plain Linux process with glibc shims. The probe
   attaches with `cr_probe_attach(libg_base)`; `tools/native_analysis/crx_engine_host.py`
   boots the engine, installs the presentation guards, attaches the probe and
   serves the usual `cr-native-control.v1` port. The pinned 15.535.29 bionic
   runtime is no longer needed for 16.635.1.
5. **1.4 — GATE: PASSED.** Scripted X-Bow seeds 1–5 reproduce the A51 corpus
   decision-for-decision (`first_difference: null`), 0 rejected actions, normal
   terminals, and the seed-1 repeat is deterministic. After the A51 vtable fix
   (§3.36) the General-vs-IL evaluator also matches: both seats terminate at
   the same ticks/actions as x86_64 (3601 and 4794), with 0 rejected actions.
   `attest` is `production_ready` against
   `native_runner/versions/native-16.635.1-x86_64.json`. The x86 engine is now a
   **ruleset, scripted and evaluator** oracle.

The phone is no longer the training engine. The GPU line below is about
evaluation volume and PPO updates, not about rescuing a 4-engine farm. The phone
stays plugged in as the ARM64 oracle (`--engines 1` on demand).

### 2. Then buy this: 1× RTX A6000, `--gpus 1` — **done; box deleted, re-rent only when the engine feed justifies it**

| Option | $/h | Hours for $20 | Verdict |
| --- | --- | --- | --- |
| **RTX A6000 ×1** | **0.35** | **~57** | **Used for the first smoke; 6.33 GiB peak, 4 % utilisation** |
| RTX A6000 ×2 | 0.35 claimed | ~57 | Only if the 0.00 second GPU survives checkout — and still run `--gpus 1` |
| L40 ×1 / ×2 | 0.79 / 1.58 | 25 / 12.7 | 2.3× price for FLOPs this model cannot use |
| A100 80 GB ×1 / ×2 | 1.09 / 2.18 | 18 / 9 | 3.1× price; 80 GB VRAM and 24 vCPU are both irrelevant |

**Why the A6000 is not a compromise.** The model is 12.7M params today (17.5M
with the 16.635 config): ~70 MB fp32 weights, ~210 MB with Adam moments, and
activations for `--lane-minibatch-size 384` × `--sequence-chunk-steps 16` are
small — the update fits well under 2 GB, so 48 GB is 20–40× headroom. The load
is set by the engine, not the GPU:

| Engine | Actor rate | Batched-forward budget |
| --- | --- | --- |
| 4 A51 engines × 8 lanes | ~15.6k decisions/s | ~2 ms @ 32 lanes |
| x86_64, 16 matches/process × 8 | ~62k decisions/s | ~2 ms @ 128 lanes |

Either way Ampere clears it with the CUDA-graph replay path in
`async_cluster_self_play.py`; a bigger card buys latency margin the engine
cannot consume. `torch==2.5.1` + cu121 covers sm_86 (A6000) and sm_89 (L40).

**Do not start on two ranks.** `--gpus N` becomes
`torch.distributed.run --nproc-per-node=N` and splits the engines across ranks
(`rank_engine_indices [[0,2],[1,3]]` for the A51). That adds NCCL init and DDP
averaging to the first smoke for throughput the engine cannot produce. Split
ranks later, when the x86 farm is large enough that one GPU is genuinely
saturated — that is an evaluation/update problem, not a training-start problem.

**The $0.00 second A6000 is not a plan.** It is almost certainly the per-unit
price echoed onto the second slot. Read the provider's hourly estimate before
starting. If a 2-GPU box genuinely bills $0.35/h, take it and still pass
`--gpus 1` — a free spare GPU helps `serve_policy`/evaluation, not training.

**Skip the A100 host.** In remote-worker mode the tensorizers run on the
engine-node host; the GPU box only runs the rank, and `_bind_rank_cpus` needs
one worker CPU per engine plus ≥1 control CPU. With 4 engines,
`--worker-cpus 0,1,2,3 --control-cpus 4` validates on 6 vCPU. 24 vCPU/192 GB
buys nothing. If the x86 engine runs *on the GPU box*, CPU:GPU ratio flips and
16 vCPU is the useful upgrade — check that after step 1, not before.

### 3. Pre-flight on the GPU box, before a long run

- torch **2.5.1 + cu121** wheel, not the local `+cpu` build.
- The A51 is behind NAT and the trainer needs **one** inbound port (`29900`).
  Reverse-tunnel it from this laptop instead of exposing anything:
  `ssh -N -R 29900:127.0.0.1:29900 <gpu-box>`
  The manifest already records `remote_worker_host: 127.0.0.1`, so the tunnel is
  transparent — no manifest edit and no `--worker-host 0.0.0.0`.
- `--trainer-host` must equal `hostname -s` **on the GPU box**; the local `.env`
  has empty `CR_PPO_*`, so export `CR_PPO_DECK_POOL`, `CR_PPO_CHECKPOINT`,
  `CR_PPO_TOPOLOGY`, `CR_PPO_OUTPUT_DIR`, `CR_AI_PYTHON` there (environment
  wins over `.env`).
- ~20 GB disk (repo + 1.8 GB dataset + checkpoints).
- Confirm preemption policy; if spot, `--snapshot-every 1`.
- Keep the phone awake: `adb shell svc power stayon usb`.

Budget plan: 3–4 h (~$1.4) for the smoke + a few hundred paired eval games,
then stop and decide. Evaluation volume, not engine hours, is what $20 buys:
at ~2 s/game on an A6000 a 400-game paired gate is minutes, versus ~5 min/game
on this host's CPU.

### 4. N3 — done; reproduce or extend

```bash
./launch_cluster_a51.sh --engines 4          # visible windows, big cores 4-7
.venv/bin/python tools/native_analysis/resident_batch_throughput.py \
    --ports 26789,26790,26791,26792 --slots 16 --batches 10 --ticks 600 --actions \
    --output /tmp/opencode/resident_4x16.json
adb -s R58NA0QTHWV logcat -b crash -d
```

Expect ~14 matches/s capacity and a ~13.9 makespan floor. Record any change in
`FINDINGS.md` §3.30. The next lever is GPU inference, not more engines.

### 5. The GPU smoke — **done 2026-11-14**, see the GPU-smoke section above

The procedure that actually works (ThunderCompute box, A51 over a tunnel):

```bash
# on this host
./launch_cluster_a51.sh --engines 4
./launch_ppo_a51.sh --skip-launch --engines 4 --worker-host 127.0.0.1 --output-dir runs/a51-node0
setsid ssh -N -o ExitOnForwardFailure=yes \
    -R 127.0.0.1:29900:127.0.0.1:29900 <box>      # worker service
setsid ssh -N -R 127.0.0.1:26789:127.0.0.1:26789 <box>   # evaluator only

# on the box
.venv/bin/python -m native_runner.training.v4.build_ppo_multinode_topology \
    --node-ready runs/a51-node0/engine-node-ready.json --output runs/topology.json \
    --trainer-job-id 1 --trainer-host "$(hostname -s)" --gpus 1 \
    --worker-cpus 0,1,2,3 --control-cpus 4
export SLURM_JOB_ID=1 CR_AI_PYTHON=$PWD/.venv/bin/python CR_PPO_SEED=1
export CR_PPO_ALLOW_UNVERIFIED_DECK_POOL=1
export CR_PPO_GATE_TEMPERATURE_START=1.0 CR_PPO_GATE_TEMPERATURE_END=0.2 \
       CR_PPO_GATE_TEMPERATURE_ANNEAL_UPDATES=20
export CR_PPO_INFERENCE_BATCH_WAIT_MS=250   # ~one A51 engine step (§3.38)
bash native_runner/training/v4/launch_ppo_multinode_training.sh \
    runs/ppo-smoke runs/topology.json 1 40      # 1 update, 40 s segment
```

Non-obvious requirements: `SLURM_JOB_ID` must equal the topology's
`trainer_slurm_job_id` (there is no Slurm; just export it), the seed must be >0,
and the pool must be the 20-focus variant
(`select_focus_decks.py --deck-pool runs/deck-pool.json --top 20 ...`).

The 40 s segment is a **decision budget** (`segment_decision_steps = seconds*4`
per lane), not wall time: 40 s = 160 decisions/lane ≈ 5,120 frames.

Gate the run on: all 32 resident matches completing (needs ≥180 s at 720
decisions/match), **0 rejected actions**, `native_final_result` terminals, and a
saved checkpoint with a matching SHA/optimizer step. Then run 2–3 evaluator games
at `--device cuda` against IL and Hog26-2 before deciding on the fine-tune
length. Do not train on 15.535 labels.

### 6. N2 — the remaining port work

Resolve the `AMBIGUOUS`/`MISSING` anchors and install the rich hook rings
(combat events, movement/character/visibility/remaining, tower troops). Follow
§5 of `FINDINGS.md`; validate each anchor with `cr_elf.py` / `diff_builds.py`.

**Free head start — DONE (§3.33/§3.34).** The RoyaleFish table closed every
AMBIGUOUS anchor and 8 of 10 MISSING ones. The rich-subsystem offsets are now in
`offset-diff.json`; installing the rings is mechanical: emit a per-subsystem
16.635 layout header (combat, phase, special/action movement, character state,
remaining, visibility, tower troops), drop that subsystem's
`#if !defined(CR_PROBE_TARGET_16_635_1)` guard, and re-attest one subsystem at a
time. Validate each with a prologue match plus a live `observe_rich` sample. The
ARM inline hook copies 16 bytes verbatim, so a hook whose first 16 bytes contain
a branch target outside the steal window needs a relocated trampoline (most
ring anchors are calls/returns, not branch entries, so this is a small set).

**New, concrete N2 blocker:** the native-render gate SIGSEGVs on 16.635.1
(`cr_render_gate_swap_buffers` → replay JSON setter, §3.31). Fix the
replay-manager `+0x20` controller slot / setter `+0x90` manager read, then
re-attest `render_gate` with a rendered-match smoke. Until then,
`offline_agent.py` / `offline_duel.py` are headless-only in practice. This is
cosmetic for training: deprioritize it behind step 1.


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

**Published 16.635 baselines** (loadable now through the 16.635.1 release):

| Model | Deck | Params | Notes |
| --- | --- | --- | --- |
| `.resource/RoyaleFish/models/B-general-v275.pt` | many | 17,506,419 | 275 updates on 16.635 data; best public generalist; **loads** (step 1.1) |
| `.resource/RoyaleFish/models/A-hog26-v738.pt` | Hog 2.6 | 17,506,419 | **71.9% ±2.6 vs its start over 1,147 paired games**; same release/schema as B-general |
| `.resource/FirstLight-Zero/checkpoints/firstlight-zero-v1450.pt` | X-Bow 3.0 | ~1.17M | self-play only, **15.535.13**, mirror-only result |

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

**Decision: 1× RTX A6000 @ $0.35/h, `--gpus 1`, ~57 h of runway for $20.**
The GPU is not the bottleneck; the engine is.

- **RTX A6000 48 GB — $0.35/hr.** 12.7M (or 17.5M on the 16.635 config) params
  fit in <2 GB; 48 GB is headroom, not a reason to buy more compute. Cheapest
  per useful hour and the only GPU this plan needs.
- **L40 $0.79/hr and A100 80 GB $1.09/hr are 2–3× the price for FLOPs and VRAM
  the engine cannot feed** (~15.6k decisions/s on 4 A51 engines, ~2 ms per
  batched forward). They only make sense if the x86 farm grows by an order of
  magnitude, which is a later decision.
- **A "$0.00" second GPU must be verified at checkout** before it influences
  any plan; until then assume the second unit double-bills.
- **GCP $300 / Azure $100.** Useful only if you need a high-vCPU host with a GPU
  or a cloud Android/AVD cluster. New accounts often have **zero GPU quota**;
  check before planning. Free-trial terms apply per account.
- **One inbound port is all the networking you need.** Reverse-SSH `29900` from
  this laptop; keep the worker bound to `127.0.0.1`.
- **Spend order:** x86 16.635 engine, catalogs, deck pool, IL cache first
  (CPU, free), then GPU hours for evaluation volume and PPO updates. Never bill
  GPU hours to CPU-bound prep.

Scale options, in order of leverage:

1. **x86_64 16.635.1 native engine on this host** — 16 matches/process, no
   phone, same ruleset. The correct replacement for phone-bound training.
2. **crforge 16.635.3/8** — 127k steps/s/thread, tick-exact to recorded
   battles, `VectorBattle` Python env. Highest raw throughput; needs a V4→crforge
   observation adapter, and it is a reimplementation.
3. **More A51-class ARM64 devices** — cheapest per *deployable* engine, but
   hardware-bound and now the wrong axis for training.
4. **Cloud x86 host with KVM + ARM64 Android image/AVD cluster** — only if the
   deployment target itself must be scaled.

---

## Risks

1. **16.x port completeness** — the rich hook rings and several core anchors
   remain; N2 may take longer than N0/N1.
2. **Engine throughput** — no longer the limiter: one A51 gives ~12–14
   matches/s (§3.30). The remaining risk is a regression into the `background`
   cpuset; the launcher now verifies it. GPU policy inference is the cap.
3. **Host inference, not the phone** — one CPU core spends ~217 ms per policy
   forward; a full evaluation game is ~5 min. Continuous training needs a GPU.
4. **Argmax is passive** — both checkpoints give `ACT` only ~6% probability at
   the 5-tick cadence, so deterministic decoding stands still. Use `--sample`
   for evaluation and check gate entropy before live deployment.
5. **Catalog adaptation** — the device runs content 16.635.93 while the local
   default catalogs are pinned at 15.535.86. Deck ids/forms/costs match at
   level 11; Electro Spirit HP differs (215 vs 217). **Resolved for loading:**
   16.635 is a selectable, verified release (`native_runner/releases/16.635.1.json`)
   adopted from `RoyaleFish/data_16635` with a repaired manifest, and
   `B-general-v275` loads against it. The local `Clashback` 16.635 `csv_logic`
   assets are a *different content revision* and do not reproduce those catalog
   ids, so do not assume a recompile equals the published models. See
   `docs/native/COMPETITIVE_RELEASES.md`.
6. **Null's content** — official-game 16.x data may not match Null's custom
   balance/cards. Verify the fingerprint. Note FirstLight-Zero and RoyaleFish
   both train on official 16.635 tables, so Null's custom balance remains the
   one unmeasured domain gap.
7. **Ladder rules** — automated play may be prohibited; confirm before using a
   live account.
8. **Cloud quotas** — GCP/Azure GPU quota may be zero on new accounts.
9. **Native-render gate crash** — 16.635.1 SIGSEGVs in
   `cr_render_gate_swap_buffers` (§3.31). Headless evaluation/training is
   unaffected; the offline renderer and any live-render demo are blocked.
10. **GPU pricing is not a quote** — the "0.00" second GPU is almost certainly
    a display artifact. Verify the hourly estimate before starting a run, and
    treat preemption as real: `--snapshot-every 1` on spot.
11. **NAT between the GPU host and the A51** — the trainer needs inbound
    `29900`. The reverse SSH tunnel from this laptop is the supported path; a
    dropped tunnel looks like an engine timeout mid-segment. `tcp_endpoint_proxy.py`
    is the fallback, not a public bind.
12. **Simulator self-evaluation** — Hasty-CR's own warning: 93% against its own
    engine, and one human match found a bug 30M training steps had missed.
    In-engine win rates are not evidence. Paired gates must run on the native
    16.635.1 engine (A51 or x86), never only on crforge.
13. **Model-quality gap, not an engine gap** — the best public Ranked result is
    ~43% (ClashAI). Nothing in the ecosystem is above water vs top humans yet.
    Budget the work as a league/opponent/eval problem, and expect the first
    honest target to be "beats General and the Hog specialists reliably", not
    "beats top ladder".
14. **Candidate legality gap — closed.** `delay_aware_legality` is now branched
    on in the decoder, the environment action mask and the compound validator
    (§3.39), and gate temperature 1.0 collects cleanly. The residual risk is
    diagnostic, not semantic: one post-port `env 6` failure did not reproduce,
    and the resident batch error string still collapses injection, step-bound
    and capture failures. Capture it with `CR_PPO_FAILURE_DUMP` and
    `adb logcat -s CRNativeProbe` if it returns.

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
- **Native as the primary bulk trainer** — superseded 2026-11-15. The Python
  per-packet data path caps it at 0.082 matches/s and 4 % GPU. It is retained as
  the gate, the parity oracle, the deploy rig and the fallback; do not delete or
  idle it.
- **“crforge cannot be the trainer”** — reversed 2026-11-15: it is the bulk
  backend after C1; never a gate.
- **Official Supercell servers** — out of scope, not deferred. Different client,
  version and protocol; a legal/ToS question, not an engineering milestone.
- **“100 % GPU utilisation” as a goal** — retired as a target; the metric is
  matches/s per dollar at a gateable quality. Saturation is a diagnostic.

---

## Key paths

| Path | Purpose |
| --- | --- |
| `ROADMAP.md` | The single plan |
| `docs/native/16.635.1/FINDINGS.md` | 16.635.1 probe port evidence log (§3.35 = x86_64 host) |
| `tools/native_analysis/crx_engine_host.py` | Boot the x86_64 16.635.1 engine on Linux (crx) and attach the probe; `--engine-index N` for multiple host engines |
| `tools/native_analysis/profile_real_collection.py` | Real V4 collector on CPU. `--synthetic-model` runs the checkpoint's contract at narrow widths (isolates loop from forward); `--segments N` exercises boundary bootstrap + resume |
| `tools/native_analysis/profile_cluster_collection.py` | Scripted transport floor. `--diagnostic-timing` splits each worker round into decode / native step / tensorize / channel |
| `runs/loop-pipeline-20261115/` | Session-8 evidence: scripted decomposition, old-vs-new loop A/B, 10-segment stress |
| `runs/step-breakdown/` | §3.42 step attribution and §3.43 paired stock/lean observation evidence (`ab-*`, `ab8-*`) |
| `tools/native_analysis/profile_step_breakdown.py` | Per-method `BattleEnvV1.step` sections + sequential cProfile; use it for paired A/B on this host |
| `native_runner/probe/build_16_635_1_layout_x86.h` | Generated x86_64 core anchors |
| `native_runner/versions/native-16.635.1-x86_64.json` | x86_64 build pin (select with `CR_NATIVE_BUILD_ABI=x86_64`) |
| `native_runner/probe/out/x86_64/libcrprobe.so` | x86_64 probe (build artifact, pinned by hash) |
| `ROADMAP_CRFORGE.md` | **Bulk-training plan of record**: C0–C7 gates, fidelity contract, economics, CI/parity role |
| `.resource/crforge/python/crforge_env/vector.py` | `VectorBattle(count, threads)` — 256-lane zero-copy NumPy vector; `LadderConfig`, `SideSetup` |
| `.resource/crforge/python/crforge_env/bench.py` | `crforge_env.bench` — env-steps/s, ticks/s, obs MB by batch size; the C1 baseline |
| `.resource/crforge/env/build/install/env` | Installed JVM env distribution for `VectorBattle` |
| `~/.crforge/tables/16.635.8` | Pinned game tables (also 16.635.3); record the lock with every run |
| `crforge_bridge/` | `BattleEnvV1` bridge for evaluation/parity; 152/152 cards, 118 forms. `native_runner/crforge_resident.py` presents the same surface in-process |
| `crforge_bridge/vector_rows.py` | **C3-B row build** — `VectorGroupBuilder` (`_groups`+children), `VectorCardBuilder` (own/opponent sets incl. the 21-column pending schema), `VectorTowerBuilder` (the six-row tower section + the tower model-row map), `VectorSpatialPlaneBuilder` (the 14-channel explicit planes), `VectorScalarBuilder` (the 18 match scalars), `VectorEmptySectionBuilder` (the empty effect/event records), `VectorCandidateBuilder` (legal hand slots, native placement grid, building geometry), `VectorRelationBuilder` (target/reverse/card→group edges, tower tokens through `tower_index`) over a whole `VectorBattle` batch; every builder is actor-seat aware; `reference_observation` / `reference_action_mask` project the per-lane oracle input from the same facts |
| `tools/crforge/batched_groups.py` | **C3-B group gate** — per-lane `_groups` oracle vs the batched section; writes `runs/crforge-c3/batched-groups.json` |
| `tools/crforge/batched_cards.py` | **C3-B card/relation gate** — reads the checkpoint's own `model_config` (21 card columns), verifies both sections against the stock methods, then times them paired; writes `runs/crforge-c3/batched-cards.json` |
| `tools/crforge/batched_towers.py` | **C3-B tower gate** — 256 learner-deck lanes, `TowerSetV4` validation and full section equivalence every step, paired timing, the two envelope pins on the artifact; writes `runs/crforge-c3/batched-towers.json` |
| `tools/crforge/batched_spatial_planes.py` | **C3-B spatial-plane gate** — 256 learner-deck lanes, full-plane equivalence every step (per-frame caches cleared per lane), paired timing and coverage counters; writes `runs/crforge-c3/batched-spatial-planes.json` |
| `tools/crforge/batched_scalars.py` | **C3-B scalar gate** — 256 learner-deck lanes, full scalar equivalence with the oracle's own group/candidate counts, paired timing; writes `runs/crforge-c3/batched-scalars.json` |
| `tools/crforge/batched_empty_sections.py` | **C3-B empty-record gate** — 256 learner-deck lanes, both records validated and compared every step, paired timing; writes `runs/crforge-c3/batched-empty-sections.json` |
| `tools/crforge/batched_candidates.py` | **C3-B candidate gate** — 256 learner-deck lanes past the start cooldown, `ActionCandidatesV4` validation and full section equivalence every step, paired timing; writes `runs/crforge-c3/batched-candidates.json` |
| `tools/crforge/batched_tensorize.py` | **C3-B end-to-end gate** — 256 learner-deck lanes × 40 decisions, `--seats 0 1` drives one `VectorTensorizer` and one stock tensorizer per actor, compares whole records field by field plus `batch_digest`, records actions on both paths, and reports combined timing per lane-decision; writes `runs/crforge-c3/batched-tensorize.json` |
| `tools/crforge/frame_crosscheck.py` | **Native frame gate** — one play at the same game point in the x86_64 engine and a one-lane `VectorBattle`, compared on the first spawned frame; also records the facts-layer pending point; writes `runs/crforge-c3/frame-crosscheck.json` |
| `crforge_bridge/tests/test_encoder_spike.py` | Pins the C1 encoders to the bridge's own facts and card-parameter packing |
| `runs/crforge-c0-20261116/` | C0 legality soak: 20 games, `illegal`, `truncated`, `rejected_total` counters |
| `runs/crforge-c1/` | C1 encoder-spike JSON (`encoder-spike.json`), default shape 256 × 200 × 3 |
| `runs/crforge-c3/` | C3 tensorizer profiles: `tensorizer-profile.json` (shipped), `ab-stock.json`, `ab-optimized.json` (reverted NumPy variant), `ab-pair{1,2}-{stock,python}.json`; the C3-B section gates `vector-observation.json`, `batched-groups.json`, `batched-cards.json`, `batched-candidates.json`, `batched-towers.json`, `batched-spatial-planes.json`, `batched-scalars.json`, `batched-empty-sections.json`; the both-seat end-to-end gate `batched-tensorize.json` (+ `-seed2.json`) and the native frame check `frame-crosscheck.json`; `learner-deck/` holds the locked-deck match config, the clean 2-game rollout and its tensorizer profile |
| `tools/crforge/bridge_session.py` | Shared live bridge episode: evaluator and **locked X-Bow learner deck** constants (`DECK`, `LEARNER_DECK`, form availability), tower troop, release scope, one session per seat — used by all crforge tools; takes `port=` (socket bridge) or `native=` (resident adapter) |
| `tools/crforge/resident_parity.py` | **C2 deliverable** — resident vs bridge, decision for decision: parity digests (native/rich/atomic, masks, rewards, terminals) + paired `env.step` timing; writes `runs/crforge-c2/resident-parity.json` |
| `tools/crforge/profile_tensorizer.py` | **C3 deliverable** — replays a live policy episode's `tensorize`+`record_action` under cProfile; capture digest + per-decision `batch_digest` prove an A/B is bit-identical; `--profile-decisions 0` is the timing-only A/B mode |
| `tools/crforge/encoder_spike.py` | **C1 deliverable** — 256-lane `VectorBattle`, per-lane bridge facts vs vectorized NumPy frame, equivalence check, encode/tensorize split; writes `runs/crforge-c1/encoder-spike.json` |
| `docs/native/16.635.1/*.json` | Relocation, field offsets, vtables, overrides |
| `tools/native_analysis/` | A51 parity/probe tools |
| `tools/native_analysis/a51_engine_node.py` | Attest the A51 cluster → `v4-ppo-engine-node.v1` manifest |
| `launch_ppo_a51.sh` | Cluster + manifest + remote PPO worker service |
| `tools/native_analysis/resident_batch_throughput.py` | Resident-channel capacity measurement |
| `tools/native_analysis/scripted_xbow_match.py` | Scripted X-Bow match on the probe |
| `tools/native_analysis/parity_manifest.py` | Archive/verify/compare an A51↔x86 parity corpus |
| `runs/parity-a51/` | A51 ground-truth corpus + `manifest.json`, re-captured on `b80f2bdb…` (§3.32, §3.36) |
| `runs/parity-x86/` | x86_64 candidate corpus; reproduces every A51 entry (§3.36) |
| `docs/native/COMPETITIVE_RELEASES.md` | Selectable competitive data releases, 16.635 adoption |
| `native_runner/releases/16.635.1.json` | 16.635.1 release descriptor |
| `native_runner/data/competitive-16.635/` | Verified 16.635 catalogs (1.5 MB) |
| `tools/native_analysis/adopt_compiled_release.py` | Adopt a compiled bundle as a verified release |
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
| `.resource/CRkonwledagebase/` | Ecosystem index (58 projects, R0–R4 evidence) |
| `.resource/Clashback/` | **Gate substrate** (commit `002aa8c`): `game/engine/libg.so` (build `160635001`, sha `c4721bd9…`), `vendor/crx` ELF loader, `neo_replay` tick cache/snapshots. License unknown — **local use only, never commit or redistribute**. **Upstream unreachable since 2026-11-15 (404); this checkout is the only copy** |
| `.resource/RoyaleFish/` | Apache-2.0 (commit `c42108d`; README-only since `da9c8cf`): `data_16635/` (adopted), `B-general-v275` / `A-hog26-v738` (league seed + gate opponents), `royalefish/ever_*` (**C4 throughput recipe**), patches (reference only) |
| `.resource/FirstLight-Zero/` | X-Bow 3.0 self-play checkpoints (15.535.13) |
| `.resource/daybreak-cr/` | IL→PPO→live-Null's pipeline with league/bc recipe |
| `runs/deck-pool.json` | 7,089 decks / 443,381 uses (111 X-Bow) |

---

## Do not do

- **Do not start a bulk run before C0.** One reject (MiniPekka `26000018`) was
  still open at the 2026-11-15 handoff; a rollout with a reject is invalid.
- **Do not full-send vectorization before the C1 number.** A straight per-lane
  port is ~1.4×, not 10×. Measure `ms/lane` for per-lane and vectorized builds
  first; if the gate misses twice, fix the encoder, not the lane count.
- **Do not delete, retire or idle the native engine.** It is the promotion gate,
  the differential-parity oracle and the deploy rig. Keep it runnable; exercise
  it on a fixed cadence.
- **Do not validate parity only at the end.** 50–150 native matches every N
  updates and after every adapter change; a persistent win-rate gap freezes the
  checkpoint.
- **Do not chase 100 % GPU utilisation.** The metric is matches/s per dollar at a
  gateable quality; saturation is a diagnostic, and the model is launch-bound.
- **Do not plan for official Supercell servers.** Different client, version and
  protocol; a legal/ToS question, not a later milestone.
- **Do not treat the 2026-11-15 laptop bench as a cloud number.** It ran under
  load ~11 on a `powersave` i5-1235U; quote it as a floor and re-measure on the
  box before sizing a run.
- Do not train R0 or start an R0 data/checkpoint/adapter run.
- Do not use the x86 15.535.29 engine as the source of deployable Null's
  weights without an explicit adaptation step. (The **16.635.1 x86_64** engine
  is a different case: same client build, so it is a training substrate for the
  same ruleset.)
- Do not apply `RoyaleFish/patches/probe_x86_64_16635.diff` as-is: its base is
  the 15.535.1 ARM64 probe, while our tree is already 16.635.1 ARM64. Port the
  x86 call path and tables, not the retarget.
- Do not treat in-engine or mirror win rates as ladder evidence. Hasty-CR: 93%
  vs its own engine, and one human match found a bug 30M steps missed.
- Do not gate a promoted checkpoint only on crforge; gate on the native
  16.635.1 engine.
- Do not run native-render (`offline_agent.py`, `offline_duel.py`) on 16.635.1:
  the probe SIGSEGVs in the render gate (§3.31). The headless evaluator/PPO path
  is safe.
- Do not spend cloud credits before the x86 16.635 engine runs. (N0/N1 pass; the
  next spend is evaluation volume, after step 1.)
- Do not evaluate or deploy these checkpoints with deterministic argmax: it
  collapses to `WAIT`. Use `--sample` or verify gate entropy first. The PPO
  collector needs the same care: `gate_temperature 0.2` produced `ACT` = 0 over
  5,120 eligible ticks. Collect at 1.0 and anneal down.
- Do not read §3.30's 12–14 matches/s as a training decision rate: it was
  measured at 600 ticks per RPC. At the PPO 5-tick cadence four A51 engines are
  worth **0.26 matches/s** and one x86_64 process **0.61**.
- Do not build a gate tensorizer from a bare `ModelConfigV4()` when the
  checkpoint declares its own config: the default is the **16-column** card
  runtime schema, while `B-general-v275` declares **21** (the pending-execution
  columns). Read `contract.model_config` (`config_from_contract`) — the card
  gate does. The group section is unaffected because only that field differs.
- Do not time the tensorizer on a scripted policy without recording its actions:
  the public tracker's hand/cycle partition check fails, and `isinstance(entry,
  dict)` on `placement_masks` values (which are `FrozenMapping`) silently leaves
  the scripted policy on `WAIT`, so the "measurement" is an empty board. Use
  `tools/crforge/profile_tensorizer.py`, which replays the real recorded
  actions.
- Do not quote a min-of-decisions `tensorize` number as the packet cost. The C1
  tool reports the cheapest single decision; the replay mean on a busy board is
  0.99–1.07 ms/decision and is the number to size a run with.
- Do not expect another round of Python tuning to reach ≤0.5 ms/lane: the packet
  is 3,754 calls/decision and the reductions that remove calls measured flat
  end-to-end (FINDINGS §3.44).
- Do not treat a crforge hand slot's tile block as candidate legality. crforge
  keeps the slot open while its play waits to run (the card is still in hand and
  a second try can be *given*); `BattleEnvV1` closes it with `pending_execution`,
  and the batched candidate rows follow the environment: in hand, not queued,
  affordable at this decision's elixir, and at least one legal tile. A row set
  built from the tile block alone emits a candidate the bridge would never offer
  (`FINDINGS.md` §3.49).
- Do not order or type tower rows from the native kind alone. `_towers` sorts and
  emits `tower_type` by the **model** kind, which swaps the princesses under a
  horizontal flip; a build that only mirrors coordinates puts the princess rows
  in the wrong order and mislabels them. Also keep "destroyed" (a zero-HP row,
  maximum preserved) distinct from "absent" (padding) — `FINDINGS.md` §3.50.
- Do not accumulate the explicit spatial planes with float64 `np.add.at`
  operands: the per-lane loop adds each entity's float32 value to a float32
  plane, so a cell shared by two entities differs by one ULP. Cast the addends to
  float32 before the scatter. And do not mask the whole `(lanes, entities)` grid
  for every expression — flatten to the kept entities once; that alone was
  2.44× → 12.7× (`FINDINGS.md` §3.51).
- Do not drop the empty records' padding sentinels: `EventSetV4` validates
  `owner_type` 2 and group/token addresses `-1` on masked rows, and effects keep
  the parent-index `-1`; a zeroed record fails `validate` (`FINDINGS.md` §3.53).
- Do not take `_scalars`' phase and remaining clock from the same tick. The
  phase/multiplier come from the **native** tick and the remaining clock from the
  **canonical** tick; `batch.tick` is canonical and the builder takes the
  encoder's first raw tick as `native_tick_offset`. And do not feed the scalar
  oracle the batched group/candidate counts it is meant to check — run the stock
  chain (`FINDINGS.md` §3.52).
- Do not read `resident_batch_throughput.py` matches/s as a training rate. It is
  a raw transport ceiling: four engines measured 0.736 aggregate because one
  Python client decodes every lane, while the real worker floor is ~0.08
  matches/s per engine (§3.41). Convert units explicitly (720 decisions vs
  6,068 ticks per match, 1.69x apart).
- Do not expect loop pipelining to raise a row-bound CPU forward. It pays on a
  fixed-cost (GPU) forward and at engine counts where the coordinator would
  otherwise be serial (§3.40–§3.41).
- Do not run the pipelined collector's inference thread on CUDA until graph
  capture/replay, autocast and the `model_streams` waits are confirmed there;
  it is CPU-verified only.
- Do not raise `CR_PPO_INFERENCE_BATCH_WAIT_MS` to coalesce engines. 250 ms does
  hit 4.0 packets/forward, but the wait costs more than the saved forward: it is
  net slower than 2 ms on both row-bound CPU and a ~2 ms GPU forward (§3.40).
  Keep the default and pipeline the loop instead.
- Do not collect `B-general-v275` at gate temperature 1.0 without the
  delay-aware legality port — it is now in place (§3.39). If an
  `env N ... resident batch step or atomic capture failed` appears again, set
  `CR_PPO_FAILURE_DUMP` and watch `adb logcat -s CRNativeProbe`; the current
  probe error string still conflates injection, step-bound and capture.
- Do not name `H_LAST_RUN_REFUSAL` from `Schema.refusalNames()`: that list is the
  *giving* refusal namespace (`PlayerCommands.Refusal`), while the header is a
  `LadderMatch` code. They overlap at 4 — `OVER` in the match, `START_COOLDOWN`
  in the wrong list — so a decided match was reported as a cooldown. A queued
  play refused with `OVER` is a terminal cancel, not a rejected command
  (`FINDINGS.md` §3.45).
- Do not rent a GPU until a local segment completes in <10 min and C1 has a
  measured number; at 0.04 matches/s the A6000 idles at 4 %.
- Do not assume one A51 matches the upstream 1,528-match training scale.
- Do not mix 15.535.29 labels with 16.x labels.
- Do not mix the ARM64 and x86_64 build pins: `CR_NATIVE_BUILD_ABI` selects
  `native-16.635.1-x86_64.json`; the A51 must stay on the unsuffixed pin.
- Do not load the x86_64 probe through `JNI_OnLoad` on the host: crx mappings
  are anonymous. Use `cr_probe_attach(libg_base)` from `crx_engine_host.py`.
- Do not preload the probe's zlib `RTLD_LOCAL`. crx resolves imports with
  `dlsym(RTLD_DEFAULT)` and substitutes a stub that returns 0 for missing
  symbols, so `compress2` "succeeds" with a zero-byte output and every
  compressed batch entry is truncated (`FINDINGS.md` §3.37).
- Do not mix competitive releases (15.535 vs 16.635) in one training run or on
  one engine: a single engine tensorizes every actor row, so the learner, the IL
  anchor and every historical opponent must share the catalog release and model
  config. The trainer and collector now fail closed instead of shape-erroring.
- Do not expect a 15.535-contract checkpoint (General/IL) to tensorize on the
  x86_64 engine: its content is 16.635.8 and unresolved effect ids fail closed.
  Train 16.635-contract checkpoints there; keep the 15.535 pair for the A51 or
  an explicit adaptation step.
- Do not rebuild `native_runner/probe/out/x86_64/libcrprobe.so` without
  regenerating the pin's `probe_sha256` (attestation is fail-closed).
- Do not gate a 16.635 rich-classification anchor on `__x86_64__`.  Both
  `build_16_635_1_layout*.h` headers must carry the same vtable names and the
  probe must select them on both ABIs; the stale 15.535 fallback silently
  degrades the observation (§3.35–§3.36, enforced by
  `tests/test_probe_build_layout.py`).
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
