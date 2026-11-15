#!/usr/bin/env python3
"""Run the real V4 PPO collection loop on CPU against live resident engines.

This is the companion to ``profile_cluster_collection.py``: that one measures the
transport floor with a scripted WAIT policy, this one runs
``collect_async_cluster_wave_v4`` itself - real model, real tensorizers, real
rollout storage - on a non-CUDA device (the collector now falls back to an eager
sampler instead of requiring a CUDA graph).  Use it to separate engine cost from
learner cost without renting a GPU.

    # 4 A51 engines already forwarded on 26789..26792
    .venv/bin/python tools/native_analysis/profile_real_collection.py \
        --engines 4 --lanes 8 --steps 40

Reported: wall time, packets, per-packet cost, and the loop's own timing dict
(inference / assembly / storage / action / command / ready-wait).
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path
import sys
import time

import torch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from native_runner.training.v4.async_cluster_self_play import (  # noqa: E402
    AsyncResidentEngineSpecV4,
    collect_async_cluster_wave_v4,
)
from contextlib import nullcontext  # noqa: E402

from native_runner.competitive_release import load_release, use_release  # noqa: E402
from native_runner.training.v4.factory import build_production_model_v4  # noqa: E402
from native_runner.training.v4.policy_session import (  # noqa: E402
    load_policy_v4,
    model_config_from_mapping,
    resolve_checkpoint_release,
)
from native_runner.training.v4.ppo_runtime import ppo_environment_config_v4  # noqa: E402
from profile_cluster_collection import _assignments, _start_worker_server  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deck-pool", type=Path, default=REPO_ROOT / "runs" / "deck-pool-a51.json")
    parser.add_argument("--checkpoint", type=Path, default=REPO_ROOT / "checkpoints/General/checkpoint-step-00000460.pt")
    parser.add_argument("--engines", type=int, default=1)
    parser.add_argument("--engine-base-port", type=int, default=26_789)
    parser.add_argument("--worker-port", type=int, default=29_950)
    parser.add_argument("--lanes", type=int, default=8)
    parser.add_argument("--steps", type=int, default=40, help="decision steps per lane for one segment")
    parser.add_argument("--battle-level", type=int, default=11)
    parser.add_argument("--deck-offset", type=int, default=0)
    parser.add_argument("--deck-source", choices=("pool", "focus"), default="pool")
    parser.add_argument("--card-keys", help="8 comma-separated card keys; pins both decks")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="torch RNG seed for reproducible sampled actions (default: unseeded)",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument(
        "--segments",
        type=int,
        default=1,
        help="collect N consecutive segments (exercises boundary bootstrap + resume)",
    )
    parser.add_argument(
        "--synthetic-model",
        action="store_true",
        help=(
            "build a narrowed model from --checkpoint's contract instead of loading its weights; "
            "isolates loop cost from the (CPU) forward"
        ),
    )
    parser.add_argument("--gate-temperature", type=float, default=1.0)
    parser.add_argument("--affinity-width", type=int, choices=(1, 2, 4), default=1)
    parser.add_argument("--allow-cpu-sharing", action="store_true")
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--no-server", action="store_true", help="reuse an already running worker server")
    parser.add_argument("--log", type=Path, default=Path("/tmp/opencode/profile-real-worker.log"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    torch.set_num_threads(args.threads)
    if args.seed is not None:
        torch.manual_seed(args.seed)
    device = torch.device("cpu")
    environment = ppo_environment_config_v4()
    max_decision_steps = math.ceil(environment.max_battle_ticks / 5) + 4

    if args.synthetic_model:
        # The same contract shape and catalogs as the checkpoint, but narrow
        # widths: a CPU forward is then cheap enough that the measured rate is
        # the collection loop's, not the model's.
        payload = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
        contract = payload["contract"]
        config = model_config_from_mapping(contract["model_config"], origin="synthetic profile")
        config = replace(
            config,
            transformer_layers=1,
            attention_heads=4,
            transformer_ffn_dim=128,
            spatial_hidden_channels=16,
            spatial_res_blocks=1,
            lstm_input_dim=256,
            lstm_hidden_dim=64,
            decoder_hidden_dim=64,
        )
        release_name, _bundle = resolve_checkpoint_release(contract)
        model = build_production_model_v4(config=config, release=release_name, device=device)
    else:
        loaded = load_policy_v4(args.checkpoint, device=device)
        model = loaded.model
        payload = torch.load(args.checkpoint, map_location=device, weights_only=False)
        release_name, _bundle = resolve_checkpoint_release(payload["contract"])
    model.set_ppo_gate_temperature(args.gate_temperature)
    model.set_ppo_action_temperature(1.0)
    release_context = use_release(load_release(release_name)) if release_name else nullcontext()

    specs = tuple(
        AsyncResidentEngineSpecV4(
            engine_index=index,
            cpu=index,
            host="127.0.0.1",
            base_port=args.engine_base_port,
            lanes=args.lanes,
            timeout=args.timeout,
            capture_mode="compact",
            worker_host="127.0.0.1",
            worker_port=args.worker_port,
        )
        for index in range(args.engines)
    )
    assignments = tuple(
        assignment
        for spec in specs
        for assignment in _assignments(
            args.deck_pool,
            engine_index=spec.engine_index,
            lanes=args.lanes,
            battle_level=args.battle_level,
            base_sequence=1,
            deck_offset=args.deck_offset,
            deck_source=args.deck_source,
            card_keys=args.card_keys,
        )
    )

    state = {"segments": 0, "started": time.perf_counter(), "frames": 0}

    def segment_callback(collection: object) -> str | None:
        state["segments"] += 1
        rollout = getattr(collection, "rollout", None)
        valid = getattr(rollout, "valid_mask", None)
        if valid is not None:
            state["frames"] += int(valid.sum())
        print(
            json.dumps(
                {
                    "event": "cpu_segment_complete",
                    "segment": state["segments"],
                    "elapsed_seconds": time.perf_counter() - state["started"],
                    "timing": getattr(collection, "timing", {}),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return f"cpu-profile-{state['segments'] + 1}" if state["segments"] < args.segments else None

    started = time.perf_counter()
    server = None
    if not args.no_server:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        server = _start_worker_server(args, args.log)
        print(json.dumps({"event": "worker_server_ready", "port": args.worker_port}), flush=True)
    try:
        with release_context:
            collection = collect_async_cluster_wave_v4(
                specs,
                assignments,
                model,
                model,
                {},
                gamma_per_decision=0.9997,
                shaping_beta=0.05,
                mutual_elixir_overflow_penalty=0.0,
                mutual_elixir_overflow_grace=0.0,
                unilateral_elixir_overflow_penalty=0.0,
                elixir_overflow_step_penalty_cap=0.1,
                policy_state_id="cpu-profile",
                max_decision_steps=max_decision_steps,
                validate_tensors=False,
                segment_decision_steps=args.steps * 4,
                segment_callback=segment_callback,
            )
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except Exception:  # noqa: BLE001 - best effort teardown
                server.kill()
    elapsed = time.perf_counter() - started
    timing = dict(collection.timing)
    last_frames = int(collection.rollout.valid_mask.sum()) if hasattr(collection.rollout, "valid_mask") else 0
    # ``frames`` is every owner frame collected across the requested segments, so
    # the rates below stay meaningful with --segments > 1.
    frames = state["frames"] or last_frames
    lanes = args.engines * args.lanes
    summary = {
        "engines": args.engines,
        "lanes": args.lanes,
        "steps": args.steps,
        "elapsed_seconds": elapsed,
        "segments": state["segments"],
        "frames": frames,
        "last_segment_frames": last_frames,
        "packets_per_second": (frames / lanes / elapsed) if lanes and frames else 0.0,
        "lane_decisions_per_second": (frames / elapsed) if frames else 0.0,
        "matches_per_second": ((frames / elapsed) / (3600 / 5)) if frames else 0.0,
        "device": str(device),
        "release": release_name,
        "synthetic_model": args.synthetic_model,
        "checkpoint": str(args.checkpoint),
        **{f"timing_{key}": value for key, value in timing.items()},
    }
    print(json.dumps(summary, sort_keys=True))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
