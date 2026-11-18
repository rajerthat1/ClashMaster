"""The producer's JVM escape hatch: ``--jvm-arg`` reaches the engine.

A rented host's HotSpot C2 compiler miscompiled ``SeatIds.of`` while the PPO
update kept the JVM idle between segments and segfaulted the producer at the
next segment; ``-XX:TieredStopAtLevel=1`` (C1 only) passes.  The flag is the
producer's, so it has to reach ``VectorBattle`` unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "crforge"
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import self_play


def test_vector_forwards_the_jvm_options(monkeypatch) -> None:
    import crforge_env.vector as vector_module

    seen: dict[str, object] = {}

    class FakeVectorBattle:
        def __init__(self, *args: object, **kwargs: object) -> None:
            seen.update(kwargs)

        def reset(self, *args: object, **kwargs: object) -> None:
            pass

    monkeypatch.setattr(vector_module, "VectorBattle", FakeVectorBattle)
    self_play._vector(2, max_ticks=0, jvm_args=("-XX:TieredStopAtLevel=1",))
    assert seen["jvm_args"] == ("-XX:TieredStopAtLevel=1",)


def test_the_default_is_no_extra_option(monkeypatch) -> None:
    import crforge_env.vector as vector_module

    seen: dict[str, object] = {}

    class FakeVectorBattle:
        def __init__(self, *args: object, **kwargs: object) -> None:
            seen.update(kwargs)

        def reset(self, *args: object, **kwargs: object) -> None:
            pass

    monkeypatch.setattr(vector_module, "VectorBattle", FakeVectorBattle)
    self_play._vector(1, max_ticks=0)
    assert seen["jvm_args"] == ()
