"""Build a V4 ``ObservationV1`` from one official-client memory frame.

The policy only needs the public FAIR view.  This adapter therefore emits:

* the actor's player record (elixir, hand, deck, cycle),
* the six fixed towers,
* public troops/buildings,
* a fully materialized action mask (hand slots plus 18x32 placement rows).

It deliberately does **not** invent opponent hand/elixir, combat events,
abilities, or evolution runtime state.  Those are unknown on the live client
and the tensorizer treats them as absent rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping, Sequence

from ..arena import card_placement_mask
from ..contracts import (
    ActionKind,
    ActionMaskV1,
    EntityStateV1,
    ObservationTier,
    ObservationV1,
    PlayerStateV1,
    TimeStateV1,
    TowerStateV1,
)
from ..contracts import BOARD_HEIGHT, BOARD_WIDTH, DEFAULT_TICK_MS
from .frames import (
    DEFAULT_TOWER_TROOP_ID,
    LiveEntity,
    LiveFrame,
    LivePlayer,
    LiveTower,
)

DEFAULT_TOWER_TROOP_IDS = (DEFAULT_TOWER_TROOP_ID, DEFAULT_TOWER_TROOP_ID)


class LiveObservationError(ValueError):
    """The frame cannot be represented as a FAIR V4 observation."""


@dataclass(frozen=True, slots=True)
class LiveObservationConfig:
    """Everything the live frame cannot tell us by itself."""

    ruleset_id: str
    decks: tuple[tuple[int, ...], tuple[int, ...]]
    tower_troop_ids: tuple[int, int] = DEFAULT_TOWER_TROOP_IDS
    phase: str = "regular"
    game_mode: int = 72000006
    arena: int = 54000001
    episode_id: str | None = "official-live"

    def __post_init__(self) -> None:
        if not self.ruleset_id:
            raise LiveObservationError("ruleset_id is required")
        if len(self.decks) != 2 or any(len(deck) != 8 for deck in self.decks):
            raise LiveObservationError("decks must be two eight-card tuples")


_SHA256 = re.compile(r"[0-9a-f]{64}")


def ruleset_digest(value: str) -> str:
    """Placement masks require a lowercase SHA-256; accept a readable label too."""

    if _SHA256.fullmatch(value):
        return value
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _resolve_slot_card(value: int, deck: Sequence[int]) -> int:
    """Accept a deck index (0..7) or a card ID and return the card ID."""

    if 0 <= value < len(deck):
        return int(deck[value])
    if value in deck:
        return int(value)
    raise LiveObservationError(f"hand value {value} is neither a deck index nor a deck card")


def _hand_cards(player: LivePlayer, deck: Sequence[int]) -> tuple[int, ...]:
    cards = tuple(_resolve_slot_card(int(value), deck) for value in player.hand_indices[:4])
    if len(cards) != 4:
        raise LiveObservationError(f"actor hand has {len(cards)} cards, expected 4")
    return cards


def _cycle_cards(player: LivePlayer, deck: Sequence[int]) -> tuple[int, ...]:
    result: list[int] = []
    for value in player.cycle_indices:
        try:
            result.append(_resolve_slot_card(int(value), deck))
        except LiveObservationError:
            continue
    return tuple(result)


def _placement_entry(
    *,
    card_id: int,
    owner: int,
    spec: Any,
    ruleset_id: str,
    cost: float,
) -> tuple[dict[str, Any], bool]:
    """One V4 placement contract, or an explicit blocked entry."""

    form = "base"
    try:
        artifact = card_placement_mask(spec, owner, ruleset_id=ruleset_id, form=form)
        rows = [list(row) for row in artifact.rows]
        entry = {
            "card_id": card_id,
            "visible_card_id": card_id,
            "effective_card_id": card_id,
            "native_effective_card_id": card_id,
            "effective_cost": float(cost),
            "form_code": 0,
            "native_form_code": 0,
            "shape": [BOARD_HEIGHT, BOARD_WIDTH],
            "row_major": rows,
            "placement_rule": artifact.rule.value,
            "accuracy": artifact.accuracy.value,
            "mask_id": artifact.mask_id,
            "algorithm": artifact.algorithm,
            "model_subcell_offset": (
                None if artifact.model_subcell_offset is None else list(artifact.model_subcell_offset)
            ),
            "reasons": list(artifact.reasons),
        }
        return entry, any(any(row) for row in rows)
    except Exception as error:  # noqa: BLE001 - a mask failure must block, not crash the loop
        return (
            {
                "card_id": card_id,
                "visible_card_id": card_id,
                "effective_card_id": card_id,
                "native_effective_card_id": card_id,
                "effective_cost": float(cost),
                "form_code": 0,
                "native_form_code": 0,
                "shape": [BOARD_HEIGHT, BOARD_WIDTH],
                "row_major": [[False] * BOARD_WIDTH for _ in range(BOARD_HEIGHT)],
                "placement_rule": "unknown",
                "accuracy": "blocked",
                "reasons": [f"placement_unavailable:{type(error).__name__}"],
            },
            False,
        )


def _card_cost(spec: Any, fallback: float = 10.0) -> float:
    raw = getattr(spec, "elixir_cost", None)
    return float(raw) if raw is not None else fallback


def _entity(
    entity: LiveEntity,
    *,
    valid_cards: Sequence[int],
    form_to_base: Mapping[int, int],
    card_to_global: Mapping[int, int],
    name_to_global: Mapping[str, int],
    has_catalog: bool,
) -> EntityStateV1 | None:
    card_id = int(entity.card_id) if entity.card_id is not None else 0
    if card_id <= 0:
        return None
    card_id = int(form_to_base.get(card_id, card_id))
    if valid_cards and card_id not in valid_cards:
        return None
    global_id = name_to_global.get(entity.name or "") or card_to_global.get(card_id)
    if global_id is None and has_catalog:
        # A spell's on-field area effect has no character record.  Skip it
        # rather than feed the tensorizer an entity it must reject.
        return None
    hp = entity.hitpoints
    max_hp = entity.max_hitpoints
    if hp is None and max_hp is None:
        hp = max_hp = None
    elif hp is None:
        hp = max_hp
    elif max_hp is None:
        max_hp = max(hp, 1.0)
    return EntityStateV1(
        entity_id=int(entity.entity_id),
        owner=int(entity.owner),
        card_id=card_id,
        entity_kind=entity.entity_kind,
        position=(float(entity.x), float(entity.y)),
        hitpoints=hp,
        max_hitpoints=max_hp,
        native_data_global_id=None if global_id is None else int(global_id),
    )


def _tower(tower: LiveTower) -> TowerStateV1:
    return TowerStateV1(
        entity_id=int(tower.entity_id),
        owner=int(tower.owner),
        tower_kind=tower.tower_kind,
        position=(float(tower.x), float(tower.y)),
        hitpoints=float(tower.hitpoints),
        max_hitpoints=float(tower.max_hitpoints),
        tower_troop_id=tower.tower_troop_id,
        active=float(tower.hitpoints) > 0.0,
    )


def build_live_observation(
    frame: LiveFrame,
    *,
    actor_owner: int,
    config: LiveObservationConfig,
    bundle: Any,
    form_to_base: Mapping[int, int] | None = None,
) -> ObservationV1:
    """Convert one normalized frame into the actor's fair observation."""

    if not frame.active:
        raise LiveObservationError("cannot build an observation from an inactive frame")
    if actor_owner not in (0, 1):
        raise LiveObservationError("actor_owner must be 0 or 1")
    actor = frame.player(actor_owner)
    if actor is None:
        raise LiveObservationError(
            f"frame has no player {actor_owner}; readers usually expose only the controlled side"
        )
    deck = tuple(int(card) for card in config.decks[actor_owner])
    hand = _hand_cards(actor, deck)
    cycle = _cycle_cards(actor, deck)
    elixir = actor.elixir

    specs = getattr(bundle, "card_specs", {})
    valid_cards = tuple(sorted(specs))
    archetype_catalog = getattr(bundle, "entity_archetype_catalog", None)
    name_to_global = {
        str(name): int(global_id)
        for global_id, name in getattr(archetype_catalog, "character_global_ids", ())
    }
    card_to_global: dict[int, int] = {}
    for card_id, spec in specs.items():
        name = getattr(spec, "attributes", {}).get("SummonCharacter")
        global_id = name_to_global.get(str(name)) if name else None
        if global_id is not None:
            card_to_global[int(card_id)] = int(global_id)
    has_catalog = bool(name_to_global or card_to_global)
    masks: dict[str, dict[str, Any]] = {}
    hand_runtime: dict[str, dict[str, Any]] = {}
    slots: list[bool] = []
    slot_reasons: dict[str, str] = {}
    any_unaffordable = False
    for slot, card_id in enumerate(hand):
        spec = specs.get(card_id)
        if spec is None:
            masks[str(slot)] = {
                "card_id": card_id,
                "effective_cost": 10.0,
                "form_code": 0,
                "shape": [BOARD_HEIGHT, BOARD_WIDTH],
                "row_major": [[False] * BOARD_WIDTH for _ in range(BOARD_HEIGHT)],
                "placement_rule": "unknown",
                "accuracy": "blocked",
                "reasons": ["card_spec_unavailable"],
            }
            hand_runtime[str(slot)] = {
                "hand_slot": slot,
                "visible_card_id": card_id,
                "effective_card_id": card_id,
                "effective_cost": 10.0,
                "form_code": 0,
            }
            slots.append(False)
            slot_reasons[str(slot)] = "card_spec_unavailable"
            continue
        cost = _card_cost(spec)
        entry, placed = _placement_entry(
            card_id=card_id,
            owner=actor_owner,
            spec=spec,
            ruleset_id=ruleset_digest(config.ruleset_id),
            cost=cost,
        )
        masks[str(slot)] = entry
        hand_runtime[str(slot)] = {
            "hand_slot": slot,
            "visible_card_id": card_id,
            "effective_card_id": card_id,
            "effective_cost": cost,
            "form_code": 0,
        }
        legal = cost <= elixir + 1e-9 and placed
        if cost > elixir + 1e-9:
            any_unaffordable = True
            slot_reasons[str(slot)] = "insufficient_elixir"
        elif not placed:
            slot_reasons[str(slot)] = "no_legal_placement"
        else:
            slot_reasons[str(slot)] = "legal"
        slots.append(legal)

    hand_slot_by_card = {str(card): slot for slot, card in enumerate(hand)}
    player = PlayerStateV1(
        owner=actor_owner,
        elixir_exact=elixir,
        elixir_visible=elixir,
        hand=hand,
        deck=deck,
        cycle=cycle,
        private_state_visible=True,
        metadata={
            "hand_slot_by_card": hand_slot_by_card,
            "hand_runtime_by_slot": hand_runtime,
        },
    )
    opponent = PlayerStateV1(owner=1 - actor_owner)

    action_mask = ActionMaskV1(
        kinds={
            ActionKind.WAIT.value: True,
            ActionKind.WAIT_UNTIL_AFFORDABLE.value: any_unaffordable,
            ActionKind.PLAY_CARD.value: any(slots),
            ActionKind.ACTIVATE_ABILITY.value: False,
        },
        hand_slots=tuple(slots),
        placement_masks=masks,
        ability_sources=(),
        target_entities=(),
        reasons={
            "decision_ready": True,
            "effective_elixir": elixir,
            "reserved_elixir": 0.0,
            "slots": slot_reasons,
        },
    )

    entities = tuple(
        item
        for item in (
            _entity(
                entity,
                valid_cards=valid_cards,
                form_to_base=form_to_base or {},
                card_to_global=card_to_global,
                name_to_global=name_to_global,
                has_catalog=has_catalog,
            )
            for entity in frame.entities
            if entity.owner in (0, 1)
        )
        if item is not None
    )
    towers = tuple(_tower(tower) for tower in frame.towers)
    if len(towers) != 6:
        raise LiveObservationError(f"expected six towers, got {len(towers)}")

    observation = ObservationV1(
        tier=ObservationTier.FAIR,
        tick=int(frame.tick),
        owner=actor_owner,
        time=TimeStateV1(elapsed_ms=int(frame.tick) * DEFAULT_TICK_MS, tick_ms=DEFAULT_TICK_MS),
        phase=config.phase,
        players=(player, opponent),
        towers=towers,
        entities=entities,
        events=(),
        opponent_belief=None,
        action_mask=action_mask,
        pending_actions=(),
        terminal=None,
        native_digest=None,
        ruleset_id=ruleset_digest(config.ruleset_id),
        episode_id=config.episode_id,
        raster=None,
        truncated=False,
        metadata={
            "live_source": frame.source,
            "live_tick": int(frame.tick),
            "game_mode": int(config.game_mode),
            "arena": int(config.arena),
            "towers_observed": bool(frame.raw.get("towers")),
        },
    )
    return observation
