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

## Why this is the path

The runtime already exists and is version-pinned:
Android 31 userland → `linker64` → `dalvikvm64 -Xint` → binderless JniHost →
`libnative_host_bridge.so` → original x86_64 `libg.so` → JSON-line TCP worker.
It supports up to 48 workers on ports `39031 + slot`, with `verify-bundle`,
`install`, `doctor`, `start`, `status`, `smoke`, `stop`.

Bundles (GitHub releases, not in git):

| Bundle | Size | SHA-256 |
| --- | ---: | --- |
| `cr-native-linux-bionic-runtime-150535029.zip` | 273,274,290 B | `e25f6484e888c175cadddaed4be9fbb69bf92d2f6f9b75f3f3c4c17629830db0` |
| `cr-native-sandbox-runtime-150535029.zip` | 899,751,022 B | `8d829f219455ad5cb48abba717eaac2ffa97e0d51c8c9dbc76d2ad91ed15bc28` |

`libg.so` for this runtime: `fa6704b83cb9c5b8eecb7b56c9671b834d636a3a6d9ac446e698e1262dc246ba`.

## What we know about throughput

- Pure engine: ~**74.7 µs/update** → ~13.4k ticks/s ≈ **2.2 matches/s per host
  process** before observations/policy.
- Published full loop (4.8M model, 4 matches/process, decisions every 4 ticks,
  25-core host + RTX 5090): 12 processes = **0.84 matches/s**, 24 = **0.93**.
- So **engine-only 10 matches/s is easy (5–8 workers)**; full training at 10 is
  an observation/host optimization problem.
- A51 context only: 2.1–2.8 matches/s sustained per big core, 0.9–1.2 per
  background/little lane; 3 lanes ≈ 4.1–5.0 capacity. Not the training host.

## Next session — Phase 0

1. **Provision a throwaway Ubuntu 22.04/24.04 x86_64 VM** with root, 8+ vCPU,
   16 GB RAM, 50 GB disk, and a snapshot. Do not install on the daily-driver
   Linux.
2. Download both bundles and verify SHA-256.
3. Install the runtime CLI:
   ```bash
   git clone https://github.com/IMAX9D/cr-native-linux-bionic.git
   cd cr-native-linux-bionic
   python3 -m venv .venv && . .venv/bin/activate
   pip install -e .
   ```
4. Verify, install, diagnose:
   ```bash
   cr-native-bionic verify-bundle --bundle-dir ./cr-native-linux-bionic-runtime-150535029
   sudo .venv/bin/cr-native-bionic install \
       --bundle-dir ./cr-native-linux-bionic-runtime-150535029 \
       --runtime-root /opt/cr-native-bionic --workers 8 --apply
   sudo .venv/bin/cr-native-bionic doctor \
       --bundle-dir ./cr-native-linux-bionic-runtime-150535029 \
       --runtime-root /opt/cr-native-bionic --workers 8
   # require hard_pass=true
   ```
5. Start 4 workers and smoke:
   ```bash
   sudo .venv/bin/cr-native-bionic start \
       --bundle-dir ./cr-native-linux-bionic-runtime-150535029 \
       --runtime-root /opt/cr-native-bionic --count 4 --base-port 39031
   cr-native-bionic status --count 4 --base-port 39031
   cr-native-bionic smoke --bundle-dir ./cr-native-linux-bionic-runtime-150535029 \
       --runtime-root /opt/cr-native-bionic --base-port 39031
   ```
6. Measure **engine-only** ticks/s per worker first (no policy).
7. Scale 4 → 8 → 12 workers; record CPU/RSS and the knee.
8. Write the numbers into this handoff or a short findings note before touching
   training code.

**Gate:** `doctor` hard-pass, `smoke` passes, one worker completes a match,
1 h with no crash.

## Then — Phase 1

- Install `cr-native-sandbox`; connect `NativeRoyaleEnv` to the workers.
- Configure the X-Bow 3.0 deck: X-Bow, Hero Knight, Evo Archers, Evo Tesla,
  Skeletons, Electro Spirit, Fireball, The Log.
- Exercise reset/observe/act/step; 20 matches; same seed → same observations.
- Validate observations against A51/16.635.1 goldens (card IDs, HP, elixir,
  tick, hand, abilities).
- Decide the model: sandbox R0 elite-v3, FirstLight v1450 benchmark, or a
  smaller fresh model.

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
the OS or future packages. That is why the runtime is for a **dedicated VM or
container with a snapshot**, not the daily driver. Uninstall by stopping the
workers and removing the created roots; a snapshot makes a bad install a
revert.

## Open questions

- Does the sandbox card catalog already contain the X-Bow deck, all forms,
  Hero Knight and Evo cards?
- What is the real engine-only and end-to-end throughput on our host?
- How large is the 15.535.29 vs 16.635.93 balance/card gap?
- Is bridging FirstLight v1450 worth it, or train from scratch with the
  sandbox stack?
- Can the live canary use the existing A51/16.635.1 probe/touch path unchanged?

## Key paths

| Path | Purpose |
| --- | --- |
| `ROADMAP.md` | The single plan |
| `ROADMAP_CRFORGE.md` | Ultrafast simulator / CI oracle (reference) |
| `docs/native/LINUX_BIONIC_RUNTIME.md` | Runtime evaluation, hashes, commands |
| `.resource/cr-native-linux-bionic/` | Runtime CLI source |
| `.resource/cr-native-sandbox/` | Native env, card catalog, self-play/PPO |
| `.resource/FirstLight-Zero/` | X-Bow recipe, v1450 benchmark |
| `.resource/native/16.635.1/reference/libg.so` | A51 parity reference |
| `docs/native/16.635.1/FINDINGS.md` | 16.635.1 evidence log |
| `tools/native_analysis/` | A51 probe tools (parity only) |

## Do not do

- Do not install the runtime on the daily-driver Linux.
- Do not build an AVD/MuMu/phone cluster or a custom loader unless a Phase 0/1
  gate fails.
- Do not train on `IL_Replay`.
- Do not make crforge the critical path.
- Do not run real-time matches as the training loop.
- Do not re-run `start` on a busy port (duplicate-process bug); check `ss` /
  `/proc/PID/cmdline` first.
- Do not start the main ladder before Phase 4.
