"""Raw facts from a crforge seat observation.

The bridge emits the facts ``BattleEnvV1`` already knows how to encode. It does
not build ``ObservationV1`` here: the field names below are the native probe's
ordinary and rich envelopes, filled from ``Schema``'s integer rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .catalog import FORM_BASE, FORM_EVOLVED, FORM_HERO, Catalog, pack_card_parameter

UNKNOWN = -1
STATUS_ENUM = ["authoritative", "derived", "pending", "unavailable"]

_PROVENANCE = {
    "nativeObjectId": "authoritative",
    "dataGlobalId": "authoritative",
    "entityKey": "derived",
    "targetEntityKey": "authoritative",
    "targetEntityValidated": "derived",
    "shield.current": "authoritative",
    "shield.max": "authoritative",
    "attackSequenceStage": "authoritative",
    "activeEffects": "authoritative",
    "activeEffects.buffGlobalId": "authoritative",
    "activeEffects.name": "authoritative",
    "activeEffects.remainingMs": "authoritative",
    "activeEffects.sourceEntityKey": "authoritative",
    "activeEffects.sourceEntityValidated": "derived",
    "invisibleCount": "authoritative",
    "visibilityState": "derived",
    "components.slots.vtableLibgOffset": "authoritative",
    "components.slots.typeGetterLibgOffset": "authoritative",
    "components.slots.engineType": "authoritative",
    "projectile": "derived",
    "projectile.projectileDataGlobalId": "derived",
    "projectile.sourceEntityKey": "derived",
    "projectile.targetEntityKey": "derived",
    "projectile.homingTargetEntityKey": "derived",
    "projectile.destination": "derived",
    "projectile.terminal": "derived",
    "projectile.nativePhase": "derived",
    "projectile.dragStage": "authoritative",
    "entityResourceRuntime": "authoritative",
    "periodicAttackModifierRuntime": "authoritative",
    "captureRuntime": "authoritative",
    "thresholdRelocationRuntime": "authoritative",
    "players.ownerRoot": "derived",
    "players.abilityRuntime": "derived",
    "players.abilityRuntime.buttonState": "derived",
    "players.abilityRuntime.cooldown": "derived",
    "players.abilityRuntime.charges": "derived",
    "players.evolutionRuntime": "derived",
    "players.evolutionRuntime.cycle": "derived",
    "players.evolutionRuntime.playedForm": "unavailable",
}

_CAPABILITIES = {
    "entityCore": "authoritative",
    "targetCoordinates": "authoritative",
    "targetEntity": "authoritative",
    "hitpoints": "authoritative",
    "componentInventory": "authoritative",
    "shield": "authoritative",
    "attackSequenceStage": "authoritative",
    "buffs": "authoritative",
    "debuffs": "unavailable",
    "slow": "unavailable",
    "rage": "unavailable",
    "stun": "unavailable",
    "freeze": "unavailable",
    "attackPhase": "unavailable",
    "deployPhase": "unavailable",
    "chargeStage": "unavailable",
    "sourceEntity": "unavailable",
    "projectile": "derived",
    "impact": "unavailable",
    "visibility": "unavailable",
    "invisibility": "authoritative",
    "abilityRuntime": "derived",
    "evolutionRuntime": "derived",
}


def _record(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "source": "crforge",
        "validation": "tables-and-schema",
        "confidence": status,
        "failClosed": True,
    }


@dataclass(frozen=True)
class Row:
    """One entity row of a seat's observation, already named."""

    slot: int
    entity_id: int
    kind: int
    side: int
    row_name: str
    card_id: int | None
    x: int
    y: int
    hp: int
    max_hp: int
    shield: int
    level: int
    target_id: int
    target_x: int
    target_y: int
    flags: int
    deploy_ms: int


def decode_rows(observation: list[int], schema, vocabulary, catalog: Catalog) -> tuple[list[Row], dict[str, int]]:
    """Decode one seat observation's header and entity rows."""

    header: dict[str, int] = {
        "tick": observation[schema.H_TICK],
        "result": observation[schema.H_RESULT],
        "elixir": observation[schema.H_ELIXIR],
        "crowns": observation[schema.H_CROWNS],
        "crowns_enemy": observation[schema.H_CROWNS_ENEMY],
        "next_card": observation[schema.H_NEXT_CARD],
        "entity_count": observation[schema.H_ENTITY_COUNT],
        "time_ms": observation[schema.H_TIME_MS],
        "illegal": observation[schema.H_ILLEGAL],
        "last_refusal": observation[schema.H_LAST_REFUSAL],
        "last_run_refusal": observation[schema.H_LAST_RUN_REFUSAL],
    }
    names = vocabulary.names()
    rows: list[Row] = []
    for slot in range(header["entity_count"]):
        base = schema.HEADER_INTS + slot * schema.ENTITY_INTS
        name = str(names[observation[base + schema.E_ROW]])
        rows.append(
            Row(
                slot=slot,
                entity_id=observation[base + schema.E_ID],
                kind=observation[base + schema.E_KIND],
                side=observation[base + schema.E_SIDE],
                row_name=name,
                card_id=_nearest_card_id(catalog, name),
                x=observation[base + schema.E_X],
                y=observation[base + schema.E_Y],
                hp=observation[base + schema.E_HP],
                max_hp=observation[base + schema.E_MAX_HP],
                shield=observation[base + schema.E_SHIELD],
                level=observation[base + schema.E_LEVEL],
                target_id=observation[base + schema.E_TARGET],
                target_x=observation[base + schema.E_TARGET_X],
                target_y=observation[base + schema.E_TARGET_Y],
                flags=observation[base + schema.E_FLAGS],
                deploy_ms=observation[base + schema.E_DEPLOY_MS],
            )
        )
    return rows, header


def _nearest_card_id(catalog: Catalog, row_name: str) -> int | None:
    """The card id a crforge row belongs to, or None for a spawned row.

    A row is a card itself, its evolution, or its hero form. A spawned unit
    (``Skeleton``, ``LavaPup``, a projectile) has no card of its own in the
    current catalog and is reported as unknown rather than guessed onto a
    parent.
    """

    direct = catalog.card_id(row_name)
    if direct is not None:
        return direct
    for suffix in ("_EV1", "_hero"):
        if row_name.endswith(suffix):
            return catalog.card_id(row_name[: -len(suffix)])
    return None


def entity_rows(
    observation: list[int], schema, vocabulary, catalog: Catalog
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Row]]:
    """Decode one seat's objects into (ordinary facts, rich facts, rows)."""

    rows, _header = decode_rows(observation, schema, vocabulary, catalog)
    owner_by_id: dict[int, int] = {row.entity_id: row.side for row in rows}
    characters = (int(schema.KIND_CHARACTER), int(schema.KIND_TOWER))
    # Projectiles and area effects are Phase 5. Until their rich runtime exists,
    # emitting them with a synthetic card/global id makes the policy tensorizer
    # treat them as unknown characters. Keep the vector to live characters and
    # towers, which is what the current envelope claims to supply.
    visible_rows = [row for row in rows if int(row.kind) in characters]
    objects: list[dict[str, Any]] = []
    rich: list[dict[str, Any]] = []
    for row in visible_rows:
        native_id = row.entity_id + 1
        target_native_id = row.target_id + 1 if row.target_id >= 0 else None
        target_owner = owner_by_id.get(row.target_id) if row.target_id >= 0 else None
        target_key = (
            [target_owner, -2, target_native_id]
            if target_native_id is not None and target_owner is not None
            else None
        )
        hp = None if row.hp == UNKNOWN else row.hp
        max_hp = None if row.max_hp == UNKNOWN else row.max_hp
        shield = None
        if row.shield > 0:
            # Schema exposes only the current shield, so `max` is not claimed:
            # the rich adapter accepts the current value on its own.
            shield = {"current": row.shield, "max": row.shield, "status": "authoritative"}
        common = {
            "slot": row.slot,
            "nativeObjectId": native_id,
            "entityKey": [row.side, -2, native_id],
            "owner": row.side,
            "cardId": row.card_id or 0,
            "dataGlobalId": max(1, row.card_id or 0),
            "x": row.x,
            "y": row.y,
            "targetX": row.target_x,
            "targetY": row.target_y,
            "targetEntityKey": target_key,
            "targetEntityValidated": target_key is not None,
            "objectIndex": row.slot,
            "secondaryIndex": 0,
            "hp": hp,
            "maxHp": max_hp,
            "shield": shield,
            "attackSequenceStage": None,
            "invisibleCount": None,
            "visibilityState": None,
            "projectile": None,
            "entityResourceRuntime": None,
            "periodicAttackModifierRuntime": None,
            "captureRuntime": None,
            "thresholdRelocationRuntime": None,
            "activeEffects": [],
            "components": {"valid": False, "count": 0, "capacity": 0, "slots": []},
        }
        rich.append(common)
        objects.append(
            {
                **{
                    key: common[key]
                    for key in (
                        "slot",
                        "nativeObjectId",
                        "entityKey",
                        "owner",
                        "cardId",
                        "x",
                        "y",
                        "hp",
                        "maxHp",
                        "shield",
                        "targetX",
                        "targetY",
                        "targetEntityKey",
                        "targetEntityValidated",
                        "objectIndex",
                        "secondaryIndex",
                    )
                },
                "kind": row.kind,
                "rowName": row.row_name,
                "level": row.level,
                "flags": row.flags,
                "deployMs": row.deploy_ms,
            }
        )
    return objects, rich, visible_rows


def player_facts(
    observation: list[int],
    schema,
    vocabulary,
    catalog: Catalog,
    *,
    owner: int,
) -> dict[str, Any]:
    """One player's private facts from that player's own seat observation."""

    names = vocabulary.names()
    deck_cards = [observation[schema.H_DECK + slot] for slot in range(8)]
    hand: list[dict[str, Any]] = []
    for hand_index in range(4):
        card_vocab_id = observation[schema.H_HAND_CARD + hand_index]
        if card_vocab_id < 0:
            continue
        card_name = str(names[card_vocab_id])
        card_id = catalog.card_id(card_name) or _nearest_card_id(catalog, card_name)
        if card_id is None:
            continue
        deck_slot = next((slot for slot, value in enumerate(deck_cards) if value == card_vocab_id), -1)
        if deck_slot < 0:
            deck_slot = next(
                (
                    slot
                    for slot, value in enumerate(deck_cards)
                    if value >= 0 and _nearest_card_id(catalog, str(names[value])) == card_id
                ),
                -1,
            )
        form = observation[schema.H_HAND_FORM + hand_index]
        form_code = FORM_EVOLVED if form == FORM_EVOLVED else FORM_HERO if form == FORM_HERO else FORM_BASE
        cost = observation[schema.H_HAND_COST + hand_index]
        slot_flags = observation[schema.H_DECK_SLOTS + deck_slot] if deck_slot >= 0 else 0
        level = observation[schema.H_DECK_LEVEL + deck_slot] if deck_slot >= 0 else 1
        parameter = pack_card_parameter(
            deck_slot=max(0, deck_slot),
            level=max(1, level),
            cost=max(0, min(15, cost)),
            form_code=form_code,
            slot_flags=max(0, slot_flags) & 0x7,
        )
        hand.append(
            {
                "handIndex": hand_index,
                "cardId": card_id,
                "commandCardId": card_id,
                "cardParameter": parameter,
                "cost": cost,
                "deckSlot": max(0, deck_slot),
            }
        )

    deck = []
    for slot, card_vocab_id in enumerate(deck_cards):
        if card_vocab_id < 0:
            continue
        card_name = str(names[card_vocab_id])
        card_id = catalog.card_id(card_name) or _nearest_card_id(catalog, card_name) or 0
        deck.append(
            {
                "cardId": card_id,
                "slot": slot,
                "formFlags": observation[schema.H_DECK_SLOTS + slot],
                "level": observation[schema.H_DECK_LEVEL + slot],
            }
        )

    next_vocab = observation[schema.H_NEXT_CARD]
    next_card = None
    if next_vocab >= 0:
        next_name = str(names[next_vocab])
        next_id = catalog.card_id(next_name) or _nearest_card_id(catalog, next_name) or 0
        next_card = {"cardId": next_id, "cardParameter": None}

    cycle = []
    for index in range(4):
        value = observation[schema.H_QUEUE + index]
        if value < 0:
            continue
        name = str(names[value])
        card_id = catalog.card_id(name) or _nearest_card_id(catalog, name) or 0
        cycle.append({"cardId": card_id})

    # crforge already knows which hand slots have a play waiting to run.  The
    # seat header's per-slot flag is the authoritative projection: without it a
    # policy can replay a card the engine has promised and get CARD_PENDING.
    pending_hand_slots = [
        hand_index
        for hand_index in range(4)
        if int(observation[schema.H_HAND_PENDING + hand_index]) > 0
    ]
    pending: list[dict[str, Any]] = []
    pending_count = int(observation[schema.H_PENDING_COUNT])
    for row in range(min(max(0, pending_count), int(schema.PENDING_ROWS))):
        base = schema.H_PENDING + row * schema.PENDING_INTS
        card_vocab_id = observation[base]
        if card_vocab_id < 0:
            continue
        pending_name = str(names[card_vocab_id])
        pending.append(
            {
                "cardId": catalog.card_id(pending_name) or _nearest_card_id(catalog, pending_name) or 0,
                "x": int(observation[base + 1]),
                "y": int(observation[base + 2]),
                "ticksUntilRun": int(observation[base + 3]),
            }
        )

    return {
        "owner": owner,
        "accountId": owner,
        "elixirRaw": observation[schema.H_ELIXIR],
        "elixirReservedRaw": observation[schema.H_ELIXIR_RESERVED],
        "crownsRaw": observation[schema.H_CROWNS],
        "hand": hand,
        "deck": deck,
        "nextCard": next_card,
        "cycle": cycle,
        "handSize": len(hand),
        "pending": pending,
        "pendingHandSlots": pending_hand_slots,
        "lastRefusal": int(observation[schema.H_LAST_REFUSAL]),
        "lastRunRefusal": int(observation[schema.H_LAST_RUN_REFUSAL]),
    }


def _combat_events(tick: int, generation: int, state_epoch: int) -> dict[str, Any]:
    return {
        "ok": True,
        "schema": "native-combat-events.v1",
        "generation": generation,
        "stateEpoch": state_epoch,
        "observationTick": tick,
        "capacity": 1024,
        "hookSetAttested": False,
        "hookSetInstalled": False,
        "capability": _record("unavailable"),
        "epochFirstSequence": 1,
        "oldestRetainedSequence": 1,
        "nextSequence": 1,
        "overflowCount": 0,
        "sequenceGapBeforeOldest": False,
        "complete": True,
        "events": [],
        "rejectedCaptureCount": 0,
    }


def rich_envelope(
    *,
    tick: int,
    generation: int,
    state_epoch: int,
    rich_objects: list[dict[str, Any]],
    players: list[dict[str, Any]],
) -> dict[str, Any]:
    """The minimal v3 rich envelope the native validators accept.

    Everything crforge cannot yet supply is an explicit null, never a guess.
    The provenance/capability records say which facts are authoritative for
    crforge and which are merely derived from the seat's view.
    """

    return {
        "ok": True,
        "schema": "native-rich-telemetry.v3",
        "statusEnum": list(STATUS_ENUM),
        "generation": generation,
        "stateEpoch": state_epoch,
        "tick": tick,
        "provenance": {name: _record(status) for name, status in _PROVENANCE.items()},
        "capabilities": {name: _record(status) for name, status in _CAPABILITIES.items()},
        "count": len(rich_objects),
        "returned": len(rich_objects),
        "truncated": False,
        "objects": rich_objects,
        "players": players,
        "combatEvents": _combat_events(tick, generation, state_epoch),
    }


def empty_player(owner: int) -> dict[str, Any]:
    return {
        "owner": owner,
        "ownerRootValidated": False,
        "ownerEntityKey": None,
        "abilityRuntime": None,
        "evolutionRuntime": None,
    }


def atomic_envelope(
    *,
    tick: int,
    generation: int,
    state_epoch: int,
    objects,
    rich_objects,
    players,
    rich_players,
    ended: bool,
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema": "native-observation-capture.v1",
        "atomic": True,
        "maxObjects": 256,
        "tick": tick,
        "generation": generation,
        "stateEpoch": state_epoch,
        "ended": bool(ended),
        "ordinary": {
            "ok": True,
            "tick": tick,
            "generation": generation,
            "stateEpoch": state_epoch,
            "ended": bool(ended),
            "count": len(objects),
            "returned": len(objects),
            "truncated": False,
            "objects": objects,
            "players": players,
        },
        "rich": rich_envelope(
            tick=tick,
            generation=generation,
            state_epoch=state_epoch,
            rich_objects=rich_objects,
            players=rich_players,
        ),
    }
