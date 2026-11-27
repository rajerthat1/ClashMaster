# Session handoff — 2026-11-14 — Track B: CR Native Linux runtime

> **Read [`ROADMAP.md`](ROADMAP.md) first.** It is the single plan now.
> This file is the next-session starting point.

## Current focus

**Track B only: cr-native on x86_64 Linux.**

- Engine host: **`cr-native-linux-bionic`** — original x86_64 `libg.so` in a
  plain Linux process (Android 31 Bionic userland, no AVD/QEMU/KVM).
- Training stack: **`cr-native-sandbox`** — `NativeRoyaleEnv`, self-play, PPO.
- Target: **15.535.29 / 150535029, x86_64**.
- Retired from the plan: Linux AVD, MuMu, phone engine clusters, the custom
  ARM64 `libg` loader, and the 16.635.1 probe as trainer.
- A51 / 16.635.1 stays as **parity + live reference only**.
- `crforge` is the ultrafast simulator / CI oracle in
  [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md); not the training path.

Runtime details: [`docs/native/LINUX_BIONIC_RUNTIME.md`](docs/native/LINUX_BIONIC_RUNTIME.md).
Bring-up evidence and runbook: [`docs/native/PHASE0_BRINGUP.md`](docs/native/PHASE0_BRINGUP.md).

## Phase 0 — PASSED: 1 h soak + 4/8/12 sweep (2026-11-14)

Evidence directory: `builds/native/phase0-20261114T000821Z/`.

1. **Soak: PASS.** 4 workers, 60 samples over 60 minutes, `ping_failures: 0`.
   RSS stayed flat at 324.6–327.3 MB per process; CPU mean 0.06 %; ping mean
   ≈0.85 ms. No worker died and no sample failed.
2. **First sweep attempt: failed, then fixed.** The sweep aborted at
   `native Tick freeze: 3681->3681`. Root cause: near the terminal the engine
   holds the native clock for one frame and reports `terminated` on the *next*
   RPC (`terminal_tick` still 3681). The sweep treated the zero-delta frame as
   a hard stall. Fix committed in `.resource/cr-native-sandbox`
   (`48b273f`, tests in `8264789`): `one_transition` retries up to 8 no-action
   frames before failing a tier.
3. **Orchestrator fixes** in `tools/native_bringup/run_phase0.py`: the sweep
   `--output` is now resolved absolute before running from `cwd=SANDBOX`
   (the failed attempt wrote `sweep.json` inside the sandbox clone), and a
   failed/missing sweep now marks the whole run failed instead of `complete`.
4. The 4/8/12 sweep was rerun into the same directory using the passed soak.
   Result (`sweep-summary.json` / `sweep.json`, 180 s per tier):

   | Workers | Aggregate ticks/s | Per-worker p50 | Status |
   | ---: | ---: | ---: | --- |
   | 4 | 300.5 | 75.6 | complete |
   | 8 | **486.1** | 60.0 | complete |
   | 12 | 474.0 | 39.0 | complete |

   `recommended_workers: 8`; **8 is the knee**, 12 is oversubscription (total
   falls and per-worker p50 drops 60 → 39). Every tier had no
   `safety_reasons`, 100 % action acceptance, 0 unexpected rejections, and
   terminal/reset counts matched (8/8, 13/13, 12/12). The failed attempt is
   preserved as `sweep-failed-attempt.json`.

   Rerun only the sweep (keeps the existing soak result):

   ```bash
   .venv/bin/python tools/native_bringup/run_phase0.py \
       --output-dir builds/native/phase0-20261114T000821Z --skip-soak
   ```
5. The sweep runs `decision_ticks=1`, so these are RPC-bound per-tick decision
   rates, not engine-only throughput. With the sweep's ~6.7k-tick matches that
   is ≈0.04 → 0.07 → 0.07 matches/s. Do not compare with the 100-tick
   no-policy engine figure.

**Read this first in the next session:**

```bash
cd /home/raj/Desktop/ClashMaster
OUT=$(ls -dt builds/native/phase0-* | head -1)
cat "$OUT/DONE"                        # exists only after the run finishes
jq . "$OUT/soak-summary.json"          # rss/cpu/ping per worker
jq . "$OUT/sweep-summary.json"         # ticks/s per tier, recommended_workers
tail -20 "$OUT/detached.log"
```

What the gates require:

- soak: `ping_failures: 0`, one row per port per minute in `soak.csv`, RSS flat
  (≈325–340 MB/process), no worker disappears;
- sweep: each tier `status: "complete"`, no `safety_reasons`, and
  `native_ticks_per_second` rises from 4 → 8 then plateaus at the knee.
  **12 workers is an oversubscription point** on this host (10 physical cores,
  12 logical); use the 8-worker number for official host sizing and 12 only as
  the knee/contention data point.
- The sweep runs `decision_ticks=1`, so its ticks/s is an **RPC-bound
  per-tick decision loop**, not raw engine throughput. Report it as such;
  the 100-tick no-policy figure (~8.3k ticks/s/worker) measures a different
  workload.

If the orchestrator is gone but workers remain, stop them by port:

```bash
for p in $(seq 39031 39042); do
    pid=$(ss -ltnp | grep ":$p " | grep -oP 'pid=\K[0-9]+' | head -1)
    [ -n "$pid" ] && kill "$pid"
done
```

### Plain English: what "soak" and "sweep" mean here

| Check | What it does | What it answers |
| --- | --- | --- |
| **Soak** | Starts 4 workers, leaves them running for an hour with nothing but `ping` every minute, recording RSS, CPU and thread count. | Is the runtime stable over time? Does memory creep? Does a worker die or stop answering? |
| **Sweep** | Runs the same tick-driving workload with 4 workers, then 8, then 12, measuring native ticks/s and latencies for each tier. | How many workers this host can actually feed before adding more stops helping — the **knee**. |

Soak is a **reliability** test; sweep is a **capacity** test. Both write
machine-readable evidence under `builds/native/phase0-*/`.

## A51 unit-HP + ability parity — completed 2026-11-14 (in parallel with the soak)

The previously open Phase 1 items are now measured at **level 11** on both
builds, with tools that take the same level normalization and deck:

- `tools/native_analysis/entity_hp_probe.py` — extended with `--level`,
  `--deck standard|xbow`, `--decisions`, `--deploy-cards`, `--owners`; emits a
  per-card `unitMaxHp` table. A51 evidence:
  `builds/native/a51-entity-hp-16.635.1-L11-seed1.json`.
- `tools/native_bringup/probe_native_entity_hp.py` — native counterpart on a
  local worker via `NativeRoyaleEnv`; forces target cards into the arena
  (`--cycle-elixir` keeps a bank so the 6-cost X-Bow can be played) and can
  press the Hero Knight ability (`--activate-ability`). Evidence:
  `builds/native/xbow-entity-hp-15.535.29-units-seed1.json` and
  `xbow-entity-hp-15.535.29-xbow-seed1.json`.
- `verify_xbow_determinism.py` now also aggregates per-card max HP and ability
  telemetry (`--entity-hp-output`); the match/action logic is unchanged (seed 1
  still ends at tick 3815).

Unit max HP, level 11:

| Card | A51 16.635.1 | native 15.535.29 | delta |
| --- | ---: | ---: | ---: |
| X-Bow | 1600 | 1600 | 0 |
| Knight / Hero Knight | 1766 | 1766 | 0 |
| Archers / Evo Archers | 304 | 304 | 0 |
| Tesla / Evo Tesla | 1182 | 1182 | 0 |
| Skeletons | 81 | 81 | 0 |
| Electro Spirit | **215** | **217** | **−2** |

Towers still match (King 4824, Princess 3052). Squads share HP between base
and evolution form on both builds. The Electro Spirit delta is real, not a
level artifact: A51 gives 195 at L10, 215 at L11, 236 at L12, while every other
card lands exactly on the native L11 value.

Hero Knight ability (`Knight_hero_Ability`, 2 elixir) matches exactly:

| | A51 16.635.1 | native 15.535.29 |
| --- | --- | --- |
| before | `buttonState 2`, charges 1/1 | `state 2`, charges 1, mana 2 |
| activation | accepted, state 6, charges 0 | accepted, `result_code 0`, elixir 6→4 |
| after | state 6 (`AllChargesConsumed`), 0 charges | state 6, 0 charges |
| second press | refused | n/a (one press per entity) |

A51 evidence: `builds/native/a51-ability-action-16.635.1.json` (0 problems).
Native evidence in `xbow-entity-hp-15.535.29-xbow-seed1.json` →
`abilityActivations`. Combined comparison:
`builds/native/parity-unit-hp-15.535.29-vs-16.635.1-L11.json`.

Note: the sandbox label table `ABILITY_STATE_NAMES` names code 6 `"disabled"`,
but the observed post-activation state is zero charges, and the A51 table names
code 6 `AllChargesConsumed`. Prefer the numeric codes for parity work.

Residual gap: evolution-cycle counters. A51 exposes `evolutionRuntime` only in
`observe_rich`; the native training state has no equivalent field. Evolution
forms are passive (no ability button/charges) and deploy with base-form HP.

## Phase 2 — bottleneck plan, measured 2026-11-14

Full write-up: [`docs/native/PHASE2_BOTTLENECK.md`](docs/native/PHASE2_BOTTLENECK.md).
Evidence: `builds/native/phase2-20261114T015424Z/profile-*.json`. Tool:
`tools/native_bringup/profile_native_loop.py` (transport / loop / model
phases; `--transition-mode fast-json`, `--transport-steps`, `--loop-steps`).

**Headline:** the engine is not the bottleneck; the one-tick JSON-RPC boundary
is. Engine-only at 100 ticks/RPC is **36.4k ticks/s at 8 workers** (≈9.6
matches/s) and **40.2k at 12** (≈10.6), so the Phase 2 10-matches/s gate is met
at 12 and within 4 % at 8. At one decision per tick the full sandbox loop is
**0.074 matches/s at 8 workers** (48 % inference, 40 % transition wait, 10 %
Python observation/mask).

Transport, 10 s after warm-up, one native tick per RPC:

| Workers | legacy ticks/s | fast-json ticks/s | speed-up |
| ---: | ---: | ---: | ---: |
| 4 | 410.3 | 928.3 | 2.26× |
| 8 | 611.0 | 1,024.9 | 1.68× |
| 12 | 562.0 | 973.6 | 1.73× |

Fast-json, ticks per RPC (decision interval):

| Ticks/RPC | 8 workers ticks/s | 8 eq. matches/s | 12 workers ticks/s | 12 eq. matches/s |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 1,024.9 | 0.27 | 973.6 | 0.26 |
| 4 | 3,836.7 | 1.01 | 3,951.7 | 1.04 |
| 8 | 7,357.7 | 1.94 | 7,383.7 | 1.95 |
| 100 | 36,382.2 | 9.60 | 40,249.8 | 10.62 |

Full `VectorNativeSelfPlayCollector` loop, one match per worker:

| Workers | Mode | Ticks/decision | Matches/s | Seconds/match |
| ---: | --- | ---: | ---: | ---: |
| 4 | legacy | 1 | 0.041 | 24.63 |
| 4 | fast-json | 1 | 0.058 | 17.13 |
| 8 | fast-json | 1 | 0.074 | 13.55 |
| 8 | fast-json | 4 | **0.293** | 3.41 |
| 8 | fast-json | 8 | **0.503** | 1.99 |

Worker `timing_v1` (8 workers, legacy, profiled) puts the engine at ~8 % of
transition time: ~51 % response JSON serialization, ~32 % observation JSON
parse, ~9 % step JSON parse, ~8 % engine JNI. The probe adds one extra
serialization pass, so profiled throughput (167 ticks/s) is not a capacity
number.

Model forward on this CPU (median of 20): sandbox recurrent **2.78 ms**
(batch 2, 1 thread) / **4.47–9.85 ms** (batch 8, 2 threads); R0 elite-v3
**125–269 ms** (batch 2–8, 1–8 threads). R0 is 20–90× slower and does not
scale with threads.

**Loop fix — implemented 2026-11-14 in `.resource/cr-native-sandbox`:**

1. ✅ `decision_ticks` added to both collectors
   (`training/vector_rollout.py`, `training/rollout.py`), with validation.
2. ✅ `training/train.py` and `selfplay_v2/train.py` expose
   `--decision-ticks` and `--transition-mode` (default `fast-json`), record
   both in the run manifest, and scale `native_ticks` by the interval.
   `training/evaluate.py` accepts the same two flags.
3. ✅ Re-measured with the real collector at 8 workers / interval 8:
   **0.522 matches/s**, 2,303 ticks/s, 1.91 s/match
   (`profile-w8-fast-json-steps8-collector.json`), matching the earlier
   0.503 monkey-patched run.
   Test evidence: mask, random-legal, run-contract, evaluation, dashboard,
   fast-path, active-worker, deck-spec, reward, persistent-client, scaling
   and v2 suites: **66 passed, 2 skipped, 1 pre-existing failure**
   (`test_v2_action_rate::test_sample_and_evaluate_log_probability_match`,
   `_safe_card_mask` shape bug in `selfplay_v2/model.py`; not touched by this
   change). `test_fast_training_transition.py` is 14 passed, 2 skipped.
4. Remaining before Phase 3: decide the inference host — R0 needs a GPU (or a
   smaller/distilled policy); the sandbox recurrent policy stays CPU-feasible.
5. Keep training at **8 workers**. 12 adds ~10 % engine-only and no RPC
   throughput; it remains an oversubscription point for one-tick decisions.

## Status 2026-11-13 (latest session): Phase 1 deck contract + determinism

Phase 1 items 1–5 are functionally done. A51 parity is first-pass (tower HP,
deck ids/forms/costs and starting elixir match at level 11); unit HP and
abilities are the remaining parity gap. All code changes are in the sandbox
clone (`.resource/cr-native-sandbox`, commits `feat(training): explicit deck
spec and X-Bow policy-token contract` and `feat(selfplay-v2): derive deck
contract from replay`), except the top-level tools below.

**1. `training/deck_spec.py` — explicit deck + policy-token contract.**
`DeckCard(card_id, form, level, token)` and `DeckSpec` replace the hardcoded
`CARD_IDS` list as the source of truth. The spec owns:

- the ordered eight cards and one stable policy token per card;
- `index` (base card id → slot) and `native_index` (base **and**
  evolution/hero form id → slot);
- `cost()` from the live catalog, `replay_entries()` for replay encoding, and
  `from_json` / `from_replay_entries` loaders.

It fails closed on an unknown card, an unavailable form, duplicate cards or
duplicate tokens. `training/schema.py` keeps `CARD_IDS` / `CARD_INDEX` /
`CARD_COSTS` as bootstrap defaults, but `ObservationEncoder(deck)` and
`build_action_masks(..., deck=...)` now use the explicit spec. Hand and
public-action features index by policy slot; entity grid features also accept
the native form id (and use `base_card_id` for building/troop channel
classification). The three form cards map as:

| Card | Native form id | Policy token | Slot |
| --- | ---: | --- | ---: |
| Hero Knight | 203000000 | `hero_knight` | 1 |
| Evo Archers | 13000001 | `evo_archers` | 2 |
| Evo Tesla | 13000102 | `evo_tesla` | 3 |

`ObservationEncoder`, the training/vector collectors, evaluation, behavior
aggregation and `selfplay_v2` telemetry now take a `DeckSpec`; `train.py` and
`evaluate.py` derive it from the replay, so the manifest records the exact
deck/token contract.

**2. X-Bow deck artifacts.**
`configs/decks/xbow-3.0.json` is the canonical config (deck order, forms, level
11, explicit tokens, `decision_model: r0-elite-v3`).
`examples/xbow-3.0.json` is the matching replay (both decks, seed 1), built from
the bootstrap template. Deck order: X-Bow, Hero Knight, Evo Archers, Evo Tesla,
Skeletons, Electro Spirit, Fireball, The Log.

**3. Determinism + legality harness.**
`tools/native_bringup/verify_xbow_determinism.py`: same replay seed + same
deterministic legal-action script → identical per-tick observation hash chain;
it also validates hand order, deck indices, form flags, `has_evolution` /
`has_hero`, and classifies engine refusals (result codes 3/4 = gate, anything
else = illegal). It supports `--ports` for parallel workers and
`--golden-output` for the `xbow-native-golden-v1` parity frames.

Recorded run (2026-11-13, 4 workers on 39031–39034, seeds 1–20, two runs per
seed = 40 matches, 618.35 s wall):

- **20/20 seeds deterministic**; per-tick hash chains match on the repeat.
- **`illegal: 0`**, `gate_refusals: 0`, `deck_errors: 0`, `failure_count: 0`.
- Terminal ticks range **3681–6139** (median ≈ 4041).
- Native golden seed 1: `builds/native/xbow-golden-15.535.29-seed1.json`.
- Full report: `builds/native/xbow-determinism-20.json`.

**4. A51 golden validation — first pass complete.** The A51 (`SM_A515U`,
`R58NA0QTHWV`) was attached and the 16.635.1 probe served
`coldReady: true`. `tools/native_analysis/scripted_xbow_match.py --level 11`
completed a fixed X-Bow match with **0 problems, 0 ability rejections**:
175 decisions / 42 actions at level 1, 174 decisions / 59 actions at level 11;
terminal tick **3601** in both, crowns `[1, 0]` at level 1 and `[0, 1]` at
level 11 (same seed, different tower levels).

Level normalisation is the key: A51 `MatchConfig` defaults to level 1. With
`--level 11` (`level_cap=11`, `cardlvlmin=11`, `king_tower_level=11`) the
tower HP matches 15.535.29 exactly:

| Field | 15.535.29 native | 16.635.1 A51 L11 |
| --- | ---: | ---: |
| King tower max HP | 4824 | 4824 |
| Princess tower max HP | 3052 | 3052 |
| Deck card ids / order | exact | exact |
| Form flags | `[0,2,1,1,0,0,0,0]` | `[0,2,1,1,0,0,0,0]` |
| Card costs | XBow 6, Knight 3, Archers 3, Tesla 4, Skeleton 1, Spirit 1, Fireball 4, Log 2 | same |
| Starting elixir | 6.0 at tick 0 (7.0 at tick 100) | 6.0 at tick 0 |

The opening hand differs for the same seed (build-dependent RNG), and unit-HP /
ability parity still needs a level-11 entity probe. Reports:
`builds/native/a51-scripted-xbow-16.635.1-L11-seed1.json` and
`a51-scripted-xbow-16.635.1-seed1.json` (level-1 baseline).

**5. Model smoke.**
`tools/native_bringup/smoke_r0_model.py --rich` passes on CPU: R0 elite-v3,
36,853,407 parameters, vocabulary 212, observation schema
`07c183d2…7426`, one real PPO update, checkpoint 444,213,575 B, save/reload
bit-exact (`state_dict_sha256 50f08a8f…d014`). The resumed update hits the KL
guard (`updated: false, reason: kl_guard`), which is expected after applying
the first update to the same segment.

**Test evidence:** `tests/test_deck_spec.py` plus the existing mask, random-legal,
run-contract, evaluation, dashboard, fast-path and active-worker tests:
**37 passed, 2 skipped**. The 20-seed, 40-match determinism/legality run passed
with `illegal: 0`; details are in the item 3 block above and
`builds/native/xbow-determinism-20.json`.

## Previous session: Phase 0 cold start PASSED without a VM

The runtime now cold-starts, serves, and completes matches **on this daily
driver, without root and without a VM**. No host paths were written; everything
is under `builds/`.

What was actually wrong with the published bundle, and the fixes:

1. `linker64` / `dalvikvm64` were mode `0666` → `chmod +x` in the overlay.
2. `libandroid_runtime.so` missing from `public.libraries.txt` → appended.
3. Host JAR/bridge predate the binderless JNI → rebuilt from sandbox source.
4. `assets/assets.scdb` + `data/update/data_manifest.toml` missing → extracted
   from the sandbox runtime ZIP's split asset pack. `asset-pack.apk` is **not**
   needed by a direct worker (verified with and without it).
5. Property area: `__system_properties_init()` = `-1` was **not** a bundle
   defect. bionic's `prop_area::map_fd_ro()` rejects any property file whose
   `st_uid`/`st_gid` is not 0. A `--system-root` install as `raj` owns them, so
   it fails. Running the worker in a user namespace with
   `--unshare-user --uid 0 --gid 0` maps our files to root-owned inside the
   namespace and init returns 0.
6. Bridge UI-locale guard: on the non-null path the cave at `0x180B1E5` called
   the locale registration at `0x1AE0270`, which did a pointer-sized `new`
   (~134 TB) → `std::bad_alloc` → abort after loading. The cave now jumps
   straight to the resume point `0x14ABA2A`. The registration is
   presentation-only.

New tools (all under `tools/native_bringup/`):

- `preflight_bundle.py` — now checks property-file ownership and the headless
  locale cave; requires only the two real worker assets.
- `build_host_artifacts.sh` — rebuilds `lifecycle-probe.jar` + bridge.
- `build_local_overlay.py` — patches the published bundle and rewrites the
  manifest; output is `builds/bundles/…-local-overlay/`.
- `run_rootless_worker.sh` — runs one installed worker via `bwrap`.
- `patches/0001-bridge-headless-ui-locale-guard.patch` — the bridge cave fix
  (also applied to the local `.resource/cr-native-sandbox` clone).

Recorded PASS evidence:

- Preflight: **PASS** on the local overlay (published bundle still FAIL).
- `verify-bundle`: 8 archives, 276,550,763 B, libg `fa6704b8…46ba`.
- `install --system-root builds/rootless/system-root --workers 4 --apply`:
  ok; `doctor`: **hard_pass=true**.
- 4 workers on 39031–39034: all `ping` ok; ≈335 MB RSS each, stable after
  100 matches.
- One worker completed a full match: terminal tick **6147**
  (`native_tiebreak_exact_draw`, crowns `[0, 0]`).
- 100 consecutive matches (4 workers × 25, `reset` between) completed with
  zero failures; RSS did not grow.
- Soak: 23 min idle with 0 crashes and flat RSS, plus the 100-match burst.
  The full 1 h gate was **not** completed (the workers were stopped on
  purpose); rerun it in the next session if the strict gate is required.
- No-policy throughput, 100-tick steps, installed root on NVMe:
  - one worker: 6,100 ticks in **0.74 s** ≈ **8.3k ticks/s** ≈
    **1.33 matches/s**;
  - 4 concurrent full matches: wall 0.89 s, aggregate **32.6k ticks/s** ≈
    **5.3 matches/s**; per worker **1.33 matches/s**;
  - **100 consecutive matches** (25 per worker) with zero failures and
    `reset` between matches: **4.63 matches/s sustained**, RSS flat at
    ~335 MB/process.
  - (The same worker on the `/tmp` tmpfs fake root was ~0.81 matches/s; the
    root filesystem matters.)

Local overlay artifacts (gitignored, not upstream):

| Item | Size | SHA-256 |
| --- | ---: | --- |
| `…-local-overlay.zip` | 276,545,837 B | `a4bb0248dc5ee773aee8ac56284f705369cd2c20d510c0f69a1b095cb2ae1048` |
| `libnative_host_bridge.so` | 8,817,864 B | `3e51fd43bce2167de952d5e3e94cc549e257f17eeceb4dd7312d7c3a24d9d867` |
| `lifecycle-probe.jar` | 28,214 B | `03fcfa37dedeff68d9b5faae5e0593dabb48c466e6aa610fbb09b695c0af4d86` |

Published bundles (GitHub releases, not in git): engine
`e25f6484…30db0` (273,274,290 B), sandbox runtime `8d829f21…bc28` (899,751,022 B).

## What we know about throughput

- No-policy, one worker, 100-tick RPC steps, `-Xint`: ~**8.3k ticks/s** ≈
  **1.33 matches/s** on this 12-thread host (installed root on NVMe).
- 4 workers concurrently: ~**32.6k ticks/s** ≈ **5.3 matches/s** aggregate.
- Active-worker sweep 2026-11-14 (`decision_ticks=1`, RPC-bound): **4 →
  300.5**, **8 → 486.1**, **12 → 474.0 ticks/s**; per-worker p50 75.6 / 60.0 /
  39.0. Knee at **8 workers**; 12 is oversubscription.
- Converted to matches/s with the sweep's ~6.7k-tick bootstrap matches:
  ≈0.04 / 0.07 / 0.07 matches/s for the per-tick decision loop.
- Engine-only 4-worker (32.6k ticks/s ÷ ~3.8k ticks/match) ≈ **8.6 matches/s**,
  still below the Phase 2 gate of 10; the sustained 100-match burst (with
  observation and reset overhead) measured 4.63 matches/s.
- The old pure-engine figure (~74.7 µs/update → ~13.4k ticks/s) is without the
  bridge's terminal scan/observation work; do not compare directly.
- Published full loop (4.8M model, decisions every 4 ticks, 25-core host +
  RTX 5090): 12 processes = 0.84 matches/s, 24 = 0.93.
- Phase 2 profile 2026-11-14 (`docs/native/PHASE2_BOTTLENECK.md`):
  - engine-only, 100 ticks/RPC, fast-json: 36.4k ticks/s at 8 (≈9.6
    matches/s), 40.2k at 12 (≈10.6);
  - one tick/RPC, fast-json: 1,025 ticks/s at 8, 974 at 12; RPC rate is flat
    at ~920–990/s for 1–8 ticks/RPC, so the JSON-RPC boundary is the limit;
  - full sandbox loop at 8: 0.074 matches/s at 1 tick/decision, **0.277 at 4,
    0.522 at 8** with the real `decision_ticks` collector (0.293 / 0.503 in
    the instrumented harness);
  - worker time at one tick/RPC is ~90 % JSON (≈51 % serialize, ≈32 % observe
    parse, ≈9 % step parse) and ~8 % engine JNI;
  - fast-json is 1.5–2.1× faster than legacy; the decision interval is the
    largest single lever.
- So the training bottleneck is the JSON-RPC boundary plus model inference,
  not the Python observation encoder (~10 % of wall). R0 CPU inference
  (125–269 ms/pass) needs a GPU before Phase 3.

## Next work — do these in order

### 1. Phase 0 evidence — already read and recorded (no action left)

The soak passed and the 4/8/12 sweep is complete; both gates are marked in
`ROADMAP.md`. Evidence lives in
`builds/native/phase0-20261114T000821Z/` (`soak-summary.json`, `sweep.json`,
`sweep-summary.json`, `sweep-failed-attempt.json`). Only revisit it if a
future run contradicts these numbers.

### 2. A51 parity — DONE 2026-11-14 (no action left)

Level-11 unit HP and the Hero Knight ability are now measured on both builds;
see **“A51 unit-HP + ability parity”** above. Residual: Electro Spirit
215 (A51 16.635.1) vs 217 (native 15.535.29); every other card matches, and
the ability sequence is identical. If a level-11 entity/ability sample is ever
needed again:

```bash
adb forward tcp:26789 tcp:26789
.venv/bin/python tools/native_analysis/entity_hp_probe.py --level 11 --deck xbow \
    --decisions 80 --output builds/native/a51-entity-hp-16.635.1-L11-seed1.json
.venv/bin/python tools/native_analysis/ability_action_probe.py \
    --output builds/native/a51-ability-action-16.635.1.json
```

Native side: `tools/native_bringup/probe_native_entity_hp.py --port <worker>
--targets 27000008 --activate-ability`.

### 3. Phase 2 — DONE 2026-11-14 (plan written)

See **“Phase 2 — bottleneck plan”** above and
[`docs/native/PHASE2_BOTTLENECK.md`](docs/native/PHASE2_BOTTLENECK.md).
The one-tick sweep still says **8 workers** is the knee (486.1 ticks/s; 12 is
oversubscribed at 474.0 with per-worker p50 39 vs 60). Engine-only capacity at
100 ticks/RPC is 9.6 matches/s at 8 and 10.6 at 12, so the 10-matches/s gate is
met at 12 and within 4 % at 8. Training stays at 8.

### 4. Phase 3 — training, after the measured loop fix

The loop fix is in place (fast-json default, `--decision-ticks` 4–8,
measured **0.522 matches/s at 8 workers / interval 8**). Before the long run:

1. choose the inference host: R0 elite-v3 needs a GPU (125–269 ms per CPU
   pass); the sandbox recurrent policy stays CPU-feasible;
2. pick and record the decision interval (start at 4; 8 is the measured
   throughput point) and the level/deck contract in the run manifest;
3. run a short smoke (`--smoke`) and one iteration with checkpoint save/restore
   before detaching the long PPO run.

R0 elite-v3 remains the chosen stack; FirstLight v1450 remains a
benchmark/opponent.

**Phase status:** Phase 0 ✅ (1 h soak, knee 8). Phase 1 ✅ (deck/schema,
X-Bow config + tokens, 20-match determinism/legality, model smoke, A51
tower/card/elixir/unit-HP/ability parity; Electro Spirit 215 vs 217 is the one
documented delta). Phase 2 ✅ (bottleneck plan measured; engine-only gate met
at 12 workers). Next is the loop fix above, then Phase 3.

### Starting workers manually (if the detached run is not active)

12 slots are now installed under `builds/rootless/system-root`. Start any
contiguous range:

```bash
cd /home/raj/Desktop/ClashMaster
for slot in 0 1 2 3; do
    LOG=/tmp/opencode/rootless-worker-$slot.log SLOT=$slot \
        PORT=$((39031 + slot)) \
        nohup tools/native_bringup/run_rootless_worker.sh >/dev/null 2>&1 &
done
```

Stop by port (never run `start` again on a busy port):

```bash
for p in $(seq 39031 39042); do
    pid=$(ss -ltnp | grep ":$p " | grep -oP 'pid=\K[0-9]+' | head -1)
    [ -n "$pid" ] && kill "$pid"
done
```

## Phase 1 readiness — deck and schema (source audit)

Open question 1 is answered from
`native_core/data/live_card_catalog.json` (152 cards, 42 evolutions, 16 hero
forms, game version `15.535.29`):

| Card | ID | Form |
| --- | ---: | --- |
| X-Bow | 27000008 | base |
| Hero Knight | 26000000 | hero (`Knight_hero`) |
| Evo Archers | 26000001 | evolution (`Archer_EV1`) |
| Evo Tesla | 27000006 | evolution (`Tesla_EV1`) |
| Skeletons | 26000010 | base |
| Electro Spirit | 26000084 | base |
| Fireball | 28000000 | base |
| The Log | 28000011 | base |

The deck is representable: `card_catalog.py` encodes per-card form flags in
`el` (base 0, evolution 1, hero 2, both 3) and `NativeRoyaleEnv` reads them into
`form_flags`. Both gaps identified in the Phase 0 session are now closed:

- `training/schema.py` no longer hardcodes the bootstrap deck: `DeckSpec` in
  `training/deck_spec.py` owns the ordered cards, policy tokens and native-id
  aliases, and the encoder/masks/collectors take it explicitly.
- The token contract is explicit (`configs/decks/xbow-3.0.json`) and does not
  rely on slot order or `human_vs_ai.py`'s warning. The determinism harness
  verifies the engine-reported hand order, deck index, form flags,
  `has_evolution` and `has_hero` against that contract.

Deck order for the config: **X-Bow, Hero Knight, Evo Archers, Evo Tesla,
Skeletons, Electro Spirit, Fireball, The Log**. The model decision is explicit
in the config (R0 elite-v3 first; v1450 is a benchmark/opponent).

## `/apex` and `/system` — why the warning

The installer writes Android-style absolute paths as root:

- `/apex` — Android's APEX runtime modules (ART, ICU, Conscrypt, …)
- `/system` — Android system libs/framework JARs
- `/linkerconfig` — Android linker namespaces
- `/dev/__properties__` — Android property area (tmpfs)
- `/data/local/tmp/cr-native-direct-N` — per-worker runtime

On a normal Linux host these are usually empty or nonexistent, so creating them
is additive. It does **not** touch `/boot`, the kernel, GRUB, or partitions, so
it **cannot brick the firmware**. The risk is different: it runs as root and
writes absolute paths, so a bug, a wrong bundle, or a path collision can damage
the OS or future packages. That is why a root install belongs in a **dedicated
VM or container with a snapshot**, not the daily driver.

The rootless path used this session does **not** install as root: it writes a
`--system-root` under `builds/` and runs the worker in a user namespace. Use it
on the daily driver.

## Open questions

- ~~Why does `__system_properties_init()` return `-1`?~~ **Answered:** bionic
  `prop_area::map_fd_ro()` requires `st_uid`/`st_gid` 0. Fake-root installs
  leave the files user-owned; a real root install or `--uid 0` user namespace
  fixes it.
- ~~Which artifact is startable?~~ **Answered:** the published bundle is not;
  the local overlay built by `build_local_overlay.py` is.
- ~~Where do the missing worker assets come from?~~ **Answered:** the sandbox
  runtime ZIP's `split_install_time_asset_pack.apk`; only `assets.scdb` and
  `data_manifest.toml` are needed, not the 886 MB APK.
- ~~Does the rebuilt host JAR/bridge pass a full match?~~ **Answered:** yes,
  terminal tick 6147.
- ~~Is the X-Bow deck representable, and does the engine report its forms/hand
  consistently?~~ **Answered:** yes; the deck/token contract and the
  determinism harness validate hand order, deck indices, form flags and
  abilities. See `builds/native/xbow-determinism-20.json`.
- ~~Can one PPO step run on the sandbox R0 elite-v3 stack?~~ **Answered:** yes,
  CPU smoke with a bit-exact checkpoint restore.
- What is the 4/8/12-worker knee on this host? **Answer in
  `builds/native/phase0-20261114T000821Z/sweep-summary.json`**; treat 12 as an
  oversubscription point (10 physical cores).
- How large is the 15.535.29 vs 16.635.93 balance/card gap? **Answered for the
  X-Bow deck at level 11:** towers, deck ids/forms/costs, starting elixir and
  all unit max HP match except **Electro Spirit (A51 215 vs native 217)**.
  Hero Knight ability state/charges/elixir match exactly. Opening-hand RNG and
  evolution-cycle counters remain build-dependent/not exposed. Evidence:
  `builds/native/parity-unit-hp-15.535.29-vs-16.635.1-L11.json`.
- Is bridging FirstLight v1450 worth it, or train from scratch with the
  sandbox stack?
- Can the live canary use the existing A51/16.635.1 probe/touch path unchanged?

## Key paths

| Path | Purpose |
| --- | --- |
| `ROADMAP.md` | The single plan |
| `ROADMAP_CRFORGE.md` | Ultrafast simulator / CI oracle (reference) |
| `docs/native/PHASE0_BRINGUP.md` | Cold-start evidence, root causes, rootless runbook |
| `docs/native/PHASE2_BOTTLENECK.md` | Measured throughput breakdown and Phase 2 plan |
| `tools/native_bringup/profile_native_loop.py` | Transport / full-loop / model profiler (fast-json and decision-interval A/B) |
| `builds/native/phase2-20261114T015424Z/` | Phase 2 profile evidence (gitignored) |
| `tools/native_bringup/preflight_bundle.py` | Static cold-deploy preflight (no root) |
| `tools/native_bringup/build_host_artifacts.sh` | Rebuild host JAR + bridge |
| `tools/native_bringup/build_local_overlay.py` | Patch the published bundle + manifest |
| `tools/native_bringup/run_rootless_worker.sh` | Run a worker via `bwrap` user namespace |
| `tools/native_bringup/verify_xbow_determinism.py` | X-Bow deck contract + per-tick determinism/legality harness (now also per-card HP/ability aggregates) |
| `tools/native_bringup/probe_native_entity_hp.py` | Native per-card max HP + Hero Knight ability probe (level via replay) |
| `tools/native_analysis/entity_hp_probe.py` | A51 per-card max HP probe (`--level`, `--deck xbow`, targeted deploys) |
| `tools/native_bringup/smoke_r0_model.py` | R0 elite-v3 PPO step + checkpoint save/restore smoke |
| `tools/native_bringup/run_phase0.py` | Detached 1 h soak + 4/8/12-worker sweep + evidence |
| `builds/native/phase0-*/` | Soak CSV, RSS/CPU summary, sweep report (gitignored) |
| `builds/native/xbow-determinism-20.json` | 20-seed determinism/legality report |
| `builds/native/xbow-golden-15.535.29-seed1.json` | Native 15.535.29 golden frames |
| `builds/native/a51-scripted-xbow-16.635.1-L11-seed1.json` | A51 16.635.1 level-11 fixed-script report |
| `builds/native/a51-entity-hp-16.635.1-L11-seed1.json` | A51 level-11 per-card max HP |
| `builds/native/xbow-entity-hp-15.535.29-xbow-seed1.json` | Native level-11 per-card max HP + Hero Knight ability |
| `builds/native/parity-unit-hp-15.535.29-vs-16.635.1-L11.json` | Combined unit-HP + ability parity table |
| `.resource/cr-native-sandbox/training/deck_spec.py` | Explicit deck + policy-token contract |
| `.resource/cr-native-sandbox/configs/decks/xbow-3.0.json` | X-Bow 3.0 deck config |
| `.resource/cr-native-sandbox/examples/xbow-3.0.json` | X-Bow replay for both sides |
| `docs/native/LINUX_BIONIC_RUNTIME.md` | Runtime evaluation, hashes, commands |
| `builds/bundles/` | Verified bundles + local overlay (gitignored) |
| `builds/rootless/` | Rootless `--system-root` install + runtime (gitignored) |
| `.resource/cr-native-linux-bionic/` | Runtime CLI source |
| `.resource/cr-native-sandbox/` | Native env, card catalog, self-play/PPO |
| `.resource/FirstLight-Zero/` | X-Bow recipe, v1450 benchmark |
| `.resource/clash-royale-research/` | 160402020 approximate simulator + assets (side reference) |
| `.resource/native/16.635.1/reference/libg.so` | A51 parity reference |
| `docs/native/16.635.1/FINDINGS.md` | 16.635.1 evidence log |
| `tools/native_analysis/` | A51 probe tools (parity only) |

## Do not do

- Do not run `cr-native-bionic install` **as root** on the daily driver; use
  the rootless `--system-root` + `run_rootless_worker.sh` path there.
- Do not trust `doctor` `hard_pass=true` as proof the runtime starts; run
  `preflight_bundle.py` and one real worker first.
- Do not add the 886 MB `asset-pack.apk` to a direct worker; it is not needed.
- Do not build an AVD/MuMu/phone cluster or a custom loader unless a Phase 0/1
  gate fails.
- Do not train on `IL_Replay`.
- Do not make crforge the critical path.
- Do not run real-time matches as the training loop.
- Do not re-run `start` on a busy port (duplicate-process bug); check `ss` /
  `/proc/PID/cmdline` first.
- Do not start the main ladder before Phase 4.
