# Native runtime bring-up tools

Helpers for the `cr-native-linux-bionic` host. They do not need root and do not
write outside the paths you give them. Everything here is version-pinned to
`15.535.29 / 150535029 / x86_64`.

## `preflight_bundle.py`

Static cold-deploy checks that `cr-native-bionic doctor` does not perform:

- archive size and SHA-256 against the bundle manifest (`BUNDLE-MANIFEST.json`
  by default, falling back to the repository manifest);
- executable bits on `linker64` and `dalvikvm64` inside the userland archive;
- `libandroid_runtime.so` in `system-etc.tar.gz:etc/public.libraries.txt`;
- root-owned (`uid/gid 0`) property-area files in `android-properties.tar.gz`
  (bionic's `prop_area::map_fd_ro` rejects anything else);
- `assets/assets.scdb` and `data/update/data_manifest.toml` in the worker
  template; `asset-pack.apk` is optional and not needed by a direct worker;
- `nativeRegisterAndroidRuntime` in `lifecycle-probe.jar` and in the exported
  symbols of `libnative_host_bridge.so`, plus the step/observe training entry
  points;
- the headless UI-locale guard byte cave in the bridge (the published bridge
  calls the locale registration and aborts the worker with a bogus
  pointer-sized allocation).

```bash
python3 tools/native_bringup/preflight_bundle.py \
    --bundle-dir builds/bundles/cr-native-linux-bionic-runtime-150535029-local-overlay
# JSON for CI:
python3 tools/native_bringup/preflight_bundle.py \
    --bundle-dir builds/bundles/cr-native-linux-bionic-runtime-150535029-local-overlay --json
```

Exit status is 0 only when every required check passes.

## `build_host_artifacts.sh`

Rebuilds the two host artifacts the published bundle predates:
`lifecycle-probe.jar` and `libnative_host_bridge.so` (with the headless
UI-locale fix). Needs the NDK, a JDK, `d8.jar` and a platform `android.jar`.

The bridge fix is
[`patches/0001-bridge-headless-ui-locale-guard.patch`](patches/0001-bridge-headless-ui-locale-guard.patch)
against `.resource/cr-native-sandbox`. If the source does not already contain
it, the script stops; apply it with
`git -C .resource/cr-native-sandbox apply <patch>` or rerun with
`APPLY_PATCH=1`.

```bash
ANDROID_JAR=/path/to/platforms/android-34/android.jar \
    tools/native_bringup/build_host_artifacts.sh
# -> builds/local-overlay/artifacts/{lifecycle-probe.jar,libnative_host_bridge.so}
```

## `build_local_overlay.py`

Turns the published bundle into a cold-startable **local overlay** under
`builds/` (never call it upstream):

- restores the executable bits;
- adds `libandroid_runtime.so` to `public.libraries.txt`;
- drops in the rebuilt JAR and bridge from `build_host_artifacts.sh`;
- adds `assets/assets.scdb` and `data/update/data_manifest.toml` extracted
  from the sandbox runtime ZIP's split asset pack;
- rewrites `BUNDLE-MANIFEST.json` and `SHA256SUMS.txt` so `verify-bundle`
  accepts the patched archives, then zips the result.

```bash
python3 tools/native_bringup/build_local_overlay.py \
    --source-bundle builds/bundles/cr-native-linux-bionic-runtime-150535029 \
    --sandbox-runtime-zip builds/bundles/cr-native-sandbox-runtime-150535029.zip \
    --bridge builds/local-overlay/artifacts/libnative_host_bridge.so \
    --probe-jar builds/local-overlay/artifacts/lifecycle-probe.jar
```

## `run_rootless_worker.sh`

Runs one installed worker from a user-owned `--system-root` via `bwrap`.
Android libc requires the property-area files to be owned by uid/gid 0; the
user namespace (`--unshare-user --uid 0 --gid 0`) maps the installer's own
files to root inside the namespace, so no host root is required and nothing
outside the install root is touched.

```bash
export PYTHONPATH=.resource/cr-native-linux-bionic
python3 -m cr_native_bionic install \
    --bundle-dir builds/bundles/cr-native-linux-bionic-runtime-150535029-local-overlay \
    --manifest builds/bundles/cr-native-linux-bionic-runtime-150535029-local-overlay/BUNDLE-MANIFEST.json \
    --system-root builds/rootless/system-root \
    --runtime-root builds/rootless/runtime --workers 4 --apply
python3 -m cr_native_bionic doctor ...   # expect hard_pass=true

tools/native_bringup/run_rootless_worker.sh --slot 0 --port 39031
```

Why this exists: the published `150535029` bundle passes `verify-bundle` and
`doctor` but fails every cold-start check. See
[`docs/native/PHASE0_BRINGUP.md`](../../docs/native/PHASE0_BRINGUP.md).

## `verify_xbow_determinism.py`

Phase 1 harness for the X-Bow 3.0 deck. It uses the explicit deck/token
contract in `.resource/cr-native-sandbox/training/deck_spec.py`, checks that
`NativeRoyaleEnv` reports the expected hand order and form flags, runs a
deterministic legal-action script, and hashes the full observation (grid +
scalars for both sides plus the canonical state) every tick. Each seed is run
twice; the two per-tick hash chains must match. Engine refusals with result
codes 3/4 are counted as gate refusals, anything else is `illegal`.

```bash
# workers already listening on 39031..39034
.venv/bin/python tools/native_bringup/verify_xbow_determinism.py \
    --matches 20 --ports 39031,39032,39033,39034 \
    --golden-output builds/native/xbow-golden-15.535.29-seed1.json
# -> builds/native/xbow-determinism-<UTC>.json
```

`--golden-output` writes the initial/sparse/terminal frames in the
`xbow-native-golden-v1` shape for the Phase 4 A51/16.635.1 parity comparison.

## `smoke_r0_model.py`

Runs the sandbox R0 elite-v3 stack on CPU: one synthetic rollout, one real PPO
update, then a checkpoint save/reload with a bit-exact state-dict check. It
does not start a worker, read a dataset, or use the GPU.

```bash
.venv/bin/python tools/native_bringup/smoke_r0_model.py --rich
# -> builds/native/r0-model-smoke.json
```
