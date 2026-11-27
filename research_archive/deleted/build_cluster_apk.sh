#!/usr/bin/env bash
# Build a patched, signed multi-process engine-cluster APK on Linux.
#
# The Windows path (native_runner/build_engine_cluster_apk.ps1) adds
# EngineClusterApplication plus Engine0..N activities, each declared with its
# own `android:process=":engineN"`.  Android therefore gives every engine an
# independent zygote child, libg address space and control port (26789 + N).
# This is the same mechanism MuMu uses; it also works on a physical phone, so
# one A51 can expose several lanes.
#
# Usage:
#   ./build_cluster_apk.sh --input-apk nr_16.635.1_release_951fe421.apk \
#       --target-build 16.635.1 --engine-count 4 \
#       --output-apk builds/cr-engine-cluster-16.635.1.apk
#
# The decoded pristine tree is cached under .resource/cluster/ (gitignored) so
# repeat builds only pay the apktool build/sign cost, not a fresh decode.
#
# Tool discovery: CR_NDK_ROOT, CR_BUILD_TOOLS, CR_JAVA, CR_KEYTOOL, CR_APKTOOL,
# CR_PYTHON, CR_PROBE, or the defaults under .resource/android-tools/.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
input_apk="${CR_INPUT_APK:-}"
output_apk=""
target_build="${CR_TARGET_BUILD:-15.535.13}"
engine_count="${CR_ENGINE_COUNT:-1}"
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
    --engine-count) engine_count="${2:?missing value}"; shift 2 ;;
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
if ! [[ "$engine_count" =~ ^[0-9]+$ ]] || (( engine_count < 1 || engine_count > 24 )); then
  echo "--engine-count must be between 1 and 24." >&2
  exit 2
fi
if [[ -z "$output_apk" ]]; then
  output_apk="$repo_root/builds/cr-engine-cluster-$target_build.apk"
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

cache_root="$repo_root/.resource/cluster"
pristine="$cache_root/decoded-$target_build-pristine"
work="$(mktemp -d "$repo_root/build/cluster-$target_build.XXXXXX")"
source_root="$work/source"
unsigned="$work/cluster-unsigned.apk"
aligned="$work/cluster-aligned.apk"
signed="$work/cluster-signed.apk"
keystore="$repo_root/native_runner/probe/crprobe-cluster.keystore"
password_file="$keystore.pass"

cleanup() {
  rm -rf "$work"
}
trap cleanup EXIT

echo "[1/7] Verify the input APK ($target_build)"
check_args=("$repo_root/native_runner/offline_build.py" check-input --input-apk "$input_apk")
if [[ -n "$libg_sha256" ]]; then
  check_args+=(--libg-sha256 "$libg_sha256")
fi
"$python_bin" "${check_args[@]}"

echo "[2/7] Build the native probe ($target_build)"
"$repo_root/native_runner/probe/build_probe.sh" \
  --target-build "$target_build" --output "$probe"

echo "[3/7] Decode the pristine APK (cached)"
if [[ ! -d "$pristine" ]]; then
  mkdir -p "$cache_root"
  tmp_pristine="$cache_root/.decoded-$target_build.$$"
  rm -rf "$tmp_pristine"
  "$java_bin" -jar "$apktool_jar" decode "$input_apk" --output "$tmp_pristine" --force
  rm -rf "$pristine"
  mv "$tmp_pristine" "$pristine"
fi
cp -a "$pristine" "$source_root"

echo "[4/7] Patch the bootstrap and add $engine_count engine processes"
"$python_bin" "$repo_root/native_runner/offline_build.py" patch --source "$source_root"
"$python_bin" - "$source_root" "$engine_count" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
engine_count = int(sys.argv[2])
package = "nullsroyale.rel.free"

manifest = source / "AndroidManifest.xml"
text = manifest.read_text(encoding="utf-8")
application_anchor = '<application android:allowBackup="true"'
meta_anchor = '        <meta-data android:name="io.sentry.auto-init"'
if application_anchor not in text or meta_anchor not in text:
    raise SystemExit("could not find the manifest insertion point")
if "EngineClusterApplication" in text:
    raise SystemExit("source tree is already cluster-patched; rebuild the pristine cache")

text = text.replace(
    application_anchor,
    '<application android:name="com.supercell.clashroyale.EngineClusterApplication" android:allowBackup="true"',
    1,
)
activities = []
for engine_id in range(engine_count):
    activities.append(
        '        <activity android:configChanges="keyboard|keyboardHidden|navigation|orientation|'
        'screenLayout|screenSize|smallestScreenSize" android:excludeFromRecents="true" '
        'android:exported="true" android:launchMode="singleTask" '
        f'android:name="com.supercell.clashroyale.Engine{engine_id}App" '
        f'android:process=":engine{engine_id}" android:screenOrientation="sensorPortrait" '
        f'android:taskAffinity="{package}.engine{engine_id}" '
        'android:theme="@android:style/Theme.NoTitleBar.Fullscreen" '
        'android:windowSoftInputMode="adjustNothing|stateUnchanged"/>'
    )
text = text.replace(meta_anchor, "\n".join(activities) + "\n" + meta_anchor, 1)
manifest.write_text(text, encoding="utf-8")

smali_dir = source / "smali" / "com" / "supercell" / "clashroyale"
application_smali = """.class public Lcom/supercell/clashroyale/EngineClusterApplication;
.super Landroid/app/Application;


# direct methods
.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Landroid/app/Application;-><init>()V

    return-void
.end method


# virtual methods
.method protected attachBaseContext(Landroid/content/Context;)V
    .locals 4

    invoke-super {p0, p1}, Landroid/app/Application;->attachBaseContext(Landroid/content/Context;)V

    sget v0, Landroid/os/Build$VERSION;->SDK_INT:I

    const/16 v1, 0x1c

    if-lt v0, v1, :done

    invoke-static {}, Landroid/app/Application;->getProcessName()Ljava/lang/String;

    move-result-object v0

    const-string v1, ":engine"

    invoke-virtual {v0, v1}, Ljava/lang/String;->lastIndexOf(Ljava/lang/String;)I

    move-result v2

    if-ltz v2, :done

    add-int/lit8 v2, v2, 0x1

    invoke-virtual {v0, v2}, Ljava/lang/String;->substring(I)Ljava/lang/String;

    move-result-object v3

    const-string v0, "engine0"

    invoke-virtual {v3, v0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v0

    if-nez v0, :done

    invoke-static {v3}, Landroid/webkit/WebView;->setDataDirectorySuffix(Ljava/lang/String;)V

    :done
    return-void
.end method
"""
(smali_dir / "EngineClusterApplication.smali").write_text(application_smali, encoding="utf-8")

for engine_id in range(engine_count):
    class_name = f"Engine{engine_id}App"
    engine_smali = f""".class public Lcom/supercell/clashroyale/{class_name};
.super Lcom/supercell/clashroyale/GameApp;


# direct methods
.method public constructor <init>()V
    .locals 0

    invoke-direct {{p0}}, Lcom/supercell/clashroyale/GameApp;-><init>()V

    return-void
.end method
"""
    (smali_dir / f"{class_name}.smali").write_text(engine_smali, encoding="utf-8")
PY
mkdir -p "$source_root/lib/arm64-v8a"
cp -f "$probe" "$source_root/lib/arm64-v8a/libcrprobe.so"

echo "[5/7] Build, align and sign"
"$java_bin" -jar "$apktool_jar" build "$source_root" --force --output "$unsigned"
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

echo "[6/7] Verify the signature and payload"
"$build_tools/apksigner" verify --verbose --print-certs "$signed" >/dev/null
verify_args=("$repo_root/native_runner/offline_build.py" verify --apk "$signed" --probe "$probe")
if [[ -n "$libg_sha256" ]]; then
  verify_args+=(--libg-sha256 "$libg_sha256")
fi
"$python_bin" "${verify_args[@]}"

echo "[7/7] Publish"
mkdir -p "$(dirname -- "$output_apk")"
cp -f "$signed" "$output_apk"
cp -f "${signed%.apk}.build.json" "${output_apk%.apk}.build.json" 2>/dev/null || true
echo "Built: $output_apk ($engine_count engine processes)"
sha256sum "$output_apk"
