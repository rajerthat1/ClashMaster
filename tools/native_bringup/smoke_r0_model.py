#!/usr/bin/env python3
"""R0 elite-v3 model smoke: forward/backward plus checkpoint save/restore.

This does not start a native worker, read a dataset or touch the GPU. It runs
the sandbox's synthetic R0 elite-v3 stack on CPU, performs one PPO update, then
saves and reloads model + optimizer state and checks the weights are bit-exact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

import torch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SANDBOX = PROJECT_ROOT / ".resource" / "cr-native-sandbox"
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

from training_r0.catalog import CardVocabulary  # noqa: E402
from training_r0.config import ModelConfig, TrainingConfig  # noqa: E402
from training_r0.learning import update_minibatch  # noqa: E402
from training_r0.model import R0Policy  # noqa: E402
from training_r0.observation import ObservationBuilder  # noqa: E402
from training_r0.synthetic import rollout  # noqa: E402


def _digest(state: dict[str, torch.Tensor]) -> str:
    sha = hashlib.sha256()
    for name, value in sorted(state.items()):
        sha.update(name.encode("utf-8"))
        sha.update(value.detach().cpu().contiguous().numpy().tobytes())
    return sha.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--seed", type=int, default=1701)
    parser.add_argument("--rich", action="store_true",
                        help="exercise non-empty effects/relations/commands")
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=PROJECT_ROOT / "builds" / "native" / "r0-elite-v3-smoke.pt",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "builds" / "native" / "r0-model-smoke.json",
    )
    args = parser.parse_args(argv)

    torch.set_num_threads(2)
    torch.manual_seed(args.seed)
    started = time.perf_counter()

    vocabulary = CardVocabulary.from_native()
    config = ModelConfig(allow_incomplete_capture=True)  # elite-v3 profile
    model = R0Policy(vocabulary, config).cpu()
    builder = ObservationBuilder(vocabulary, config)
    segment = rollout(
        model, builder, steps=args.steps, sides=(0, 1), rich=args.rich
    )
    settings = TrainingConfig()
    optimizer = torch.optim.Adam(model.parameters(), lr=settings.learning_rate)
    metrics = update_minibatch(
        model, optimizer, segment,
        expected_behavior=segment.behavior, config=settings,
    )
    if not metrics.get("updated"):
        raise RuntimeError("PPO update did not run")

    digest_before = _digest(model.state_dict())
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "kind": "r0_elite_v3_model_smoke_v1",
        "seed": args.seed,
        "profile": "elite",
        "observation_schema_sha256": builder.schema_hash,
        "state_dict": model.state_dict(),
        "optimizer": optimizer.state_dict(),
    }, args.checkpoint)

    fresh = R0Policy(vocabulary, config).cpu()
    checkpoint = torch.load(args.checkpoint, map_location="cpu",
                            weights_only=False)
    if checkpoint.get("kind") != "r0_elite_v3_model_smoke_v1":
        raise RuntimeError("checkpoint kind mismatch")
    if checkpoint["observation_schema_sha256"] != builder.schema_hash:
        raise RuntimeError("checkpoint observation schema mismatch")
    fresh.load_state_dict(checkpoint["state_dict"])
    digest_after = _digest(fresh.state_dict())
    if digest_after != digest_before:
        raise RuntimeError("checkpoint restore is not bit-exact")

    restored_optimizer = torch.optim.Adam(
        fresh.parameters(), lr=settings.learning_rate
    )
    restored_optimizer.load_state_dict(checkpoint["optimizer"])
    resumed_metrics = update_minibatch(
        fresh, restored_optimizer, segment,
        expected_behavior=segment.behavior, config=settings,
    )

    report = {
        "schema": "r0-elite-v3-model-smoke-v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "profile": "elite",
        "seed": args.seed,
        "steps": args.steps,
        "rich_fixture": args.rich,
        "parameters": sum(parameter.numel() for parameter in model.parameters()),
        "vocabulary_size": vocabulary.size,
        "observation_schema_sha256": builder.schema_hash,
        "checkpoint": str(args.checkpoint),
        "checkpoint_bytes": args.checkpoint.stat().st_size,
        "state_dict_sha256": digest_before,
        "restore_bit_exact": digest_after == digest_before,
        "metrics": metrics,
        "resumed_metrics": resumed_metrics,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "native_worker_started": False,
        "real_dataset_read": False,
        "gpu_used": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
