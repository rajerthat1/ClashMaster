"""The overlapped loop: a second model, a worker thread and a measured tax.

These tests use the small fixture model and a fake trainer.  They are about the
pipeline's contract — the collector keeps stable weights, the boundary copy is
in-place and verified, and a collector whose weights moved is refused — not
about PPO's numerics, which the trainer tests cover.
"""

from __future__ import annotations

import threading

import pytest
import torch

from crforge_bridge.training import OverlapUpdater, copy_weights, policy_state_id, weights_digest
from native_runner.tests.test_training_v4_model import _model


class _BlockingTrainer:
    """A trainer stand-in whose update blocks until the test releases it."""

    def __init__(self, model: torch.nn.Module, *, checkpoint_id: str = "fixture") -> None:
        self.model = model
        self.checkpoint_id = checkpoint_id
        self.update_entries: list[dict[str, object]] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls: list[tuple[int, str]] = []

    def enqueue(self, rollout: object, *, segment: int, expected_policy_state_id: str) -> None:
        self.calls.append((int(segment), str(expected_policy_state_id)))
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("the test never released the fake update")
        self.update_entries.append({"segment": int(segment)})

    def boundary_state(self, state: object, *, rows: int) -> object:
        return state


def _pair() -> tuple[torch.nn.Module, torch.nn.Module, _BlockingTrainer, OverlapUpdater]:
    collection = _model().eval()
    update = _model().eval()
    copy_weights(collection, update)
    trainer = _BlockingTrainer(update)
    updater = OverlapUpdater(trainer, collection)  # type: ignore[arg-type]
    return collection, update, trainer, updater


def test_the_collector_keeps_its_weights_while_the_update_runs() -> None:
    collection, update, trainer, updater = _pair()
    sampled = policy_state_id(collection, "fixture")

    updater.submit(object(), segment=0, expected_policy_state_id=sampled)  # type: ignore[arg-type]
    assert trainer.entered.wait(timeout=5), "the worker did not start"
    # This is the gate: the collecting pass still hashes to the id it sampled with.
    assert policy_state_id(collection, "fixture") == sampled

    # The worker's update lands on the second model, not the collection model.
    with torch.no_grad():
        next(update.parameters()).add_(0.01)
    trained = weights_digest(update)
    assert weights_digest(collection) != trained
    trainer.release.set()
    updater.finish()

    assert weights_digest(collection) == trained
    summary = updater.summary()
    assert summary["collection_identity_checks"] == 1
    assert summary["collection_identity_unchanged"] is True
    assert summary["weights_verified"] is True
    # One boundary submit plus the final sync.
    assert summary["boundaries"] == 2
    assert [call[0] for call in trainer.calls] == [0]


def test_a_collecting_pass_whose_weights_moved_is_refused() -> None:
    collection, _update, _trainer, updater = _pair()
    sampled = policy_state_id(collection, "fixture")

    with torch.no_grad():
        next(collection.parameters()).add_(0.01)

    with pytest.raises(RuntimeError, match="collecting weights changed during the segment"):
        updater.submit(object(), segment=0, expected_policy_state_id=sampled)  # type: ignore[arg-type]


def test_the_update_model_must_be_a_second_object() -> None:
    model = _model().eval()
    trainer = _BlockingTrainer(model)
    with pytest.raises(ValueError, match="second model"):
        OverlapUpdater(trainer, model)  # type: ignore[arg-type]


def test_submit_reports_the_boundary_clock() -> None:
    _collection, _update, trainer, updater = _pair()
    sampled = policy_state_id(updater.collection_model, "fixture")
    boundary = updater.submit(object(), segment=3, expected_policy_state_id=sampled)  # type: ignore[arg-type]

    trainer.release.set()
    updater.finish()

    assert boundary["segment"] == 3
    assert boundary["boundary_wall_s"] >= 0.0
    assert boundary["join_wall_s"] >= 0.0
    assert boundary["sync_wall_s"] >= 0.0
    assert boundary["collection_identity_unchanged"] is True
