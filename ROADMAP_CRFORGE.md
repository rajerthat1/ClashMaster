# crforge — modular roadmap (fallback / CI oracle)

Status: **optional, not the critical path**, revised 2026-11-12
Role: deterministic Linux trainer fallback and CI/parity oracle.
Primary plan: [`ROADMAP.md`](ROADMAP.md) — native engine route (E).

**Do not start this work just because it is already built.** Activate it only when a trigger below fires.

---

## 0. What this is

crforge is a Java reimplementation of the 16.635.1 client / 16.635.8 data rules. It runs without Android, KVM, a game client, or a GPU, which makes it valuable as:

1. a **deterministic CI oracle** for the same `BattleEnvV1` contract;
2. a **fallback trainer** when the native 16.635 probe is blocked or too slow;
3. a **second implementation** for differential testing (lane flips, side swaps, entity identity).

It is **not** the primary trainer. The native engine route has no fidelity gap and already has a proven X-Bow self-play recipe; crforge's remaining cost is open-ended `observe-rich` fidelity.

---

## 1. Activation triggers

| Condition | Action |
| --- | --- |
| Native 16.635 probe blocked > 4 weeks | crforge becomes the **fallback trainer**; native stays the live oracle |
| Native throughput < 1 match/s aggregate | crforge becomes the **primary trainer**; native stays live/eval |
| Native throughput 1–5 matches/s | crforge handles **bulk PPO**; native handles live/eval |
| Native throughput ≥ 5 matches/s | crforge is **CI only**; do not extend `observe-rich` |
| CI needs a deterministic no-Android test | Use crforge regardless of the primary trainer |

The Phase 0 decision memo in `ROADMAP.md` is the authority. If it says native is the trainer, this document is maintenance-only.

---

## 2. What already exists (2026-11-12)

- crforge 16.635.3 / 16.635.8 tables and a `16.635.1` release pin.
- A full `BattleEnvV1.reset` + 2,387-step match: terminal tick 5,403, rewards `{0: +1, 1: -1}`, `illegal: 0`, `truncated: 0`.
- Bridge commands: `status`, `attest`, `configure`, `step`, `advance-native`, `deploy`, `inject`, `observe`, `observe-rich`, `observe-atomic`, `pause`, `resume`, `speed`, `render`, `touch`.
- `crforge_catalog.json`: 152/152 cards, 118 forms, `unmapped: []`.
- F0 mostly in place: numpy conversion for JPype arrays, pending/`last_run_refusal` projection, loud `_giving`, typed `CARD_PENDING`, pending-slot mask. Rejected actions dropped **9 → 1**; one remains.
- One 16.635 example replay labels end to end: 1,222 rows, 611 decisions, 22 labelled, 0 refusals.
- Raw `VectorBattle`: ~2.2k decisions/s at 1 env, 3,112 at 16, 6,838 at 64, 8,371 at 256 (short re-run).
- Bridge: 20–30 decisions/s with full `BattleEnvV1` observation assembly; ~13 ms/step observation cost.

**Known open item:** one remaining reject (owner 0, MiniPekka, `26000018`) from the pending/refusal projection. Close it before claiming a clean 20-game run.

---

## 3. Scope if activated

Only implement the path needed for X-Bow training. Do **not** build the full `observe-rich` envelope unless crforge becomes the primary trainer and native is dead.

### F0 — close legality (hours)

- [x] Surface `pending` and `last_run_refusal` in `player_facts`; feed pending hand slots into `action_mask`.
- [x] Map `CARD_PENDING` to a typed `action_rejected` in `BattleEnvV1`.
- [ ] Close the remaining MiniPekka reject; add a regression test for the exact event.
- [ ] Re-run 20 games: `illegal: 0`, `truncated: 0`, no unexplained rejects.
- [ ] Re-run `evaluate --games 2`; record rejected actions separately from illegal actions.

### F1 — resident adapter (3–5 days)

- [ ] New `native_runner/crforge_resident.py` (or `crforge_vector_env.py`) backed by `LiveBattle` lanes (compound actions) or `VectorBattle` lanes (numpy buffers).
- [ ] Present the `NativeClashEnv` surface: `create_match`, `observe`, `observe_atomic`, `observe_rich`, `step`, `queue_hand_action_at`, `queue_ability_action_at`, `attest`, `status`, `close_transport`.
- [ ] Implement the resident batch coordinator or the binary `multi-*` protocol; keep the TCP bridge for evaluation.
- [ ] Use `LadderConfig(decision_ticks=5, level=16, tower_level=16)` and `SideSetup` for tower troop/forms.
- [ ] Acceptance: same observations/actions as the bridge on 50+ steps; 2× env throughput.

### F2 — fast observation (1–2 weeks)

- [ ] Build `ObservationV1` from the numpy seat row; skip raw dicts and repeated validation.
- [ ] Use crforge's native `masks` instead of re-deriving placement masks.
- [ ] Cache towers/footprints once per tick; use `policy_features_only=True`.
- [ ] Target 100–200 decisions/s per process.

### F3 — batch integration (1 week)

- [ ] 8 lanes per engine; multiple engine processes; batched GPU inference.
- [ ] Reuse `ResidentBatchCoordinatorV1` and `capture_mode="compact"`.
- [ ] Target 300–1,000 decisions/s aggregate; log env-steps/s, tensorize ms, inference ms.

### Minimal observation for X-Bow

Enough for a deck specialist: tick/result, elixir, hand/next/cycle, towers, entities (id/side/card/form/position/HP), attack targets, and incoming spells/projectiles. Add other field groups only if an ablation shows they change X-Bow decisions.

---

## 4. CI role (always)

Even when native is the primary trainer, keep crforge as a pinned, deterministic test target:

- [ ] Golden trace: seed + actions → per-tick observation hash; diff on every bridge change.
- [ ] Differential parity: same deck/actions on crforge and native; compare entity counts, target choices, death ticks, tower HP, elixir.
- [ ] Contract tests: `configure`/`attest`/`observe` shape, catalog mapping, action legality.
- [ ] Nightly 20-game smoke: `illegal: 0`, `truncated: 0`, no hangs.
- [ ] Freeze tables and `crforge-data.lock`; record the lock commit in every artifact.

Attestation is not optional. A silently different engine must not produce a checkpoint you cannot reproduce.

---

## 5. Proven commands

```bash
# bridge
.venv/bin/python -m crforge_bridge --port 26789 --tables ~/.crforge/tables/16.635.8

# raw benchmark
PYTHONPATH=.resource/crforge/python .venv/bin/python -m crforge_env.bench \
  --install .resource/crforge/env/build/install/env \
  --tables ~/.crforge/tables/16.635.8 \
  --engine ladder --counts 1 16 64 256 --steps 200 --repeats 3

# end-to-end evaluation
.venv/bin/python -m native_runner.training.v4.evaluate \
  --checkpoint-a checkpoints/General/checkpoint-step-00000460.pt \
  --checkpoint-b checkpoints/IL/checkpoint-step-00029396.pt \
  --games 2 --output evaluations/crforge-f0
```

Tests: `.venv/bin/python -m pytest crforge_bridge/tests -q`

---

## 6. Non-goals

- Full `observe-rich` parity while native is primary.
- Replacing the native engine as the long-term trainer.
- Reimplementing `ObservationV1` in Java.
- Training on `IL_Replay`.
- Porting FirstLight-Zero or Proxv weights blindly; reuse the recipe, not the weights.
- A 4–6 month adapter project before the first live bot.

---

## 7. Maintenance rules

- Pin `crforge-data.lock` and the tables; fork if upstream moves.
- Do not let crforge "fix" state in the adapter. If crforge is wrong, fix crforge or report the field as `null`.
- Every new field group needs a unit test and a 20-game sanity run.
- If native becomes primary, freeze this document and touch it only for CI regressions.
