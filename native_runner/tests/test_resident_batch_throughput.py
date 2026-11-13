"""Aggregate-throughput math for the resident batch tool.

The multi-lane number is what the engine decision rule uses, so the distinction
between the lane-sum capacity and the makespan floor must not regress: a phone
whose top-app lane runs on big cores finishes long before a background lane
pinned to cpu0-1, and the makespan-only figure used to under-count that case by
~40% (FINDINGS §3.25).
"""

from __future__ import annotations

import sys

from native_runner.paths import REPOSITORY_ROOT

TOOLS = REPOSITORY_ROOT / "tools" / "native_analysis"
sys.path.insert(0, str(TOOLS))

import resident_batch_throughput as rbt  # type: ignore[import-not-found]


def lane(port: int, lane_seconds: float, ticks_per_second: float, actions_per_second: float = 0.0) -> dict:
    return {
        "port": port,
        "slots": 8,
        "laneSeconds": lane_seconds,
        "engineTicksPerSecond": ticks_per_second,
        "slotMatchesPerSecond": ticks_per_second / rbt.MATCH_TICKS,
        "actionsPerSecond": actions_per_second,
        "totalActions": 16,
    }


def test_one_lane_capacity_equals_makespan() -> None:
    aggregate = rbt.aggregate_lanes([lane(26789, 2.0, 12000.0)], 8, 600, 5)
    assert aggregate["aggregateSlotMatchesPerSecond"] == aggregate["makespanSlotMatchesPerSecond"]
    assert aggregate["aggregateSlotTicksPerSecond"] == 12000.0
    assert aggregate["battleCapacity"] == 8


def test_unequal_lanes_report_capacity_above_the_makespan_floor() -> None:
    lanes = [
        lane(26789, 4.0, 6000.0, 3.0),  # background little core
        lane(26790, 2.0, 12000.0, 9.0),  # top-app big core
    ]
    aggregate = rbt.aggregate_lanes(lanes, 8, 600, 5)

    # Capacity is the lane sum and stays correct for mixed-speed lanes.
    assert aggregate["aggregateSlotTicksPerSecond"] == 18000.0
    assert aggregate["aggregateSlotMatchesPerSecond"] == round(18000.0 / rbt.MATCH_TICKS, 3)
    # The makespan floor divides both lanes' work by the slowest window.
    assert aggregate["makespanSlotTicksPerSecond"] == 12000.0
    assert aggregate["makespanSlotMatchesPerSecond"] == round(12000.0 / rbt.MATCH_TICKS, 3)
    assert aggregate["aggregateSlotMatchesPerSecond"] > aggregate["makespanSlotMatchesPerSecond"]
    assert aggregate["aggregateWindowSeconds"] == 4.0
    assert aggregate["battleCapacity"] == 16
    assert aggregate["aggregateActionsPerSecond"] == 12.0
    assert aggregate["totalActions"] == 32
