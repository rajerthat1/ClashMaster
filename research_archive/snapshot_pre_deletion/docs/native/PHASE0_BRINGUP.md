# Phase 0 bring-up — findings and runbook

Status: **runtime cold-starts and completes matches on this Linux host
(2026-11-13, no VM, no host root).** Plan: [`ROADMAP.md`](../../ROADMAP.md)
Phase 0. The published bundle is still broken as shipped; use the local overlay
built by `tools/native_bringup/build_local_overlay.py`.

This note records (1) what was wrong with the published `150535029` bundle,
(2) the root causes and fixes, and (3) the exact rootless procedure used on the
daily driver.

## 1. TL;DR

- Both release assets download and match the documented size and SHA-256:
  engine `e25f6484…30db0`, sandbox `8d829f21…bc28`. `verify-bundle` passes.
- The published bundle fails seven cold-start checks. Most are packaging
  defects; the last two only appear on a blank instance:

  | Check | Published bundle | Fix |
  | --- | --- | --- |
  | `linker64` / `dalvikvm64` executable bit | absent (`0666`) | `chmod +x` in the userland archive |
  | `public.libraries.txt` exposes `libandroid_runtime.so` | absent | append the line |
  | worker `assets/assets.scdb` | absent | extract from the sandbox asset pack |
  | worker `data/update/data_manifest.toml` | absent | extract from the sandbox asset pack |
  | host JAR calls `nativeRegisterAndroidRuntime` | absent | rebuild from sandbox source |
  | host bridge exports the JNI entry points | absent | rebuild from sandbox source |
  | headless UI-locale guard | bridge calls the locale registration | one-cave bridge patch |

- `asset-pack.apk` is **not** required by a direct worker. The 886 MB split is
  only relevant to the AVD/Windows runtime. Cold-start was verified with and
  without it; only `assets.scdb` + `data_manifest.toml` matter.
- The property-area failure (`__system_properties_init()` = `-1`) is **solved**:
  bionic's `prop_area::map_fd_ro()` rejects property files whose `st_uid` or
  `st_gid` is not 0. A `--system-root` install made by an ordinary user owns
  the files, so it fails. Either install as root or run the worker in a user
  namespace with `--unshare-user --uid 0 --gid 0`.
- The old bridge aborted after data loading with
  `std::bad_alloc` from a ~134 TB allocation. The allocation is the UI-locale
  registration at `0x1AE0270`, called from the bridge's code cave at
  `0x180B1E5` (`call` at `0x180B1F0`). The binderless Context makes the
  registration compute a pointer-sized length. The fixed cave jumps straight
  to the resume point (`0x14ABA2A`), as the null branch already did.

## 2. What was verified (no root, host untouched)

Bundle verified locally:

```text
cr-native-linux-bionic-runtime-150535029.zip
  273,274,290 B
  e25f6484e888c175cadddaed4be9fbb69bf92d2f6f9b75f3f3c4c17629830db0  (match)
  verify-bundle: 8 archives, 273,268,400 B, libg fa6704b8…46ba  (pass)
cr-native-sandbox-runtime-150535029.zip
  899,751,022 B
  8d829f219455ad5cb48abba717eaac2ffa97e0d51c8c9dbc76d2ad91ed15bc28  (match)
  runtime/x86_64-libs/libg.so sha256 fa6704b8…46ba  (match)
```

Preflight on the published bundle fails; on the local overlay it passes:

```bash
python3 tools/native_bringup/preflight_bundle.py \
    --bundle-dir builds/bundles/cr-native-linux-bionic-runtime-150535029
# RESULT: FAIL

python3 tools/native_bringup/preflight_bundle.py \
    --bundle-dir builds/bundles/cr-native-linux-bionic-runtime-150535029-local-overlay
# RESULT: PASS
```

The overlay bundle directory under `builds/` is gitignored and is not an
upstream artifact.

### 2.1 Property area — root cause

bionic's `prop_area::map_fd_ro()` (Android 12 / API 31) returns `nullptr`
unless every property file is `uid 0`, `gid 0`, and not group/other writable:

```cpp
if ((fd_stat.st_uid != 0) || (fd_stat.st_gid != 0) ||
    ((fd_stat.st_mode & (S_IWGRP | S_IWOTH)) != 0) ||
    (fd_stat.st_size < sizeof(prop_area))) {
  return nullptr;
}
```

`android-properties.tar.gz` stores `root/root` ownership, so a real root
install is fine. A fake-root install (`--system-root DIR` as user `raj`)
produces `raj/raj`, and `__system_properties_init()` returns `-1` even though
the files are readable. This was previously misdiagnosed as an unexplained
bundle/property-extraction defect.

Rootless fix used here (no host writes):

```bash
bwrap --unshare-user --uid 0 --gid 0 --bind "$SYSTEM_ROOT" / ...
```

Inside the user namespace the installer's own uid is mapped to 0, so the
property files stat as root-owned and init returns 0.

### 2.2 Headless UI-locale guard — root cause

The Sep-14 bridge installs this cave at `0x180B1E5`:

```asm
test rdi, rdi            ; null container -> cleanup
je   +0x10
cmp  qword [rdi], 0      ; null registry -> cleanup
je   +0x0A
call 0x1AE0270           ; UI locale registration
jmp  0x14ABA2A           ; resume
jmp  0x14ABA2A           ; cleanup
```

On the valid path the registration allocates with a bogus, pointer-sized
length (observed `0x7fff…`-sized `new`), libc++ throws `std::bad_alloc`, and
the worker aborts. The registration is presentation-only, so the fixed cave
replaces the `call` with a jump to the same resume address:

```asm
test rdi, rdi
je   +0x10
cmp  qword [rdi], 0
je   +0x0A
jmp  0x14ABA2A           ; skip registration
jmp  0x14ABA2A
jmp  0x14ABA2A
```

The bridge source now carries this cave and validates it before patching
(`android_probe/native/jni_bridge.cpp`, `binderless_ui_locale_container_*`).
`preflight_bundle.py` fails any bridge that still contains the legacy call.

### 2.3 Missing worker assets

`assets/assets.scdb` and `data/update/data_manifest.toml` come from
`cr-native-sandbox-runtime-150535029.zip` →
`runtime/apks/split_install_time_asset_pack.apk` (`assets/assets.scdb`,
`assets/data_manifest.toml`). Cold-start was tested:

| Removed | Result |
| --- | --- |
| `asset-pack.apk` | starts and completes a match |
| `assets/assets.scdb` | segfault during GameMain/data load |
| `data/update/data_manifest.toml` | segfault during GameMain/data load |

## 3. Local overlay

`tools/native_bringup/build_local_overlay.py` applies all six fixes and
rewrites the manifest. It does not rebuild the host artifacts itself; use
`tools/native_bringup/build_host_artifacts.sh` first:

```bash
# Host artifacts need the SDK platform android.jar (not in the repo).
ANDROID_JAR=/path/to/platforms/android-34/android.jar \
    tools/native_bringup/build_host_artifacts.sh

python3 tools/native_bringup/build_local_overlay.py \
    --source-bundle builds/bundles/cr-native-linux-bionic-runtime-150535029 \
    --sandbox-runtime-zip builds/bundles/cr-native-sandbox-runtime-150535029.zip \
    --bridge builds/local-overlay/artifacts/libnative_host_bridge.so \
    --probe-jar builds/local-overlay/artifacts/lifecycle-probe.jar
```

Result (this host): 276,545,837 B ZIP,
sha256 `a4bb0248dc5ee773aee8ac56284f705369cd2c20d510c0f69a1b095cb2ae1048`.
Bridge `3e51fd43…d967`, JAR `03fcfa37…4d86`.

## 4. Rootless runbook (used on the daily driver)

No VM, no host root, no writes outside `builds/rootless/`.

```bash
export PYTHONPATH=.resource/cr-native-linux-bionic
B=builds/bundles/cr-native-linux-bionic-runtime-150535029-local-overlay
M=$B/BUNDLE-MANIFEST.json

python3 -m cr_native_bionic verify-bundle --bundle-dir "$B" --manifest "$M"
python3 -m cr_native_bionic install \
    --bundle-dir "$B" --manifest "$M" \
    --system-root builds/rootless/system-root \
    --runtime-root builds/rootless/runtime \
    --workers 4 --apply
python3 -m cr_native_bionic doctor \
    --bundle-dir "$B" --manifest "$M" \
    --system-root builds/rootless/system-root \
    --runtime-root builds/rootless/runtime --workers 4
# expect hard_pass=true

for slot in 0 1 2 3; do
    LOG=/tmp/opencode/rootless-worker-$slot.log SLOT=$slot \
        PORT=$((39031 + slot)) \
        tools/native_bringup/run_rootless_worker.sh &
done
```

`start` is not used: the upstream CLI `start` hardcodes absolute `/apex`,
`/system` paths and has no `--system-root`. `run_rootless_worker.sh` is the
rootless equivalent and runs the same command line inside `bwrap`.

### 4.1 Recorded result

- `preflight_bundle.py`: **PASS** on the overlay.
- `verify-bundle`: 8 archives, 276,550,763 B, libg `fa6704b8…46ba`.
- `install --workers 4 --apply`: 4 slots created.
- `doctor`: **hard_pass=true**, `missing_system_files=[]`.
- 4 workers on 39031–39034: all `ping` ok; RSS ≈ 335 MB each.
- One worker: full match, terminal tick **6147**
  (`native_tiebreak_exact_draw`, crowns `[0, 0]`).
- One worker, no policy, steps of 100 ticks, installed root on NVMe:
  6,100 ticks in **0.74 s** ≈ **8.3k ticks/s** ≈ **1.33 matches/s**.
- 4 concurrent full matches: wall 0.89 s, aggregate **32.6k ticks/s** ≈
  **5.3 matches/s**; per worker **1.33 matches/s**.
- **100 consecutive matches** (4 workers × 25, `reset` between): zero failures,
  **4.63 matches/s** sustained including reset, RSS flat at ~335 MB/process.
- Earlier single-worker run on the `/tmp` tmpfs fake root was ~0.81
  matches/s; the root filesystem matters.

## 5. VM runbook (only if a real root install is wanted)

Provision Ubuntu 22.04/24.04 x86_64 with root, 8+ vCPU, 16 GB RAM, 60 GB disk
and a snapshot. Then:

```bash
sudo apt-get update && sudo apt-get install -y python3-venv git unzip tar bwrap
# copy the local overlay bundle + manifest
python3 -m venv .venv && . .venv/bin/activate
pip install -e /path/to/cr-native-linux-bionic
sudo .venv/bin/cr-native-bionic verify-bundle --bundle-dir "$B" --manifest "$M"
sudo .venv/bin/cr-native-bionic install --bundle-dir "$B" --manifest "$M" \
    --runtime-root /opt/cr-native-bionic --workers 8 --apply
sudo .venv/bin/cr-native-bionic doctor --bundle-dir "$B" --manifest "$M" \
    --runtime-root /opt/cr-native-bionic --workers 8
# require hard_pass=true, then start/status/smoke
```

A root install writes `/apex`, `/system`, `/linkerconfig`,
`/dev/__properties__` and `/data/local/tmp/cr-native-direct-N`. It does not
touch `/boot`, the kernel, GRUB or partitions. Still do not run it as root on
a daily driver; use the rootless path above there.

## 6. Throughput measurement (Phase 0 / Phase 2)

Active-worker sweep, no policy:

```bash
cd cr-native-sandbox
.venv/bin/python scripts/run_native_active_worker_sweep.py \
    --ports 39031-39042 --tiers 4,8,12 \
    --warmup-seconds 20 --measure-seconds 180 \
    --decision-ticks 1 --action-mode deterministic-legal \
    --output /tmp/cr-native-sweep.json
```

Record ticks/s, derived matches/s (`ticks / 6068`), per-process RSS and CPU, and
the knee. Do not compare engine-only numbers with the published PPO loop
(0.84–0.93 matches/s on 12–24 processes).

## 7. Gate

- [x] Preflight passes on the exact bundle ZIP (local overlay).
- [x] `doctor` hard-pass (rootless install).
- [x] 4 workers `ping`/`status` OK.
- [x] One worker completes a match (tick 6147, terminal).
- [ ] 1 h with 4 workers and no crash; record RSS/CPU.
- [x] Engine-only ticks/s and matches/s per worker recorded in
      [`HANDOFF.md`](../../HANDOFF.md) (1.33 matches/s per worker;
      5.3 matches/s with 4 workers; 100 matches stress-clean).
