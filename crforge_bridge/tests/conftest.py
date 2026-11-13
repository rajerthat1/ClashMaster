"""Shared fixtures for the crforge bridge tests.

The bridge needs JPype, an installed crforge distribution and generated tables.
Tests skip with a concrete reason when any of those are absent, mirroring the
bridge docstring rather than failing on a machine that only has FirstLight.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from crforge_bridge.engine import Engine
from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES


def _missing_dependency() -> str | None:
    if importlib.util.find_spec("jpype") is None:
        return "jpype is not installed"
    if not sorted((Path(DEFAULT_INSTALL) / "lib").glob("*.jar")):
        return f"crforge env distribution is missing: {DEFAULT_INSTALL}"
    if not (Path(DEFAULT_TABLES) / "build.properties").is_file():
        return f"crforge tables are missing: {DEFAULT_TABLES}"
    return None


@pytest.fixture(scope="session")
def crforge_runtime() -> None:
    reason = _missing_dependency()
    if reason:
        pytest.skip(reason)


@pytest.fixture()
def engine(crforge_runtime: None) -> Engine:
    instance = Engine()
    instance.start()
    return instance
