"""Linux probe/APK build entry points stay runnable and self-describing."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from native_runner.paths import REPOSITORY_ROOT

SCRIPTS = (
    REPOSITORY_ROOT / "native_runner" / "probe" / "build_probe.sh",
    REPOSITORY_ROOT / "build_offline_apk.sh",
    REPOSITORY_ROOT / "deploy_a51.sh",
)
TARGET_SCRIPTS = SCRIPTS[:2]


def bash() -> str:
    executable = shutil.which("bash")
    if executable is None:
        pytest.skip("bash is unavailable")
    return executable


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_script_exists_executable_and_parses(script) -> None:
    assert script.is_file(), f"missing build script: {script}"
    assert os.access(script, os.X_OK), f"{script.name} is not executable"
    subprocess.run([bash(), "-n", str(script)], check=True, capture_output=True)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_script_help_is_self_describing(script) -> None:
    result = subprocess.run(
        [bash(), str(script), "--help"], check=True, capture_output=True, text=True
    )
    assert "Usage:" in result.stdout


@pytest.mark.parametrize("script", TARGET_SCRIPTS, ids=lambda path: path.name)
def test_target_build_scripts_document_target_build(script) -> None:
    result = subprocess.run(
        [bash(), str(script), "--help"], check=True, capture_output=True, text=True
    )
    assert "--target-build" in result.stdout
