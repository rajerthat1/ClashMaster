# crforge bridge

A Python process that makes **crforge** stand in for FirstLight's native runner at
`127.0.0.1:26789`. It answers the same line-delimited JSON commands `NativeClashEnv`
sends, so `BattleEnvV1`, `ObservationV1`, the evaluator and the trainer stay
unchanged.

Design: optional fallback/CI path in [`ROADMAP_CRFORGE.md`](../ROADMAP_CRFORGE.md); the primary plan is [`ROADMAP.md`](../ROADMAP.md).

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
