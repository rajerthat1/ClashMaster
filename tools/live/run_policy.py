#!/usr/bin/env python3
"""Run a V4 checkpoint against the official client via a memory reader + ADB.

Examples:

    # replay captured reader frames with no device (checks the adapter + policy)
    python tools/live/run_policy.py --jsonl runs/live/frames.jsonl --dry-run

    # live: reader in its own process, taps through adb
    python tools/live/run_policy.py \
        --checkpoint .resource/RoyaleFish/models/B-general-v275.pt \
        --serial 127.0.0.1:16480 \
        --reader-cmd "adb -s 127.0.0.1:16480 shell su -c '/data/local/tmp/hcr_reader 1234 0x7f00000000 50'" \
        --match-config tools/native_analysis/match-configs/xbow-vs-hog26.json

The reader must be root-capable.  ``--dry-run`` computes and prints taps but
never sends them, which is the right first live test.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from native_runner.live.frames import LiveFrame, frames_from_lines, normalize_frame  # noqa: E402
from native_runner.live.observation import LiveObservationConfig  # noqa: E402
from native_runner.live.runner import LivePolicyRunner  # noqa: E402
from native_runner.live.taps import AdbTapper, ScreenLayout  # noqa: E402
from native_runner.training.v4.policy_session import load_policy_v4  # noqa: E402


def parse_deck(text: str | None) -> tuple[int, ...] | None:
    if not text:
        return None
    values = tuple(int(item) for item in text.replace(" ", "").split(",") if item)
    if len(values) != 8:
        raise SystemExit(f"deck must have eight card IDs, got {len(values)}")
    return values


def load_match_config(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def frames_from_command(command: str) -> Iterator[LiveFrame]:
    process = subprocess.Popen(
        shlex.split(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    try:
        yield from frames_from_lines(process.stdout)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--serial", help="adb serial; omit for --jsonl replay")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--reader-cmd", help="command that prints one JSON frame per line")
    parser.add_argument("--jsonl", type=Path, help="recorded reader frames")
    parser.add_argument("--match-config", type=Path, help="deck/tower-troop source")
    parser.add_argument("--deck0")
    parser.add_argument("--deck1")
    parser.add_argument("--actor-owner", type=int, choices=(0, 1), default=0)
    parser.add_argument("--tower-troop", type=int, default=None)
    parser.add_argument("--ruleset-id", default="official-live-16.635.13")
    parser.add_argument("--screen", help="WxH override, e.g. 1080x1920")
    parser.add_argument("--device", default="cuda" if _cuda() else "cpu")
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-decisions", type=int, default=0)
    arguments = parser.parse_args()

    config_payload = load_match_config(arguments.match_config) if arguments.match_config else {}
    deck0 = parse_deck(arguments.deck0) or tuple(int(v) for v in config_payload.get("deck0", ()))
    deck1 = parse_deck(arguments.deck1) or tuple(int(v) for v in config_payload.get("deck1", ()))
    if len(deck0) != 8 or len(deck1) != 8:
        raise SystemExit("provide --deck0/--deck1 or --match-config with two eight-card decks")

    tower_troop = arguments.tower_troop
    if tower_troop is None:
        key = f"tower_troop{arguments.actor_owner}_id"
        tower_troop = int(config_payload.get(key, 159_000_000))

    observation_config = LiveObservationConfig(
        ruleset_id=arguments.ruleset_id,
        decks=(deck0, deck1),
        tower_troop_ids=(tower_troop, tower_troop),
        game_mode=int(config_payload.get("game_mode", 72000006)),
        arena=int(config_payload.get("arena", 54000001)),
    )

    loaded = load_policy_v4(arguments.checkpoint, device=arguments.device)
    print(
        f"checkpoint : {loaded.checkpoint_id} step {loaded.update_step} "
        f"({loaded.training_stage})",
        file=sys.stderr,
    )

    layout: ScreenLayout | None = None
    tapper: AdbTapper | None = None
    if arguments.serial and not arguments.dry_run:
        tapper = AdbTapper(arguments.serial, arguments.adb)
        layout = tapper.layout()
    elif arguments.screen:
        width, _, height = arguments.screen.partition("x")
        layout = ScreenLayout.from_size(int(width), int(height))
    else:
        layout = ScreenLayout.from_size(1080, 1920)

    runner = LivePolicyRunner(
        loaded,
        observation_config,
        actor_owner=arguments.actor_owner,
        device=arguments.device,
        sample=not arguments.deterministic,
        layout=layout,
        tapper=tapper,
        dry_run=arguments.dry_run,
    )

    if arguments.jsonl:
        handle = arguments.jsonl.open("r", encoding="utf-8")
        stream = frames_from_lines(handle)
    elif arguments.reader_cmd:
        handle = None
        stream = frames_from_command(arguments.reader_cmd)
    else:
        raise SystemExit("pass --jsonl or --reader-cmd")

    decisions = 0
    try:
        for frame in stream:
            frame = normalize_frame(frame, decks=(deck0, deck1))
            decision = runner.step(frame)
            if decision is None:
                continue
            print(json.dumps(decision.to_dict()), flush=True)
            decisions += 1
            if arguments.max_decisions and decisions >= arguments.max_decisions:
                break
    finally:
        runner.end()
        if handle is not None:
            handle.close()
    return 0


def _cuda() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
