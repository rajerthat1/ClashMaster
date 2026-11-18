"""The batched decision server: shape buckets, the wire and the two gates.

The CUDA halves (graph capture, fp16, pinned staging) cannot run on this host's
CPU-only torch build; what is pinned here is what a GPU box would otherwise only
discover by running it: the live-width padding is inert, a request's greedy
answer does not depend on who it shares a round with, a seeded sample equals the
in-process eager path, the recurrent state stays the client's, and a client built
from other weights is refused.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Any

import pytest
import torch

from crforge_bridge.decision_server import (
    CHILD_BUCKETS,
    GROUP_BUCKETS,
    DecisionClient,
    DecisionRequest,
    DecisionServer,
    PackedRecord,
    bucket_for,
    canonical_batch,
    pad_rows,
    row_bucket,
)
from native_runner.tests.test_training_v4_model import _batch, _model
from native_runner.training.v4.tensors import BattleGroupSetV4, RelationEdgesV4, UniversalSemanticBatchV4


def _server(**kwargs: Any) -> DecisionServer:
    torch.manual_seed(3)
    model = _model().eval()
    return DecisionServer(
        model,
        device="cpu",
        precision="fp32",
        cuda_graphs=False,
        checkpoint_id="fixture",
        checkpoint_sha256="fixture-sha",
        weights_digest="fixture-weights",
        **kwargs,
    )


def _request(
    server: DecisionServer,
    batch: UniversalSemanticBatchV4,
    *,
    mode: str = "sample",
    seed: int | None = None,
    state: Any = None,
    episode_start: torch.Tensor | None = None,
) -> DecisionRequest:
    rows = int(batch.batch_size)
    return DecisionRequest(
        mode=mode,
        batch=batch,
        state=server.model.initial_state(rows, device="cpu") if state is None else state,
        episode_start=(torch.ones(rows, dtype=torch.bool) if episode_start is None else episode_start),
        seed=seed,
        arrived=time.perf_counter(),
    )


def _narrow_groups(batch: UniversalSemanticBatchV4, groups: int, children: int) -> UniversalSemanticBatchV4:
    """A record at a live width, the shape a producer actually sends."""

    record = batch.groups
    narrowed = BattleGroupSetV4(
        source_card_vocab_id=record.source_card_vocab_id[:, :groups],
        group_type=record.group_type[:, :groups],
        owner_type=record.owner_type[:, :groups],
        runtime_form=record.runtime_form[:, :groups],
        features=record.features[:, :groups],
        position=record.position[:, :groups],
        extent=record.extent[:, :groups],
        mask=record.mask[:, :groups],
        child_archetype_id=record.child_archetype_id[:, :children],
        child_group_index=record.child_group_index[:, :children],
        child_type=record.child_type[:, :children],
        child_features=record.child_features[:, :children],
        child_position=record.child_position[:, :children],
        child_extent=record.child_extent[:, :children],
        child_radius=record.child_radius[:, :children],
        child_mask=record.child_mask[:, :children],
    )
    edges = batch.relation_edges
    narrowed_edges = RelationEdgesV4(
        source=edges.source[:, :1],
        target=edges.target[:, :1],
        relation_type=edges.relation_type[:, :1],
        mask=edges.mask[:, :1],
    )
    return replace(batch, groups=narrowed, relation_edges=narrowed_edges)


def test_bucket_ladders_cover_the_model_capacity() -> None:
    assert bucket_for(0, GROUP_BUCKETS) == 1
    assert bucket_for(11, GROUP_BUCKETS) == 12
    assert bucket_for(12, GROUP_BUCKETS) == 12
    assert bucket_for(48, GROUP_BUCKETS) == 48
    assert bucket_for(160, CHILD_BUCKETS) == 160
    # A width above the ladder is returned unchanged rather than silently clipped.
    assert bucket_for(200, CHILD_BUCKETS) == 200


def test_row_bucket_is_a_power_of_two_multiple() -> None:
    assert row_bucket(16, 16) == 16
    assert row_bucket(17, 16) == 32
    assert row_bucket(48, 16) == 64
    assert row_bucket(3, 2) == 4
    with pytest.raises(ValueError):
        row_bucket(0, 16)


def test_canonical_batch_pads_only_the_variable_axes() -> None:
    batch = _narrow_groups(_batch(2), groups=1, children=1)
    padded = canonical_batch(batch, groups=4, children=8, relations=8, effects=4)
    assert int(padded.groups.mask.shape[1]) == 4
    assert int(padded.groups.child_mask.shape[1]) == 8
    assert int(padded.relation_edges.mask.shape[1]) == 8
    assert int(padded.active_effects.mask.shape[1]) == 4
    # The live prefix is untouched and the tail is masked.
    assert torch.equal(padded.groups.mask[:, :1], batch.groups.mask)
    assert not padded.groups.mask[:, 1:].any()
    assert not padded.groups.child_mask[:, 1:].any()
    assert not padded.relation_edges.mask[:, 1:].any()
    assert not padded.active_effects.mask.any()
    # Everything that is not a variable axis is the same tensor.
    assert padded.own_cards is batch.own_cards
    assert padded.towers is batch.towers
    assert padded.explicit_spatial_planes is batch.explicit_spatial_planes
    with pytest.raises(ValueError, match="narrower"):
        canonical_batch(batch, groups=1, children=1, relations=0, effects=0)


def test_pad_rows_keeps_the_valid_rows() -> None:
    batch = _batch(2)
    padded = pad_rows(batch, 5)
    assert int(padded.batch_size) == 5
    assert torch.equal(padded.match_scalars[:2], batch.match_scalars)
    assert not padded.match_scalars[2:].any()
    assert torch.equal(padded.candidates.mask[:2], batch.candidates.mask)
    assert not padded.candidates.mask[2:].any()
    # Canonical sentinels, not zeros: a padded group/event row keeps the values
    # the record's validation expects on a masked row.
    assert (padded.groups.owner_type[2:] == 2).all()
    assert (padded.previous_action.target_cell[2:] == -1).all()
    with pytest.raises(ValueError, match="down to"):
        pad_rows(batch, 1)


def test_packed_record_round_trip() -> None:
    batch = _narrow_groups(_batch(2), groups=1, children=1)
    packed = PackedRecord.from_record({"batch": batch, "extra": torch.arange(2)})
    restored = PackedRecord.from_payload(packed.descriptor(), list(packed.payloads())).record()
    assert isinstance(restored, dict)
    assert torch.equal(restored["batch"].match_scalars, batch.match_scalars)
    assert torch.equal(restored["batch"].groups.mask, batch.groups.mask)
    assert torch.equal(restored["batch"].candidates.placement, batch.candidates.placement)
    assert torch.equal(restored["extra"], torch.arange(2))
    with pytest.raises(ValueError, match="payload byte count"):
        PackedRecord.from_payload(packed.descriptor(), [bytearray(1)] * len(packed.source_dtypes))


def test_greedy_output_does_not_depend_on_the_round() -> None:
    server = _server()
    solo_batch = _batch(2)
    other = _batch(3)
    solo = server.run_round([_request(server, solo_batch, mode="greedy")])[0]
    batched = server.run_round([_request(server, solo_batch, mode="greedy"), _request(server, other, mode="greedy")])[0]
    assert solo.output is not None and batched.output is not None
    for field in ("gate", "micro_action_count", "candidate_index", "candidate_uid", "target_cell", "delay_offset_bin"):
        assert torch.equal(getattr(solo.output.actions, field), getattr(batched.output.actions, field)), field
    assert torch.allclose(solo.output.log_prob, batched.output.log_prob, atol=1e-4)
    assert torch.allclose(solo.output.value, batched.output.value, atol=1e-4)
    assert torch.allclose(solo.output.next_state.hidden, batched.output.next_state.hidden, atol=1e-4)
    assert torch.allclose(solo.output.next_state.cell, batched.output.next_state.cell, atol=1e-4)


def test_seeded_sample_matches_the_eager_path() -> None:
    server = _server()
    batch = _batch(2)
    request = _request(server, batch, seed=11)
    result = server.run_round([request])[0]
    assert result.output is not None
    torch.manual_seed(11)
    eager = server.model.sample_for_ppo_rollout(
        batch,
        server.model.initial_state(2, device="cpu"),
        episode_start=torch.ones(2, dtype=torch.bool),
        validate=False,
    )
    # The round padded the live axes to the bucket widths: the padding must be
    # inert, which is exactly what an action-for-action match proves.
    for field in ("gate", "micro_action_count", "candidate_index", "candidate_uid", "target_cell", "delay_offset_bin"):
        assert torch.equal(getattr(result.output.actions, field), getattr(eager.actions, field)), field
    assert torch.allclose(result.output.log_prob, eager.log_prob, atol=1e-4)
    assert torch.allclose(result.output.value, eager.value, atol=1e-4)


def test_a_seeded_request_must_be_served_alone() -> None:
    server = _server()
    with pytest.raises(ValueError, match="served alone"):
        server.run_round([_request(server, _batch(2), seed=1), _request(server, _batch(3), seed=2)])


def test_the_recurrent_state_is_the_clients() -> None:
    server = _server()
    batch = _batch(2)
    first = server.run_round([_request(server, batch, mode="greedy")])[0]
    assert first.output is not None
    # ``next_state`` is advanced in place on the next call, so keep the first
    # step's bytes before the second request can move them.
    first_hidden = first.output.next_state.hidden.detach().clone()
    second = server.run_round(
        [
            _request(
                server,
                batch,
                mode="greedy",
                state=first.output.next_state,
                episode_start=torch.zeros(2, dtype=torch.bool),
            )
        ]
    )[0]
    assert second.output is not None
    # The same two steps through the model, with the same batch and mask, must
    # carry the same state.
    torch.manual_seed(3)
    state = server.model.initial_state(2, device="cpu")
    starts = torch.ones(2, dtype=torch.bool)
    step_one = server.model.act(batch, state, episode_start=starts, validate=False)
    step_two = server.model.act(
        batch, step_one.next_state, episode_start=torch.zeros(2, dtype=torch.bool), validate=False
    )
    assert torch.allclose(first_hidden, step_one.next_state.hidden, atol=1e-4)
    assert torch.allclose(second.output.next_state.hidden, step_two.next_state.hidden, atol=1e-4)
    # The carry moved: the second decision did not start from the first's state.
    assert not torch.allclose(first_hidden, second.output.next_state.hidden, atol=1e-4)


def test_value_mode_returns_the_bootstrap_value() -> None:
    server = _server()
    batch = _batch(2)
    result = server.run_round([_request(server, batch, mode="value")])[0]
    assert result.output is None and result.value is not None
    eager = server.model.forward(
        batch,
        server.model.initial_state(2, device="cpu"),
        episode_start=torch.ones(2, dtype=torch.bool),
        validate=False,
    )
    assert torch.allclose(result.value, eager.value, atol=1e-4)


def test_a_seeded_request_shares_the_window_but_not_the_round(tmp_path: Any) -> None:
    """A seeded request must be served alone even when another arrives with it.

    The 4-producer smoke found this the hard way: a seeded verification request
    that shared the wait window with a training request failed the whole round.
    """

    server = _server(max_requests=8, wait_ms=50.0)
    path = tmp_path / "decision.sock"
    identity = {"checkpoint_sha256": "fixture-sha", "weights_digest": "fixture-weights"}
    ready = threading.Event()
    stop = threading.Event()
    failures: list[BaseException] = []

    def run() -> None:
        try:
            server.serve(f"unix:{path}", stop=stop.is_set, on_ready=lambda _info: ready.set())
        except Exception as error:  # noqa: BLE001 - surfaced by the assertion below
            failures.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    gate = threading.Barrier(2)
    answers: dict[str, tuple[int, ...]] = {}
    errors: list[BaseException] = []

    def ask(name: str, seed: int | None) -> None:
        try:
            with DecisionClient(f"unix:{path}", identity=identity, timeout=60.0) as client:
                batch = _batch(2)
                state = server.model.initial_state(2, device="cpu")
                gate.wait(5.0)
                output = client.decide(
                    batch, state, episode_start=torch.ones(2, dtype=torch.bool), seed=seed
                )
                answers[name] = tuple(output.actions.gate.shape)
        except BaseException as error:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(error)

    try:
        assert ready.wait(10.0)
        askers = [
            threading.Thread(target=ask, args=("seeded", 7), daemon=True),
            threading.Thread(target=ask, args=("plain", None), daemon=True),
        ]
        for asker in askers:
            asker.start()
        for asker in askers:
            asker.join(60.0)
        assert not errors, errors
        assert answers == {"seeded": (2,), "plain": (2,)}
        with DecisionClient(f"unix:{path}", identity=identity, timeout=60.0) as supervisor:
            stats = supervisor.server_stats()
        assert stats["failed_rounds"] == 0
        # The plain request and the seeded one were two rounds, not one.
        assert stats["rounds"] == 2
        assert stats["requests"] == 2
    finally:
        stop.set()
        thread.join(10.0)
    assert not thread.is_alive()
    assert not failures, failures


def test_the_wire_carries_a_decision_and_refuses_other_weights(tmp_path: Any) -> None:
    server = _server(max_requests=4, wait_ms=1.0)
    path = tmp_path / "decision.sock"
    identity = {"checkpoint_sha256": "fixture-sha", "weights_digest": "fixture-weights"}
    ready = threading.Event()
    stop = threading.Event()
    failures: list[BaseException] = []

    def run() -> None:
        try:
            server.serve(f"unix:{path}", stop=stop.is_set, on_ready=lambda _info: ready.set())
        except Exception as error:  # noqa: BLE001 - surfaced by the assertion below
            failures.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert ready.wait(10.0)
        with DecisionClient(f"unix:{path}", identity=identity, timeout=30.0) as client:
            batch = _batch(2)
            state = server.model.initial_state(2, device="cpu")
            output = client.decide(batch, state, episode_start=torch.ones(2, dtype=torch.bool))
            assert output.actions.gate.shape == (2,)
            assert int(output.next_state.hidden.shape[0]) == 2
            value = client.value(batch, output.next_state, episode_start=torch.zeros(2, dtype=torch.bool))
            assert value.shape == (2,)
            stats = client.request_stats()
        assert stats["requests"] == 2
        assert stats["round_trip_ms_mean"] > 0.0
        with pytest.raises(RuntimeError, match="weights digest mismatch"):
            DecisionClient(
                f"unix:{path}", identity={"checkpoint_sha256": "fixture-sha", "weights_digest": "other"}, timeout=30.0
            )
    finally:
        stop.set()
        thread.join(10.0)
    assert not thread.is_alive()
    assert not failures, failures
    described = server.describe()
    assert described["rounds"] == 2
    assert described["requests"] == 2
    assert described["modes"] == {"sample": 1, "value": 1}
