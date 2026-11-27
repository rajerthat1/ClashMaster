# crforge → FirstLight adapter — engineering checklist

Companion to `ROADMAP.md` Phase 2–6. Goal: make crforge stand in for the native engine so
FirstLight's model, evaluator and trainer run unchanged.

Status: planned, 2026-11-12
Engine: crforge (`version=16.635.8`, `compatible=16.635.3 16.635.8`, client 16.635.1)
Seam: TCP line protocol on `127.0.0.1:26789` (`native_runner/cr_native_env.py`)

---

## 0. Why this shape

`NativeClashEnv` is a plain socket client. It sends one text command per line and reads one JSON
reply. `BattleEnvV1` then turns the reply's raw facts into `ObservationV1`, and the tensorizer
turns that into model input.

So the port is **not** "reimplement FirstLight in Java". It is:

1. a Java process that answers the commands, and
2. a mapping from crforge's objects to the same raw facts the real runner emits.

Everything above the raw facts — encoding, legality, decoding, replay, training — is reused.

Do **not** build `ObservationV1` in Java. Encoding bugs are the failure mode that produces a
policy that "plays badly for reasons no log explains".

---

## 1. Module

```
bridge/                      new gradle module
  src/main/java/org/crforge/bridge/
    RunnerServer.java        socket accept loop, one env per connection
    CommandParser.java       verb + args
    CommandRouter.java       dispatch
    EnvRegistry.java         env id -> LadderEnv
    ObservationWriter.java   LadderEnv -> raw fact JSON
    RichObservationWriter.java  the big one (Phase 5)
    AttestationWriter.java   version, content sha, lock commit
    SnapshotStore.java       create/restore/release
  build.gradle.kts           depends on :core, :env
```

Java 21, no new third-party deps. `bridge` must not depend on `desktop`.

Start it with a main that takes `--port 26789 --tables <dir>`, so FirstLight's default
`CR_CONTROL_PORT` connects with no config change.

---

## 2. Command surface

Complete list, taken from `cr_native_env.py`. Priority order is top to bottom.

| # | Command | Phase | Notes |
| --- | --- | --- | --- |
| 1 | `status` | 4 | name, version, ready |
| 2 | `attest` | 4 | version, content sha, lock commit |
| 3 | `configure {json}` | 4 | decks, levels, seed, deck seats, ticks |
| 4 | `observe` | 4 | basic facts |
| 5 | `step N` | 4 | N ticks |
| 6 | `advance-native N` | 4 | native-tick advance |
| 7 | `deploy <player> …` | 4 | card, slot, tile |
| 8 | `activate-ability <player> …` | 4 | champion/hero |
| 9 | `pause` / `resume` / `speed F` | 4 | no-ops acceptable at first |
| 10 | `observe-rich` | 5 | the bulk of the work |
| 11 | `observe-atomic` | 5 | atomic read of basic + rich |
| 12 | `snapshot-create` / `restore` / `release` | 5 | replay work |
| 13 | `replay-schedule-card` / `-ability` / `-status` / `-clear` | 6 | deterministic replay |
| 14 | `inject {json}` | 6 | fault/repro |
| 15 | `multi-*`, `env <id> close` | 6 | throughput only |

Unknown verbs must reply with an error JSON, never hang. `NativeClashEnv` treats a bad reply as
`RunnerError`; a hang is worse than a failure because it stalls the whole batch.

---

## 3. `configure` payload

Map to `LadderEnv` + `EnvConfig` + `SideSetup`.

| FirstLight | crforge |
| --- | --- |
| `seed` | battle seed |
| `ticks` / `decision` | `EnvConfig.decisionTicks` (default 5) |
| `players[i].deck` | `SideSetup` deck, deck slots |
| `players[i].level` | `SideSetup` card level |
| `players[i].tower_level` | `SideSetup` king + princess levels |
| `players[i].tower_troop` | `SideSetup` tower troop |
| `location` / `arena` | arena on the standard board |
| `ruleset` | anything not modelled is refused, never guessed |

Card identity is the hard part: FirstLight's vocabulary is 122 cards / 62 forms; crforge's is
the 16.635 table's own names and ids. Add a generated map:

```
native_runner/data/competitive/crforge_catalog.json
  firstlight_card_id -> crforge name        (and the reverse)
  firstlight_form_id -> crforge evolution/hero variant
  unmapped:  [...]                          (must be empty before Phase 6)
```

Generate it from both catalogues and check it into git. Tests fail on any unmapped id used by a
deck in `deck_pool.json`.

---

## 4. `observe` — minimal facts

Enough for `BattleEnvV1` to build a basic observation and for a match to end correctly.

```
tick, result, winner
side[0..1]:
  elixir (int), hand[4] (card id), next (card id), cycle
  towers: king + 2 princess (id, side, hp, max_hp, x, y, alive)
```

Acceptance: a match plays to a result, `rewards` are ±1/0, `dones` fires once.

---

## 5. `observe-rich` — field groups

Land these in order. Each gets unit tests plus a 20-game run with `illegal: 0`,
`truncated: 0` before moving on.

1. **Entities** — characters, buildings, towers, spawners.
   `id, side, card, form, level, x, y, hp, max_hp, alive, deploy_tick`.
2. **Attack targets** — current target id, retarget tick, `attack targets` list.
3. **Projectiles** — id, owner, target, progress, class.
4. **Area effects** — id, radius, remaining ticks, owner.
5. **Visibility** — per side: what that player can see, fog boundaries.
6. **Phase runtime** — evolution stage, hero/champion state, ability readiness, tower troop.
7. **Character state** — charge, ramp, jump, burrow, transform.
8. **Action movement** — in-flight plays, landed plays, per-side pending.
9. **Special movement** — dash, knockback, tunnels.
10. **Deck exposure** — forms available, cycle position per side.

Each group has a parser already: `phase_runtime.py`, `character_state_runtime.py`,
`action_movement_runtime.py`, `special_movement_runtime.py`, `visibility_runtime.py`,
`projectile_catalog.py`, `effect_catalog.py`. Read the parser first, then emit what it accepts.
That is the cheapest way to get the contract exactly right.

**Parity oracle.** The 15.535.13 probe still runs offline. Use it to validate *semantics*: same
deck, same plays, compare entity counts, target choices, and death ticks between the probe and a
crforge battle. Do not expect byte equality — the rule ranges differ. Expect agreement on
outcomes that a policy would depend on.

---

## 6. `attest`

`BattleEnvV1._verify_runner_attestation` refuses a manifested episode if the runner cannot attest.
crforge reports different facts, so:

- [ ] `attest` returns: crforge version tag, `version=16.635.8`, `content_sha` per data version,
      `crforge-data.lock` commit, `compatible` list.
- [ ] `attestation.py` gains a `crforge` branch that verifies against a checked-in
      `native_runner/versions/16.635.1.json` (hashes of the tables, not of `libg.so`).
- [ ] `ruleset.py` builds a manifest from the crforge tables instead of the pinned `15.535.86`
      competitive data.
- [ ] The old `libg` path stays for Track B and offline replays.

**Attestation is not optional.** It is what stops a silently different engine from producing a
checkpoint you cannot reproduce.

---

## 7. Actions

| FirstLight action | crforge |
| --- | --- |
| deploy (card, tile, side) | `PlayerCommands.playCard(side, slot, level, x, y, name)` |
| ability | `PlayerCommands.useAbility(side, objectId, name)` |
| wait | no action |

crforge already stamps plays and runs them 20 ticks later (`PLAY_DELAY_TICKS`), which matches
the real game's 1 s card delay. That is a direct win over the 15.535 path and removes the
`yes-delay` mismatch for live play.

Illegal actions must increment `I_ILLEGAL_*` and set `last_refusal`. A non-zero illegal count in
evaluation fails the run.

---

## 8. Determinism

- One battle per `env` id; reset is explicit.
- Same seed + same actions ⇒ same trace. Test it: replay a 3,600-tick battle twice, diff.
- Do not let the observation writer mutate battle state (read-only accessors only).
- Keep `decisionTicks` fixed across a run; changing it mid-run invalidates comparisons.

---

## 9. Acceptance tests

| Test | Pass |
| --- | --- |
| `configure` + `status` + `attest` | FirstLight reports the engine ready |
| 2-game `evaluate` | `illegal: 0`, `truncated: 0`, result recorded |
| deck pool mapping | no unmapped card id |
| determinism | identical trace on replay |
| basic observation | towers/elixir/hand match the battle after every step |
| rich parity | 50+ frames agree with the probe on entity counts, targets, deaths |
| cache builder | one demo dataset converts `--verify-roundtrip` |
| arena | General vs IL in the expected ballpark |

---

## 10. Failure modes to watch

- **Silent misalignment:** observation is well-formed but wrong (a lane flipped, a side swapped).
  Detect with the probe parity frames, not with win rates.
- **Hanging command:** always reply, always with a timeout.
- **Leaky abstraction:** the adapter starts "fixing" state instead of reporting it. If crforge
  is wrong, fix crforge.
- **Version drift:** crforge moves `version=` under you. Fork and pin.
- **Card mapping gaps:** a new 16.635 form is dropped silently. Fail loudly on unmapped ids.
