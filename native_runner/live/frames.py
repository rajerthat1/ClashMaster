"""Normalize the two official-client memory readers into one frame shape.

Supported inputs:

* ``r0-elite-training`` ``mumu-live-private`` / ``mumu_live_probe`` frames:
  ``{"battle_active", "game_tick", "players": [...], "entities": [...]}``
* ``Hasty-CR`` ``hcr_reader`` lines:
  ``{"t": tick, "me": [side, elixir_raw, hand, cycle], "u": [[...], ...]}``

Both readers are read-only.  Nothing here opens ``/proc``; the caller owns the
transport so the same parser can be tested without a device.

Coordinates are the game's world units: 18,000 x 32,000 for an 18 x 32 arena
(1,000 units per tile).  The V4 tensorizer divides by that scale itself, so the
adapter passes world units through unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..contracts import BOARD_HEIGHT, BOARD_WIDTH

# Mirrors ``battle_env.TOWER_LAYOUT`` (entity_id, owner, kind, x, y) in world
# units.  ``tests/test_live_frames.py`` asserts the two stay equal.
TOWER_LAYOUT: tuple[tuple[int, int, str, int, int], ...] = (
    (0, 0, "king", 9000, 3000),
    (1, 0, "princess_left", 3500, 6500),
    (2, 0, "princess_right", 14500, 6500),
    (3, 1, "king", 9000, 29000),
    (4, 1, "princess_left", 3500, 25500),
    (5, 1, "princess_right", 14500, 25500),
)

WORLD_WIDTH = BOARD_WIDTH * 1000
WORLD_HEIGHT = BOARD_HEIGHT * 1000

DEFAULT_TOWER_TROOP_ID = 159_000_000
DEFAULT_TOWER_HP = {"king": 4824.0, "princess_left": 3052.0, "princess_right": 3052.0}

_TOWER_MATCH_RADIUS = 1500.0


class FrameError(ValueError):
    """A reader line is not a frame this adapter understands."""


@dataclass(frozen=True, slots=True)
class LivePlayer:
    side: int
    elixir_raw: int = 0
    hand_indices: tuple[int, ...] = ()
    cycle_indices: tuple[int, ...] = ()
    deck_card_ids: tuple[int, ...] = ()

    @property
    def elixir(self) -> float:
        return max(0.0, min(10.0, float(self.elixir_raw) / 10_000.0))


@dataclass(frozen=True, slots=True)
class LiveEntity:
    entity_id: int
    owner: int
    card_id: int | None
    x: float
    y: float
    hitpoints: float | None = None
    max_hitpoints: float | None = None
    level: int | None = None
    entity_kind: str = "unit"
    name: str | None = None


@dataclass(frozen=True, slots=True)
class LiveTower:
    entity_id: int
    owner: int
    tower_kind: str
    x: float
    y: float
    hitpoints: float
    max_hitpoints: float
    tower_troop_id: int | None = None


@dataclass(frozen=True, slots=True)
class LiveFrame:
    tick: int
    active: bool
    players: tuple[LivePlayer, ...] = ()
    entities: tuple[LiveEntity, ...] = ()
    towers: tuple[LiveTower, ...] = ()
    source: str = "unknown"
    terminal: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict)

    def player(self, side: int) -> LivePlayer | None:
        return next((item for item in self.players if item.side == side), None)

    @property
    def sides(self) -> tuple[int, ...]:
        return tuple(sorted({item.side for item in self.players}))


def _int_tuple(value: Any) -> tuple[int, ...]:
    if value is None:
        return ()
    return tuple(int(item) for item in value)


def _entity_kind(value: Any) -> str:
    text = str(value or "").lower()
    if "build" in text or "tower" in text:
        return "building"
    return "unit"


def parse_hasty(payload: Mapping[str, Any]) -> LiveFrame:
    tick = int(payload.get("t", -1))
    if tick < 0:
        return LiveFrame(tick=0, active=False, source="hasty", terminal=True, raw=payload)
    me = payload.get("me")
    if not isinstance(me, Sequence) or len(me) < 4:
        raise FrameError("hasty frame has no 'me' tuple")
    side = int(me[0])
    player = LivePlayer(
        side=side,
        elixir_raw=int(me[1]),
        hand_indices=_int_tuple(me[2]),
        cycle_indices=_int_tuple(me[3]),
    )
    entities: list[LiveEntity] = []
    for index, row in enumerate(payload.get("u") or ()):
        if not isinstance(row, Sequence) or len(row) < 11:
            continue
        address, _category, kind, owner, x, y, card, level, hp, max_hp, name = row[:11]
        if int(owner) not in (0, 1):
            continue
        card_id = int(card)
        if card_id <= 0:
            continue
        entities.append(
            LiveEntity(
                entity_id=int(address) or index + 1,
                owner=int(owner),
                card_id=card_id,
                x=float(x),
                y=float(y),
                hitpoints=float(hp),
                max_hitpoints=float(max_hp),
                level=int(level),
                entity_kind=_entity_kind(kind),
                name=str(name) or None,
            )
        )
    return LiveFrame(
        tick=tick,
        active=True,
        players=(player,),
        entities=tuple(entities),
        source="hasty",
        raw=payload,
    )


def parse_r0(payload: Mapping[str, Any]) -> LiveFrame:
    active = bool(payload.get("battle_active", True))
    tick = int(payload.get("game_tick", payload.get("tick", -1)))
    if not active or tick < 0:
        return LiveFrame(tick=0, active=False, source="r0", terminal=True, raw=payload)
    players: list[LivePlayer] = []
    for row in payload.get("players") or ():
        side = int(row.get("side", -1))
        if side not in (0, 1):
            continue
        players.append(
            LivePlayer(
                side=side,
                elixir_raw=int(row.get("elixir_raw", 0)),
                hand_indices=_int_tuple(row.get("hand_deck_indices") or row.get("hand")),
                cycle_indices=_int_tuple(row.get("cycle_deck_indices") or row.get("cycle")),
                deck_card_ids=_int_tuple(row.get("deck_card_ids")),
            )
        )
    entities: list[LiveEntity] = []
    for index, row in enumerate(payload.get("entities") or ()):
        side = row.get("side")
        if side is None or int(side) not in (0, 1):
            continue
        card_id = row.get("card_id", row.get("card"))
        if card_id is None or int(card_id) <= 0:
            continue
        max_hp = row.get("max_hp", row.get("maxhp", row.get("maxHitpoints")))
        entities.append(
            LiveEntity(
                entity_id=int(row.get("address", row.get("entity_id", index + 1))),
                owner=int(side),
                card_id=int(card_id),
                x=float(row.get("x", 0.0)),
                y=float(row.get("y", 0.0)),
                hitpoints=None if row.get("hp") is None else float(row["hp"]),
                max_hitpoints=None if max_hp is None else float(max_hp),
                level=None if row.get("level") is None else int(row["level"]),
                entity_kind=_entity_kind(row.get("category", row.get("kind"))),
            )
        )
    return LiveFrame(
        tick=tick,
        active=True,
        players=tuple(players),
        entities=tuple(entities),
        source="r0",
        raw=payload,
    )


def parse_frame(payload: Mapping[str, Any]) -> LiveFrame:
    """Auto-detect a reader's frame schema."""

    if not isinstance(payload, Mapping):
        raise FrameError("frame is not a JSON object")
    if "me" in payload or ("t" in payload and "players" not in payload):
        return parse_hasty(payload)
    if "players" in payload or "entities" in payload or "battle_active" in payload:
        return parse_r0(payload)
    raise FrameError(f"unrecognized frame keys: {sorted(payload)}")


def _nearest_tower(entity: LiveEntity) -> tuple[int, str] | None:
    best: tuple[float, int, str] | None = None
    for entity_id, _owner, kind, x, y in TOWER_LAYOUT:
        if entity.owner != _owner:
            continue
        distance = ((entity.x - x) ** 2 + (entity.y - y) ** 2) ** 0.5
        if distance <= _TOWER_MATCH_RADIUS and entity.max_hitpoints and entity.max_hitpoints > 2000:
            if best is None or distance < best[0]:
                best = (distance, entity_id, kind)
    return None if best is None else (best[1], best[2])


def normalize_frame(
    frame: LiveFrame,
    *,
    decks: Sequence[Sequence[int]] = (),
    tower_troop_id: int = DEFAULT_TOWER_TROOP_ID,
    tower_hp: Mapping[str, float] | None = None,
) -> LiveFrame:
    """Fill the parts a reader may omit: deck identities and tower state.

    ``decks`` is ``(deck0, deck1)``.  A reader that only reports hand *indexes*
    needs it to resolve card IDs.  Towers are taken from the entities when the
    reader exposes them; otherwise the standard layout is emitted at full HP so
    the observation remains contract-valid (and visibly optimistic).
    """

    if not frame.active:
        return frame
    default_hp = {**DEFAULT_TOWER_HP, **(tower_hp or {})}
    players: list[LivePlayer] = []
    for player in frame.players:
        deck = tuple(player.deck_card_ids)
        if not deck and len(decks) > player.side:
            deck = tuple(int(card) for card in decks[player.side])
        if deck and (not player.hand_indices or len(player.hand_indices) < 4):
            # Some readers emit card IDs directly.  Accept an 8-card hand only
            # if every value is a real deck member.
            deck_set = set(deck)
            if player.hand_indices and set(player.hand_indices) <= deck_set and len(player.hand_indices) == 4:
                pass
        players.append(
            LivePlayer(
                side=player.side,
                elixir_raw=player.elixir_raw,
                hand_indices=player.hand_indices,
                cycle_indices=player.cycle_indices,
                deck_card_ids=deck,
            )
        )

    tower_entities: dict[int, LiveEntity] = {}
    ordinary: list[LiveEntity] = []
    for entity in frame.entities:
        match = _nearest_tower(entity)
        if match is None:
            ordinary.append(entity)
        else:
            tower_entities[match[0]] = entity

    towers: list[LiveTower] = []
    for entity_id, owner, kind, x, y in TOWER_LAYOUT:
        observed = tower_entities.get(entity_id)
        max_hp = float(observed.max_hitpoints) if observed and observed.max_hitpoints else float(default_hp[kind])
        hp = float(observed.hitpoints) if observed and observed.hitpoints is not None else max_hp
        hp = max(0.0, min(hp, max_hp))
        towers.append(
            LiveTower(
                entity_id=entity_id,
                owner=owner,
                tower_kind=kind,
                x=float(x),
                y=float(y),
                hitpoints=hp,
                max_hitpoints=max_hp,
                tower_troop_id=None if kind == "king" else int(tower_troop_id),
            )
        )

    return LiveFrame(
        tick=frame.tick,
        active=frame.active,
        players=tuple(players),
        entities=tuple(ordinary),
        towers=tuple(towers),
        source=frame.source,
        terminal=frame.terminal,
        raw=frame.raw,
    )


def frames_from_lines(lines: Iterable[str]) -> Iterable[LiveFrame]:
    """Yield parsed frames from newline-delimited reader output.

    Blank lines and the reader's own diagnostics are skipped.  A line that is
    valid JSON but not a frame raises ``FrameError`` so a protocol change is
    loud instead of silently treated as an empty battle.
    """

    import json

    for line in lines:
        text = line.strip()
        if not text or not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        if "event" in payload and not {"t", "players", "entities"} & set(payload):
            continue
        yield parse_frame(payload)
