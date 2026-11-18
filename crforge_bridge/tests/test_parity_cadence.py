"""The native parity cadence: checkpoints mid-run, and the gate's price in updates.

Session 50 added ``self_play.py --checkpoint-every N`` (a checkpoint the native
gate can read without ending the run) and ``tools/native_analysis/parity_curve.py``
(the gate run against those checkpoints, plus the cadence arithmetic).  These
tests pin the parts that decide the cadence, without starting a native engine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_NATIVE_DIR = _REPO / "tools" / "native_analysis"
_TOOL_DIR = _REPO / "tools" / "crforge"
for _path in (_NATIVE_DIR, _TOOL_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import parity_curve
import self_play


def test_a_cadence_checkpoint_is_named_by_its_update_step() -> None:
    path = self_play._cadence_checkpoint_path(Path("runs/c4/ck.pt"), 291)
    assert path == Path("runs/c4/ck-step-291.pt")
    assert parity_curve.checkpoint_step(path, 0) == 291


def test_a_checkpoint_without_a_step_falls_back_to_its_position() -> None:
    assert parity_curve.checkpoint_step(Path("runs/c4/pilot.pt"), 3) == 3


def test_a_cadence_needs_a_checkpoint_to_copy() -> None:
    self_play._validate_cadence(0, None)
    with pytest.raises(SystemExit):
        self_play._validate_cadence(2, None)
    with pytest.raises(SystemExit):
        self_play._validate_cadence(-1, Path("ck.pt"))


def test_the_cadence_is_the_gate_measured_in_updates_over_the_budget() -> None:
    # A 600 s gate against a 24 s update is 25 updates of training; at a 10 %
    # budget it can fire every 250 updates, and then it is exactly 10 % of the run.
    cadence = parity_curve.recommend_cadence(gate_seconds=600.0, update_seconds=24.0, budget=0.1)
    assert cadence["gate_in_updates"] == pytest.approx(25.0)
    assert cadence["every_updates"] == 250
    assert cadence["share"] == pytest.approx(0.1)


def test_a_cheap_gate_still_rounds_up_to_a_whole_number_of_updates() -> None:
    cadence = parity_curve.recommend_cadence(gate_seconds=60.0, update_seconds=24.0, budget=0.1)
    assert cadence["every_updates"] == 25
    assert 0.0 < cadence["share"] <= 0.1


def test_a_missing_wall_refuses_to_invent_a_cadence() -> None:
    assert parity_curve.recommend_cadence(gate_seconds=0.0, update_seconds=24.0, budget=0.1)["every_updates"] is None
    assert parity_curve.recommend_cadence(gate_seconds=600.0, update_seconds=0.0, budget=0.1)["every_updates"] is None
    assert parity_curve.recommend_cadence(gate_seconds=600.0, update_seconds=24.0, budget=0.0)["every_updates"] is None
