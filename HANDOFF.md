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

## Right now — Phase 0 soak + sweep running detached (2026-11-14 00:08 UTC)

The remaining Phase 0 gate is a detached run of
`tools/native_bringup/run_phase0.py`:

```
builds/native/phase0-20261114T000821Z/
```

It installed 12 rootless worker slots, is soaking 4 workers for 60 minutes
(60 s sampling), then stops them, starts 12, and runs the active-worker sweep
for 4/8/12. Expected: soak ends ≈ **01:08 UTC**, sweep ≈ **01:20 UTC**.

**Read this first in the next session:**

```bash
cd /home/raj/Desktop/ClashMaster
OUT=$(ls -dt builds/native/phase0-* | head -1)
cat "$OUT/DONE"                        # exists only after the run finishes
jq . "$OUT/soak-summary.json"          # rss/cpu/ping per worker
jq . "$OUT/sweep-summary.json"         # ticks/s per tier, recommended_workers
tail -20 "$OUT/detached.log"
```

What passes:

- soak: `ping_failures: 0`, one row per port per minute in `soak.csv`, RSS flat
  (≈325–340 MB/process), no worker disappears;
- sweep: each tier `status: "complete"`, no `safety_reasons`, and
  `native_ticks_per_second` rises from 4 → 8 then plateaus at the knee.
  **12 workers is an oversubscription point** on this host (10 physical cores,
  12 logical); use the 8-worker number for official host sizing and 12 only as
  the knee/contention data point.

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
- The old pure-engine figure (~74.7 µs/update → ~13.4k ticks/s) is without the
  bridge's terminal scan/observation work; do not compare directly.
- Published full loop (4.8M model, decisions every 4 ticks, 25-core host +
  RTX 5090): 12 processes = 0.84 matches/s, 24 = 0.93.
- So engine-only throughput is already enough; the training bottleneck remains
  observation/host work, as the Sep-10 cloud note found.

## Next session — do these in order

### 1. Read the Phase 0 result (5 minutes)

```bash
cd /home/raj/Desktop/ClashMaster
OUT=$(ls -dt builds/native/phase0-* | head -1)
cat "$OUT/DONE"
jq '{passed, samples, ping_failures, workers}' "$OUT/soak-summary.json"
jq '{status, tiers, recommended_workers}' "$OUT/sweep-summary.json"
```

- If soak passed and RSS is flat, mark the Phase 0 soak gate done in
  `ROADMAP.md`.
- Record the 4/8/12 ticks/s and the knee in this handoff and in
  `ROADMAP.md` Phase 2.
- Do not rerun the 1 h soak unless the summary shows a failure; inspect
  `soak.csv` and the failing worker log instead.

### 2. Finish A51 parity (the only open Phase 1 item)

The A51 (`R58NA0QTHWV`, `SM_A515U`) is attached. Launch the probe and forward
the control port:

```bash
adb forward tcp:26789 tcp:26789
adb shell monkey -p nullsroyale.rel.free -c android.intent.category.LAUNCHER 1
```

Already confirmed: at **level 11**, 16.635.1 and 15.535.29 agree on King HP
4824, Princess HP 3052, deck ids/order, forms `[0,2,1,1,0,0,0,0]`, all eight
elixir costs, and starting elixir. Remaining:

1. Capture a level-11 **unit HP** sample on A51 for Knight, Archers, Tesla,
   X-Bow, Skeletons and Electro Spirit, and the same tick on native 15.535.29.
   `tools/native_analysis/entity_hp_probe.py` currently uses `MatchConfig()`
   defaults (level 1); add a `--level` option (or pass
   `level_cap/minimum_card_level/king_tower_level=11`) before trusting it.
2. Compare ability state/charges for Hero Knight and the evolution cards.
3. Record the residual 15.535.29 vs 16.635.1 gap in this handoff and keep
   `ROADMAP.md` Phase 1 parity unchecked until it is written.
4. A51 report shape: `builds/native/a51-scripted-xbow-16.635.1-L11-seed1.json`.
   Native golden: `builds/native/xbow-golden-15.535.29-seed1.json`.

### 3. Then Phase 2 — host sizing

Use the sweep output, not a fresh guess. Official host sizing should stay at or
below **10 physical cores** (4 and 8 workers); 12 workers is the
oversubscription/knee point. Convert ticks/s to matches/s with the measured
average match length (~3,700–4,000 native ticks for the scripted X-Bow loop;
use the actual `soak`/determinism terminal ticks, not real time).

### 4. Only then Phase 3 — training

Do not start the long PPO run until unit-HP/ability parity is recorded. R0
elite-v3 is the chosen stack; FirstLight v1450 remains a benchmark/opponent.

**Phase 1 status:** deck/schema ✅, X-Bow config + tokens ✅, 20-match
determinism/legality ✅, model smoke ✅, A51 tower/card/elixir parity ✅,
A51 unit HP + abilities ⏳.

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
- How large is the 15.535.29 vs 16.635.93 balance/card gap? **Partially
  answered:** at level 11 the tower HP, deck ids/forms/costs and starting
  elixir match 16.635.1. Opening-hand RNG and unit HP/abilities remain.
- Is bridging FirstLight v1450 worth it, or train from scratch with the
  sandbox stack?
- Can the live canary use the existing A51/16.635.1 probe/touch path unchanged?

## Key paths

| Path | Purpose |
| --- | --- |
| `ROADMAP.md` | The single plan |
| `ROADMAP_CRFORGE.md` | Ultrafast simulator / CI oracle (reference) |
| `docs/native/PHASE0_BRINGUP.md` | Cold-start evidence, root causes, rootless runbook |
| `tools/native_bringup/preflight_bundle.py` | Static cold-deploy preflight (no root) |
| `tools/native_bringup/build_host_artifacts.sh` | Rebuild host JAR + bridge |
| `tools/native_bringup/build_local_overlay.py` | Patch the published bundle + manifest |
| `tools/native_bringup/run_rootless_worker.sh` | Run a worker via `bwrap` user namespace |
| `tools/native_bringup/verify_xbow_determinism.py` | X-Bow deck contract + per-tick determinism/legality harness |
| `tools/native_bringup/smoke_r0_model.py` | R0 elite-v3 PPO step + checkpoint save/restore smoke |
| `tools/native_bringup/run_phase0.py` | Detached 1 h soak + 4/8/12-worker sweep + evidence |
| `builds/native/phase0-*/` | Soak CSV, RSS/CPU summary, sweep report (gitignored) |
| `builds/native/xbow-determinism-20.json` | 20-seed determinism/legality report |
| `builds/native/xbow-golden-15.535.29-seed1.json` | Native 15.535.29 golden frames |
| `builds/native/a51-scripted-xbow-16.635.1-L11-seed1.json` | A51 16.635.1 level-11 fixed-script report |
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
