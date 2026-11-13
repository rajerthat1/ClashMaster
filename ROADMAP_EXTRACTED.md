# Extracted-engine Linux roadmap — 10 matches/s

Status: **active plan, drafted 2026-11-13**
Primary track: **B — clapha-type Linux `libg.so` loader**
Fallback track: **A — Linux AVD + the real APK**
Target: **10 matches/s aggregate** with real card actions and observations
Reference lane: **1.80 matches/s** (A51, one engine, 8 resident slots, control lane on a big core)

> This file is the plan for moving training off the phone. The phone remains the
> golden reference and a verified lane. The main repo plan is still
> [`ROADMAP.md`](ROADMAP.md).

---

## 0. What "extracted engine" means here

The game APK ships **only `arm64-v8a` and `armeabi-v7a` `libg.so`** — there is no
x86/x86_64 build. So "running the engine on Linux" is one of:

| Route | What runs | Speed | Difficulty |
| --- | --- | --- | --- |
| **B1. Native ARM64 Linux** | The ARM64 `libg.so` in a Linux process on an ARM64 host | ~1.5–2.5 matches/s per core | High (loader + Bionic shim + app bootstrap) |
| **B2. x86_64 + QEMU user-mode** | The same `libg.so` under `qemu-aarch64` | ~0.3–0.8 matches/s per core | High + emulation overhead |
| **B3. Full-system ARM emulation** | An ARM64 Android image under QEMU | ~0.1–0.2 matches/s per core | Highest; rejected |
| **A. Linux AVD** | The real APK in a rooted x86_64 AVD with KVM + ARM translation | ~0.5–4.9 matches/s per engine | Medium; FirstLight-proven |

Track B reuses the probe's offset knowledge but replaces the app with a host
harness. Track A reuses the whole APK and probe unchanged.

---

## 1. Throughput: FirstLight vs daybreak/clapha

These are **end-to-end training collection** numbers, not an engine microbenchmark.

| Stack | Published rate | Hardware | Per concurrent game |
| --- | ---: | --- | ---: |
| **FirstLight-Zero** | 1,024 episodes / 104 s = **9.85 matches/s** | 2 engine processes × 8 resident slots, 1 GPU | ~1.6 s/game; ~0.62 matches/s per slot |
| **daybreak-cr / clapha, peak** | 36–48k games/h = **10–13.3 matches/s** | 34 collectors (RTX 5090) + 50 collectors (Students) + 4 decision servers, 4 GPUs | ~6–10 s/game; ~0.10–0.16 matches/s per collector |
| **daybreak-cr, earlier** | 14–17k games/h = **3.9–4.7 matches/s** | 13 collectors on a 4th A100, fewer actors | ~0.09 matches/s per collector |

**Reading:**

- **Absolute peak is comparable** (daybreak slightly higher at its largest
  configuration), but daybreak used **~5× more collectors and 4 GPUs**.
- **FirstLight is far more efficient per lane and per GPU** (~0.62 vs ~0.1–0.16
  matches/s per concurrent game).
- This is **not proof that clapha's engine is slower**. daybreak's documented
  bottleneck was the decision-server QoS (16 CPUs), and its collectors were at
  25–30% of a core. The stack, not the engine, dominated.
- FirstLight's own README credits daybreak's asynchronous framework for a **1.5×
  PPO throughput** improvement, so the two are complementary.

**Planning numbers for this project:**

| Host | Per-lane estimate |
| --- | ---: |
| A51 big core (measured) | **1.80 matches/s** |
| A51 little core (measured) | 0.62 matches/s |
| Desktop x86 / modern ARM core, native | 1.5–2.5 matches/s |
| x86 + QEMU user-mode | 0.3–0.8 matches/s |
| x86_64 AVD + ARM translation | 0.5–4.9 matches/s per engine (must measure) |

So 10 matches/s means roughly **6–8 native ARM64 cores**, **12–25 QEMU
processes**, or **2–4 efficient AVD engines**. Core count, not architecture, is
the real budget.

---

## 2. Hardware: native ARM cluster on student credits

Track B needs **native ARM64 Linux**, not Android, and **does not need KVM** —
which is exactly what cloud ARM VMs are good at. Track A *does* need KVM, and
cloud ARM VMs usually do **not** expose nested virtualization, so Track A stays
on the laptop/MuMu or a phone farm.

| Option | Spec | Approx. cost | 10 matches/s? |
| --- | --- | --- | --- |
| **Hetzner CAX31** | 8 shared Ampere vCPU, 16 GB | **€21/mo** (~$23) | Likely (8 × ~1.5) |
| **Hetzner CAX41** | 16 shared Ampere vCPU, 32 GB | **€41/mo** (~$45) | Comfortable (15–25) |
| Oracle Always Free A1 | 2 OCPU / 12 GB (halved June 2026) | **free** | No (~3–5); good smoke test |
| Oracle $300 trial / PAYG A1 | 8–16 OCPU, 32–64 GB | credits / ~$0.01/OCPU-h | Yes, for ~1–2 months |
| GitHub Student Pack | Azure $100, DigitalOcean $200, GCP $300, AWS Educate | credits | Yes, 1–2 month pilot |
| Azure Ampere / GCP Tau T2A / AWS Graviton | 8–16 ARM vCPU | credits/spot | Yes |
| Apple silicon Mac (M-series) | 8–12 cores, native Linux VM | owned hardware | Yes (12–18) |
| Raspberry Pi 5 cluster | 4× A76 2.4 GHz, 8–16 GB each | ~$100/board | 2 boards ≈ 12–16 |

**Recommendation:**

1. **Hetzner CAX31** for the first paid 10 matches/s target (~€21/mo). It is the
   cheapest way to buy ARM cores; check stock (CAX is EU-only and sometimes
   unavailable).
2. **Oracle Always Free** for the loader smoke test (2 cores is enough to prove
   the engine boots).
3. **GitHub Student Pack credits** for a 1–2 month Azure/GCP/AWS ARM pilot if
   you do not want to pay out of pocket.
4. An **Apple silicon Mac** is the best host if you already own one.

Memory budget: ~700 MB per engine process with 8 resident slots. 8 lanes ≈
6 GB; 16 lanes ≈ 11 GB. CAX31 (16 GB) fits 8–12 processes; CAX41 (32 GB) fits
20+.

---

## 3. Track B — Linux `libg.so` loader (primary)

### Goal

Run `libg.so` as a Linux process, drive full matches with the existing resident
protocol, and train on a Linux host at 10 matches/s.

### Design

```
host process
  ├── ELF loader + Bionic/glibc shim   (project-qemu / PenguDroid / clapha-style)
  ├── libg.so (ARM64)                  real battle engine
  ├── content + assets                 csv_logic, assets.scdb, fingerprints
  ├── probe or host API                resident protocol, actions, observations
  └── training collectors              FirstLight-Zero / daybreak loop
```

Two execution backends:

- **B1 — native ARM64 Linux (preferred).** No translation. Start here if a cloud
  ARM host or Apple silicon is available.
- **B2 — x86_64 + `qemu-aarch64` user-mode (development fallback).** Works on the
  laptop; expect 2–5× slowdown. `project-qemu` proves the load path.

Rejected: full-system ARM emulation (B3), and translating the engine to x86.

### Starting artifacts

| Artifact | Use |
| --- | --- |
| `PeterHackz/project-qemu` | Public ARM64 ELF loader for `libg.so`; maps, relocates, fakes NDK symbols, runs init |
| `PenguDroid` | Mature Bionic→glibc shim, APK native-lib loading, assets, EGL/GLES, ALooper, Vulkan |
| `sober-oss` | ARM64→x86 Android binary translator with Bionic shims (reference) |
| `clapha-engine` | Private; daybreak's `CrxEnv(libg.so, workdir, assets)` shows the target API |
| This repo's probe | 15.535.13 and 16.635.1 offsets, resident protocol, actions, HP, abilities |

**Reuse the probe.** `libcrprobe.so` is already a self-contained ARM64 control
server that hooks `libg.so` and speaks the resident protocol. If the shim can
load it alongside the engine, Track B skips writing a new engine API. The
blocker is its JNI/Android dependencies and the app bootstrap; a
`CR_PROBE_STANDALONE` path is a concrete deliverable.

### Content: the update screen is not a blocker

Both APKs already bundle the engine's data under `assets/`:
`assets/assets.scdb`, `assets/csv_logic/`, `assets/fingerprint.json` and
`assets/build.tag`. The "update required" loading screen is a server/client
version gate, not missing data.

| APK | Bundled content | Local path |
| --- | --- | --- |
| `nr_16.635.1_release_951fe421.apk` | `fingerprint.json` version **16.635.1**, 18,151 files | already extracted at `.resource/cluster/decoded-16.635.1-pristine/assets` (902 MB) |
| `nr_15.535.13_apkpure.apk` | `fingerprint.json` version **15.535.3** | extract from the APK if needed |

FirstLight's 15.535 lab used the downloaded **15.535.86** update; the A51's
live client uses **16.635.93**. For the loader proof-of-concept the bundled
`assets/` directory is enough — point the engine's asset manager at it. For
training parity with the live client you may later want the newer downloaded
update, which lives in the app's private `files/update/` (not readable without
root). Two ways to get it:

1. build the APK with `android:debuggable="true"`, update-install (data is
   kept), then `adb shell run-as nullsroyale.rel.free` to tar `files/update`;
2. `adb backup` — the manifest has `allowBackup="true"` — if the Android 13
   build still permits it.

The engine only needs a consistent `fingerprint.json` + `csv_logic/` +
`assets.scdb` set; it does not need the server.

### Phases

#### B0 — Recon and baseline (2–4 days)

- Target build: **16.635.1 first**. This repo's probe, offsets, actions, HP and
  ability path are already verified on 16.635.1, the A51 golden is 16.635.1, and
  its APK content is already extracted. Keep 15.535 only as the
  clapha/daybreak comparison build.
- Acquire `clapha-engine` if possible; otherwise clone `project-qemu` +
  `PenguDroid`.
- Set up an ARM64 rootfs with `qemu-aarch64-static` (or boot an ARM64 cloud VM).
- **Gate B0:** `libg.so` maps, relocates, and reaches `DT_INIT`/`JNI_OnLoad`
  without a fatal missing symbol.

#### B1 — Minimal libg boot (1–2 weeks)

- Bionic shim coverage: `libc`, `libm`, `liblog`, `libandroid`, `libEGL`,
  `libGLESv2`, `libc++_shared`; missing symbols such as `__sF`, `__strlen_chk`.
- Fake `JNIEnv`, system properties, `AAssetManager`, `ALooper`.
- Call one known internal function and read engine memory.
- **Gate B1:** init routines complete; one engine function returns a sane
  value; 60-second idle with no crash.

#### B2 — Engine bootstrap and content (2–5 weeks — the hard part)

Reconstruct the app's startup without `GameApp`/Titan:

- content-context construction (`0xdcc180` on 15.535, `0xef92f8` on 16.635),
- content-ready gate (`+0x6e8` on 15.535, `+0x720` on 16.635),
- `assets.scdb` / `csv_logic` load,
- `GameStateManager` allocation and setup (`0x230` on 15.535, `0x240` on 16.635),
- deck/card/battle creation.

Reference implementation: the probe's existing headless `configure` /
`create_match` path.

- **Gate B2:** create a headless manager, load a match, step 100 ticks, no crash.
- **Pivot rule:** if not met ~4 weeks after B1, switch primary effort to Track A.

#### B3 — Drive matches (1–3 weeks)

Try in order:

1. Load the existing `libcrprobe.so` under the shim, start its control socket,
   reuse the whole verified protocol.
2. Otherwise write a host API in the clapha/`CrxEnv` style.

- **Gate B3:** a full 6,068-tick match with real actions, observations, HP and
  abilities; validate against A51 goldens.

#### B4 — Throughput and host sizing (1–2 weeks)

- Measure one process on B1 and B2.
- Scale with a process launcher: one process per core, ports `26789 + N`,
  `debug.cr.engine_cpu_map` or `taskset`.
- Pick the host from §2.
- **Gate B4:** 10 matches/s for 10 minutes, 0 crashes.

#### B5 — Training integration and soak (1–2 weeks)

- Point FirstLight-Zero collectors at the loader (8 resident slots per engine).
- Validate 15.535 → 16.635 card IDs, forms, elixir/tick, abilities, observations.
- **Gate B5:** 50-round PPO/IL smoke (`illegal/truncated/rejected = 0`) and a
  1-hour soak.

### Track B timeline

| Scenario | First playable match | 10 matches/s |
| --- | --- | --- |
| `clapha-engine` obtainable | 1–2 weeks | **6–10 weeks part-time** |
| Public loaders only | 2–4 weeks | **2–3 months part-time** |
| From scratch, 16.635 only | 4–8 weeks | **4–6 months** |

### Track B risks

1. **App bootstrap/content** — Java/Titan entanglement is the biggest schedule
   risk.
2. **Bionic coverage** — solvable; PenguDroid has already mapped most of it.
3. **Threads** — `libg` starts ~70 threads; raise `ulimit -u` (daybreak hit this
   on Slurm).
4. **Protection** — Null's Royale is section-stripped but statically
   disassemblable (our prologues work), so easier than official Arxan builds.
5. **Throughput** — QEMU user-mode may force a bigger host; native ARM64 is the
   clean answer.

---

## 4. Track A — Linux AVD (fallback)

This is the FirstLight-proven path: rooted x86_64 Android guest(s) with KVM and
ARM translation, running the real APK and the existing probe unchanged.

### A0 — Feasibility spike (2–3 days)

- Boot one rooted AVD with an ARM-capable system image and the compatibility
  libraries (`CR_EMULATOR_COMPAT`).
- First boot the original 16.635.1 APK to download content; then install our
  offline APK with `tools/prepare_training_guest.py`.
- `configure_emulator_engine_guests.py --engines 1`; confirm `production_ready`.
- Run one match; measure `resident_batch_throughput.py --actions --slots 8`.

**Gate A0:** hooks install and a match runs under translation. If not, stop Track
A and use the phone farm/MuMu.

### A1 — Single-guest multi-engine (3–5 days)

- Build the cluster APK (4–6 engine processes) with `build_cluster_apk.sh`.
- Clone the prepared userdata; run 4–6 engines in one guest, one per vCPU.
- Measure aggregate with `resident_batch_throughput.py --ports ...`.

**Gate A1:** near-linear scaling, no crashes, 1-hour soak.

### A2 — Multi-guest cluster (1 week)

- Use `native_runner/training/v4/launch_ppo_engine_node.sh`: G guests × E
  engines, each guest pinned to a host CPU pool.
- Laptop (12 threads): 2–3 guests ≈ 3.6–5.4 matches/s.
- 32–64-vCPU x86 host: 6–8 guests ≈ 10–14 matches/s.

**Gate A2:** ≥10 matches/s for 10 minutes.

### A3 — Training integration (1–2 weeks)

Same as B5.

### A4 — Productionize (1–2 weeks)

Autostart/restart, port mapping, monitoring, crash recovery, thermal guard,
multi-hour soak.

### Track A timeline

**4–6 weeks part-time**; first go/no-go in **2–3 days**.

### Track A notes from the A51 (2026-11-13)

These apply to any Android host:

- **CPU affinity matters most.** One engine lane on a big core is **1.80
  matches/s**; on a little core it is **0.62**. Android's default
  `engine N -> cpu N` mapping is wrong on heterogeneous phones. Use
  `debug.cr.engine_cpu_map` (probe) or `taskset` (AVD).
- **Android cpusets throttle background engines.** Only the top app gets all
  CPUs; background processes fall to cpu0–1. On the A51, engine 0 was `top-app`
  (0–7) and engine 1 `background` (0–1).
- **Cold ready needs the activity resumed.** Engines launched while covered may
  never reach `coldReady`; launch and verify them one at a time.
- **Memory:** one engine with 8 slots is ~690 MB RSS. The A51 has 3.6 GB total.

---

## 5. Shared work

- **Target build.** Track B targets **16.635.1**: the probe, offsets, actions,
  HP and ability path are already verified there, the A51 golden is 16.635.1,
  and its APK content is extracted locally. Use 15.535.13 only for
  clapha/daybreak comparisons.
- **Probe reuse.** The same resident protocol and offset tables serve both
  tracks.
- **Data validation.** Card IDs, forms, elixir, tick, abilities and HP must match
  A51 captures before training results are trusted.
- **Training loop.** FirstLight-Zero already expects 8 resident slots per engine;
  daybreak's async collector is the reference for scaling.

---

## 6. Decision gates and pivot rules

| Gate | Question | If no |
| --- | --- | --- |
| B0 | Does `libg.so` load and init on Linux? | Try the other loader; if still no after 1 week, reassess |
| B1 | Does it survive 60 s idle and one call? | Fix shim coverage; time-box 1 week |
| B2 | Does a headless match start? | **Pivot to Track A** (loader becomes background bet) |
| B3 | Full match with actions/observations? | Pivot to Track A |
| B4 | 10 matches/s on a sized host? | Bigger host or Track A |
| A0 | Do the probe hooks survive translation? | Phone farm / Windows MuMu |
| A2 | 10 matches/s on the AVD cluster? | Cloud x86 or phone farm |

**Rule of thumb:** if Track B has no headless match 6 weeks after B0, Track A
becomes primary.

---

## 7. First 10 working days (Track B)

1. **Days 1–2:** get `clapha-engine` if possible; otherwise clone `project-qemu`
   + `PenguDroid`; build the ARM64 rootfs; confirm `qemu-aarch64` runs a
   hello-world.
2. **Days 3–4:** load **16.635.1** `libg.so`; reach `DT_INIT`; log every missing
   symbol and build the shim list. Point the engine's asset manager at
   `.resource/cluster/decoded-16.635.1-pristine/assets/` (already extracted from
   the APK; no update download needed).
3. **Days 5–7:** get `JNI_OnLoad` and the content-context constructor invoked;
   log the first successful internal call.
4. **Days 8–10:** attempt headless `GameStateManager` creation using the probe's
   existing code as the reference. Record exactly which Java/Titan dependencies
   block you.
5. **Day 10 decision:** continue Track B, or pivot to Track A with a known cost.

In parallel, spend 2 days on Track A0 so the fallback number exists.

---

## 8. Cost model (rough)

| Host | Monthly | Expected matches/s | Notes |
| --- | ---: | ---: | --- |
| Laptop (12 threads, QEMU) | $0 | 3–8 | Development |
| Oracle Always Free (2 ARM cores) | $0 | 3–5 | Smoke test |
| Hetzner CAX31 (8 ARM cores) | €21 | 8–14 | Best first target |
| Hetzner CAX41 (16 ARM cores) | €41 | 15–25 | Comfortable |
| Oracle A1 16 OCPU (PAYG) | ~$120 | 20–30 | $300 trial ≈ 2.5 months |
| Student credits (Azure/GCP/AWS ARM) | credits | 10–25 | 1–2 month pilot |
| Apple silicon Mac | owned | 12–18 | Best if available |

Cloud ARM VMs generally do not expose `/dev/kvm`, so they suit **Track B**, not
Track A. Track A runs on the laptop (KVM), Windows MuMu, or a phone farm.

---

## 9. References

| Path / link | Purpose |
| --- | --- |
| [`ROADMAP.md`](ROADMAP.md) | Main repo plan |
| [`HANDOFF.md`](HANDOFF.md) | Current state and next actions |
| `docs/native/16.635.1/FINDINGS.md` | Evidence log for every offset |
| `native_runner/probe/` | Probe source; reuse for Track B |
| `build_cluster_apk.sh` | Linux multi-process cluster APK build |
| `launch_cluster_a51.sh` | A51 engine-cluster launch and readiness |
| `tools/native_analysis/resident_batch_throughput.py` | Single- and multi-engine throughput |
| `tools/prepare_training_guest.py` | Track A guest preparation |
| `native_runner/training/v4/configure_emulator_engine_guests.py` | Track A engine attestation |
| `native_runner/training/v4/launch_ppo_engine_node.sh` | Track A multi-guest cluster |
| `PeterHackz/project-qemu` | ARM64 `libg.so` loader |
| `PenguDroid` | Bionic→glibc shim and APK native-lib loader |
| `Jason-XII/daybreak-cr` | clapha-engine API and async training reference |
| `Jaasssoooonnnnn/FirstLight_CR` | Linux AVD and cluster reference |
