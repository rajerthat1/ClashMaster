# Session handoff — 2026-11-13 — Track B: CR Native Linux runtime

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

## Status 2026-11-13 (latest session): Phase 0 cold start PASSED without a VM

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

## Next session — Phase 1 coding (the runtime is already up)

### 0. Housekeeping (10–15 min, optional)

1. **1 h soak** with 4 workers if the strict Phase 0 gate is required:
   leave them up, record RSS/CPU every 10 min, confirm `ping` still works.
   (This session stopped at 23 min; 0 crashes, RSS flat, 100 matches clean.)
2. Run the active-worker sweep on 4/8/12 workers with
   `cr-native-sandbox/scripts/run_native_active_worker_sweep.py` and record the
   knee. Do not exceed physical cores for host-sizing numbers.

### 1. Start the workers (copy/paste)

```bash
cd /home/raj/Desktop/ClashMaster
export PYTHONPATH=.resource/cr-native-linux-bionic
B=builds/bundles/cr-native-linux-bionic-runtime-150535029-local-overlay
M=$B/BUNDLE-MANIFEST.json

# install once (already present in builds/rootless)
python3 -m cr_native_bionic doctor --bundle-dir "$B" --manifest "$M" \
    --system-root builds/rootless/system-root \
    --runtime-root builds/rootless/runtime --workers 4

for slot in 0 1 2 3; do
    LOG=/tmp/opencode/rootless-worker-$slot.log SLOT=$slot \
        PORT=$((39031 + slot)) \
        tools/native_bringup/run_rootless_worker.sh &
done

python3 -m cr_native_bionic smoke --bundle-dir "$B" --manifest "$M" \
    --runtime-root builds/rootless/runtime --base-port 39031
```

Stop by port (do not run `start` again on a busy port):

```bash
for p in 39031 39032 39033 39034; do
    pid=$(ss -ltnp | grep ":$p " | grep -oP 'pid=\K[0-9]+' | head -1)
    [ -n "$pid" ] && kill "$pid"
done
```

### 2. Phase 1 work items, in order

1. **Parameterise `training/schema.py`.** It still hardcodes `CARD_IDS` to the
   eight-card bootstrap deck and indexes hand/action features by that list.
   Replace it with a deck spec (ordered `(card_id, form)` entries), derive the
   feature indices from it, and keep the bootstrap deck as an explicit default
   so existing tests keep passing. Fail closed on an unknown card/form.
2. **Add the X-Bow deck config + token contract.** The deck is in the table
   below. Define explicit policy tokens for the three form cards instead of
   relying on slot order or `human_vs_ai.py`'s warning. Verify
   `NativeRoyaleEnv` returns the form flags and hand in the agreed order.
3. **Determinism harness.** Same replay seed + same action script → identical
   per-tick observation hash. Run 20 matches with the X-Bow deck; require
   `illegal: 0` and an action mask consistent with engine refusals.
4. **Golden validation vs A51/16.635.1.** Fixed script, compare card IDs,
   tower/unit HP, elixir, tick, hand and abilities. Record the 15.535.29 vs
   16.635.1 gap in this handoff; it drives the Phase 4 parity decision.
5. **Model smoke test.** Load the sandbox R0 elite-v3 stack, run one real
   forward/backward step and a checkpoint save/restore. Do **not** start the
   long PPO run until items 1–4 pass. FirstLight v1450 stays a benchmark and
   opponent, not a weight port.

**Exit criteria for the next session:** 20 matches, 0 errors, deterministic
reset, X-Bow deck plays legally, and one PPO step runs end-to-end.

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
`form_flags`. Two gaps remain before training:

- `training/schema.py` hardcodes `CARD_IDS` to the eight-card bootstrap deck
  and indexes hand/action features by that list; it must be parameterised for
  the X-Bow deck (and the 3 form cards) before any observation test.
- `human_vs_ai.py` warns that combined hero/evolution decks need an explicit
  token contract; define the policy token vocabulary explicitly rather than
  relying on slot order.

Deck order for the config: **X-Bow, Hero Knight, Evo Archers, Evo Tesla,
Skeletons, Electro Spirit, Fireball, The Log**. Keep the model decision
explicit in the config (R0 elite-v3 first; v1450 is a benchmark/opponent).

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
- What is the 4/8/12-worker knee on this host? (soak + sweep next).
- How large is the 15.535.29 vs 16.635.93 balance/card gap?
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
