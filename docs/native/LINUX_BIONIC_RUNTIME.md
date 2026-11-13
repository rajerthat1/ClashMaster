# Linux Bionic runtime — the engine host

Status: **evaluated 2026-11-13; the chosen engine host**
Sources:
- <https://github.com/IMAX9D/cr-native-linux-bionic>
- <https://github.com/IMAX9D/cr-native-sandbox>

## What it is

`cr-native-linux-bionic` runs the **original Android `libg.so` as a plain Linux
process** — no AVD, no QEMU, no KVM, no Android emulator. It installs a minimal
Android 31 userland (`linker64`, `dalvikvm64 -Xint`, ART boot classpath, APEXes,
framework JARs, properties) and starts a binderless `JniHost` that loads the
game's x86_64 `libg.so`:

```
Linux kernel
  -> Android linker64
  -> dalvikvm64 -Xint
  -> Binderless JniHost
  -> libnative_host_bridge.so
  -> original x86_64 libg.so (15.535.29)
  -> JSON-line TCP worker, port 39031 + slot
```

`cr-native-sandbox` is the companion: `NativeRoyaleEnv` (`reset` / `observe` /
`act` / `step`), card catalog, self-play, PPO, expert collection, and a manifest
with exact RVAs and field offsets for the same build.

## Version and components

| Item | Value |
| --- | --- |
| Game version | **15.535.29** (`runtime_version` 150535029) |
| ABI | **x86_64** |
| Android userland | API 31 |
| `libg.so` SHA-256 | `fa6704b83cb9c5b8eecb7b56c9671b834d636a3a6d9ac446e698e1262dc246ba` |
| Workers | up to **48** slots per host |
| Ports | `39031 + slot` |
| Linux runtime bundle | 273,274,290 bytes, SHA-256 `e25f6484…30db0` |
| Sandbox runtime bundle | 899,751,022 bytes, SHA-256 `8d829f21…bc28` |

Both bundles are GitHub release assets; they are not in git. The runtime is
version-pinned and fails closed on hash/version mismatch. It is **not**
compatible with the 160402002 ARM64 build or our 16.635.1 client.

## Why this matters

- It is exactly the "extracted engine on Linux" we planned, already built and
  attested. **No loader, Bionic shim or app bootstrap to write** for 15.535.29.
- x86_64 means **no ARM translation**: any x86_64 Linux host with root works.
- The "update required" screen is irrelevant: the bundle ships its own
  `base.apk`, `libg.so` and assets.
- It provides the full worker lifecycle: `verify-bundle`, `install`, `doctor`,
  `start`, `status`, `smoke`, `stop`, with per-slot isolation and hash checks.
- `cr-native-sandbox` gives us a working native API and training stack.

## Throughput reality

The author's resident Linux report (2026-09-12, 25-core host + RTX 5090 D,
4.8M model, 4 matches per process, decisions every 4 ticks) measured:

| Processes × matches | Aggregate tick/s | Matches/s (÷6,068) |
| ---: | ---: | ---: |
| 1 × 4 | 594.3 | 0.098 |
| 4 × 16 | 1,926.9 | 0.318 |
| 8 × 32 | 3,450.1 | 0.569 |
| 12 × 48 | 5,090.8 | **0.839** |
| 16 × 64 | 5,470.8 | 0.902 |
| 24 × 96 | 5,626.4 | **0.927** |

Pure engine cost was ~**74.7 µs/update** in that run, i.e. ~13.4k ticks/s
(~2.2 matches/s) per host process before observations and policy. The report
attributes the end-to-end loss to observation construction (26% wall / 36% CPU),
host wrapper work (31.65%), and small-kernel CUDA scheduling — **not** `libg`
(14.9% of host CPU).

So:

- **Engine-only 10 matches/s is easy**: 5–8 worker processes at ~2.2 each.
- **Full PPO collection at 10 matches/s is not automatic**: the published
  sandbox loop does ~0.84–0.93 matches/s on 12–24 processes. Reaching 10
  end-to-end needs a leaner observation/feature path or ~10× the host.
- Our own resident protocol is lighter than the sandbox's 72-feature pipeline;
  the A51 engine-only test already shows the engine is not the bottleneck.

## Requirements and safety

- **Dedicated x86_64 Linux VM/container with root.** The installer creates
  `/apex`, `/system`, `/linkerconfig`, `/dev/__properties__` and
  `/data/local/tmp`. Do **not** install it on the daily-driver Linux boot.
- 8+ vCPU, 16 GB+ RAM, ~50 GB disk to start; scale workers with measured RSS.
- Python 3.11+, `tar`, `cp` (reflink support is a bonus).
- MIT license covers the deployment code; game assets/runtime are not
  redistributed and must be obtained legally.

## First-run checklist

1. **Provision a throwaway Ubuntu 22.04/24.04 x86_64 VM with root**, 8 vCPU /
   16 GB / 50 GB.
2. Clone and install the CLI:
   ```bash
   git clone https://github.com/IMAX9D/cr-native-linux-bionic.git
   cd cr-native-linux-bionic
   python3 -m venv .venv && . .venv/bin/activate
   pip install -e .
   ```
3. Download and verify the runtime bundle:
   ```bash
   # cr-native-linux-bionic-runtime-150535029.zip, 273,274,290 bytes
   sha256sum cr-native-linux-bionic-runtime-150535029.zip
   # e25f6484e888c175cadddaed4be9fbb69bf92d2f6f9b75f3f3c4c17629830db0
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
5. Start 4 workers, check, smoke:
   ```bash
   sudo .venv/bin/cr-native-bionic start \
       --bundle-dir ./cr-native-linux-bionic-runtime-150535029 \
       --runtime-root /opt/cr-native-bionic --count 4 --base-port 39031
   cr-native-bionic status --count 4 --base-port 39031
   cr-native-bionic smoke --bundle-dir ./cr-native-linux-bionic-runtime-150535029 \
       --runtime-root /opt/cr-native-bionic --base-port 39031
   ```
6. Measure **engine-only** ticks/s per worker first (no policy), then the full
   `NativeRoyaleEnv` loop. Record both; do not compare them to each other.
7. Scale 4 → 8 → 12 workers, watch CPU/RSS, and stop at the knee.

## Caveats

- **Published bundle is not startable (2026-11-13).** `verify-bundle` and
  `doctor` pass, but a fresh install does not start: `linker64`/`dalvikvm64`
  lack executable bits, `libandroid_runtime.so` is absent from
  `public.libraries.txt`, the host JAR/bridge predate the binderless JNI, the
  worker lacks `assets/assets.scdb` + `data/update/data_manifest.toml`, and the
  old bridge aborts in the headless UI-locale registration. Build the local
  overlay with `tools/native_bringup/build_local_overlay.py` and run
  `preflight_bundle.py` before spending a VM. The same document records the
  rootless `bwrap` path that cold-starts on a daily driver without root:
  [`PHASE0_BRINGUP.md`](PHASE0_BRINGUP.md).
- **15.535.29 only.** The A51 golden is 16.635.93; card/balance data differs.
  Use this for throughput/training, keep 16.635.1 for live parity.
- The sandbox's `NativeRoyaleEnv` is its own protocol, not our resident
  `batch-v1`. Bridging to FirstLight-Zero's collector needs an adapter.
- Known launcher issue: pinging a busy worker can time out and a second `start`
  can create a duplicate process. Do not re-run `start` on a busy port; check
  `ss`/`/proc/PID/cmdline`.
- Runtime and assets are user-supplied; do not commit them.

## How this fits the plan

| Step | Before | With this runtime |
| --- | --- | --- |
| Loader, shim, bootstrap, first match | 4–8 weeks | **1–2 days** (install + doctor + smoke) |
| Engine-only 10 matches/s | uncertain | 5–8 workers on an 8–16 vCPU host |
| Full training 10 matches/s | 2–4 months | 3–6 weeks: measure, then optimize observations or scale the host |
| 16.635.1 support | the long pole | A51 parity only; port later if the 15.535.29 gap matters |

**Decision:** this is the engine host. If it passes `doctor` and the smoke test,
all training runs here. The A51/16.635.1 probe is the parity/live reference.
