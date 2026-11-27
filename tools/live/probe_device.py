#!/usr/bin/env python3
"""Probe a connected Android device for the official-client live path."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from native_runner.live.device import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
