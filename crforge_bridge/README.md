# crforge bridge

A Python process that makes **crforge** stand in for FirstLight's native runner at
`127.0.0.1:26789`. It answers the same line-delimited JSON commands `NativeClashEnv`
sends, so `BattleEnvV1`, `ObservationV1`, the evaluator and the trainer stay
unchanged.

Design: optional fallback/CI path in [`ROADMAP_CRFORGE.md`](../ROADMAP_CRFORGE.md); the primary plan is [`ROADMAP.md`](../ROADMAP.md).

> **Same contract, no wire:** `native_runner/crforge_resident.py` presents the
> identical `NativeClashEnv` surface over an in-process `Engine`, calling this
> package's `dispatch()` directly. Use it for bulk collection
> (`CrforgeResidentEnv`) and this server for a real network endpoint and CI
> parity. Stage C2 measured it: 60/60 decisions identical to the bridge, ~1.2-1.3x
> the environment-step throughput (the remaining per-decision cost is
> `BattleEnvV1`'s observation/mask build, which is stage C3).

## What it is, and is not

- It **is** a thin adapter: `configure` → `LiveBattle`, `inject`/`deploy` →
  `LiveBattle.play`/`useAbility`, `observe`/`observe-atomic` → raw facts decoded
  from crforge's `Schema`.
- It **is not** a second `ObservationV1`. Encoding stays in Python, above the
  wire, exactly as with the native probe.
- It currently emits the **minimal v3 rich envelope**: entities, towers, HP,
  targets, buffs, hand, elixir, crowns. Projectiles, area effects, fog, champion
  and evolution runtime are explicit `null`s until Phase 5 fills them.

## Install

```bash
# once, in the crforge checkout
cd .resource/crforge
./gradlew :env:installDist -Pcrforge.assetSource=cdn
./gradlew -q :tables:gameTables -Pcrforge.assetSource=cdn

# in this workspace
.venv/bin/pip install jpype1 gymnasium pettingzoo
```

Tables land in `~/.crforge/tables/16.635.8` (and `.3`). The bridge uses `.8` by
default; pass `--tables` for `.3`.

## Run

```bash
.venv/bin/python -m crforge_bridge \
  --port 26789 \
  --tables ~/.crforge/tables/16.635.8
```

Then, from another process:

```python
from native_runner.cr_native_env import NativeClashEnv

with NativeClashEnv() as env:  # 127.0.0.1:26789
    print(env.status())
    print(env.attest())
```

Or let `BattleEnvV1` detect the crforge endpoint: it builds the manifest from the
pinned `native_runner/versions/16.635.1.json` release, then verifies the live
`CrforgeAttestationV1` before the first mutation.

## Commands implemented

| Command | State |
| --- | --- |
| `status`, `attest` | working |
| `configure` | working; decks, slots, seed, tower troop, king level |
| `step N`, `advance-native N` | working; one decision per tick |
| `deploy`, `inject` (`ct=153` card, `ct=189` ability) | working |
| `observe`, `observe-rich`, `observe-atomic` | working, minimal rich |
| `pause`, `resume`, `speed`, `render`, `touch` | receipts, no-ops |
| `snapshot-*`, `restore`, `release-snapshot` | refused explicitly |
| `multi-*`, `env <id> close` | refused explicitly |

## Design notes

- **One tick per decision.** `EnvConfig.decisionTicks=1` so a seat's observation
  is current after every `step`; FirstLight owns the policy cadence. The native
  probe exposes live state after an arbitrary `step N`, so the bridge must too.
- **The real delay.** A play is given at the current tick through
  `LiveBattle.play`, so crforge's `PLAY_DELAY_TICKS=20` applies. This is the
  real game's card delay, not the `yes-delay` mismatch of the 15.535 path.
- **Battle ids, not seat ids.** A seat observation numbers entities per seat.
  The bridge matches each row to the battle's `WorldEntity` by
  `(side, row, x, y)` and emits the battle id, which is what an ability command
  names. Projectiles and area effects keep a stable synthetic id.
- **No lying.** Fields crforge cannot supply are `null`, with the provenance
  status saying so. Adding a field is a Phase 5 change with a test, not a guess.

## Vectorized observation facts (C3-B precondition)

`vector_observation.py` is not part of the wire server: it reads a
`VectorBattle`'s zero-copy buffers for all 256 lanes and fills a fixed-size
`VectorObservationBatch` (both seats' headers and cards, six towers, placement
mask views, derived `age_ms` / `velocity`). It is the input the batched V4 row
build will consume; the bridge's per-lane `decode_rows` / `player_facts` /
`entity_rows` are its equivalence oracle.

```bash
.venv/bin/python tools/crforge/vector_observation_spike.py \
  --count 256 --threads 1 8 --steps 200 --repeats 3 \
  --output runs/crforge-c3/vector-observation.json
```

Measured 2026-11-18: **0.0317 ms/lane**, 4.8x the per-lane decoders, equivalence
pass (`docs/native/16.635.1/FINDINGS.md` §3.46).

## Batched causal-group row build (C3-B, first section)

`vector_rows.py` consumes the facts batch and fills the `_groups` + children
tensors for every lane at once. `VectorEntityTables` compiles the card /
archetype / child-type / extent / runtime-form semantics once per tensorizer;
`VectorGroupBuilder.build` is a per-lane priority sort and fixed-array fill, not
a graph walk (the bridge envelope has no causal-group events, so every group is
a singleton). The per-lane tensorizer stays the oracle:
`reference_entities` + `oracle_groups` run the stock `_groups` on
`EntityStateV1` rows projected from the same facts.

```bash
.venv/bin/python tools/crforge/batched_groups.py \
  --count 256 --steps 40 --repeats 3 --timing-samples 8 --verify-lanes 32 \
  --output runs/crforge-c3/batched-groups.json
```

Measured 2026-11-19: **0.0539 ms/lane** vs the 0.2207 per-lane oracle (**4.09x**),
equivalence pass over 40 decisions (`docs/native/16.635.1/FINDINGS.md` §3.47).
Section only: cards, candidates, relations, towers, scalars and spatial planes
are still per-lane, and a facts-only `tensorize` must land before the C3 gate.

## Tests

```bash
.venv/bin/python -m pytest crforge_bridge/tests -q
```

The tests need `JPYPE`, the installed env distribution and the tables. They skip
with a clear message when any is missing. They cover catalog packing, the
pinned attestation, configure/observe shape, a short stepped episode, native
`dataGlobalId` identity, and TCP dispatch.

## Roadmap gap

The bridge is the **optional fallback/CI** seam, not the primary training path. If crforge is
activated for bulk PPO, move the same writer into a `multi-*` resident vector inside a JVM
process; the wire contract does not change. See [`ROADMAP_CRFORGE.md`](../ROADMAP_CRFORGE.md).
