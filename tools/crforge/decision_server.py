"""Serve one checkpoint's forward to many crforge producers.

The producer's forward is 24 % of its packet at 128 lanes and the card serves
about four producers at that share; past that the scale-out step is the field's
shape — CPU collectors, one batched decision server per GPU — so every waiting
collector's batch is served in one forward call.  This is the server process:

    .venv/bin/python tools/crforge/decision_server.py \
      --address unix:/tmp/crforge-decision.sock \
      --checkpoint .resource/RoyaleFish/models/B-general-v275.pt \
      --ready-file /tmp/crforge-decision.ready.json

and the producer attaches with ``self_play.py --decision-server unix:/tmp/...``.
The equality gate lives in the producer: ``--verify-decision-server`` makes the
server sample a seeded batch and compares it action for action with the same
checkpoint's eager in-process path.  The handshake refuses a producer built from
other weights, so a stale server cannot label its samples with this run's policy
state.

The artifact is the server's own clock: rounds, requests and rows per round, the
forward's mean/max, and the row/shape buckets the graph vocabulary settled on.
A GPU box prices the lever from those numbers; this host's CPU build only proves
the wire and the gate.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / ".resource" / "crforge" / "python"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import torch

from crforge_bridge.decision_server import DEFAULT_MAX_REQUESTS, DEFAULT_WAIT_MS, DecisionServer
from crforge_bridge.decision_server import SCHEMA as SERVER_SCHEMA
from crforge_bridge.training import weights_digest
from native_runner.training.v4.policy_session import load_policy_v4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=Path(".resource/RoyaleFish/models/B-general-v275.pt"))
    parser.add_argument(
        "--address",
        default="unix:/tmp/crforge-decision.sock",
        help="unix:/path or host:port; a bare path is a Unix socket",
    )
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--inference-precision", default="auto", choices=("auto", "fp32", "fp16", "bf16"))
    parser.add_argument("--no-cuda-graphs", action="store_true")
    parser.add_argument("--full-cuda-graph", action="store_true")
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument("--wait-ms", type=float, default=DEFAULT_WAIT_MS)
    parser.add_argument(
        "--torch-threads",
        type=int,
        default=None,
        help="torch intra-op threads for the forward (default: torch's own, i.e. OMP_NUM_THREADS)",
    )
    parser.add_argument(
        "--ready-file",
        type=Path,
        default=None,
        help="write {address, checkpoint, device} once the listener is bound, for a supervisor",
    )
    parser.add_argument("--serve-seconds", type=float, default=0.0, help="stop after this wall; 0 = until signalled")
    parser.add_argument("--output", type=Path, default=Path("runs/crforge-c4/decision-server.json"))
    args = parser.parse_args(argv)

    if args.max_requests < 1:
        raise SystemExit("--max-requests must be positive")
    if not 0.0 <= args.wait_ms <= 1000.0:
        raise SystemExit("--wait-ms must be in 0..1000")
    if args.full_cuda_graph and args.no_cuda_graphs:
        raise SystemExit("--full-cuda-graph is the captured path; drop --no-cuda-graphs")
    if args.torch_threads is not None:
        if args.torch_threads < 1:
            raise SystemExit("--torch-threads must be positive")
        torch.set_num_threads(int(args.torch_threads))
    device = torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise SystemExit("--device cuda requested but CUDA is unavailable")

    policy = load_policy_v4(args.checkpoint, device=device)
    model = policy.model
    model.eval()
    server = DecisionServer(
        model,
        device=device,
        precision=args.inference_precision,
        cuda_graphs=not args.no_cuda_graphs,
        full_cuda_graph=args.full_cuda_graph,
        max_requests=args.max_requests,
        wait_ms=args.wait_ms,
        checkpoint_id=str(policy.checkpoint_id),
        checkpoint_sha256=policy.checkpoint_sha256,
        weights_digest=weights_digest(model),
        identity={"checkpoint": str(args.checkpoint)},
    )

    stop = {"requested": False, "signal": None}
    deadline = time.perf_counter() + args.serve_seconds if args.serve_seconds > 0 else None

    def on_signal(number: int, _frame: object) -> None:
        stop["requested"] = True
        stop["signal"] = signal.Signals(number).name

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    def should_stop() -> bool:
        if stop["requested"]:
            return True
        return deadline is not None and time.perf_counter() >= deadline

    def on_ready(info: dict[str, object]) -> None:
        line = {"event": "decision_server_ready", **info}
        print(json.dumps(line), flush=True)
        if args.ready_file is not None:
            args.ready_file.parent.mkdir(parents=True, exist_ok=True)
            args.ready_file.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

    started = time.perf_counter()
    try:
        server.serve(args.address, stop=should_stop, on_ready=on_ready)
    finally:
        wall = time.perf_counter() - started
        artifact = {
            "schema": SERVER_SCHEMA,
            "measured": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "address": args.address,
            "wall_s": wall,
            "stop": dict(stop),
            "checkpoint": {
                "path": str(args.checkpoint),
                "id": policy.checkpoint_id,
                "sha256": policy.checkpoint_sha256,
                "weights_digest": weights_digest(model),
            },
            "server": server.describe(),
            "torch_threads": torch.get_num_threads(),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"event": "decision_server_stopped", "wall_s": wall, "output": str(args.output)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
