from __future__ import annotations


import pytest

from native_runner.contracts import content_hash
from native_runner.runtime_scope import (
    NORMAL_MODE_CASE_SCOPE_SHA256,
    RuntimeScopeError,
    build_normal_mode_runtime_policy_scope,
    validate_normal_mode_runtime_policy_scope,
)
from native_runner.semantic_subset import (
    NORMAL_MODE_POLICY_CARD_IDS,
    build_semantic_supported_subset,
    load_normal_mode_policy_readiness,
)
from native_runner.card_specs import build_card_catalog
from native_runner.competitive_release import load_release, use_release


def _policy_scope():
    return build_normal_mode_runtime_policy_scope(
        normal_mode_scope_version="normal-mode-card-scope.v1",
        eligible_card_ids=tuple(sorted(NORMAL_MODE_POLICY_CARD_IDS)),
        case_scope_id=load_normal_mode_policy_readiness().case_scope_id,
    )


def test_policy_uses_compatibility_reference_without_experiment_inventory():
    policy = _policy_scope()
    assert validate_normal_mode_runtime_policy_scope(policy) == policy
    assert policy["case_scope_id"] == NORMAL_MODE_CASE_SCOPE_SHA256
    assert "normal_mode_case_scope" not in policy
    assert policy["publication_scope"] == "normal-mode-only"
    assert policy["source_card_count"] == 152
    assert policy["eligible_card_count"] == 122


def test_policy_rejects_changed_compatibility_reference():
    policy = _policy_scope()
    policy["case_scope_id"] = "0" * 64
    policy["scope_id"] = content_hash({k: v for k, v in policy.items() if k != "scope_id"})
    with pytest.raises(RuntimeScopeError, match="identity mismatch"):
        validate_normal_mode_runtime_policy_scope(policy)


def test_policy_uses_the_active_release_identity():
    """A 16.635 release has 153 source / 123 eligible cards, not 152 / 122."""

    with use_release(load_release("16.635.1")):
        subset = build_semantic_supported_subset(build_card_catalog())
        readiness = load_normal_mode_policy_readiness()
        policy = build_normal_mode_runtime_policy_scope(
            normal_mode_scope_version=subset.normal_mode_scope_version,
            eligible_card_ids=tuple(sorted(subset.allowed_ids)),
            case_scope_id=readiness.case_scope_id,
        )
        assert policy["source_card_count"] == 153
        assert policy["eligible_card_count"] == 123
        assert validate_normal_mode_runtime_policy_scope(policy) == policy


@pytest.mark.parametrize("change", ["legacy", "hash", "event", "duplicate", "bool"])
def test_persisted_policy_rejects_scope_partition_and_type_drift(change):
    policy = _policy_scope()
    if change == "legacy":
        policy["publication_scope"] = "full-built-in-suite"
    elif change == "hash":
        policy["eligible_card_ids_sha256"] = "0" * 64
    elif change == "event":
        policy["eligible_card_ids"][-1] = 99_000_001
        policy["eligible_card_ids_sha256"] = content_hash(policy["eligible_card_ids"])
    elif change == "duplicate":
        policy["eligible_card_ids"][1] = policy["eligible_card_ids"][0]
    else:
        policy["eligible_card_count"] = True
    policy["scope_id"] = content_hash({k: v for k, v in policy.items() if k != "scope_id"})
    with pytest.raises(RuntimeScopeError):
        validate_normal_mode_runtime_policy_scope(policy)
