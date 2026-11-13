#!/usr/bin/env bash
# Run one cr-native-linux-bionic worker from a user-owned install root.
#
# Android's libc (prop_area::map_fd_ro) refuses property-area files whose
# st_uid/st_gid is not 0.  A `cr-native-bionic install --system-root DIR` run
# as an ordinary user leaves them owned by that user, so the worker cannot
# start.  A user namespace with `--uid 0 --gid 0` maps our own files to
# root-owned inside the namespace and fixes property init without root.
#
# Host filesystem is not modified; only the install root is visible as /.
#
#   tools/native_bringup/run_rootless_worker.sh --slot 0 --port 39031
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
SYSTEM_ROOT=${SYSTEM_ROOT:-$PROJECT_ROOT/builds/rootless/system-root}
SLOT=${SLOT:-0}
PORT=${PORT:-39031}
LOG=${LOG:-/tmp/opencode/rootless-worker-$SLOT.log}

DIRECT=/data/local/tmp/cr-native-direct-$SLOT
BOOT=/apex/com.android.art/javalib/core-oj.jar:/apex/com.android.art/javalib/core-libart.jar:/apex/com.android.art/javalib/okhttp.jar:/apex/com.android.art/javalib/bouncycastle.jar:/apex/com.android.art/javalib/apache-xml.jar:/system/framework/framework.jar:/system/framework/framework-graphics.jar:/system/framework/ext.jar:/system/framework/telephony-common.jar:/system/framework/voip-common.jar:/system/framework/ims-common.jar:/apex/com.android.i18n/javalib/core-icu4j.jar:/apex/com.android.appsearch/javalib/framework-appsearch.jar:/apex/com.android.conscrypt/javalib/conscrypt.jar:/apex/com.android.ipsec/javalib/android.net.ipsec.ike.jar:/apex/com.android.media/javalib/updatable-media.jar:/apex/com.android.mediaprovider/javalib/framework-mediaprovider.jar:/apex/com.android.os.statsd/javalib/framework-statsd.jar:/apex/com.android.permission/javalib/framework-permission.jar:/apex/com.android.permission/javalib/framework-permission-s.jar:/apex/com.android.scheduling/javalib/framework-scheduling.jar:/apex/com.android.sdkext/javalib/framework-sdkextensions.jar:/apex/com.android.tethering/javalib/framework-connectivity.jar:/apex/com.android.tethering/javalib/framework-tethering.jar:/apex/com.android.wifi/javalib/framework-wifi.jar

command -v bwrap >/dev/null || {
    echo "bwrap is required" >&2
    exit 1
}
if [ ! -d "$SYSTEM_ROOT$DIRECT" ]; then
    echo "worker slot not installed: $SYSTEM_ROOT$DIRECT" >&2
    exit 1
fi

exec env -i \
  ANDROID_ART_ROOT=/apex/com.android.art \
  ANDROID_DATA=/data \
  ANDROID_I18N_ROOT=/apex/com.android.i18n \
  ANDROID_ROOT=/system \
  ANDROID_RUNTIME_ROOT=/apex/com.android.runtime \
  ANDROID_TZDATA_ROOT=/apex/com.android.tzdata \
  CR_BINDERLESS_ANDROID=1 \
  CR_BINDERLESS_NATIVE_CONFIG_GETTERS=1 \
  CR_BINDERLESS_NATIVE_CONFIG_POSTPROCESS=1 \
  CR_BINDERLESS_PRELOAD_CORE_DATA=0 \
  CR_BINDERLESS_DEFER_NULL_DEPENDENCIES=0 \
  CR_BINDERLESS_DEFER_OPTIONAL_CLIENT_GLOBALS=0 \
  CR_NATIVE_LOADING_INITIAL_SETTLE_MS=0 \
  CR_NATIVE_LOADING_MAX_FRAMES=10000 \
  CR_NATIVE_LOADING_TIMEOUT_MS=30000 \
  CR_NATIVE_LOADING_SLEEP_MS=5 \
  HOME=/data/local/tmp \
  LD_LIBRARY_PATH=$DIRECT \
  TMPDIR=$DIRECT/cache \
  PATH=/system/bin \
  /usr/bin/bwrap \
  --unshare-user --uid 0 --gid 0 \
  --bind "$SYSTEM_ROOT" / \
  --proc /proc \
  --dev /dev \
  --bind "$SYSTEM_ROOT/dev/__properties__" /dev/__properties__ \
  --chdir "$DIRECT/assets" \
  /apex/com.android.runtime/bin/linker64 \
  /apex/com.android.art/bin/dalvikvm64 \
  -Xint \
  "-Xbootclasspath:$BOOT" \
  "-Xbootclasspath-locations:$BOOT" \
  "-Djava.library.path=$DIRECT" \
  -cp "/system/framework/android.test.base.jar:/system/framework/android.test.mock.jar:$DIRECT/lifecycle-probe.jar:$DIRECT/base.apk" \
  royale.nativehost.JniHost "$DIRECT" serve-direct "$PORT" \
  >"$LOG" 2>&1
