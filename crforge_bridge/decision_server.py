"""One forward for many producers: the batched decision server.

The C4 producer owns one ``VectorBattle`` and one forward per process.  On a GPU
box that is one core and one copy of the model per producer, and the card serves
about four of them at the full-graph share (``ROADMAP_CRFORGE.md`` §4).  The
scale-out step past that is the shape the field uses: keep the collectors on the
CPU half and serve every waiting collector's batch in **one forward call** from a
process that owns the card.

This module is that server, plus the client the producer uses.  It does not port
``ever_server``'s protocol (``FINDINGS.md`` §3.72): the wire here carries the
producer's already-tensorized batch, the recurrent state and the episode-start
flags, and comes back with the sampled action sequence, log-probability,
entropy, value and next state — exactly the ``PolicyOutputV4`` the in-process
path produces.

Two things make the batch joinable at all:

* **Live widths are bucketed, not fixed.**  A producer sends the record at the
  board's live group/child width (session 43) and two producers' boards differ,
  so the server pads every request in a round to the widest bucket the ladder
  offers (``GROUP_BUCKETS`` and friends) before joining them.  The bucket is part
  of the forward's shape, so a run reuses a handful of graphs instead of
  capturing one per board (``FINDINGS.md`` §3.77).
* **The row axis is bucketed too.**  ``k`` requests of ``R`` rows join to
  ``k × R`` and pad up to the next power-of-two multiple of ``R``, so the forward
  sees a stable set of row counts (the same trick as ``FastForwardV4``'s graph
  per padded row count).

The gates are behavioural, in the same spirit as ``FastForwardV4.verify``:

1. **Identity.**  A client handshakes with the checkpoint id, the checkpoint
   hash and a digest of the sampling weights.  The server refuses a client whose
   digest differs, so a server started from another checkpoint cannot silently
   label its samples with this run's policy state.
2. **Sampling.**  A request may carry a ``seed``; the server seeds the
   generator immediately before that round and serves it alone.  A client that
   seeds its own model identically must then draw the *same* action sequence, so
   ``self_play.py --verify-decision-server`` compares the server's sampled
   actions against the in-process eager path on the same batch.
3. **State.**  The recurrent state is the client's, sent with the request and
   replaced by the response's ``next_state``; the server keeps no state of its
   own between rounds, so two producers cannot leak a carry into each other.
4. **Weights.**  A training producer pushes its updated state dict at every
   segment boundary (``DecisionClient.push_weights``).  The push names the
   weights the client last sampled with and the digest of the new ones, the
   server copies them into its own parameters **in place** (so captured graphs
   and staging stay valid) and re-hashes the result, and a push that does not
   hash to the client's policy, or that arrives against a server whose weights
   moved first, is refused.  A producer that trains behind the server therefore
   samples the weights its rollout is labelled with (``FINDINGS.md`` §3.82).

The gate the push protects is the stale-data one: the rollout store binds the
sampling weights' digest, so a push that silently failed would mislabel every
row it collected afterwards.
"""

from __future__ import annotations

import os
import pickle
import select
import socket
import struct
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Any, Self

import torch
from torch import Tensor

from native_runner.training.v4.tensors import (
    ActiveEffectSetV4,
    PolicyOutputV4,
    RecurrentPolicyStateV4,
    RelationEdgesV4,
    UniversalSemanticBatchV4,
    concatenate_tensor_records,
    tensor_padding_value,
)

from .fast_forward import FastForwardV4
from .training import state_digest, weights_digest
from .vector_rows import pad_group_record

SCHEMA = "crforge-decision-server.v1"
DEFAULT_WAIT_MS = 2.0
DEFAULT_MAX_REQUESTS = 8
# The ladders are the graph's shape vocabulary: a round pads every request up to
# the smallest bucket that holds the widest live width in the round.  The top
# entry of each ladder is the model's own capacity.
GROUP_BUCKETS = (1, 2, 4, 8, 12, 16, 24, 32, 48)
CHILD_BUCKETS = (1, 4, 8, 16, 32, 64, 96, 160)
RELATION_BUCKETS = (0, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048)
EFFECT_BUCKETS = (0, 4, 8, 16, 32, 64, 128, 256, 512)
MODES = ("sample", "greedy", "value")

_PREFIX = struct.Struct("!cQ")
_MESSAGE = b"M"
_MAX_HEADER_BYTES = 64 * 1024 * 1024
_MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
_MAX_PAYLOADS = 32
# The idle poll lets ``stop`` close a server that has no clients waiting; the
# forward itself is never polled, only the accept/read loop.
_IDLE_POLL_SECONDS = 0.1


# ---------------------------------------------------------------------------
# shape helpers
# ---------------------------------------------------------------------------


def bucket_for(value: int, ladder: Sequence[int]) -> int:
    """Smallest ladder entry at or above ``value`` (the value itself if taller)."""

    value = int(value)
    for entry in ladder:
        if entry >= value:
            return int(entry)
    return value


def _rows_of(record: object) -> int:
    if isinstance(record, Tensor):
        if record.ndim < 1:
            raise ValueError("a packed record needs a batch axis")
        return int(record.shape[0])
    if is_dataclass(record):
        for field in fields(record):
            value = getattr(record, field.name)
            if isinstance(value, Tensor):
                return int(value.shape[0])
            if is_dataclass(value):
                return _rows_of(value)
    raise TypeError("a packed record must contain a tensor")


def _pad_axis_one(tensor: Tensor, width: int, padding: float | bool) -> Tensor:
    if int(tensor.shape[1]) == int(width):
        return tensor
    if int(tensor.shape[1]) > int(width):
        raise ValueError(f"cannot pad axis 1 from {int(tensor.shape[1])} down to {int(width)}")
    output = tensor.new_full((int(tensor.shape[0]), int(width), *tensor.shape[2:]), padding)
    output[:, : int(tensor.shape[1])] = tensor
    return output


def canonical_batch(
    batch: UniversalSemanticBatchV4, *, groups: int, children: int, relations: int, effects: int
) -> UniversalSemanticBatchV4:
    """Project one producer's live-width batch onto a round's bucket widths.

    Only the four variable axes move: the group/child axes (live width,
    ``FINDINGS.md`` §3.76–§3.79) and the active-effect/relation axes (already
    padded by ``FastForwardV4`` for a graph).  Every other axis is the model's
    fixed capacity and must already match, which is what makes two requests
    joinable with ``concatenate_tensor_records``.
    """

    padded_groups = pad_group_record(batch.groups, groups=int(groups), children=int(children))
    if int(effects) < int(batch.active_effects.mask.shape[1]):
        raise ValueError("the effect bucket is narrower than the live effect rows")
    if int(relations) < int(batch.relation_edges.mask.shape[1]):
        raise ValueError("the relation bucket is narrower than the live relation rows")
    padded_effects = ActiveEffectSetV4(
        effect_vocab_id=_pad_axis_one(batch.active_effects.effect_vocab_id, effects, 0),
        parent_type=_pad_axis_one(batch.active_effects.parent_type, effects, 0),
        parent_index=_pad_axis_one(batch.active_effects.parent_index, effects, -1),
        source_owner_type=_pad_axis_one(batch.active_effects.source_owner_type, effects, 0),
        runtime_features=_pad_axis_one(batch.active_effects.runtime_features, effects, 0.0),
        mask=_pad_axis_one(batch.active_effects.mask, effects, False),
    )
    padded_relations = RelationEdgesV4(
        source=_pad_axis_one(batch.relation_edges.source, relations, 0),
        target=_pad_axis_one(batch.relation_edges.target, relations, 0),
        relation_type=_pad_axis_one(batch.relation_edges.relation_type, relations, 0),
        mask=_pad_axis_one(batch.relation_edges.mask, relations, False),
    )
    return replace(batch, groups=padded_groups, active_effects=padded_effects, relation_edges=padded_relations)


def pad_rows(record: object, rows: int) -> object:
    """Grow a nested record's batch axis with canonical, masked padding rows."""

    target = int(rows)
    current = _rows_of(record)
    if target < current:
        raise ValueError(f"cannot pad {current} rows down to {target}")

    def pad_record(value: object) -> object:
        if is_dataclass(value):
            return type(value)(**{field.name: pad_field(value, field.name) for field in fields(value)})
        if isinstance(value, tuple):
            return tuple(pad_record(item) for item in value)
        if isinstance(value, list):
            return [pad_record(item) for item in value]
        if isinstance(value, dict):
            return {key: pad_record(item) for key, item in value.items()}
        raise TypeError(f"cannot pad rows through {type(value).__name__}")

    def pad_field(parent: object, name: str) -> object:
        value = getattr(parent, name)
        if isinstance(value, Tensor):
            if int(value.shape[0]) != current:
                raise ValueError("record fields disagree on the batch axis")
            if target == current:
                return value
            padding = tensor_padding_value(parent, name)
            filler = value.new_full((target - current, *value.shape[1:]), padding)
            return torch.cat((value, filler), dim=0)
        return pad_record(value)

    return pad_record(record)


def row_bucket(total: int, quantum: int) -> int:
    """Next power-of-two multiple of ``quantum`` at or above ``total``."""

    if total < 1 or quantum < 1:
        raise ValueError("row buckets need a positive row count")
    multiple = 1
    while quantum * multiple < int(total):
        multiple *= 2
    return quantum * multiple


# ---------------------------------------------------------------------------
# row-packed records
# ---------------------------------------------------------------------------


class PackedRecord:
    """One nested record packed into a small number of row-major buffers.

    The schema is the same shape ``_RowPackedTensorRecordV4`` uses: one buffer
    per dtype, each tensor a ``(rows, width)`` slice at ``offset``, restored as
    a view so the receiver never allocates a tensor per field.
    """

    __slots__ = ("batch_size", "buffer_dtypes", "buffers", "schema", "source_dtypes")

    def __init__(
        self,
        schema: object,
        batch_size: int,
        source_dtypes: tuple[torch.dtype, ...],
        buffer_dtypes: dict[torch.dtype, torch.dtype],
        buffers: dict[torch.dtype, Tensor],
    ) -> None:
        self.schema = schema
        self.batch_size = int(batch_size)
        self.source_dtypes = source_dtypes
        self.buffer_dtypes = dict(buffer_dtypes)
        self.buffers = dict(buffers)

    # -- packing ------------------------------------------------------------

    @classmethod
    def from_record(cls, record: object) -> PackedRecord:
        source_tensors: list[tuple[torch.dtype, int, int, Tensor]] = []
        widths: dict[torch.dtype, int] = {}
        batch_size: int | None = None

        def describe(value: object) -> object:
            nonlocal batch_size
            if isinstance(value, Tensor):
                if value.ndim < 1:
                    raise ValueError("a packed tensor needs a batch axis")
                if int(value.numel()) == 0:
                    # An empty catalog buffer carries no bytes; the schema is its
                    # whole content, so the receiver rebuilds it into the same
                    # tensor rather than skipping a named field (the weight hash
                    # names it, so the record has to carry it).
                    return ("empty", value.dtype, tuple(int(size) for size in value.shape))
                rows = int(value.shape[0])
                if batch_size is None:
                    batch_size = rows
                elif rows != batch_size:
                    raise ValueError("packed tensor records disagree on the batch axis")
                if rows < 1:
                    raise ValueError("cannot pack an empty tensor record")
                width = int(value.numel()) // rows
                offset = widths.get(value.dtype, 0)
                widths[value.dtype] = offset + width
                source_tensors.append((value.dtype, offset, width, value))
                return ("tensor", value.dtype, tuple(int(size) for size in value.shape[1:]), offset, width)
            if is_dataclass(value) and not isinstance(value, type):
                return (
                    "dataclass",
                    type(value),
                    tuple((field.name, describe(getattr(value, field.name))) for field in fields(value)),
                )
            if isinstance(value, tuple):
                return ("tuple", tuple(describe(item) for item in value))
            if isinstance(value, list):
                return ("list", tuple(describe(item) for item in value))
            if isinstance(value, dict):
                return ("dict", tuple((describe(key), describe(item)) for key, item in value.items()))
            return ("value", value)

        schema = describe(record)
        if batch_size is None:
            raise ValueError("a packed record must contain a tensor")
        buffers = {dtype: torch.empty((batch_size, width), dtype=dtype) for dtype, width in widths.items()}
        for dtype, offset, width, tensor in source_tensors:
            buffers[dtype][:, offset : offset + width] = tensor.reshape(batch_size, width)
        return cls(schema, batch_size, tuple(widths), {dtype: dtype for dtype in widths}, buffers)

    def descriptor(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "batch_size": self.batch_size,
            "source_dtypes": self.source_dtypes,
            "buffer_dtypes": self.buffer_dtypes,
            "buffer_shapes": tuple(
                tuple(int(size) for size in self.buffers[dtype].shape) for dtype in self.source_dtypes
            ),
        }

    def payloads(self) -> tuple[memoryview, ...]:
        return tuple(
            memoryview(self.buffers[dtype].detach().contiguous().reshape(-1).view(torch.uint8).numpy())
            for dtype in self.source_dtypes
        )

    # -- restoring ----------------------------------------------------------

    @classmethod
    def from_payload(
        cls, descriptor: dict[str, Any], payloads: Sequence[bytearray], *, device: str | torch.device = "cpu"
    ) -> PackedRecord:
        source_dtypes = descriptor.get("source_dtypes")
        buffer_dtypes = descriptor.get("buffer_dtypes")
        shapes = descriptor.get("buffer_shapes")
        batch_size = descriptor.get("batch_size")
        schema = descriptor.get("schema")
        if (
            not isinstance(source_dtypes, tuple)
            or not isinstance(buffer_dtypes, dict)
            or not isinstance(shapes, tuple)
            or len(shapes) != len(source_dtypes)
            or not isinstance(batch_size, int)
            or batch_size < 1
            or not isinstance(schema, (tuple, list))
        ):
            raise ValueError("packed record descriptor is malformed")
        if len(payloads) != len(source_dtypes):
            raise ValueError("packed record payload count changed")
        buffers: dict[torch.dtype, Tensor] = {}
        for source_dtype, shape, payload in zip(source_dtypes, shapes, payloads, strict=True):
            if not isinstance(source_dtype, torch.dtype):
                raise TypeError("packed record dtype is malformed")
            buffer_dtype = buffer_dtypes.get(source_dtype)
            if buffer_dtype != source_dtype:
                raise ValueError("packed record dtype was converted in flight")
            if not isinstance(shape, tuple) or len(shape) != 2 or int(shape[0]) != batch_size:
                raise ValueError("packed record buffer shape is malformed")
            count = int(shape[0]) * int(shape[1])
            expected = count * torch.empty((), dtype=buffer_dtype).element_size()
            if len(payload) != expected:
                raise ValueError("packed record payload byte count changed")
            buffer = torch.frombuffer(payload, dtype=buffer_dtype, count=count).reshape(shape)
            buffers[source_dtype] = buffer.to(device)
        return cls(schema, batch_size, source_dtypes, buffer_dtypes, buffers)

    def record(self) -> object:
        def restore(item: object) -> object:
            if not isinstance(item, (tuple, list)) or len(item) < 1:
                raise ValueError("packed record schema is malformed")
            kind = item[0]
            if kind == "tensor":
                _kind, dtype, tail, offset, width = item
                flat = self.buffers[dtype][:, int(offset) : int(offset) + int(width)]
                return flat.reshape((self.batch_size, *tail))
            if kind == "empty":
                _kind, dtype, shape = item
                return torch.empty(tuple(int(size) for size in shape), dtype=dtype)
            if kind == "dataclass":
                return item[1](**{name: restore(child) for name, child in item[2]})
            if kind == "tuple":
                return tuple(restore(child) for child in item[1])
            if kind == "list":
                return [restore(child) for child in item[1]]
            if kind == "dict":
                return {restore(key): restore(child) for key, child in item[1]}
            if kind == "value":
                return item[1]
            raise ValueError(f"unknown packed record node {kind!r}")

        return restore(self.schema)


def pack_state_dict(model: Any) -> PackedRecord:
    """One model's parameters and buffers as a single packed record.

    Every tensor is flattened to one row so the record has a single batch axis
    and one buffer per dtype: the state crosses the wire in one copy per dtype
    rather than one allocation per parameter.  Each weight's original shape
    rides in the schema's shape map, so the receiver restores parameters, not
    byte ranges, and parameters and buffers stay separate so it can hash the
    incoming bytes exactly as ``weights_digest`` would.
    """

    def flattened(tensors: Mapping[str, Tensor]) -> dict[str, Any]:
        values: dict[str, Tensor] = {}
        shapes: dict[str, tuple[int, ...]] = {}
        for name, value in tensors.items():
            values[str(name)] = value.detach().reshape(1, -1) if int(value.numel()) else value.detach()
            shapes[str(name)] = tuple(int(size) for size in value.shape)
        return {"values": values, "shapes": shapes}

    parameters = flattened(dict(model.named_parameters()))
    buffers = flattened(dict(model.named_buffers()))
    if not parameters["values"]:
        raise ValueError("cannot push a model with no parameters")
    return PackedRecord.from_record({"parameters": parameters, "buffers": buffers})


def unpack_state_dict(record: object) -> dict[str, Tensor]:
    """The inverse of :func:`pack_state_dict`: names back to shaped tensors."""

    state: dict[str, Tensor] = {}
    if not isinstance(record, dict) or not {"parameters", "buffers"} <= set(record):
        raise TypeError("a weights push must carry parameters and buffers")
    for part_name in ("parameters", "buffers"):
        part = record[part_name]
        if not isinstance(part, dict) or not {"values", "shapes"} <= set(part):
            raise TypeError(f"a weights push must carry {part_name} values and shapes")
        values, shapes = part["values"], part["shapes"]
        if not isinstance(values, dict) or not isinstance(shapes, dict) or set(values) != set(shapes):
            raise ValueError(f"the pushed {part_name} names and shapes disagree")
        for name, value in values.items():
            if not isinstance(value, Tensor):
                raise TypeError(f"the pushed {part_name} {name!r} is not a tensor")
            shape = tuple(int(size) for size in shapes[name])
            expected = 1
            for size in shape:
                expected *= size
            if int(value.numel()) != expected:
                raise ValueError(f"the pushed {part_name} {name!r} does not hold {shape}")
            state[str(name)] = value.reshape(shape)
    return state


# ---------------------------------------------------------------------------
# framing
# ---------------------------------------------------------------------------


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    output = bytearray(size)
    _recv_into(sock, output)
    return bytes(output)


def _recv_into(sock: socket.socket, output: bytearray) -> None:
    view = memoryview(output)
    cursor = 0
    while cursor < len(output):
        received = sock.recv_into(view[cursor:])
        if received == 0:
            raise EOFError("decision-server connection closed")
        cursor += received


def send_frame(sock: socket.socket, header: dict[str, Any], payloads: Sequence[memoryview] = ()) -> None:
    encoded = pickle.dumps(header, protocol=5)
    if len(encoded) > _MAX_HEADER_BYTES:
        raise ValueError("decision-server frame header is too large")
    sock.sendall(_PREFIX.pack(_MESSAGE, len(encoded)))
    sock.sendall(encoded)
    for payload in payloads:
        sock.sendall(payload)


def recv_frame(sock: socket.socket) -> tuple[dict[str, Any], list[bytearray]]:
    kind, header_size = _PREFIX.unpack(_recv_exact(sock, _PREFIX.size))
    if kind != _MESSAGE:
        raise ValueError("decision-server frame kind is invalid")
    if header_size > _MAX_HEADER_BYTES:
        raise ValueError("decision-server frame header exceeds its bound")
    header = pickle.loads(_recv_exact(sock, int(header_size)))
    if not isinstance(header, dict):
        raise TypeError("decision-server frame header is not a mapping")
    sizes = header.get("bytes", ())
    if not isinstance(sizes, (tuple, list)) or len(sizes) > _MAX_PAYLOADS:
        raise ValueError("decision-server payload list is malformed")
    if sum(int(size) for size in sizes) > _MAX_PAYLOAD_BYTES:
        raise ValueError("decision-server frame payload exceeds its bound")
    payloads: list[bytearray] = []
    for size in sizes:
        payload = bytearray(int(size))
        _recv_into(sock, payload)
        payloads.append(payload)
    return header, payloads


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DecisionRequest:
    """One producer's decision, as the server's round sees it."""

    mode: str
    batch: UniversalSemanticBatchV4
    state: RecurrentPolicyStateV4
    episode_start: Tensor
    seed: int | None
    arrived: float

    @property
    def rows(self) -> int:
        return int(self.batch.batch_size)


@dataclass(slots=True)
class DecisionResult:
    """One producer's slice of the round's forward."""

    output: PolicyOutputV4 | None
    value: Tensor | None
    rows: int


class DecisionServer:
    """Own one forward and serve every waiting producer's batch in one call."""

    def __init__(
        self,
        model: Any,
        *,
        device: str | torch.device = "cpu",
        precision: str = "auto",
        cuda_graphs: bool = True,
        full_cuda_graph: bool = False,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        wait_ms: float = DEFAULT_WAIT_MS,
        checkpoint_id: str | None = None,
        checkpoint_sha256: str | None = None,
        weights_digest: str | None = None,
        identity: dict[str, Any] | None = None,
    ) -> None:
        if int(max_requests) < 1:
            raise ValueError("the decision server needs at least one request per round")
        if not 0.0 <= float(wait_ms) <= 1000.0:
            raise ValueError("the decision server wait must be in 0..1000 ms")
        self.model = model
        self.device = torch.device(device)
        self.config = model.config
        self.precision = precision
        self.cuda_graphs = bool(cuda_graphs)
        self.full_cuda_graph = bool(full_cuda_graph)
        self.max_requests = int(max_requests)
        self.wait_seconds = float(wait_ms) / 1000.0
        self.checkpoint_id = checkpoint_id
        self.checkpoint_sha256 = checkpoint_sha256
        self.weights_digest = weights_digest
        self.identity = dict(identity or {})
        self._forwards: dict[tuple[int, ...], FastForwardV4] = {}
        # The group/child axes are the model's own capacities.  The effect and
        # relation axes have no config capacity: ``FastForwardV4`` pads them to
        # its staging capacities, so these ladders end at the same numbers.
        self._capacity = {
            "groups": int(self.config.max_battle_groups),
            "children": int(self.config.max_children_total),
            "relations": RELATION_BUCKETS[-1],
            "effects": EFFECT_BUCKETS[-1],
        }
        if self._capacity["groups"] > GROUP_BUCKETS[-1] or self._capacity["children"] > CHILD_BUCKETS[-1]:
            raise ValueError("the group/child ladders must cover the model's capacities")
        self.stats: dict[str, Any] = {
            "rounds": 0,
            "requests": 0,
            "rows": 0,
            "max_requests_per_round": 0,
            "max_rows_per_round": 0,
            "forward_ms_sum": 0.0,
            "forward_ms_max": 0.0,
            "assemble_ms_sum": 0.0,
            "assemble_ms_max": 0.0,
            "queue_wait_ms_sum": 0.0,
            "queue_wait_ms_max": 0.0,
            "sequence_ms_sum": 0.0,
            "failed_rounds": 0,
            "weight_pushes": 0,
            "row_buckets": {},
            "shape_buckets": {},
            "modes": {},
            "clients": 0,
        }

    # -- public surface ------------------------------------------------------

    def forward(self, rows: int, shape: Mapping[str, int] | None = None) -> FastForwardV4:
        """The forward built for one padded row *and* width bucket.

        A capture freezes the batch's shape.  The row axis is bucketed, and so is
        every live width (session 43 sends the board's live group/child width),
        so the key has to be the whole shape: keying by rows alone lets the first
        round of a segment fix the graph at an empty board's width and the next
        round overflow it.  The card's run therefore captures one graph per
        (rows, groups, children, relations, effects) tuple it visits.
        """

        bucket = {
            "groups": int((shape or {}).get("groups", 0)),
            "children": int((shape or {}).get("children", 0)),
            "relations": int((shape or {}).get("relations", 0)),
            "effects": int((shape or {}).get("effects", 0)),
        }
        key = (
            int(rows),
            bucket["groups"],
            bucket["children"],
            bucket["relations"],
            bucket["effects"],
        )
        fast = self._forwards.get(key)
        if fast is None:
            fast = FastForwardV4(
                self.model,
                device=self.device,
                rows=int(rows),
                precision=self.precision,
                cuda_graphs=self.cuda_graphs,
                full_cuda_graph=self.full_cuda_graph,
            )
            self._forwards[key] = fast
        return fast

    def run_round(self, requests: Sequence[DecisionRequest]) -> list[DecisionResult]:
        """Join, forward and split one round; results keep the request order."""

        if not requests:
            raise ValueError("a decision round needs at least one request")
        modes = {request.mode for request in requests}
        if len(modes) > 1:
            raise ValueError(f"a decision round mixes modes: {sorted(modes)}")
        mode = next(iter(modes))
        if mode not in MODES:
            raise ValueError(f"unknown decision mode {mode!r}")
        seeded = [request for request in requests if request.seed is not None]
        if seeded and len(requests) > 1:
            raise ValueError("a seeded request must be served alone; its generator is global")
        if seeded:
            seed = int(seeded[0].seed)
            torch.manual_seed(seed)
            if self.device.type == "cuda":
                torch.cuda.manual_seed_all(seed)

        shape = {
            "groups": bucket_for(max(int(r.batch.groups.mask.shape[1]) for r in requests), GROUP_BUCKETS),
            "children": bucket_for(max(int(r.batch.groups.child_mask.shape[1]) for r in requests), CHILD_BUCKETS),
            "relations": bucket_for(max(int(r.batch.relation_edges.mask.shape[1]) for r in requests), RELATION_BUCKETS),
            "effects": bucket_for(max(int(r.batch.active_effects.mask.shape[1]) for r in requests), EFFECT_BUCKETS),
        }
        for name, value in shape.items():
            if value > self._capacity[name]:
                raise ValueError(f"a request's {name} width exceeds the model capacity")
        rows_total = sum(request.rows for request in requests)
        rows = row_bucket(rows_total, requests[0].rows)

        started = time.perf_counter()
        batches = [
            canonical_batch(
                request.batch,
                groups=shape["groups"],
                children=shape["children"],
                relations=shape["relations"],
                effects=shape["effects"],
            )
            for request in requests
        ]
        joined = concatenate_tensor_records(tuple(batches))
        states = concatenate_tensor_records(tuple(request.state for request in requests))
        starts = torch.cat(tuple(request.episode_start.detach().to("cpu", torch.bool) for request in requests))
        if int(joined.batch_size) != rows_total or int(states.hidden.shape[0]) != rows_total:
            raise ValueError("the joined round lost rows")
        joined = pad_rows(joined, rows)
        states = pad_rows(states, rows)
        if rows_total != rows:
            starts = torch.cat((starts, torch.zeros(rows - rows_total, dtype=torch.bool)))
        states = RecurrentPolicyStateV4(
            hidden=states.hidden.to(dtype=torch.float32), cell=states.cell.to(dtype=torch.float32)
        )
        assemble_ms = (time.perf_counter() - started) * 1000.0

        fast = self.forward(rows, shape)
        forward_started = time.perf_counter()
        with torch.inference_mode():
            if mode == "value":
                output = None
                context = fast.forward(joined, states, episode_start=starts)
                value = context.value.detach().float()
            elif mode == "greedy":
                output = fast.act(joined, states, episode_start=starts)
                value = None
            else:
                output = fast.sample(joined, states, episode_start=starts)
                value = None
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
        sequence_ms = (time.perf_counter() - forward_started) * 1000.0

        results: list[DecisionResult] = []
        offset = 0
        for request in requests:
            length = request.rows
            if output is not None:
                results.append(DecisionResult(output=output.narrow_batch(offset, length), value=None, rows=length))
            else:
                assert value is not None
                results.append(DecisionResult(output=None, value=value.narrow(0, offset, length), rows=length))
            offset += length

        self.stats["rounds"] += 1
        self.stats["requests"] += len(requests)
        self.stats["rows"] += rows_total
        self.stats["max_requests_per_round"] = max(self.stats["max_requests_per_round"], len(requests))
        self.stats["max_rows_per_round"] = max(self.stats["max_rows_per_round"], rows_total)
        self.stats["forward_ms_sum"] += sequence_ms
        self.stats["forward_ms_max"] = max(self.stats["forward_ms_max"], sequence_ms)
        self.stats["assemble_ms_sum"] += assemble_ms
        self.stats["assemble_ms_max"] = max(self.stats["assemble_ms_max"], assemble_ms)
        self.stats["sequence_ms_sum"] += sequence_ms
        self.stats["row_buckets"][str(rows)] = self.stats["row_buckets"].get(str(rows), 0) + 1
        key = f"{shape['groups']}/{shape['children']}/{shape['relations']}/{shape['effects']}"
        self.stats["shape_buckets"][key] = self.stats["shape_buckets"].get(key, 0) + 1
        self.stats["modes"][mode] = self.stats["modes"].get(mode, 0) + 1
        return results

    def apply_weights(
        self,
        state: Mapping[str, Tensor],
        *,
        checkpoint_sha256: str | None,
        base_digest: str | None,
        digest: str | None = None,
    ) -> dict[str, Any]:
        """Copy a client's updated weights into the served model, in place.

        In place is the whole point: the captured forwards, the staging buffers
        and the pinned mirrors hold this model's ``Parameter`` objects, so a new
        set of values keeps every graph valid while the *identity* changes.  The
        push is refused unless the client's checkpoint and its view of the
        server's current weights both match, and the copy is then re-hashed and
        required to equal the client's digest — a push is either exactly the
        client's policy bytes or it does not land.
        """

        if not self.checkpoint_sha256 or checkpoint_sha256 != self.checkpoint_sha256:
            raise ValueError(
                "weights push refused: the server serves checkpoint "
                f"{self.checkpoint_sha256!r}, the client has {checkpoint_sha256!r}"
            )
        if self.weights_digest is None:
            raise ValueError("weights push refused: the server was not given a weight identity")
        if base_digest != self.weights_digest:
            raise ValueError(
                "weights push refused: the client sampled "
                f"{base_digest!r} but the server holds {self.weights_digest!r} — its weights moved "
                "under the client; reconnect and resample"
            )
        parameters = dict(self.model.named_parameters())
        buffers = dict(self.model.named_buffers())
        # ``state_dict`` omits non-persistent buffers; the identity hash does not,
        # so the push carries every named parameter and buffer and the target set
        # is that union, not the persistent state.
        target = {**parameters, **buffers}
        if set(state) != set(target):
            missing = sorted(set(state) - set(target))
            extra = sorted(set(target) - set(state))
            raise ValueError(f"weights push refused: missing {missing[:4]}, unexpected {extra[:4]}")
        # Validate every shape and dtype, and hash the incoming bytes, before the
        # first store: a refused push must not leave half of a policy in the model.
        for name, value in state.items():
            expected = target[name]
            if tuple(value.shape) != tuple(expected.shape):
                raise ValueError(
                    f"weights push refused: {name} is {tuple(value.shape)} in the push, "
                    f"{tuple(expected.shape)} in the server"
                )
            if value.dtype != expected.dtype:
                raise ValueError(
                    f"weights push refused: {name} is {value.dtype} in the push, {expected.dtype} in the server"
                )
        incoming = state_digest(
            {name: state[name] for name in parameters}, {name: state[name] for name in buffers}
        )
        if digest is not None and incoming != digest:
            raise ValueError(
                "weights push refused: the pushed weights hash to "
                f"{incoming!r}, the client's policy is {digest!r}"
            )
        started = time.perf_counter()
        with torch.no_grad():
            for name, value in state.items():
                expected = target[name]
                expected.copy_(value.to(device=expected.device))
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
        measured = weights_digest(self.model)
        apply_ms = (time.perf_counter() - started) * 1000.0
        if measured != incoming:  # defensive: the copy is why the digest is the identity
            raise RuntimeError(f"weights push lost bytes: the server hashes to {measured!r}, the push to {incoming!r}")
        self.weights_digest = measured
        self.stats["weight_pushes"] += 1
        return {
            "kind": "weights",
            "checkpoint_sha256": self.checkpoint_sha256,
            "weights_digest": measured,
            "apply_ms": apply_ms,
        }

    def describe(self) -> dict[str, Any]:
        rounds = max(int(self.stats["rounds"]), 1)
        requests = max(int(self.stats["requests"]), 1)
        return {
            "schema": SCHEMA,
            "device": str(self.device),
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_sha256": self.checkpoint_sha256,
            "weights_digest": self.weights_digest,
            "precision": self.precision,
            "cuda_graphs": self.cuda_graphs,
            "full_cuda_graph": self.full_cuda_graph,
            "wait_ms": self.wait_seconds * 1000.0,
            "max_requests": self.max_requests,
            "captured_row_counts": sorted({key[0] for key in self._forwards}),
            "captured_shapes": [
                {"rows": key[0], "groups": key[1], "children": key[2], "relations": key[3], "effects": key[4]}
                for key in sorted(self._forwards)
            ],
            "forwards": [self._forwards[key].describe() for key in sorted(self._forwards)],
            "rounds": int(self.stats["rounds"]),
            "requests": int(self.stats["requests"]),
            "rows": int(self.stats["rows"]),
            "max_requests_per_round": int(self.stats["max_requests_per_round"]),
            "max_rows_per_round": int(self.stats["max_rows_per_round"]),
            "requests_per_round": int(self.stats["requests"]) / rounds,
            "rows_per_round": int(self.stats["rows"]) / rounds,
            "forward_ms_mean": float(self.stats["forward_ms_sum"]) / rounds,
            "forward_ms_max": float(self.stats["forward_ms_max"]),
            "forward_ms_per_request": float(self.stats["forward_ms_sum"]) / requests,
            "assemble_ms_mean": float(self.stats["assemble_ms_sum"]) / rounds,
            "assemble_ms_max": float(self.stats["assemble_ms_max"]),
            "row_buckets": dict(self.stats["row_buckets"]),
            "shape_buckets": dict(self.stats["shape_buckets"]),
            "modes": dict(self.stats["modes"]),
            "failed_rounds": int(self.stats["failed_rounds"]),
            "weight_pushes": int(self.stats["weight_pushes"]),
            "identity": self.identity,
        }

    def handshake(self, header: dict[str, Any], *, require_weights: bool = True) -> dict[str, Any]:
        """Refuse a client that was not built from this server's weights.

        A ``stats`` reader only needs the checkpoint: counters are read-only and
        cannot sample, so the weight digest is not required there (a supervisor
        that opened before a push would otherwise be locked out of a server it
        is supervising).
        """

        if header.get("checkpoint_sha256") != self.checkpoint_sha256:
            raise ValueError(
                "checkpoint hash mismatch: the server serves "
                f"{self.checkpoint_sha256!r}, the client has {header.get('checkpoint_sha256')!r}"
            )
        if require_weights and header.get("weights_digest") != self.weights_digest:
            raise ValueError("weights digest mismatch: the server's sampling weights are not this checkpoint's")
        return {
            "kind": "hello",
            "schema": SCHEMA,
            "device": str(self.device),
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_sha256": self.checkpoint_sha256,
            "weights_digest": self.weights_digest,
            "precision": self.precision,
            "cuda_graphs": self.cuda_graphs,
            "max_requests": self.max_requests,
            "wait_ms": self.wait_seconds * 1000.0,
        }

    # -- the wire ------------------------------------------------------------

    def serve(
        self,
        address: str,
        *,
        stop: Callable[[], bool] | None = None,
        on_ready: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Serve decision requests until ``stop`` says otherwise.

        One thread on purpose: the forward is the only long operation and the
        sockets are idle while it runs, so a second thread would add a GIL
        contender to a process whose whole job is to keep the card fed.  Requests
        that arrive during a forward wait in their socket buffers and are read on
        the next round, which is exactly the batching this server exists for.
        """

        listener, shutdown = _listen(address)
        try:
            descriptor = {
                "schema": SCHEMA,
                "address": address,
                "device": str(self.device),
                "checkpoint_id": self.checkpoint_id,
                "checkpoint_sha256": self.checkpoint_sha256,
                "weights_digest": self.weights_digest,
                "precision": self.precision,
                "max_requests": self.max_requests,
                "wait_ms": self.wait_seconds * 1000.0,
            }
            if on_ready is not None:
                on_ready(descriptor)
            clients: set[socket.socket] = set()
            queue: deque[tuple[socket.socket, int, DecisionRequest]] = deque()
            deadline: float | None = None
            while True:
                if stop is not None and stop():
                    break
                timeout = _IDLE_POLL_SECONDS if deadline is None else max(0.0, deadline - time.perf_counter())
                try:
                    readable, _, _ = select.select([listener, *clients], [], [], timeout)
                except InterruptedError:  # pragma: no cover - signal timing
                    continue
                for sock in readable:
                    if sock is listener:
                        connection, _ = listener.accept()
                        connection.settimeout(30.0)
                        clients.add(connection)
                        self.stats["clients"] += 1
                        continue
                    try:
                        header, payloads = recv_frame(sock)
                    except (EOFError, OSError):
                        clients.discard(sock)
                        _close(sock)
                        continue
                    accepted = self._accept(sock, header, payloads, clients)
                    if accepted is not None:
                        queue.append(accepted)
                if queue:
                    # The first request opens the wait window; the round runs
                    # when the window closes or the round is full, so a burst of
                    # producers is served by one forward and a lone producer
                    # waits one window.
                    if deadline is None:
                        deadline = time.perf_counter() + self.wait_seconds
                    if len(queue) >= self.max_requests or time.perf_counter() >= deadline:
                        self._serve_round(queue, clients)
                        queue.clear()
                        deadline = None
                else:
                    deadline = None
        finally:
            _close(listener)
            shutdown()

    def _accept(
        self, sock: socket.socket, header: dict[str, Any], payloads: list[bytearray], clients: set[socket.socket]
    ) -> tuple[socket.socket, int, DecisionRequest] | None:
        kind = header.get("kind")
        if kind == "hello":
            try:
                reply = self.handshake(header)
            except ValueError as error:
                send_frame(sock, {"kind": "error", "error": str(error)})
                clients.discard(sock)
                _close(sock)
                return None
            send_frame(sock, reply)
            return None
        if kind == "stats":
            # The supervisor snapshots this around its concurrent arm, so the
            # server's round shape is reported for the measurement, not for its
            # whole life (the baseline arm shares the process).
            try:
                self.handshake(header, require_weights=False)
            except ValueError as error:
                send_frame(sock, {"kind": "error", "error": str(error)})
                clients.discard(sock)
                _close(sock)
                return None
            send_frame(sock, {"kind": "stats", "stats": self.describe()})
            return None
        if kind == "weights":
            try:
                packed = PackedRecord.from_payload(header["packed"], payloads, device=self.device)
                state = unpack_state_dict(packed.record())
                applied = self.apply_weights(
                    state,
                    checkpoint_sha256=header.get("checkpoint_sha256"),
                    base_digest=header.get("base_weights_digest"),
                    digest=header.get("weights_digest"),
                )
            except (KeyError, TypeError, ValueError) as error:
                send_frame(sock, {"kind": "error", "id": header.get("id"), "error": f"{type(error).__name__}: {error}"})
                clients.discard(sock)
                _close(sock)
                return None
            send_frame(sock, {**applied, "id": header.get("id")})
            return None
        if kind != "decide":
            send_frame(sock, {"kind": "error", "error": f"unknown request kind {kind!r}"})
            clients.discard(sock)
            _close(sock)
            return None
        try:
            packed = PackedRecord.from_payload(header["packed"], payloads)
            payload = packed.record()
            if not isinstance(payload, dict):
                raise TypeError("a decision request must pack a mapping")
            mode = str(header["mode"])
            request = DecisionRequest(
                mode=mode,
                batch=payload["batch"],
                state=payload["state"],
                episode_start=payload["episode_start"],
                seed=header.get("seed"),
                arrived=time.perf_counter(),
            )
            return sock, int(header["id"]), request
        except (KeyError, TypeError, ValueError) as error:
            send_frame(sock, {"kind": "error", "id": header.get("id"), "error": f"{type(error).__name__}: {error}"})
            clients.discard(sock)
            _close(sock)
            return None

    def _serve_round(
        self, queue: deque[tuple[socket.socket, int, DecisionRequest]], clients: set[socket.socket]
    ) -> None:
        entries = list(queue)
        # A seeded request must be served alone (its generator is global), so a
        # seeded request that shared the wait window with other producers gets its
        # own forward instead of failing the round for everyone.
        seeded = [entry for entry in entries if entry[2].seed is not None]
        plain = [entry for entry in entries if entry[2].seed is None]
        groups = ([plain] if plain else []) + [[entry] for entry in seeded]
        for group in groups:
            self._run_and_reply(group, clients)

    def _run_and_reply(
        self,
        entries: list[tuple[socket.socket, int, DecisionRequest]],
        clients: set[socket.socket],
    ) -> None:
        round_started = time.perf_counter()
        requests = [request for _sock, _id, request in entries]
        try:
            results = self.run_round(requests)
        except Exception as error:  # noqa: BLE001 - reply, count, keep serving
            self.stats["failed_rounds"] += 1
            for sock, identity, _request in entries:
                _try_send(sock, {"kind": "error", "id": identity, "error": f"{type(error).__name__}: {error}"})
                clients.discard(sock)
                _close(sock)
            return
        for (sock, identity, request), result in zip(entries, results, strict=True):
            record = {"output": result.output} if result.output is not None else {"value": result.value}
            packed = PackedRecord.from_record(record)
            queue_wait_ms = (round_started - request.arrived) * 1000.0
            self.stats["queue_wait_ms_sum"] += queue_wait_ms
            self.stats["queue_wait_ms_max"] = max(self.stats["queue_wait_ms_max"], queue_wait_ms)
            sent = _try_send(
                sock,
                {
                    "kind": "decision",
                    "id": identity,
                    "mode": request.mode,
                    "rows": result.rows,
                    "packed": packed.descriptor(),
                    "bytes": tuple(len(payload) for payload in packed.payloads()),
                    "queue_wait_ms": queue_wait_ms,
                    "stats": {
                        "rounds": self.stats["rounds"],
                        "requests_per_round": len(entries),
                        "rows_per_round": self.stats["rows"] / max(self.stats["rounds"], 1),
                        "forward_ms_mean": self.stats["forward_ms_sum"] / max(self.stats["rounds"], 1),
                    },
                },
                packed.payloads(),
            )
            if not sent:
                clients.discard(sock)
                _close(sock)


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------


class DecisionClient:
    """The producer's end of the wire: one request, one decision, one state."""

    def __init__(
        self, address: str, *, timeout: float = 300.0, identity: dict[str, Any] | None = None, connect: bool = True
    ) -> None:
        self.address = address
        self.timeout = float(timeout)
        self.identity = dict(identity or {})
        self.server: dict[str, Any] = {}
        self.stats: dict[str, Any] = {
            "requests": 0,
            "pack_ms_sum": 0.0,
            "round_trip_ms_sum": 0.0,
            "unpack_ms_sum": 0.0,
            "queue_wait_ms_sum": 0.0,
            "round_trip_ms_max": 0.0,
            "weight_pushes": 0,
        }
        self._socket: socket.socket | None = None
        self._next_id = 0
        if connect:
            self.connect()

    # -- connection ----------------------------------------------------------

    def connect(self) -> dict[str, Any]:
        if self._socket is not None:
            return self.server
        family, target = _address_target(self.address)
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(target)
        except OSError:
            sock.close()
            raise
        self._socket = sock
        send_frame(sock, {"kind": "hello", **self.identity})
        reply, _payloads = recv_frame(sock)
        if reply.get("kind") != "hello":
            sock.close()
            self._socket = None
            raise RuntimeError(f"decision server refused the handshake: {reply.get('error')}")
        self.server = reply
        return reply

    def close(self) -> None:
        if self._socket is not None:
            _close(self._socket)
            self._socket = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- requests ------------------------------------------------------------

    def decide(
        self,
        batch: UniversalSemanticBatchV4,
        state: RecurrentPolicyStateV4,
        *,
        episode_start: Tensor | None = None,
        mode: str = "sample",
        seed: int | None = None,
    ) -> PolicyOutputV4:
        record, _header = self._request(batch, state, episode_start=episode_start, mode=mode, seed=seed)
        output = record["output"]
        if not isinstance(output, PolicyOutputV4):  # defensive: the schema carries the type
            raise TypeError("the decision server returned a non-output record")
        return output

    def value(
        self, batch: UniversalSemanticBatchV4, state: RecurrentPolicyStateV4, *, episode_start: Tensor | None = None
    ) -> Tensor:
        record, _header = self._request(batch, state, episode_start=episode_start, mode="value")
        value = record["value"]
        if not isinstance(value, Tensor):
            raise TypeError("the decision server returned no value")
        return value

    def request_stats(self) -> dict[str, Any]:
        requests = max(int(self.stats["requests"]), 1)
        return {
            "address": self.address,
            "requests": int(self.stats["requests"]),
            "pack_ms_mean": float(self.stats["pack_ms_sum"]) / requests,
            "round_trip_ms_mean": float(self.stats["round_trip_ms_sum"]) / requests,
            "round_trip_ms_max": float(self.stats["round_trip_ms_max"]),
            "unpack_ms_mean": float(self.stats["unpack_ms_sum"]) / requests,
            "queue_wait_ms_mean": float(self.stats["queue_wait_ms_sum"]) / requests,
            "weight_pushes": int(self.stats["weight_pushes"]),
            "server": dict(self.server),
        }

    def server_stats(self) -> dict[str, Any]:
        """The server's own counters, for a supervisor's before/after snapshot."""

        if self._socket is None:
            self.connect()
        assert self._socket is not None
        send_frame(self._socket, {"kind": "stats", **self.identity})
        reply, _payloads = recv_frame(self._socket)
        if reply.get("kind") != "stats" or not isinstance(reply.get("stats"), dict):
            raise RuntimeError(f"decision server refused a stats request: {reply.get('error')}")
        return dict(reply["stats"])

    # -- weight push ---------------------------------------------------------

    def push_weights(
        self,
        model: Any,
        *,
        checkpoint_sha256: str | None = None,
        digest: str | None = None,
    ) -> dict[str, Any]:
        """Give the server this model's weights and require it to hold exactly them.

        The push names the weights the client last sampled with, so a server
        that moved first refuses instead of silently jumping a version (a lost
        update is stale data, and this is the gate that stops it).  The reply's
        digest must equal the client's own, which is the behaviour identity the
        next segment's rollout will be labelled with.
        """

        if self._socket is None:
            self.connect()
        assert self._socket is not None
        local = str(digest) if digest is not None else weights_digest(model)
        pack_started = time.perf_counter()
        packed = pack_state_dict(model)
        payloads = packed.payloads()
        pack_ms = (time.perf_counter() - pack_started) * 1000.0
        identity = self._next_id
        self._next_id += 1
        header = {
            "kind": "weights",
            "id": identity,
            "checkpoint_sha256": checkpoint_sha256 or self.identity.get("checkpoint_sha256"),
            "base_weights_digest": self.server.get("weights_digest"),
            "weights_digest": local,
            "packed": packed.descriptor(),
            "bytes": tuple(len(payload) for payload in payloads),
            "rows": packed.batch_size,
        }
        started = time.perf_counter()
        send_frame(self._socket, header, payloads)
        reply, _payloads = recv_frame(self._socket)
        round_trip_ms = (time.perf_counter() - started) * 1000.0
        if reply.get("kind") != "weights":
            raise RuntimeError(f"decision server refused the weights push: {reply.get('error')}")
        acked = str(reply.get("weights_digest"))
        if acked != local:
            raise RuntimeError(f"decision server accepted {acked!r}, this producer's policy is {local!r}")
        self.server["weights_digest"] = acked
        self.identity["weights_digest"] = acked
        self.stats["weight_pushes"] += 1
        return {
            "weights_digest": acked,
            "checkpoint_sha256": reply.get("checkpoint_sha256"),
            "bytes": sum(int(size) for size in header["bytes"]),
            "pack_ms": pack_ms,
            "round_trip_ms": round_trip_ms,
            "apply_ms": reply.get("apply_ms"),
        }

    def ensure_weights(
        self,
        model: Any,
        *,
        checkpoint_sha256: str | None = None,
        digest: str | None = None,
    ) -> dict[str, Any] | None:
        """Push only if the server's weights are not this model's; ``None`` if in step."""

        local = str(digest) if digest is not None else weights_digest(model)
        if self._socket is not None and local == self.server.get("weights_digest"):
            return None
        return self.push_weights(model, checkpoint_sha256=checkpoint_sha256, digest=local)


    def _request(
        self,
        batch: UniversalSemanticBatchV4,
        state: RecurrentPolicyStateV4,
        *,
        episode_start: Tensor | None,
        mode: str,
        seed: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if mode not in MODES:
            raise ValueError(f"unknown decision mode {mode!r}")
        if self._socket is None:
            self.connect()
        assert self._socket is not None
        rows = int(batch.batch_size)
        if int(state.hidden.shape[0]) != rows:
            raise ValueError("the recurrent state does not carry one row per batch row")
        starts = (
            torch.zeros(rows, dtype=torch.bool)
            if episode_start is None
            else episode_start.detach().to("cpu", torch.bool)
        )
        packed = PackedRecord.from_record({"batch": batch, "state": state, "episode_start": starts})
        identity = self._next_id
        self._next_id += 1
        header = {
            "kind": "decide",
            "id": identity,
            "mode": mode,
            "rows": rows,
            "packed": packed.descriptor(),
            "bytes": tuple(len(payload) for payload in packed.payloads()),
            "seed": seed,
        }
        started = time.perf_counter()
        send_frame(self._socket, header, packed.payloads())
        pack_ms = (time.perf_counter() - started) * 1000.0
        reply, payloads = recv_frame(self._socket)
        round_trip_ms = (time.perf_counter() - started) * 1000.0
        if reply.get("kind") != "decision":
            raise RuntimeError(f"decision server error: {reply.get('error')}")
        if int(reply.get("id", -1)) != identity:
            raise RuntimeError("decision server replied out of order")
        unpack_started = time.perf_counter()
        restored = PackedRecord.from_payload(reply["packed"], payloads).record()
        unpack_ms = (time.perf_counter() - unpack_started) * 1000.0
        if not isinstance(restored, dict):
            raise TypeError("the decision reply is not a mapping")
        self.stats["requests"] += 1
        self.stats["pack_ms_sum"] += pack_ms
        self.stats["round_trip_ms_sum"] += round_trip_ms
        self.stats["round_trip_ms_max"] = max(self.stats["round_trip_ms_max"], round_trip_ms)
        self.stats["unpack_ms_sum"] += unpack_ms
        self.stats["queue_wait_ms_sum"] += float(reply.get("queue_wait_ms", 0.0))
        return restored, reply


# ---------------------------------------------------------------------------
# addressing and sockets
# ---------------------------------------------------------------------------


def _address_target(address: str) -> tuple[int, Any]:
    text = str(address)
    if text.startswith("unix:"):
        return socket.AF_UNIX, text[len("unix:") :]
    text = text.removeprefix("tcp://")
    host, separator, port = text.rpartition(":")
    if separator and port.isdigit() and host:
        return socket.AF_INET, (host, int(port))
    return socket.AF_UNIX, text


def _listen(address: str) -> tuple[socket.socket, Callable[[], None]]:
    family, target = _address_target(address)
    listener = socket.socket(family, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    shutdown = lambda: None
    if family == socket.AF_UNIX:
        path = str(target)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        listener.bind(path)
        listener.listen(64)

        def unlink() -> None:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

        shutdown = unlink
    else:
        host, port = target
        listener.bind((host, int(port)))
        listener.listen(64)
    return listener, shutdown


def _try_send(sock: socket.socket, header: dict[str, Any], payloads: Sequence[memoryview] = ()) -> bool:
    try:
        send_frame(sock, header, payloads)
    except OSError:
        return False
    return True


def _close(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass
