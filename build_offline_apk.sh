#!/usr/bin/env bash
# Build a patched, signed single-process offline APK on Linux.
#
# This is the no-root A51 path: it injects libcrprobe.so plus the same smali
# bootstrap used by the Windows build, but skips the engine-cluster
# Application/activities (those are for the multi-process MuMu harness).
#
# Usage:
#   ./build_offline_apk.sh --input-apk nr_16.635.1_release_951fe421.apk \
#       --target-build 16.635.1 --output-apk builds/cr-ai-16.635.1.apk
#
# Tool discovery: CR_NDK_ROOT, CR_BUILD_TOOLS, CR_JAVA, CR_KEYTOOL, CR_APKTOOL,
# CR_PYTHON, CR_PROBE, or the defaults under .resource/android-tools/.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
input_apk="${CR_INPUT_APK:-}"
output_apk=""
target_build="${CR_TARGET_BUILD:-15.535.13}"
probe="${CR_PROBE:-$repo_root/native_runner/probe/out/libcrprobe.so}"
python_bin="${CR_PYTHON:-}"
java_bin="${CR_JAVA:-}"
keytool_bin="${CR_KEYTOOL:-}"
build_tools="${CR_BUILD_TOOLS:-}"
apktool_jar="${CR_APKTOOL:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input-apk) input_apk="${2:?missing value}"; shift 2 ;;
    --output-apk) output_apk="${2:?missing value}"; shift 2 ;;
    --target-build) target_build="${2:?missing value}"; shift 2 ;;
    --probe) probe="${2:?missing value}"; shift 2 ;;
    -h|--help)
      sed -n '2,/^[^#]/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$input_apk" ]]; then
  echo "Missing --input-apk (or CR_INPUT_APK)." >&2
  exit 1
fi
if [[ ! -f "$input_apk" ]]; then
  echo "Input APK not found: $input_apk" >&2
  exit 1
fi
if [[ -z "$output_apk" ]]; then
  output_apk="$repo_root/builds/cr-ai-$target_build.apk"
fi

[[ -n "$python_bin" ]] || python_bin="$repo_root/.venv/bin/python"
[[ -x "$python_bin" ]] || python_bin="$(command -v python3)"
[[ -n "$java_bin" ]] || java_bin="$(command -v java)"
[[ -n "$keytool_bin" ]] || keytool_bin="$(command -v keytool)"

if [[ -z "$apktool_jar" ]]; then
  apktool_jar="$repo_root/.resource/android-tools/apktool_2.12.1.jar"
fi
if [[ ! -f "$apktool_jar" ]]; then
  echo "Missing apktool jar: $apktool_jar" >&2
  exit 1
fi

if [[ -z "$build_tools" ]]; then
  build_tools="$(find "$repo_root/.resource/android-tools" -maxdepth 3 -type f -name zipalign -printf '%h\n' 2>/dev/null | head -1)"
fi
if [[ -z "$build_tools" || ! -x "$build_tools/zipalign" || ! -x "$build_tools/apksigner" ]]; then
  echo "Missing Android build-tools (zipalign/apksigner). Set CR_BUILD_TOOLS." >&2
  exit 1
fi

libg_sha256=""
target_file="$repo_root/docs/native/$target_build/build.json"
if [[ -f "$target_file" ]]; then
  libg_sha256="$("$python_bin" -c 'import json,sys; print(json.load(open(sys.argv[1]))["libg_sha256"])' "$target_file")"
fi

mkdir -p "$repo_root/build"
work="$(mktemp -d "$repo_root/build/offline-$target_build.XXXXXX")"
decoded="$work/decoded"
unsigned="$work/unsigned.apk"
aligned="$work/aligned.apk"
signed="$work/signed.apk"
keystore="$repo_root/native_runner/probe/crprobe-cluster.keystore"
password_file="$keystore.pass"

cleanup() {
  rm -rf "$work"
}
trap cleanup EXIT

echo "[1/6] Verify the input APK ($target_build)"
check_args=("$repo_root/native_runner/offline_build.py" check-input --input-apk "$input_apk")
if [[ -n "$libg_sha256" ]]; then
  check_args+=(--libg-sha256 "$libg_sha256")
fi
"$python_bin" "${check_args[@]}"

echo "[2/6] Build the native probe ($target_build)"
"$repo_root/native_runner/probe/build_probe.sh" \
  --target-build "$target_build" --output "$probe"

echo "[3/6] Decode and patch the APK"
"$java_bin" -jar "$apktool_jar" decode "$input_apk" --output "$decoded" --force
"$python_bin" "$repo_root/native_runner/offline_build.py" patch --source "$decoded"
mkdir -p "$decoded/lib/arm64-v8a"
cp -f "$probe" "$decoded/lib/arm64-v8a/libcrprobe.so"

echo "[4/6] Build, align and sign"
"$java_bin" -jar "$apktool_jar" build "$decoded" --force --output "$unsigned"
"$build_tools/zipalign" -f -p 4 "$unsigned" "$aligned"

if [[ ! -f "$keystore" ]]; then
  if [[ -f "$password_file" ]]; then
    echo "Keystore password file exists without a keystore: $password_file" >&2
    exit 1
  fi
  mkdir -p "$(dirname -- "$keystore")"
  password="$("$python_bin" -c 'import secrets; print(secrets.token_hex(48))')"
  printf '%s' "$password" > "$password_file"
  chmod 600 "$password_file"
  "$keytool_bin" -genkeypair \
    -keystore "$keystore" \
    -storepass "$password" \
    -keypass "$password" \
    -alias "crprobe-cluster" \
    -keyalg RSA \
    -keysize 2048 \
    -validity 36500 \
    -dname "CN=Firstlight CR Local Build" \
    -noprompt >/dev/null
fi
password="$(cat "$password_file")"
"$build_tools/apksigner" sign \
  --ks "$keystore" \
  --ks-key-alias "crprobe-cluster" \
  --ks-pass "pass:$password" \
  --key-pass "pass:$password" \
  --out "$signed" \
  "$aligned"

echo "[5/6] Verify the signature and payload"
"$build_tools/apksigner" verify --verbose --print-certs "$signed" >/dev/null
verify_args=("$repo_root/native_runner/offline_build.py" verify --apk "$signed" --probe "$probe")
if [[ -n "$libg_sha256" ]]; then
  verify_args+=(--libg-sha256 "$libg_sha256")
fi
"$python_bin" "${verify_args[@]}"

echo "[6/6] Publish"
mkdir -p "$(dirname -- "$output_apk")"
cp -f "$signed" "$output_apk"
cp -f "${signed%.apk}.build.json" "${output_apk%.apk}.build.json" 2>/dev/null || true
echo "Built: $output_apk"
sha256sum "$output_apk"
