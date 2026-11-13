#!/usr/bin/env bash
# Rebuild the two host artifacts that the published bundle gets wrong:
# lifecycle-probe.jar and libnative_host_bridge.so.
#
# Requires the Android NDK, a JDK, d8 and an Android platform android.jar.
# The SDK platform jar is not in the repository; point ANDROID_JAR at it.
#
#   ANDROID_JAR=/path/to/platforms/android-34/android.jar \
#       tools/native_bringup/build_host_artifacts.sh
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
SANDBOX=${CR_SANDBOX_SOURCE:-$PROJECT_ROOT/.resource/cr-native-sandbox}
NDK=${ANDROID_NDK:-$PROJECT_ROOT/.resource/android-tools/android-ndk-r27c}
JDK=${JAVA_HOME:-$PROJECT_ROOT/.resource/jdk21/jdk-21.0.13+9}
D8=${D8_JAR:-$PROJECT_ROOT/.resource/android-tools/android-14/lib/d8.jar}
OUT=${OUT:-$PROJECT_ROOT/builds/local-overlay/artifacts}
ANDROID_JAR=${ANDROID_JAR:?set ANDROID_JAR to the SDK platform android.jar}
ANDROID_TEST_MOCK_JAR=${ANDROID_TEST_MOCK_JAR:-$(dirname "$ANDROID_JAR")/optional/android.test.mock.jar}

for path in "$NDK" "$JDK" "$D8" "$ANDROID_JAR" "$SANDBOX/android_probe/java" \
            "$SANDBOX/android_probe/native/jni_bridge.cpp"; do
    if [ ! -e "$path" ]; then
        echo "missing build input: $path" >&2
        exit 1
    fi
done

PATCH=$PROJECT_ROOT/tools/native_bringup/patches/0001-bridge-headless-ui-locale-guard.patch
if ! grep -q '0xE9, 0x35, 0x08, 0xCA, 0xFF' \
        "$SANDBOX/android_probe/native/jni_bridge.cpp"; then
    if [ "${APPLY_PATCH:-0}" = "1" ]; then
        git -C "$SANDBOX" apply "$PATCH"
    else
        echo "jni_bridge.cpp lacks the headless UI-locale fix." >&2
        echo "Apply it first: git -C $SANDBOX apply $PATCH" >&2
        echo "(or rerun with APPLY_PATCH=1)" >&2
        exit 1
    fi
fi

mkdir -p "$OUT" /tmp/opencode/cr-host-build/classes
rm -rf /tmp/opencode/cr-host-build/classes/*
CLANGXX=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/clang++
SYSROOT=$NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/include

echo "building libnative_host_bridge.so"
"$CLANGXX" \
    --target=x86_64-linux-android23 -std=c++20 -fPIC -shared -O2 -g \
    -Wall -Wextra -Werror -I"$SYSROOT" -I"$SANDBOX/android_probe/native" \
    "$SANDBOX/android_probe/native/jni_bridge.cpp" -ldl \
    -o "$OUT/libnative_host_bridge.so"

echo "building lifecycle-probe.jar"
"$JDK/bin/javac" --release 8 -classpath "$ANDROID_JAR:$ANDROID_TEST_MOCK_JAR" \
    -d /tmp/opencode/cr-host-build/classes \
    $(find "$SANDBOX/android_probe/java" -name '*.java')
"$JDK/bin/java" -cp "$D8" com.android.tools.r8.D8 \
    --debug --min-api 23 --lib "$ANDROID_JAR" --lib "$ANDROID_TEST_MOCK_JAR" \
    --output "$OUT/lifecycle-probe.jar" \
    $(find /tmp/opencode/cr-host-build/classes -name '*.class')

sha256sum "$OUT/libnative_host_bridge.so" "$OUT/lifecycle-probe.jar"
