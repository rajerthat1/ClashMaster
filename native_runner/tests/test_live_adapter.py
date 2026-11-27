"""Official-client live adapter: frames -> FAIR observation -> V4 tensor/Predict.

These tests need no device and no reader binary.  They replay synthetic frames
through the same code path the live runner uses, including a real RoyaleFish
checkpoint when it is present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from native_runner.battle_env import TOWER_LAYOUT as ENV_TOWER_LAYOUT
from native_runner.contracts import ActionKind, ActionV1, EpisodeConfigV1, ObservationTier, ObservationV1
from native_runner.live.frames import (
    TOWER_LAYOUT,
    LiveFrame,
    normalize_frame,
    parse_frame,
)
from native_runner.live.observation import LiveObservationConfig, build_live_observation
from native_runner.live.taps import ScreenLayout, taps_for_action, taps_for_actions

REPO_ROOT = Path(__file__).resolve().parents[2]
RF_CHECKPOINT = REPO_ROOT / ".resource" / "RoyaleFish" / "models" / "B-general-v275.pt"
DECK0 = (26000001, 26000084, 28000000, 26000000, 26000010, 27000006, 28000011, 27000008)
DECK1 = (27000000, 28000000, 26000021, 26000038, 26000030, 26000014, 26000010, 28000011)


def _r0_frame(tick: int = 200, *, side: int = 0, elixir_raw: int = 50_000) -> dict:
    return {
        "battle_active": True,
        "game_tick": tick,
        "players": [
            {
                "side": side,
                "elixir_raw": elixir_raw,
                "hand_deck_indices": [0, 1, 2, 3],
                "cycle_deck_indices": [4, 5, 6, 7],
            }
        ],
        "entities": [
            {
                "address": 0x1000,
                "side": side,
                "card_id": DECK0[0],
                "x": 9000.0,
                "y": 5000.0,
                "hp": 100.0,
                "max_hp": 119.0,
                "level": 11,
                "category": "unit",
            },
            {
                "address": 0x2000,
                "side": 1 - side,
                "card_id": DECK1[2],
                "x": 9000.0,
                "y": 27000.0,
                "hp": 200.0,
                "max_hp": 220.0,
                "level": 11,
                "category": "unit",
            },
        ],
    }


def _hasty_frame(tick: int = 200) -> dict:
    return {
        "t": tick,
        "me": [0, 50_000, [0, 1, 2, 3], [4, 5, 6, 7]],
        "u": [
            [0x1000, 4, 15, 0, 9000, 5000, DECK0[0], 11, 100, 119, "Knight"],
            [0x2000, 4, 15, 1, 9000, 27000, DECK1[2], 11, 200, 220, "HogRider"],
        ],
    }


def test_tower_layout_matches_environment() -> None:
    assert TOWER_LAYOUT == ENV_TOWER_LAYOUT


def test_parse_both_readers() -> None:
    r0 = parse_frame(_r0_frame())
    assert r0.active and r0.source == "r0" and r0.tick == 200
    assert r0.player(0).elixir == pytest.approx(5.0)
    assert len(r0.entities) == 2

    hasty = parse_frame(_hasty_frame())
    assert hasty.active and hasty.source == "hasty"
    assert hasty.player(0).hand_indices == (0, 1, 2, 3)
    assert hasty.entities[0].name == "Knight"


def test_inactive_frame_is_not_a_battle() -> None:
    assert not parse_frame({"t": -1}).active
    assert not parse_frame({"battle_active": False}).active


def test_normalize_fills_six_towers_and_decks() -> None:
    frame = normalize_frame(parse_frame(_r0_frame()), decks=(DECK0, DECK1))
    assert len(frame.towers) == 6
    assert frame.player(0).deck_card_ids == DECK0
    assert {tower.tower_kind for tower in frame.towers} == {"king", "princess_left", "princess_right"}
    # The synthesized towers are full HP, and the entity list keeps only troops.
    assert all(tower.hitpoints == tower.max_hitpoints for tower in frame.towers)
    assert len(frame.entities) == 2


def test_normalize_promotes_a_real_tower_entity() -> None:
    payload = _r0_frame()
    payload["entities"].append(
        {
            "address": 0x3000,
            "side": 0,
            "card_id": 159000000,
            "x": 9000.0,
            "y": 3000.0,
            "hp": 1200.0,
            "max_hp": 4824.0,
            "level": 11,
            "category": "tower",
        }
    )
    frame = normalize_frame(parse_frame(payload), decks=(DECK0, DECK1))
    king = next(tower for tower in frame.towers if tower.owner == 0 and tower.tower_kind == "king")
    assert king.hitpoints == pytest.approx(1200.0)
    assert len(frame.entities) == 2  # the tower entity was consumed, not duplicated


def test_build_observation_contract() -> None:
    frame = normalize_frame(parse_frame(_r0_frame()), decks=(DECK0, DECK1))
    config = LiveObservationConfig(ruleset_id="test-live", decks=(DECK0, DECK1))

    class _Spec:
        elixir_cost = 3.0

    class _Bundle:
        card_specs = {card: _Spec() for card in set(DECK0) | set(DECK1)}

    observation = build_live_observation(frame, actor_owner=0, config=config, bundle=_Bundle())
    assert isinstance(observation, ObservationV1)
    assert observation.owner == 0 and observation.tick == 200
    assert observation.players[0].hand == DECK0[:4]
    assert observation.players[0].elixir_exact == pytest.approx(5.0)
    assert observation.action_mask.kinds[ActionKind.WAIT.value] is True
    assert len(observation.towers) == 6
    assert observation.action_mask.reasons["effective_elixir"] == pytest.approx(5.0)
    assert observation.players[1].private_state_visible is False


def test_owner_one_observation_keeps_world_coordinates() -> None:
    frame = normalize_frame(parse_frame(_r0_frame(side=1)), decks=(DECK0, DECK1))
    config = LiveObservationConfig(ruleset_id="test-live", decks=(DECK0, DECK1))

    class _Spec:
        elixir_cost = 3.0

    class _Bundle:
        card_specs = {card: _Spec() for card in set(DECK0) | set(DECK1)}

    observation = build_live_observation(frame, actor_owner=1, config=config, bundle=_Bundle())
    assert observation.owner == 1
    # World coordinates are passed through; the V4 perspective flips them.
    assert observation.entities[0].position == (9000.0, 5000.0)


def test_tap_plan_for_a_card_play() -> None:
    layout = ScreenLayout.from_size(1080, 1920)
    action = ActionV1.play(0, hand_slot=1, grid=(9, 28))
    points = taps_for_action(action, layout)
    assert len(points) == 2
    hand, target = points
    assert hand == layout.hand_point(1)
    assert 0 <= target[0] < 1080 and 0 <= target[1] < 1920
    # V4's grid keeps the owner's own side at low y (world y=0 is the bottom
    # of the renderer), so row 28 is the enemy back and row 4 is ours.
    assert target[1] < layout.cell_point((9, 4))[1]


def test_wait_has_no_taps_and_ability_refuses() -> None:
    layout = ScreenLayout.from_size(1080, 1920)
    assert taps_for_actions((ActionV1.wait(0),), layout) == ()
    with pytest.raises(Exception):
        taps_for_action(
            ActionV1(owner=0, kind=ActionKind.ACTIVATE_ABILITY, source_entity=1),
            layout,
        )


@pytest.fixture(scope="module")
def loaded_rf():
    if not RF_CHECKPOINT.is_file():
        pytest.skip("RoyaleFish checkpoint is not present")
    from native_runner.training.v4.policy_session import load_policy_v4

    return load_policy_v4(RF_CHECKPOINT, device="cpu")


def test_rf_tensorizes_and_decides_a_live_frame(loaded_rf) -> None:
    from native_runner.training.v4.factory import build_episode_tensorizer_v4
    from native_runner.training.v4.policy_session import PolicySessionV4

    frame = normalize_frame(parse_frame(_r0_frame(tick=200, elixir_raw=95_000)), decks=(DECK0, DECK1))
    config = LiveObservationConfig(ruleset_id="test-live", decks=(DECK0, DECK1))
    observation = build_live_observation(
        frame,
        actor_owner=0,
        config=config,
        bundle=loaded_rf.model.semantic_bundle,
    )
    # A 9.5-elixir hand must expose legal plays; a blocked placement mask here
    # is the bug that made every live policy turn a PASS.
    assert any(observation.action_mask.hand_slots)
    assert observation.action_mask.kinds[ActionKind.PLAY_CARD.value] is True
    episode = EpisodeConfigV1(
        ruleset_id="test-live",
        deck0=DECK0,
        deck1=DECK1,
        seed=0,
        observation_tier=ObservationTier.FAIR,
        tags={"deck0_form_availability": (0,) * 8, "deck1_form_availability": (0,) * 8},
    )
    tensorizer = build_episode_tensorizer_v4(
        episode,
        actor_owner=0,
        bundle=loaded_rf.model.semantic_bundle,
        config=loaded_rf.model.config,
    )
    session = PolicySessionV4(loaded_rf.model, tensorizer, device="cpu", sample=False)
    session.start_episode(observation, initial_elixir={0: 5.0, 1: 6.0})
    decision = session.decide(observation)
    assert decision.decoded.owner == 0
    assert decision.decoded.actions
    for action in decision.decoded.actions:
        assert action.kind in (
            ActionKind.WAIT,
            ActionKind.WAIT_UNTIL_AFFORDABLE,
            ActionKind.PLAY_CARD,
        )
