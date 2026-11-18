"""Batched row build for the C3-B packet: groups, cards, candidates and relations.

C3 measured the packet as ~1.0 CPU ms/decision of tensorizer and attributed
~0.54 of it to ``_groups`` + its children, the largest section, then ~0.33 to
``_card_sets``, ~0.30 to ``_candidates`` and ~0.14 to ``_relations``.  Session
17 landed the vectorized facts layer (``vector_observation.py``, 0.032 ms/lane);
this module is the row build on top of it: the same rows the per-lane
``UniversalObservationTensorizerV4`` emits, computed for every lane of a
``VectorBattle`` in one pass over fixed-size NumPy arrays.

Sections here:

* :class:`VectorGroupBuilder` — causal groups and children;
* :class:`VectorCardBuilder` — the actor's and opponent's card sets, including
  the 16.635 21-column pending-execution columns;
* :class:`VectorTowerBuilder` — the six-row tower section (health, active,
  target), with the tower troop as a static build input;
* :class:`VectorSpatialPlaneBuilder` — the 14-channel explicit spatial planes;
* :class:`VectorScalarBuilder` — the generic match scalars (clock, phase,
  elixirs, crowns, counts);
* :class:`VectorEmptySectionBuilder` — the empty active-effects and event
  records the no-event envelope carries;
* :class:`VectorCandidateBuilder` — the actor's legal deploy candidates, with
  the native placement grid and the static building geometry the decoder needs;
* :class:`VectorRelationBuilder` — target/reverse-target and card→group edges.

Scope is deliberately narrow, and the scope is the bridge's no-event envelope
``VectorObservationBatch`` carries:

* one group per entity (the bridge never sends a causal-group reference, so
  ``build_causal_groups`` returns singletons);
* the bridge's raw ``entity_kind`` rule: the card spec's kind for a resolved
  card, ``effect`` for a row with no card id, ``unknown`` otherwise.  Towers
  are not entities (they are the separate six-row tower section);
* no attack/movement/projectile/visibility/evolution/ability runtime, no source
  entities, no captures and no combat events, exactly as the bridge's minimal
  rich player envelope supplies them;
* ``revealed_cards`` stays empty (the bridge receives no events), so the
  opponent card set is the eight zero rows plus the unknown-card row;
* deploy candidates only: ``players.abilityRuntime`` is ``null`` in this
  envelope, so no ability source exists and no ABILITY row is emitted.  Mirror
  is refused (it needs the actor-private runtime contract the frame lacks);
* seat 0 (the facts layer reads ``VectorBattle.observations`` seat 0).

The per-lane tensorizer stays the oracle.  ``reference_observation`` /
``reference_action_mask`` project the ``EntityStateV1``/player/tower/pending/mask
rows the oracle consumes from the same facts, and each gate compares the whole
section against the stock method.  The oracle resolves archetypes, forms,
tokens, placement rows and edge order through its own helpers, so a mismatch
means this batched build is wrong, not that both sides share a shortcut.

Nothing here validates or freezes a contract, and nothing here may be used to
replace the validated evaluator/parity path.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import torch

from native_runner.arena import GRID_HEIGHT, GRID_WIDTH, WORLD_HEIGHT, WORLD_WIDTH, native_building_footprint
from native_runner.building_placement_profiles import building_placement_profile
from native_runner.contracts import DEFAULT_TICK_MS as TICK_MS
from native_runner.contracts import (
    ActionKind,
    ActionV1,
    CardKind,
    EntityStateV1,
    ObservationTier,
    PendingActionV1,
    TargetKind,
    construct_contract,
)
from native_runner.cr_native_env import LIVE_COMMAND_AGE_TICKS
from native_runner.match_factory import NATIVE_GAMEPLAY_END_TICK
from native_runner.training.v4.catalog import UNKNOWN_CARD_VOCAB_ID
from native_runner.training.v4.config import (
    CARD_RUNTIME_FEATURE_NAMES,
    CARD_RUNTIME_FEATURE_NAMES_V2,
    CHILD_FEATURE_NAMES,
    GROUP_FEATURE_NAMES,
    MATCH_SCALAR_NAMES,
    TOWER_FEATURE_NAMES,
)
from native_runner.training.v4.decoding import candidate_uid_v4
from native_runner.training.v4.mechanics import ACTIVE_EFFECT_RUNTIME_FEATURE_NAMES
from native_runner.training.v4.native_actions import MIRROR_CARD_ID
from native_runner.training.v4.tensors import (
    CANDIDATE_DEPLOY,
    TARGET_GRID,
    ActionCandidatesV4,
    ActiveEffectSetV4,
    BattleGroupSetV4,
    CardSetV4,
    EventSetV4,
    RelationEdgesV4,
    TowerSetV4,
)

from .catalog import FORM_EVOLVED, FORM_HERO
from .observe import _nearest_card_id
from .vector_observation import VectorObservationBatch

# The tensorizer's vocabularies.  Kept local so importing this module does not
# pull in the whole tensorizer (and torch graph machinery) at module import.
CHILD_TYPE = {
    "troop": 0,
    "building": 1,
    "hero": 2,
    "projectile": 3,
    "area": 4,
    "effect": 5,
    "character": 6,
    "unknown": 7,
}
GROUP_TYPE = {"deployment": 0, "spawn_wave": 1, "volley": 2, "persistent_effect": 3}

OWNER_SELF = 0
OWNER_ENEMY = 1
OWNER_NEUTRAL = 2

FORM_NORMAL = 0
UNKNOWN_ARCHETYPE_VOCAB_ID = 1
# The native hand is four indexed slots; ``hand_slot_by_card`` addresses them.
HAND_SLOTS = 4

# Feature indices, named so the build reads like the tensorizer's assignments.
_G_MEMBERS = GROUP_FEATURE_NAMES.index("member_count_over_16")
_G_DROPPED = GROUP_FEATURE_NAMES.index("dropped_child_count_over_16")
_G_HP = GROUP_FEATURE_NAMES.index("summed_hitpoints_over_10000")
_G_SHIELD = GROUP_FEATURE_NAMES.index("summed_shield_over_10000")
_G_RATIO_MEAN = GROUP_FEATURE_NAMES.index("mean_hitpoints_ratio")
_G_RATIO_MIN = GROUP_FEATURE_NAMES.index("min_hitpoints_ratio")
_G_RATIO_MAX = GROUP_FEATURE_NAMES.index("max_hitpoints_ratio")
_G_SPAN_X = GROUP_FEATURE_NAMES.index("x_span_tiles")
_G_SPAN_Y = GROUP_FEATURE_NAMES.index("y_span_tiles")
_G_ABILITY = GROUP_FEATURE_NAMES.index("has_ability_source")
_G_TARGET = GROUP_FEATURE_NAMES.index("has_visible_target")
_G_VELOCITY_X = GROUP_FEATURE_NAMES.index("mean_velocity_x_over_10")
_G_VELOCITY_Y = GROUP_FEATURE_NAMES.index("mean_velocity_y_over_10")
_G_SPEED = GROUP_FEATURE_NAMES.index("mean_speed_over_10")

_C_HP_RATIO = CHILD_FEATURE_NAMES.index("hitpoints_ratio")
_C_HP = CHILD_FEATURE_NAMES.index("hitpoints_over_10000")
_C_MAX_HP = CHILD_FEATURE_NAMES.index("max_hitpoints_over_10000")
_C_SHIELD = CHILD_FEATURE_NAMES.index("shield_over_10000")
_C_AGE = CHILD_FEATURE_NAMES.index("age_over_60000ms")
_C_TARGET = CHILD_FEATURE_NAMES.index("has_visible_target")
_C_ABILITY = CHILD_FEATURE_NAMES.index("is_ability_source")
_C_X = CHILD_FEATURE_NAMES.index("x_over_board_width")
_C_Y = CHILD_FEATURE_NAMES.index("y_over_board_height")
_C_VELOCITY_X = CHILD_FEATURE_NAMES.index("velocity_x_over_10")
_C_VELOCITY_Y = CHILD_FEATURE_NAMES.index("velocity_y_over_10")

# Tower feature columns (``native_runner/training/v4/config.py``).  The bridge's
# no-event envelope leaves the attack-state and tower-troop-runtime blocks at
# zero, so only the six static/health columns are written.
_T_HP_RATIO = TOWER_FEATURE_NAMES.index("hitpoints_ratio")
_T_SHIELD_RATIO = TOWER_FEATURE_NAMES.index("shield_over_max_hitpoints")
_T_ACTIVE = TOWER_FEATURE_NAMES.index("active")
_T_TARGET = TOWER_FEATURE_NAMES.index("has_visible_target")
_T_KING_ACTIVATED = TOWER_FEATURE_NAMES.index("king_activated")
_T_HP = TOWER_FEATURE_NAMES.index("hitpoints_over_5000")
_T_MAX_HP = TOWER_FEATURE_NAMES.index("max_hitpoints_over_5000")

# Card runtime columns (the 16-column schema; the 16.635 21-column schema adds
# the five pending-execution columns at the end).
_CD_IN_HAND = CARD_RUNTIME_FEATURE_NAMES.index("in_hand")
_CD_NEXT = CARD_RUNTIME_FEATURE_NAMES.index("is_next_card")
_CD_UNKNOWN_FRACTION = CARD_RUNTIME_FEATURE_NAMES.index("unknown_opponent_card_fraction")
_CD_PENDING = CARD_RUNTIME_FEATURE_NAMES_V2.index("pending_execution")
_CD_PENDING_TICKS = CARD_RUNTIME_FEATURE_NAMES_V2.index("pending_ticks_remaining_over_20")
_CD_PENDING_X = CARD_RUNTIME_FEATURE_NAMES_V2.index("pending_target_x_over_board_width")
_CD_PENDING_Y = CARD_RUNTIME_FEATURE_NAMES_V2.index("pending_target_y_over_board_height")
_CD_PENDING_ABILITY = CARD_RUNTIME_FEATURE_NAMES_V2.index("ability_pending")

# Relation vocabularies (``native_runner/training/v4/tensorizer.py``).
REL_TARGETS = 1
REL_TARGETED_BY = 2
REL_CARD_REPRESENTS_GROUP = 7
RELATION_TYPES = 14


def _actor_seat(tensorizer: Any) -> int:
    """The actor seat the tensorizer builds for; both seats are supported."""

    seat = int(tensorizer.actor_owner)
    if seat not in (0, 1):
        raise ValueError(f"tensorizer actor owner {seat} is not a seat")
    return seat


def _kind_rule(card_id: int, card_specs: dict[int, Any]) -> str:
    """The bridge's raw ``entity_kind`` for a card id (no projectile runtime)."""

    spec = card_specs.get(int(card_id)) if int(card_id) > 0 else None
    if spec is not None:
        return str(spec.kind.value)
    return "effect" if int(card_id) <= 0 else "unknown"


def _tile_positions(
    world_x: np.ndarray, world_y: np.ndarray, *, config: Any, perspective: Any
) -> tuple[np.ndarray, np.ndarray]:
    """The tensorizer's ``_tile_position`` over arrays: flip, scale, clamp."""

    x = float(WORLD_WIDTH) - world_x if perspective.flip_x else world_x
    y = float(WORLD_HEIGHT) - world_y if perspective.flip_y else world_y
    x = x / (float(WORLD_WIDTH) / float(config.board_width))
    y = y / (float(WORLD_HEIGHT) / float(config.board_height))
    x = np.minimum(np.maximum(x, 0.0), float(config.board_width) - 1e-4)
    y = np.minimum(np.maximum(y, 0.0), float(config.board_height) - 1e-4)
    return x, y


@dataclass(frozen=True)
class VectorEntityTables:
    """Static policy semantics for every row of the vector vocabulary.

    One table build per tensorizer, not per decision.  ``vocab_size`` is the
    vector's vocabulary length; entries outside it are never read.
    """

    vocab_size: int
    card_id: np.ndarray
    entity_kind: tuple[str, ...]
    archetype_id: np.ndarray
    child_kind: tuple[str, ...]
    child_type: np.ndarray
    global_id: np.ndarray
    base_card_id: np.ndarray
    card_vocab_id: np.ndarray
    runtime_form: np.ndarray
    group_type: np.ndarray
    footprint_half_w: np.ndarray
    footprint_half_h: np.ndarray
    radius: np.ndarray
    is_hero_or_building: np.ndarray
    is_airborne: np.ndarray
    static_damage: np.ndarray
    is_persistent_area_root: np.ndarray

    @classmethod
    def build(cls, *, tensorizer: Any, vocabulary: Sequence[str], bridge_catalog: Any) -> VectorEntityTables:
        """Compile the tables from the tensorizer's own catalogs.

        ``bridge_catalog`` is ``crforge_bridge.catalog.Catalog``; it is the
        vocabulary-name -> FirstLight card id rule the bridge's decoders use.
        """

        names = [str(name) for name in vocabulary]
        size = len(names)
        specs = tensorizer.card_specs
        archetypes = tensorizer.entity_archetype_catalog
        global_by_form = {form: int(global_id) for global_id, form in archetypes.character_global_ids}

        card_id = np.full(size, -1, dtype=np.int64)
        archetype_id = np.full(size, UNKNOWN_ARCHETYPE_VOCAB_ID, dtype=np.int64)
        global_id = np.zeros(size, dtype=np.int64)
        entity_kind: list[str] = []
        child_kind: list[str] = []
        child_type = np.zeros(size, dtype=np.int64)
        base_card_id = np.full(size, -1, dtype=np.int64)
        card_vocab_id = np.zeros(size, dtype=np.int64)
        runtime_form = np.zeros(size, dtype=np.int64)
        group_type = np.zeros(size, dtype=np.int64)
        footprint_w = np.zeros(size, dtype=np.float64)
        footprint_h = np.zeros(size, dtype=np.float64)
        radius = np.zeros(size, dtype=np.float64)
        is_hero_or_building = np.zeros(size, dtype=bool)
        is_airborne = np.zeros(size, dtype=bool)
        static_damage = np.zeros(size, dtype=np.float64)
        is_persistent_area_root = np.zeros(size, dtype=bool)

        for index, name in enumerate(names):
            value = bridge_catalog.card_id(name)
            if value is None:
                value = _nearest_card_id(bridge_catalog, name)
            if value is not None:
                card_id[index] = int(value)

            kind = _kind_rule(int(card_id[index]), specs)
            entity_kind.append(kind)

            archetype = archetypes.vocab_id(f"form:{name}")
            if archetype <= UNKNOWN_ARCHETYPE_VOCAB_ID:
                for node_id in (f"CHARACTER.{name}", f"BUILDING.{name}", f"AREA.{name}", f"AOE.{name}", f"EXT.{name}"):
                    candidate = archetypes.node_vocab_id(node_id)
                    if candidate > UNKNOWN_ARCHETYPE_VOCAB_ID:
                        archetype = candidate
                        break
            archetype_id[index] = int(archetype)
            if name in global_by_form:
                global_id[index] = global_by_form[name]

            metadata = archetypes.metadata_for_vocab_id(int(archetype))
            if not metadata.child_kind_known:
                resolved = kind
            elif metadata.child_kind in {"building", "projectile", "area"}:
                resolved = metadata.child_kind
            elif metadata.child_kind == "character" and kind not in {"troop", "hero"}:
                resolved = "character"
            else:
                resolved = kind
            child_kind.append(resolved)
            child_type[index] = CHILD_TYPE.get(resolved, CHILD_TYPE["unknown"])

            # Spatial-plane semantics are the same archetype metadata
            # ``_entity_child_kind`` / ``_entity_is_airborne`` /
            # ``_entity_static_damage`` read, compiled once per row.
            is_airborne[index] = bool(metadata.is_airborne) if metadata.is_airborne_known else False
            static_damage[index] = (
                float(metadata.static_damage_basis)
                if metadata.static_damage_basis_known and metadata.static_damage_basis is not None
                else 0.0
            )
            # ``_entity_is_persistent_area_root`` also tests a persistent-effect
            # causal group, which the bridge envelope never carries.
            is_persistent_area_root[index] = resolved in {"area", "effect"}

            if kind in {"hero", "building"}:
                is_hero_or_building[index] = True
            if kind == "hero":
                runtime_form[index] = FORM_HERO
            if kind == "projectile":
                group_type[index] = GROUP_TYPE["volley"]
            elif kind in {"area", "effect"}:
                group_type[index] = GROUP_TYPE["persistent_effect"]
            else:
                group_type[index] = GROUP_TYPE["deployment"]

            # ``_normalize_public_card_id``: entity card ids are already base
            # cards, so a card in the policy namespace survives and anything
            # else is not a public play.
            raw_card = int(card_id[index])
            base = raw_card if 26_000_000 <= raw_card < 29_000_000 else -1
            base_card_id[index] = base
            if base > 0:
                # Mirrors ``_vocab_id``: an out-of-catalog card is a hard
                # error on the oracle path.  The table carries the sentinel;
                # ``build`` raises only when such a row is actually retained.
                card_vocab_id[index] = int(tensorizer.catalog.vocab_id(base))

            if resolved == "building":
                spec = specs.get(base) if base > 0 else None
                footprint = native_building_footprint(spec) if spec is not None else None
                if footprint is not None:
                    footprint_w[index] = float(footprint.width_tiles) / 2.0
                    footprint_h[index] = float(footprint.height_tiles) / 2.0
            elif resolved in {"area", "effect"}:
                value = 0.0
                if metadata.child_kind == "area" and metadata.projectile_radius_known:
                    value = float(metadata.projectile_radius_tiles)
                else:
                    spec = specs.get(base) if base > 0 else None
                    if spec is not None and spec.kind == CardKind.SPELL and spec.radius_tiles and spec.radius_tiles > 0:
                        value = float(spec.radius_tiles)
                radius[index] = value
                footprint_w[index] = value
                footprint_h[index] = value

        return cls(
            vocab_size=size,
            card_id=card_id,
            entity_kind=tuple(entity_kind),
            archetype_id=archetype_id,
            child_kind=tuple(child_kind),
            child_type=child_type,
            global_id=global_id,
            base_card_id=base_card_id,
            card_vocab_id=card_vocab_id,
            runtime_form=runtime_form,
            group_type=group_type,
            footprint_half_w=footprint_w,
            footprint_half_h=footprint_h,
            radius=radius,
            is_hero_or_building=is_hero_or_building,
            is_airborne=is_airborne,
            static_damage=static_damage,
            is_persistent_area_root=is_persistent_area_root,
        )


@dataclass(frozen=True)
class VectorGroupSection:
    """The batched ``_groups`` output for every lane.

    ``groups`` tensors have the lane count as their batch dimension.  The two
    index arrays are ``(lanes, max_entities)``; ``-1`` marks an entity whose
    group or child row was dropped, so a caller can reproduce the tensorizer's
    per-lane dicts exactly.
    """

    groups: BattleGroupSetV4
    group_index: np.ndarray
    child_index: np.ndarray
    group_overflow: np.ndarray
    child_overflow: np.ndarray

    def lane(self, lane: int) -> BattleGroupSetV4:
        """One lane back to the exact one-row record the stock method emits."""

        index = int(lane)

        def rows(value: torch.Tensor) -> torch.Tensor:
            return value[index : index + 1].clone()

        return BattleGroupSetV4(
            source_card_vocab_id=rows(self.groups.source_card_vocab_id),
            group_type=rows(self.groups.group_type),
            owner_type=rows(self.groups.owner_type),
            runtime_form=rows(self.groups.runtime_form),
            features=rows(self.groups.features),
            position=rows(self.groups.position),
            extent=rows(self.groups.extent),
            mask=rows(self.groups.mask),
            child_archetype_id=rows(self.groups.child_archetype_id),
            child_group_index=rows(self.groups.child_group_index),
            child_type=rows(self.groups.child_type),
            child_features=rows(self.groups.child_features),
            child_position=rows(self.groups.child_position),
            child_extent=rows(self.groups.child_extent),
            child_radius=rows(self.groups.child_radius),
            child_mask=rows(self.groups.child_mask),
        )


_GROUP_PAD_FILL = {"owner_type": OWNER_NEUTRAL, "mask": False}
_CHILD_PAD_FILL = {"child_mask": False}
_GROUP_SLOT_FIELDS = (
    "source_card_vocab_id",
    "group_type",
    "owner_type",
    "runtime_form",
    "features",
    "position",
    "extent",
    "mask",
)
_CHILD_SLOT_FIELDS = (
    "child_archetype_id",
    "child_group_index",
    "child_type",
    "child_features",
    "child_position",
    "child_extent",
    "child_radius",
    "child_mask",
)


def pad_group_record(record: BattleGroupSetV4, *, groups: int, children: int) -> BattleGroupSetV4:
    """Widen a group/child record onto a fixed capacity (see the section form)."""

    def widen(values: torch.Tensor, width: int, fill: Any) -> torch.Tensor:
        current = int(values.shape[1])
        if current >= int(width):
            return values
        output = values.new_full((int(values.shape[0]), int(width)) + tuple(values.shape[2:]), fill)
        output[:, :current] = values
        return output

    values: dict[str, torch.Tensor] = {}
    for name in _GROUP_SLOT_FIELDS:
        values[name] = widen(getattr(record, name), int(groups), _GROUP_PAD_FILL.get(name, 0))
    for name in _CHILD_SLOT_FIELDS:
        values[name] = widen(getattr(record, name), int(children), _CHILD_PAD_FILL.get(name, 0))
    return BattleGroupSetV4(**values)


def pad_group_section(section: VectorGroupSection, *, groups: int, children: int) -> VectorGroupSection:
    """Project a live-width section onto a wider fixed capacity.

    The producer emits the batch's live width (see ``VectorGroupBuilder.build``).
    The per-lane oracle and stacked storage still use the configured capacity,
    so the digest gate and any cross-width concatenation pad here.  A section
    that is already at least as wide as the target is returned unchanged.

    The fill matches the oracle's padded tail: zeros for features/positions/
    extents and ids, ``OWNER_NEUTRAL`` for a group's owner, ``False`` for the
    masks.  These rows are unread (their masks are false), but the digest gate
    compares the whole record, so the bytes are part of the contract.
    """

    return VectorGroupSection(
        groups=pad_group_record(section.groups, groups=groups, children=children),
        group_index=section.group_index,
        child_index=section.child_index,
        group_overflow=section.group_overflow,
        child_overflow=section.child_overflow,
    )


class _PhaseMarker:
    """Accumulate named ``perf_counter`` boundaries into a caller's dict.

    ``build(phases=...)`` and ``build_pair(phases=...)`` mark their own terms;
    a caller that passes nothing pays one attribute check per boundary.
    """

    __slots__ = ("phases", "started")

    def __init__(self, phases: dict[str, float] | None) -> None:
        self.phases = phases
        self.started = time.perf_counter() if phases is not None else 0.0

    def __call__(self, name: str) -> None:
        if self.phases is None:
            return
        now = time.perf_counter()
        self.phases[name] = self.phases.get(name, 0.0) + (now - self.started) * 1000.0
        self.started = now


@dataclass(frozen=True)
class _CompactRows:
    """The compact ``(lanes, widest live count)`` entity-row axis.

    A lane's non-tower entity rows are packed into ascending entity-slot order,
    with the entity slot each column came from in ``slots`` and the live count
    in ``counts``.  ``valid`` marks the columns below that lane's count.  The
    padded columns carry the same fills as before, but nothing reads them.
    """

    lanes: int
    width: int
    live: np.ndarray
    lane_of: np.ndarray
    rank: np.ndarray
    counts: np.ndarray
    slots: np.ndarray
    valid: np.ndarray
    vocab_live: np.ndarray

    def scatter(self, values: np.ndarray, dtype: Any = None, fill: Any = 0) -> np.ndarray:
        """Live-row values, ``fill`` in every padded column."""

        out = np.full((self.lanes, self.width), fill, dtype=values.dtype if dtype is None else dtype)
        out[self.lane_of, self.rank] = values
        return out

    def field(self, source: np.ndarray, dtype: Any = None, fill: Any = 0) -> np.ndarray:
        """The live rows of one batch field, ``fill`` in every padded column."""

        return self.scatter(np.asarray(source).ravel()[self.live], dtype, fill)

    def table(self, values: np.ndarray) -> np.ndarray:
        out = np.zeros((self.lanes, self.width), dtype=values.dtype)
        out[self.lane_of, self.rank] = values[self.vocab_live]
        return out


class VectorGroupBuilder:
    """Fill the group/children section for a whole vector batch in one pass."""

    # Tables the group build reads.  ``build_pair`` refuses two builders whose
    # static semantics differ, so a shared row build cannot mix catalogs.
    _PAIR_TABLE_FIELDS = (
        "card_id",
        "archetype_id",
        "child_type",
        "card_vocab_id",
        "runtime_form",
        "group_type",
        "is_hero_or_building",
        "radius",
        "footprint_half_w",
        "footprint_half_h",
    )

    def __init__(self, tensorizer: Any, tables: VectorEntityTables) -> None:
        self.tensorizer = tensorizer
        self.tables = tables
        self.config = tensorizer.config
        self.perspective = tensorizer.perspective
        self.max_groups = int(self.config.max_battle_groups)
        self.max_children = int(self.config.max_children_total)
        # id(other) -> other, held so an id cannot be reused under the cache.
        self._pair_checked: dict[int, VectorGroupBuilder] = {}

    # ---- entity row view ---------------------------------------------------

    def _compact_rows(self, batch: VectorObservationBatch) -> _CompactRows:
        """The shared lane-major axis both actor seats read from.

        The seat only changes a row's position, its velocity sign and which
        owner a group belongs to, so the axis (and every id, hitpoint, table
        value and sort key derived from it) is built once for the batch.
        """

        dense_valid = batch.entity_valid & ~batch.entity_is_tower
        lanes, entity_slots = dense_valid.shape
        live = np.flatnonzero(dense_valid.ravel())
        lane_of = live // entity_slots
        slot_of = live % entity_slots
        counts = np.bincount(lane_of, minlength=lanes).astype(np.int64)
        width = max(1, int(counts.max()))
        starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
        rank = np.arange(live.size, dtype=np.int64) - starts[lane_of]
        slots = np.zeros((lanes, width), dtype=np.int64)
        slots[lane_of, rank] = slot_of
        valid = np.arange(width, dtype=np.int64)[None, :] < counts[:, None]
        row_vocab = np.clip(batch.entity_row_vocab, 0, self.tables.vocab_size - 1)
        return _CompactRows(
            lanes=lanes,
            width=width,
            live=live,
            lane_of=lane_of,
            rank=rank,
            counts=counts,
            slots=slots,
            valid=valid,
            vocab_live=row_vocab.ravel()[live],
        )

    def _core_rows(self, batch: VectorObservationBatch, axis: _CompactRows) -> dict[str, Any]:
        """Every seat-independent per-entity field on the compact axis."""

        target_live = batch.entity_target.ravel()[axis.live]
        velocity_valid = axis.field(batch.entity_velocity_valid, bool)
        velocity_valid &= axis.valid
        return {
            "valid": axis.valid,
            "counts": axis.counts,
            "slots": axis.slots,
            "row_vocab": axis.scatter(axis.vocab_live),
            "entity_id": axis.field(batch.entity_id, np.int64, -1),
            "hp": axis.scatter(np.maximum(batch.entity_hp.ravel()[axis.live], 0), np.float64),
            "max_hp": axis.scatter(np.maximum(batch.entity_max_hp.ravel()[axis.live], 0), np.float64),
            "shield": axis.scatter(np.maximum(batch.entity_shield.ravel()[axis.live], 0), np.float64),
            "age_ms": axis.field(batch.entity_age_ms, np.float64),
            "target": axis.scatter(target_live >= 0),
            "target_id": axis.scatter(target_live, fill=-1),
            "velocity_valid": velocity_valid,
            "card_id": axis.table(self.tables.card_id),
            "archetype_id": axis.table(self.tables.archetype_id),
            "child_type": axis.table(self.tables.child_type),
            "card_vocab_id": axis.table(self.tables.card_vocab_id),
            "runtime_form": axis.table(self.tables.runtime_form),
            "group_type": axis.table(self.tables.group_type),
            "is_hero_or_building": axis.table(self.tables.is_hero_or_building),
            "radius": axis.table(self.tables.radius),
            "side": axis.field(batch.entity_side, fill=-1),
        }

    def _seat_rows(
        self,
        core: dict[str, Any],
        batch: VectorObservationBatch,
        perspective: Any,
        axis: _CompactRows,
    ) -> dict[str, Any]:
        """Add one actor seat's positions, extents and velocity to the core rows."""

        rows = dict(core)
        x = axis.field(batch.entity_x, np.float64)
        y = axis.field(batch.entity_y, np.float64)
        x, y = _tile_positions(x, y, config=self.config, perspective=perspective)
        half_w = axis.table(self.tables.footprint_half_w)
        half_h = axis.table(self.tables.footprint_half_h)
        rows["x"] = x
        rows["y"] = y
        rows["extent_x0"] = x - half_w
        rows["extent_y0"] = y - half_h
        rows["extent_x1"] = x + half_w
        rows["extent_y1"] = y + half_h

        # The model flips signs exactly as ``_model_velocity`` does, then the
        # tensorizer divides by world-to-board scale before the ``/ 10``.  Only
        # the live rows carry a velocity; the padded columns are zero and
        # ``velocity_valid`` is false for them.
        velocity = np.zeros((axis.lanes, axis.width, 2), dtype=np.float64)
        velocity[axis.lane_of, axis.rank] = batch.entity_velocity.reshape(-1, 2)[axis.live]
        if perspective.flip_x:
            np.negative(velocity[..., 0], out=velocity[..., 0])
        if perspective.flip_y:
            np.negative(velocity[..., 1], out=velocity[..., 1])
        scale_x = float(WORLD_WIDTH) / float(self.config.board_width)
        scale_y = float(WORLD_HEIGHT) / float(self.config.board_height)
        np.divide(velocity[..., 0], scale_x, out=velocity[..., 0])
        np.divide(velocity[..., 1], scale_y, out=velocity[..., 1])
        rows["velocity"] = velocity
        return rows

    def _entity_rows(self, batch: VectorObservationBatch) -> dict[str, Any]:
        """Per-entity arrays for every lane of one actor seat, at live width.

        The previous form assembled the same values but scattered them into a
        zero-filled ``(lanes, max_entities)`` array, so every field paid the
        200-row axis in writes — float64 ids included — for ~11 live rows.  The
        group build only ever reads live rows, and the section's dense
        ``group_index`` / ``child_index`` are scattered back once at the end.

        This is one seat's view.  :meth:`build_pair` builds the same rows for
        both seats from one shared core, which is what the producer does.
        """

        axis = self._compact_rows(batch)
        return self._seat_rows(self._core_rows(batch, axis), batch, self.perspective, axis)

    def _dense_entity_rows(self, batch: VectorObservationBatch, rows: dict[str, Any]) -> dict[str, Any]:
        """Expand compact rows onto the ``max_entities`` axis (oracle form).

        ``build(live_slots=False)`` restores the session-42 assembly, which
        gathers over the full entity axis.  This is its input: every compact
        column written back to the entity slot it came from, and the fills the
        dense form used on every other slot.
        """

        dense_valid = batch.entity_valid & ~batch.entity_is_tower
        lanes, entity_slots = dense_valid.shape
        slots = rows["slots"]
        valid = rows["valid"]
        lane_axis = np.broadcast_to(np.arange(lanes, dtype=np.int64)[:, None], valid.shape)
        fills = {"entity_id": -1, "target_id": -1, "side": -1}
        dense: dict[str, Any] = {}
        for name, values in rows.items():
            if name in {"valid", "counts", "slots"}:
                continue
            output = np.full((lanes, entity_slots) + values.shape[2:], fills.get(name, 0), dtype=values.dtype)
            output[lane_axis[valid], slots[valid]] = values[valid]
            dense[name] = output
        dense["valid"] = dense_valid
        return dense

    # ---- sort keys ---------------------------------------------------------

    def _slot_order(self, include: np.ndarray, keys: np.ndarray, width: int | None = None) -> np.ndarray:
        """Order a lane's ``include`` slots by ``keys``, as indices into the axis.

        Only the included slots are sorted: ``build`` reads at most the lane's
        live width from the result, and sorting (and keying) all entries was
        most of the sort's cost.  The producer's axis is the compact live-row
        axis (``_entity_rows``), the oracle's is the entity axis.  Padded slots
        repeat index 0; ``build`` masks them off, exactly as it masked the
        invalid rows that used to fill this tail.  ``width`` pads the axis out
        to a fixed capacity for the ``live_slots=False`` oracle path.
        """

        lanes, entity_count = include.shape
        count = include.sum(axis=1)
        live_width = max(1, int(count.max()))
        if width is not None:
            live_width = max(live_width, int(width))
        flat = np.flatnonzero(include.ravel())
        lane_of = flat // entity_count
        starts = np.concatenate(([0], np.cumsum(count)[:-1]))
        rank = np.arange(flat.size, dtype=np.int64) - starts[lane_of]
        keyed = np.full((lanes, live_width), np.iinfo(np.int64).max, dtype=np.int64)
        keyed[lane_of, rank] = keys
        index = np.zeros((lanes, live_width), dtype=np.int64)
        index[lane_of, rank] = flat - lane_of * entity_count
        return np.take_along_axis(index, np.argsort(keyed, axis=1, kind="stable"), axis=1)

    def _orders(self, rows: dict[str, np.ndarray], *, width: int | None = None) -> np.ndarray:
        """Stable per-lane order for the group priority.

        ``_group_priority`` over singleton members reduces to
        ``(id not in ability_sources, no visible target, not hero/building,
        no projectile, id)``.  The bridge envelope has no ability sources and
        no projectiles, so only the target and kind terms vary per entity.
        Encoding the tuple in one int64 key keeps the sort vectorized across
        lanes.
        """

        valid = rows["valid"]
        flat = np.flatnonzero(valid.ravel())
        relation = np.where(rows["target"].ravel()[flat], 0, 1)
        hero_building = np.where(rows["is_hero_or_building"].ravel()[flat], 0, 1)
        entity_id = np.clip(rows["entity_id"].ravel()[flat], 0, 2**31 - 1)
        key = ((relation.astype(np.int64) * 2 + hero_building) << 52) | entity_id
        return self._slot_order(valid, key, width)

    def _targeted_mask(self, rows: dict[str, np.ndarray]) -> np.ndarray:
        """Per-entity: is some other entity's visible target this entity?

        The tensorizer's child priority uses the whole observation's
        ``relation_ids`` set, which is keyed by *target* id.  Lane-tagged int64
        keys reduce the membership test to one ``searchsorted`` over the whole
        batch, with no per-lane Python loop.

        The keys are built at the **live rows** only.  Every invalid or tower
        slot carries ``entity_id == -1``, which tags as ``0xFFFFFFFF`` and can
        never equal a non-negative target id, so those slots are false in both
        forms; the dense batch-wide key arrays were ~33x the work for the same
        answer.
        """

        targeted = np.zeros(rows["valid"].shape, dtype=bool)
        if not rows["target"].any():
            return targeted
        shape = rows["valid"].shape
        live = np.flatnonzero(rows["valid"].ravel())
        lane_offsets = ((live // shape[1]).astype(np.int64) << 32)
        # The set the tensorizer builds is target ids, not the ids of the
        # entities that own a target.
        has_target = rows["target"].ravel()[live]
        target_ids = rows["target_id"].ravel()[live][has_target].astype(np.int64)
        target_keys = np.sort(lane_offsets[has_target] | (target_ids & 0xFFFFFFFF))
        keys = lane_offsets | (rows["entity_id"].ravel()[live] & 0xFFFFFFFF)
        positions = np.searchsorted(target_keys, keys)
        clipped = np.clip(positions, 0, target_keys.size - 1)
        targeted.ravel()[live] = (positions < target_keys.size) & (target_keys[clipped] == keys)
        return targeted

    def _child_order(
        self,
        rows: dict[str, np.ndarray],
        candidate: np.ndarray,
        targeted: np.ndarray,
        *,
        width: int | None = None,
    ) -> np.ndarray:
        """``_child_priority`` over the children of retained groups only.

        Unlike a singleton group's priority, the relation term here is the
        tensorizer's *global* ``relation_ids`` set: an entity sorts earlier
        when some other entity targets it, not when it has a target itself.
        """

        flat = np.flatnonzero(candidate.ravel())
        relation = np.where(targeted.ravel()[flat], 0, 1)
        hero_building = np.where(rows["is_hero_or_building"].ravel()[flat], 0, 1)
        hp_key = np.minimum(np.maximum(rows["hp"].ravel()[flat], 0.0), 1_048_575.0).astype(np.int64)
        entity_id = np.clip(rows["entity_id"].ravel()[flat], 0, 2**31 - 1)
        key = ((relation.astype(np.int64) * 2 + hero_building) << 52) | ((1_048_576 - hp_key) << 31) | entity_id
        return self._slot_order(candidate, key, width)

    # ---- build -------------------------------------------------------------

    def build(
        self,
        batch: VectorObservationBatch,
        *,
        live_slots: bool = True,
        phases: dict[str, float] | None = None,
    ) -> VectorGroupSection:
        """Build one actor seat's section.

        ``live_slots`` is the producer path: only the lanes' live group/child
        slots are gathered, assembled and returned, so the record carries the
        batch's live width.  ``live_slots=False`` restores the previous
        full-capacity assembly so ``cpu_half_micro.py`` can A/B the two forms in
        one process; that form is also the oracle, and ``pad_group_section``
        projects a live-width record onto it for the digest gate.

        The returned record is bit-identical either way once the live form is
        padded to the fixed capacity — the input to every consumer is the
        record's own mask shape, not the configured maximum.

        Fields come from the compact live-row view (:meth:`_entity_rows`); the
        dense entity axis is only rebuilt for the oracle path.

        ``phases`` is an optional millisecond accumulator for the terms inside
        the build (the tool's phase table); it does not change the record.
        """

        mark = _PhaseMarker(phases)
        axis = self._compact_rows(batch)
        rows = self._seat_rows(self._core_rows(batch, axis), batch, self.perspective, axis)
        if not live_slots:
            rows = self._dense_entity_rows(batch, rows)
        mark("entity_rows")
        lanes = int(rows["valid"].shape[0])
        return self._assemble(
            rows,
            lanes=lanes,
            live_slots=live_slots,
            mark=mark,
            actor_owner=np.full(lanes, int(self.tensorizer.actor_owner), dtype=np.int64),
            entity_slots=int(batch.entity_valid.shape[1]),
        )

    def build_pair(
        self,
        other: VectorGroupBuilder,
        batch: VectorObservationBatch,
        *,
        phases: dict[str, float] | None = None,
    ) -> tuple[VectorGroupSection, VectorGroupSection]:
        """Build both actor seats' sections in one pass over one shared row core.

        The two seats differ only in a row's position (the flip), its velocity
        sign and which owner a group belongs to; every id, hitpoint, table value,
        sort key and rank is the same.  At the producer's training width a
        settled board has a handful of live rows, so this section is dominated
        by per-call overhead: running the assembly once on the two seats'
        stacked rows halves the number of NumPy calls instead of building the
        same section twice.  The returned sections are lane-for-lane
        bit-identical to two :meth:`build` calls (the CPU-half tool asserts it,
        and the digest gate runs the pair path).

        ``self`` supplies the first seat's perspective and the static tables;
        ``other`` must build the same record shape and semantics.  The pair
        path is live-width only — the oracle form stays :meth:`build`.
        """

        self._check_pair(other)
        mark = _PhaseMarker(phases)
        axis = self._compact_rows(batch)
        core = self._core_rows(batch, axis)
        left = self._seat_rows(core, batch, self.perspective, axis)
        right = other._seat_rows(core, batch, other.perspective, axis)
        rows = {name: np.concatenate((values, right[name]), axis=0) for name, values in left.items()}
        mark("entity_rows")

        lanes = int(axis.lanes)
        combined = self._assemble(
            rows,
            lanes=2 * lanes,
            live_slots=True,
            mark=mark,
            actor_owner=np.concatenate(
                (
                    np.full(lanes, int(self.tensorizer.actor_owner), dtype=np.int64),
                    np.full(lanes, int(other.tensorizer.actor_owner), dtype=np.int64),
                )
            ),
            entity_slots=int(batch.entity_valid.shape[1]),
        )
        return self._section_slice(combined, 0, lanes), self._section_slice(combined, lanes, 2 * lanes)

    def _check_pair(self, other: VectorGroupBuilder) -> None:
        """Refuse a pair build whose static semantics or record shape differ."""

        if other is self:
            raise ValueError("a group pair build needs two actor seats")
        key = id(other)
        if key in self._pair_checked:
            return
        if (self.max_groups, self.max_children) != (other.max_groups, other.max_children):
            raise ValueError("group pair build: record capacities differ")
        if self.config != other.config:
            raise ValueError("group pair build: model configs differ")
        if int(self.tables.vocab_size) != int(other.tables.vocab_size):
            raise ValueError("group pair build: entity vocabularies differ")
        for name in self._PAIR_TABLE_FIELDS:
            if not np.array_equal(getattr(self.tables, name), getattr(other.tables, name)):
                raise ValueError(f"group pair build: entity table {name} differs")
        self._pair_checked[key] = other

    @staticmethod
    def _section_slice(section: VectorGroupSection, lo: int, hi: int) -> VectorGroupSection:
        """One seat's rows of a combined section (a view, not a copy)."""

        values: dict[str, torch.Tensor] = {}
        for name in _GROUP_SLOT_FIELDS:
            values[name] = getattr(section.groups, name)[lo:hi]
        for name in _CHILD_SLOT_FIELDS:
            values[name] = getattr(section.groups, name)[lo:hi]
        return VectorGroupSection(
            groups=BattleGroupSetV4(**values),
            group_index=section.group_index[lo:hi],
            child_index=section.child_index[lo:hi],
            group_overflow=section.group_overflow[lo:hi],
            child_overflow=section.child_overflow[lo:hi],
        )

    def _assemble(
        self,
        rows: dict[str, Any],
        *,
        lanes: int,
        live_slots: bool,
        mark: _PhaseMarker,
        actor_owner: np.ndarray,
        entity_slots: int,
    ) -> VectorGroupSection:
        """Gather, assemble and mask every slot of ``rows``.

        ``rows`` is one seat's row view, or both seats' row views stacked on the
        lane axis.  ``actor_owner`` is per lane, so the same body serves the
        pair path.
        """

        valid = rows["valid"]
        entity_count = valid.sum(axis=1).astype(np.int64)
        # The producer sorts only the live slots; the oracle path keeps the
        # order at the record's fixed capacity so the wide assembly is measured.
        group_order = self._orders(rows, width=None if live_slots else self.max_groups)

        retained_groups = np.minimum(entity_count, self.max_groups)
        group_overflow = entity_count - retained_groups

        g = self.max_groups
        c = self.max_children
        # Only the live slots are gathered and assembled.  A settled board
        # carries a handful of groups, so building all 48 group and 160 child
        # slots per lane (and masking the rest) was most of this section's own
        # time.  The oracle form pads with zeros for the feature/position/
        # extent tensors, zero for the ids, and OWNER_NEUTRAL for a group.
        live_g = min(g, max(1, int(entity_count.max()))) if live_slots else g
        # Advanced indexing is the gather: ``take_along_axis`` rebuilds an index
        # tuple on every call (35 times per build), which was a third of this
        # section's own time.  ``order`` holds columns into the row view's axis,
        # so the pair of index arrays is the same operation without it.
        lane_index = np.arange(lanes, dtype=np.int64)[:, None]
        mark("orders")

        def gather(values: np.ndarray, order: np.ndarray, width: int) -> np.ndarray:
            return values[lane_index, order[:, :width]]

        # Group rows keep the first ``retained_groups`` entries of the group
        # order; everything after is padded with zeros and masked off.
        group_mask = np.arange(live_g, dtype=np.int64)[None, :] < retained_groups[:, None]
        g_hp = gather(rows["hp"], group_order, live_g)
        g_max_hp = gather(rows["max_hp"], group_order, live_g)
        g_shield = gather(rows["shield"], group_order, live_g)
        g_x = gather(rows["x"], group_order, live_g)
        g_y = gather(rows["y"], group_order, live_g)
        g_extent = np.stack(
            [
                gather(rows["extent_x0"], group_order, live_g),
                gather(rows["extent_y0"], group_order, live_g),
                gather(rows["extent_x1"], group_order, live_g),
                gather(rows["extent_y1"], group_order, live_g),
            ],
            axis=-1,
        )
        g_velocity = np.stack(
            [
                gather(rows["velocity"][..., 0], group_order, live_g),
                gather(rows["velocity"][..., 1], group_order, live_g),
            ],
            axis=-1,
        )
        g_velocity_valid = gather(rows["velocity_valid"], group_order, live_g)
        g_target = gather(rows["target"], group_order, live_g)
        mark("group_gathers")

        # Group rank per entity: the retained portion of the group order.
        # Scatter only the kept slots: padded slots repeat entity row 0, and
        # an unguarded scatter would overwrite row 0's real rank with -1.
        group_rank = np.full(valid.shape, -1, dtype=np.int32)
        retained_slots = group_order[:, :live_g]
        retained_rank = np.broadcast_to(np.arange(live_g, dtype=np.int64)[None, :], retained_slots.shape)
        lane_rows = np.broadcast_to(np.arange(lanes, dtype=np.int64)[:, None], retained_slots.shape)
        group_rank[lane_rows[group_mask], retained_slots[group_mask]] = retained_rank[group_mask]

        # Children are drawn only from retained groups, in child priority
        # order (the tensorizer filters ``child_candidates`` by
        # ``group_index_by_entity``).
        candidate = group_rank >= 0
        candidate_count = candidate.sum(axis=1).astype(np.int64)
        mark("group_rank")
        targeted = self._targeted_mask(rows)
        mark("targeted_mask")
        child_order = self._child_order(rows, candidate, targeted, width=None if live_slots else c)
        live_c = min(c, max(1, int(candidate_count.max()))) if live_slots else c
        child_entity = child_order[:, :live_c]
        child_candidate = np.take_along_axis(candidate, child_order, axis=1)[:, :live_c]
        child_count = np.minimum(candidate_count, self.max_children)
        child_mask = np.arange(live_c, dtype=np.int64)[None, :] < child_count[:, None]
        child_mask &= child_candidate
        child_overflow = candidate_count - child_count

        child_rank = np.full(valid.shape, -1, dtype=np.int32)
        child_rank_rows = np.broadcast_to(np.arange(lanes, dtype=np.int64)[:, None], child_entity.shape)
        child_rank_values = np.broadcast_to(np.arange(live_c, dtype=np.int64)[None, :], child_entity.shape)
        child_rank[child_rank_rows[child_mask], child_entity[child_mask]] = child_rank_values[child_mask]
        mark("child_order_rank")

        # ---- group tensors -------------------------------------------------
        group_features = np.zeros((lanes, live_g, len(GROUP_FEATURE_NAMES)), dtype=np.float32)
        group_position = np.empty((lanes, live_g, 2), dtype=np.float32)
        group_extent = np.empty((lanes, live_g, 4), dtype=np.float32)
        group_mask_f = group_mask.astype(bool)

        # ``_finite(hitpoints, max_hitpoints or 1)``: a present hitpoint value
        # over a missing maximum divides by 1, exactly as the Python does.
        ratio = g_hp / np.where(g_max_hp > 0, g_max_hp, 1.0)
        retained_ids = np.zeros(valid.shape, dtype=bool)
        retained_ids[child_rank_rows[child_mask], child_entity[child_mask]] = True
        g_retained = gather(retained_ids, group_order, live_g)

        group_features[..., _G_MEMBERS] = 1.0 / 16.0
        group_features[..., _G_DROPPED] = np.where(g_retained, 0.0, 1.0 / 16.0)
        group_features[..., _G_HP] = g_hp / 10_000.0
        group_features[..., _G_SHIELD] = g_shield / 10_000.0
        group_features[..., _G_RATIO_MEAN] = ratio
        group_features[..., _G_RATIO_MIN] = ratio
        group_features[..., _G_RATIO_MAX] = ratio
        group_features[..., _G_SPAN_X] = g_extent[..., 2] - g_extent[..., 0]
        group_features[..., _G_SPAN_Y] = g_extent[..., 3] - g_extent[..., 1]
        group_features[..., _G_ABILITY] = 0.0
        group_features[..., _G_TARGET] = g_target.astype(np.float32)
        # The stock accumulator starts at ``0.0`` and adds each member's model
        # velocity, so a singleton group whose velocity is ``-0.0`` lands on
        # ``+0.0`` (``0.0 + -0.0 == 0.0``).  Reproduce that signed-zero rule;
        # the bytes are part of the digest, even though ``torch.equal`` agrees.
        velocity_x = np.add(0.0, g_velocity[..., 0])
        velocity_y = np.add(0.0, g_velocity[..., 1])
        group_features[..., _G_VELOCITY_X] = np.where(g_velocity_valid, velocity_x / 10.0, 0.0)
        group_features[..., _G_VELOCITY_Y] = np.where(g_velocity_valid, velocity_y / 10.0, 0.0)
        group_features[..., _G_SPEED] = np.where(
            g_velocity_valid, np.hypot(velocity_x, velocity_y) / 10.0, 0.0
        )
        group_position[..., 0] = g_x
        group_position[..., 1] = g_y
        group_extent[...] = g_extent

        g_side = gather(rows["side"], group_order, live_g)
        owner_type = np.where(g_side == actor_owner[:, None], OWNER_SELF, OWNER_ENEMY)
        group_owner = np.where((g_side == 0) | (g_side == 1), owner_type, OWNER_NEUTRAL)
        source_card = gather(rows["card_vocab_id"], group_order, live_g)
        if np.any((source_card == UNKNOWN_CARD_VOCAB_ID) & group_mask):
            bad = int(
                gather(rows["card_id"], group_order, live_g)[(source_card == UNKNOWN_CARD_VOCAB_ID) & group_mask][0]
            )
            raise ValueError(f"public causal-group source card {bad} is outside this V4 catalog")
        group_type = gather(rows["group_type"], group_order, live_g)
        runtime_form = gather(rows["runtime_form"], group_order, live_g)
        mark("group_features")

        # ---- child tensors -------------------------------------------------
        child_features = np.zeros((lanes, live_c, len(CHILD_FEATURE_NAMES)), dtype=np.float32)
        child_position = np.empty((lanes, live_c, 2), dtype=np.float32)
        child_extent = np.empty((lanes, live_c, 4), dtype=np.float32)
        child_radius = np.empty((lanes, live_c), dtype=np.float32)
        child_archetype = np.empty((lanes, live_c), dtype=np.int64)
        child_group = np.empty((lanes, live_c), dtype=np.int64)
        child_type = np.empty((lanes, live_c), dtype=np.int64)

        c_hp = gather(rows["hp"], child_order, live_c)
        c_max_hp = gather(rows["max_hp"], child_order, live_c)
        c_shield = gather(rows["shield"], child_order, live_c)
        c_age = gather(rows["age_ms"], child_order, live_c)
        c_target = gather(rows["target"], child_order, live_c)
        c_x = gather(rows["x"], child_order, live_c)
        c_y = gather(rows["y"], child_order, live_c)
        c_extent = np.stack(
            [
                gather(rows["extent_x0"], child_order, live_c),
                gather(rows["extent_y0"], child_order, live_c),
                gather(rows["extent_x1"], child_order, live_c),
                gather(rows["extent_y1"], child_order, live_c),
            ],
            axis=-1,
        )
        c_velocity = np.stack(
            [
                gather(rows["velocity"][..., 0], child_order, live_c),
                gather(rows["velocity"][..., 1], child_order, live_c),
            ],
            axis=-1,
        )
        c_velocity_valid = gather(rows["velocity_valid"], child_order, live_c)

        child_features[..., _C_HP_RATIO] = c_hp / np.where(c_max_hp > 0, c_max_hp, 1.0)
        child_features[..., _C_HP] = c_hp / 10_000.0
        child_features[..., _C_MAX_HP] = c_max_hp / 10_000.0
        child_features[..., _C_SHIELD] = c_shield / 10_000.0
        child_features[..., _C_AGE] = c_age / 60_000.0
        child_features[..., _C_TARGET] = c_target.astype(np.float32)
        child_features[..., _C_ABILITY] = 0.0
        child_features[..., _C_X] = c_x / float(self.config.board_width)
        child_features[..., _C_Y] = c_y / float(self.config.board_height)
        child_features[..., _C_VELOCITY_X] = np.where(c_velocity_valid, c_velocity[..., 0] / 10.0, 0.0)
        child_features[..., _C_VELOCITY_Y] = np.where(c_velocity_valid, c_velocity[..., 1] / 10.0, 0.0)
        child_position[..., 0] = c_x
        child_position[..., 1] = c_y
        child_extent[...] = c_extent
        child_radius[...] = gather(rows["radius"], child_order, live_c)
        child_archetype[...] = gather(rows["archetype_id"], child_order, live_c)
        child_type[...] = gather(rows["child_type"], child_order, live_c)
        child_group[...] = group_rank[lane_index, child_entity]
        mark("child_features")

        # The arena leaves a masked row at zero; the gathers above can carry a
        # padded slot's row-0 values into a masked one, so zero it explicitly.
        # ``np.copyto(where=...)`` measured slower than the fancy assignment
        # here (0.18 against 0.10 ms over the 14 fields), so keep the assignment.
        group_features[~group_mask_f] = 0.0
        group_position[~group_mask_f] = 0.0
        group_extent[~group_mask_f] = 0.0
        source_card[~group_mask] = 0
        # ``_groups`` fills owner_type with OWNER_NEUTRAL before iterating, so a
        # masked row is neutral (2), not zero.
        group_owner[~group_mask] = OWNER_NEUTRAL
        group_type[~group_mask] = 0
        runtime_form[~group_mask] = 0

        child_features[~child_mask] = 0.0
        child_position[~child_mask] = 0.0
        child_extent[~child_mask] = 0.0
        child_radius[~child_mask] = 0.0
        child_archetype[~child_mask] = 0
        child_type[~child_mask] = 0
        child_group[~child_mask] = 0
        mark("masked_zeroing")

        def pad_slots(values: np.ndarray, width: int, fill: Any = 0) -> np.ndarray:
            """Grow a live-width slot axis to the record's fixed capacity.

            Only the ``live_slots=False`` oracle pads.  The producer keeps the
            lane's live width: every consumer reads the slot count from the
            record's own ``mask``/``child_mask`` shape, and a padded tail is a
            fresh zeroed allocation nothing reads.  At a settled board that pad
            was 0.3–0.6 ms of the 1.3–1.5 ms build (a 7.5 MB zeroed copy per
            seat; session 43).  A caller that needs the fixed capacity — the
            digest gate against the per-lane record, or storage that stacks
            decisions of different widths — pads with
            :func:`pad_group_section`.
            """

            if live_slots or int(values.shape[1]) == width:
                return values
            output = np.full((values.shape[0], width) + values.shape[2:], fill, dtype=values.dtype)
            output[:, : int(values.shape[1])] = values
            return output

        groups = BattleGroupSetV4(
            source_card_vocab_id=torch.from_numpy(pad_slots(source_card, g)),
            group_type=torch.from_numpy(pad_slots(group_type, g)),
            owner_type=torch.from_numpy(pad_slots(group_owner, g, OWNER_NEUTRAL)),
            runtime_form=torch.from_numpy(pad_slots(runtime_form, g)),
            features=torch.from_numpy(pad_slots(group_features, g)),
            position=torch.from_numpy(pad_slots(group_position, g)),
            extent=torch.from_numpy(pad_slots(group_extent, g)),
            mask=torch.from_numpy(pad_slots(group_mask_f, g, False)),
            child_archetype_id=torch.from_numpy(pad_slots(child_archetype, c)),
            child_group_index=torch.from_numpy(pad_slots(child_group, c)),
            child_type=torch.from_numpy(pad_slots(child_type, c)),
            child_features=torch.from_numpy(pad_slots(child_features, c)),
            child_position=torch.from_numpy(pad_slots(child_position, c)),
            child_extent=torch.from_numpy(pad_slots(child_extent, c)),
            child_radius=torch.from_numpy(pad_slots(child_radius, c)),
            child_mask=torch.from_numpy(pad_slots(child_mask, c, False)),
        )
        mark("record")
        if live_slots:
            # The producer's ranks are compact columns; the section's index
            # arrays are per entity slot, so scatter them once through the
            # compact→slot map.  Padded columns are excluded by ``valid``.
            dense_shape = (lanes, int(entity_slots))
            entity_lane = np.broadcast_to(np.arange(lanes, dtype=np.int64)[:, None], valid.shape)
            group_index = np.full(dense_shape, -1, dtype=np.int32)
            child_index = np.full(dense_shape, -1, dtype=np.int32)
            group_index[entity_lane[valid], rows["slots"][valid]] = group_rank[valid]
            child_index[entity_lane[valid], rows["slots"][valid]] = child_rank[valid]
        else:
            group_index = group_rank
            child_index = child_rank
        mark("index_cast")
        return VectorGroupSection(
            groups=groups,
            group_index=group_index,
            child_index=child_index,
            group_overflow=group_overflow,
            child_overflow=child_overflow,
        )


class VectorCardBuilder:
    """Fill the own and opponent card sets for a whole vector batch in one pass.

    The bridge envelope this reproduces is deliberately narrow:

    * the actor's deck layout, roles and vocabulary come from the tensorizer's
      own cached layout, so the rows cannot drift from the per-lane path;
    * a card's runtime form is its hand form when it is in hand and the normal
      form otherwise — crforge carries no evolution runtime, so the tensorizer
      never sees a ready evolution outside the hand;
    * ``revealed_cards`` stays empty: the bridge receives no combat events, so
      the opponent set is eight zero rows plus the unknown-card row with the
      ``unknown_opponent_card_fraction`` term, exactly as the per-lane builder
      emits it from an empty reveal set;
    * the actor's own ability runtime is empty for the same reason.

    The 16.635 21-column schema adds the actor's in-flight command: a pending
    play sets ``pending_execution``, the target cell and (unless the checkpoint
    neutralises it) the remaining ticks.  The pending row's point is in the
    seat's frame, so the model cell is recovered with the same mirror the
    native decoder applies.
    """

    def __init__(self, tensorizer: Any) -> None:
        self.tensorizer = tensorizer
        self.config = tensorizer.config
        self.seat = _actor_seat(tensorizer)
        self.perspective = tensorizer.perspective
        if tensorizer.tracker is not None:
            raise ValueError(
                "the batched card build needs the bridge's no-event envelope; a tracker would invent reveals"
            )
        own_order, card_to_row, own_roles, own_vocab = tensorizer._actor_card_layout()
        self.own_order = tuple(int(card_id) for card_id in own_order)
        self.card_to_row = dict(card_to_row)
        self.own_roles = [tuple(bool(bit) for bit in role) for role in own_roles]
        self.own_vocab = tuple(int(value) for value in own_vocab)
        self.own_count = int(self.config.max_own_cards)
        self.opponent_count = int(self.config.max_opponent_cards)
        self.feature_dim = int(self.config.card_runtime_feature_dim)
        self._subcell_offsets = self._card_subcell_offsets(tensorizer)
        if self.own_count != len(self.own_order):
            raise ValueError(f"actor deck layout has {len(self.own_order)} rows, expected {self.own_count}")
        if self.feature_dim not in {len(CARD_RUNTIME_FEATURE_NAMES), len(CARD_RUNTIME_FEATURE_NAMES_V2)}:
            raise ValueError(f"card runtime schema of {self.feature_dim} columns is not a V4 profile")
        if self.opponent_count != 9:
            raise ValueError("the opponent card set is eight cards plus the unknown row")

    @staticmethod
    def _card_subcell_offsets(tensorizer: Any) -> dict[int, tuple[float, float]]:
        """Policy-cell offset from the cell center, per deploying card.

        ``NativeBuildingFootprintV1.model_subcell_offset`` puts an even-sized
        footprint half a tile past the cell center, so the native command point
        for a 2×2 building is one cell over from the cell it names.  Recovering
        the model cell from a pending point therefore needs the same per-card
        offset; non-buildings are centered and use ``(0, 0)``.
        """

        offsets: dict[int, tuple[float, float]] = {}
        for card_id, spec in tensorizer.card_specs.items():
            footprint = native_building_footprint(spec)
            if footprint is not None:
                offsets[int(card_id)] = (
                    0.5 if footprint.width_tiles % 2 == 0 else 0.0,
                    0.5 if footprint.height_tiles % 2 == 0 else 0.0,
                )
        return offsets

    def build(self, batch: VectorObservationBatch) -> VectorCardSection:
        lanes = int(batch.count)
        seat = self.seat
        hand_ids = batch.hand_card_id[:, seat, :]
        hand_present = batch.hand_vocab[:, seat, :] >= 0
        next_card = batch.next_card_id[:, seat]

        own_ids = np.asarray(self.own_order, dtype=np.int32)
        own_vocab = np.asarray(self.own_vocab, dtype=np.int64)
        matches = (hand_ids[:, None, :] == own_ids[None, :, None]) & hand_present[:, None, :]
        in_hand = matches.any(axis=2)
        slot = matches.argmax(axis=2)
        hand_form = np.take_along_axis(batch.hand_form[:, seat, :], slot, axis=1)
        runtime_form = np.where(in_hand, hand_form, 0).astype(np.int64)

        features = np.zeros((lanes, self.own_count, self.feature_dim), dtype=np.float32)
        features[..., _CD_IN_HAND] = in_hand.astype(np.float32)
        features[..., _CD_NEXT] = (next_card[:, None] == own_ids[None, :]).astype(np.float32)
        if self.feature_dim == len(CARD_RUNTIME_FEATURE_NAMES_V2):
            self._pending_features(batch, features, own_ids, seat=seat)

        own = CardSetV4(
            card_vocab_id=torch.from_numpy(np.broadcast_to(own_vocab[None, :], (lanes, self.own_count)).copy()),
            runtime_form=torch.from_numpy(runtime_form),
            role_bits=torch.from_numpy(
                np.broadcast_to(np.asarray(self.own_roles, dtype=bool)[None, :, :], (lanes, self.own_count, 2)).copy()
            ),
            runtime_features=torch.from_numpy(features),
            mask=torch.ones((lanes, self.own_count), dtype=torch.bool),
        )

        # Eight zero rows (not revealed) followed by the unknown-card row.  The
        # per-lane builder sorts the reveal set by vocabulary id; with no
        # reveal set every slot is a zero placeholder and the unknown row is
        # the only masked-in row.
        opponent_vocab = np.zeros((lanes, self.opponent_count), dtype=np.int64)
        opponent_vocab[:, -1] = UNKNOWN_CARD_VOCAB_ID
        opponent_mask = np.zeros((lanes, self.opponent_count), dtype=bool)
        opponent_mask[:, -1] = True
        opponent_features = np.zeros((lanes, self.opponent_count, self.feature_dim), dtype=np.float32)
        opponent_features[:, -1, _CD_UNKNOWN_FRACTION] = 1.0
        opponent = CardSetV4(
            card_vocab_id=torch.from_numpy(opponent_vocab),
            runtime_form=torch.zeros((lanes, self.opponent_count), dtype=torch.long),
            role_bits=torch.zeros((lanes, self.opponent_count, 2), dtype=torch.bool),
            runtime_features=torch.from_numpy(opponent_features),
            mask=torch.from_numpy(opponent_mask),
        )
        return VectorCardSection(own=own, opponent=opponent, card_to_row=dict(self.card_to_row))

    def _pending_features(
        self, batch: VectorObservationBatch, features: np.ndarray, own_ids: np.ndarray, *, seat: int
    ) -> None:
        """The actor's in-flight command for each own card token.

        ``pending_x`` / ``pending_y`` are game coordinates (the fact layer
        converts each seat's frame once).  The native command point carries the
        owner-signed building subcell offset, so the native cell is
        ``(point - sign * offset) // tile``; the model cell is that native cell
        through the actor perspective (``flip_x`` / ``flip_y``), exactly as the
        decoder applies it in reverse.  A waiting row whose card is not one of
        the actor's deck cards is an ability command; crforge keeps those in the
        champion-slot block, and :meth:`_ability_pending_features` covers them.
        """

        if not batch.pending_rows:
            return
        tile_x = float(WORLD_WIDTH) / float(self.config.board_width)
        tile_y = float(WORLD_HEIGHT) / float(self.config.board_height)
        card_ids = batch.pending_card_id[:, seat, :]
        live = card_ids > 0
        offset_x, offset_y = self._pending_offsets(card_ids)
        # ``card_placement_mask`` moves a building's native anchor by
        # ``model_subcell_offset * (+1 owner 0 / -1 owner 1)``.
        sign = 1.0 if int(seat) == 0 else -1.0
        native_x = batch.pending_x[:, seat, :].astype(np.float64) / tile_x - offset_x * sign
        native_y = batch.pending_y[:, seat, :].astype(np.float64) / tile_y - offset_y * sign
        native_cell_x = np.clip(np.floor(native_x).astype(np.int64), 0, GRID_WIDTH - 1)
        native_cell_y = np.clip(np.floor(native_y).astype(np.int64), 0, GRID_HEIGHT - 1)
        cell_x = GRID_WIDTH - 1 - native_cell_x if self.perspective.flip_x else native_cell_x
        cell_y = GRID_HEIGHT - 1 - native_cell_y if self.perspective.flip_y else native_cell_y
        ticks = batch.pending_ticks[:, seat, :].astype(np.float64)
        normalized_x = cell_x.astype(np.float32) / float(max(1, self.config.board_width - 1))
        normalized_y = cell_y.astype(np.float32) / float(max(1, self.config.board_height - 1))
        for row, card_id in enumerate(own_ids):
            match = live & (card_ids == card_id)
            has_pending = match.any(axis=1)
            if not has_pending.any():
                continue
            take = match.argmax(axis=1)
            features[:, row, _CD_PENDING] = has_pending.astype(np.float32)
            remaining = np.take_along_axis(ticks, take[:, None], axis=1)[:, 0]
            features[:, row, _CD_PENDING_TICKS] = np.where(
                has_pending, remaining / float(LIVE_COMMAND_AGE_TICKS), 0.0
            ).astype(np.float32)
            features[:, row, _CD_PENDING_X] = np.where(
                has_pending, np.take_along_axis(normalized_x, take[:, None], axis=1)[:, 0], 0.0
            )
            features[:, row, _CD_PENDING_Y] = np.where(
                has_pending, np.take_along_axis(normalized_y, take[:, None], axis=1)[:, 0], 0.0
            )
            if self.config.neutralize_pending_ticks_remaining:
                # The real receipt cannot supply this column live, so every
                # checkpoint that declares the 21-column schema neutralises it.
                features[:, row, _CD_PENDING_TICKS] = 0.0
        self._ability_pending_features(batch, features, own_ids, seat=seat)

    def _pending_offsets(self, card_ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Per-pending-row subcell offset (0 for cards without a footprint)."""

        if not self._subcell_offsets:
            zeros = np.zeros(card_ids.shape, dtype=np.float64)
            return zeros, zeros
        keys = np.fromiter(sorted(self._subcell_offsets), dtype=np.int64)
        table = np.asarray([self._subcell_offsets[int(key)] for key in keys], dtype=np.float64)
        positions = np.searchsorted(keys, card_ids)
        clipped = np.clip(positions, 0, keys.size - 1)
        found = (positions < keys.size) & (keys[clipped] == card_ids)
        offset_x = np.where(found, table[clipped, 0], 0.0)
        offset_y = np.where(found, table[clipped, 1], 0.0)
        return offset_x, offset_y

    def _ability_pending_features(
        self, batch: VectorObservationBatch, features: np.ndarray, own_ids: np.ndarray, *, seat: int
    ) -> None:
        """A waiting champion ability sets ``ability_pending`` on its card row.

        The engine keeps ability commands in their own header block (not the
        pending-card rows); a non-zero ``ability_command_ticks`` is a command
        that has been given and has not run.  The per-lane builder matches it by
        ``ability_id`` against the card's static ability set, so the card token
        is the champion's.
        """

        if batch.ability_slots <= 0:
            return
        champion = batch.ability_champion_id[:, seat, :]
        waiting = (champion > 0) & (batch.ability_ticks[:, seat, :] > 0)
        ticks = batch.ability_ticks[:, seat, :].astype(np.float64)
        for row, card_id in enumerate(own_ids):
            match = waiting & (champion == card_id)
            has_pending = match.any(axis=1)
            if not has_pending.any():
                continue
            take = match.argmax(axis=1)
            features[:, row, _CD_PENDING_ABILITY] = has_pending.astype(np.float32)
            if not self.config.neutralize_pending_ticks_remaining:
                remaining = np.take_along_axis(ticks, take[:, None], axis=1)[:, 0]
                features[:, row, _CD_PENDING_TICKS] = np.where(
                    has_pending, remaining / float(LIVE_COMMAND_AGE_TICKS), 0.0
                ).astype(np.float32)


@dataclass(frozen=True)
class VectorTowerSection:
    """The batched ``_towers`` output for every lane.

    ``towers`` carries the fixed six-row tower set with the lane count as the
    batch dimension and ``tower_index`` maps a native tower id to its model row
    (``-1`` when the row is absent), which is the ``index_by_entity`` dict the
    tensorizer's ``_relations`` consumes as ``tower_token_by_entity``.
    """

    towers: TowerSetV4
    tower_index: np.ndarray

    def lane(self, lane: int) -> tuple[TowerSetV4, dict[int, int]]:
        index = int(lane)
        rows = self.tower_index[index]
        return (
            TowerSetV4(
                tower_type=self.towers.tower_type[index : index + 1].clone(),
                tower_troop_type=self.towers.tower_troop_type[index : index + 1].clone(),
                owner_type=self.towers.owner_type[index : index + 1].clone(),
                features=self.towers.features[index : index + 1].clone(),
                position=self.towers.position[index : index + 1].clone(),
                extent=self.towers.extent[index : index + 1].clone(),
                mask=self.towers.mask[index : index + 1].clone(),
            ),
            {
                entity_id: int(rows[entity_id]) for entity_id in np.flatnonzero(rows >= 0)
            },
        )


class VectorTowerBuilder:
    """Fill the six-row tower section for a whole vector batch in one pass.

    ``_towers`` emits one row per tower, sorted actor-first and then by model
    kind (king, princess left, princess right) and native id, and writes the
    health/active/target columns plus the tile position and kind-sized extent.
    The vector facts layer already carries every one of those facts per tower id,
    so the batched build is one gather over a per-lane six-row view.

    Two envelope facts are pinned here rather than derived:

    * ``status`` is always empty because tower-activation events are not
      supplied, so ``king_activated`` is zero.  A destroyed tower also never
      reports "activated"; the per-lane path has the same gap.
    * ``tower_troop_id`` is not in the vector frame.  Every player in a match
      fields the same tower troop, so it is a static build input (the learner
      deck's ``159000000`` = Tower Princess) and recorded as a fidelity pin on
      the gate artifact.  ``None`` means "no tower troop", which is the shape the
      bridge emits when the match config carries none.

    The attack-state and tower-troop-runtime feature blocks stay zero for the
    same reason the per-lane envelope leaves them at zero: the bridge supplies
    neither a tower attack state nor a troop runtime.
    """

    def __init__(self, tensorizer: Any, *, tower_troop_id: int | None = None) -> None:
        # Imported lazily so loading this module stays as light as the rest of
        # the row build; the vocabularies are the tensorizer's own.
        from native_runner.training.v4.tensorizer import TOWER_TROOP_TYPE, TOWER_TYPE

        self.tensorizer = tensorizer
        self.config = tensorizer.config
        self.actor = _actor_seat(tensorizer)
        self.max_towers = int(self.config.max_towers)
        if self.max_towers != 6:
            raise ValueError(f"the batched tower build assumes six tower rows, config has {self.max_towers}")
        if int(self.config.tower_feature_dim) != len(TOWER_FEATURE_NAMES):
            raise ValueError(
                f"tower feature schema of {int(self.config.tower_feature_dim)} columns is not a V4 profile"
            )
        self.flip_x = bool(tensorizer.perspective.flip_x)
        self.flip_y = bool(tensorizer.perspective.flip_y)
        self.perspective = tensorizer.perspective
        self.tower_type = dict(TOWER_TYPE)
        self.tower_troop_type = dict(TOWER_TROOP_TYPE)
        # ``_model_tower_kind`` swaps the princesses under a horizontal flip, so
        # both the sort order and the emitted type code follow the model view.
        self.model_kind = np.asarray([0, 2, 1] if self.flip_x else [0, 1, 2], dtype=np.int64)
        troop = 0
        if tower_troop_id is not None:
            key = int(tower_troop_id)
            if key not in self.tower_troop_type:
                raise ValueError(f"tower troop {key} is not a competitive Tower Troop")
            troop = int(self.tower_troop_type[key])
        self.tower_troop = troop

    def build(self, batch: VectorObservationBatch) -> VectorTowerSection:
        lanes = int(batch.count)
        g = self.max_towers
        slots = VectorRelationBuilder._tower_slots(batch)
        present = slots >= 0
        safe_slots = np.maximum(slots, 0)
        tower_ids = np.broadcast_to(np.arange(g, dtype=np.int64)[None, :], present.shape)

        # The oracle reads a tower's world point from the shared entity row, not
        # from the tower block, so use the same source.
        world_x = np.take_along_axis(batch.entity_x, safe_slots, axis=1).astype(np.float64)
        world_y = np.take_along_axis(batch.entity_y, safe_slots, axis=1).astype(np.float64)
        target = np.take_along_axis(batch.entity_target, safe_slots, axis=1).astype(np.int64)

        # ``_towers`` order: actor first, then model kind, then native id.
        model_type = self.model_kind[np.clip(batch.tower_kind, 0, 2)]
        key = (((batch.tower_owner != self.actor).astype(np.int64) * 8) + model_type) * 8 + tower_ids
        key = np.where(present, key, np.iinfo(np.int64).max)
        order = np.argsort(key, axis=1, kind="stable")

        def gather(values: np.ndarray) -> np.ndarray:
            return np.take_along_axis(values, order[:, :g], axis=1)

        row_owner = gather(batch.tower_owner.astype(np.int64))
        row_type = gather(model_type)
        row_ids = gather(tower_ids)
        row_hp = gather(batch.tower_hp.astype(np.float64))
        row_max_hp = gather(batch.tower_max_hp.astype(np.float64))
        row_target = gather(target)
        row_present = gather(present)
        tile_x, tile_y = _tile_positions(
            gather(world_x), gather(world_y), config=self.config, perspective=self.perspective
        )

        half_extent = np.where(row_type == self.tower_type["king"], 2.0, 1.5)
        position = np.stack([tile_x, tile_y], axis=-1)
        extent = np.stack(
            [tile_x - half_extent, tile_y - half_extent, tile_x + half_extent, tile_y + half_extent],
            axis=-1,
        )

        features = np.zeros((lanes, g, len(TOWER_FEATURE_NAMES)), dtype=np.float32)
        # A missing maximum divides by one, exactly as ``_finite`` does; the row
        # is masked below anyway, and a live tower always carries its maximum.
        features[..., _T_HP_RATIO] = row_hp / np.where(row_max_hp > 0, row_max_hp, 1.0)
        features[..., _T_SHIELD_RATIO] = 0.0
        features[..., _T_ACTIVE] = (row_present & (row_hp > 0)).astype(np.float32)
        features[..., _T_TARGET] = (row_target >= 0).astype(np.float32)
        features[..., _T_KING_ACTIVATED] = 0.0
        features[..., _T_HP] = row_hp / 5000.0
        features[..., _T_MAX_HP] = row_max_hp / 5000.0

        owner_type = np.where(row_owner == self.actor, OWNER_SELF, OWNER_ENEMY).astype(np.int64)
        troop_type = np.full((lanes, g), self.tower_troop, dtype=np.int64)

        # The gathers above can carry a padded slot's row-0 values into a masked
        # row, so reset every masked row to the oracle's padding.
        features[~row_present] = 0.0
        position[~row_present] = 0.0
        extent[~row_present] = 0.0
        owner_type[~row_present] = OWNER_NEUTRAL
        row_type[~row_present] = 0
        troop_type[~row_present] = 0

        tower_index = np.full((lanes, g), -1, dtype=np.int64)
        lane_rows = np.broadcast_to(np.arange(lanes, dtype=np.int64)[:, None], row_present.shape)
        row_numbers = np.broadcast_to(np.arange(g, dtype=np.int64)[None, :], row_present.shape)
        tower_index[lane_rows[row_present], row_ids[row_present]] = row_numbers[row_present]

        towers = TowerSetV4(
            tower_type=torch.from_numpy(row_type),
            tower_troop_type=torch.from_numpy(troop_type),
            owner_type=torch.from_numpy(owner_type),
            features=torch.from_numpy(features),
            position=torch.from_numpy(position.astype(np.float32)),
            extent=torch.from_numpy(extent.astype(np.float32)),
            mask=torch.from_numpy(row_present.astype(bool)),
        )
        return VectorTowerSection(towers=towers, tower_index=tower_index)


@dataclass(frozen=True)
class VectorSpatialPlaneSection:
    """The batched ``_spatial_planes`` output for every lane."""

    planes: torch.Tensor

    def lane(self, lane: int) -> torch.Tensor:
        index = int(lane)
        return self.planes[index : index + 1].clone()


class VectorSpatialPlaneBuilder:
    """Fill the explicit spatial planes for a whole vector batch in one pass.

    ``_spatial_planes`` writes a 14-channel board per decision: for each side,
    four occupancy categories (ground, airborne, building, projectile), a
    hitpoint channel, a shield channel and a threat channel.  Every entity lands
    its category at its tile cell — a building fills its whole footprint — while
    hitpoints, shield and threat always go to the entity's own cell.

    The batch already carries the facts: position, side, hitpoints and shield per
    row, and the archetype metadata behind the category (child kind, airborne)
    and the threat value (static damage).  ``VectorEntityTables`` compiles the
    last group once.  Two rules decide membership: a tower is not an entity for
    this section, and an ownerless row (neutral) is skipped, exactly as the
    per-lane loop skips ``owner is None``.  Persistent area roots are skipped too,
    but the bridge's entity envelope carries none (it emits characters and
    towers only); the table still encodes the rule.
    """

    def __init__(self, tensorizer: Any, tables: VectorEntityTables) -> None:
        self.tensorizer = tensorizer
        self.tables = tables
        self.config = tensorizer.config
        self.perspective = tensorizer.perspective
        self.actor = _actor_seat(tensorizer)
        self.channels = int(self.config.explicit_spatial_channels)
        if self.channels != 14:
            raise ValueError(f"the spatial plane build assumes 14 channels, config has {self.channels}")
        self.enemy_base = self.channels // 2
        self.board_w = int(self.config.board_width)
        self.board_h = int(self.config.board_height)

    def build(self, batch: VectorObservationBatch) -> VectorSpatialPlaneSection:
        lanes = int(batch.count)
        planes = np.zeros((lanes, self.channels, self.board_h, self.board_w), dtype=np.float32)
        # Flatten once and work on the kept entities only: the (lanes, entities)
        # grid is ~15k cells, while a real board carries a few hundred entities.
        row_width = batch.entity_valid.shape[1]
        side_all = batch.entity_side
        rv_all = np.clip(batch.entity_row_vocab, 0, self.tables.vocab_size - 1)
        keep = (
            batch.entity_valid
            & ~batch.entity_is_tower
            & ((side_all == 0) | (side_all == 1))
            & ~self.tables.is_persistent_area_root[rv_all]
        ).ravel()
        index = np.flatnonzero(keep)
        if index.size == 0:
            return VectorSpatialPlaneSection(planes=torch.from_numpy(planes))

        lane_of = index // row_width
        rv = rv_all.ravel()[index]
        side = side_all.ravel()[index]
        x, y = _tile_positions(
            batch.entity_x.ravel()[index].astype(np.float64),
            batch.entity_y.ravel()[index].astype(np.float64),
            config=self.config,
            perspective=self.perspective,
        )
        cell_x = np.clip(x.astype(np.int64), 0, self.board_w - 1)
        cell_y = np.clip(y.astype(np.int64), 0, self.board_h - 1)
        base = np.where(side == self.actor, 0, self.enemy_base)
        child = self.tables.child_type[rv]
        building = child == CHILD_TYPE["building"]
        projectile = child == CHILD_TYPE["projectile"]
        airborne = self.tables.is_airborne[rv]
        category = np.where(projectile, 3, np.where(building, 2, np.where(airborne, 1, 0)))

        # A building fills the policy cells whose centres lie inside its exact
        # footprint; everything else (and a building with no mapped cell) marks
        # its own cell.
        half_w = self.tables.footprint_half_w[rv]
        half_h = self.tables.footprint_half_h[rv]
        start_x = np.maximum(0, np.ceil(x - half_w - 0.5)).astype(np.int64)
        stop_x = np.minimum(self.board_w, np.ceil(x + half_w - 0.5)).astype(np.int64)
        start_y = np.maximum(0, np.ceil(y - half_h - 0.5)).astype(np.int64)
        stop_y = np.minimum(self.board_h, np.ceil(y + half_h - 0.5)).astype(np.int64)
        count_x = np.maximum(0, stop_x - start_x)
        count_y = np.maximum(0, stop_y - start_y)
        footprint = building & (count_x > 0) & (count_y > 0)
        owner_plane = lane_of * self.channels + base

        flat = planes.reshape(lanes * self.channels, self.board_h * self.board_w)
        point = ~footprint
        point_rows = (owner_plane + category)[point]
        point_cols = (cell_y * self.board_w + cell_x)[point]
        if point_rows.size:
            np.add.at(flat, (point_rows, point_cols), 1.0)

        if footprint.any():
            spans_x = int(count_x.max())
            spans_y = int(count_y.max())
            for dx in range(spans_x):
                for dy in range(spans_y):
                    take = footprint & (count_x > dx) & (count_y > dy)
                    if not take.any():
                        continue
                    rows = (owner_plane + 2)[take]
                    cols = ((start_y + dy) * self.board_w + (start_x + dx))[take]
                    np.add.at(flat, (rows, cols), 1.0)

        all_rows = owner_plane
        all_cols = cell_y * self.board_w + cell_x
        hp_raw = batch.entity_hp.ravel()[index]
        shield_raw = batch.entity_shield.ravel()[index]
        hitpoints = np.where(hp_raw >= 0, hp_raw, 0).astype(np.float64) / 10_000.0
        shield = np.where(shield_raw > 0, shield_raw, 0).astype(np.float64) / 10_000.0
        threat = self.tables.static_damage[rv].astype(np.float64) / 5_000.0
        # ``ufunc.at`` with float64 operands accumulates in float64 and narrows
        # once, while the per-lane loop adds float32 values one at a time; cast
        # first so a cell shared by two entities matches to the last bit.
        np.add.at(flat, (all_rows + 4, all_cols), hitpoints.astype(np.float32))
        np.add.at(flat, (all_rows + 5, all_cols), shield.astype(np.float32))
        np.add.at(flat, (all_rows + 6, all_cols), threat.astype(np.float32))
        return VectorSpatialPlaneSection(planes=torch.from_numpy(planes))


@dataclass(frozen=True)
class VectorEmptySection:
    """The batched empty effects and events records for every lane.

    The bridge's no-event envelope carries no active effects and no combat
    events, so both sections are the stock empty records widened to the lane
    count: effects have zero rows, events keep their fixed capacity with every
    row masked off and the padding sentinels the tensorizer validates
    (``owner_type`` neutral, group/token addresses ``-1``).
    """

    active_effects: ActiveEffectSetV4
    events: EventSetV4
    event_count: np.ndarray
    event_overflow: np.ndarray

    def lane(self, lane: int) -> tuple[ActiveEffectSetV4, EventSetV4, int, int]:
        index = int(lane)
        effects = ActiveEffectSetV4(
            effect_vocab_id=self.active_effects.effect_vocab_id[index : index + 1].clone(),
            parent_type=self.active_effects.parent_type[index : index + 1].clone(),
            parent_index=self.active_effects.parent_index[index : index + 1].clone(),
            source_owner_type=self.active_effects.source_owner_type[index : index + 1].clone(),
            runtime_features=self.active_effects.runtime_features[index : index + 1].clone(),
            mask=self.active_effects.mask[index : index + 1].clone(),
        )
        events = EventSetV4(
            event_type=self.events.event_type[index : index + 1].clone(),
            owner_type=self.events.owner_type[index : index + 1].clone(),
            source_card_vocab_id=self.events.source_card_vocab_id[index : index + 1].clone(),
            source_form=self.events.source_form[index : index + 1].clone(),
            source_group_index=self.events.source_group_index[index : index + 1].clone(),
            target_token_index=self.events.target_token_index[index : index + 1].clone(),
            features=self.events.features[index : index + 1].clone(),
            mask=self.events.mask[index : index + 1].clone(),
        )
        return effects, events, int(self.event_count[index]), int(self.event_overflow[index])


class VectorEmptySectionBuilder:
    """Build the empty effects/events records for a whole vector batch.

    This is a pure widening of the stock empty records, so there is nothing to
    derive per lane.  It exists so the facts-only ``tensorize`` can fill every
    ``UniversalSemanticBatchV4`` field without the per-lane contract path, and so
    the envelope's "no effects, no events" scope is explicit rather than implied.
    """

    def __init__(self, tensorizer: Any) -> None:
        self.tensorizer = tensorizer
        self.config = tensorizer.config
        _actor_seat(tensorizer)
        self.effect_feature_dim = len(ACTIVE_EFFECT_RUNTIME_FEATURE_NAMES)
        self.event_rows = int(self.config.max_recent_events)
        self.event_feature_dim = int(self.config.event_feature_dim)

    def build(self, batch: VectorObservationBatch) -> VectorEmptySection:
        lanes = int(batch.count)
        active_effects = ActiveEffectSetV4(
            effect_vocab_id=torch.zeros((lanes, 0), dtype=torch.long),
            parent_type=torch.zeros((lanes, 0), dtype=torch.long),
            parent_index=torch.full((lanes, 0), -1, dtype=torch.long),
            source_owner_type=torch.zeros((lanes, 0), dtype=torch.long),
            runtime_features=torch.zeros((lanes, 0, self.effect_feature_dim), dtype=torch.float32),
            mask=torch.zeros((lanes, 0), dtype=torch.bool),
        )
        rows = self.event_rows
        events = EventSetV4(
            event_type=torch.zeros((lanes, rows), dtype=torch.long),
            owner_type=torch.full((lanes, rows), OWNER_NEUTRAL, dtype=torch.long),
            source_card_vocab_id=torch.zeros((lanes, rows), dtype=torch.long),
            source_form=torch.zeros((lanes, rows), dtype=torch.long),
            source_group_index=torch.full((lanes, rows), -1, dtype=torch.long),
            target_token_index=torch.full((lanes, rows), -1, dtype=torch.long),
            features=torch.zeros((lanes, rows, self.event_feature_dim), dtype=torch.float32),
            mask=torch.zeros((lanes, rows), dtype=torch.bool),
        )
        zeros = np.zeros(lanes, dtype=np.int64)
        return VectorEmptySection(
            active_effects=active_effects,
            events=events,
            event_count=zeros,
            event_overflow=zeros.copy(),
        )


@dataclass(frozen=True)
class VectorScalarSection:
    """The batched ``_scalars`` output for every lane."""

    scalars: torch.Tensor

    def lane(self, lane: int) -> torch.Tensor:
        index = int(lane)
        return self.scalars[index : index + 1].clone()


class VectorScalarBuilder:
    """Fill the generic match scalars for a whole vector batch in one pass.

    ``_scalars`` writes 18 numbers per decision: the match clock and phase
    (from the mode timeline), both elixirs and the reserved balance, both crown
    counts, the visible-entity count, the group/child/candidate/event overflow
    ratios and the static actor identity.  The batch carries every per-frame
    fact; the timeline is the same static asset ``BattleEnvV1`` uses, so it is
    compiled once into per-tick lookup tables.

    Scope is the bridge envelope: no tracker (so the enemy elixir interval is the
    sound public bound, upper 1.0 / uncertainty 1.0) and no events (so both event
    columns are zero).  A tracker-backed caller is refused rather than guessed.
    """

    def __init__(self, tensorizer: Any, *, timeline: Any, native_tick_offset: int = 0) -> None:
        self.tensorizer = tensorizer
        self.config = tensorizer.config
        self.actor = _actor_seat(tensorizer)
        if tensorizer.tracker is not None:
            raise ValueError(
                "the batched scalar build needs the bridge's no-event envelope; a tracker would invent a belief"
            )
        if int(self.config.generic_scalar_dim) != len(MATCH_SCALAR_NAMES):
            raise ValueError(
                f"scalar schema of {int(self.config.generic_scalar_dim)} columns is not a V4 profile"
            )
        self.timeline = timeline
        self.native_tick_offset = int(native_tick_offset)
        self.max_tick = int(timeline.maximum_tick)
        # ``BattleTimelineV1.phase`` is a per-tick scan; compile it once.
        phases = [timeline.phase(tick) for tick in range(self.max_tick + 1)]
        self.overtime = np.asarray([name in {"overtime", "sudden_death"} for name, _ in phases], dtype=np.float32)
        self.tiebreak = np.asarray([name == "tiebreak" for name, _ in phases], dtype=np.float32)
        self.multiplier = np.asarray([value for _, value in phases], dtype=np.float64)

    def build(
        self,
        batch: VectorObservationBatch,
        *,
        groups: VectorGroupSection,
        candidates: VectorCandidateSection,
    ) -> VectorScalarSection:
        lanes = int(batch.count)
        scalars = np.zeros((lanes, len(MATCH_SCALAR_NAMES)), dtype=np.float32)
        seat = self.actor
        canonical = batch.tick[:, seat].astype(np.int64)
        phase_tick = np.clip(canonical + self.native_tick_offset, 0, self.max_tick)
        remaining_ms = np.maximum(0, NATIVE_GAMEPLAY_END_TICK - canonical) * TICK_MS
        entity_count = (batch.entity_valid & ~batch.entity_is_tower).sum(axis=1)
        elixir_exact = batch.elixir_raw[:, seat].astype(np.float64) / 10_000.0
        reserved = batch.elixir_reserved_raw[:, seat].astype(np.float64) / 10_000.0

        scalars[:, 0] = remaining_ms.astype(np.float64) / 300_000.0
        scalars[:, 1] = self.multiplier[phase_tick] / 3.0
        scalars[:, 2] = self.overtime[phase_tick]
        scalars[:, 3] = self.tiebreak[phase_tick]
        scalars[:, 4] = elixir_exact / 10.0
        scalars[:, 5] = batch.crowns[:, seat] / 3.0
        scalars[:, 6] = batch.crowns_enemy[:, seat] / 3.0
        scalars[:, 7] = entity_count / float(self.config.max_children_total)
        # No events in the bridge envelope.
        scalars[:, 8] = 0.0
        scalars[:, 9] = groups.group_overflow / float(self.config.max_battle_groups)
        scalars[:, 10] = groups.child_overflow / float(self.config.max_children_total)
        scalars[:, 11] = batch.entity_overflow[:, seat].astype(np.float64) / float(self.config.max_children_total)
        scalars[:, 12] = 0.0
        scalars[:, 13] = candidates.count / float(self.config.max_action_candidates)
        scalars[:, 14] = reserved / 10.0
        # Without a tracker the only sound public bound is the full [0, 10]
        # interval, encoded as (upper 1, uncertainty 1).
        scalars[:, 15] = 1.0
        scalars[:, 16] = 1.0
        # Native owner 0 is absolute True Red and survives perspective.
        scalars[:, 17] = float(self.actor == 0)
        return VectorScalarSection(scalars=torch.from_numpy(scalars))


@dataclass(frozen=True)
class CandidateGeometry:
    """Static placement facts for one card, as ``BattleEnvV1``'s mask entry carries them.

    ``_cached_placement_entry`` builds these from ``card_placement_mask``: the
    footprint comes from the native-probed building lattice
    (``native_building_footprint``), the subcell offset from that footprint's
    even-size rule, and ``is_building`` from a stationary collision profile.
    They are card-static, so the vector path compiles them once instead of
    rebuilding a 32x18 grid per lane; the live legality of a tile is the
    vector's own mask view.
    """

    is_building: bool = False
    width_tiles: float = 0.0
    height_tiles: float = 0.0
    offset_x: float = 0.0
    offset_y: float = 0.0

    @property
    def half_width(self) -> float:
        return self.width_tiles / 2.0 if self.is_building else 0.0

    @property
    def half_height(self) -> float:
        return self.height_tiles / 2.0 if self.is_building else 0.0


def candidate_geometry(tensorizer: Any, card_id: int, form_code: int = 0) -> CandidateGeometry:
    """The placement facts for one card and policy form.

    Base and evolution forms share the deployment lattice, exactly as
    ``native_building_footprint`` documents; the form is still passed so a
    future divergence surfaces here rather than in a stale table.
    """

    spec = tensorizer.card_specs.get(int(card_id))
    if spec is None:
        return CandidateGeometry()
    form = "evolution" if int(form_code) == FORM_EVOLVED else "base"
    footprint = native_building_footprint(spec, form=form)
    profile = building_placement_profile(int(card_id)) if spec.kind == CardKind.BUILDING else None
    is_building = bool(
        spec.kind == CardKind.BUILDING and profile is not None and profile.stationary_collision_rectangle
    )
    if footprint is None:
        return CandidateGeometry(is_building=is_building)
    offset_x, offset_y = footprint.model_subcell_offset
    return CandidateGeometry(
        is_building=is_building,
        width_tiles=float(footprint.width_tiles),
        height_tiles=float(footprint.height_tiles),
        offset_x=float(offset_x),
        offset_y=float(offset_y),
    )


class VectorCandidateBuilder:
    """Fill the deploy-candidate section for a whole vector batch in one pass.

    ``_candidates`` walks the actor's legal hand slots and emits one row each:
    the card token and its runtime form, the deck row it addresses, the runtime
    cost, the native placement grid, and the building geometry the decoder uses
    to turn a model cell back into a native command point.

    The vector envelope supplies all of that as facts — the hand header carries
    the card, cost, form and pending flag, and ``VectorBattle.masks`` carries the
    play tiles — so the only per-card work is the static geometry table above.
    A slot is a candidate under ``BattleEnvV1``'s rule: the card is in hand, its
    play is not already waiting to run, its runtime cost fits this decision's
    effective elixir, and the native mask opens at least one tile.

    Scope: deploy rows only.  The bridge envelope carries no per-unit ability
    runtime (``players.abilityRuntime`` is ``null``), so ``ability_sources`` is
    empty on both paths and no ABILITY row exists to reproduce.  Mirror is out of
    scope: it needs the actor-private hand/placement runtime contract to name the
    copied card, which the vector frame does not carry.
    """

    def __init__(self, tensorizer: Any) -> None:
        self.tensorizer = tensorizer
        self.config = tensorizer.config
        self.seat = _actor_seat(tensorizer)
        self.perspective = tensorizer.perspective
        self.max_candidates = int(self.config.max_action_candidates)
        self.rows_shape = (int(self.config.board_height), int(self.config.board_width))
        if self.rows_shape != (GRID_HEIGHT, GRID_WIDTH):
            raise ValueError(
                f"candidate placement is {GRID_HEIGHT}x{GRID_WIDTH}, config is {self.rows_shape}"
            )
        # The same layout the card section rows use, so ``own_card_row`` cannot
        # address a different column than the one the model reads.
        _own_order, card_to_row, _roles, _vocab = tensorizer._actor_card_layout()
        deck = sorted(int(card_id) for card_id in card_to_row)
        if MIRROR_CARD_ID in deck:
            raise ValueError("the vector candidate build does not model the Mirror runtime contract")
        self.deck_ids = np.asarray(deck, dtype=np.int64)
        self.deck_rows = np.asarray([int(card_to_row[card_id]) for card_id in deck], dtype=np.int64)
        self.deck_vocab = np.asarray(
            [
                int(tensorizer.catalog.require_vocab_id(card_id, context="legal deployment card"))
                for card_id in deck
            ],
            dtype=np.int64,
        )
        geometry = [candidate_geometry(tensorizer, card_id) for card_id in deck]
        self.deck_is_building = np.asarray([item.is_building for item in geometry], dtype=bool)
        self.deck_half_width = np.asarray([item.half_width for item in geometry], dtype=np.float32)
        self.deck_half_height = np.asarray([item.half_height for item in geometry], dtype=np.float32)
        self.deck_offset_x = np.asarray([item.offset_x for item in geometry], dtype=np.float32)
        self.deck_offset_y = np.asarray([item.offset_y for item in geometry], dtype=np.float32)
        # The UID is a stable hash of (variant, visible, effective, native slot);
        # the deck is static, so every candidate the build can emit is one of
        # at most ``deck x hand_slots`` identities.
        self.deck_uid = np.asarray(
            [
                [
                    candidate_uid_v4(
                        variant=CANDIDATE_DEPLOY,
                        visible_card_vocab_id=int(vocab_id),
                        effective_card_vocab_id=int(vocab_id),
                        native_hand_slot=slot,
                    )
                    for slot in range(HAND_SLOTS)
                ]
                for vocab_id in self.deck_vocab
            ],
            dtype=np.int64,
        )

    def _deck_columns(self, card_ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Deck row and vocabulary id per card id, or -1 outside the actor deck."""

        positions = np.searchsorted(self.deck_ids, card_ids)
        clipped = np.clip(positions, 0, self.deck_ids.size - 1)
        found = (positions < self.deck_ids.size) & (self.deck_ids[clipped] == card_ids)
        rows = np.where(found, self.deck_rows[clipped], -1)
        vocab = np.where(found, self.deck_vocab[clipped], -1)
        return rows, vocab

    def hand_slots(self, batch: VectorObservationBatch, *, seat: int | None = None) -> tuple[np.ndarray, np.ndarray]:
        """The model-view hand mask and the effective elixir ``_candidates`` reads."""

        seat = self.seat if seat is None else int(seat)
        if int(batch.hand_slots) != HAND_SLOTS:
            raise ValueError(f"the candidate build assumes {HAND_SLOTS} hand slots, batch has {batch.hand_slots}")
        cost_raw = batch.hand_cost[:, seat, :].astype(np.int64) * 10_000
        effective_raw = np.maximum(batch.elixir_raw[:, seat].astype(np.int64) - batch.elixir_reserved_raw[:, seat], 0)
        placement = batch.placement_rows(seat)
        occupied = placement.any(axis=(2, 3))
        legal = (
            (batch.hand_vocab[:, seat, :] >= 0)
            & (batch.hand_pending[:, seat, :] == 0)
            & (cost_raw <= effective_raw[:, None])
            & occupied
        )
        return legal, effective_raw.astype(np.float64) / 10_000.0

    def _model_grid(self, grid: np.ndarray) -> np.ndarray:
        """Game-frame placement rows -> the actor's model grid."""

        if self.perspective.flip_y:
            grid = grid[:, :, ::-1, :]
        if self.perspective.flip_x:
            grid = grid[:, :, :, ::-1]
        return grid

    def build(self, batch: VectorObservationBatch, *, seat: int | None = None) -> VectorCandidateSection:
        seat = self.seat if seat is None else int(seat)
        lanes = int(batch.count)
        c = self.max_candidates
        legal, effective_elixir = self.hand_slots(batch, seat=seat)
        count = legal.sum(axis=1).astype(np.int64)
        if int(count.max(initial=0)) > c:
            raise ValueError("live legal candidate set exceeds V4 capacity")

        # Rows are packed in hand-slot order, which is the order ``_candidates``
        # walks ``player.hand`` in.
        column = np.cumsum(legal, axis=1, dtype=np.int64) - 1
        lane_rows, slot_rows = np.nonzero(legal)
        target = column[lane_rows, slot_rows]

        hand_ids = batch.hand_card_id[:, seat, :].astype(np.int64)
        card_ids = hand_ids[lane_rows, slot_rows]
        if np.any(card_ids == MIRROR_CARD_ID):
            raise ValueError("the vector candidate build does not model the Mirror runtime contract")
        deck_row, vocab_id = self._deck_columns(card_ids)
        if np.any(deck_row < 0):
            missing = int(card_ids[deck_row < 0][0])
            raise ValueError(f"legal hand card {missing} is not in the actor deck")
        geometry_index = np.searchsorted(self.deck_ids, card_ids)
        form = batch.hand_form[:, seat, :][lane_rows, slot_rows].astype(np.int64)
        placement = self._model_grid(batch.placement_rows(seat))[lane_rows, slot_rows].astype(bool)

        # Padding matches the oracle's arena defaults, so a lane slice can be
        # compared field for field without re-masking.
        template = ActionCandidatesV4(
            mask=np.zeros((lanes, c), dtype=bool),
            elixir=np.zeros(lanes, dtype=np.float32),
            uid=np.zeros((lanes, c), dtype=np.int64),
            variant=np.zeros((lanes, c), dtype=np.int64),
            visible_card_vocab_id=np.zeros((lanes, c), dtype=np.int64),
            effective_card_vocab_id=np.zeros((lanes, c), dtype=np.int64),
            effective_form=np.zeros((lanes, c), dtype=np.int64),
            own_card_row=np.full((lanes, c), -1, dtype=np.int64),
            source_group_index=np.full((lanes, c), -1, dtype=np.int64),
            source_child_index=np.full((lanes, c), -1, dtype=np.int64),
            ability_vocab_id=np.zeros((lanes, c), dtype=np.int64),
            cost=np.zeros((lanes, c), dtype=np.float32),
            runtime_features=np.zeros((lanes, c, int(self.config.candidate_runtime_feature_dim)), dtype=np.float32),
            target_mode=np.zeros((lanes, c), dtype=np.int64),
            placement=np.zeros((lanes, c, *self.rows_shape), dtype=bool),
            is_building=np.zeros((lanes, c), dtype=bool),
            building_half_width=np.zeros((lanes, c), dtype=np.float32),
            building_half_height=np.zeros((lanes, c), dtype=np.float32),
            building_offset_x=np.zeros((lanes, c), dtype=np.float32),
            building_offset_y=np.zeros((lanes, c), dtype=np.float32),
            exclusion_group_id=np.full((lanes, c), -1, dtype=np.int64),
            native_hand_slot=np.full((lanes, c), -1, dtype=np.int64),
            native_source_entity=np.full((lanes, c), -1, dtype=np.int64),
            native_visible_card_id=np.full((lanes, c), -1, dtype=np.int64),
        )
        rows = (lane_rows, target)
        template.mask[rows] = True
        template.elixir[...] = effective_elixir.astype(np.float32)
        template.variant[rows] = CANDIDATE_DEPLOY
        template.visible_card_vocab_id[rows] = vocab_id
        template.effective_card_vocab_id[rows] = vocab_id
        template.effective_form[rows] = form
        template.own_card_row[rows] = deck_row
        template.cost[rows] = batch.hand_cost[:, seat, :][lane_rows, slot_rows].astype(np.float32)
        template.target_mode[rows] = TARGET_GRID
        template.placement[rows] = placement
        template.is_building[rows] = self.deck_is_building[geometry_index]
        template.building_half_width[rows] = self.deck_half_width[geometry_index]
        template.building_half_height[rows] = self.deck_half_height[geometry_index]
        template.building_offset_x[rows] = self.deck_offset_x[geometry_index]
        template.building_offset_y[rows] = self.deck_offset_y[geometry_index]
        template.exclusion_group_id[rows] = 1000 + slot_rows
        template.native_hand_slot[rows] = slot_rows
        template.native_visible_card_id[rows] = card_ids
        template.runtime_features[lane_rows, target, 0] = template.cost[lane_rows, target] / 10.0
        if placement.shape[0]:
            # ``float(placement_array.mean())`` over the 32x18 grid, in the same
            # float64 arithmetic, then narrowed once.
            template.runtime_features[lane_rows, target, 1] = placement.reshape(
                int(placement.shape[0]), -1
            ).mean(axis=1)
        template.uid[rows] = self.deck_uid[geometry_index, slot_rows]
        candidates = ActionCandidatesV4(
            **{
                name: torch.from_numpy(getattr(template, name))
                for name in _CANDIDATE_FIELDS
            }
        )
        return VectorCandidateSection(
            candidates=candidates,
            count=count,
            hand_slots=legal,
            effective_elixir=effective_elixir.astype(np.float32),
        )


class VectorRelationBuilder:
    """Fill the target/card/group relation edges for a whole vector batch.

    The bridge's entity envelope has no attack/projectile/capture/source runtime
    and no causal-group references, so the edge set is exactly:

    * ``entity -> its visible target`` and the reverse, when both ends have a
      token (a target may be a tower, which is a token even though it is not an
      entity);
    * ``tower -> its visible target`` and the reverse;
    * ``card -> group`` for every retained group whose (owner, source card) is
      one of the actor's cards or the opponent's unknown row.

    The tensorizer sorts and de-duplicates the edge set, so this build does the
    same with one ``np.unique`` over lane-tagged keys.  A lane's edges are
    variable-length; :meth:`VectorRelationSection.lane` slices one lane back to
    the exact ``(1, count)`` tensors the per-lane path emits.
    """

    def __init__(self, tensorizer: Any) -> None:
        self.tensorizer = tensorizer
        self.config = tensorizer.config
        self.group_start = int(self.config.max_towers + self.config.max_own_cards + self.config.max_opponent_cards)
        self.own_start = int(self.config.max_towers)
        self.opponent_start = self.own_start + int(self.config.max_own_cards)
        self.tower_count = int(self.config.max_towers)
        token_count = self.group_start + int(self.config.max_battle_groups)
        if token_count > 128:
            # The packed sort key gives ``source``/``target`` seven bits.
            raise ValueError(f"relation token space {token_count} exceeds the 128-token packing")

    def build(
        self,
        batch: VectorObservationBatch,
        cards: VectorCardSection,
        groups: VectorGroupSection,
        towers: VectorTowerSection,
    ) -> VectorRelationSection:
        lanes = int(batch.count)
        # The stock ``_relations`` receives ``tower_token_by_entity`` from
        # ``_towers``: a tower's token is its **model row**, not its native id.
        # Those differ for seat 1 (actor-first order) and under a mirror.
        tower_index = towers.tower_index
        tower_columns = int(tower_index.shape[1])
        lane_axis = np.broadcast_to(np.arange(lanes, dtype=np.int64)[:, None], batch.entity_id.shape)
        group_rank = groups.group_index.astype(np.int64)
        valid_entity = batch.entity_valid & ~batch.entity_is_tower
        entity_id = np.where(valid_entity, batch.entity_id, -1).astype(np.int64)
        group_token = np.where(valid_entity & (group_rank >= 0), self.group_start + group_rank, -1)
        raw_target = np.where(batch.entity_valid, batch.entity_target, -1).astype(np.int64)

        # Targets can name a tower (ids 0..5) or another entity; resolve both
        # through one lane-tagged sorted-key search.
        target_id = np.where(valid_entity, raw_target, -1)
        lane_offsets = (np.arange(lanes, dtype=np.int64) << 32)[:, None]
        id_keys = (lane_offsets | (entity_id & 0xFFFFFFFF)).ravel()
        token_values = group_token.ravel()
        useful = token_values >= 0
        order = np.argsort(id_keys[useful], kind="stable")
        lookup_keys = id_keys[useful][order]
        lookup_values = token_values[useful][order]

        def resolve(ids: np.ndarray) -> np.ndarray:
            keys = (lane_offsets | (ids.astype(np.int64) & 0xFFFFFFFF)).ravel()
            if lookup_keys.size:
                positions = np.searchsorted(lookup_keys, keys)
                clipped = np.clip(positions, 0, lookup_keys.size - 1)
                matched = (positions < lookup_keys.size) & (lookup_keys[clipped] == keys)
                resolved = np.where(matched, lookup_values[clipped], -1).reshape(ids.shape)
            else:
                resolved = np.full(ids.shape, -1, dtype=np.int64)
            # A tower target is a token through the same model-row map.
            tower_target = (ids >= 0) & (ids < self.tower_count)
            safe = np.clip(ids, 0, tower_columns - 1)
            tower_token = np.take_along_axis(tower_index, safe, axis=1)
            return np.where(tower_target, tower_token, resolved)

        target_token = resolve(target_id)

        edges: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []

        source_ok = (group_token >= 0) & (target_token >= 0) & (target_token != group_token)
        edges.append(
            (
                lane_axis[source_ok],
                group_token[source_ok],
                target_token[source_ok],
                np.full(int(source_ok.sum()), REL_TARGETS, dtype=np.int64),
            )
        )
        edges.append(
            (
                lane_axis[source_ok],
                target_token[source_ok],
                group_token[source_ok],
                np.full(int(source_ok.sum()), REL_TARGETED_BY, dtype=np.int64),
            )
        )

        # Tower sources: the fixed six-row section, whose visible target lives
        # on the shared entity row for that tower id.  A tower can target an
        # entity as well as another tower.  The source token is the tower's
        # model row, exactly as the oracle's ``tower_token_by_entity``.
        tower_slot = self._tower_slots(batch)
        tower_row_target = np.take_along_axis(raw_target, np.maximum(tower_slot, 0), axis=1)
        tower_valid = (tower_slot >= 0) & (tower_row_target >= 0)
        tower_source = tower_index
        tower_resolved = resolve(np.where(tower_valid, tower_row_target, -1))
        tower_edges = (
            tower_valid & (tower_source >= 0) & (tower_resolved >= 0) & (tower_resolved != tower_source)
        )
        tower_lanes = np.broadcast_to(np.arange(lanes, dtype=np.int64)[:, None], tower_valid.shape)
        edges.append(
            (
                tower_lanes[tower_edges],
                tower_source[tower_edges],
                tower_resolved[tower_edges],
                np.full(int(tower_edges.sum()), REL_TARGETS, dtype=np.int64),
            )
        )
        edges.append(
            (
                tower_lanes[tower_edges],
                tower_resolved[tower_edges],
                tower_source[tower_edges],
                np.full(int(tower_edges.sum()), REL_TARGETED_BY, dtype=np.int64),
            )
        )

        # Card -> group: a static (owner, card vocabulary) token table.
        own_map = self._card_token_map(cards.own.card_vocab_id[0].numpy(), cards.own.mask[0].numpy(), self.own_start)
        enemy_map = self._card_token_map(
            cards.opponent.card_vocab_id[0].numpy(), cards.opponent.mask[0].numpy(), self.opponent_start
        )
        group_mask = groups.groups.mask.numpy()
        group_owner = groups.groups.owner_type.numpy()
        group_source = groups.groups.source_card_vocab_id.numpy()
        card_token = np.where(
            group_owner == OWNER_SELF,
            self._lookup_token(own_map, group_source),
            np.where(group_owner == OWNER_ENEMY, self._lookup_token(enemy_map, group_source), -1),
        )
        group_rows = np.broadcast_to(np.arange(group_mask.shape[1], dtype=np.int64)[None, :], group_mask.shape)
        group_lanes = np.broadcast_to(np.arange(lanes, dtype=np.int64)[:, None], group_mask.shape)
        card_edges = group_mask & (card_token >= 0)
        edges.append(
            (
                group_lanes[card_edges],
                card_token[card_edges],
                (self.group_start + group_rows)[card_edges],
                np.full(int(card_edges.sum()), REL_CARD_REPRESENTS_GROUP, dtype=np.int64),
            )
        )
        return self._pack(lanes, edges)

    @staticmethod
    def _tower_slots(batch: VectorObservationBatch) -> np.ndarray:
        """Tower id -> its shared entity-row slot, or -1 when the row is absent."""

        slots = np.full((int(batch.count), 6), -1, dtype=np.int64)
        rows = np.flatnonzero(batch.entity_valid.ravel() & batch.entity_is_tower.ravel())
        if rows.size:
            lanes, columns = np.unravel_index(rows, batch.entity_valid.shape)
            ids = batch.entity_id[lanes, columns].astype(np.int64)
            known = (ids >= 0) & (ids < 6)
            slots[lanes[known], ids[known]] = columns[known]
        return slots

    def _card_token_map(self, vocab: np.ndarray, mask: np.ndarray, start: int) -> np.ndarray:
        table = np.full(int(self.tensorizer.catalog.vocab_size), -1, dtype=np.int64)
        for row in np.flatnonzero(mask):
            value = int(vocab[row])
            if 0 <= value < table.size:
                table[value] = start + int(row)
        return table

    @staticmethod
    def _lookup_token(table: np.ndarray, vocab: np.ndarray) -> np.ndarray:
        safe = np.clip(vocab, 0, max(0, table.size - 1))
        values = table[safe] if table.size else np.full(vocab.shape, -1, dtype=np.int64)
        return np.where((vocab >= 0) & (vocab < table.size), values, -1)

    def _pack(
        self, lanes: int, edges: Sequence[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]
    ) -> VectorRelationSection:
        """Sort and de-duplicate the lane-tagged edge set into padded arrays."""

        flat_lanes = np.concatenate([edge[0] for edge in edges]) if edges else np.zeros(0, dtype=np.int64)
        flat_sources = np.concatenate([edge[1] for edge in edges]) if edges else np.zeros(0, dtype=np.int64)
        flat_targets = np.concatenate([edge[2] for edge in edges]) if edges else np.zeros(0, dtype=np.int64)
        flat_kinds = np.concatenate([edge[3] for edge in edges]) if edges else np.zeros(0, dtype=np.int64)

        # ``source``/``target`` are token ids below ~100 and ``relation`` is a
        # 4-bit code, so one int64 key orders exactly like the tensorizer's
        # ``sorted(edges)`` tuple order.
        key = (((flat_sources * 128) + flat_targets) * 16) + flat_kinds
        lane_key = flat_lanes * (128 * 128 * 16) + key
        if lane_key.size:
            lane_key = np.unique(lane_key)
            flat_lanes = lane_key // (128 * 128 * 16)
            key = lane_key % (128 * 128 * 16)
            flat_kinds = key % 16
            key = key // 16
            flat_targets = key % 128
            flat_sources = key // 128

        counts = np.bincount(flat_lanes, minlength=lanes).astype(np.int64)
        width = max(1, int(counts.max(initial=0)))
        source = np.zeros((lanes, width), dtype=np.int64)
        target = np.zeros((lanes, width), dtype=np.int64)
        relation = np.zeros((lanes, width), dtype=np.int64)
        mask = np.zeros((lanes, width), dtype=bool)
        if flat_lanes.size:
            starts = np.concatenate([[0], np.cumsum(counts)])
            for lane in np.flatnonzero(counts):
                begin, end = int(starts[lane]), int(starts[lane + 1])
                source[lane, : end - begin] = flat_sources[begin:end]
                target[lane, : end - begin] = flat_targets[begin:end]
                relation[lane, : end - begin] = flat_kinds[begin:end]
                mask[lane, : end - begin] = True
        return VectorRelationSection(
            source=torch.from_numpy(source),
            target=torch.from_numpy(target),
            relation_type=torch.from_numpy(relation),
            mask=torch.from_numpy(mask),
        )


@dataclass(frozen=True)
class VectorRelationSection:
    """Padded relation edges for every lane.

    ``lane(n)`` slices one lane back to the variable-length ``(1, count)``
    record the per-lane tensorizer emits, so a batch digest can be compared
    row for row.
    """

    source: torch.Tensor
    target: torch.Tensor
    relation_type: torch.Tensor
    mask: torch.Tensor

    def count(self, lane: int) -> int:
        return int(self.mask[int(lane)].sum())

    def lane(self, lane: int) -> RelationEdgesV4:
        count = max(1, self.count(lane))
        return RelationEdgesV4(
            source=self.source[int(lane) : int(lane) + 1, :count].clone(),
            target=self.target[int(lane) : int(lane) + 1, :count].clone(),
            relation_type=self.relation_type[int(lane) : int(lane) + 1, :count].clone(),
            mask=self.mask[int(lane) : int(lane) + 1, :count].clone(),
        )


@dataclass(frozen=True)
class VectorCardSection:
    """The batched ``_card_sets`` output for every lane."""

    own: CardSetV4
    opponent: CardSetV4
    card_to_row: dict[int, int]

    def lane(self, lane: int) -> tuple[CardSetV4, CardSetV4]:
        """One lane back to the exact one-row card sets the stock method emits."""

        index = int(lane)

        def card_set(value: CardSetV4) -> CardSetV4:
            return CardSetV4(
                card_vocab_id=value.card_vocab_id[index : index + 1].clone(),
                runtime_form=value.runtime_form[index : index + 1].clone(),
                role_bits=value.role_bits[index : index + 1].clone(),
                runtime_features=value.runtime_features[index : index + 1].clone(),
                mask=value.mask[index : index + 1].clone(),
            )

        return card_set(self.own), card_set(self.opponent)


@dataclass(frozen=True)
class VectorCandidateSection:
    """The batched ``_candidates`` output for every lane.

    Candidate rows are variable-length per lane, so ``candidates`` is padded to
    ``max_action_candidates`` with the same sentinels the per-lane arena writes
    and ``count`` is the live row count.  ``hand_slots`` is the mask the
    tensorizer reads (``pending_execution`` / ``insufficient_elixir`` /
    ``no_legal_placement`` all close a slot), and ``effective_elixir`` is the
    exact value the section's ``elixir`` field carries.
    """

    candidates: ActionCandidatesV4
    count: np.ndarray
    hand_slots: np.ndarray
    effective_elixir: np.ndarray

    def lane(self, lane: int) -> ActionCandidatesV4:
        index = int(lane)
        values = {
            item: getattr(self.candidates, item)[index : index + 1].clone()
            for item in _CANDIDATE_FIELDS
            if item != "elixir"
        }
        values["elixir"] = self.candidates.elixir[index : index + 1].clone()
        return ActionCandidatesV4(**values)


_CANDIDATE_FIELDS = (
    "mask",
    "elixir",
    "uid",
    "variant",
    "visible_card_vocab_id",
    "effective_card_vocab_id",
    "effective_form",
    "own_card_row",
    "source_group_index",
    "source_child_index",
    "ability_vocab_id",
    "cost",
    "runtime_features",
    "target_mode",
    "placement",
    "is_building",
    "building_half_width",
    "building_half_height",
    "building_offset_x",
    "building_offset_y",
    "exclusion_group_id",
    "native_hand_slot",
    "native_source_entity",
    "native_visible_card_id",
)


@dataclass
class _PlayerView:
    """The player attributes ``_card_sets`` / ``_scalars`` read.

    A dataclass because the stock perspective transform copies the actor player
    when a seat-1 oracle is built (``dataclasses.fields`` is how it does that).
    """

    owner: int
    hand: tuple[int, ...] = ()
    next_card: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    crowns: int = 0
    elixir_exact: float | None = None
    deck: tuple[int, ...] = ()
    revealed_cards: tuple[int, ...] = ()
    evolution_runtime_states: tuple[Any, ...] = ()
    ability_runtime_states: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        self.owner = int(self.owner)
        self.hand = tuple(int(value) for value in self.hand)
        self.deck = tuple(int(value) for value in self.deck)
        self.metadata = dict(self.metadata)
        self.crowns = int(self.crowns)
        self.revealed_cards = ()
        self.evolution_runtime_states = ()
        self.ability_runtime_states = ()


class _TowerView:
    """The tower attributes ``_towers`` / ``_relations`` read."""

    def __init__(
        self,
        *,
        entity_id: int,
        owner: int,
        tower_kind: str,
        position: tuple[int, int],
        hitpoints: float,
        max_hitpoints: float,
        active: bool,
        tower_troop_id: int | None,
        visible_target: int | None,
    ) -> None:
        self.entity_id = int(entity_id)
        self.owner = int(owner)
        self.tower_kind = tower_kind
        self.position = (float(position[0]), float(position[1]))
        self.hitpoints = float(hitpoints)
        self.max_hitpoints = float(max_hitpoints)
        self.shield = 0.0
        self.active = bool(active)
        self.status: tuple[str, ...] = ()
        self.tower_troop_id = tower_troop_id
        self.tower_troop_runtime = None
        self.visible_target = visible_target
        self.attack_state = None
        self.effect_states: tuple[Any, ...] = ()


_TOWER_KIND_NAMES = ("king", "princess_left", "princess_right")


def reference_pending_actions(
    batch: VectorObservationBatch, lane: int, *, tensorizer: Any, seat: int = 0
) -> tuple[PendingActionV1, ...]:
    """The actor's waiting plays as `ActionV1`/`PendingActionV1` rows.

    ``pending_x`` / ``pending_y`` are game coordinates (the fact layer converts
    each seat's frame once).  The native command point carries the owner-signed
    building subcell offset, so the native cell is ``(point - sign*offset)/tile``.
    This projects the *native* (game) grid, which is what the per-lane tensorizer
    receives before its perspective transform.  Only plays of the actor's deck
    cards are emitted, which is what the bridge envelope can carry.
    """

    config = tensorizer.config
    tick = int(batch.tick[int(lane), int(seat)])
    tile_x = float(WORLD_WIDTH) / float(config.board_width)
    tile_y = float(WORLD_HEIGHT) / float(config.board_height)
    sign = 1.0 if int(seat) == 0 else -1.0
    actions: list[PendingActionV1] = []
    for row in range(int(batch.pending_rows)):
        card_id = int(batch.pending_card_id[int(lane), int(seat), row])
        if card_id <= 0:
            continue
        remaining = max(0, int(batch.pending_ticks[int(lane), int(seat), row]))
        spec = tensorizer.card_specs.get(card_id)
        footprint = native_building_footprint(spec) if spec is not None else None
        # The native command point for an even-sized building sits half a tile
        # past the cell center, so undo the same per-card offset the mask used.
        offset_x = (0.5 if footprint is not None and footprint.width_tiles % 2 == 0 else 0.0) * sign
        offset_y = (0.5 if footprint is not None and footprint.height_tiles % 2 == 0 else 0.0) * sign
        cell_x = int(float(batch.pending_x[int(lane), int(seat), row]) // tile_x - offset_x)
        cell_y = int(float(batch.pending_y[int(lane), int(seat), row]) // tile_y - offset_y)
        cell = (min(max(cell_x, 0), GRID_WIDTH - 1), min(max(cell_y, 0), GRID_HEIGHT - 1))
        action = construct_contract(
            ActionV1,
            owner=int(seat),
            kind=ActionKind.PLAY_CARD,
            hand_slot=0,
            card_id=card_id,
            target_kind=TargetKind.GRID,
            target_grid=cell,
        )
        actions.append(
            construct_contract(
                PendingActionV1, action=action, requested_tick=tick, expected_execution_tick=tick + remaining
            )
        )
    for slot in range(int(batch.ability_slots)):
        card_id = int(batch.ability_champion_id[int(lane), int(seat), slot])
        remaining = int(batch.ability_ticks[int(lane), int(seat), slot])
        if card_id <= 0 or remaining <= 0:
            continue
        ability_ids = tensorizer.ability_ids_by_card.get(card_id)
        if not ability_ids:
            continue
        source = int(batch.ability_unit[int(lane), int(seat), slot])
        action = construct_contract(
            ActionV1,
            owner=int(seat),
            kind=ActionKind.ACTIVATE_ABILITY,
            source_entity=max(0, source),
            ability_id=min(ability_ids),
            target_kind=TargetKind.NONE,
        )
        actions.append(
            construct_contract(
                PendingActionV1, action=action, requested_tick=tick, expected_execution_tick=tick + remaining
            )
        )
    return tuple(actions)


def reference_observation(
    batch: VectorObservationBatch,
    lane: int,
    *,
    tables: VectorEntityTables,
    tensorizer: Any,
    seat: int = 0,
    tower_troop_id: int | None = None,
    action_mask: Any | None = None,
    episode_id: str | None = None,
) -> Any:
    """The per-lane oracle input, projected from the same facts.

    The tensorizer's own ``_card_sets`` / ``_towers`` / ``_relations`` decide
    everything from these rows, so a mismatch means a batched table is wrong,
    not that both sides share a shortcut.  Only the attributes those sections
    read are supplied.  ``action_mask`` is the projected mask a caller needs for
    ``_candidates``; a missing one is the empty mask the other sections see.
    ``episode_id`` is only for a caller that drives the stock ``tensorize``
    entry point (the section oracles never read it).
    """

    if seat not in (0, 1):
        raise ValueError(f"facts seat {seat} is not a seat")
    lane = int(lane)
    entities = reference_entities(batch, lane, tables=tables, tensorizer=tensorizer, seat=seat)

    metadata: dict[str, Any] = {"hand_slot_by_card": {}, "hand_runtime_by_slot": {}}
    hand: list[int] = []
    for slot in range(int(batch.hand_slots)):
        if int(batch.hand_vocab[lane, seat, slot]) < 0:
            continue
        card_id = int(batch.hand_card_id[lane, seat, slot])
        hand.append(card_id)
        metadata["hand_slot_by_card"][str(card_id)] = slot
        metadata["hand_runtime_by_slot"][str(slot)] = {"form_code": int(batch.hand_form[lane, seat, slot])}
    next_id = int(batch.next_card_id[lane, seat])
    players = (
        _PlayerView(
            seat,
            hand=hand,
            next_card=next_id if next_id > 0 else None,
            metadata=metadata,
            crowns=int(batch.crowns[lane, seat]),
            elixir_exact=float(batch.elixir_raw[lane, seat]) / 10_000.0,
        ),
        _PlayerView(1 - seat, hand=(), next_card=None, metadata={}, crowns=int(batch.crowns_enemy[lane, seat])),
    )

    tower_slots = VectorRelationBuilder._tower_slots(batch)
    towers = []
    for entity_id in range(int(batch.tower_rows)):
        slot = int(tower_slots[lane, entity_id]) if entity_id < tower_slots.shape[1] else -1
        if slot < 0:
            continue
        owner = int(batch.tower_owner[lane, entity_id])
        kind = _TOWER_KIND_NAMES[int(batch.tower_kind[lane, entity_id])]
        hp = float(batch.tower_hp[lane, entity_id])
        max_hp = float(batch.tower_max_hp[lane, entity_id])
        target = int(batch.entity_target[lane, slot])
        towers.append(
            _TowerView(
                entity_id=entity_id,
                owner=owner,
                tower_kind=kind,
                position=(int(batch.entity_x[lane, slot]), int(batch.entity_y[lane, slot])),
                hitpoints=hp,
                max_hitpoints=max_hp,
                active=bool(batch.tower_valid[lane, entity_id] and hp > 0),
                tower_troop_id=(int(tower_troop_id) if tower_troop_id else None),
                visible_target=target if target >= 0 else None,
            )
        )

    return _ObservationView(
        entities,
        players=players,
        towers=tuple(towers),
        pending_actions=reference_pending_actions(batch, lane, tensorizer=tensorizer, seat=seat),
        tick=int(batch.tick[lane, seat]),
        action_mask=action_mask,
        owner=int(seat),
        episode_id=str(episode_id) if episode_id is not None else "",
    )


def reference_scalar_observation(
    batch: VectorObservationBatch,
    lane: int,
    *,
    tables: VectorEntityTables,
    tensorizer: Any,
    timeline: Any,
    native_tick_offset: int = 0,
    seat: int = 0,
    action_mask: Any | None = None,
    episode_id: str | None = None,
    tower_troop_id: int | None = None,
) -> _ObservationView:
    """The per-lane observation ``_scalars`` reads, with the timeline facts.

    ``BattleEnvV1.observe`` takes the phase and the elixir multiplier from the
    mode timeline at the *native* tick and the remaining clock from the canonical
    tick, so the projection keeps the same split: ``batch.tick`` is canonical,
    ``native_tick_offset`` is the encoder's first raw tick.
    """

    base = reference_observation(
        batch,
        lane,
        tables=tables,
        tensorizer=tensorizer,
        seat=seat,
        action_mask=action_mask,
        episode_id=episode_id,
        tower_troop_id=tower_troop_id,
    )
    canonical = int(batch.tick[int(lane), int(seat)])
    phase, multiplier = timeline.phase(max(0, canonical + int(native_tick_offset)))
    remaining = max(0, NATIVE_GAMEPLAY_END_TICK - canonical) * TICK_MS
    return _ObservationView(
        base.entities,
        players=base.players,
        towers=base.towers,
        pending_actions=base.pending_actions,
        tick=base.tick,
        action_mask=base.action_mask,
        time=_TimeView(remaining_ms=remaining, elixir_multiplier=multiplier),
        phase=phase,
        metadata={"entity_overflow_count": int(batch.entity_overflow[int(lane), int(seat)])},
        owner=base.owner,
        episode_id=base.episode_id,
        tier=base.tier,
    )


def compare_empty_section(
    section: VectorEmptySection,
    tensorizer: Any,
    batch: VectorObservationBatch,
    *,
    tables: VectorEntityTables,
    seat: int = 0,
    lanes: Sequence[int] | None = None,
) -> list[str]:
    """The empty effect/event records, lane by lane, against the oracle."""

    mismatches: list[str] = []
    selected = range(batch.count) if lanes is None else [int(lane) for lane in lanes]
    for lane in selected:
        observation = reference_observation(batch, lane, tables=tables, tensorizer=tensorizer, seat=int(seat))
        tensorizer._frame_players.clear()
        _own, _opponent, _card_to_row = tensorizer._card_sets(observation)
        _groups, group_index, child_index, _group_overflow, _child_overflow = oracle_groups(
            tensorizer, observation.entities
        )
        _towers, tower_index = tensorizer._towers(observation)
        expected_effects = tensorizer._active_effects(
            observation, child_index_by_entity=child_index, tower_index_by_entity=tower_index
        )
        expected_events, event_count, event_overflow = tensorizer._events(
            observation, group_index_by_entity=group_index, tower_token_by_entity=tower_index
        )
        actual_effects, actual_events, actual_count, actual_overflow = section.lane(lane)
        for name in (
            "effect_vocab_id",
            "parent_type",
            "parent_index",
            "source_owner_type",
            "runtime_features",
            "mask",
        ):
            if not torch.equal(getattr(actual_effects, name), getattr(expected_effects, name)):
                mismatches.append(f"active_effects.{name} lane={lane}")
        for name in (
            "event_type",
            "owner_type",
            "source_card_vocab_id",
            "source_form",
            "source_group_index",
            "target_token_index",
            "features",
            "mask",
        ):
            if not torch.equal(getattr(actual_events, name), getattr(expected_events, name)):
                mismatches.append(f"events.{name} lane={lane}")
        if actual_count != int(event_count) or actual_overflow != int(event_overflow):
            mismatches.append(f"events.count lane={lane}")
        if len(mismatches) >= 20:
            break
    return mismatches


def compare_scalar_section(
    section: VectorScalarSection,
    tensorizer: Any,
    batch: VectorObservationBatch,
    *,
    tables: VectorEntityTables,
    timeline: Any,
    native_tick_offset: int = 0,
    seat: int = 0,
    lanes: Sequence[int] | None = None,
) -> list[str]:
    """Every match scalar, lane by lane, against the oracle.

    The oracle resolves the group/child overflow and the candidate count through
    its own sections, so the scalar comparison does not inherit the batched
    counts it is meant to check.
    """

    mismatches: list[str] = []
    selected = range(batch.count) if lanes is None else [int(lane) for lane in lanes]
    for lane in selected:
        observation = reference_scalar_observation(
            batch,
            lane,
            tables=tables,
            tensorizer=tensorizer,
            timeline=timeline,
            native_tick_offset=native_tick_offset,
            seat=int(seat),
            action_mask=reference_action_mask(batch, lane, tensorizer=tensorizer, seat=int(seat)),
        )
        tensorizer._frame_players.clear()
        _own, _opponent, card_to_row = tensorizer._card_sets(observation)
        _groups, group_index, child_index, group_overflow, child_overflow = oracle_groups(
            tensorizer, observation.entities
        )
        _candidates, candidate_count = tensorizer._candidates(
            observation,
            card_to_row=card_to_row,
            group_index_by_entity=group_index,
            child_index_by_entity=child_index,
        )
        expected = tensorizer._scalars(
            observation,
            group_overflow=group_overflow,
            child_overflow=child_overflow,
            event_count=0,
            event_overflow=0,
            candidate_count=candidate_count,
        )
        if not torch.equal(section.lane(lane), expected):
            mismatches.append(f"match_scalars lane={lane}")
        if len(mismatches) >= 20:
            break
    return mismatches


def reference_action_mask(
    batch: VectorObservationBatch,
    lane: int,
    *,
    tensorizer: Any,
    seat: int = 0,
    model_frame: bool = True,
) -> _MaskView:
    """The per-lane ``action_mask`` projection ``_candidates`` reads.

    The stock section consumes four facts from the mask: which hand slots are
    open, the placement entry behind each open slot, the ability sources (empty
    in this envelope) and the exact effective elixir.  The entry is the
    projection of the same hand header and native tile view the batched build
    uses: the card's runtime cost and form, its static footprint and subcell
    offset, and ``row_major`` as the ``[32][18]`` sequence the tensorizer
    validates.

    ``model_frame=True`` applies the actor perspective to ``row_major`` (for a
    direct section oracle); ``False`` leaves the native/game grid intact, which
    is what ``tensorize`` transforms itself.  The two differ only for seat 1.

    A slot is open under the environment's rule — in hand, its play not already
    waiting to run, affordable at this decision's elixir, and at least one legal
    tile — which is why the floor of the projected grid is all it contributes.
    """

    if seat not in (0, 1):
        raise ValueError(f"facts seat {seat} is not a seat")
    lane = int(lane)
    elixir_raw = int(batch.elixir_raw[lane, seat])
    reserved_raw = int(batch.elixir_reserved_raw[lane, seat])
    effective_elixir = max(0, elixir_raw - reserved_raw) / 10_000.0
    placement = batch.placement_rows(seat)[lane]
    if model_frame:
        if tensorizer.perspective.flip_y:
            placement = placement[:, ::-1, :]
        if tensorizer.perspective.flip_x:
            placement = placement[:, :, ::-1]
    slots: list[bool] = []
    entries: dict[str, Any] = {}
    for slot in range(int(batch.hand_slots)):
        if int(batch.hand_vocab[lane, seat, slot]) < 0:
            slots.append(False)
            continue
        card_id = int(batch.hand_card_id[lane, seat, slot])
        cost = int(batch.hand_cost[lane, seat, slot])
        form_code = int(batch.hand_form[lane, seat, slot])
        geometry = candidate_geometry(tensorizer, card_id, form_code)
        rows = [[bool(value) for value in row] for row in placement[slot]]
        legal = bool(
            not int(batch.hand_pending[lane, seat, slot])
            and cost * 10_000 <= max(0, elixir_raw - reserved_raw)
            and placement[slot].any()
        )
        slots.append(legal)
        entries[str(slot)] = {
            "card_id": card_id,
            "shape": (GRID_HEIGHT, GRID_WIDTH),
            "row_major": rows,
            "visible_card_id": card_id,
            "effective_card_id": card_id,
            "native_effective_card_id": card_id,
            "effective_cost": float(cost),
            "form_code": form_code,
            "native_form_code": form_code,
            "source_native_hand_slot": slot,
            "footprint_width_tiles": (geometry.width_tiles if geometry.width_tiles > 0 else None),
            "footprint_height_tiles": (geometry.height_tiles if geometry.height_tiles > 0 else None),
            "model_subcell_offset": (
                (geometry.offset_x, geometry.offset_y)
                if geometry.width_tiles > 0 or geometry.height_tiles > 0
                else None
            ),
        }
    return _MaskView(
        hand_slots=tuple(slots),
        placement_masks=entries,
        ability_sources=(),
        reasons={"effective_elixir": effective_elixir, "reserved_elixir": reserved_raw / 10_000.0},
    )


def compare_candidate_section(
    section: VectorCandidateSection,
    tensorizer: Any,
    batch: VectorObservationBatch,
    *,
    tables: VectorEntityTables,
    seat: int = 0,
    lanes: Sequence[int] | None = None,
) -> list[str]:
    """Every candidate tensor field, lane by lane, against the oracle."""

    mismatches: list[str] = []
    selected = range(batch.count) if lanes is None else [int(lane) for lane in lanes]
    for lane in selected:
        observation = reference_observation(
            batch,
            lane,
            tables=tables,
            tensorizer=tensorizer,
            seat=int(seat),
            action_mask=reference_action_mask(batch, lane, tensorizer=tensorizer, seat=int(seat)),
        )
        tensorizer._frame_players.clear()
        _own, _opponent, card_to_row = tensorizer._card_sets(observation)
        _groups, group_index, child_index, _group_overflow, _child_overflow = oracle_groups(
            tensorizer, observation.entities
        )
        expected, expected_count = tensorizer._candidates(
            observation,
            card_to_row=card_to_row,
            group_index_by_entity=group_index,
            child_index_by_entity=child_index,
        )
        if int(section.count[lane]) != int(expected_count):
            mismatches.append(f"candidate_count lane={lane}")
        actual = section.lane(lane)
        for name in _CANDIDATE_FIELDS:
            if not torch.equal(getattr(actual, name), getattr(expected, name)):
                mismatches.append(f"candidates.{name} lane={lane}")
        if int(section.hand_slots[lane].sum()) != sum(bool(value) for value in observation.action_mask.hand_slots):
            mismatches.append(f"hand_slots lane={lane}")
        if len(mismatches) >= 20:
            break
    return mismatches


def compare_card_section(
    section: VectorCardSection,
    tensorizer: Any,
    batch: VectorObservationBatch,
    *,
    tables: VectorEntityTables,
    seat: int = 0,
    lanes: Sequence[int] | None = None,
) -> list[str]:
    """Own and opponent card tensors, lane by lane, against the oracle."""

    if tensorizer.tracker is not None:
        raise ValueError("the card oracle needs the bridge's no-event envelope (tracker=None)")
    mismatches: list[str] = []
    selected = range(batch.count) if lanes is None else [int(lane) for lane in lanes]
    for lane in selected:
        observation = reference_observation(batch, lane, tables=tables, tensorizer=tensorizer, seat=int(seat))
        # ``_player`` caches by ``id(observation)``; a per-lane view is freed
        # after each lane, so a reused id could answer with another lane's row.
        tensorizer._frame_players.clear()
        own, opponent, card_to_row = tensorizer._card_sets(observation)
        for label, expected, actual in (("own", own, section.own), ("opponent", opponent, section.opponent)):
            for name in ("card_vocab_id", "runtime_form", "role_bits", "runtime_features", "mask"):
                if not torch.equal(getattr(actual, name)[lane], getattr(expected, name)[0]):
                    mismatches.append(f"{label}.{name} lane={lane}")
        if dict(section.card_to_row) != {int(key): int(value) for key, value in card_to_row.items()}:
            mismatches.append(f"card_to_row lane={lane}")
        if len(mismatches) >= 20:
            break
    return mismatches


def compare_relation_section(
    section: VectorRelationSection,
    tensorizer: Any,
    batch: VectorObservationBatch,
    *,
    tables: VectorEntityTables,
    tower_troop_id: int | None = None,
    seat: int = 0,
    lanes: Sequence[int] | None = None,
) -> list[str]:
    """Every relation edge, lane by lane, against the oracle sections."""

    if tensorizer.tracker is not None:
        raise ValueError("the relation oracle needs the bridge's no-event envelope (tracker=None)")
    mismatches: list[str] = []
    selected = range(batch.count) if lanes is None else [int(lane) for lane in lanes]
    for lane in selected:
        observation = reference_observation(
            batch, lane, tables=tables, tensorizer=tensorizer, tower_troop_id=tower_troop_id, seat=int(seat)
        )
        tensorizer._frame_players.clear()
        own, opponent, _card_to_row = tensorizer._card_sets(observation)
        _towers, tower_index = tensorizer._towers(observation)
        groups, group_index, _child_index, _group_overflow, _child_overflow = oracle_groups(
            tensorizer, observation.entities
        )
        expected = tensorizer._relations(
            observation,
            group_index_by_entity=group_index,
            tower_token_by_entity=tower_index,
            own_cards=own,
            opponent_cards=opponent,
            groups=groups,
        )
        actual = section.lane(lane)
        for name in ("source", "target", "relation_type", "mask"):
            if not torch.equal(getattr(actual, name), getattr(expected, name)):
                mismatches.append(f"relations.{name} lane={lane}")
        if len(mismatches) >= 20:
            break
    return mismatches


def compare_tower_section(
    section: VectorTowerSection,
    tensorizer: Any,
    batch: VectorObservationBatch,
    *,
    tables: VectorEntityTables,
    tower_troop_id: int | None = None,
    seat: int = 0,
    lanes: Sequence[int] | None = None,
) -> list[str]:
    """Every tower tensor field and row index, lane by lane, against the oracle."""

    mismatches: list[str] = []
    selected = range(batch.count) if lanes is None else [int(lane) for lane in lanes]
    for lane in selected:
        observation = reference_observation(
            batch, lane, tables=tables, tensorizer=tensorizer, tower_troop_id=tower_troop_id, seat=int(seat)
        )
        expected, index_by_entity = tensorizer._towers(observation)
        actual, actual_index = section.lane(lane)
        for name in (
            "tower_type",
            "tower_troop_type",
            "owner_type",
            "features",
            "position",
            "extent",
            "mask",
        ):
            if not torch.equal(getattr(actual, name), getattr(expected, name)):
                mismatches.append(f"towers.{name} lane={lane}")
        for entity_id in range(int(batch.tower_rows)):
            expected_row = int(index_by_entity.get(entity_id, -1))
            if int(actual_index.get(entity_id, -1)) != expected_row:
                mismatches.append(f"towers.index lane={lane} entity={entity_id}")
        if len(mismatches) >= 20:
            break
    return mismatches


def compare_spatial_plane_section(
    section: VectorSpatialPlaneSection,
    tensorizer: Any,
    batch: VectorObservationBatch,
    *,
    tables: VectorEntityTables,
    seat: int = 0,
    lanes: Sequence[int] | None = None,
) -> list[str]:
    """Every spatial plane, lane by lane, against the oracle."""

    mismatches: list[str] = []
    selected = range(batch.count) if lanes is None else [int(lane) for lane in lanes]
    for lane in selected:
        # ``_spatial_planes`` reads six per-frame caches keyed by entity id, and
        # vector lanes reuse ids, so clear them exactly as ``tensorize`` does.
        for name in (
            "_frame_tile_position_cache",
            "_frame_base_card_id_cache",
            "_frame_spatial_extent_cache",
            "_frame_spatial_radius_cache",
            "_frame_archetype_id_cache",
            "_frame_child_kind_cache",
        ):
            getattr(tensorizer, name).clear()
        observation = reference_observation(batch, lane, tables=tables, tensorizer=tensorizer, seat=int(seat))
        expected = tensorizer._spatial_planes(observation)
        if not torch.equal(section.lane(lane), expected):
            mismatches.append(f"explicit_spatial_planes lane={lane}")
        if len(mismatches) >= 20:
            break
    return mismatches


def reference_entities(
    batch: VectorObservationBatch, lane: int, *, tables: VectorEntityTables, tensorizer: Any, seat: int = 0
) -> tuple[EntityStateV1, ...]:
    """The per-lane oracle input: ``EntityStateV1`` rows from the same facts.

    The tensorizer's own ``_groups`` then decides archetype, child kind,
    extent, radius and runtime form, so a batched mismatch is a table bug.
    """

    if seat not in (0, 1):
        raise ValueError(f"facts seat {seat} is not a seat")
    if int(tensorizer.actor_owner) != seat:
        raise ValueError(
            f"tensorizer actor {int(tensorizer.actor_owner)} does not match facts seat {seat}"
        )
    lane = int(lane)
    entities: list[EntityStateV1] = []
    for slot in np.flatnonzero(batch.entity_valid[lane] & ~batch.entity_is_tower[lane]):
        slot = int(slot)
        row_vocab = int(batch.entity_row_vocab[lane, slot])
        if not 0 <= row_vocab < tables.vocab_size:
            raise ValueError(f"entity row vocabulary {row_vocab} is outside the vector vocabulary")
        hp = int(batch.entity_hp[lane, slot])
        max_hp = int(batch.entity_max_hp[lane, slot])
        shield = int(batch.entity_shield[lane, slot])
        side = int(batch.entity_side[lane, slot])
        target = int(batch.entity_target[lane, slot])
        velocity_valid = bool(batch.entity_velocity_valid[lane, slot])
        velocity = (
            (float(batch.entity_velocity[lane, slot, 0]), float(batch.entity_velocity[lane, slot, 1]))
            if velocity_valid
            else None
        )
        card_id = int(tables.card_id[row_vocab])
        global_id = int(tables.global_id[row_vocab])
        entities.append(
            construct_contract(
                EntityStateV1,
                entity_id=int(batch.entity_id[lane, slot]),
                owner=side if side in (0, 1) else None,
                card_id=card_id if card_id > 0 else None,
                entity_kind=tables.entity_kind[row_vocab],
                position=(float(batch.entity_x[lane, slot]), float(batch.entity_y[lane, slot])),
                velocity=velocity,
                hitpoints=float(hp) if hp >= 0 else None,
                max_hitpoints=float(max_hp) if max_hp >= 0 else None,
                shield=float(shield) if shield > 0 else None,
                age_ms=int(batch.entity_age_ms[lane, slot]),
                visible_target=target if target >= 0 else None,
                native_data_global_id=global_id if global_id > 0 else None,
            )
        )
    return tuple(entities)


@dataclass
class _ObservationView:
    """The observation attributes the batched-section oracles read.

    ``owner`` / ``episode_id`` / ``tier`` exist so the stock ``tensorize``
    entry point accepts a projection for a full-record digest gate, and the
    class is a dataclass so the stock perspective transform can copy it for a
    seat-1 oracle.  The section oracles never read the identity fields.
    """

    entities: tuple[EntityStateV1, ...] = ()
    players: tuple[Any, ...] = ()
    towers: tuple[Any, ...] = ()
    pending_actions: tuple[PendingActionV1, ...] = ()
    tick: int = 0
    action_mask: Any | None = None
    time: Any | None = None
    phase: str = "normal"
    metadata: dict[str, Any] = field(default_factory=dict)
    events: tuple[Any, ...] = ()
    owner: int = 0
    episode_id: str = ""
    tier: ObservationTier = ObservationTier.FAIR

    def __post_init__(self) -> None:
        self.entities = tuple(self.entities)
        self.players = tuple(self.players)
        self.towers = tuple(self.towers)
        self.pending_actions = tuple(self.pending_actions)
        self.tick = int(self.tick)
        if self.action_mask is None:
            self.action_mask = _MaskView()
        self.metadata = dict(self.metadata)
        self.events = tuple(self.events)
        self.owner = int(self.owner)
        self.episode_id = str(self.episode_id)


class _TimeView:
    """The ``TimeStateV1`` attributes ``_scalars`` reads."""

    def __init__(self, *, remaining_ms: int, elixir_multiplier: float) -> None:
        self.remaining_ms = int(remaining_ms)
        self.elixir_multiplier = float(elixir_multiplier)


@dataclass
class _MaskView:
    """The ``ActionMaskV1`` attributes the tensorizer reads, without the contract.

    A dataclass so the stock perspective transform can copy it for a seat-1
    digest oracle (``dataclasses.fields``).
    """

    hand_slots: tuple[bool, ...] = (False, False, False, False)
    placement_masks: dict[str, Any] = field(default_factory=dict)
    ability_sources: tuple[int, ...] = ()
    reasons: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.hand_slots = tuple(bool(value) for value in self.hand_slots)
        self.placement_masks = dict(self.placement_masks)
        self.ability_sources = tuple(int(value) for value in self.ability_sources)
        self.reasons = dict(self.reasons)



def oracle_groups(
    tensorizer: Any, entities: Sequence[EntityStateV1]
) -> tuple[Any, dict[int, int], dict[int, int], int, int]:
    """Run the per-lane oracle with only the fields ``_groups`` reads.

    ``tensorize`` clears its per-frame caches before building a decision, so a
    replay that calls ``_groups`` directly must do the same: the caches are
    keyed by entity id, and vector lanes reuse ids.
    """

    for name in (
        "_frame_tile_position_cache",
        "_frame_base_card_id_cache",
        "_frame_spatial_extent_cache",
        "_frame_spatial_radius_cache",
        "_frame_archetype_id_cache",
        "_frame_child_kind_cache",
    ):
        getattr(tensorizer, name).clear()
    return tensorizer._groups(_ObservationView(entities))


def compare_group_section(
    section: VectorGroupSection,
    tensorizer: Any,
    batch: VectorObservationBatch,
    *,
    tables: VectorEntityTables,
    seat: int = 0,
    lanes: Sequence[int] | None = None,
) -> list[str]:
    """Every group/child tensor field, lane by lane, against the oracle."""

    section = pad_group_section(
        section,
        groups=int(tensorizer.config.max_battle_groups),
        children=int(tensorizer.config.max_children_total),
    )
    mismatches: list[str] = []
    selected = range(batch.count) if lanes is None else [int(lane) for lane in lanes]
    for lane in selected:
        entities = reference_entities(batch, lane, tables=tables, tensorizer=tensorizer, seat=int(seat))
        groups, group_index, child_index, group_overflow, child_overflow = oracle_groups(tensorizer, entities)
        if int(section.group_overflow[lane]) != int(group_overflow):
            mismatches.append(f"group_overflow lane={lane}")
        if int(section.child_overflow[lane]) != int(child_overflow):
            mismatches.append(f"child_overflow lane={lane}")
        for name in (
            "source_card_vocab_id",
            "group_type",
            "owner_type",
            "runtime_form",
            "features",
            "position",
            "extent",
            "mask",
            "child_archetype_id",
            "child_group_index",
            "child_type",
            "child_features",
            "child_position",
            "child_extent",
            "child_radius",
            "child_mask",
        ):
            actual = getattr(section.groups, name)[lane]
            expected = getattr(groups, name)[0]
            if not torch.equal(actual, expected):
                mismatches.append(f"{name} lane={lane}")
        for slot in np.flatnonzero(batch.entity_valid[lane] & ~batch.entity_is_tower[lane]):
            entity_id = int(batch.entity_id[lane, slot])
            expected_group = int(group_index.get(entity_id, -1))
            if int(section.group_index[lane, slot]) != expected_group:
                mismatches.append(f"group_index lane={lane} entity={entity_id}")
            expected_child = int(child_index.get(entity_id, -1))
            if int(section.child_index[lane, slot]) != expected_child:
                mismatches.append(f"child_index lane={lane} entity={entity_id}")
        if len(mismatches) >= 20:
            break
    return mismatches


__all__ = [
    "CandidateGeometry",
    "VectorCandidateBuilder",
    "VectorCandidateSection",
    "VectorCardBuilder",
    "VectorCardSection",
    "VectorEmptySection",
    "VectorEmptySectionBuilder",
    "VectorEntityTables",
    "VectorGroupBuilder",
    "VectorGroupSection",
    "VectorRelationBuilder",
    "VectorRelationSection",
    "VectorScalarBuilder",
    "VectorScalarSection",
    "VectorSpatialPlaneBuilder",
    "VectorSpatialPlaneSection",
    "VectorTowerBuilder",
    "VectorTowerSection",
    "candidate_geometry",
    "compare_candidate_section",
    "compare_card_section",
    "compare_empty_section",
    "compare_group_section",
    "compare_relation_section",
    "compare_scalar_section",
    "compare_spatial_plane_section",
    "compare_tower_section",
    "oracle_groups",
    "pad_group_record",
    "pad_group_section",
    "reference_action_mask",
    "reference_entities",
    "reference_observation",
    "reference_pending_actions",
    "reference_scalar_observation",
]
