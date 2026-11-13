#!/usr/bin/env bash
# Build libcrprobe.so for arm64-v8a on Linux.
#
# The PowerShell script remains the canonical Windows path.  This mirrors it so
# the same probe can be compiled on a Linux host with the Android NDK.
#
# Usage:
#   native_runner/probe/build_probe.sh [--target-build 15.535.13|16.635.1] \
#       [--ndk <NDK root>] [--output <path/to/libcrprobe.so>]
#
# Environment fallbacks: CR_NDK_ROOT, CR_PROBE.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"

target_build="${CR_TARGET_BUILD:-15.535.13}"
ndk_root="${CR_NDK_ROOT:-}"
output="${CR_PROBE:-$script_dir/out/libcrprobe.so}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-build) target_build="${2:?missing value}"; shift 2 ;;
    --ndk) ndk_root="${2:?missing value}"; shift 2 ;;
    --output) output="${2:?missing value}"; shift 2 ;;
    -h|--help)
      sed -n '2,/^[^#]/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "$target_build" in
  15.535.13) target_define=() ;;
  16.635.1) target_define=(-DCR_PROBE_TARGET_16_635_1) ;;
  *) echo "unsupported target build: $target_build" >&2; exit 2 ;;
esac

if [[ -z "$ndk_root" ]]; then
  for candidate in "$repo_root/.resource/android-tools"/android-ndk-*; do
    [[ -d "$candidate" ]] && ndk_root="$candidate" && break
  done
fi
if [[ -z "$ndk_root" || ! -d "$ndk_root" ]]; then
  echo "Missing Android NDK. Set CR_NDK_ROOT or install one under .resource/android-tools/." >&2
  exit 1
fi

compiler="$ndk_root/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android24-clang++"
if [[ ! -x "$compiler" ]]; then
  echo "Missing NDK compiler: $compiler" >&2
  exit 1
fi

mkdir -p "$(dirname -- "$output")"

echo "Building $target_build probe -> $output"
"$compiler" \
  -std=c++17 \
  -O2 \
  -fPIC \
  -fvisibility=hidden \
  -Wall \
  -Wextra \
  "${target_define[@]}" \
  -shared \
  -Wl,-z,max-page-size=16384 \
  -o "$output" \
  "$script_dir/cr_replay_probe.cpp" \
  "$script_dir/native_touch_interceptor.cpp" \
  "$script_dir/native_call_arm64.S" \
  "$script_dir/remaining_runtime_arm64.S" \
  -llog \
  -ldl \
  -lz

echo "Built: $output"
sha256sum "$output"
