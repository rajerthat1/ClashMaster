"""Measure the vectorized observation facts against the bridge's per-lane path.

The C1 spike measured a *private* ``PolicyFrame`` built from the vector and
proved the per-lane facts are ~19x cheaper when vectorized. C3-B needs more
than that frame: it needs every fact ``ObservationV1`` carries (both seats'
headers and private card rows, all six towers, the native placement rows) plus
the derived entity state the observation layer normally tracks (age, velocity).
``crforge_bridge.vector_observation`` is that layer; this tool is its gate.

It runs one ``VectorBattle``, encodes the same vector two ways -- the batch
encoder and the bridge's per-lane ``decode_rows`` / ``player_facts`` /
``entity_rows`` -- asserts they agree on a tick, then times them paired over
``--steps`` decisions and ``--repeats`` trials.  Paired timing matters: this
host's absolute numbers move ~2x with background load, so the per-lane ratio is
the only stable comparison.

    .venv/bin/python tools/crforge/vector_observation_spike.py \
        --count 256 --threads 1 8 --steps 200 --repeats 3 \
        --per-lane-lanes 16 --output runs/crforge-c3/vector-observation.json

The gate this tool can move is the *facts* half (C1 already passes it).  It does
not tensorize: the C3-B row build is measured by
``tools/crforge/profile_tensorizer.py`` and its own harness once it exists.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / ".resource" / "crforge" / "python"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import numpy as np
from crforge_env.vector import LadderConfig, SideSetup, VectorBattle

from crforge_bridge.catalog import Catalog
from crforge_bridge.jvm import DEFAULT_INSTALL, DEFAULT_TABLES
from crforge_bridge.observe import decode_rows, entity_rows, player_facts
from crforge_bridge.vector_observation import (
    VectorObservationEncoder,
    VocabularyView,
    _SchemaView,
    compare_lane_facts,
)

# The same locked learner deck the C1 spike and the learner-deck rollout use,
# so the entity mix (forms, a hero, spells) is the deck the bulk path trains on.
DECK = ("Xbow", "Tesla", "Archer", "Knight", "Skeletons", "ElectroSpirit", "Fireball", "Log")
DECK_SLOTS = (0, 1, 1, 2, 0, 0, 0, 0)
TOWERS = "King_PrincessTowers"
DEFAULT_LEVEL = 16
PLAY_RATE = 0.3


def _priority(n: int) -> np.ndarray:
    return np.random.default_rng(12345).permutation(n).astype(np.int32)


_PRIORITY: dict[int, np.ndarray] = {}


def draw_actions(masks: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """A random open play per seat with PLAY_RATE, else the wait.

    Same sampler the C1 spike uses, so the two artifacts describe one workload.
    """

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


class PerLaneFacts:
    """The control: the bridge's own decoders, one lane at a time."""

    def __init__(self, vector: VectorBattle, catalog: Catalog) -> None:
        self.vector = vector
        self.catalog = catalog
        self.schema = _SchemaView(vector.schema)
        self.vocabulary = VocabularyView(list(vector.vocabulary))

    def decode(self, lane: int) -> dict[str, Any]:
        seat0 = self.vector.observations[int(lane), 0].tolist()
        rows, header = decode_rows(seat0, self.schema, self.vocabulary, self.catalog)
        players = [
            player_facts(
                seat0 if seat == 0 else self.vector.observations[int(lane), 1].tolist(),
                self.schema,
                self.vocabulary,
                self.catalog,
                owner=seat,
            )
            for seat in (0, 1)
        ]
        objects, _rich, visible = entity_rows(
            seat0, self.schema, self.vocabulary, self.catalog, rows=rows
        )
        return {"header": header, "players": players, "objects": objects, "rows": visible}


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
    catalog: Catalog,
) -> dict[str, Any]:
    setup = SideSetup(towers=TOWERS, king_level=level, tower_level=level)
    rng = np.random.default_rng(1)
    lanes = list(range(min(per_lane_lanes, count)))

    with VectorBattle(
        count,
        threads,
        install=install,
        tables=tables,
        config=LadderConfig(decision_ticks=5, level=level, tower_level=level),
    ) as vector:
        encoder = VectorObservationEncoder(vector, catalog)
        batch = encoder.batch()
        control = PerLaneFacts(vector, catalog)

        def reset() -> None:
            vector.reset(
                list(range(seed_base, seed_base + count)),
                list(DECK),
                list(DECK),
                list(DECK_SLOTS),
                list(DECK_SLOTS),
                setup0=setup,
                setup1=setup,
            )
            encoder.reset()

        # Warm the JIT, the catalog lookups and the NumPy paths.
        reset()
        for _ in range(max(10, steps // 20)):
            vector.step(draw_actions(vector.masks, rng))
            encoder.encode(batch)
            for lane in lanes:
                control.decode(lane)

        vector_seconds: list[float] = []
        control_seconds: list[float] = []
        loop_seconds: list[float] = []
        mismatches: list[str] = []
        verify = True
        entity_counts: list[int] = []
        tower_counts: list[int] = []
        nonzero_placements: list[int] = []
        placement_peak: list[int] = []
        check_step = max(1, steps // 2)
        for repeat in range(repeats):
            reset()
            encode_t = control_t = loop_t = 0.0
            for step in range(steps):
                before = time.perf_counter()
                actions = draw_actions(vector.masks, rng)
                vector.step(actions)
                loop_t += time.perf_counter() - before
                before = time.perf_counter()
                encoder.encode(batch)
                encode_t += time.perf_counter() - before
                before = time.perf_counter()
                for lane in lanes:
                    control.decode(lane)
                control_t += time.perf_counter() - before
                if verify and step == check_step:
                    mismatches = compare_lane_facts(encoder, batch, lanes)
                    verify = False
                if repeat == 0:
                    per_seat = [int(batch.placement_rows(seat).sum()) for seat in (0, 1)]
                    if not placement_peak:
                        placement_peak = per_seat
                    else:
                        placement_peak = [max(a, b) for a, b in zip(placement_peak, per_seat)]
                if repeat == 0 and step == check_step:
                    entity_counts = batch.entity_valid.sum(axis=1).tolist()[:8]
                    tower_counts = batch.tower_valid.sum(axis=1).tolist()[:8]
                    nonzero_placements = [
                        int(batch.placement_rows(seat)[0].sum()) for seat in (0, 1)
                    ]
            vector_seconds.append(encode_t)
            control_seconds.append(control_t)
            loop_seconds.append(loop_t)

        best_vector = min(vector_seconds)
        best_control = min(control_seconds)
        best_loop = min(loop_seconds)
        vector_per_lane = best_vector / (count * steps)
        control_per_lane = best_control / (len(lanes) * steps)
        return {
            "threads": vector.threads,
            "count": count,
            "steps": steps,
            "repeats": repeats,
            "per_lane_lanes": len(lanes),
            "data_version": vector.data_version,
            "content_sha": vector.content_sha,
            "vectorized": {
                "ms_per_lane": vector_per_lane * 1_000.0,
                "decisions_per_s": 1.0 / vector_per_lane if vector_per_lane else 0.0,
                "loop_decisions_per_s": (
                    count * steps / (best_loop + best_vector)
                    if best_loop + best_vector
                    else 0.0
                ),
                "spread_pct": _spread(vector_seconds),
            },
            "per_lane": {
                "ms_per_lane": control_per_lane * 1_000.0,
                "decisions_per_s": 1.0 / control_per_lane if control_per_lane else 0.0,
                "spread_pct": _spread(control_seconds),
            },
            "speedup": control_per_lane / vector_per_lane if vector_per_lane else 0.0,
            "equivalence": {
                "checked": not mismatches,
                "lanes": len(lanes),
                "mismatches": mismatches[:20],
            },
            "shape": {
                "entities_first_lanes": entity_counts,
                "towers_first_lanes": tower_counts,
                "nonzero_placement_cells_lane0_at_check": nonzero_placements,
                "placement_cell_peak_all_lanes": placement_peak,
            },
        }


def _spread(seconds: Sequence[float]) -> float:
    if len(seconds) < 2:
        return 0.0
    best = min(seconds)
    return (max(seconds) - best) / best * 100.0 if best else 0.0


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
        help="lanes the control decodes each decision; per-lane ms is scaled",
    )
    parser.add_argument("--install", default=str(DEFAULT_INSTALL))
    parser.add_argument("--tables", default=str(DEFAULT_TABLES))
    parser.add_argument("--level", type=int, default=DEFAULT_LEVEL)
    parser.add_argument("--seed-base", type=int, default=1)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    catalog = Catalog.load()
    rows = [
        _run_one(
            threads=threads,
            count=args.count,
            steps=args.steps,
            repeats=args.repeats,
            per_lane_lanes=args.per_lane_lanes,
            install=args.install,
            tables=args.tables,
            seed_base=args.seed_base,
            level=args.level,
            catalog=catalog,
        )
        for threads in args.threads
    ]

    for row in rows:
        vectorized = row["vectorized"]
        print(
            f"threads {row['threads']}: vectorized facts {vectorized['ms_per_lane']:.4f} ms/lane, "
            f"{vectorized['decisions_per_s']:.0f} decisions/s; per-lane control "
            f"{row['per_lane']['ms_per_lane']:.4f} ms/lane ({row['speedup']:.1f}x); "
            f"equivalence {'pass' if row['equivalence']['checked'] else 'FAIL'}",
            flush=True,
        )
        print(
            f"  entities {row['shape']['entities_first_lanes']}, "
            f"towers {row['shape']['towers_first_lanes']}, "
            f"placement cells lane0 {row['shape']['nonzero_placement_cells_lane0_at_check']} "
            f"peak all lanes {row['shape']['placement_cell_peak_all_lanes']}",
            flush=True,
        )

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "tool": "crforge-vector-observation-spike.v1",
                    "count": args.count,
                    "steps": args.steps,
                    "repeats": args.repeats,
                    "deck": list(DECK),
                    "deck_slots": list(DECK_SLOTS),
                    "level": args.level,
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


if __name__ == "__main__":
    raise SystemExit(main())
