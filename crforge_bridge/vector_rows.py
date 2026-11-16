"""Batched row build, starting with the causal-group section (C3-B).

C3 measured the packet as ~1.0 CPU ms/decision of tensorizer and attributed
~0.54 of it to ``_groups`` + its children, the largest section.  Session 17
landed the vectorized facts layer (``vector_observation.py``, 0.032 ms/lane);
this module is the first row build on top of it: the same groups and children
the per-lane ``UniversalObservationTensorizerV4._groups`` emits, computed for
every lane of a ``VectorBattle`` in one pass over fixed-size NumPy arrays.

Scope is deliberately narrow, and the scope is the bridge's no-event envelope
``VectorObservationBatch`` carries:

* one group per entity (the bridge never sends a causal-group reference, so
  ``build_causal_groups`` returns singletons);
* the bridge's raw ``entity_kind`` rule: the card spec's kind for a resolved
  card, ``effect`` for a row with no card id, ``unknown`` otherwise.  Towers
  are not entities (they are the separate six-row tower section);
* no attack/movement/projectile/visibility/evolution runtime, no ability
  sources and no combat events, exactly as the bridge's minimal rich player
  envelope supplies them;
* seat 0 (the facts layer reads ``VectorBattle.observations`` seat 0).

The per-lane tensorizer stays the oracle.  ``reference_entities`` builds the
``EntityStateV1`` rows the oracle consumes from the same facts, and the gate
tool compares every group/child tensor against
``UniversalObservationTensorizerV4._groups`` on those entities.  The oracle
takes its archetype/child-kind/extent/radius/runtime-form decisions through the
tensorizer's own helper methods, so a mismatch means this batched table is
wrong, not that both sides share a shortcut.

Nothing here validates or freezes a contract, and nothing here may be used to
replace the validated evaluator/parity path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import torch

from native_runner.arena import WORLD_HEIGHT, WORLD_WIDTH, native_building_footprint
from native_runner.contracts import CardKind, EntityStateV1, construct_contract
from native_runner.training.v4.catalog import UNKNOWN_CARD_VOCAB_ID
from native_runner.training.v4.config import CHILD_FEATURE_NAMES, GROUP_FEATURE_NAMES
from native_runner.training.v4.tensors import BattleGroupSetV4

from .catalog import FORM_HERO
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


def _kind_rule(card_id: int, card_specs: dict[int, Any]) -> str:
    """The bridge's raw ``entity_kind`` for a card id (no projectile runtime)."""

    spec = card_specs.get(int(card_id)) if int(card_id) > 0 else None
    if spec is not None:
        return str(spec.kind.value)
    return "effect" if int(card_id) <= 0 else "unknown"


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


class VectorGroupBuilder:
    """Fill the group/children section for a whole vector batch in one pass."""

    def __init__(self, tensorizer: Any, tables: VectorEntityTables) -> None:
        self.tensorizer = tensorizer
        self.tables = tables
        self.config = tensorizer.config
        self.perspective = tensorizer.perspective
        self.max_groups = int(self.config.max_battle_groups)
        self.max_children = int(self.config.max_children_total)

    # ---- entity row view ---------------------------------------------------

    def _entity_rows(self, batch: VectorObservationBatch) -> dict[str, np.ndarray]:
        """Per-entity arrays for every lane, masked to live non-tower rows."""

        valid = batch.entity_valid & ~batch.entity_is_tower
        rv = np.clip(batch.entity_row_vocab, 0, self.tables.vocab_size - 1)
        take = lambda array: np.where(valid, array[rv], 0)

        entity_id = np.where(valid, batch.entity_id, -1).astype(np.int64)
        hp = np.where(valid & (batch.entity_hp >= 0), batch.entity_hp, 0).astype(np.float64)
        max_hp = np.where(valid & (batch.entity_max_hp >= 0), batch.entity_max_hp, 0).astype(np.float64)
        shield = np.where(valid & (batch.entity_shield > 0), batch.entity_shield, 0).astype(np.float64)
        age = np.where(valid, batch.entity_age_ms, 0).astype(np.float64)
        target = valid & (batch.entity_target >= 0)

        x, y = self._tile_positions(batch, valid)
        half_w = take(self.tables.footprint_half_w)
        half_h = take(self.tables.footprint_half_h)
        extent_x0 = x - half_w
        extent_y0 = y - half_h
        extent_x1 = x + half_w
        extent_y1 = y + half_h

        velocity = np.zeros((*valid.shape, 2), dtype=np.float64)
        velocity[...] = batch.entity_velocity.astype(np.float64)
        velocity_valid = valid & batch.entity_velocity_valid
        # The model flips signs exactly as ``_model_velocity`` does, then the
        # tensorizer divides by world-to-board scale before the ``/ 10``.
        if self.perspective.flip_x:
            velocity[..., 0] = -velocity[..., 0]
        if self.perspective.flip_y:
            velocity[..., 1] = -velocity[..., 1]
        velocity[..., 0] = velocity[..., 0] / (float(WORLD_WIDTH) / float(self.config.board_width))
        velocity[..., 1] = velocity[..., 1] / (float(WORLD_HEIGHT) / float(self.config.board_height))

        return {
            "valid": valid,
            "row_vocab": rv,
            "entity_id": entity_id,
            "hp": hp,
            "max_hp": max_hp,
            "shield": shield,
            "age_ms": age,
            "target": target,
            "target_id": np.where(valid, batch.entity_target, -1),
            "x": x,
            "y": y,
            "extent_x0": extent_x0,
            "extent_y0": extent_y0,
            "extent_x1": extent_x1,
            "extent_y1": extent_y1,
            "velocity": velocity,
            "velocity_valid": velocity_valid,
            "card_id": take(self.tables.card_id),
            "archetype_id": take(self.tables.archetype_id),
            "child_type": take(self.tables.child_type),
            "card_vocab_id": take(self.tables.card_vocab_id),
            "runtime_form": take(self.tables.runtime_form),
            "group_type": take(self.tables.group_type),
            "is_hero_or_building": np.where(valid, self.tables.is_hero_or_building[rv], False),
            "radius": take(self.tables.radius),
            "side": np.where(valid, batch.entity_side, -1),
        }

    def _tile_positions(self, batch: VectorObservationBatch, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        config = self.config
        x = np.where(valid, batch.entity_x, 0).astype(np.float64)
        y = np.where(valid, batch.entity_y, 0).astype(np.float64)
        if self.perspective.flip_x:
            x = float(WORLD_WIDTH) - x
        if self.perspective.flip_y:
            y = float(WORLD_HEIGHT) - y
        x = x / (float(WORLD_WIDTH) / float(config.board_width))
        y = y / (float(WORLD_HEIGHT) / float(config.board_height))
        x = np.minimum(np.maximum(x, 0.0), float(config.board_width) - 1e-4)
        y = np.minimum(np.maximum(y, 0.0), float(config.board_height) - 1e-4)
        return x, y

    # ---- sort keys ---------------------------------------------------------

    def _orders(self, rows: dict[str, np.ndarray]) -> np.ndarray:
        """Stable per-lane order for the group priority.

        ``_group_priority`` over singleton members reduces to
        ``(id not in ability_sources, no visible target, not hero/building,
        no projectile, id)``.  The bridge envelope has no ability sources and
        no projectiles, so only the target and kind terms vary per entity.
        Encoding the tuple in one int64 key keeps the sort vectorized across
        lanes.
        """

        valid = rows["valid"]
        relation = np.where(rows["target"], 0, 1)
        hero_building = np.where(rows["is_hero_or_building"], 0, 1)
        entity_id = np.clip(rows["entity_id"], 0, 2**31 - 1)
        key = ((relation.astype(np.int64) * 2 + hero_building) << 52) | entity_id
        key = np.where(valid, key, np.iinfo(np.int64).max)
        return np.argsort(key, axis=1, kind="stable")

    def _targeted_mask(self, rows: dict[str, np.ndarray]) -> np.ndarray:
        """Per-entity: is some other entity's visible target this entity?

        The tensorizer's child priority uses the whole observation's
        ``relation_ids`` set, which is keyed by *target* id.  Lane-tagged int64
        keys reduce the membership test to one ``searchsorted`` over the whole
        batch, with no per-lane Python loop.
        """

        targeted = np.zeros(rows["valid"].shape, dtype=bool)
        if not rows["target"].any():
            return targeted
        lanes = rows["entity_id"].shape[0]
        lane_offsets = (np.arange(lanes, dtype=np.int64) << 32)[:, None]
        keys = lane_offsets | (rows["entity_id"] & 0xFFFFFFFF)
        # The set the tensorizer builds is target ids, not the ids of the
        # entities that own a target.
        target_keys = lane_offsets | (rows["target_id"].astype(np.int64) & 0xFFFFFFFF)
        target_keys = np.sort(target_keys[rows["target"]])
        flat = keys.ravel()
        positions = np.searchsorted(target_keys, flat)
        clipped = np.clip(positions, 0, target_keys.size - 1)
        matched = (positions < target_keys.size) & (target_keys[clipped] == flat)
        return matched.reshape(targeted.shape)

    def _child_order(self, rows: dict[str, np.ndarray], candidate: np.ndarray, targeted: np.ndarray) -> np.ndarray:
        """``_child_priority`` over the children of retained groups only.

        Unlike a singleton group's priority, the relation term here is the
        tensorizer's *global* ``relation_ids`` set: an entity sorts earlier
        when some other entity targets it, not when it has a target itself.
        """

        relation = np.where(targeted, 0, 1)
        hero_building = np.where(rows["is_hero_or_building"], 0, 1)
        hp_key = np.minimum(np.maximum(rows["hp"], 0.0), 1_048_575.0).astype(np.int64)
        entity_id = np.clip(rows["entity_id"], 0, 2**31 - 1)
        key = ((relation.astype(np.int64) * 2 + hero_building) << 52) | ((1_048_576 - hp_key) << 31) | entity_id
        key = np.where(candidate, key, np.iinfo(np.int64).max)
        return np.argsort(key, axis=1, kind="stable")

    # ---- build -------------------------------------------------------------

    def build(self, batch: VectorObservationBatch) -> VectorGroupSection:
        rows = self._entity_rows(batch)
        lanes = int(batch.count)
        valid = rows["valid"]
        entity_count = valid.sum(axis=1).astype(np.int64)
        group_order = self._orders(rows)

        retained_groups = np.minimum(entity_count, self.max_groups)
        group_overflow = entity_count - retained_groups

        g = self.max_groups
        c = self.max_children

        def gather(values: np.ndarray, order: np.ndarray, width: int) -> np.ndarray:
            slots = order[:, :width]
            return np.take_along_axis(values, slots, axis=1)

        # Group rows keep the first ``retained_groups`` entries of the group
        # order; everything after is padded with zeros and masked off.
        group_mask = np.arange(g, dtype=np.int64)[None, :] < retained_groups[:, None]
        g_hp = gather(rows["hp"], group_order, g)
        g_max_hp = gather(rows["max_hp"], group_order, g)
        g_shield = gather(rows["shield"], group_order, g)
        g_x = gather(rows["x"], group_order, g)
        g_y = gather(rows["y"], group_order, g)
        g_extent = np.stack(
            [
                gather(rows["extent_x0"], group_order, g),
                gather(rows["extent_y0"], group_order, g),
                gather(rows["extent_x1"], group_order, g),
                gather(rows["extent_y1"], group_order, g),
            ],
            axis=-1,
        )
        g_velocity = np.stack(
            [gather(rows["velocity"][..., 0], group_order, g), gather(rows["velocity"][..., 1], group_order, g)],
            axis=-1,
        )
        g_velocity_valid = gather(rows["velocity_valid"], group_order, g)
        g_target = gather(rows["target"], group_order, g)

        # Group rank per entity: the retained portion of the group order.
        # Scatter only the kept slots: padded slots repeat entity row 0, and
        # an unguarded scatter would overwrite row 0's real rank with -1.
        group_rank = np.full(valid.shape, -1, dtype=np.int64)
        retained_slots = group_order[:, :g]
        retained_rank = np.broadcast_to(np.arange(g, dtype=np.int64)[None, :], retained_slots.shape)
        lane_rows = np.broadcast_to(np.arange(lanes, dtype=np.int64)[:, None], retained_slots.shape)
        group_rank[lane_rows[group_mask], retained_slots[group_mask]] = retained_rank[group_mask]

        # Children are drawn only from retained groups, in child priority
        # order (the tensorizer filters ``child_candidates`` by
        # ``group_index_by_entity``).
        candidate = group_rank >= 0
        candidate_count = candidate.sum(axis=1).astype(np.int64)
        targeted = self._targeted_mask(rows)
        child_order = self._child_order(rows, candidate, targeted)
        child_entity = child_order[:, :c]
        child_candidate = np.take_along_axis(candidate, child_order, axis=1)[:, :c]
        child_count = np.minimum(candidate_count, self.max_children)
        child_mask = np.arange(c, dtype=np.int64)[None, :] < child_count[:, None]
        child_mask &= child_candidate
        child_overflow = candidate_count - child_count

        child_rank = np.full(valid.shape, -1, dtype=np.int64)
        child_rank_rows = np.broadcast_to(np.arange(lanes, dtype=np.int64)[:, None], child_entity.shape)
        child_rank_values = np.broadcast_to(np.arange(c, dtype=np.int64)[None, :], child_entity.shape)
        child_rank[child_rank_rows[child_mask], child_entity[child_mask]] = child_rank_values[child_mask]

        # ---- group tensors -------------------------------------------------
        group_features = np.zeros((lanes, g, len(GROUP_FEATURE_NAMES)), dtype=np.float32)
        group_position = np.zeros((lanes, g, 2), dtype=np.float32)
        group_extent = np.zeros((lanes, g, 4), dtype=np.float32)
        group_mask_f = group_mask.astype(bool)

        # ``_finite(hitpoints, max_hitpoints or 1)``: a present hitpoint value
        # over a missing maximum divides by 1, exactly as the Python does.
        ratio = g_hp / np.where(g_max_hp > 0, g_max_hp, 1.0)
        retained_ids = np.zeros(valid.shape, dtype=bool)
        retained_ids[child_rank_rows[child_mask], child_entity[child_mask]] = True
        g_retained = gather(retained_ids, group_order, g)

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
        group_features[..., _G_VELOCITY_X] = np.where(g_velocity_valid, g_velocity[..., 0] / 10.0, 0.0)
        group_features[..., _G_VELOCITY_Y] = np.where(g_velocity_valid, g_velocity[..., 1] / 10.0, 0.0)
        group_features[..., _G_SPEED] = np.where(
            g_velocity_valid, np.hypot(g_velocity[..., 0], g_velocity[..., 1]) / 10.0, 0.0
        )
        group_position[..., 0] = g_x
        group_position[..., 1] = g_y
        group_extent[...] = g_extent

        g_side = gather(rows["side"], group_order, g)
        owner_type = np.where(g_side == self.tensorizer.actor_owner, OWNER_SELF, OWNER_ENEMY)
        group_owner = np.where((g_side == 0) | (g_side == 1), owner_type, OWNER_NEUTRAL)
        source_card = gather(rows["card_vocab_id"], group_order, g)
        if np.any((source_card == UNKNOWN_CARD_VOCAB_ID) & group_mask):
            bad = int(gather(rows["card_id"], group_order, g)[(source_card == UNKNOWN_CARD_VOCAB_ID) & group_mask][0])
            raise ValueError(f"public causal-group source card {bad} is outside this V4 catalog")
        group_type = gather(rows["group_type"], group_order, g)
        runtime_form = gather(rows["runtime_form"], group_order, g)

        # ---- child tensors -------------------------------------------------
        child_features = np.zeros((lanes, c, len(CHILD_FEATURE_NAMES)), dtype=np.float32)
        child_position = np.zeros((lanes, c, 2), dtype=np.float32)
        child_extent = np.zeros((lanes, c, 4), dtype=np.float32)
        child_radius = np.zeros((lanes, c), dtype=np.float32)
        child_archetype = np.zeros((lanes, c), dtype=np.int64)
        child_group = np.full((lanes, c), -1, dtype=np.int64)
        child_type = np.zeros((lanes, c), dtype=np.int64)

        c_hp = gather(rows["hp"], child_order, c)
        c_max_hp = gather(rows["max_hp"], child_order, c)
        c_shield = gather(rows["shield"], child_order, c)
        c_age = gather(rows["age_ms"], child_order, c)
        c_target = gather(rows["target"], child_order, c)
        c_x = gather(rows["x"], child_order, c)
        c_y = gather(rows["y"], child_order, c)
        c_extent = np.stack(
            [
                gather(rows["extent_x0"], child_order, c),
                gather(rows["extent_y0"], child_order, c),
                gather(rows["extent_x1"], child_order, c),
                gather(rows["extent_y1"], child_order, c),
            ],
            axis=-1,
        )
        c_velocity = np.stack(
            [gather(rows["velocity"][..., 0], child_order, c), gather(rows["velocity"][..., 1], child_order, c)],
            axis=-1,
        )
        c_velocity_valid = gather(rows["velocity_valid"], child_order, c)

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
        child_radius[...] = gather(rows["radius"], child_order, c)
        child_archetype[...] = gather(rows["archetype_id"], child_order, c)
        child_type[...] = gather(rows["child_type"], child_order, c)
        child_group[...] = np.take_along_axis(group_rank, child_entity, axis=1)

        # The arena leaves a masked row at zero; the gathers above can carry a
        # padded slot's row-0 values into a masked one, so zero it explicitly.
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

        groups = BattleGroupSetV4(
            source_card_vocab_id=torch.from_numpy(source_card),
            group_type=torch.from_numpy(group_type),
            owner_type=torch.from_numpy(group_owner),
            runtime_form=torch.from_numpy(runtime_form),
            features=torch.from_numpy(group_features),
            position=torch.from_numpy(group_position),
            extent=torch.from_numpy(group_extent),
            mask=torch.from_numpy(group_mask_f),
            child_archetype_id=torch.from_numpy(child_archetype),
            child_group_index=torch.from_numpy(child_group),
            child_type=torch.from_numpy(child_type),
            child_features=torch.from_numpy(child_features),
            child_position=torch.from_numpy(child_position),
            child_extent=torch.from_numpy(child_extent),
            child_radius=torch.from_numpy(child_radius),
            child_mask=torch.from_numpy(child_mask),
        )
        return VectorGroupSection(
            groups=groups,
            group_index=group_rank.astype(np.int32),
            child_index=child_rank.astype(np.int32),
            group_overflow=group_overflow,
            child_overflow=child_overflow,
        )


def reference_entities(
    batch: VectorObservationBatch, lane: int, *, tables: VectorEntityTables, tensorizer: Any, seat: int = 0
) -> tuple[EntityStateV1, ...]:
    """The per-lane oracle input: ``EntityStateV1`` rows from the same facts.

    The tensorizer's own ``_groups`` then decides archetype, child kind,
    extent, radius and runtime form, so a batched mismatch is a table bug.
    """

    if seat != 0:
        raise ValueError("the vector facts layer currently carries seat 0 only")
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


class _ObservationView:
    """The two attributes ``_groups`` reads from an ``ObservationV1``."""

    def __init__(self, entities: Sequence[EntityStateV1]) -> None:
        self.entities = tuple(entities)
        self.action_mask = _MaskView()


class _MaskView:
    ability_sources: tuple[int, ...] = ()


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
    lanes: Sequence[int] | None = None,
) -> list[str]:
    """Every group/child tensor field, lane by lane, against the oracle."""

    mismatches: list[str] = []
    selected = range(batch.count) if lanes is None else [int(lane) for lane in lanes]
    for lane in selected:
        entities = reference_entities(batch, lane, tables=tables, tensorizer=tensorizer)
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
    "VectorEntityTables",
    "VectorGroupBuilder",
    "VectorGroupSection",
    "compare_group_section",
    "oracle_groups",
    "reference_entities",
]
