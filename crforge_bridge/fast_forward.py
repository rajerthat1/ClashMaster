"""The in-process inference port of ``ever_server``'s four tricks.

Source: ``.resource/RoyaleFish/royalefish/ever_server.py`` (with
``ever_packed.py``).  Its TCP/pickle wire protocol is deliberately **not**
ported: the C4 producer owns the forward in-process, so there is no second GPU
process to talk to.  What is ported is the forward itself:

1. **Precision.**  Autocast fp16 on CUDA (bf16 on CPU is opt-in and only for
   verifying the path), with the log-probability, entropy and value restored to
   fp32 before the caller sees them.
2. **CUDA graphs at the producer's row count.**  Captured lazily per padded row
   count and replayed, instead of one graph for batch 1.  The graph covers the
   deterministic encode + recurrent-core forward only.  Sampling stays eager:
   a captured sample would replay the same random draws on every replay
   (``torch 2.5`` has no graph-safe generator state), which is acceptable for a
   deployment server and fatal for a self-play collector.
3. **One GPU-side LSTM state table.**  The recurrent state lives in one
   preallocated pair of tensors for the run; each decision copies the previous
   ``next_state`` into that table instead of letting the allocator hand out a new
   one, and the graph reads/writes fixed buffers.
4. **Layout and pack once.**  The padded device batch is allocated once per row
   count by :func:`native_runner.training.v4.policy_session._fixed_cuda_graph_batch_v4`
   and refilled in place with a valid-slice copy.  On CUDA the host side is a
   pinned mirror and the transfer is ``non_blocking``; on CPU the staging is the
   model input, so the padding is skipped entirely (padding a CPU forward only
   makes it slower).

The gate for this module is numerical, not the wall: fp16/graph replay must
reproduce the fp32 sampled actions exactly and its log-probs within
``DEFAULT_TOLERANCE`` on the same batch and RNG.  :meth:`FastForwardV4.verify`
does that comparison and is what ``self_play.py --verify-fast-forward`` records
in the artifact.  The throughput claim (``ms.inference_ms`` at 128 lanes with
the CPU half beside it) can only be measured on a GPU box; this host has a
CPU-only torch build.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any

import torch

from native_runner.training.v4.policy_session import _fixed_cuda_graph_batch_v4
from native_runner.training.v4.tensors import (
    PolicyContextV4,
    PolicyOutputV4,
    RecurrentPolicyStateV4,
    UniversalSemanticBatchV4,
)

DEFAULT_TOLERANCE = 5e-3
_PRECISIONS = ("auto", "fp32", "fp16", "bf16")


def resolve_precision(precision: str, device: torch.device) -> str:
    """Resolve ``auto`` against the device and refuse a precision it cannot run."""

    if precision not in _PRECISIONS:
        raise ValueError(f"inference precision must be one of {_PRECISIONS}, got {precision!r}")
    if precision == "auto":
        return "fp16" if device.type == "cuda" else "fp32"
    if precision == "fp16" and device.type != "cuda":
        raise ValueError("fp16 autocast inference needs CUDA; use bf16 to check the path on CPU")
    return precision


def _copy_full_record_(target: object, source: object, *, non_blocking: bool = False) -> None:
    """Copy a nested tensor record into a same-shaped staging record."""

    if isinstance(target, torch.Tensor):
        if not isinstance(source, torch.Tensor):
            raise TypeError("fast-forward staging source is not a tensor")
        if tuple(target.shape) != tuple(source.shape):
            raise ValueError(f"fast-forward staging shape changed: {tuple(target.shape)} -> {tuple(source.shape)}")
        target.copy_(source, non_blocking=non_blocking)
        return
    if is_dataclass(target):
        if type(source) is not type(target):
            raise TypeError("fast-forward staging record type changed")
        for field in fields(target):
            _copy_full_record_(getattr(target, field.name), getattr(source, field.name), non_blocking=non_blocking)
        return
    if target != source:
        raise ValueError("fast-forward staging non-tensor input changed")


def _copy_valid_record_(target: object, source: object) -> None:
    """Copy the valid prefix of a batch into a padded staging record in place.

    Only the batch and the intentionally variable second axis (active effects,
    relation edges) can shrink, so an intersection copy is all the layout needs;
    the rest of the padded row stays masked out.
    """

    if isinstance(target, torch.Tensor):
        if not isinstance(source, torch.Tensor):
            raise TypeError("fast-forward staging source is not a tensor")
        if target.ndim != source.ndim:
            raise ValueError("fast-forward staging tensor rank changed")
        rows = int(source.shape[0])
        if rows > int(target.shape[0]):
            raise ValueError(f"fast-forward staging rows {rows} exceed capacity {int(target.shape[0])}")
        for actual, capacity in zip(source.shape[1:], target.shape[1:]):
            if int(actual) > int(capacity):
                raise ValueError(f"fast-forward staging axis {int(actual)} exceeds capacity {int(capacity)}")
        slices = (slice(0, rows),) + tuple(slice(0, int(size)) for size in source.shape[1:])
        target[slices].copy_(source)
        return
    if is_dataclass(target):
        if type(source) is not type(target):
            raise TypeError("fast-forward staging record type changed")
        for field in fields(target):
            _copy_valid_record_(getattr(target, field.name), getattr(source, field.name))
        return
    if target != source:
        raise ValueError("fast-forward staging non-tensor input changed")


def _copy_state_(target: RecurrentPolicyStateV4, source: RecurrentPolicyStateV4) -> None:
    if tuple(target.hidden.shape) != tuple(source.hidden.shape):
        raise ValueError(f"recurrent state rows changed: {tuple(source.hidden.shape)} -> {tuple(target.hidden.shape)}")
    target.hidden.copy_(source.hidden.to(dtype=target.hidden.dtype))
    target.cell.copy_(source.cell.to(dtype=target.cell.dtype))


def _pin_record(value: object) -> object:
    """Pin a nested CPU tensor record for an asynchronous host-to-device copy."""

    if isinstance(value, torch.Tensor):
        return value if value.is_pinned() else value.pin_memory()
    if is_dataclass(value):
        return type(value)(**{field.name: _pin_record(getattr(value, field.name)) for field in fields(value)})
    return value


class _StagedBatch:
    """One padded device batch for a row count, refilled in place every decision."""

    __slots__ = ("batch", "episode_start", "pinned", "pinned_episode_start", "rows")

    def __init__(
        self,
        host_batch: UniversalSemanticBatchV4,
        device: torch.device,
        *,
        effect_capacity: int,
        relation_capacity: int,
        pin_memory: bool,
    ) -> None:
        padded = _fixed_cuda_graph_batch_v4(
            host_batch, effect_capacity=effect_capacity, relation_capacity=relation_capacity
        )
        self.rows = int(padded.batch_size)
        if pin_memory:
            # The padded CPU record is the pinned mirror: hold it, and give the
            # device a separate copy so a later fill cannot race the graph.  The
            # H2D copy is asynchronous only because the source is pinned.
            self.pinned = _pin_record(padded)
            self.batch = padded.to_model_input(device)
        else:
            self.pinned = None
            self.batch = padded.to_model_input(device)
        self.episode_start = torch.zeros(self.rows, dtype=torch.bool, device=device)
        self.pinned_episode_start = torch.zeros(self.rows, dtype=torch.bool, pin_memory=pin_memory)

    def fill(self, host_batch: UniversalSemanticBatchV4, episode_start: torch.Tensor) -> None:
        if int(host_batch.batch_size) != self.rows:
            raise ValueError(f"fast-forward batch rows changed: {int(host_batch.batch_size)} -> {self.rows}")
        if self.pinned is not None:
            _copy_valid_record_(self.pinned, host_batch)
            _copy_full_record_(self.batch, self.pinned, non_blocking=True)
            self.pinned_episode_start.copy_(episode_start.detach().to("cpu", torch.bool))
            self.episode_start.copy_(self.pinned_episode_start, non_blocking=True)
        else:
            _copy_valid_record_(self.batch, host_batch)
            self.episode_start.copy_(episode_start.detach().to(self.batch.match_scalars.device, torch.bool))


class _GraphSlot:
    """One captured forward for a padded row count, and optionally the decode.

    A **partial** slot captures only the deterministic encode + recurrent core;
    the sampler runs eagerly outside it.  A **full** slot captures the sampled
    decode as well, which is the only form that can pay: at 256 rows the eager
    decode and the staging it forces are what made the partial graph 34 ms of
    wall slower than the same fp16 path without graphs.  A sampled graph is only
    accepted if replay keeps drawing fresh random numbers, which
    :meth:`FastForwardV4._assert_graph_rng_advances` checks before the first
    decision.
    """

    __slots__ = ("context", "graph", "output", "stage", "state_in")

    def __init__(
        self,
        model: Any,
        device: torch.device,
        stage: _StagedBatch,
        state: RecurrentPolicyStateV4,
        autocast: Any,
        *,
        full: bool = False,
        sample: bool = True,
    ) -> None:
        reference = state.hidden
        self.stage = stage
        self.state_in = RecurrentPolicyStateV4(
            hidden=torch.zeros_like(reference, device=device),
            cell=torch.zeros_like(reference, device=device),
        )
        _copy_state_(self.state_in, state)
        stream = torch.cuda.Stream(device=device)
        stream.wait_stream(torch.cuda.current_stream(device))
        with torch.cuda.stream(stream), torch.inference_mode(), autocast():
            for _ in range(3):
                context = model.forward(
                    stage.batch,
                    self.state_in,
                    episode_start=stage.episode_start,
                    validate=False,
                )
                if full:
                    model.select_action_from_context(stage.batch, context, sample=sample, validate=False)
        torch.cuda.current_stream(device).wait_stream(stream)
        torch.cuda.synchronize(device)
        self.graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(self.graph), torch.inference_mode(), autocast():
            self.context: PolicyContextV4 = model.forward(
                stage.batch,
                self.state_in,
                episode_start=stage.episode_start,
                validate=False,
            )
            self.output: PolicyOutputV4 | None = None
            if full:
                output = model.select_action_from_context(stage.batch, self.context, sample=sample, validate=False)
                # The caller's PPO bookkeeping is fp32 even when the graph ran
                # in half; the cast is deterministic and belongs inside the
                # capture, or it would be an eager op per decision.
                output.log_prob = output.log_prob.float()
                output.entropy = output.entropy.float()
                output.value = output.value.float()
                self.output = output

    def run(
        self,
        host_batch: UniversalSemanticBatchV4,
        state: RecurrentPolicyStateV4,
        episode_start: torch.Tensor,
    ) -> tuple[PolicyContextV4, PolicyOutputV4 | None]:
        if tuple(state.hidden.shape) != tuple(self.state_in.hidden.shape):
            raise ValueError("fast-forward recurrent state does not match the captured row count")
        self.stage.fill(host_batch, episode_start)
        _copy_state_(self.state_in, state)
        self.graph.replay()
        return self.context, self.output


class FastForwardV4:
    """The producer's forward: staging, a state table, optional graphs and autocast."""

    def __init__(
        self,
        model: Any,
        *,
        device: torch.device | str,
        rows: int,
        precision: str = "auto",
        cuda_graphs: bool = True,
        full_cuda_graph: bool = False,
        pin_memory: bool | None = None,
        effect_capacity: int = 512,
        relation_capacity: int = 2048,
    ) -> None:
        if int(rows) < 1:
            raise ValueError("fast-forward rows must be positive")
        self.model = model
        self.device = torch.device(device)
        self.rows = int(rows)
        self.precision = resolve_precision(precision, self.device)
        requested_graphs = bool(cuda_graphs)
        self.cuda_graphs = requested_graphs and self.device.type == "cuda"
        self.full_cuda_graph = bool(full_cuda_graph)
        if self.full_cuda_graph:
            if not requested_graphs:
                raise ValueError("--full-cuda-graph is the captured path; do not combine it with --no-cuda-graphs")
            if self.device.type != "cuda":
                raise ValueError(
                    "a full-call CUDA Graph (forward + sampled decode) needs CUDA; "
                    "this host's torch cannot capture one"
                )
        self.pin_memory = self.device.type == "cuda" if pin_memory is None else bool(pin_memory)
        if self.pin_memory and self.device.type != "cuda":
            raise ValueError("pinned staging needs CUDA")
        self.effect_capacity = int(effect_capacity)
        self.relation_capacity = int(relation_capacity)
        self._graphs: dict[int, _GraphSlot] = {}
        self._full_graphs: dict[tuple[int, bool], _GraphSlot] = {}
        self._staged: dict[int, _StagedBatch] = {}
        self.rng_gate: dict[str, Any] | None = None
        state = model.initial_state(self.rows, device=self.device)
        # The table is fp32 even under autocast: it is the canonical carry between
        # decisions, and both graph input and output are cast into it.
        self._table = RecurrentPolicyStateV4(
            hidden=torch.zeros_like(state.hidden, dtype=torch.float32),
            cell=torch.zeros_like(state.cell, dtype=torch.float32),
        )
        self.captures = 0

    # -- graph helpers -------------------------------------------------------

    def _autocast(self):
        if self.precision == "fp32":
            return torch.autocast(device_type=self.device.type, enabled=False)
        dtype = torch.float16 if self.precision == "fp16" else torch.bfloat16
        return torch.autocast(device_type=self.device.type, dtype=dtype)

    def _stage(self, host_batch: UniversalSemanticBatchV4) -> _StagedBatch:
        rows = int(host_batch.batch_size)
        if rows != self.rows:
            raise ValueError(f"fast-forward was built for {self.rows} rows, got {rows}")
        stage = self._staged.get(rows)
        if stage is None:
            stage = _StagedBatch(
                host_batch,
                self.device,
                effect_capacity=self.effect_capacity,
                relation_capacity=self.relation_capacity,
                pin_memory=self.pin_memory,
            )
            self._staged[rows] = stage
        return stage

    def _slot(self, host_batch: UniversalSemanticBatchV4) -> _GraphSlot:
        rows = int(host_batch.batch_size)
        slot = self._graphs.get(rows)
        if slot is None:
            stage = self._stage(host_batch)
            stage.fill(host_batch, torch.zeros(rows, dtype=torch.bool, device=self.device))
            slot = _GraphSlot(self.model, self.device, stage, self._table, self._autocast)
            self._graphs[rows] = slot
            self.captures += 1
        return slot

    def _full_slot(self, host_batch: UniversalSemanticBatchV4, *, sample: bool) -> _GraphSlot:
        """The captured forward + decode for this row count and decode mode."""

        rows = int(host_batch.batch_size)
        key = (rows, bool(sample))
        slot = self._full_graphs.get(key)
        if slot is None:
            stage = self._stage(host_batch)
            stage.fill(host_batch, torch.zeros(rows, dtype=torch.bool, device=self.device))
            slot = _GraphSlot(
                self.model, self.device, stage, self._table, self._autocast, full=True, sample=sample
            )
            self._full_graphs[key] = slot
            self.captures += 1
            if sample:
                self._assert_graph_rng_advances(slot, host_batch)
        return slot

    def _assert_graph_rng_advances(self, slot: _GraphSlot, host_batch: UniversalSemanticBatchV4) -> None:
        """Refuse a sampled graph whose replay repeats its random draws.

        A graph is only safe for a collector if the default generator's state is
        part of the capture: PyTorch registers the default generator at
        ``capture_begin``, but a build without that cooperation replays the same
        draw forever, which would make every decision sample from one fixed RNG
        position.  Two replays of the *same* batch must differ; exact equality of
        the sampled log-probabilities is the signature of a frozen stream.
        """

        rows = int(host_batch.batch_size)
        state_before = RecurrentPolicyStateV4(hidden=self._table.hidden.clone(), cell=self._table.cell.clone())
        rng = _save_rng(self.device)
        starts = torch.zeros(rows, dtype=torch.bool, device=self.device)
        try:
            _context, first = slot.run(host_batch, self._table, starts)
            assert first is not None
            first_log_prob = first.log_prob.detach().clone()
            _context, second = slot.run(host_batch, self._table, starts)
            assert second is not None
            second_log_prob = second.log_prob.detach()
            advance = float((second_log_prob - first_log_prob).abs().max())
        finally:
            _copy_state_(self._table, state_before)
            _restore_rng(self.device, rng)
        self.rng_gate = {
            "rows": rows,
            "max_log_prob_delta_between_replays": advance,
            "passed": advance > 0.0,
            "reasoning": (
                "two replays of one captured sampled call must draw different numbers; "
                "exact equality means the RNG stream is frozen in the graph"
            ),
        }
        if advance <= 0.0:
            raise RuntimeError(
                "the sampled CUDA Graph replayed the same random draws twice; this torch build does not "
                "make the sampler graph-safe, so --full-cuda-graph is refused (use --no-cuda-graphs)"
            )

    # -- public surface ------------------------------------------------------

    @property
    def state(self) -> RecurrentPolicyStateV4:
        """The canonical recurrent state the producer carries between decisions."""

        return self._table

    def rebind(self, state: RecurrentPolicyStateV4) -> None:
        """Make ``state`` the table's contents (a boundary state or a resume)."""

        if state is self._table:
            return
        _copy_state_(self._table, state)

    def forward(
        self,
        batch: UniversalSemanticBatchV4,
        state: RecurrentPolicyStateV4,
        *,
        episode_start: torch.Tensor | None = None,
    ) -> PolicyContextV4:
        """The encode+core forward, without sampling and without committing state."""

        starts = self._episode_start(episode_start, batch, state)
        with torch.inference_mode():
            if self.cuda_graphs:
                # A capture freezes the cache-hit branch: refresh the static
                # tables in place before replay so the graph reads current ones.
                self.model.ensure_static_memory()
                context, _output = self._slot(batch).run(batch, state, starts)
                return context
            model_batch = batch.to_model_input(self.device)
            with self._autocast():
                return self.model.forward(model_batch, state, episode_start=starts, validate=False)

    def sample(
        self,
        batch: UniversalSemanticBatchV4,
        state: RecurrentPolicyStateV4,
        *,
        episode_start: torch.Tensor | None = None,
    ) -> PolicyOutputV4:
        """The sampled PPO forward, committed to the state table."""

        return self._decide(batch, state, episode_start=episode_start, sample=True)

    def act(
        self,
        batch: UniversalSemanticBatchV4,
        state: RecurrentPolicyStateV4,
        *,
        episode_start: torch.Tensor | None = None,
    ) -> PolicyOutputV4:
        """The argmax forward (the producer's ``--greedy`` debug path)."""

        return self._decide(batch, state, episode_start=episode_start, sample=False)

    def describe(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "precision": self.precision,
            "cuda_graphs": self.cuda_graphs,
            "full_cuda_graph": self.full_cuda_graph,
            "graph_covers": (
                "encode + recurrent core + sampled decode"
                if self.full_cuda_graph
                else ("encode + recurrent core" if self.cuda_graphs else "nothing")
            ),
            "sampled_graph_rng_gate": self.rng_gate,
            "captured_shapes": sorted(self._graphs),
            "captured_full_shapes": [
                {"rows": rows, "sample": sample} for rows, sample in sorted(self._full_graphs)
            ],
            "state_table": "fp32 device table",
            "staging": "padded layout reused per row count",
            "pinned_staging": self.pin_memory,
            "effect_capacity": self.effect_capacity,
            "relation_capacity": self.relation_capacity,
        }

    # -- verification --------------------------------------------------------

    @torch.inference_mode()
    def verify(
        self,
        batch: UniversalSemanticBatchV4,
        state: RecurrentPolicyStateV4,
        *,
        episode_start: torch.Tensor | None = None,
        tolerance: float = DEFAULT_TOLERANCE,
    ) -> dict[str, Any]:
        """Compare the fast forward with eager fp32 on the same batch and RNG.

        Both paths sample, so the generators are rewound between them; the
        live table and the caller's RNG are restored afterwards, which makes
        this safe to run between decisions as well as at startup.
        """

        starts = self._episode_start(episode_start, batch, state)
        table_before = RecurrentPolicyStateV4(hidden=self._table.hidden.clone(), cell=self._table.cell.clone())
        state_before = RecurrentPolicyStateV4(hidden=state.hidden.clone(), cell=state.cell.clone())
        rng = _save_rng(self.device)
        try:
            _restore_rng(self.device, rng)
            reference = self._eager(batch, state_before, starts)
            _restore_rng(self.device, rng)
            fast = self.sample(batch, state_before, episode_start=starts)
        finally:
            self.rebind(table_before)
            _restore_rng(self.device, rng)

        action_fields = (
            "gate",
            "micro_action_count",
            "candidate_index",
            "candidate_uid",
            "target_cell",
            "delay_offset_bin",
        )
        action_mismatches = {
            name: int(
                (getattr(reference.actions, name).detach().cpu() != getattr(fast.actions, name).detach().cpu()).sum()
            )
            for name in action_fields
        }
        deltas = {
            "log_prob": float((reference.log_prob - fast.log_prob).abs().max()),
            "entropy": float((reference.entropy - fast.entropy).abs().max()),
            "value": float((reference.value - fast.value).abs().max()),
        }
        actions_equal = not any(action_mismatches.values())
        return {
            "rows": int(batch.batch_size),
            "precision": self.precision,
            "cuda_graphs": self.cuda_graphs,
            "actions_equal": actions_equal,
            "action_mismatches": action_mismatches,
            "max_abs_delta": deltas,
            "tolerance": float(tolerance),
            "passed": bool(actions_equal and max(deltas.values()) <= float(tolerance)),
            "state_table_restored": True,
        }

    # -- internals -----------------------------------------------------------

    def _episode_start(
        self, episode_start: torch.Tensor | None, batch: UniversalSemanticBatchV4, state: RecurrentPolicyStateV4
    ) -> torch.Tensor:
        if int(batch.batch_size) != self.rows:
            raise ValueError(f"fast-forward was built for {self.rows} rows, got {int(batch.batch_size)}")
        if tuple(state.hidden.shape) != (self.rows, self._table.hidden.shape[1]):
            raise ValueError("fast-forward recurrent state does not match the built row count")
        if episode_start is None:
            return torch.zeros(int(batch.batch_size), dtype=torch.bool, device=self.device)
        starts = episode_start.detach().to(device=self.device, dtype=torch.bool)
        if tuple(starts.shape) != (int(batch.batch_size),):
            raise ValueError("episode_start must be bool [rows]")
        return starts

    def _decide(
        self,
        batch: UniversalSemanticBatchV4,
        state: RecurrentPolicyStateV4,
        *,
        episode_start: torch.Tensor | None,
        sample: bool,
    ) -> PolicyOutputV4:
        starts = self._episode_start(episode_start, batch, state)
        with torch.inference_mode():
            if self.full_cuda_graph:
                self.model.ensure_static_memory()
                _context, output = self._full_slot(batch, sample=sample).run(batch, state, starts)
                if output is None:  # defensive: a full slot always captures the decode
                    raise RuntimeError("a full-call graph slot did not capture the decode")
            elif self.cuda_graphs:
                self.model.ensure_static_memory()
                slot = self._slot(batch)
                context, _output = slot.run(batch, state, starts)
                output = self._decode(slot.stage.batch, context, sample=sample)
            else:
                model_batch = batch.to_model_input(self.device)
                with self._autocast():
                    context = self.model.forward(model_batch, state, episode_start=starts, validate=False)
                output = self._decode(model_batch, context, sample=sample)
            self._commit(output.next_state)
            output.next_state = self._table
            return output

    def _decode(self, batch: UniversalSemanticBatchV4, context: PolicyContextV4, *, sample: bool) -> PolicyOutputV4:
        with self._autocast():
            output = self.model.select_action_from_context(batch, context, sample=sample, validate=False)
        # The caller's PPO bookkeeping is fp32 even when the forward ran in half.
        output.log_prob = output.log_prob.float()
        output.entropy = output.entropy.float()
        output.value = output.value.float()
        return output

    def _commit(self, next_state: RecurrentPolicyStateV4) -> None:
        _copy_state_(self._table, next_state)

    def _eager(
        self, batch: UniversalSemanticBatchV4, state: RecurrentPolicyStateV4, episodes: torch.Tensor
    ) -> PolicyOutputV4:
        model_batch = batch.to_model_input(self.device)
        output = self.model.sample_for_ppo_rollout(model_batch, state, episode_start=episodes, validate=False)
        output.log_prob = output.log_prob.float()
        output.entropy = output.entropy.float()
        output.value = output.value.float()
        return output


def _save_rng(device: torch.device) -> dict[str, Any]:
    return {
        "torch": torch.get_rng_state(),
        "cuda": torch.cuda.get_rng_state_all() if device.type == "cuda" else None,
    }


def _restore_rng(device: torch.device, state: dict[str, Any]) -> None:
    torch.set_rng_state(state["torch"])
    if device.type == "cuda" and state["cuda"] is not None:
        torch.cuda.set_rng_state_all(state["cuda"])
