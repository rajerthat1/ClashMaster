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
* the group build at live width vs the previous full-capacity assembly
  (``VectorGroupBuilder.build(..., live_slots=False)`` is the oracle), which is
  where the padded 48/160 slots were being paid for;
* ``record_action``: the vectorised NumPy pass vs the per-lane loop it replaced
  (the loop is one device sync per field per row on CUDA, which is what made the
  box's ``decode`` bucket 68 ms at 128 lanes).

    OMP_NUM_THREADS=4 .venv/bin/python tools/crforge/cpu_half_micro.py \
        --count 128 --repeats 20 --output runs/crforge-c4/cpu-half-micro.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import time
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

    spec = importlib.util.spec_from_file_location("crforge_self_play", _REPO_ROOT / "tools" / "crforge" / "self_play.py")
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

        # A decision that actually plays: open the game, then let it settle so
        # candidates exist (otherwise the record and decode loops are empty).
        for _ in range(60):
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
        for _ in range(200):
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
        entity_rows = groups._entity_rows(batch)
        order = groups._orders(entity_rows)
        g = groups.max_groups
        slots = order[:, :g]
        values = entity_rows["hp"]
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
        assert _records_equal(wide_section, live_section)
        entity_counts = batch.entity_valid.sum(axis=1)
        build = {
            "full_capacity_ms": _time(lambda: groups.build(batch, live_slots=False), args.repeats),
            "live_slots_ms": _time(lambda: groups.build(batch, live_slots=True), args.repeats),
            "entity_rows_ms": _time(lambda: groups._entity_rows(batch), args.repeats),
            "live_entities_mean": float(entity_counts.mean()),
            "live_entities_max": int(entity_counts.max()),
            "equal_values": True,
        }
        build["saved_ms"] = build["full_capacity_ms"] - build["live_slots_ms"]

        # ---- table gather + mask ------------------------------------------
        # ``_entity_rows`` gathers nine per-vocabulary tables through the
        # entity row map and zeroes the invalid rows.  Masking the gather's own
        # copy in place saves one allocation per field; measured here it saves
        # only ~0.12 ms over nine fields, below the section's noise, so the
        # producer keeps the single-expression ``where`` form.
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
        batch_stub = SimpleNamespace(count=int(batch.count), batch_size=int(batch.count), candidates=stacks[0].candidates)
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
