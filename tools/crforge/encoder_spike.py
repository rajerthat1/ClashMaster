"""The C1 encoder spike: per-lane vs vectorized observation encoding.

The bulk-training plan (the crforge plan stage C1) is gated on one
measurement: how many lane-decisions per second a crforge ``VectorBattle`` can
encode for a policy.  The bridge's TCP/JSON path measures 20–30 decisions/s, but
its in-process fact projection is under a millisecond per lane; the plan's old
"13 ms/lane" figure mixed the transport and the observation build.  A vectorized
encoder reads the same ``observations`` buffer, which is already
``(count, 2, seat_ints)`` int32 in direct memory, and fills preallocated arrays
with NumPy.

This tool runs one vector and encodes the *same* observations two ways:

* ``per-lane`` - the existing bridge path: ``np.tolist()`` per seat,
  ``decode_rows``, ``entity_rows`` (the bridge repeats the decode), and
  ``player_facts`` per owner.  This is the control.
* ``vectorized`` - field-for-field NumPy extraction from the zero-copy view.

Both write into the same fixed-size ``PolicyFrame`` buffers, so the difference
is Python work, not allocation.  The tool checks that the two frames agree on a
tick before reporting.  Times are paired: both encoders run on the same vector
at the same tick, so host load affects them equally (see the session notes: this
host's absolute numbers move ~2x, so paired runs are the only stable
comparison).

It deliberately does **not** pretend to tensorize the vectorized frame: the V4
tensorizer consumes ``ObservationV1`` today, and vectorizing it is stage C3.
``--tensorize-port`` measures the existing bridge path per decision (a cold
``BattleEnvV1.observe`` plus the real V4 tensorizer) on a live episode, so the
report shows the encoder/tensorize split and an end-to-end projection without
conflating the two.

    # the C1 gate: 256 lanes, both thread counts
    .venv/bin/python tools/crforge/encoder_spike.py \
        --count 256 --threads 1 8 --steps 200 --repeats 3 \
        --tensorize-port 26789 \
        --output runs/crforge-c1/encoder-spike.json

The gate is vectorized **>= 1,000 decisions/s per process** (<= 1 ms/lane at
256 lanes); the target is **<= 0.5 ms/lane**.  A per-lane port is ~1.4x.

**What the split means.** The vectorized frame replaces the observation half
(``BattleEnvV1.observe``: facts, contract projection and masks).  It does not
replace ``UniversalObservationTensorizerV4``, so ``projected`` adds the measured
tensorizer cost to the vectorized encoder.  If the projection misses 1 ms/lane,
the next work is the tensorizer (C3), not more lanes.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Run from the repository root without an editable install of the simulator.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / ".resource" / "crforge" / "python"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import numpy as np
from crforge_env.vector import LadderConfig, SideSetup, VectorBattle

from crforge_bridge.catalog import FORM_EVOLVED, FORM_HERO, Catalog
from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
from crforge_bridge.observe import decode_rows, entity_rows, player_facts
from native_runner.arena import WORLD_HEIGHT, WORLD_WIDTH

# The locked learner deck (the session notes 2026-11-15).  Evolution/hero forms are
# selected by slot flags on the base card, not by naming the form row.
DECK = ("Xbow", "Tesla", "Archer", "Knight", "Skeletons", "ElectroSpirit", "Fireball", "Log")
# Evolution slots: Tesla, Archer.  Hero slot: Knight.  X-Bow has no form.
DECK_SLOTS = (0, 1, 1, 2, 0, 0, 0, 0)
TOWERS = "King_PrincessTowers"
DEFAULT_LEVEL = 16

# Actions per decision: the benchmark's open-play sampler, so the battle
# advances like a training rollout instead of sitting still.
PLAY_RATE = 0.3


def _priority(n: int) -> np.ndarray:
    return np.random.default_rng(12345).permutation(n).astype(np.int32)


_PRIORITY: dict[int, np.ndarray] = {}


def draw_actions(masks: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """A random open play per seat with PLAY_RATE, else the wait."""

    n = masks.shape[2]
    priority = _PRIORITY.get(n)
    if priority is None:
        priority = _PRIORITY[n] = _priority(n)
    actions = np.zeros((masks.shape[0], 2), dtype=np.int32)
    play = rng.random((masks.shape[0], 2)) < PLAY_RATE
    if play.any():
        rolled = (priority + int(rng.integers(n))) % n + 1
        open_plays = masks[play].astype(np.int32)
        open_plays[:, 0] = 0
        scored = open_plays * rolled
        chosen = scored.argmax(axis=1)
        chosen[scored.max(axis=1) == 0] = 0
        actions[play] = chosen
    return actions


@dataclass(frozen=True)
class VocabularyView:
    """The bridge's ``Vocabulary`` surface over the vector's name list."""

    names_list: Sequence[str]

    def names(self) -> Sequence[str]:
        return self.names_list


@dataclass
class PolicyFrame:
    """The per-decision arrays a V4 policy needs, shared by both encoders.

    The shapes are fixed so a training loop can write straight into a rollout
    buffer.  ``valid`` marks the entity rows the seat was shown; values in
    ``row_vocab`` are crforge vocabulary ids, ``card_id`` the FirstLight ids
    the bridge catalog maps them to.
    """

    count: int
    seats: int
    max_entities: int
    hand_slots: int
    deck_size: int
    pending_rows: int
    actions: int

    tick: np.ndarray = field(init=False)
    result: np.ndarray = field(init=False)
    elixir_raw: np.ndarray = field(init=False)
    elixir_reserved_raw: np.ndarray = field(init=False)
    crowns: np.ndarray = field(init=False)
    crowns_enemy: np.ndarray = field(init=False)
    entity_count: np.ndarray = field(init=False)
    time_ms: np.ndarray = field(init=False)
    illegal: np.ndarray = field(init=False)
    last_refusal: np.ndarray = field(init=False)
    last_run_refusal: np.ndarray = field(init=False)

    entity_valid: np.ndarray = field(init=False)
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
    entity_target: np.ndarray = field(init=False)
    entity_target_x: np.ndarray = field(init=False)
    entity_target_y: np.ndarray = field(init=False)
    entity_flags: np.ndarray = field(init=False)
    entity_deploy_ms: np.ndarray = field(init=False)

    hand_vocab: np.ndarray = field(init=False)
    hand_card_id: np.ndarray = field(init=False)
    hand_cost: np.ndarray = field(init=False)
    hand_form: np.ndarray = field(init=False)
    hand_pending: np.ndarray = field(init=False)
    hand_parameter: np.ndarray = field(init=False)

    deck_vocab: np.ndarray = field(init=False)
    deck_card_id: np.ndarray = field(init=False)
    deck_slot_flags: np.ndarray = field(init=False)
    deck_level: np.ndarray = field(init=False)

    next_card_vocab: np.ndarray = field(init=False)
    next_card_id: np.ndarray = field(init=False)

    pending_card_vocab: np.ndarray = field(init=False)
    pending_card_id: np.ndarray = field(init=False)
    pending_x: np.ndarray = field(init=False)
    pending_y: np.ndarray = field(init=False)
    pending_ticks: np.ndarray = field(init=False)

    mask: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        c, s, e = self.count, self.seats, self.max_entities
        h, d, p = self.hand_slots, self.deck_size, self.pending_rows
        scalar = (c, s)
        for name in (
            "tick",
            "result",
            "elixir_raw",
            "elixir_reserved_raw",
            "crowns",
            "crowns_enemy",
            "entity_count",
            "time_ms",
            "illegal",
            "last_refusal",
            "last_run_refusal",
            "next_card_vocab",
            "next_card_id",
        ):
            setattr(self, name, np.zeros(scalar, dtype=np.int32))
        entity = (c, s, e)
        self.entity_valid = np.zeros(entity, dtype=bool)
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
            "entity_target",
            "entity_target_x",
            "entity_target_y",
            "entity_flags",
            "entity_deploy_ms",
        ):
            setattr(self, name, np.zeros(entity, dtype=np.int32))
        hand = (c, s, h)
        for name in ("hand_vocab", "hand_card_id", "hand_cost", "hand_form", "hand_pending"):
            setattr(self, name, np.zeros(hand, dtype=np.int32))
        self.hand_parameter = np.zeros(hand, dtype=np.uint32)
        deck = (c, s, d)
        for name in ("deck_vocab", "deck_card_id", "deck_slot_flags", "deck_level"):
            setattr(self, name, np.zeros(deck, dtype=np.int32))
        pending = (c, s, p)
        for name in ("pending_card_vocab", "pending_card_id", "pending_x", "pending_y", "pending_ticks"):
            setattr(self, name, np.zeros(pending, dtype=np.int32))
        self.mask = np.zeros((c, s, self.actions), dtype=np.uint8)

    def clear_entities(self, lanes: Sequence[int]) -> None:
        """Reset the sampled lanes before the per-lane builder writes them."""

        if not lanes:
            return
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
            "entity_target",
            "entity_target_x",
            "entity_target_y",
            "entity_flags",
            "entity_deploy_ms",
        ):
            getattr(self, name)[lanes] = 0
        self.entity_valid[lanes] = False

    def clear_players(self, lanes: Sequence[int]) -> None:
        """Reset the sampled lanes' player columns before the per-lane writer."""

        if not lanes:
            return
        for name in (
            "tick",
            "result",
            "elixir_raw",
            "elixir_reserved_raw",
            "crowns",
            "crowns_enemy",
            "entity_count",
            "time_ms",
            "illegal",
            "last_refusal",
            "last_run_refusal",
            "next_card_vocab",
            "next_card_id",
        ):
            getattr(self, name)[lanes] = 0
        for name in (
            "hand_vocab",
            "hand_card_id",
            "hand_cost",
            "hand_form",
            "hand_pending",
            "hand_parameter",
            "deck_vocab",
            "deck_card_id",
            "deck_slot_flags",
            "deck_level",
            "pending_card_vocab",
            "pending_card_id",
            "pending_x",
            "pending_y",
            "pending_ticks",
        ):
            getattr(self, name)[lanes] = 0


def _lookup_table(vocabulary: Sequence[str], catalog: Catalog) -> np.ndarray:
    """Vocabulary id -> FirstLight card id, with the bridge's form fallbacks."""

    table = np.full(len(vocabulary), -1, dtype=np.int32)
    for index, name in enumerate(vocabulary):
        direct = catalog.card_id(name)
        if direct is None:
            for suffix in ("_EV1", "_hero"):
                if name.endswith(suffix):
                    direct = catalog.card_id(name[: -len(suffix)])
                    if direct is not None:
                        break
        if direct is not None:
            table[index] = int(direct)
    return table


def _safe_lookup(table: np.ndarray, ids: np.ndarray) -> np.ndarray:
    """Vocabulary ids to FirstLight card ids; unmapped or absent becomes 0.

    0 is the bridge's sentinel for "not a card" (a tower or spawned unit), so
    the vectorized encoder and ``player_facts``/``entity_rows`` agree.
    """

    clipped = np.clip(ids, 0, len(table) - 1)
    values = table[clipped]
    values = np.where(values >= 0, values, 0)
    return np.where(ids >= 0, values, 0).astype(np.int32)


def _pack_parameters(
    slot: np.ndarray,
    level: np.ndarray,
    cost: np.ndarray,
    form: np.ndarray,
    flags: np.ndarray,
    present: np.ndarray,
) -> np.ndarray:
    """Vectorized ``catalog.pack_card_parameter`` for the hand's deck slots."""

    form_code = np.where(form == FORM_EVOLVED, 1, np.where(form == FORM_HERO, 2, 0))
    packed = (
        (form_code & 0xF)
        | (1 << 4)
        | (1 << 7)
        | ((np.clip(level, 1, 127) & 0x7F) << 10)
        | ((np.clip(flags, 0, 7) & 0x7) << 19)
        | (((np.clip(slot, 0, 7) + 1) & 0x3F) << 22)
        | ((np.clip(cost, 0, 15) & 0xF) << 28)
    ).astype(np.uint32)
    return np.where(present, packed, 0).astype(np.uint32)


class VectorEncoder:
    """The vectorized encoder over ``VectorBattle.observations``."""

    def __init__(self, vector: VectorBattle, catalog: Catalog) -> None:
        self.vector = vector
        self.schema = vector.schema
        self.catalog = catalog
        self.lookup = _lookup_table(vector.vocabulary, catalog)
        max_entities = self.schema.max_entities
        self._row_ids = np.arange(max_entities, dtype=np.int32)

    def frame_spec(self, count: int) -> dict[str, int]:
        return {
            "count": count,
            "seats": 2,
            "max_entities": self.schema.max_entities,
            "hand_slots": self.schema.hand_slots,
            "deck_size": self.schema.deck_size,
            "pending_rows": self.schema.pending_rows,
            "actions": self.schema.actions,
        }

    def encode(self, frame: PolicyFrame) -> None:
        vector = self.vector
        obs = vector.observations
        s = self.schema
        H = s.header
        E = s.entity
        head = s.header_ints

        def header(name: str) -> np.ndarray:
            return obs[..., H[name][0]]

        frame.tick[...] = obs[..., H["tick"][0]]
        frame.result[...] = obs[..., H["result"][0]]
        frame.elixir_raw[...] = obs[..., H["elixir"][0]]
        frame.elixir_reserved_raw[...] = obs[..., H["elixir_reserved"][0]]
        frame.crowns[...] = obs[..., H["crowns"][0]]
        frame.crowns_enemy[...] = obs[..., H["crowns_enemy"][0]]
        frame.entity_count[...] = obs[..., H["entity_count"][0]]
        frame.time_ms[...] = obs[..., H["time_ms"][0]]
        frame.illegal[...] = obs[..., H["illegal"][0]]
        frame.last_refusal[...] = obs[..., H["last_refusal"][0]]
        frame.last_run_refusal[...] = obs[..., H["last_run_refusal"][0]]

        raw_hand = obs[:, :, list(H["hand_card"])]
        hand_present = raw_hand >= 0
        frame.hand_vocab[...] = np.where(hand_present, raw_hand, 0)
        frame.hand_cost[...] = np.where(hand_present, obs[:, :, list(H["hand_cost"])], 0)
        frame.hand_form[...] = np.where(hand_present, obs[:, :, list(H["hand_form"])], 0)
        frame.hand_pending[...] = np.where(hand_present, obs[:, :, list(H["hand_pending"])], 0)
        raw_deck = obs[:, :, list(H["deck"])]
        deck_present = raw_deck >= 0
        frame.deck_vocab[...] = np.where(deck_present, raw_deck, 0)
        frame.deck_slot_flags[...] = np.where(deck_present, obs[:, :, list(H["deck_slot"])], 0)
        frame.deck_level[...] = np.where(deck_present, obs[:, :, list(H["deck_level"])], 0)
        raw_next = obs[..., H["next_card"][0]]
        frame.next_card_vocab[...] = np.where(raw_next >= 0, raw_next, 0)
        frame.pending_card_vocab[...] = obs[:, :, list(H["pending_card"])]
        frame.pending_x[...] = obs[:, :, list(H["pending_x"])]
        frame.pending_y[...] = obs[:, :, list(H["pending_y"])]
        frame.pending_ticks[...] = obs[:, :, list(H["pending_ticks"])]
        # Only rows below pending_count are live; the header's spare rows are
        # -1 and the bridge's player facts omit them.
        pending_index = np.arange(s.pending_rows, dtype=np.int32)
        pending_live = pending_index[None, None, :] < obs[..., H["pending_count"][0]][..., None]
        frame.pending_card_vocab[...] = np.where(pending_live, frame.pending_card_vocab, 0)
        frame.pending_x[...] = np.where(pending_live, frame.pending_x, 0)
        frame.pending_y[...] = np.where(pending_live, frame.pending_y, 0)
        frame.pending_ticks[...] = np.where(pending_live, frame.pending_ticks, 0)

        frame.hand_card_id[...] = _safe_lookup(self.lookup, frame.hand_vocab)
        frame.deck_card_id[...] = _safe_lookup(self.lookup, frame.deck_vocab)
        frame.next_card_id[...] = _safe_lookup(self.lookup, frame.next_card_vocab)
        frame.pending_card_id[...] = _safe_lookup(self.lookup, frame.pending_card_vocab)

        # Hand card -> deck slot, first by vocabulary id then by card id.  This
        # is the bridge's ``player_facts`` resolution, vectorized over seats.
        deck_by_seat = frame.deck_vocab
        hand_by_seat = frame.hand_vocab
        same_vocab = (
            (hand_by_seat[:, :, :, None] == deck_by_seat[:, :, None, :])
            & hand_present[:, :, :, None]
            & deck_present[:, :, None, :]
        )
        vocab_slot = same_vocab.argmax(axis=-1)
        has_vocab = same_vocab.any(axis=-1)
        same_card = (
            (frame.hand_card_id[:, :, :, None] == frame.deck_card_id[:, :, None, :])
            & hand_present[:, :, :, None]
            & deck_present[:, :, None, :]
        )
        card_slot = same_card.argmax(axis=-1)
        has_card = same_card.any(axis=-1)
        slot = np.where(has_vocab, vocab_slot, np.where(has_card, card_slot, 0))
        hand_flags = np.take_along_axis(frame.deck_slot_flags, slot, axis=-1)
        hand_level = np.take_along_axis(frame.deck_level, slot, axis=-1)
        frame.hand_parameter[...] = _pack_parameters(
            slot,
            hand_level,
            frame.hand_cost,
            frame.hand_form,
            hand_flags,
            hand_present,
        )

        # Entities come from seat 0's frame: the bridge emits one scene and
        # both seats read it.  The other seat's columns stay invalid.
        rows = obs[:, 0, head:].reshape(obs.shape[0], s.max_entities, s.entity_ints)
        count = obs[..., H["entity_count"][0]][:, 0]
        kind = rows[..., E["kind"]]
        # The bridge emits live characters and towers only; projectiles and
        # area effects are a later phase, so they are not policy entities.
        visible = (kind == s.kinds["character"]) | (kind == s.kinds["tower"])
        valid = (self._row_ids[None, :] < count[:, None]) & visible
        frame.entity_valid[:, 0, :] = valid
        frame.entity_valid[:, 1, :] = False
        row_vocab = rows[..., E["row"]]
        frame.entity_kind[:, 0, :] = rows[..., E["kind"]]
        frame.entity_side[:, 0, :] = rows[..., E["side"]]
        # An unwritten row is all zeros; its row vocabulary id 0 would map to
        # a real card, so keep the invalid slots at the per-lane sentinel.
        frame.entity_row_vocab[:, 0, :] = np.where(valid, row_vocab, 0)
        frame.entity_card_id[:, 0, :] = np.where(valid, _safe_lookup(self.lookup, row_vocab), 0)
        frame.entity_x[:, 0, :] = np.where(valid, WORLD_WIDTH - rows[..., E["x"]], 0)
        frame.entity_y[:, 0, :] = np.where(valid, rows[..., E["y"]], 0)
        frame.entity_hp[:, 0, :] = rows[..., E["hp"]]
        frame.entity_max_hp[:, 0, :] = rows[..., E["max_hp"]]
        frame.entity_shield[:, 0, :] = np.where(rows[..., E["shield"]] > 0, rows[..., E["shield"]], 0)
        frame.entity_level[:, 0, :] = rows[..., E["level"]]
        frame.entity_target[:, 0, :] = rows[..., E["target"]]
        frame.entity_target_x[:, 0, :] = np.where(valid, WORLD_WIDTH - rows[..., E["target_x"]], 0)
        frame.entity_target_y[:, 0, :] = np.where(valid, rows[..., E["target_y"]], 0)
        frame.entity_flags[:, 0, :] = rows[..., E["flags"]]
        frame.entity_deploy_ms[:, 0, :] = rows[..., E["deploy_ms"]]

        # The mask is a zero-copy native view in both encoders.
        frame.mask = vector.masks


class PerLaneEncoder:
    """The existing bridge encoder, lane by lane, into the same frame."""

    def __init__(self, vector: VectorBattle, catalog: Catalog, java_schema: Any) -> None:
        self.vector = vector
        self.catalog = catalog
        self.java_schema = java_schema
        self.vocabulary = VocabularyView(vector.vocabulary)
        self._row_ids = np.arange(vector.schema.max_entities, dtype=np.int32)

    def encode(self, frame: PolicyFrame, lanes: Sequence[int]) -> None:
        frame.clear_players(lanes)
        frame.clear_entities(lanes)
        schema = self.java_schema
        catalog = self.catalog
        vocabulary = self.vocabulary
        for lane in lanes:
            seat0 = self.vector.observations[lane, 0].tolist()
            rows, header = decode_rows(seat0, schema, vocabulary, catalog)
            for seat in (0, 1):
                observation = seat0 if seat == 0 else self.vector.observations[lane, 1].tolist()
                facts = player_facts(observation, schema, vocabulary, catalog, owner=seat)
                frame.tick[lane, seat] = header["tick"]
                frame.result[lane, seat] = header["result"]
                frame.elixir_raw[lane, seat] = facts["elixirRaw"]
                frame.elixir_reserved_raw[lane, seat] = facts["elixirReservedRaw"]
                frame.crowns[lane, seat] = facts["crownsRaw"]
                frame.crowns_enemy[lane, seat] = header["crowns_enemy"]
                frame.entity_count[lane, seat] = header["entity_count"]
                frame.time_ms[lane, seat] = header["time_ms"]
                frame.illegal[lane, seat] = header["illegal"]
                frame.last_refusal[lane, seat] = facts["lastRefusal"]
                frame.last_run_refusal[lane, seat] = facts["lastRunRefusal"]
                for card in facts["hand"]:
                    index = int(card["handIndex"])
                    if not 0 <= index < frame.hand_slots:
                        continue
                    frame.hand_vocab[lane, seat, index] = int(observation[schema.H_HAND_CARD + index])
                    frame.hand_card_id[lane, seat, index] = int(card["cardId"])
                    frame.hand_cost[lane, seat, index] = int(card["cost"])
                    frame.hand_form[lane, seat, index] = int(observation[schema.H_HAND_FORM + index])
                    frame.hand_pending[lane, seat, index] = int(observation[schema.H_HAND_PENDING + index])
                    frame.hand_parameter[lane, seat, index] = int(card["cardParameter"])
                for card in facts["deck"]:
                    index = int(card["slot"])
                    if not 0 <= index < frame.deck_size:
                        continue
                    frame.deck_vocab[lane, seat, index] = int(observation[schema.H_DECK + index])
                    frame.deck_card_id[lane, seat, index] = int(card["cardId"])
                    frame.deck_slot_flags[lane, seat, index] = int(card["formFlags"])
                    frame.deck_level[lane, seat, index] = int(card["level"])
                next_card = facts["nextCard"]
                if next_card is not None:
                    frame.next_card_vocab[lane, seat] = int(observation[schema.H_NEXT_CARD])
                    frame.next_card_id[lane, seat] = int(next_card["cardId"])
                for index, pending in enumerate(facts["pending"][: frame.pending_rows]):
                    frame.pending_card_vocab[lane, seat, index] = int(
                        observation[schema.H_PENDING + index * schema.PENDING_INTS]
                    )
                    frame.pending_card_id[lane, seat, index] = int(pending["cardId"])
                    # Facts are game-frame: seat 0 mirrors x, seat 1 mirrors y.
                    frame.pending_x[lane, seat, index] = (
                        WORLD_WIDTH - int(pending["x"]) if seat == 0 else int(pending["x"])
                    )
                    frame.pending_y[lane, seat, index] = (
                        int(pending["y"]) if seat == 0 else WORLD_HEIGHT - int(pending["y"])
                    )
                    frame.pending_ticks[lane, seat, index] = int(pending["ticksUntilRun"])
            objects, _rich, visible_rows = entity_rows(seat0, schema, vocabulary, catalog)
            for obj, row in zip(objects, visible_rows, strict=True):
                slot = int(obj["slot"])
                if not 0 <= slot < frame.max_entities:
                    continue
                frame.entity_valid[lane, 0, slot] = True
                frame.entity_kind[lane, 0, slot] = int(obj["kind"])
                frame.entity_side[lane, 0, slot] = int(obj["owner"])
                frame.entity_row_vocab[lane, 0, slot] = int(
                    seat0[schema.HEADER_INTS + slot * schema.ENTITY_INTS + schema.E_ROW]
                )
                frame.entity_card_id[lane, 0, slot] = int(obj["cardId"])
                frame.entity_x[lane, 0, slot] = int(obj["x"])
                frame.entity_y[lane, 0, slot] = int(obj["y"])
                frame.entity_hp[lane, 0, slot] = -1 if obj["hp"] is None else int(obj["hp"])
                frame.entity_max_hp[lane, 0, slot] = -1 if obj["maxHp"] is None else int(obj["maxHp"])
                shield = obj["shield"]
                frame.entity_shield[lane, 0, slot] = 0 if not shield else int(shield["current"])
                frame.entity_level[lane, 0, slot] = int(obj["level"])
                frame.entity_target[lane, 0, slot] = int(row.target_id)
                frame.entity_target_x[lane, 0, slot] = int(obj["targetX"])
                frame.entity_target_y[lane, 0, slot] = int(obj["targetY"])
                frame.entity_flags[lane, 0, slot] = int(obj["flags"])
                frame.entity_deploy_ms[lane, 0, slot] = int(obj["deployMs"])
        # The mask is a zero-copy native view in both encoders.
        frame.mask = self.vector.masks


def _compare_frames(
    expected: PolicyFrame, actual: PolicyFrame, lanes: Sequence[int]
) -> list[str]:
    """The first field/lane/seat that disagrees between the two encoders."""

    mismatches: list[str] = []
    entity_fields = {
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
        "entity_target",
        "entity_target_x",
        "entity_target_y",
        "entity_flags",
        "entity_deploy_ms",
    }
    # Only the rows a seat was shown are policy input; the vector buffer may
    # hold stale values past entity_count and the per-lane control zeroes them.
    valid = expected.entity_valid[lanes]
    for name in vars(expected):
        value = getattr(expected, name)
        if not isinstance(value, np.ndarray):
            continue
        left = value[lanes]
        right = getattr(actual, name)[lanes]
        if name in entity_fields:
            left = left[valid]
            right = right[valid]
        elif name == "entity_valid":
            pass
        if left.shape != right.shape or not np.array_equal(left, right):
            where = np.argwhere(left != right)[:3].tolist()
            mismatches.append(f"{name}: {where}")
            if len(mismatches) >= 5:
                break
    return mismatches


def _run_one(
    *,
    threads: int,
    count: int,
    steps: int,
    repeats: int,
    per_lane_lanes: int,
    install: str,
    tables: str,
    seed_base: int,
    level: int,
    tower_level: int,
    catalog: Catalog,
    java_schema: Any,
) -> dict[str, Any]:
    decks = (list(DECK), list(DECK))
    slots = (list(DECK_SLOTS), list(DECK_SLOTS))
    setup = SideSetup(towers=TOWERS, king_level=level, tower_level=tower_level)
    rng = np.random.default_rng(1)

    with VectorBattle(
        count,
        threads,
        install=install,
        tables=tables,
        config=LadderConfig(decision_ticks=5, level=level, tower_level=tower_level),
    ) as vector:
        spec = {
            "count": count,
            "seats": 2,
            "max_entities": vector.schema.max_entities,
            "hand_slots": vector.schema.hand_slots,
            "deck_size": vector.schema.deck_size,
            "pending_rows": vector.schema.pending_rows,
            "actions": vector.schema.actions,
        }
        vectorized_builder = VectorEncoder(vector, catalog)
        per_lane_builder = PerLaneEncoder(vector, catalog, java_schema)
        vector_frame = PolicyFrame(**spec)
        lane_frame = PolicyFrame(**spec)
        lanes = list(range(min(per_lane_lanes, count)))

        def reset() -> None:
            vector.reset(
                list(range(seed_base, seed_base + count)),
                *decks,
                *slots,
                setup0=setup,
                setup1=setup,
            )

        # Warm the JIT, the catalog and the NumPy paths.
        reset()
        for _ in range(max(10, steps // 20)):
            actions = draw_actions(vector.masks, rng)
            vector.step(actions)
            vectorized_builder.encode(vector_frame)
            per_lane_builder.encode(lane_frame, lanes)

        vectorized_seconds: list[float] = []
        per_lane_seconds: list[float] = []
        loop_seconds: list[float] = []
        mismatches: list[str] = []
        verify = True
        verify_ran = False
        check_step = max(1, steps // 2)
        for repeat in range(repeats):
            reset()
            encode_t = 0.0
            lane_t = 0.0
            loop_t = 0.0
            for step in range(steps):
                before = time.perf_counter()
                actions = draw_actions(vector.masks, rng)
                vector.step(actions)
                loop_t += time.perf_counter() - before
                before = time.perf_counter()
                vectorized_builder.encode(vector_frame)
                encode_t += time.perf_counter() - before
                before = time.perf_counter()
                per_lane_builder.encode(lane_frame, lanes)
                lane_t += time.perf_counter() - before
                if verify and step == check_step:
                    mismatches = _compare_frames(lane_frame, vector_frame, lanes)
                    verify_ran = True
                    verify = False
            vectorized_seconds.append(encode_t)
            per_lane_seconds.append(lane_t)
            loop_seconds.append(loop_t)

        best_vectorized = min(vectorized_seconds)
        best_per_lane = min(per_lane_seconds)
        best_loop = min(loop_seconds)
        vectorized_per_lane = best_vectorized / (count * steps)
        per_lane_per_lane = best_per_lane / (len(lanes) * steps)
        tensorize = None
        return {
            "threads": vector.threads,
            "count": count,
            "steps": steps,
            "repeats": repeats,
            "per_lane_lanes": len(lanes),
            "data_version": vector.data_version,
            "content_sha": vector.content_sha,
            "vectorized": {
                "ms_per_lane": vectorized_per_lane * 1_000.0,
                "ms_per_batch": vectorized_per_lane * count * 1_000.0,
                "decisions_per_s": 1.0 / vectorized_per_lane if vectorized_per_lane else 0.0,
                "loop_decisions_per_s": (
                    count * steps / (best_loop + best_vectorized) if best_loop + best_vectorized else 0.0
                ),
                "spread_pct": _spread(vectorized_seconds),
            },
            "per_lane": {
                "ms_per_lane": per_lane_per_lane * 1_000.0,
                "projected_ms_per_batch_256": per_lane_per_lane * 256 * 1_000.0,
                "decisions_per_s": 1.0 / per_lane_per_lane if per_lane_per_lane else 0.0,
                "spread_pct": _spread(per_lane_seconds),
            },
            "encode_speedup": (
                per_lane_per_lane / vectorized_per_lane if vectorized_per_lane else 0.0
            ),
            "equivalence": {
                "checked": verify_ran and not mismatches,
                "ran": verify_ran,
                "lanes": len(lanes),
                "mismatches": mismatches,
            },
            "tensorize": tensorize,
        }


def _spread(seconds: Sequence[float]) -> float:
    if len(seconds) < 2:
        return 0.0
    best = min(seconds)
    return (max(seconds) - best) / best * 100.0 if best else 0.0


def _measure_existing_path(
    *,
    port: int,
    checkpoint: str,
    decisions: int,
    seed: int,
) -> dict[str, Any]:
    """Time the existing bridge observation + tensorizer path per decision.

    The tensorizer is stage C3 work; C1 only needs its per-decision cost to show
    the split.  The tensorizer half is timed *inside* a real policy session
    (``tensorize`` wrapped, ``record_action`` still called by the session), which
    is the only way to keep the public tracker consistent: replaying
    observations without the actions the policy recorded trips the hand/cycle
    partition check.  The observation half stays a cold ``observe`` call on the
    same tick, which is the build a trained packet pays.
    """

    import torch
    from bridge_session import build_episode, initial_elixir

    torch.set_num_threads(1)
    # The tensorizer half stays on the evaluator deck, which is the shape the C0
    # soak validates and which the tensorizer classifies completely.  The X-Bow
    # learner deck currently surfaces a public `effect` entity that resolves to
    # UNKNOWN archetype (native_data_global_id=1) and fails closed; that is a C2
    # item, not a C1 measurement (the native notes).
    environment, observations, sessions, close = build_episode(
        port=port, checkpoint=checkpoint, seed=seed
    )
    starting_elixir = initial_elixir(observations)
    for owner, session in sessions.items():
        session.start_episode(observations[owner], initial_elixir=starting_elixir)

    tensorizer = sessions[0].tensorizer
    tensorize_seconds: list[float] = []
    original_tensorize = tensorizer.tensorize

    def timed_tensorize(observation, *, validate: bool = True):
        before = time.perf_counter()
        batch = original_tensorize(observation, validate=validate)
        tensorize_seconds.append(time.perf_counter() - before)
        return batch

    observation_seconds: list[float] = []
    step_seconds: list[float] = []
    measured = 0
    tensorizer.tensorize = timed_tensorize
    try:
        for _ in range(decisions):
            # ``step`` has already built this tick's observations; clear the
            # per-tick scene and mask caches so the timed call is a cold build,
            # which is the cost a trained packet actually pays.
            environment._fair_scene_cache_identity = None
            environment._fair_scene_cache = None
            environment._action_mask_cache.clear()
            before = time.perf_counter()
            environment.observe(owner=0, policy_features_only=True)
            observation_seconds.append(time.perf_counter() - before)
            commands = {}
            for owner, session in sessions.items():
                commands[owner] = tuple(session.decide(observations[owner]).decoded.actions)
            before = time.perf_counter()
            observations, _, terminated, truncated, _ = environment.step(
                commands, record_trace=False
            )
            step_seconds.append(time.perf_counter() - before)
            measured += 1
            if bool(terminated["__all__"]) or bool(truncated["__all__"]):
                break
    finally:
        tensorizer.tensorize = original_tensorize
        close()

    def summary(values: list[float]) -> dict[str, float]:
        best = min(values)
        return {
            "ms_per_lane": best * 1_000.0,
            "median_ms_per_lane": statistics.median(values) * 1_000.0,
            "ms_per_batch_256": best * 256 * 1_000.0,
        }

    observation = summary(observation_seconds)
    tensorize = summary(tensorize_seconds)
    combined = summary([o + t for o, t in zip(observation_seconds, tensorize_seconds)])
    return {
        "source": f"bridge:{port}",
        "decisions": measured,
        "observation": observation,
        "tensorize": tensorize,
        "observation_plus_tensorize": combined,
        "step": summary(step_seconds),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--count", type=int, default=256, help="lanes in the vector")
    parser.add_argument("--threads", type=int, nargs="+", default=[1, 8])
    parser.add_argument("--steps", type=int, default=200, help="decisions per trial")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--per-lane-lanes",
        type=int,
        default=16,
        help="lanes the control encodes each decision; per-lane ms is scaled",
    )
    parser.add_argument("--install", default=str(DEFAULT_INSTALL))
    parser.add_argument("--tables", default=str(DEFAULT_TABLES))
    parser.add_argument("--level", type=int, default=DEFAULT_LEVEL)
    parser.add_argument("--tower-level", type=int, default=DEFAULT_LEVEL)
    parser.add_argument("--seed-base", type=int, default=1)
    parser.add_argument(
        "--tensorize-port",
        type=int,
        default=None,
        help="a live crforge bridge to measure the existing observe+tensorizer path on",
    )
    parser.add_argument(
        "--tensorize-checkpoint",
        default=".resource/RoyaleFish/models/B-general-v275.pt",
    )
    parser.add_argument("--tensorize-decisions", type=int, default=100)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    catalog = Catalog.load()
    rows: list[dict[str, Any]] = []
    for threads in args.threads:
        row = _run_one(
            threads=threads,
            count=args.count,
            steps=args.steps,
            repeats=args.repeats,
            per_lane_lanes=args.per_lane_lanes,
            install=args.install,
            tables=args.tables,
            seed_base=args.seed_base,
            level=args.level,
            tower_level=args.tower_level,
            catalog=catalog,
            # The JVM starts with the first VectorBattle; this proxy resolves
            # the bridge's Java Schema constants on first use inside the run.
            java_schema=_LazyJavaSchema(),
        )
        rows.append(row)

    if args.tensorize_port is not None:
        existing = _measure_existing_path(
            port=args.tensorize_port,
            checkpoint=args.tensorize_checkpoint,
            decisions=args.tensorize_decisions,
            seed=args.seed_base,
        )
        tensorize_ms = existing["tensorize"]["ms_per_lane"]
        for row in rows:
            row["existing_path"] = existing
            # C3 feeds the tensorizer from the vectorized frame instead of
            # rebuilding ObservationV1; the observation half is what the
            # vectorized encoder replaces, the tensorizer is what remains.
            projected_ms = row["vectorized"]["ms_per_lane"] + tensorize_ms
            today_ms = existing["observation_plus_tensorize"]["ms_per_lane"]
            row["projected"] = {
                "ms_per_lane": projected_ms,
                "decisions_per_s": 1_000.0 / projected_ms if projected_ms else 0.0,
                "matches_per_s_1214": 1_000.0 / projected_ms / 1_214.0 if projected_ms else 0.0,
                "today_ms_per_lane": today_ms,
                "speedup_vs_today": today_ms / projected_ms if projected_ms else 0.0,
            }

    for row in rows:
        vectorized = row["vectorized"]
        gate = vectorized["decisions_per_s"] >= 1_000.0
        target = vectorized["ms_per_lane"] <= 0.5
        projected_gate = None
        if row.get("projected"):
            projected_gate = row["projected"]["ms_per_lane"] <= 1.0
        row["gate"] = {
            "encoder_1000_decisions_s": gate,
            "encoder_target_0_5_ms_per_lane": target,
            "projected_end_to_end_1000_decisions_s": projected_gate,
            "equivalence": row["equivalence"]["checked"],
        }
        print(
            f"threads {row['threads']}: vectorized {vectorized['ms_per_lane']:.4f} ms/lane, "
            f"{vectorized['decisions_per_s']:.0f} decisions/s; "
            f"per-lane {row['per_lane']['ms_per_lane']:.4f} ms/lane, "
            f"{row['encode_speedup']:.1f}x; "
            f"equivalence {'pass' if row['equivalence']['checked'] else 'FAIL'}",
            flush=True,
        )
        if row.get("existing_path"):
            existing = row["existing_path"]
            print(
                f"  existing bridge: observe {existing['observation']['ms_per_lane']:.4f}, "
                f"tensorize {existing['tensorize']['ms_per_lane']:.4f}, "
                f"observe+tensorize {existing['observation_plus_tensorize']['ms_per_lane']:.4f} ms/lane",
                flush=True,
            )
            print(
                f"  projected end-to-end {row['projected']['ms_per_lane']:.4f} ms/lane = "
                f"{row['projected']['decisions_per_s']:.0f} decisions/s "
                f"({row['projected']['speedup_vs_today']:.1f}x today)",
                flush=True,
            )

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "tool": "crforge-encoder-spike.v1",
                    "count": args.count,
                    "steps": args.steps,
                    "repeats": args.repeats,
                    "deck": list(DECK),
                    "deck_slots": list(DECK_SLOTS),
                    "level": args.level,
                    "tower_level": args.tower_level,
                    "install": str(args.install),
                    "tables": str(args.tables),
                    "rows": rows,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


def _jvm_started() -> bool:
    try:
        import jpype

        return bool(jpype.isJVMStarted())
    except ImportError:
        return False


class _LazyJavaSchema:
    """A Java ``Schema`` handle resolved after the vector starts the JVM."""

    def __getattr__(self, name: str):
        if not name.startswith(
            ("H_", "E_", "PENDING_", "HEADER_", "ENTITY_", "KIND_")
        ):
            raise AttributeError(name)
        from crforge_env._jvm import java_class

        value = getattr(java_class("org.crforge.env.Schema"), name)
        setattr(self, name, value)
        return value


if __name__ == "__main__":
    raise SystemExit(main())
