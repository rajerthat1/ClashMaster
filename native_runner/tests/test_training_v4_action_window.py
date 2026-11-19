"""The native half of the one-action-per-tick rule (the native notes)."""

import pytest

from native_runner.contracts import ActionKind, ActionV1
from native_runner.training.v4.action_window import separate_micro_action_ticks


def _play(slot: int, offset: int, *, bin: int | None = None) -> ActionV1:
    metadata = {}
    if bin is not None:
        metadata = {"policy_micro_action_step": slot, "policy_delay_offset_bin": bin, "execute_offset_ticks": offset}
    return ActionV1.play(0, slot, (10, 10), execute_offset_ticks=offset, metadata=metadata)


def test_actions_on_distinct_ticks_pass_through_unchanged() -> None:
    actions = (_play(0, 1), _play(1, 4))

    kept, shifted, dropped = separate_micro_action_ticks(actions, window=5)

    assert kept == actions
    assert (shifted, dropped) == (0, 0)


def test_colliding_micro_action_moves_to_the_next_free_tick() -> None:
    actions = (_play(0, 1), _play(1, 1))

    kept, shifted, dropped = separate_micro_action_ticks(actions, window=5)

    assert [a.execute_offset_ticks for a in kept] == [1, 2]
    # Slot, target and the sampled delay bin are the policy's; only the
    # execution tick moved.
    assert [a.hand_slot for a in kept] == [0, 1]
    assert (shifted, dropped) == (1, 0)


def test_shift_rewrites_the_timing_metadata_but_keeps_the_sampled_bin() -> None:
    actions = (_play(0, 1, bin=0), _play(1, 1, bin=0))

    kept, _, _ = separate_micro_action_ticks(actions, window=5)

    assert kept[0].metadata["execute_offset_ticks"] == 1
    assert kept[1].metadata["execute_offset_ticks"] == 2
    assert kept[1].metadata["policy_delay_offset_bin"] == 0


def test_later_action_fills_a_gap_before_using_later_ticks() -> None:
    actions = (_play(0, 1), _play(1, 3), _play(2, 1))

    kept, shifted, dropped = separate_micro_action_ticks(actions, window=5)

    assert [a.execute_offset_ticks for a in kept] == [1, 3, 2]
    assert (shifted, dropped) == (1, 0)


def test_three_actions_on_one_tick_get_consecutive_ticks() -> None:
    actions = (_play(0, 2), _play(1, 2), _play(2, 2))

    kept, shifted, dropped = separate_micro_action_ticks(actions, window=5)

    assert [a.execute_offset_ticks for a in kept] == [2, 3, 4]
    # Each shifted action counts its own distance: +1 and +2.
    assert (shifted, dropped) == (3, 0)


def test_a_full_window_drops_instead_of_overwriting() -> None:
    actions = (_play(0, 5), _play(1, 5))

    kept, shifted, dropped = separate_micro_action_ticks(actions, window=5)

    assert [a.execute_offset_ticks for a in kept] == [5]
    assert (shifted, dropped) == (0, 1)


def test_ability_activations_consume_a_tick_like_plays() -> None:
    play = _play(0, 1)
    ability = ActionV1(
        owner=0, kind=ActionKind.ACTIVATE_ABILITY, source_entity=7, ability_id="champion", execute_offset_ticks=1
    )

    kept, shifted, dropped = separate_micro_action_ticks((play, ability), window=5)

    assert [a.execute_offset_ticks for a in kept] == [1, 2]
    assert kept[1].kind is ActionKind.ACTIVATE_ABILITY
    assert (shifted, dropped) == (1, 0)


def test_a_wait_is_not_scheduled_on_a_tick() -> None:
    wait = ActionV1.wait(0, ticks=5)

    kept, shifted, dropped = separate_micro_action_ticks((wait,), window=5)

    assert kept == (wait,)
    assert (shifted, dropped) == (0, 0)


def test_invalid_windows_and_offsets_are_refused() -> None:
    with pytest.raises(ValueError, match="at least one engine tick"):
        separate_micro_action_ticks((_play(0, 1),), window=0)
    with pytest.raises(ValueError, match="execute offset"):
        separate_micro_action_ticks((_play(0, 0),), window=5)
