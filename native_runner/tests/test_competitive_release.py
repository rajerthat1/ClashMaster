"""Selectable competitive releases and checkpoint-described loading."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from native_runner.competitive_release import (
    available_release_names,
    current_identity,
    load_release,
    use_release,
)
from native_runner.training.v4.checkpoint import contract_compatible
from native_runner.training.v4.config import (
    CARD_RUNTIME_FEATURE_NAMES,
    CARD_RUNTIME_FEATURE_NAMES_V2,
    ModelConfigV4,
)
from native_runner.training.v4.factory import production_semantic_bundle

REPO_ROOT = Path(__file__).resolve().parents[2]
RF_CHECKPOINT = REPO_ROOT / ".resource" / "RoyaleFish" / "models" / "B-general-v275.pt"
LEGACY_CHECKPOINT = REPO_ROOT / "checkpoints" / "General" / "checkpoint-step-00000460.pt"

# Published contract ids of RoyaleFish B-general-v275 (16.635.1 catalogs).
RF_CATALOG_IDS = {
    "card": "8c0baac97613adee70b0945b4aefb491ecd9a798e94aa3199056d6c3933f7d5d",
    "ability": "128b6679df4663b12e5a1d658d866db85ee71fc22b0b830ed86467fc44568f9c",
    "entity_archetype": "140e32c1ab59443983b6f8662e401e2c992e08d5e0581a9b4bb4b0ae8b2d1478",
    "effect": "a5bf4db42bc953ce8ccd39b802e03973dcfd17bd4b948540651ef59752cd79eb",
    "mechanic_profile": "00b73f61309bfcc293c6b30ce1e72b6f969d94aad2eea0021918125c913f4eab",
}


def test_default_config_stays_on_the_15_535_runtime_schema():
    config = ModelConfigV4()
    assert config.card_runtime_feature_dim == len(CARD_RUNTIME_FEATURE_NAMES) == 16
    assert config.neutralize_pending_ticks_remaining is False
    assert config.child_archetype_count == 429
    assert config.ability_vocab_size == 25


def test_config_accepts_the_16_635_runtime_schema():
    config = dataclasses.replace(
        ModelConfigV4(),
        card_runtime_feature_dim=len(CARD_RUNTIME_FEATURE_NAMES_V2),
        child_archetype_count=453,
        ability_vocab_size=26,
        neutralize_pending_ticks_remaining=True,
        delay_aware_legality=True,
    )
    assert config.card_runtime_feature_dim == 21


def test_config_rejects_unknown_runtime_width():
    with pytest.raises(ValueError, match="card_runtime_feature_dim"):
        dataclasses.replace(ModelConfigV4(), card_runtime_feature_dim=19)


def test_16_635_release_builds_the_published_catalogs():
    assert "16.635.1" in available_release_names()
    bundle = production_semantic_bundle("16.635.1")
    actual = {
        "card": bundle.card_catalog.catalog_id,
        "ability": bundle.ability_catalog.catalog_id,
        "entity_archetype": bundle.entity_archetype_catalog.catalog_id,
        "effect": bundle.effect_catalog.catalog_id,
        "mechanic_profile": bundle.mechanic_profile_catalog.catalog_id,
    }
    assert actual == RF_CATALOG_IDS
    assert bundle.ability_catalog.vocab_size == 26
    assert bundle.entity_archetype_catalog.vocab_size == 453


def test_active_release_switches_identity_and_bundles_are_independent():
    legacy = production_semantic_bundle()
    release = load_release("16.635.1")
    with use_release(release):
        identity = current_identity()
        active = production_semantic_bundle()
        assert identity.policy_card_count == 123
        assert identity.card_runtime_feature_dim == 21
    assert current_identity().policy_card_count == 122
    assert active.card_catalog.catalog_id != legacy.card_catalog.catalog_id
    assert production_semantic_bundle() is legacy


def test_contract_compatibility_allows_additive_schema_fields():
    legacy = {
        "policy_version": "universal-card-policy.v4",
        "observation_version": "universal-semantic-observation.v4",
        "action_version": "universal-candidate-action.v4",
        "model_config": {"card_runtime_feature_dim": 16, "lstm_hidden_dim": 512},
        "catalog_ids": {"card": "x"},
    }
    current = {
        **legacy,
        "model_config": {
            "card_runtime_feature_dim": 16,
            "lstm_hidden_dim": 512,
            "neutralize_pending_ticks_remaining": False,
            "ablate_lstm": False,
        },
    }
    assert contract_compatible(legacy, current)
    changed = {**current, "model_config": {**current["model_config"], "lstm_hidden_dim": 768}}
    assert not contract_compatible(legacy, changed)
    assert not contract_compatible(
        {**legacy, "model_config": {**legacy["model_config"], "card_runtime_feature_dim": 16}},
        {**current, "catalog_ids": {"card": "y"}},
    )


@pytest.mark.skipif(not RF_CHECKPOINT.is_file(), reason="RoyaleFish model not present")
def test_16_635_checkpoint_loads_with_its_own_schema():
    from native_runner.training.v4.policy_session import load_policy_v4

    policy = load_policy_v4(RF_CHECKPOINT, device="cpu")
    assert sum(value.numel() for value in policy.model.state_dict().values()) == 17_506_419
    assert policy.model.config.card_runtime_feature_dim == 21
    assert policy.model.config.lstm_hidden_dim == 768
    assert policy.model.catalog.catalog_id == RF_CATALOG_IDS["card"]


@pytest.mark.skipif(not LEGACY_CHECKPOINT.is_file(), reason="General checkpoint not present")
def test_15_535_checkpoint_still_loads():
    from native_runner.training.v4.policy_session import load_policy_v4

    policy = load_policy_v4(LEGACY_CHECKPOINT, device="cpu")
    assert policy.model.config.card_runtime_feature_dim == 16
    assert sum(value.numel() for value in policy.model.state_dict().values()) == 12_684_339


def test_model_config_mapping_round_trips_and_fails_closed():
    from native_runner.training.v4.policy_session import model_config_from_mapping

    config = ModelConfigV4(card_runtime_feature_dim=len(CARD_RUNTIME_FEATURE_NAMES_V2))
    mapping = dataclasses.asdict(config)
    assert model_config_from_mapping(mapping) == config
    with pytest.raises(ValueError, match="unknown fields"):
        model_config_from_mapping({**mapping, "bogus": 1})


def test_engine_spec_transports_the_tensorizer_identity():
    """Tensorization runs on the engine node, so the spec must carry both halves."""

    import pickle

    from native_runner.training.v4.async_cluster_self_play import AsyncResidentEngineSpecV4
    from native_runner.training.v4.policy_session import model_config_from_mapping

    spec = AsyncResidentEngineSpecV4(
        engine_index=0,
        cpu=0,
        competitive_release="16.635.1",
        model_config=dataclasses.asdict(ModelConfigV4(card_runtime_feature_dim=21)),
    )
    restored = pickle.loads(pickle.dumps(spec))
    assert restored.competitive_release == "16.635.1"
    assert restored.model_config is not None
    assert model_config_from_mapping(restored.model_config).card_runtime_feature_dim == 21
