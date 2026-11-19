"""The Linux probe build entry point stays runnable and self-describing."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from native_runner.paths import REPOSITORY_ROOT

SCRIPT = REPOSITORY_ROOT / "native_runner" / "probe" / "build_probe.sh"


def bash() -> str:
    executable = shutil.which("bash")
    if executable is None:
        pytest.skip("bash is unavailable")
    return executable


def test_script_exists_executable_and_parses() -> None:
    assert SCRIPT.is_file(), f"missing build script: {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT.name} is not executable"
    subprocess.run([bash(), "-n", str(SCRIPT)], check=True, capture_output=True)


def test_script_help_is_self_describing() -> None:
    result = subprocess.run(
        [bash(), str(SCRIPT), "--help"], check=True, capture_output=True, text=True
    )
    assert "Usage:" in result.stdout
    assert "--target-build" in result.stdout
