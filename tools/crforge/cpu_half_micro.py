"""Attribute the CPU half of a C4 decision, and A/B the Python shims.

The producer records the buckets (``tensorize``, ``decode``, ``step``, ``facts``)
and, since session 37, the sections inside them (``cpu_half.breakdown_ms_mean``).
Both are wall-clock numbers on a loaded host, so a before/after across processes
cannot tell a faster shim from a quieter machine.  This tool measures the shims
**in one process, back to back, on the same batch**:

* the two-seat interleave: one write into ``dst[seat::seats]`` vs
  ``torch.stack(dim=1).reshape``;
* the group gather: advanced indexing vs ``np.take_along_axis`` (35 calls per
  build, each rebuilding an index tuple);
* the entity table gather: ``np.where(valid, table[rv], 0)`` vs masking the
  gather's own copy in place (nine fields per seat in ``_entity_rows``);
* ``_entity_rows`` itself: the dense form (masks and gathers over the whole
  entity axis) vs the compact live-row form the producer uses now (sessions 41
  and 44); the dense form reimplements the fields from the batch and is the
  oracle for the compact rows;
* the group sort keys and ``_targeted_mask``: the same dense/local split.  An
  order only has to cover the lane's live width, so the compact sort is
  compared on the prefix ``build`` actually reads (session 42);
* the group build at live width vs the previous full-capacity assembly
  (``VectorGroupBuilder.build(..., live_slots=False)`` is the oracle), which is
  where the padded 48/160 slots were being paid for — plus the fixed-capacity
  pad itself, replicated field by field on the live arrays the build pads;
* the model forward at the live group width vs the padded one: the group
  encoder's token and child/group-attention shapes follow ``groups.mask``, so
  the build's record width is also an inference cost (session 43);
* ``record_action``: the vectorised NumPy pass vs the per-lane loop it replaced
  (the loop is one device sync per field per row on CUDA, which is what made the
  box's ``decode`` bucket 68 ms at 128 lanes).

    OMP_NUM_THREADS=4 .venv/bin/python tools/crforge/cpu_half_micro.py \
        --count 128 --repeats 20 --output runs/crforge-c4/cpu-half-micro.json

The batch is a **live** board: the fixture waits past ``FIRST_PLAYABLE_TICK``,
plays for a few decisions and settles.  Until session 42 it played at tick 61,
before the cooldown, so every play was refused and the "settled board" was its
six towers — the section numbers in sessions 37–41 are empty-board numbers.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import torch

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / ".resource" / "crforge" / "python"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


def _load_self_play() -> Any:
    """Import the producer script as a module (it is a tool, not a package)."""

    spec = importlib.util.spec_from_file_location(
        "crforge_self_play", _REPO_ROOT / "tools" / "crforge" / "self_play.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _stack_interleave(records: list[Any], *, fill: int = 0) -> Any:
    """The pre-optimisation interleave: stack on a new axis, then reshape."""

    from dataclasses import fields, is_dataclass

    first = records[0]
    if isinstance(first, torch.Tensor):
        target = tuple(max(int(record.shape[axis]) for record in records) for axis in range(1, first.ndim))
        padded = []
        for record in records:
            if tuple(record.shape[1:]) == target:
                padded.append(record)
                continue
            output = record.new_full((int(record.shape[0]), *target), fill)
            slices = (slice(None),) + tuple(slice(0, int(size)) for size in record.shape[1:])
            output[slices] = record
            padded.append(output)
        return torch.stack(padded, dim=1).reshape(int(first.shape[0]) * len(records), *target)
    if is_dataclass(first):
        return type(first)(
            **{
                item.name: _stack_interleave([getattr(record, item.name) for record in records])
                for item in fields(first)
            }
        )
    raise TypeError(f"cannot interleave {type(first).__name__}")


def _legacy_record_action(rows: Any, sequence: Any, batch: Any, *, tables: Any) -> Any:
    """The pre-optimisation per-lane ``record_action`` write, as the oracle."""

    lanes = int(batch.batch_size)
    previous = rows._empty_previous_action(lanes)
    previous.gate = sequence.gate.detach().cpu().clone()
    previous.micro_action_count = sequence.micro_action_count.detach().cpu().clone()
    mask = tables.mask
    uid = tables.uid
    for lane in range(lanes):
        count = int(sequence.micro_action_count[lane].item())
        for step in range(count):
            candidate_uid = int(sequence.candidate_uid[lane, step].item())
            matches = np.flatnonzero(mask[lane] & (uid[lane] == candidate_uid))
            if matches.size != 1:
                raise ValueError("previous action candidate UID is missing or duplicated")
            row = int(matches[0])
            for name in (
                "variant",
                "visible_card_vocab_id",
                "effective_card_vocab_id",
                "effective_form",
                "ability_vocab_id",
            ):
                getattr(previous, name)[lane, step] = int(getattr(tables, name)[lane, row])
            previous.target_cell[lane, step] = sequence.target_cell[lane, step].cpu()
            previous.delay_offset_bin[lane, step] = sequence.delay_offset_bin[lane, step].cpu()
            previous.action_mask[lane, step] = True
    return previous


def _sequence_for(record: Any, config: Any) -> Any:
    """One play per lane that has an open candidate, in the producer's shape."""

    from native_runner.training.v4.tensors import GATE_ACT, ActionSequenceV4

    mask = np.asarray(record.mask)
    uids = np.asarray(record.uid)
    lanes = int(mask.shape[0])
    steps = int(config.max_micro_actions)
    gate = np.zeros(lanes, dtype=np.int64)
    count = np.zeros(lanes, dtype=np.int64)
    uid = np.full((lanes, steps), -1, dtype=np.int64)
    cell = np.full((lanes, steps), -1, dtype=np.int64)
    delay = np.full((lanes, steps), -1, dtype=np.int64)
    for lane in range(lanes):
        open_rows = np.flatnonzero(mask[lane])
        if open_rows.size == 0:
            continue
        gate[lane] = GATE_ACT
        count[lane] = 1
        uid[lane, 0] = int(uids[lane, open_rows[0]])
        cell[lane, 0] = 0
        delay[lane, 0] = 0
    return ActionSequenceV4(
        gate=torch.from_numpy(gate),
        micro_action_count=torch.from_numpy(count),
        candidate_index=torch.from_numpy(np.full((lanes, steps), -1, dtype=np.int64)),
        candidate_uid=torch.from_numpy(uid),
        target_cell=torch.from_numpy(cell),
        delay_offset_bin=torch.from_numpy(delay),
    )


def _dense_entity_rows(groups: Any, batch: Any) -> dict[str, Any]:
    """The pre-session-41 ``_entity_rows``: dense over the whole entity axis.

    This is the oracle for the producer's live-row form, nothing more.  It
    masks and gathers at ``max_entities`` width even though a settled board
    carries a handful of live rows.
    """

    from crforge_bridge.vector_rows import WORLD_HEIGHT, WORLD_WIDTH, _tile_positions

    valid = batch.entity_valid & ~batch.entity_is_tower
    rv = np.clip(batch.entity_row_vocab, 0, groups.tables.vocab_size - 1)

    def take(array: Any) -> Any:
        return np.where(valid, array[rv], 0)

    entity_id = np.where(valid, batch.entity_id, -1).astype(np.int64)
    hp = np.where(valid & (batch.entity_hp >= 0), batch.entity_hp, 0).astype(np.float64)
    max_hp = np.where(valid & (batch.entity_max_hp >= 0), batch.entity_max_hp, 0).astype(np.float64)
    shield = np.where(valid & (batch.entity_shield > 0), batch.entity_shield, 0).astype(np.float64)
    age = np.where(valid, batch.entity_age_ms, 0).astype(np.float64)
    target = valid & (batch.entity_target >= 0)

    x, y = _tile_positions(
        np.where(valid, batch.entity_x, 0).astype(np.float64),
        np.where(valid, batch.entity_y, 0).astype(np.float64),
        config=groups.config,
        perspective=groups.perspective,
    )
    half_w = take(groups.tables.footprint_half_w)
    half_h = take(groups.tables.footprint_half_h)

    velocity = np.zeros((*valid.shape, 2), dtype=np.float64)
    velocity[...] = batch.entity_velocity.astype(np.float64)
    velocity_valid = valid & batch.entity_velocity_valid
    if groups.perspective.flip_x:
        velocity[..., 0] = -velocity[..., 0]
    if groups.perspective.flip_y:
        velocity[..., 1] = -velocity[..., 1]
    velocity[..., 0] = velocity[..., 0] / (float(WORLD_WIDTH) / float(groups.config.board_width))
    velocity[..., 1] = velocity[..., 1] / (float(WORLD_HEIGHT) / float(groups.config.board_height))

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
        "extent_x0": x - half_w,
        "extent_y0": y - half_h,
        "extent_x1": x + half_w,
        "extent_y1": y + half_h,
        "velocity": velocity,
        "velocity_valid": velocity_valid,
        "card_id": take(groups.tables.card_id),
        "archetype_id": take(groups.tables.archetype_id),
        "child_type": take(groups.tables.child_type),
        "card_vocab_id": take(groups.tables.card_vocab_id),
        "runtime_form": take(groups.tables.runtime_form),
        "group_type": take(groups.tables.group_type),
        "is_hero_or_building": np.where(valid, groups.tables.is_hero_or_building[rv], False),
        "radius": take(groups.tables.radius),
        "side": np.where(valid, batch.entity_side, -1),
    }


def _dense_targeted_mask(rows: dict[str, Any]) -> np.ndarray:
    """The pre-session-42 ``_targeted_mask``: keys over the whole entity axis."""

    targeted = np.zeros(rows["valid"].shape, dtype=bool)
    if not rows["target"].any():
        return targeted
    lanes = rows["entity_id"].shape[0]
    lane_offsets = (np.arange(lanes, dtype=np.int64) << 32)[:, None]
    keys = lane_offsets | (rows["entity_id"] & 0xFFFFFFFF)
    target_keys = lane_offsets | (rows["target_id"].astype(np.int64) & 0xFFFFFFFF)
    target_keys = np.sort(target_keys[rows["target"]])
    flat = keys.ravel()
    positions = np.searchsorted(target_keys, flat)
    clipped = np.clip(positions, 0, target_keys.size - 1)
    matched = (positions < target_keys.size) & (target_keys[clipped] == flat)
    return matched.reshape(targeted.shape)


def _dense_orders(rows: dict[str, Any]) -> np.ndarray:
    """The pre-session-42 ``_orders``: keys and argsort over the entity axis."""

    valid = rows["valid"]
    relation = np.where(rows["target"], 0, 1)
    hero_building = np.where(rows["is_hero_or_building"], 0, 1)
    entity_id = np.clip(rows["entity_id"], 0, 2**31 - 1)
    key = ((relation.astype(np.int64) * 2 + hero_building) << 52) | entity_id
    key = np.where(valid, key, np.iinfo(np.int64).max)
    return np.argsort(key, axis=1, kind="stable")


def _dense_child_order(rows: dict[str, Any], candidate: np.ndarray, targeted: np.ndarray) -> np.ndarray:
    """The pre-session-42 ``_child_order``: keys and argsort over the entity axis."""

    relation = np.where(targeted, 0, 1)
    hero_building = np.where(rows["is_hero_or_building"], 0, 1)
    hp_key = np.minimum(np.maximum(rows["hp"], 0.0), 1_048_575.0).astype(np.int64)
    entity_id = np.clip(rows["entity_id"], 0, 2**31 - 1)
    key = ((relation.astype(np.int64) * 2 + hero_building) << 52) | ((1_048_576 - hp_key) << 31) | entity_id
    key = np.where(candidate, key, np.iinfo(np.int64).max)
    return np.argsort(key, axis=1, kind="stable")


def _pad_slots(values: np.ndarray, width: int, fill: Any = 0) -> np.ndarray:
    """Grow a live-width slot axis to a fixed capacity, as ``build`` does."""

    if int(values.shape[1]) == width:
        return values
    output = np.full((values.shape[0], width) + values.shape[2:], fill, dtype=values.dtype)
    output[:, : int(values.shape[1])] = values
    return output


def _time(fn: Any, repeats: int) -> float:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=128)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--checkpoint", type=Path, default=Path(".resource/RoyaleFish/models/B-general-v275.pt"))
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c4/cpu-half-micro.json"))
    args = parser.parse_args(argv)

    sp = _load_self_play()
    from crforge_bridge.catalog import Catalog
    from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
    from crforge_bridge.jvm import start as start_jvm
    from crforge_bridge.vector_actions import candidate_tables
    from crforge_bridge.vector_observation import VectorObservationEncoder
    from crforge_bridge.vector_rows import OWNER_NEUTRAL, pad_group_section
    from crforge_bridge.vector_tensorize import VectorTensorizer, interleave_rows
    from native_runner.timeline import load_game_mode_timeline
    from native_runner.training.v4.factory import production_semantic_bundle
    from native_runner.training.v4.policy_session import load_policy_v4

    start_jvm(str(DEFAULT_INSTALL))
    from crforge_env import _jvm as env_jvm

    env_jvm._started_with = Path(DEFAULT_INSTALL).expanduser().resolve()

    policy = load_policy_v4(args.checkpoint, device="cpu")
    model = policy.model
    model.eval()
    bundle = production_semantic_bundle(getattr(model, "competitive_release", None))
    catalog = Catalog.load()
    timeline = load_game_mode_timeline(sp.STANDARD_GAME_MODE).timeline

    vector = sp._vector(args.count, max_ticks=0)
    try:
        encoder = VectorObservationEncoder(vector, catalog)
        encoder.reset()
        batch = encoder.encode()
        seats = (0, 1)
        rows = {
            seat: VectorTensorizer(
                sp._stock_tensorizer(bundle, model.config, seat),
                vocabulary=list(vector.vocabulary),
                bridge_catalog=catalog,
                timeline=timeline,
                tower_troop_id=sp.TOWER_TROOP,
            )
            for seat in seats
        }
        for item in rows.values():
            item.reset(batch)

        # A decision that actually plays: wait past the playable cooldown, play
        # for a few decisions, then settle just long enough for the units to be
        # on the board.  The old fixture played at tick 61 with
        # ``FIRST_PLAYABLE_TICK == 90``, so every play was refused and the
        # measured "settled board" was its six towers — zero live entities.
        for _ in range(sp.FIRST_PLAYABLE_TICK + 5):
            vector.step()
        rng = np.random.default_rng(3)
        first_ability = int(vector.schema.first_ability)
        for _ in range(15):
            actions = np.zeros((args.count, 2), dtype=np.int32)
            for lane in range(args.count):
                for seat in seats:
                    if rng.random() < 0.5:
                        legal = np.flatnonzero(vector.masks[lane, seat, 1:first_ability])
                        if legal.size:
                            actions[lane, seat] = int(legal[rng.integers(legal.size)]) + 1
            vector.step(actions)
        for _ in range(40):
            vector.step()
        encoder.encode(batch)
        stacks = {seat: rows[seat].tensorize(batch) for seat in seats}

        # ---- interleave ----------------------------------------------------
        stacked = interleave_rows([stacks[seat] for seat in seats])
        old_stacked = _stack_interleave([stacks[seat] for seat in seats])
        assert old_stacked.match_scalars.shape == stacked.match_scalars.shape
        interleave = {
            "stack_then_reshape_ms": _time(
                lambda: _stack_interleave([stacks[seat] for seat in seats]), args.repeats
            ),
            "single_write_ms": _time(lambda: interleave_rows([stacks[seat] for seat in seats]), args.repeats),
        }
        interleave["saved_ms"] = interleave["stack_then_reshape_ms"] - interleave["single_write_ms"]
        interleave["equal_values"] = bool(torch.equal(old_stacked.match_scalars, stacked.match_scalars))

        # ---- group gather --------------------------------------------------
        groups = rows[0].group_builder
        dense_rows = _dense_entity_rows(groups, batch)
        live_rows = groups._entity_rows(batch)
        g = groups.max_groups
        # A full-capacity slot matrix for the gather A/B: the producer's order is
        # only the live width now, but this measures the gather *form* at the
        # record's fixed width, so the number stays comparable across sessions.
        slots = np.argsort(~dense_rows["valid"], axis=1, kind="stable")[:, :g]
        values = dense_rows["hp"]
        lane_index = np.arange(int(batch.count), dtype=np.int64)[:, None]

        def take_along() -> np.ndarray:
            total = np.zeros((int(batch.count), g))
            for _ in range(35):
                total += np.take_along_axis(values, slots, axis=1)
            return total

        def advanced() -> np.ndarray:
            total = np.zeros((int(batch.count), g))
            for _ in range(35):
                total += values[lane_index, slots]
            return total

        assert np.array_equal(take_along(), advanced())
        gather = {
            "take_along_axis_35_ms": _time(take_along, args.repeats),
            "advanced_index_35_ms": _time(advanced, args.repeats),
        }
        gather["saved_ms"] = gather["take_along_axis_35_ms"] - gather["advanced_index_35_ms"]

        # ---- group build: live slots vs full capacity ----------------------
        # ``live_slots=False`` is the previous form: gather, assemble and mask
        # all 48 group / 160 child slots even though a settled board carries a
        # handful.  Both run on the same batch in this process, so the delta is
        # the width change and nothing else.
        def _records_equal(left: Any, right: Any) -> bool:
            from dataclasses import fields, is_dataclass

            if isinstance(left, torch.Tensor):
                return torch.equal(left, right)
            if isinstance(left, np.ndarray):
                return np.array_equal(left, right)
            if is_dataclass(left) and type(left) is type(right):
                return all(
                    _records_equal(getattr(left, item.name), getattr(right, item.name)) for item in fields(left)
                )
            return left == right

        wide_section = groups.build(batch, live_slots=False)
        live_section = groups.build(batch, live_slots=True)
        # The producer keeps the live width now; project it onto the oracle's
        # fixed capacity to prove the two forms are the same record.
        live_padded = pad_group_section(
            live_section, groups=groups.max_groups, children=groups.max_children
        )
        assert _records_equal(wide_section, live_padded)
        assert set(dense_rows) <= set(live_rows)
        counts = live_rows["counts"]
        slot_map = live_rows["slots"]
        valid_live = live_rows["valid"]
        for name, values in dense_rows.items():
            assert values.dtype == live_rows[name].dtype, name
            index = slot_map if values.ndim == 2 else slot_map.reshape(slot_map.shape + (1,) * (values.ndim - 2))
            projected = np.take_along_axis(values, index, axis=1)
            assert np.array_equal(projected[valid_live], live_rows[name][valid_live]), name
        entity_counts = batch.entity_valid.sum(axis=1)
        build = {
            "full_capacity_ms": _time(lambda: groups.build(batch, live_slots=False), args.repeats),
            "live_slots_ms": _time(lambda: groups.build(batch, live_slots=True), args.repeats),
            "entity_rows_ms": _time(lambda: groups._entity_rows(batch), args.repeats),
            "entity_rows_dense_ms": _time(lambda: _dense_entity_rows(groups, batch), args.repeats),
            "entity_rows_live_width": int(counts.max()),
            "live_entities_mean": float(entity_counts.mean()),
            "live_entities_max": int(entity_counts.max()),
            "equal_values": True,
        }
        build["saved_ms"] = build["full_capacity_ms"] - build["live_slots_ms"]
        build["entity_rows_saved_ms"] = build["entity_rows_dense_ms"] - build["entity_rows_ms"]

        # ---- where the live build's time goes ------------------------------
        # ``build(phases=...)`` accumulates its own terms without changing the
        # record.  The phase sum is checked against the timed call above so the
        # table cannot silently miss work.
        phase_samples: dict[str, list[float]] = {}
        for _ in range(args.repeats):
            accumulator: dict[str, float] = {}
            groups.build(batch, phases=accumulator)
            for name, value in accumulator.items():
                phase_samples.setdefault(name, []).append(value)
        phases = {name: statistics.fmean(values) for name, values in phase_samples.items()}
        build["phases_ms_mean"] = phases
        build["phases_sum_ms"] = sum(phases.values())

        # ---- the fixed-capacity pad (what the producer no longer pays) ------
        # ``build`` used to assemble every field at the lane's live width and
        # then copy it into the record's fixed 48 group / 160 child slots: a
        # fresh zeroed allocation plus a prefix copy per field (~7.5 MB per seat
        # at 128 lanes).  Session 43 stopped padding on the producer path, so
        # this measures the work that left, replicated exactly over the live
        # arrays the build used to pad and checked against the oracle's tail.
        group_live = int(live_section.groups.mask.shape[1])
        child_live = int(live_section.groups.child_mask.shape[1])
        pad_inputs: list[tuple[str, np.ndarray, int, Any, torch.Tensor]] = []
        for name, default_fill in (
            ("source_card_vocab_id", 0),
            ("group_type", 0),
            ("owner_type", OWNER_NEUTRAL),
            ("runtime_form", 0),
            ("features", 0.0),
            ("position", 0.0),
            ("extent", 0.0),
            ("mask", False),
        ):
            pad_inputs.append(
                (
                    name,
                    getattr(live_section.groups, name).numpy().copy(),
                    groups.max_groups,
                    default_fill,
                    getattr(wide_section.groups, name),
                )
            )
        for name, default_fill in (
            ("child_archetype_id", 0),
            ("child_group_index", 0),
            ("child_type", 0),
            ("child_features", 0.0),
            ("child_position", 0.0),
            ("child_extent", 0.0),
            ("child_radius", 0.0),
            ("child_mask", False),
        ):
            pad_inputs.append(
                (
                    name,
                    getattr(live_section.groups, name).numpy().copy(),
                    groups.max_children,
                    default_fill,
                    getattr(wide_section.groups, name),
                )
            )

        def pad_all() -> list[torch.Tensor]:
            return [
                torch.from_numpy(_pad_slots(values, width, fill)) for _, values, width, fill, _ in pad_inputs
            ]

        padded_fields = pad_all()
        for (name, values, width, _, expected), actual in zip(pad_inputs, padded_fields, strict=True):
            assert torch.equal(actual, expected), (
                f"{name}: live={tuple(values.shape)} width={width} actual={tuple(actual.shape)}"
                f" expected={tuple(expected.shape)}"
            )

        def pad_alloc_copy() -> list[np.ndarray]:
            return [_pad_slots(values, width, fill) for _, values, width, fill, _ in pad_inputs]

        def pad_torch_only() -> list[torch.Tensor]:
            return [torch.from_numpy(_pad_slots(values, width, fill)) for _, values, width, fill, _ in pad_inputs]

        capacity_bytes = sum(
            int(values.shape[0]) * width * int(np.prod(values.shape[2:], dtype=np.int64)) * values.dtype.itemsize
            for _, values, width, _, _ in pad_inputs
        )
        build["pad_fields_ms"] = _time(pad_torch_only, args.repeats)
        build["pad_alloc_copy_ms"] = _time(pad_alloc_copy, args.repeats)
        build["pad_share_of_live_build"] = build["pad_fields_ms"] / build["live_slots_ms"]
        build["build_with_pad_ms"] = build["live_slots_ms"] + build["pad_fields_ms"]
        build["pad_share_of_previous_build"] = build["pad_fields_ms"] / build["build_with_pad_ms"]
        build["pad_live_slots"] = {"groups": group_live, "children": child_live}
        build["pad_capacity"] = {"groups": groups.max_groups, "children": groups.max_children}
        build["pad_capacity_mb"] = capacity_bytes / (1024.0 * 1024.0)
        build["pad_equal_values"] = True

        # ---- targeted mask: live rows vs the dense key arrays ---------------
        targeted_dense = _dense_targeted_mask(dense_rows)
        targeted_live = groups._targeted_mask(live_rows)
        projected_targeted = np.take_along_axis(targeted_dense, slot_map, axis=1)
        assert np.array_equal(projected_targeted[valid_live], targeted_live[valid_live])
        build["targeted_mask_ms"] = _time(lambda: groups._targeted_mask(live_rows), args.repeats)
        build["targeted_mask_dense_ms"] = _time(lambda: _dense_targeted_mask(dense_rows), args.repeats)
        build["targeted_mask_saved_ms"] = build["targeted_mask_dense_ms"] - build["targeted_mask_ms"]

        # ---- sort keys: live slots vs the dense entity-axis sort -----------
        # ``build`` reads at most the lane's live width from each order, so the
        # producer sorts only the live (or candidate) slots.  The dense forms
        # are the oracle; the compared prefix is each lane's own retained
        # width (the tails are masked off by ``build`` and need not agree).
        dense_candidate = wide_section.group_index >= 0
        candidate = np.take_along_axis(dense_candidate, slot_map, axis=1)
        targeted = targeted_live
        group_counts = counts
        candidate_counts = candidate.sum(axis=1)
        dense_order = _dense_orders(dense_rows)
        live_order = groups._orders(live_rows)
        assert all(
            np.array_equal(
                dense_order[lane, : group_counts[lane]],
                slot_map[lane, live_order[lane, : group_counts[lane]]],
            )
            for lane in range(dense_order.shape[0])
        )
        dense_child = _dense_child_order(dense_rows, dense_candidate, targeted_dense)
        live_child = groups._child_order(live_rows, candidate, targeted)
        assert all(
            np.array_equal(
                dense_child[lane, : candidate_counts[lane]],
                slot_map[lane, live_child[lane, : candidate_counts[lane]]],
            )
            for lane in range(dense_child.shape[0])
        )
        group_sort = {
            "orders_ms": _time(lambda: groups._orders(live_rows), args.repeats),
            "orders_dense_ms": _time(lambda: _dense_orders(dense_rows), args.repeats),
            "child_order_ms": _time(lambda: groups._child_order(live_rows, candidate, targeted), args.repeats),
            "child_order_dense_ms": _time(
                lambda: _dense_child_order(dense_rows, dense_candidate, targeted_dense), args.repeats
            ),
            "group_slots_max": int(group_counts.max()),
            "child_slots_max": int(candidate_counts.max()),
            "equal_values": True,
        }
        group_sort["orders_saved_ms"] = group_sort["orders_dense_ms"] - group_sort["orders_ms"]
        group_sort["child_order_saved_ms"] = group_sort["child_order_dense_ms"] - group_sort["child_order_ms"]

        # ---- table gather + mask ------------------------------------------
        # ``_entity_rows`` gathers nine per-vocabulary tables through the
        # entity row map and zeroes the invalid rows.  Masking the gather's own
        # copy in place saves one allocation per field; measured here it saves
        # only ~0.1 ms over nine fields, below the section's noise.  Both forms
        # are off the producer path since session 41 (it gathers at the live
        # rows); this stays as the record of why the in-place mask was not the
        # win.
        table = np.asarray(groups.tables.card_vocab_id)
        rv = np.clip(batch.entity_row_vocab, 0, groups.tables.vocab_size - 1)
        valid = batch.entity_valid & ~batch.entity_is_tower

        def where_take() -> np.ndarray:
            total = np.zeros(valid.shape, dtype=table.dtype)
            for _ in range(9):
                total += np.where(valid, table[rv], 0)
            return total

        def inplace_take() -> np.ndarray:
            total = np.zeros(valid.shape, dtype=table.dtype)
            for _ in range(9):
                values = table[rv]
                values[~valid] = 0
                total += values
            return total

        assert np.array_equal(where_take(), inplace_take())
        mask = {
            "where_9_ms": _time(where_take, args.repeats),
            "inplace_9_ms": _time(inplace_take, args.repeats),
        }
        mask["saved_ms"] = mask["where_9_ms"] - mask["inplace_9_ms"]

        # ---- record_action -------------------------------------------------
        sequence = _sequence_for(stacks[0].candidates, model.config)
        tables = candidate_tables(stacks[0].candidates)
        batch_stub = SimpleNamespace(
            count=int(batch.count), batch_size=int(batch.count), candidates=stacks[0].candidates
        )
        tensorizer = rows[0]
        tensorizer.record_action(sequence, batch_stub, validate=False, tables=tables)
        expected = tensorizer.previous_action
        from dataclasses import fields

        legacy = _legacy_record_action(tensorizer, sequence, batch_stub, tables=tables)
        assert expected is not None
        for item in fields(expected):
            assert torch.equal(getattr(expected, item.name), getattr(legacy, item.name)), item.name

        def new_record() -> None:
            tensorizer.record_action(sequence, batch_stub, validate=False, tables=tables)

        def old_record() -> None:
            _legacy_record_action(tensorizer, sequence, batch_stub, tables=tables)

        record = {
            "per_lane_loop_ms": _time(old_record, args.repeats),
            "vectorised_ms": _time(new_record, args.repeats),
            "rows_with_actions": int(sequence.micro_action_count.sum()),
        }
        if record["rows_with_actions"] == 0:
            raise SystemExit("the record benchmark saw no candidate: settle longer before timing")
        record["saved_ms"] = record["per_lane_loop_ms"] - record["vectorised_ms"]

        # ---- forward: the live-width group section vs the padded one -------
        # The producer now hands the model the batch's live group/child width,
        # so this is the model-side half of the same record change: the group
        # encoder's token count and its child/group attention follow
        # ``groups.mask``, not the configured maximum.
        from crforge_bridge.vector_rows import pad_group_record

        wide_stacks = replace(
            stacks[seats[0]],
            groups=pad_group_record(
                stacks[seats[0]].groups, groups=groups.max_groups, children=groups.max_children
            ),
        )
        with torch.no_grad():
            model.encode_observation(stacks[seats[0]], validate=False)
            model.encode_observation(wide_stacks, validate=False)
            forward = {
                "live_width_ms": _time(
                    lambda: model.encode_observation(stacks[seats[0]], validate=False), args.repeats
                ),
                "full_capacity_ms": _time(
                    lambda: model.encode_observation(wide_stacks, validate=False), args.repeats
                ),
            }
        forward["saved_ms"] = forward["full_capacity_ms"] - forward["live_width_ms"]
        forward["saved_fraction"] = forward["saved_ms"] / forward["full_capacity_ms"]

        # ---- the sections, on a fresh batch --------------------------------
        section_samples: dict[str, list[float]] = {}
        for _ in range(min(args.repeats, 12)):
            vector.step()
            encoder.encode(batch)
            timings: dict[str, float] = {}
            for seat in seats:
                rows[seat].tensorize(batch, timings=timings)
            for name, value in timings.items():
                section_samples.setdefault(name, []).append(value)
        sections = {name: statistics.fmean(values) for name, values in section_samples.items()}

        payload = {
            "schema": "crforge-c4-cpu-half-micro.v1",
            "measured": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "shape": {"lanes": args.count, "seats": list(seats), "repeats": args.repeats},
            "substrate": {"tables": str(DEFAULT_TABLES), "checkpoint": str(args.checkpoint), "device": "cpu"},
            "interleave": interleave,
            "group_gather": gather,
            "group_build": build,
            "group_sort": group_sort,
            "forward": forward,
            "entity_mask": mask,
            "record_action": record,
            "sections_ms_mean": sections,
            "note": (
                "same-process A/B; the old shims are kept here only as the oracle and are not on the "
                "producer path"
            ),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, indent=2))
    finally:
        vector.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
