"""Build a live crforge bridge episode for the bulk-path tools.

Two tools need the same thing: a ``BattleEnvV1`` bound to a running bridge, one
``PolicySessionV4`` per seat, and the deck the C0 soak and the evaluator use.
Keeping it here means the C1 spike and the C3 profiler cannot drift apart on
deck, tower troop, release scope or session construction.

The competitive-release scope has to stay open for the whole episode: the
observation build re-checks semantic ids against the active release, so leaving
the scope early would silently switch catalogs mid-episode.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import ExitStack, nullcontext
from typing import Any

# The evaluator's deck: the same eight cards the C0 soak plays, so any
# measurement here sees the entity mix the bulk path will see.
DECK = (26000000, 26000001, 26000005, 28000001, 28000000, 26000003, 26000014, 26000018)
TOWER_TROOP = 159000000
FORM_AVAILABILITY = (0, 0, 0, 0, 0, 0, 0, 0)

# The locked X-Bow learner deck (HANDOFF.md 2026-11-15).  Forms are slot flags on
# the base card: Tesla and Archer evolved, Knight hero.  Kept here beside DECK so
# a tool cannot invent a fifth spelling of the learner deck.
LEARNER_DECK = (27000008, 27000006, 26000001, 26000000, 26000010, 26000084, 28000000, 28000011)
LEARNER_FORM_AVAILABILITY = (0, 1, 1, 2, 0, 0, 0, 0)


def build_episode(
    *,
    port: int | None = None,
    native: Any | None = None,
    checkpoint: str,
    seed: int,
    deck: Sequence[int] | None = None,
    forms: Sequence[int] | None = None,
):
    """Build a live crforge episode; returns ``(environment, observations, sessions, close)``.

    One session per seat, as the evaluator builds them: a seat's own session is
    the only place its hand, cycle and elixir stay consistent, and a one-sided
    board would measure an unrepresentative entity mix.

    Pass ``port`` for the socket bridge, or ``native`` for the resident adapter
    (``native_runner/crforge_resident.py``) — exactly one.  Both present the same
    ``NativeClashEnv`` surface, so the episode is identical by construction.

    ``deck``/``forms`` default to the evaluator deck the C0 soak plays.  A tool
    that measures the *learner* deck (C1's X-Bow list) passes its own, so both
    halves of that comparison see the same cards.
    """

    import torch

    from native_runner.battle_env import (
        ENTITY_LIMIT,
        EVENT_LIMIT,
        EVENT_WINDOW_TICKS,
        STANDARD_MATCH_TICKS,
        BattleEnvV1,
    )
    from native_runner.competitive_release import load_release, use_release
    from native_runner.cr_native_env import DEFAULT_HOST, FIRST_PLAYABLE_TICK, NativeClashEnv
    from native_runner.match_factory import MatchConfig
    from native_runner.rich_telemetry_adapter import RuntimeEffectCatalog
    from native_runner.training.v4.expert import POLICY_DECISION_TICKS
    from native_runner.training.v4.factory import production_semantic_bundle
    from native_runner.training.v4.policy_session import (
        build_policy_session_v4,
        load_policy_v4,
    )

    if (port is None) == (native is None):
        raise ValueError("build_episode takes exactly one of port or native")

    policy = load_policy_v4(checkpoint, device="cpu")
    release = getattr(policy.model, "competitive_release", None)
    bundle = production_semantic_bundle(release)
    if native is None:
        native = NativeClashEnv(DEFAULT_HOST, int(port), timeout=30.0)
    stack = ExitStack()
    stack.enter_context(use_release(load_release(release)) if release else nullcontext())
    # Sampled decoding must be reproducible so a stock/optimized A/B replays the
    # same decisions; the capture digest is recorded to prove it.
    torch.manual_seed(seed)
    environment = BattleEnvV1(
        native=native,
        card_catalog=bundle.native_card_catalog,
        semantic_subset_contract=bundle.semantic_subset_contract,
        effect_catalog=RuntimeEffectCatalog.from_catalog(bundle.native_effect_catalog),
        warmup_ticks=FIRST_PLAYABLE_TICK,
        max_battle_ticks=STANDARD_MATCH_TICKS,
        entity_limit=ENTITY_LIMIT,
        event_limit=EVENT_LIMIT,
        event_window_ticks=EVENT_WINDOW_TICKS,
    )
    chosen_deck = tuple(DECK if deck is None else deck)
    chosen_forms = tuple(FORM_AVAILABILITY if forms is None else forms)
    observations, _ = environment.reset(
        match_config=MatchConfig(
            deck0=chosen_deck,
            deck1=chosen_deck,
            deck0_form_availability=chosen_forms,
            deck1_form_availability=chosen_forms,
            tower_troop0_id=TOWER_TROOP,
            tower_troop1_id=TOWER_TROOP,
            seed=seed,
        ),
        options={
            "render_mode": "headless",
            "decision_ticks": POLICY_DECISION_TICKS,
            "event_driven_decisions": False,
            "allow_initial_remaining_runtime_rejections": True,
        },
    )
    sessions = {
        owner: build_policy_session_v4(
            environment, policy.model, actor_owner=owner, device="cpu", sample=True
        )
        for owner in (0, 1)
    }

    def close() -> None:
        native.close_transport()
        stack.close()

    return environment, observations, sessions, close


def initial_elixir(observations: dict[int, Any]) -> dict[int, float]:
    """Both owners' exact starting elixir; each owner's is in its own seat view."""

    values = {
        player.owner: player.elixir_exact
        for seat in observations.values()
        for player in seat.players
        if player.elixir_exact is not None
    }
    missing = {0, 1} - set(values)
    if missing:
        raise RuntimeError(f"reset observation has no exact elixir for owners {sorted(missing)}")
    return values
