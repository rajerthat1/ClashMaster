"""Facts-only tensorize for C3-B: one ``UniversalSemanticBatchV4`` across lanes.

The row sections in :mod:`crforge_bridge.vector_rows` build every section of the
V4 packet for all lanes of a ``VectorBattle`` from the vectorized facts layer,
each gated against the stock per-lane tensorizer method.  This module is the
last piece: it calls those builders in the tensorizer's own section order and
assembles the result into the exact record
``UniversalObservationTensorizerV4.tensorize`` emits, with the lane count as the
batch dimension.

Two shapes are exported on purpose:

* :meth:`VectorTensorizer.tensorize` returns the **stacked** batch the training
  loop wants — ``batch_size == lanes`` — with variable-length sections padded
  to one common width and masked off.  Relations are the only variable-length
  section the bridge envelope emits; candidates already carry their fixed V4
  capacity.
* :func:`lane_batch` slices one lane of that stacked record back to the exact
  one-row tensors the per-lane path emits (variable-length sections trimmed to
  their live rows).  That slice is the digest gate: on the same decision
  sequence it must be ``batch_digest``-identical to
  ``UniversalObservationTensorizerV4.tensorize`` driven by
  ``reference_scalar_observation`` from the same facts.

``record_action`` is the batched form of the stock method: the previous-action
record is filled for every lane from one ``ActionSequenceV4`` whose rows are
lanes, resolving each sampled UID against that lane's candidate rows.

Scope is the bridge's no-event envelope the row builders already pin: seat 0,
singleton causal groups, no revealed opponent cards, no effects or events, a
static tower troop, and a tracker-free scalar build.  Nothing here validates or
freezes a contract, and nothing here replaces the validated evaluator/parity
path; the stacked record is still checked with ``UniversalSemanticBatchV4.validate``
when a caller asks for it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, fields, is_dataclass
from typing import Any

import numpy as np
import torch

from native_runner.training.v4.tensors import (
    CANDIDATE_ABILITY,
    GATE_WAIT,
    ActionSequenceV4,
    PreviousActionV4,
    RelationEdgesV4,
    UniversalSemanticBatchV4,
)

from .vector_observation import VectorObservationBatch
from .vector_rows import (
    VectorCandidateBuilder,
    VectorCandidateSection,
    VectorCardBuilder,
    VectorCardSection,
    VectorEmptySection,
    VectorEmptySectionBuilder,
    VectorEntityTables,
    VectorGroupBuilder,
    VectorGroupSection,
    VectorRelationBuilder,
    VectorRelationSection,
    VectorScalarBuilder,
    VectorScalarSection,
    VectorSpatialPlaneBuilder,
    VectorSpatialPlaneSection,
    VectorTowerBuilder,
    VectorTowerSection,
)


@dataclass(frozen=True)
class VectorTensorSections:
    """Every row section of one decision, in the tensorizer's build order."""

    cards: VectorCardSection
    groups: VectorGroupSection
    towers: VectorTowerSection
    empty: VectorEmptySection
    candidates: VectorCandidateSection
    relations: VectorRelationSection
    planes: VectorSpatialPlaneSection
    scalars: VectorScalarSection


class VectorTensorizer:
    """Assemble the V4 semantic batch for every lane from the facts layer.

    The constructor compiles the static per-vocabulary tables and the builders
    once.  ``tensorize`` is then one pass over the batch: cards, towers, groups,
    effects/events, candidates, relations, spatial planes and scalars, stacked
    into the record the model consumes.
    """

    def __init__(
        self,
        tensorizer: Any,
        *,
        vocabulary: Sequence[str],
        bridge_catalog: Any,
        timeline: Any,
        tower_troop_id: int | None = None,
        native_tick_offset: int = 0,
    ) -> None:
        self.tensorizer = tensorizer
        self.config = tensorizer.config
        self.tables = VectorEntityTables.build(
            tensorizer=tensorizer, vocabulary=vocabulary, bridge_catalog=bridge_catalog
        )
        self.card_builder = VectorCardBuilder(tensorizer)
        self.group_builder = VectorGroupBuilder(tensorizer, self.tables)
        self.tower_builder = VectorTowerBuilder(tensorizer, tower_troop_id=tower_troop_id)
        self.empty_builder = VectorEmptySectionBuilder(tensorizer)
        self.candidate_builder = VectorCandidateBuilder(tensorizer)
        self.relation_builder = VectorRelationBuilder(tensorizer)
        self.plane_builder = VectorSpatialPlaneBuilder(tensorizer, self.tables)
        self.scalar_builder = VectorScalarBuilder(
            tensorizer, timeline=timeline, native_tick_offset=native_tick_offset
        )
        self.tower_troop_id = tower_troop_id
        self.native_tick_offset = int(native_tick_offset)
        self._previous_action: PreviousActionV4 | None = None

    # ---- sections ----------------------------------------------------------

    def build_sections(self, batch: VectorObservationBatch) -> VectorTensorSections:
        """Every section for one decision, in the order the stock path builds them."""

        cards = self.card_builder.build(batch)
        towers = self.tower_builder.build(batch)
        groups = self.group_builder.build(batch)
        empty = self.empty_builder.build(batch)
        candidates = self.candidate_builder.build(batch)
        relations = self.relation_builder.build(batch, cards, groups, towers)
        planes = self.plane_builder.build(batch)
        scalars = self.scalar_builder.build(batch, groups=groups, candidates=candidates)
        return VectorTensorSections(
            cards=cards,
            groups=groups,
            towers=towers,
            empty=empty,
            candidates=candidates,
            relations=relations,
            planes=planes,
            scalars=scalars,
        )

    # ---- previous action ---------------------------------------------------

    def _empty_previous_action(self, lanes: int) -> PreviousActionV4:
        steps = int(self.config.max_micro_actions)
        # The record is owned data, not model output: a producer samples inside
        # ``inference_mode`` and a PPO update later writes these tensors, so the
        # empty record must be an ordinary tensor even when it is created there.
        with torch.inference_mode(False):
            return PreviousActionV4(
                gate=torch.full((lanes,), GATE_WAIT, dtype=torch.long),
                micro_action_count=torch.zeros(lanes, dtype=torch.long),
                variant=torch.zeros((lanes, steps), dtype=torch.long),
                visible_card_vocab_id=torch.zeros((lanes, steps), dtype=torch.long),
                effective_card_vocab_id=torch.zeros((lanes, steps), dtype=torch.long),
                effective_form=torch.zeros((lanes, steps), dtype=torch.long),
                ability_vocab_id=torch.zeros((lanes, steps), dtype=torch.long),
                target_cell=torch.full((lanes, steps), -1, dtype=torch.long),
                delay_offset_bin=torch.full((lanes, steps), -1, dtype=torch.long),
                source_position=torch.zeros((lanes, steps, 2), dtype=torch.float32),
                source_mask=torch.zeros((lanes, steps), dtype=torch.bool),
                action_mask=torch.zeros((lanes, steps), dtype=torch.bool),
            )

    @property
    def previous_action(self) -> PreviousActionV4 | None:
        return self._previous_action

    def reset(self, batch: VectorObservationBatch) -> None:
        """Start a fresh sequence: empty previous action for every lane."""

        self._previous_action = self._empty_previous_action(int(batch.count))

    def reset_rows(self, batch: VectorObservationBatch, lanes: Any) -> None:
        """Clear the previous action of the lanes that started a new episode.

        A vector autoresets one lane at a time, so the record cannot be dropped
        wholesale: every other lane keeps its history.  ``lanes`` is a boolean
        mask of length ``batch.count`` or an index sequence.
        """

        self._ensure_state(batch)
        assert self._previous_action is not None  # narrowed by ``_ensure_state``
        selector = np.asarray(lanes)
        if selector.dtype == bool:
            if selector.shape != (int(batch.count),):
                raise ValueError("row reset mask must have one entry per lane")
            if not selector.any():
                return
            selector = np.flatnonzero(selector)
        index = np.asarray(selector, dtype=np.int64)
        if index.size == 0:
            return
        if index.ndim != 1 or np.any(index < 0) or np.any(index >= int(batch.count)):
            raise ValueError("row reset indices are outside the batch")
        template = self._empty_previous_action(int(batch.count))
        for item in fields(self._previous_action):
            target = getattr(self._previous_action, item.name)
            target[index] = getattr(template, item.name)[index]

    def _ensure_state(self, batch: VectorObservationBatch) -> None:
        if self._previous_action is None or int(self._previous_action.gate.shape[0]) != int(batch.count):
            self.reset(batch)

    # ---- tensorize ---------------------------------------------------------

    def tensorize(self, batch: VectorObservationBatch, *, validate: bool = False) -> UniversalSemanticBatchV4:
        """Build the stacked semantic batch for every lane of ``batch``."""

        self._ensure_state(batch)
        assert self._previous_action is not None  # narrowed by ``_ensure_state``
        sections = self.build_sections(batch)
        result = UniversalSemanticBatchV4(
            match_scalars=sections.scalars.scalars,
            own_cards=sections.cards.own,
            opponent_cards=sections.cards.opponent,
            towers=sections.towers.towers,
            groups=sections.groups.groups,
            active_effects=sections.empty.active_effects,
            events=sections.empty.events,
            relation_edges=RelationEdgesV4(
                source=sections.relations.source,
                target=sections.relations.target,
                relation_type=sections.relations.relation_type,
                mask=sections.relations.mask,
            ),
            explicit_spatial_planes=sections.planes.planes,
            previous_action=self._previous_action,
            candidates=sections.candidates.candidates,
        )
        if validate:
            self.validate(result)
        return result

    def validate(self, batch: UniversalSemanticBatchV4) -> None:
        """Run the stock record validation (the model's own shape/padding contract)."""

        batch.validate(
            self.config,
            card_vocab_size=self.tensorizer.catalog.vocab_size,
            ability_vocab_size=self.tensorizer.ability_catalog.vocab_size,
            entity_archetype_vocab_size=self.tensorizer.entity_archetype_catalog.vocab_size,
            effect_vocab_size=self.tensorizer.effect_catalog.vocab_size,
        )

    # ---- record_action -----------------------------------------------------

    def record_action(
        self, sequence: ActionSequenceV4, batch: UniversalSemanticBatchV4, *, validate: bool = True
    ) -> None:
        """Record one sampled action per lane for the next decision.

        ``sequence`` carries lanes as its batch dimension, so row ``n`` is
        lane ``n``'s sampled micro-actions.  Each UID is resolved against that
        lane's candidate rows, exactly as the stock method resolves row 0.
        """

        self._ensure_state_from_batch(batch)
        candidates = batch.candidates
        lanes = int(batch.batch_size)
        if validate:
            sequence.validate(self.config, candidate_count=int(candidates.mask.shape[1]))
        previous = self._empty_previous_action(lanes)
        # The record outlives the collection step: a producer samples inside
        # ``inference_mode`` and later resets rows or hands these tensors to a
        # PPO update, and an inference tensor cannot be updated in place
        # outside that mode.  Copy the sampled rows as ordinary tensors.
        with torch.inference_mode(False):
            previous.gate = sequence.gate.detach().cpu().clone()
            previous.micro_action_count = sequence.micro_action_count.detach().cpu().clone()
        mask = candidates.mask.numpy()
        uid = candidates.uid.numpy()
        for lane in range(lanes):
            count = int(sequence.micro_action_count[lane].item())
            for step in range(count):
                candidate_uid = int(sequence.candidate_uid[lane, step].item())
                matches = np.flatnonzero(mask[lane] & (uid[lane] == candidate_uid))
                if matches.size != 1:
                    raise ValueError("previous action candidate UID is missing or duplicated")
                candidate_row = int(matches[0])
                for name in (
                    "variant",
                    "visible_card_vocab_id",
                    "effective_card_vocab_id",
                    "effective_form",
                    "ability_vocab_id",
                ):
                    getattr(previous, name)[lane, step] = getattr(candidates, name)[lane, candidate_row].cpu()
                if int(candidates.variant[lane, candidate_row]) == CANDIDATE_ABILITY:
                    child_row = int(candidates.source_child_index[lane, candidate_row].item())
                    group_row = int(candidates.source_group_index[lane, candidate_row].item())
                    if child_row >= 0:
                        previous.source_position[lane, step] = batch.groups.child_position[lane, child_row].detach().cpu()
                    elif group_row >= 0:
                        previous.source_position[lane, step] = batch.groups.position[lane, group_row].detach().cpu()
                    else:
                        raise ValueError("Ability candidate lacks a retained source")
                    previous.source_mask[lane, step] = True
                previous.target_cell[lane, step] = sequence.target_cell[lane, step].cpu()
                previous.delay_offset_bin[lane, step] = sequence.delay_offset_bin[lane, step].cpu()
                previous.action_mask[lane, step] = True
        self._previous_action = previous

    def _ensure_state_from_batch(self, batch: UniversalSemanticBatchV4) -> None:
        if self._previous_action is None or int(self._previous_action.gate.shape[0]) != int(batch.batch_size):
            self._previous_action = self._empty_previous_action(int(batch.batch_size))


# ---- lane slicing and digests ----------------------------------------------


def _slice_record(record: Any, lane: int) -> Any:
    """One row of a tensor-only V4 record, cloned the way the stock path owns it."""

    index = int(lane)
    values: dict[str, Any] = {}
    for item in fields(record):
        value = getattr(record, item.name)
        if not isinstance(value, torch.Tensor):
            raise TypeError(f"{type(record).__name__}.{item.name} is not a tensor")
        values[item.name] = value[index : index + 1].clone()
    return type(record)(**values)


def lane_batch(batch: UniversalSemanticBatchV4, lane: int) -> UniversalSemanticBatchV4:
    """Slice one lane back to the exact record the per-lane tensorizer emits.

    Every fixed-size section keeps its V4 capacity; the relation edge set is
    trimmed to that lane's live rows, because the per-lane path sizes the
    record to ``max(1, count)``.  This is the digest gate's projection, not a
    training-loop path.
    """

    index = int(lane)
    count = max(1, int(batch.relation_edges.mask[index].sum()))
    return UniversalSemanticBatchV4(
        match_scalars=batch.match_scalars[index : index + 1].clone(),
        own_cards=_slice_record(batch.own_cards, index),
        opponent_cards=_slice_record(batch.opponent_cards, index),
        towers=_slice_record(batch.towers, index),
        groups=_slice_record(batch.groups, index),
        active_effects=_slice_record(batch.active_effects, index),
        events=_slice_record(batch.events, index),
        relation_edges=RelationEdgesV4(
            source=batch.relation_edges.source[index : index + 1, :count].clone(),
            target=batch.relation_edges.target[index : index + 1, :count].clone(),
            relation_type=batch.relation_edges.relation_type[index : index + 1, :count].clone(),
            mask=batch.relation_edges.mask[index : index + 1, :count].clone(),
        ),
        explicit_spatial_planes=batch.explicit_spatial_planes[index : index + 1].clone(),
        previous_action=_slice_record(batch.previous_action, index),
        candidates=_slice_record(batch.candidates, index),
    )


def batch_digest(batch: Any) -> str:
    """A stable SHA-256 over every tensor in a tensorized record.

    Used to prove the facts-only path is bit-identical to the stock tensorizer
    on the same decision sequence, not merely "same shape".
    """

    hasher = hashlib.sha256()

    def walk(value: Any) -> None:
        if isinstance(value, torch.Tensor):
            hasher.update(str(value.dtype).encode())
            hasher.update(str(tuple(value.shape)).encode())
            hasher.update(value.detach().cpu().contiguous().numpy().tobytes())
            return
        if is_dataclass(value) and not isinstance(value, type):
            for field in fields(value):
                hasher.update(field.name.encode())
                walk(getattr(value, field.name))
            return
        hasher.update(repr(value).encode())

    walk(batch)
    return hasher.hexdigest()


def compare_semantic_batches(actual: Any, expected: Any, *, path: str = "") -> list[str]:
    """Field-by-field differences between two tensorized records.

    A digest only says "different"; this names the first differing field so a
    gate can report where the facts-only path drifted.
    """

    mismatches: list[str] = []

    def walk(left: Any, right: Any, name: str) -> None:
        if len(mismatches) >= 20:
            return
        if isinstance(left, torch.Tensor) and isinstance(right, torch.Tensor):
            if left.dtype != right.dtype:
                mismatches.append(f"{name}.dtype {left.dtype} != {right.dtype}")
            elif tuple(left.shape) != tuple(right.shape):
                mismatches.append(f"{name}.shape {tuple(left.shape)} != {tuple(right.shape)}")
            elif not torch.equal(left, right):
                mismatches.append(f"{name}.values")
            return
        if is_dataclass(left) and is_dataclass(right) and type(left) is type(right):
            for field in fields(left):
                walk(getattr(left, field.name), getattr(right, field.name), f"{name}.{field.name}".lstrip("."))
            return
        if left != right:
            mismatches.append(f"{name} {left!r} != {right!r}")

    walk(actual, expected, path)
    return mismatches


def lane_sequence(sequence: ActionSequenceV4, lane: int) -> ActionSequenceV4:
    """One lane of a stacked action sequence, as the stock method expects."""

    return _slice_record(sequence, lane)


__all__ = [
    "VectorTensorSections",
    "VectorTensorizer",
    "batch_digest",
    "compare_semantic_batches",
    "lane_batch",
    "lane_sequence",
]
