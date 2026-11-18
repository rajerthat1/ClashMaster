#!/usr/bin/env python3
"""Native parity curve: run the promotion gate against the trainer's checkpoint cadence.

``self_play.py --save-checkpoint ck.pt --checkpoint-every N`` now writes
``ck-step-<n>.pt`` every N updates.  The native engine is the only promotion gate
(``ROADMAP.md`` N1/N4), and the plan of record asks for **50-150 native matches
every N updates** — but N was undefined and nothing tied a training run's
checkpoints to blocks.  This tool is that tie::

    .venv/bin/python tools/native_analysis/parity_curve.py \\
        --checkpoints runs/crforge-c4/s50/ck-step-*.pt \\
        --reference .resource/RoyaleFish/models/B-general-v275.pt \\
        --config runs/native-parity-s40/match-config-mirror-xbow.json \\
        --games 10 --seed 101 --update-seconds 24 \\
        --output runs/native-parity-s50/curve.json

Every checkpoint plays the **same seeds** against the reference, so the curve is
paired and the difference between two points is the policy, not the schedule.
Each point is a native block, run through ``native_block_run.sh`` (chunks behind
a fresh engine host with one retry) and merged by ``native_block.summarize``, so
a point carries the Wilson interval, the per-seat split and the illegal/rejected
totals the gate is read on.

The second half of the answer is the cadence itself: the tool measures the wall
per game, and with the training update's own wall (``--update-seconds``) it
reports how many updates fit in ``--overhead-budget`` of native-gate time.  A
cadence that cannot afford one update is reported as such instead of rounded to
one.

The artifact is ``native-parity-curve.v1``.  ``--skip-games`` re-aggregates the
blocks already on disk.  This is the gate, so it is never replaced by a crforge
metric.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "tools" / "native_analysis" / "native_block_run.sh"
SCHEMA = "native-parity-curve.v1"
_STEP = re.compile(r"-step-(\d+)$")


def _native_block():
    """``native_block`` is the aggregator; import it beside this file."""

    tool_dir = Path(__file__).resolve().parent
    if str(tool_dir) not in sys.path:
        sys.path.insert(0, str(tool_dir))
    import native_block

    return native_block


def checkpoint_step(path: Path, fallback: int) -> int:
    """The update step a cadence checkpoint names, or ``fallback`` when it does not."""

    match = _STEP.search(path.stem)
    return int(match.group(1)) if match else int(fallback)


def recommend_cadence(*, gate_seconds: float, update_seconds: float, budget: float) -> dict[str, Any]:
    """How many updates apart a gate keeps its wall inside ``budget`` of the training time.

    A gate every ``N`` updates costs ``gate / (N * update)`` of the run, so
    ``N >= gate / (budget * update)``.  ``gate_in_updates`` is the gate measured in
    updates — the number a cadence that ignores the budget would skip, which is
    the wrong answer and is reported so it can be seen.
    """

    if gate_seconds <= 0 or update_seconds <= 0:
        return {"every_updates": None, "share": None, "gate_in_updates": None, "reason": "a wall is missing"}
    if budget <= 0:
        return {"every_updates": None, "share": None, "gate_in_updates": None, "reason": "budget must be positive"}
    gate_in_updates = gate_seconds / update_seconds
    every = max(1, math.ceil(gate_in_updates / budget))
    return {
        "every_updates": every,
        "share": gate_seconds / (every * update_seconds),
        "gate_in_updates": gate_in_updates,
        "reason": (
            f"one gate costs {gate_in_updates:.1f} updates; every {every} updates keeps it at "
            f"{gate_seconds / (every * update_seconds):.1%} of the run"
        ),
    }


def _chunks(root: Path, name: str) -> list[Path]:
    return sorted(path for path in root.glob(f"{name}-seeds*") if path.is_dir())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--checkpoints", type=Path, nargs="+", required=True, help="cadence checkpoints, oldest first")
    parser.add_argument("--reference", type=Path, required=True, help="the checkpoint every point is measured against")
    parser.add_argument("--config", type=Path, required=True, help="native match config (paired decks, seats)")
    parser.add_argument("--games", type=int, default=10, help="games per checkpoint")
    parser.add_argument("--chunk", type=int, default=10, help="games per engine-host chunk")
    parser.add_argument("--seed", type=int, default=101, help="first seed; every checkpoint plays the same schedule")
    parser.add_argument("--update-seconds", type=float, default=0.0, help="one training update's wall, for the cadence")
    parser.add_argument("--overhead-budget", type=float, default=0.1, help="share of training time the gate may take")
    parser.add_argument("--out-root", type=Path, default=Path("runs/native-parity-curve"))
    parser.add_argument("--skip-games", action="store_true", help="only aggregate the blocks already on disk")
    parser.add_argument("--output", type=Path, default=Path("runs/native-parity-curve/curve.json"))
    args = parser.parse_args(argv)

    if args.games < 1 or args.chunk < 1:
        raise SystemExit("--games and --chunk must be positive")
    for path in [*args.checkpoints, args.reference, args.config]:
        if not path.is_file():
            raise SystemExit(f"missing {path}")
    if not RUNNER.is_file():
        raise SystemExit(f"missing {RUNNER}")

    native_block = _native_block()
    args.out_root.mkdir(parents=True, exist_ok=True)

    points: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, checkpoint in enumerate(args.checkpoints):
        step = checkpoint_step(checkpoint, index)
        name = f"parity-step{step}"
        wall = None
        if not args.skip_games:
            print(f"{name}: {args.games} games vs {args.reference.name}", file=sys.stderr)
            started = time.perf_counter()
            completed = subprocess.run(
                [
                    str(RUNNER),
                    name,
                    str(checkpoint),
                    str(args.reference),
                    str(args.config),
                    str(args.seed),
                    str(args.games),
                    str(args.chunk),
                    str(args.out_root),
                ],
                check=False,
            )
            wall = time.perf_counter() - started
            if completed.returncode != 0:
                errors.append(f"{name}: native_block_run.sh exited {completed.returncode}")
                points.append({"checkpoint": str(checkpoint), "update_step": step, "block": None, "wall_s": wall})
                continue
        chunks = _chunks(args.out_root, name)
        if not chunks:
            errors.append(f"{name}: no chunk directories under {args.out_root}")
            points.append({"checkpoint": str(checkpoint), "update_step": step, "block": None, "wall_s": wall})
            continue
        block = native_block.summarize(chunks, name=name)
        games = int(block["totals"]["games"]) or 0
        points.append(
            {
                "checkpoint": str(checkpoint),
                "update_step": step,
                "games": games,
                "wall_s": wall,
                "seconds_per_game": (wall / games) if wall and games else None,
                "block": block,
            }
        )
        totals = block["totals"]
        print(
            f"{name}: {totals['a_wins']}-{totals['b_wins']} of {block['win_rate_a']['trials']} decisive, "
            f"illegal {totals['illegal_total']}, rejected {totals['rejected_total']}",
            file=sys.stderr,
        )

    measured = [point["seconds_per_game"] for point in points if point.get("seconds_per_game")]
    gate_seconds = (sum(measured) / len(measured)) * args.games if measured else 0.0
    cadence = recommend_cadence(
        gate_seconds=gate_seconds,
        update_seconds=args.update_seconds,
        budget=args.overhead_budget,
    )
    artifact = {
        "schema": SCHEMA,
        "reference": str(args.reference),
        "config": str(args.config),
        "seeds": {"first": args.seed, "last": args.seed + args.games - 1, "games_per_point": args.games},
        "chunk": args.chunk,
        "points": points,
        "cadence": {
            "gate_games": args.games,
            "gate_wall_s": gate_seconds,
            "update_wall_s": args.update_seconds,
            "overhead_budget": args.overhead_budget,
            **cadence,
        },
        "errors": errors,
        "gate": {
            "complete": not errors and len(points) == len(args.checkpoints),
            "illegal_zero": all(
                point["block"] is not None and int(point["block"]["totals"]["illegal_total"]) == 0
                for point in points
            ),
            "rejected_zero": all(
                point["block"] is not None and int(point["block"]["totals"]["rejected_total"]) == 0
                for point in points
            ),
            "same_schedule": len({point.get("games") for point in points}) <= 1,
        },
        "semantics": (
            "every checkpoint plays the same seeds against the reference, so a difference between two "
            "points is the policy; the interval is the block's Wilson interval, never a crforge metric"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"curve: {len(points)} points, gate complete {artifact['gate']['complete']} -> {args.output}")
    return 0 if artifact["gate"]["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
