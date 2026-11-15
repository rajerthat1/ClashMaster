"""The C1 encoder spike's two builders must agree on the same observations."""

from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest

from crforge_bridge.catalog import Catalog

_TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "crforge"
if str(_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOL_DIR))

import encoder_spike  # noqa: E402  (the spike adds the simulator to sys.path)


def test_vectorized_and_per_lane_encoders_build_the_same_frame(crforge_runtime: None) -> None:
    from crforge_env.vector import LadderConfig, SideSetup, VectorBattle

    catalog = Catalog.load()
    setup = SideSetup(towers=encoder_spike.TOWERS, king_level=16, tower_level=16)
    with VectorBattle(
        4,
        1,
        install=str(encoder_spike.DEFAULT_INSTALL),
        tables=str(encoder_spike.DEFAULT_TABLES),
        config=LadderConfig(decision_ticks=5, level=16, tower_level=16),
    ) as vector:
        vector.reset(
            [1, 2, 3, 4],
            list(encoder_spike.DECK),
            list(encoder_spike.DECK),
            list(encoder_spike.DECK_SLOTS),
            list(encoder_spike.DECK_SLOTS),
            setup0=setup,
            setup1=setup,
        )
        vectorized = encoder_spike.VectorEncoder(vector, catalog)
        per_lane = encoder_spike.PerLaneEncoder(
            vector, catalog, encoder_spike._LazyJavaSchema()
        )
        spec = vectorized.frame_spec(4)
        vector_frame = encoder_spike.PolicyFrame(**spec)
        lane_frame = encoder_spike.PolicyFrame(**spec)
        # A few decisions so the entity set, hand and pending rows move.
        for _ in range(6):
            vector.step(
                encoder_spike.draw_actions(
                    vector.masks, np.random.default_rng(7)
                )
            )
        vectorized.encode(vector_frame)
        per_lane.encode(lane_frame, [0, 1, 2, 3])

        assert encoder_spike._compare_frames(lane_frame, vector_frame, [0, 1, 2, 3]) == []


def test_encoder_packs_the_hand_plate_like_the_bridge() -> None:
    from crforge_bridge.catalog import FORM_BASE, FORM_EVOLVED, FORM_HERO, pack_card_parameter

    slot = np.array([[[0, 5, 7, 2]]], dtype=np.int32)
    level = np.array([[[11, 16, 1, 9]]], dtype=np.int32)
    cost = np.array([[[3, 4, 10, 2]]], dtype=np.int32)
    form = np.array([[[FORM_EVOLVED, FORM_HERO, FORM_BASE, FORM_EVOLVED]]], dtype=np.int32)
    flags = np.array([[[1, 2, 0, 3]]], dtype=np.int32)
    present = np.array([[[True, True, True, False]]])
    packed = encoder_spike._pack_parameters(slot, level, cost, form, flags, present)
    for index in range(3):
        assert int(packed[0, 0, index]) == pack_card_parameter(
            deck_slot=int(slot[0, 0, index]),
            level=int(level[0, 0, index]),
            cost=int(cost[0, 0, index]),
            form_code=int(form[0, 0, index]),
            slot_flags=int(flags[0, 0, index]),
        )
    assert int(packed[0, 0, 3]) == 0
