"""Actor-critic checkpoint contract shared by V4 IL and PPO."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping
import uuid

import torch

from .config import UNIVERSAL_ACTION_VERSION, UNIVERSAL_OBSERVATION_VERSION, UNIVERSAL_POLICY_VERSION
from .model import UniversalCardPolicyV4


CHECKPOINT_SCHEMA_V4 = "universal-card-actor-critic-checkpoint.v4"

# Config keys added after the 15.535 contract.  A checkpoint that predates them
# omits them from its contract; our model may still carry them at these defaults
# without changing the architecture it instantiates.
ADDITIVE_CONFIG_KEYS = frozenset(
    {
        "neutralize_pending_ticks_remaining",
        "delay_aware_legality",
        "card_id_gate",
        "ablate_spatial_memory",
        "ablate_spatial_summary",
        "ablate_event_summary",
        "ablate_previous_action_summary",
        "ablate_relation_bias",
        "ablate_spatial_pair_bias",
        "ablate_mechanic_profiles",
        "ablate_lstm",
        "ablate_decoder_cross_attention",
    }
)


def contract_compatible(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    """True when ``actual`` instantiates the architecture ``expected`` describes.

    Every key the checkpoint states must match.  Keys added by newer policy
    schemas are tolerated only when they are declared additive, so a 15.535
    checkpoint still loads while a real dimension change never does.
    """

    for version in ("policy_version", "observation_version", "action_version"):
        if expected.get(version) != actual.get(version):
            return False
    if expected.get("catalog_ids") != actual.get("catalog_ids"):
        return False
    expected_config = expected.get("model_config") or {}
    actual_config = actual.get("model_config") or {}
    for key, value in expected_config.items():
        if key not in actual_config or actual_config[key] != value:
            return False
    extras = set(actual_config) - set(expected_config)
    return extras <= ADDITIVE_CONFIG_KEYS


def _catalog_ids(model: UniversalCardPolicyV4) -> dict[str, str]:
    return {
        "card": model.catalog.catalog_id,
        "ability": model.ability_catalog.catalog_id,
        "entity_archetype": model.entity_archetype_catalog.catalog_id,
        "effect": model.effect_catalog.catalog_id,
        "mechanic_profile": model.mechanic_profile_catalog.catalog_id,
    }


def checkpoint_contract(model: UniversalCardPolicyV4) -> dict[str, Any]:
    """Return architecture/data identities without changing the model."""

    return {
        "policy_version": UNIVERSAL_POLICY_VERSION,
        "observation_version": UNIVERSAL_OBSERVATION_VERSION,
        "action_version": UNIVERSAL_ACTION_VERSION,
        "model_config": asdict(model.config),
        "catalog_ids": _catalog_ids(model),
    }


def save_actor_critic_checkpoint(
    path: str | Path,
    model: UniversalCardPolicyV4,
    *,
    optimizer: torch.optim.Optimizer | None = None,
    scheduler: object | None = None,
    grad_scaler: object | None = None,
    update_step: int = 0,
    training_stage: str,
    gamma_per_decision: float,
    gae_lambda: float | None = None,
    extra: Mapping[str, Any] | None = None,
) -> str:
    """Save the complete actor, critic, optimizer, and behavior temperatures."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_id = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "schema": CHECKPOINT_SCHEMA_V4,
        "checkpoint_id": checkpoint_id,
        "contract": checkpoint_contract(model),
        "model_state_dict": model.state_dict(),
        **{
            f"{name}_state_dict": None if value is None else value.state_dict()
            for name, value in (("optimizer", optimizer), ("scheduler", scheduler), ("grad_scaler", grad_scaler))
        },
        "update_step": int(update_step),
        "training_stage": str(training_stage),
        "gamma_per_decision": float(gamma_per_decision),
        "gae_lambda": None if gae_lambda is None else float(gae_lambda),
        "ppo_gate_temperature": model.ppo_gate_temperature,
        "ppo_action_temperature": model.ppo_action_temperature,
        "ppo_continue_temperature": model.ppo_continue_temperature,
        "torch_rng_state": torch.get_rng_state(),
        "cuda_rng_state_all": (torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None),
        "extra": dict(extra or {}),
    }
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        torch.save(payload, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return checkpoint_id


def load_actor_critic_checkpoint(
    path: str | Path,
    model: UniversalCardPolicyV4,
    *,
    optimizer: torch.optim.Optimizer | None = None,
    scheduler: object | None = None,
    grad_scaler: object | None = None,
    map_location: torch.device | str | None = None,
    restore_rng: bool = False,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load a strict checkpoint after verifying V4 and catalog identities."""

    if payload is None:
        payload = torch.load(Path(path), map_location=map_location, weights_only=False)
    if not isinstance(payload, dict) or payload.get("schema") != CHECKPOINT_SCHEMA_V4:
        raise ValueError("unsupported V4 actor-critic checkpoint")
    if not contract_compatible(payload.get("contract") or {}, checkpoint_contract(model)):
        raise ValueError("checkpoint model/catalog contract does not match")
    model.load_state_dict(payload["model_state_dict"], strict=True)
    model.set_ppo_gate_temperature(float(payload["ppo_gate_temperature"]))
    model.set_ppo_action_temperature(float(payload["ppo_action_temperature"]))
    model.set_ppo_continue_temperature(float(payload.get("ppo_continue_temperature", 1.0)))

    for name, value in (("optimizer", optimizer), ("scheduler", scheduler), ("grad_scaler", grad_scaler)):
        state = payload.get(f"{name}_state_dict")
        if value is not None and state is not None:
            value.load_state_dict(state)
    if restore_rng:
        torch.set_rng_state(payload["torch_rng_state"])
        cuda_state = payload.get("cuda_rng_state_all")
        if cuda_state is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(cuda_state)
    return payload
