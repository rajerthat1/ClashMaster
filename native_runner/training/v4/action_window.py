"""Keep a decision's micro-actions on separate engine ticks.

The engine takes **one** action per seat per tick, while a V4 decision may carry
two micro-actions, each with its own ``execute_offset_ticks`` (``1 + delay
bin``).  Two micro-actions that land on the same tick lose one: the engine runs
the first and leaves the other card in its hand slot, which the bridge then
attests as ``card_still_in_slot`` (the native notes).

The crforge producer already spaces collisions with
``crforge_bridge.vector_actions.plan_window_ticks`` (§3.44/§3.57).  The native
``PolicySessionV4`` path had no equivalent, so a policy that sampled two
micro-actions on one tick emitted a command sequence no client could queue.
This module is the native counterpart: it rewrites the *execution* tick of the
later micro-action (and never the sampled delay bin, which stays the training
target) before the actions reach ``BattleEnvV1``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from ...contracts import ActionKind, ActionV1

#: Action kinds that occupy one engine tick.  A WAIT never mixes with micro
#: actions (``BattleEnvV1._validate_compound_actions``), so it is passed through.
_TICK_ACTIONS = frozenset({ActionKind.PLAY_CARD, ActionKind.ACTIVATE_ABILITY})


def separate_micro_action_ticks(actions: Sequence[ActionV1], *, window: int) -> tuple[tuple[ActionV1, ...], int, int]:
    """Give each tick-consuming micro-action its own engine tick in ``window``.

    ``actions`` is one decision's decoded commands, in submission order.  An
    ``execute_offset_ticks`` of ``1`` is the first tick after the decision, so
    the tick inside the window is ``offset - 1``.  Actions keep their order; the
    later one is shifted forward to the next free tick when two collide.

    If the window has no free tick left the action is **dropped**, and both the
    shift and the drop are counted so a caller can record the fidelity
    difference instead of hiding it.  A shifted action keeps its sampled
    ``policy_delay_offset_bin`` metadata (the elixir/legality check stays where
    the policy sampled it) and only its execution tick moves, exactly as the
    crforge producer does.

    :returns: ``(actions, shifted, dropped)`` — the kept actions in submission
        order, the number of ticks shifted, and the number of dropped actions.
    :raises ValueError: for a non-positive window or an offset ``<= 0``
    """

    window = int(window)
    if window < 1:
        raise ValueError("a decision window needs at least one engine tick")
    kept: list[ActionV1] = []
    used: set[int] = set()
    shifted = 0
    dropped = 0
    for action in actions:
        if action.kind not in _TICK_ACTIONS:
            kept.append(action)
            continue
        requested = int(action.execute_offset_ticks)
        if requested < 1:
            raise ValueError("an action's execute offset is at least one tick")
        tick = requested - 1
        while tick < window and tick in used:
            tick += 1
        if tick >= window:
            dropped += 1
            continue
        shifted += tick - (requested - 1)
        used.add(tick)
        if tick != requested - 1:
            metadata = dict(action.metadata)
            if "execute_offset_ticks" in metadata:
                metadata["execute_offset_ticks"] = tick + 1
            action = replace(action, execute_offset_ticks=tick + 1, metadata=metadata)
        kept.append(action)
    return tuple(kept), shifted, dropped
