#!/usr/bin/env bash
# Install the patched APK on a USB-connected A51 and stream probe logs.
#
# Usage:
#   ./deploy_a51.sh [--apk builds/cr-ai-16.635.1.apk] [--serial SERIAL] [--seconds 60]
#
# This never uninstalls anything.  If adb reports
# INSTALL_FAILED_UPDATE_INCOMPATIBLE, the patched APK uses a different signing
# key than the installed nulls build; uninstall it yourself, then re-run.  That
# wipes the game's downloaded update/ data, so let the game re-download before
# trusting the content fingerprint.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
apk="$repo_root/builds/cr-ai-16.635.1.apk"
serial=""
seconds=60

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apk) apk="${2:?missing value}"; shift 2 ;;
    --serial) serial="${2:?missing value}"; shift 2 ;;
    --seconds) seconds="${2:?missing value}"; shift 2 ;;
    -h|--help)
      sed -n '2,/^[^#]/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

adb_bin="$(command -v adb)"
if [[ -z "$adb_bin" ]]; then
  echo "adb is not on PATH." >&2
  exit 1
fi
if [[ ! -f "$apk" ]]; then
  echo "APK not found: $apk" >&2
  exit 1
fi

adb_args=()
if [[ -n "$serial" ]]; then
  adb_args=(-s "$serial")
fi

echo "[1/3] Checking the device"
"$adb_bin" "${adb_args[@]}" wait-for-device
"$adb_bin" "${adb_args[@]}" shell getprop ro.product.model
"$adb_bin" "${adb_args[@]}" shell getprop ro.product.cpu.abi

echo "[2/3] Installing $apk"
if ! "$adb_bin" "${adb_args[@]}" install -r "$apk"; then
  echo >&2
  echo "Install failed. If this is INSTALL_FAILED_UPDATE_INCOMPATIBLE, the local" >&2
  echo "signing key differs from the installed nulls build. Uninstall it, re-run," >&2
  echo "then let the game re-download its update data." >&2
  exit 1
fi

echo "[3/3] Launching and streaming CRNativeProbe logs for ${seconds}s"
"$adb_bin" "${adb_args[@]}" logcat -c
"$adb_bin" "${adb_args[@]}" shell monkey -p nullsroyale.rel.free -c android.intent.category.LAUNCHER 1 >/dev/null
if command -v timeout >/dev/null 2>&1; then
  timeout "$seconds" "$adb_bin" "${adb_args[@]}" logcat -s CRNativeProbe || true
else
  "$adb_bin" "${adb_args[@]}" logcat -s CRNativeProbe
fi
