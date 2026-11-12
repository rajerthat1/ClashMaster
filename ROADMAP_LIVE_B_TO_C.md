# Track B → C: from "taps work" to a production live loop

Status: **planned**, 2026-11-12
Role: the second half of Track B. Turns a working reader + tap bridge into a scheduler that
lands plays on the intended tick, never taps a slot that is not there, and exports labelled data.
Feeds `ROADMAP_CRFORGE_ADAPTER.md` (training data) and `ROADMAP.md` Phase 9 (the live gate).

---

## 1. What B and C are

| | **B — bridge** | **C — live loop** |
| --- | --- | --- |
| Reader | emits 20 Hz frames | same, plus a *coherent* guarantee (tick + queue + hand agree) |
| Action | two ADB touches per play | two touches, issued inside a prediction window |
| Elixir | taps when the model asks | reserves cost against pending plays; refuses overspend |
| Slot state | assumed | tracks empty-until-deal, deal+100 ms |
| Confirmation | hand change seen | queue visibility windows; drop-and-log unconfirmed |
| Timing | whatever tick it lands | aims at a chosen issue tick (turn-aligned) |
| Failure | crash or stall | missed receipt, reconnect, abort, stale frame |
| Output | a bot that plays | a bot that plays *and* records trainable replays |

`ROADMAP_NULLS16_PROBE.md` covers B. This file covers the B → C delta.

**C is not polish.** It is the difference between plays that land and plays that do not:

> 1,370 Ice Golem + Skeletons clutches against a lone Hog: **100% on time**, **33–37% with one
> card a tick off**, **13–25% two ticks off**. (daybreak, `docs/GAME_INTEGRATION.md`, measured on
> official `160402012`)

For a cycle deck — Hog 2.6, X-Bow 3.0 — a bridge that lands plays on an arbitrary tick is not a
weaker version of C. It is a different, much weaker bot.

---

## 2. Measured timing contract

All values below are from `GAME_INTEGRATION.md`, measured on **official 160402012**, 2026-10-03,
~330 gestures. **They must be re-measured on Null's 16.635.1 before C ships** (Phase 1b).

| Fact | Value |
| --- | --- |
| Tick | 50 ms, 20/s (19.99 measured) |
| Play gesture | two touches: hand slot, then tile |
| Issue tick | tile touch-up **+0 … +4** ticks |
| Execute | issue **+21** ticks (1.05 s) |
| Deal into the slot | at execute, i.e. issue +21 |
| Slot touchable | **deal +100 ms**; before that a touch does nothing |
| Queue appearance | issue **+1 … +12**; touch **+4 … +17** |
| Nothing can be called lost before | touch +17 ticks |
| First touch accepted | tick **94**; first issue tick **101** |
| Server turns | 10 ticks / 500 ms; received tick **R** ends in 9 |
| Certain landing | issue **R-8**, landing **R+13** (ends in 2); touch **400–440 ms** after turn R |
| Received vs game tick | `battle+0x64` runs **6–19** ticks ahead of `battle+0x60` |
| Elixir at the touch | the **screen's** bar: earlier touches already paid |
| Placement | moved to the nearest legal tile, never refused; building onto building → 3 tiles |
| Two hand cards | ~5 ms gap is enough |
| Hold / gap lengths | irrelevant (holds 2–48 ms, gaps 0–33 ms) |

Consequence: the scheduler's job is to place the *tile touch-up* so the **issue** tick is the one
it wants. Everything else is bookkeeping.

---

## 3. Phase plan

### Phase 0 — Freeze B and write the baseline (2–3 days)

- [ ] Tag the working reader commit; record `libg` SHA-256 and reader SHA-256.
- [ ] Play 5 manual matches; log every frame, touch, and hand change.
- [ ] Measure the B baseline: taps that produced a play, taps that did nothing, taps late.
- [ ] Record the round-trip between the tap command and the first frame showing the change.

**Exit:** a baseline table C must beat. No subjective "feels better".

### Phase 1a — Coherent frames (1 week)

- [ ] Frame carries: `game_tick`, `received_tick`, per-side `elixir`, `hand[4]`, `next`,
      `queue`, `failure`, `queue_ok`, `coherent`.
- [ ] Reject a frame when tick, elixir and hand do not agree (torn read).
- [ ] Two consecutive coherent frames before the model may act.
- [ ] Handle the "slot empty but a card is due" state explicitly.

**Exit:** 10 minutes of live battle with zero torn-frame actions.

### Phase 1b — Re-measure the timing contract on Null's 16.635.1 (1 week)

The table in §2 is from the official client. Null's is a different `libg` and a different server.

- [ ] Re-run the touch-to-issue distribution; confirm +0…+4.
- [ ] Confirm execute = issue +21.
- [ ] Confirm deal +100 ms touchability.
- [ ] Measure the queue-visibility window.
- [ ] Measure the round trip and the turn phase (`received_tick` ending in 9).
- [ ] Write the result into `docs/live_timing_16.635.1.md`; the code reads constants from there.

**Exit:** a measured table. Any number that differs from official is authoritative for Null's.

### Phase 2 — Tick clock (1 week)

- [ ] Estimate the offset between device monotonic time and `game_tick`; track drift.
- [ ] Map "I want issue tick N" to a wall-clock send time.
- [ ] Expose the estimate and its confidence in the frame.
- [ ] Test: 50 scripted plays aimed at a fixed tick; record the spread.

**Exit:** issue-tick spread ≤ 1 tick for 90% of plays.

### Phase 3 — Budget (1–2 weeks)

Reimplement `LiveBudget` semantics; do **not** copy daybreak's file (no licence — see §5).

- [ ] Model a reservation as *unpaid*, not a debit on a balance that already includes execution.
- [ ] `payment_tick(elixir, tick, pending, cost)` → intended issue tick or refusal.
- [ ] Walk payment times with regen counted once, capped at 10, same-tick events charged together.
- [ ] At most **two own cards pending**, plus one ability.
- [ ] Reconcile against a coherent frame; **drop and log** unconfirmed taps, never stall a battle.
- [ ] Ability with unknown cost is budgeted at 0 (the press is still allowed).

**Exit:** 200 decisions with zero overspend and zero taps refused locally.

### Phase 4 — Scheduler (2 weeks)

- [ ] Turn alignment: choose the send time that yields issue R-8 / landing R+13 when it matters.
- [ ] Distinguish plays where a tick matters (clutch cycles, spell timing) from plays where it does not.
- [ ] Slot state machine: never touch before `deal + 100 ms`; never touch a slot with no card.
- [ ] First-touch lockout before tick 94.
- [ ] Two-card overlap policy: when to hold the second play for cycle.
- [ ] Ability press path (champion/hero), including cooldown from the frame.
- [ ] Retry rule: a refused or dropped play is re-decided, not blind-resent.

**Exit:** the clutch benchmark reproduces on Null's (Phase 8).

### Phase 5 — Confirmation and recovery (1–2 weeks)

- [ ] Treat a tap as pending until queue-visible or hand-changed.
- [ ] No play is "lost" before touch +17 ticks.
- [ ] On a missed receipt: log, reconcile, continue. Never abort a battle for one tap.
- [ ] Telemetry pause vs battle end — do not exit on a pause.
- [ ] Reconnect into the same battle; forbid resending an already submitted action.
- [ ] Detect a new battle and reset state cleanly.

**Exit:** one induced disconnect recovered mid-battle; no duplicate plays.

### Phase 6 — Capture (1 week)

- [ ] Write one file per battle: frames, touches, decisions, model inputs, result.
- [ ] Export to the schema `crforge_env.label_replays` accepts (see `ROADMAP_CRFORGE_ADAPTER.md` §5).
- [ ] `--watch` mode: record hand-played battles with no model output.
- [ ] Deduplicate, and stamp deck / side / result / data version.

**Exit (M3):** 3 battles round-trip from live capture into labelled crforge rows.

### Phase 7 — Autonomy (1 week)

- [ ] `--queue`: press Battle on the ladder.
- [ ] `--games N`, `--losses N`, `--forever`.
- [ ] Stop conditions: N games, N losses, time, stall, error budget.
- [ ] Per-battle log + a metrics file (`win`, `crowns`, `illegal`, `late`, `unconfirmed`).
- [ ] Optional win-probability display from the value head (rough, per daybreak's own caveat).

**Exit:** 20 unattended games with no manual intervention.

### Phase 8 — Validate C (1 week)

- [ ] Re-run the clutch benchmark: Ice Golem + Skeletons vs a lone Hog, 100+ reps.
- [ ] Report: on-time %, one-tick-off %, two-tick-off %.
- [ ] 20 games: zero touches on a non-dealt slot.
- [ ] Zero overspends; zero duplicate submissions after reconnect.
- [ ] Observation → tap latency under 100 ms, p95.
- [ ] Publish the numbers next to the §2 table.

**Exit:** C is measurably better than B on the baseline from Phase 0.

---

## 4. Acceptance metrics

| Metric | Target |
| --- | --- |
| Issue-tick spread | ≤1 tick on 90% of plays |
| Clutch on-time rate | ≥90% (B will be well below) |
| Taps on a non-dealt slot | 0 |
| Overspends | 0 |
| Plays confirmed (queue or hand change) | ≥99% |
| Lost plays aborted on | 0 |
| Observation → tap | <100 ms, p95 |
| Reconnect | recovers within one battle, no duplicates |
| Capture → labelled rows | 3/3 battles round-trip |

---

## 5. Port map, and the licence problem

`daybreak-cr` has **no licence file** on GitHub, which means all rights reserved. Use it as a
**specification and a source of measurements**, not as code to copy.

| Daybreak file | Use | Action |
| --- | --- | --- |
| `docs/GAME_INTEGRATION.md` | timing rules | **Read; cite; re-measure.** Do not copy text into the repo |
| `docs/LIVE_PENDING_BUDGET.md` | reservation semantics | Read; write our own spec |
| `cr/live_budget.py` | `Pending`, `payment_tick`, `LiveBudget` | **Reimplement** from the semantics |
| `cr/observation.py` | `HOLD_TICKS = 22`, `DEPLOY_DELAY = 22`, `regen()` | Re-derive from our own measurements |
| `play.py` | loop shape, `--queue`, `--watch` | Reference the shape only |
| `cr/engine.py` | engine adapter | Not applicable (it drives `clapha`) |

Target layout in this repo:

```
native_runner/live/
  frame.py        coherent frame contract
  clock.py        game tick <-> wall clock
  budget.py       Pending, payment_tick, LiveBudget
  scheduler.py    turn alignment, slot state, lockouts
  executor.py     ADB two-touch, confirmation
  capture.py      battle files -> replay export
  loop.py         match loop, queue, stop conditions
```

---

## 6. Failure modes

| Failure | Symptom | Guard |
| --- | --- | --- |
| Touching a not-yet-dealt slot | play silently disappears | slot state machine; deal+100 ms gate |
| Budget counts a pending play twice | bot starves elixir | reservation is unpaid, not a debit |
| Budget counts it zero times | overspend, refused tap | charge at execute tick |
| Torn frame | bot acts on a mixed tick | `coherent` gate; two frames |
| Queue lag read as a lost play | duplicate plays | nothing lost before touch +17 |
| Turn phase assumed | plays land 1–2 ticks late | measure R live; expose confidence |
| Aborting on telemetry pause | battle abandoned | distinguish pause from end |
| Copying daybreak code | licence exposure | reimplement from measurements |

---

## 7. What C unlocks

- **Training data.** Phase 6 is the only source of 16.635 replays for
  `ROADMAP_CRFORGE_ADAPTER.md`. Without C, the engine track has no human prior.
- **The live gate.** `ROADMAP.md` Phase 9 needs 100+ unattended games with no illegal taps.
  That is a C deliverable, not a B one.
- **A fair comparison.** C's metrics are what tell you whether a crforge-trained policy actually
  transfers — otherwise every loss is ambiguous between policy and execution.

---

## 8. Schedule

| Phase | Weeks | Cumulative |
| --- | --- | --- |
| 0 baseline | 0.5 | 0.5 |
| 1a coherent frames | 1 | 1.5 |
| 1b re-measure on 16.635.1 | 1 | 2.5 |
| 2 tick clock | 1 | 3.5 |
| 3 budget | 1.5 | 5 |
| 4 scheduler | 2 | 7 |
| 5 confirmation/recovery | 1.5 | 8.5 |
| 6 capture | 1 | 9.5 |
| 7 autonomy | 1 | 10.5 |
| 8 validate | 1 | **11.5** |

**B → C ≈ 10–12 weeks at 10–15 h/week**, on top of B's 4–8. Phase 6 (capture) can be pulled
forward if the engine track needs data sooner — it only depends on Phase 1a.
