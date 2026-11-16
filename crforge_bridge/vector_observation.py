"""Vectorized observation facts for the crforge bulk path.

C1 proved that the per-lane *facts* are cheap (~0.02 ms/lane) while the
``ObservationV1`` projection and the V4 tensorizer are not: one bridge decision
costs ~0.4 ms of cold observation build plus ~1.0 ms of tensorizer.  C3-B is the
batched row build that removes the second half, and its precondition is a
vectorized observation: the same facts the bridge's
``decode_rows`` / ``entity_rows`` / ``player_facts`` emit, read straight from
``VectorBattle``'s zero-copy buffers for every lane at once.

This module is that layer.  It deliberately stops at facts: it does not build
``ObservationV1`` contract objects (the bridge's contract layer is exactly the
thing C3 wants to skip) and it does not tensorize.  What it adds over the C1
spike's private ``PolicyFrame``:

* both seats' headers and private player rows (hand, deck, cycle, pending,
  first-play order, refusals) — the fields the tensorizer reads;
* all six towers per lane, including the per-seat HP columns, so a batch does
  not have to guess tower ownership from a row name;
* the native action-mask placement rows as a fixed ``(lanes, seats, 4, 32, 18)``
  view, which is the candidate-placement half of the tensorizer;
* derived per-entity state the observation layer supplies and the raw frame does
  not: ``age_ms`` (native tick of first appearance) and ``velocity`` (position
  delta per native tick), tracked per lane exactly as ``BattleEnvV1`` does.

All arrays are fixed-size and allocated once; ``encode()`` fills them in place.
Nothing here validates or freezes a contract, so it must not be used where the
validated evaluator/parity path is expected.  Callers pass a ``VectorBattle``
and one ``Catalog``; nothing else is required.

The equivalence test (``crforge_bridge/tests/test_vector_observation.py``)
compares every fact this module emits with the bridge's per-lane decoders on the
same vector.  Timing is reported by
``tools/crforge/vector_observation_spike.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from native_runner.arena import WORLD_HEIGHT, WORLD_WIDTH
from native_runner.contracts import DEFAULT_TICK_MS as TICK_MS

from .catalog import FORM_BASE, FORM_EVOLVED, FORM_HERO, Catalog
from .observe import UNKNOWN, _nearest_card_id

# ``VectorBattle`` lives in the simulator package, which is not installed as a
# dependency of the bridge (the bridge may run against the JVM transport alone).
# Import it lazily in the constructor so importing this module stays cheap and
# type-neutral; tests and tools add ``.resource/crforge/python`` first.


def _lookup_table(vocabulary: list[str], catalog: Catalog) -> np.ndarray:
    """Vocabulary id -> FirstLight card id, with the bridge's form fallbacks.

    This is the same table the C1 spike builds.  Keeping it here means the
    vectorized facts header/deck/pending card ids cannot drift from the bridge's
    ``_nearest_card_id`` rule (``_EV1`` / ``Hero`` / ``_hero`` suffixes).
    """

    table = np.full(len(vocabulary), -1, dtype=np.int32)
    for index, name in enumerate(vocabulary):
        value = catalog.card_id(name)
        if value is None:
            value = _nearest_card_id(catalog, name)
        if value is not None:
            table[index] = int(value)
    return table


def _safe_lookup(table: np.ndarray, ids: np.ndarray) -> np.ndarray:
    """Vocabulary ids to FirstLight card ids; -1 / unmapped become 0.

    0 is the bridge's "not a card" sentinel, so tower and spawned-unit rows do
    not look like an off-catalog play.
    """

    clipped = np.clip(ids, 0, len(table) - 1)
    values = table[clipped]
    values = np.where(values >= 0, values, 0)
    return np.where(ids >= 0, values, 0).astype(np.int32)


@dataclass
class VectorObservationBatch:
    """Fixed-size observation facts for every lane of one vector, filled in place.

    Seats are indexed exactly as ``VectorBattle`` indexes them (0 and 1), and
    entity rows are the shared scene from seat 0, which is what the bridge
    emits.  ``*_valid`` masks are the policy-visible rows only; values outside
    a valid row are zero and must not be read as evidence.
    """

    count: int
    seats: int
    max_entities: int
    hand_slots: int
    deck_size: int
    pending_rows: int
    ability_slots: int
    actions: int
    cols: int
    rows: int
    tower_rows: int = 6

    # ---- seat header -------------------------------------------------------
    tick: np.ndarray = field(init=False)
    result: np.ndarray = field(init=False)
    time_ms: np.ndarray = field(init=False)
    overtime: np.ndarray = field(init=False)
    section: np.ndarray = field(init=False)
    elixir_raw: np.ndarray = field(init=False)
    elixir_reserved_raw: np.ndarray = field(init=False)
    crowns: np.ndarray = field(init=False)
    crowns_enemy: np.ndarray = field(init=False)
    entity_count: np.ndarray = field(init=False)
    entity_overflow: np.ndarray = field(init=False)
    illegal: np.ndarray = field(init=False)
    last_refusal: np.ndarray = field(init=False)
    last_run_refusal: np.ndarray = field(init=False)
    pending_count: np.ndarray = field(init=False)
    next_card_vocab: np.ndarray = field(init=False)
    next_card_id: np.ndarray = field(init=False)
    # The seat's own three towers, in layout order (king, left, right), and the
    # enemy's.  Values are taken from the seat header, so both seats agree
    # without reading the other seat's rows.
    own_tower_hp: np.ndarray = field(init=False)
    own_tower_max_hp: np.ndarray = field(init=False)
    enemy_tower_hp: np.ndarray = field(init=False)
    enemy_tower_max_hp: np.ndarray = field(init=False)
    enemy_seen_vocab: np.ndarray = field(init=False)
    enemy_seen_evolution: np.ndarray = field(init=False)
    enemy_seen_evolution_need: np.ndarray = field(init=False)

    # ---- private cards -----------------------------------------------------
    hand_vocab: np.ndarray = field(init=False)
    hand_card_id: np.ndarray = field(init=False)
    hand_cost: np.ndarray = field(init=False)
    hand_form: np.ndarray = field(init=False)
    # The engine's own statement that the slot's play is waiting to run.  It is
    # the per-slot fact ``BattleEnvV1`` reads as ``pendingHandSlots``, and the
    # candidate build must close that slot even though the card is still in hand.
    hand_pending: np.ndarray = field(init=False)
    hand_parameter: np.ndarray = field(init=False)
    deck_vocab: np.ndarray = field(init=False)
    deck_card_id: np.ndarray = field(init=False)
    deck_slot_flags: np.ndarray = field(init=False)
    deck_level: np.ndarray = field(init=False)
    cycle_vocab: np.ndarray = field(init=False)
    cycle_card_id: np.ndarray = field(init=False)
    pending_card_vocab: np.ndarray = field(init=False)
    pending_card_id: np.ndarray = field(init=False)
    pending_x: np.ndarray = field(init=False)
    pending_y: np.ndarray = field(init=False)
    pending_ticks: np.ndarray = field(init=False)

    # ---- champion ability slots --------------------------------------------
    # The king's two champion slots: the champion's card, the live unit, the
    # native button state/cooldown/charges, and the ticks until a waiting
    # ability command runs (0 when none is waiting).
    ability_champion_vocab: np.ndarray = field(init=False)
    ability_champion_id: np.ndarray = field(init=False)
    ability_unit: np.ndarray = field(init=False)
    ability_state: np.ndarray = field(init=False)
    ability_cooldown_ms: np.ndarray = field(init=False)
    ability_charges: np.ndarray = field(init=False)
    ability_ticks: np.ndarray = field(init=False)

    # ---- shared scene ------------------------------------------------------
    entity_valid: np.ndarray = field(init=False)
    entity_is_tower: np.ndarray = field(init=False)
    entity_id: np.ndarray = field(init=False)
    entity_kind: np.ndarray = field(init=False)
    entity_side: np.ndarray = field(init=False)
    entity_row_vocab: np.ndarray = field(init=False)
    entity_card_id: np.ndarray = field(init=False)
    entity_x: np.ndarray = field(init=False)
    entity_y: np.ndarray = field(init=False)
    entity_hp: np.ndarray = field(init=False)
    entity_max_hp: np.ndarray = field(init=False)
    entity_shield: np.ndarray = field(init=False)
    entity_level: np.ndarray = field(init=False)
    entity_state: np.ndarray = field(init=False)
    entity_flags: np.ndarray = field(init=False)
    entity_target: np.ndarray = field(init=False)
    entity_target_x: np.ndarray = field(init=False)
    entity_target_y: np.ndarray = field(init=False)
    entity_deploy_ms: np.ndarray = field(init=False)
    # Derived by this encoder, not present in the raw frame.
    entity_age_ms: np.ndarray = field(init=False)
    entity_velocity: np.ndarray = field(init=False)
    entity_velocity_valid: np.ndarray = field(init=False)

    # ---- towers ------------------------------------------------------------
    tower_valid: np.ndarray = field(init=False)
    tower_entity_id: np.ndarray = field(init=False)
    tower_owner: np.ndarray = field(init=False)
    tower_kind: np.ndarray = field(init=False)
    tower_hp: np.ndarray = field(init=False)
    tower_max_hp: np.ndarray = field(init=False)
    tower_x: np.ndarray = field(init=False)
    tower_y: np.ndarray = field(init=False)

    # ---- action space ------------------------------------------------------
    # ``masks`` is a zero-copy view of the vector's own buffer (uint8).
    masks: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        c, s = self.count, self.seats
        e, h, d, p = self.max_entities, self.hand_slots, self.deck_size, self.pending_rows
        for name in (
            "tick",
            "result",
            "time_ms",
            "overtime",
            "section",
            "elixir_raw",
            "elixir_reserved_raw",
            "crowns",
            "crowns_enemy",
            "entity_count",
            "entity_overflow",
            "illegal",
            "last_refusal",
            "last_run_refusal",
            "pending_count",
            "next_card_vocab",
            "next_card_id",
        ):
            setattr(self, name, np.zeros((c, s), dtype=np.int32))
        self.own_tower_hp = np.zeros((c, s, 3), dtype=np.int32)
        self.own_tower_max_hp = np.zeros((c, s, 3), dtype=np.int32)
        self.enemy_tower_hp = np.zeros((c, s, 3), dtype=np.int32)
        self.enemy_tower_max_hp = np.zeros((c, s, 3), dtype=np.int32)
        self.enemy_seen_vocab = np.full((c, s, 8), -1, dtype=np.int32)
        self.enemy_seen_evolution = np.zeros((c, s, 8), dtype=np.int32)
        self.enemy_seen_evolution_need = np.zeros((c, s, 8), dtype=np.int32)

        self.hand_vocab = np.full((c, s, h), -1, dtype=np.int32)
        self.hand_card_id = np.zeros((c, s, h), dtype=np.int32)
        self.hand_cost = np.zeros((c, s, h), dtype=np.int32)
        self.hand_form = np.zeros((c, s, h), dtype=np.int32)
        self.hand_pending = np.zeros((c, s, h), dtype=np.int32)
        self.hand_parameter = np.zeros((c, s, h), dtype=np.uint32)
        self.deck_vocab = np.full((c, s, d), -1, dtype=np.int32)
        self.deck_card_id = np.zeros((c, s, d), dtype=np.int32)
        self.deck_slot_flags = np.zeros((c, s, d), dtype=np.int32)
        self.deck_level = np.zeros((c, s, d), dtype=np.int32)
        self.cycle_vocab = np.full((c, s, 4), -1, dtype=np.int32)
        self.cycle_card_id = np.zeros((c, s, 4), dtype=np.int32)
        self.pending_card_vocab = np.full((c, s, p), -1, dtype=np.int32)
        self.pending_card_id = np.zeros((c, s, p), dtype=np.int32)
        self.pending_x = np.zeros((c, s, p), dtype=np.int32)
        self.pending_y = np.zeros((c, s, p), dtype=np.int32)
        self.pending_ticks = np.zeros((c, s, p), dtype=np.int32)
        a = self.ability_slots
        self.ability_champion_vocab = np.full((c, s, a), -1, dtype=np.int32)
        self.ability_champion_id = np.zeros((c, s, a), dtype=np.int32)
        self.ability_unit = np.full((c, s, a), -1, dtype=np.int32)
        self.ability_state = np.zeros((c, s, a), dtype=np.int32)
        self.ability_cooldown_ms = np.zeros((c, s, a), dtype=np.int32)
        self.ability_charges = np.zeros((c, s, a), dtype=np.int32)
        self.ability_ticks = np.zeros((c, s, a), dtype=np.int32)

        entity = (c, e)
        self.entity_valid = np.zeros(entity, dtype=bool)
        self.entity_is_tower = np.zeros(entity, dtype=bool)
        self.entity_id = np.full(entity, -1, dtype=np.int32)
        for name in (
            "entity_kind",
            "entity_side",
            "entity_row_vocab",
            "entity_card_id",
            "entity_x",
            "entity_y",
            "entity_hp",
            "entity_max_hp",
            "entity_shield",
            "entity_level",
            "entity_state",
            "entity_flags",
            "entity_target",
            "entity_target_x",
            "entity_target_y",
            "entity_deploy_ms",
            "entity_age_ms",
        ):
            setattr(self, name, np.zeros(entity, dtype=np.int32))
        self.entity_velocity = np.zeros((c, e, 2), dtype=np.float32)
        self.entity_velocity_valid = np.zeros(entity, dtype=bool)

        tower = (c, self.tower_rows)
        self.tower_valid = np.zeros(tower, dtype=bool)
        self.tower_entity_id = np.full(tower, -1, dtype=np.int32)
        self.tower_owner = np.zeros(tower, dtype=np.int32)
        self.tower_kind = np.zeros(tower, dtype=np.int32)
        self.tower_hp = np.zeros(tower, dtype=np.int32)
        self.tower_max_hp = np.zeros(tower, dtype=np.int32)
        self.tower_x = np.zeros(tower, dtype=np.int32)
        self.tower_y = np.zeros(tower, dtype=np.int32)

        self.masks = np.zeros((c, s, self.actions), dtype=np.uint8)

    def placement_rows(self, seat: int = 0) -> np.ndarray:
        """The four hand slots' placement masks as ``(lanes, 4, 32, 18)``.

        This is a view of ``masks`` for the play-tile block of each slot, with
        the same ``[row][col]`` orientation the bridge's
        ``placement_masks[...]["row_major"]`` uses **after** converting the
        seat's frame to game coordinates: seat 0 mirrors x, seat 1 mirrors y.
        The result is still a view (negative stride), so ``encode()`` refreshes
        it in place and no per-decision copy is made.
        """

        play = self.masks[:, seat, 1 : 1 + self.hand_slots * self.cols * self.rows]
        rows = play.reshape(self.count, self.hand_slots, self.rows, self.cols)
        return rows[:, :, :, ::-1] if int(seat) == 0 else rows[:, :, ::-1, :]


# Tower provenance: the arena is fixed for competitive 1v1.  These ids are the
# native object ids ``BattleEnvV1.TOWER_LAYOUT`` pins, and the batch reports the
# same kind codes the tensorizer uses (0 king, 1 left, 2 right).
_TOWER_KIND_CODE = {"king": 0, "princess_left": 1, "princess_right": 2}
TOWER_LAYOUT: tuple[tuple[int, int, str], ...] = (
    (0, 0, "king"),
    (1, 0, "princess_left"),
    (2, 0, "princess_right"),
    (3, 1, "king"),
    (4, 1, "princess_left"),
    (5, 1, "princess_right"),
)
_TOWER_OWNER_KIND = {entity_id: (owner, kind) for entity_id, owner, kind in TOWER_LAYOUT}


class VectorObservationEncoder:
    """Fill a :class:`VectorObservationBatch` from one ``VectorBattle``.

    ``encode()`` reads the vector's direct-memory observations and masks once
    and writes fixed arrays for every lane.  It also advances the small
    per-lane derived state (birth ticks and previous positions) that the
    observation layer normally computes while stepping, so the facts are the
    same shape the bridge's ``_entities`` emits.
    """

    def __init__(self, vector: Any, catalog: Catalog) -> None:
        self.vector = vector
        self.catalog = catalog
        self.schema = vector.schema
        self.count = int(vector.count)
        self._lookup = _lookup_table(list(vector.vocabulary), catalog)
        self._row_ids = np.arange(self.schema.max_entities, dtype=np.int32)

        # Per-lane derived state, keyed by the vector's entity row slot.  Rows
        # are stable while an entity lives; a recycled slot starts a new age.
        self._birth_tick = np.full((self.count, self.schema.max_entities), -1, dtype=np.int64)
        self._last_x = np.zeros((self.count, self.schema.max_entities), dtype=np.float64)
        self._last_y = np.zeros((self.count, self.schema.max_entities), dtype=np.float64)
        self._last_tick = np.full((self.count, self.schema.max_entities), -1, dtype=np.int64)
        self._last_entity_id = np.full((self.count, self.schema.max_entities), -1, dtype=np.int64)
        # The canonical tick offset: ``BattleEnvV1`` reports ticks from the
        # episode's first readable observation, and the batch must agree.
        self._start_tick: int | None = None

    def spec(self) -> dict[str, int]:
        s = self.schema
        return {
            "count": self.count,
            "seats": 2,
            "max_entities": s.max_entities,
            "hand_slots": s.hand_slots,
            "deck_size": s.deck_size,
            "pending_rows": s.pending_rows,
            "ability_slots": s.ability_slots,
            "actions": s.actions,
            "cols": s.cols,
            "rows": s.rows,
        }

    @property
    def start_tick(self) -> int | None:
        """The native tick the first ``encode()`` treated as tick zero."""

        return self._start_tick

    def batch(self) -> VectorObservationBatch:
        return VectorObservationBatch(**self.spec())

    def reset(self) -> None:
        """Forget derived state; call after ``VectorBattle.reset``."""

        self._birth_tick.fill(-1)
        self._last_tick.fill(-1)
        self._last_entity_id.fill(-1)
        self._start_tick = None

    def encode(self, batch: VectorObservationBatch | None = None) -> VectorObservationBatch:
        if batch is None:
            batch = self.batch()
        if batch.count != self.count:
            raise ValueError("batch was allocated for a different lane count")
        vector = self.vector
        obs = vector.observations
        s = self.schema
        H = s.header

        def header(name: str) -> np.ndarray:
            return obs[..., H[name][0]]

        def columns(name: str) -> np.ndarray:
            return obs[:, :, list(H[name])]

        raw_tick = header("tick").astype(np.int64)
        if self._start_tick is None:
            self._start_tick = int(raw_tick[:, 0].min()) if self.count else 0
        tick = raw_tick - self._start_tick
        batch.tick[...] = tick
        batch.result[...] = header("result")
        batch.time_ms[...] = header("time_ms")
        batch.overtime[...] = header("overtime")
        batch.section[...] = header("section")
        batch.elixir_raw[...] = header("elixir")
        batch.elixir_reserved_raw[...] = header("elixir_reserved")
        batch.crowns[...] = header("crowns")
        batch.crowns_enemy[...] = header("crowns_enemy")
        batch.entity_count[...] = header("entity_count")
        batch.entity_overflow[...] = header("entity_overflow")
        batch.illegal[...] = header("illegal")
        batch.last_refusal[...] = header("last_refusal")
        batch.last_run_refusal[...] = header("last_run_refusal")
        batch.pending_count[...] = header("pending_count")
        raw_next = header("next_card")
        batch.next_card_vocab[...] = raw_next
        batch.next_card_id[...] = _safe_lookup(self._lookup, raw_next)

        # The header's tower columns are owner-relative: ``own_*`` is this
        # seat's side, ``enemy_*`` the other.  Keep them side-relative so a
        # caller can build a shared six-tower list without cross-reading seats.
        for index, name in enumerate(("king", "left", "right")):
            batch.own_tower_hp[:, :, index] = header(f"own_{name}_hp")
            batch.own_tower_max_hp[:, :, index] = header(f"own_{name}_max_hp")
            batch.enemy_tower_hp[:, :, index] = header(f"enemy_{name}_hp")
            batch.enemy_tower_max_hp[:, :, index] = header(f"enemy_{name}_max_hp")
        batch.enemy_seen_vocab[...] = columns("enemy_seen")
        batch.enemy_seen_evolution[...] = columns("enemy_seen_evolution")
        batch.enemy_seen_evolution_need[...] = columns("enemy_seen_evolution_need")

        raw_hand = columns("hand_card")
        hand_present = raw_hand >= 0
        batch.hand_vocab[...] = raw_hand
        batch.hand_cost[...] = np.where(hand_present, columns("hand_cost"), 0)
        batch.hand_form[...] = np.where(hand_present, columns("hand_form"), 0)
        batch.hand_pending[...] = np.where(hand_present, columns("hand_pending"), 0)
        batch.hand_card_id[...] = np.where(hand_present, _safe_lookup(self._lookup, raw_hand), 0)

        raw_deck = columns("deck")
        deck_present = raw_deck >= 0
        batch.deck_vocab[...] = raw_deck
        batch.deck_card_id[...] = np.where(deck_present, _safe_lookup(self._lookup, raw_deck), 0)
        batch.deck_slot_flags[...] = np.where(deck_present, columns("deck_slot"), 0)
        batch.deck_level[...] = np.where(deck_present, columns("deck_level"), 0)
        # Packed hand parameters need the deck rows, so this must run after the
        # deck block above.
        self._encode_hand_parameters(batch, hand_present)

        raw_cycle = columns("queue")
        batch.cycle_vocab[...] = raw_cycle
        batch.cycle_card_id[...] = _safe_lookup(self._lookup, np.where(raw_cycle >= 0, raw_cycle, 0))

        raw_pending_vocab = columns("pending_card")
        batch.pending_card_vocab[...] = raw_pending_vocab
        # Each seat's pending point is in that seat's frame (seat 0 mirrors x,
        # seat 1 mirrors y).  Store game coordinates so every builder applies
        # exactly one perspective transform for its actor.
        raw_pending_x = columns("pending_x").astype(np.int32)
        raw_pending_y = columns("pending_y").astype(np.int32)
        batch.pending_x[...] = raw_pending_x
        batch.pending_y[...] = raw_pending_y
        batch.pending_x[:, 0, :] = WORLD_WIDTH - raw_pending_x[:, 0, :]
        batch.pending_y[:, 1, :] = WORLD_HEIGHT - raw_pending_y[:, 1, :]
        batch.pending_ticks[...] = columns("pending_ticks")
        pending_index = np.arange(s.pending_rows, dtype=np.int32)
        pending_live = pending_index[None, None, :] < batch.pending_count[..., None]
        batch.pending_card_vocab[...] = np.where(pending_live, raw_pending_vocab, -1)
        batch.pending_card_id[...] = np.where(
            pending_live & (raw_pending_vocab >= 0),
            _safe_lookup(self._lookup, np.where(raw_pending_vocab >= 0, raw_pending_vocab, 0)),
            0,
        )
        batch.pending_x[...] = np.where(pending_live, batch.pending_x, 0)
        batch.pending_y[...] = np.where(pending_live, batch.pending_y, 0)
        batch.pending_ticks[...] = np.where(pending_live, batch.pending_ticks, 0)

        self._encode_abilities(batch)

        self._encode_entities(batch, obs, raw_tick)
        self._encode_towers(batch)
        # The mask is the vector's own direct buffer; keep the view.
        batch.masks = vector.masks
        return batch

    def _encode_hand_parameters(self, batch: VectorObservationBatch, present: np.ndarray) -> None:
        """The bridge's packed card parameter, vectorized across lanes and seats.

        ``player_facts`` resolves a hand card to a deck slot by vocabulary id,
        then by card id, and packs level/cost/form/slot flags.  Doing it with
        array equality is the same rule without the per-card ``next(...)``.
        """

        deck = batch.deck_vocab
        hand = batch.hand_vocab
        same_vocab = (
            (hand[:, :, :, None] == deck[:, :, None, :])
            & present[:, :, :, None]
            & (deck[:, :, None, :] >= 0)
        )
        vocab_slot = same_vocab.argmax(axis=-1)
        has_vocab = same_vocab.any(axis=-1)
        same_card = (
            (batch.hand_card_id[:, :, :, None] == batch.deck_card_id[:, :, None, :])
            & present[:, :, :, None]
            & (deck[:, :, None, :] >= 0)
        )
        card_slot = same_card.argmax(axis=-1)
        slot = np.where(has_vocab, vocab_slot, card_slot)
        flags = np.take_along_axis(batch.deck_slot_flags, slot, axis=-1)
        level = np.take_along_axis(batch.deck_level, slot, axis=-1)
        form = batch.hand_form
        form_code = np.where(
            form == FORM_EVOLVED, FORM_EVOLVED, np.where(form == FORM_HERO, FORM_HERO, FORM_BASE)
        )
        packed = (
            (form_code & 0xF)
            | (1 << 4)
            | (1 << 7)
            | ((np.clip(level, 1, 127) & 0x7F) << 10)
            | ((np.clip(flags, 0, 7) & 0x7) << 19)
            | (((np.clip(slot, 0, 7) + 1) & 0x3F) << 22)
            | ((np.clip(batch.hand_cost, 0, 15) & 0xF) << 28)
        ).astype(np.uint32)
        batch.hand_parameter[...] = np.where(present, packed, 0)

    def _encode_abilities(self, batch: VectorObservationBatch) -> None:
        """Both seats' champion slots: card, live unit, button state and wait ticks."""

        vector = self.vector
        obs = vector.observations
        H = self.schema.header

        def columns(name: str) -> np.ndarray:
            return obs[:, :, list(H[name])]

        raw_champion = columns("ability_champion")
        batch.ability_champion_vocab[...] = raw_champion
        batch.ability_champion_id[...] = _safe_lookup(self._lookup, np.where(raw_champion >= 0, raw_champion, 0))
        batch.ability_unit[...] = columns("ability_unit")
        batch.ability_state[...] = columns("ability_state")
        batch.ability_cooldown_ms[...] = columns("ability_cooldown_ms")
        batch.ability_charges[...] = columns("ability_charges")
        batch.ability_ticks[...] = columns("ability_command_ticks")

    def _encode_entities(
        self,
        batch: VectorObservationBatch,
        obs: np.ndarray,
        raw_tick: np.ndarray,
    ) -> None:
        s = self.schema
        E = s.entity
        rows = obs[:, 0, s.header_ints :].reshape(self.count, s.max_entities, s.entity_ints)
        count = batch.entity_count[:, 0]
        kind = rows[..., E["kind"]]
        # The bridge emits live characters and towers only.  Projectiles and
        # area effects are a later phase; emitting them with a synthetic card
        # id would make the tensorizer read them as unknown characters.
        visible = (kind == s.kinds["character"]) | (kind == s.kinds["tower"])
        valid = (self._row_ids[None, :] < count[:, None]) & visible

        row_vocab = rows[..., E["row"]]
        entity_id = rows[..., E["id"]].astype(np.int64)
        batch.entity_valid[...] = valid
        batch.entity_is_tower[...] = valid & (kind == s.kinds["tower"])
        batch.entity_id[...] = np.where(valid, entity_id, -1)
        batch.entity_kind[...] = np.where(valid, kind, 0)
        batch.entity_side[...] = rows[..., E["side"]]
        batch.entity_row_vocab[...] = np.where(valid, row_vocab, 0)
        batch.entity_card_id[...] = np.where(valid, _safe_lookup(self._lookup, row_vocab), 0)
        # The seat-0 scene is seat-framed (``Schema.toSeatX(0, x) = width - x``);
        # the model and the native probe use game coordinates.  Store game x and
        # derive velocity from it, so the builders need only the actor
        # perspective (``flip_x`` / ``flip_y``) to reach the model frame.
        batch.entity_x[...] = np.where(valid, WORLD_WIDTH - rows[..., E["x"]].astype(np.int64), 0)
        batch.entity_y[...] = np.where(valid, rows[..., E["y"]], 0)
        batch.entity_hp[...] = np.where(valid, rows[..., E["hp"]], UNKNOWN)
        batch.entity_max_hp[...] = np.where(valid, rows[..., E["max_hp"]], UNKNOWN)
        shield = rows[..., E["shield"]]
        batch.entity_shield[...] = np.where(valid & (shield > 0), shield, 0)
        batch.entity_level[...] = np.where(valid, rows[..., E["level"]], 0)
        batch.entity_state[...] = np.where(valid, rows[..., E["state"]], 0)
        batch.entity_flags[...] = np.where(valid, rows[..., E["flags"]], 0)
        batch.entity_target[...] = np.where(valid, rows[..., E["target"]], UNKNOWN)
        batch.entity_target_x[...] = np.where(
            valid, WORLD_WIDTH - rows[..., E["target_x"]].astype(np.int64), 0
        )
        batch.entity_target_y[...] = np.where(valid, rows[..., E["target_y"]], 0)
        batch.entity_deploy_ms[...] = np.where(valid, rows[..., E["deploy_ms"]], 0)

        # Derived state.  A slot whose entity id changed is a new entity, even
        # if the vector reused the row, so age and velocity restart.  The
        # reference tick is the raw native tick, matching ``_entity_birth`` and
        # the velocity delta in ``BattleEnvV1._derive_events``.
        now = np.broadcast_to(raw_tick[:, 0][:, None], valid.shape)
        same_entity = (entity_id == self._last_entity_id) & valid
        new_entity = valid & ~same_entity
        self._birth_tick[new_entity] = now[new_entity]
        self._last_tick[new_entity] = now[new_entity]

        age_ticks = np.where(valid, now - self._birth_tick, 0)
        batch.entity_age_ms[...] = age_ticks * TICK_MS

        delta = np.where(same_entity, now - self._last_tick, 0)
        moved = same_entity & (delta > 0)
        velocity_x = np.zeros(valid.shape, dtype=np.float64)
        velocity_y = np.zeros(valid.shape, dtype=np.float64)
        np.divide(
            batch.entity_x.astype(np.float64) - self._last_x,
            delta,
            out=velocity_x,
            where=moved,
        )
        np.divide(
            batch.entity_y.astype(np.float64) - self._last_y,
            delta,
            out=velocity_y,
            where=moved,
        )
        batch.entity_velocity[..., 0] = velocity_x
        batch.entity_velocity[..., 1] = velocity_y
        batch.entity_velocity_valid[...] = moved

        self._last_entity_id[...] = np.where(valid, entity_id, -1)
        self._last_x[...] = batch.entity_x
        self._last_y[...] = batch.entity_y
        self._last_tick[...] = np.where(valid, now, -1)

    def _encode_towers(self, batch: VectorObservationBatch) -> None:
        """Six fixed tower rows from the shared entity scene.

        Tower ids are the fixed arena layout, so a row's ``id`` selects the
        kind.  A destroyed tower is still a valid row with zero HP, which is
        what the observation layer reports until the match ends.  Enemy tower
        HP that the seat-0 scene reports as unknown falls back to the header's
        per-seat ``enemy_*_hp`` column, which is exact.
        """

        batch.tower_valid[...] = False
        batch.tower_entity_id.fill(-1)
        batch.tower_owner.fill(0)
        batch.tower_kind.fill(0)
        batch.tower_hp.fill(0)
        batch.tower_max_hp.fill(0)
        batch.tower_x.fill(0)
        batch.tower_y.fill(0)
        if not self.count:
            return

        # Tower rows are fixed and few; do the gather per lane so a lane whose
        # tower has died (and whose row may be reused) cannot corrupt another.
        for lane in range(self.count):
            valid = batch.entity_valid[lane] & batch.entity_is_tower[lane]
            if not valid.any():
                continue
            for tower_row in np.flatnonzero(valid):
                entity_id = int(batch.entity_id[lane, tower_row])
                if entity_id not in _TOWER_OWNER_KIND:
                    continue
                owner, kind = _TOWER_OWNER_KIND[entity_id]
                hp = int(batch.entity_hp[lane, tower_row])
                max_hp = int(batch.entity_max_hp[lane, tower_row])
                if hp == UNKNOWN:
                    # Owner-relative header column: the seat-0 scene cannot see
                    # the other side's health bar, but that owner's own seat can.
                    tower_index = 0 if kind == "king" else (1 if kind == "princess_left" else 2)
                    hp = int(batch.own_tower_hp[lane, owner, tower_index])
                    max_hp = int(batch.own_tower_max_hp[lane, owner, tower_index])
                batch.tower_valid[lane, entity_id] = True
                batch.tower_entity_id[lane, entity_id] = entity_id
                batch.tower_owner[lane, entity_id] = owner
                batch.tower_kind[lane, entity_id] = _TOWER_KIND_CODE[kind]
                batch.tower_hp[lane, entity_id] = hp
                batch.tower_max_hp[lane, entity_id] = max_hp
                batch.tower_x[lane, entity_id] = int(batch.entity_x[lane, tower_row])
                batch.tower_y[lane, entity_id] = int(batch.entity_y[lane, tower_row])


class _SchemaView:
    """The ``Schema`` attribute names ``observe.py`` uses, from the vector schema.

    ``observe.py`` reads Java ``Schema`` constants (``H_TICK``, ``E_ROW``, ...);
    the simulator's Python ``Schema`` names the same fields in ``header`` /
    ``entity`` dictionaries.  This adapter keeps the comparison self-contained
    and avoids starting a second JVM-constant lookup for a pure equivalence test.
    """

    def __init__(self, schema: Any) -> None:
        self._schema = schema
        self.HEADER_INTS = schema.header_ints
        self.ENTITY_INTS = schema.entity_ints
        self.PENDING_ROWS = schema.pending_rows
        pending_cards = schema.header["pending_card"]
        self.PENDING_INTS = (
            pending_cards[1] - pending_cards[0] if len(pending_cards) > 1 else 4
        )
        self.KIND_CHARACTER = schema.kinds["character"]
        self.KIND_TOWER = schema.kinds["tower"]
        self.hand_slots = schema.hand_slots
        self.deck_size = schema.deck_size
        for name in (
            "tick",
            "result",
            "elixir",
            "elixir_reserved",
            "crowns",
            "crowns_enemy",
            "next_card",
            "entity_count",
            "time_ms",
            "illegal",
            "last_refusal",
            "last_run_refusal",
            "hand_card",
            "hand_cost",
            "hand_form",
            "hand_pending",
            "deck",
            "deck_slot",
            "deck_level",
            "queue",
            "pending_count",
            "pending_card",
        ):
            setattr(self, f"H_{name.upper()}", schema.header[name][0])
        for name in (
            "id",
            "kind",
            "side",
            "row",
            "x",
            "y",
            "hp",
            "max_hp",
            "shield",
            "level",
            "target",
            "target_x",
            "target_y",
            "flags",
            "deploy_ms",
        ):
            setattr(self, f"E_{name.upper()}", schema.entity[name])
        self.H_PENDING = schema.header["pending_card"][0]
        # ``observe.py`` spells the per-slot deck flag list with a plural name.
        self.H_DECK_SLOTS = schema.header["deck_slot"][0]


def compare_lane_facts(
    encoder: VectorObservationEncoder,
    batch: VectorObservationBatch,
    lanes: Any,
) -> list[str]:
    """Every batch fact that the bridge's per-lane decoders also expose.

    This is the equivalence gate for the vectorized layer: the control is
    ``decode_rows`` + ``player_facts`` + ``entity_rows`` on the same vector, so
    a mismatch names the field, lane and seat rather than "frames differ".
    Derived facts (``age_ms``, ``velocity``) are not part of the bridge's
    decoder and are pinned separately by unit tests.
    """

    from .observe import decode_rows, entity_rows, player_facts

    vector = encoder.vector
    schema = _SchemaView(encoder.schema)
    catalog = encoder.catalog
    vocabulary = VocabularyView(list(vector.vocabulary))
    mismatches: list[str] = []
    start_tick = 0 if encoder.start_tick is None else encoder.start_tick

    def add(name: str, lane: int, seat: int | None) -> None:
        mismatches.append(f"{name} lane={lane} seat={seat}" if seat is not None else f"{name} lane={lane}")

    for lane in lanes:
        lane = int(lane)
        seat0 = vector.observations[lane, 0].tolist()
        rows, header = decode_rows(seat0, schema, vocabulary, catalog)
        if batch.tick[lane, 0] != header["tick"] - start_tick:
            add("tick", lane, 0)
        for name, value in (
            ("result", header["result"]),
            ("time_ms", header["time_ms"]),
            ("entity_count", header["entity_count"]),
            ("crowns_enemy", header["crowns_enemy"]),
            ("illegal", header["illegal"]),
            ("last_refusal", header["last_refusal"]),
            ("last_run_refusal", header["last_run_refusal"]),
        ):
            if int(getattr(batch, name)[lane, 0]) != int(value):
                add(name, lane, 0)
        if int(batch.pending_count[lane, 0]) != int(seat0[schema.H_PENDING_COUNT]):
            add("pending_count", lane, 0)
        for seat in (0, 1):
            observation = seat0 if seat == 0 else vector.observations[lane, 1].tolist()
            facts = player_facts(observation, schema, vocabulary, catalog, owner=seat)
            for name, key in (
                ("elixir_raw", "elixirRaw"),
                ("elixir_reserved_raw", "elixirReservedRaw"),
                ("crowns", "crownsRaw"),
                ("last_refusal", "lastRefusal"),
                ("last_run_refusal", "lastRunRefusal"),
            ):
                if int(getattr(batch, name)[lane, seat]) != int(facts[key]):
                    add(name, lane, seat)
            for card in facts["hand"]:
                index = int(card["handIndex"])
                if not 0 <= index < schema.hand_slots:
                    continue
                if int(batch.hand_vocab[lane, seat, index]) != int(observation[schema.H_HAND_CARD + index]):
                    add("hand_vocab", lane, seat)
                if int(batch.hand_card_id[lane, seat, index]) != int(card["cardId"]):
                    add("hand_card_id", lane, seat)
                if int(batch.hand_cost[lane, seat, index]) != int(card["cost"]):
                    add("hand_cost", lane, seat)
                if int(batch.hand_parameter[lane, seat, index]) != int(card["cardParameter"]):
                    add("hand_parameter", lane, seat)
                pending_flag = int(observation[schema.H_HAND_PENDING + index]) > 0
                if bool(batch.hand_pending[lane, seat, index]) != pending_flag:
                    add("hand_pending", lane, seat)
            for card in facts["deck"]:
                index = int(card["slot"])
                if not 0 <= index < schema.deck_size:
                    continue
                if int(batch.deck_card_id[lane, seat, index]) != int(card["cardId"]):
                    add("deck_card_id", lane, seat)
                if int(batch.deck_slot_flags[lane, seat, index]) != int(card["formFlags"]):
                    add("deck_slot_flags", lane, seat)
                if int(batch.deck_level[lane, seat, index]) != int(card["level"]):
                    add("deck_level", lane, seat)
            next_card = facts["nextCard"]
            expected_next = 0 if next_card is None else int(next_card["cardId"])
            if int(batch.next_card_id[lane, seat]) != expected_next:
                add("next_card_id", lane, seat)
            for index, pending in enumerate(facts["pending"][: batch.pending_rows]):
                if int(batch.pending_card_id[lane, seat, index]) != int(pending["cardId"]):
                    add("pending_card_id", lane, seat)
                if int(batch.pending_x[lane, seat, index]) != int(pending["x"]):
                    add("pending_x", lane, seat)
                if int(batch.pending_y[lane, seat, index]) != int(pending["y"]):
                    add("pending_y", lane, seat)
                if int(batch.pending_ticks[lane, seat, index]) != int(pending["ticksUntilRun"]):
                    add("pending_ticks", lane, seat)

        objects, _rich, visible_rows = entity_rows(seat0, schema, vocabulary, catalog, rows=rows)
        by_slot = {int(item["slot"]): item for item in objects}
        if batch.entity_valid[lane].any():
            valid_slots = np.flatnonzero(batch.entity_valid[lane])
            expected_slots = [int(row.slot) for row in visible_rows]
            if [int(slot) for slot in valid_slots] != expected_slots:
                add("entity_slots", lane, None)
            for row in visible_rows:
                item = by_slot[int(row.slot)]
                slot = int(row.slot)
                checks = (
                    ("entity_kind", int(item["kind"])),
                    ("entity_side", int(item["owner"])),
                    (
                        "entity_row_vocab",
                        int(seat0[schema.HEADER_INTS + slot * schema.ENTITY_INTS + schema.E_ROW]),
                    ),
                    ("entity_card_id", int(item["cardId"])),
                    ("entity_x", int(item["x"])),
                    ("entity_y", int(item["y"])),
                    ("entity_hp", -1 if item["hp"] is None else int(item["hp"])),
                    ("entity_max_hp", -1 if item["maxHp"] is None else int(item["maxHp"])),
                    ("entity_shield", 0 if not item["shield"] else int(item["shield"]["current"])),
                    ("entity_level", int(item["level"])),
                    ("entity_target", int(row.target_id)),
                    ("entity_target_x", int(item["targetX"])),
                    ("entity_target_y", int(item["targetY"])),
                    ("entity_flags", int(item["flags"])),
                    ("entity_deploy_ms", int(item["deployMs"])),
                    ("entity_id", int(row.entity_id)),
                )
                for name, expected in checks:
                    if int(getattr(batch, name)[lane, slot]) != expected:
                        add(name, lane, None)

        for row in (item for item in visible_rows if int(item.kind) == int(schema.KIND_TOWER)):
            entity_id = int(row.entity_id)
            if not 0 <= entity_id < batch.tower_rows:
                add("tower_id", lane, None)
                continue
            owner, kind = _TOWER_OWNER_KIND[entity_id]
            if not bool(batch.tower_valid[lane, entity_id]):
                add("tower_valid", lane, None)
            if int(batch.tower_owner[lane, entity_id]) != owner:
                add("tower_owner", lane, None)
            if int(batch.tower_kind[lane, entity_id]) != _TOWER_KIND_CODE[kind]:
                add("tower_kind", lane, None)
            expected_hp = int(row.hp)
            expected_max_hp = int(row.max_hp)
            tower_index = 0 if kind == "king" else (1 if kind == "princess_left" else 2)
            if expected_hp == UNKNOWN:
                expected_hp = int(batch.own_tower_hp[lane, owner, tower_index])
            if expected_max_hp == UNKNOWN:
                expected_max_hp = int(batch.own_tower_max_hp[lane, owner, tower_index])
            if int(batch.tower_hp[lane, entity_id]) != expected_hp:
                add("tower_hp", lane, None)
            if int(batch.tower_max_hp[lane, entity_id]) != expected_max_hp:
                add("tower_max_hp", lane, None)
        if len(mismatches) >= 20:
            break
    return mismatches


@dataclass(frozen=True)
class VocabularyView:
    names_list: list[str]

    def names(self) -> list[str]:
        return self.names_list


__all__ = [
    "TOWER_LAYOUT",
    "VectorObservationBatch",
    "VectorObservationEncoder",
    "VocabularyView",
    "compare_lane_facts",
]
