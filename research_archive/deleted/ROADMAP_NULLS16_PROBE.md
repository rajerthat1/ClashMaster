# Track B — live bot on Null's 16.635.1

Status: **secondary track, started in parallel**, revised 2026-11-12
Role: data source + reality check for the crforge track (`ROADMAP.md`)
Not a gate: nothing downstream blocks on this finishing first.

Goal: get **V4/Everlight playing real matches on Null's 16.635.1**, and capture those matches as
labelled data for training.

**Time at 10–15 h/week: 4–8 weeks**, assuming the 16.x structs are intact. If more than ~25
observation fields have no source, stop and take the kill criterion in `ROADMAP.md` §5.

---

## Why this track exists at all

The crforge track cannot supply its own supervision. `IL_Replay` predates every rule range
crforge models, so the IL prior has to be re-captured on 16.635. This track is the only thing
that produces:

1. **Training data** — real human and bot battles on the live rule set.
2. **Ground truth** — the only check on whether crforge is teaching the right game.

Treat it as a required dependency of training, not as an optional deployment.

---

## What already exists

| Asset | State | Use |
| --- | --- | --- |
| `RoyaleHarness` | Apache-2.0, reader + ADB executor, **pinned `libg` `110aa2b5…`** | Seed. Same `libg` as `user-provided/nulls-royale.apk` (15.535.13) |
| `daybreak-cr` | `.resource/daybreak-cr/` | Scheduler reference: `play.py`, `live_budget.py`, `GAME_INTEGRATION.md` |
| V4 checkpoints | `checkpoints/` | Frozen policy |
| Everlight | `upstream/yes-delay` | Real 20-tick delay; preferred for live |
| Local APK | 15.535.13, base content | Offline replays only — **no longer connects to Null's** |

RoyaleHarness is pinned to 15.535.13, so it will not play live. Its value is that it already
solves the *hard* half — observation adaptation, execution confirmation, coordinate mapping —
for the same model and the same `libg` lineage.

---

## 0. Freeze and acquire (week 1)

- [ ] Save the working 15.535.13 APK, update folder, probe, `build/cr-ai-offline.apk`.
- [ ] Record SHA-256 for the APK, `libg.so`, probe, `fingerprint.json`, `assets.scdb`.
- [ ] Get the unmodified **Null's 16.635.1** APK.
- [ ] Check package `nullsroyale.rel.free`, ABI `arm64-v8a`, version code.
- [ ] Extract `lib/arm64-v8a/libg.so`; record SHA-256 and build-id.
- [ ] Capture a clean boot `logcat`; confirm the `libg.so` path.
- [ ] Confirm JDK 17, NDK 27.3+, build-tools 37, Python 3.12, MuMu Android 12 + root.

**Exit:** `native_runner/versions/16.635.1.json` exists with APK + `libg` hashes.

---

## 1. Offset map — reader only (weeks 1–3)

Only what `ObservationV1` needs. This is **not** the old P0 probe list: no lifecycle hooks, no
JSON parse, no command queue, no native touch, no ruleset.

- [ ] Manager / root context.
- [ ] Battle object and tick counter.
- [ ] Roster: per-side player state.
- [ ] Elixir, hand, next card, cycle.
- [ ] Tower HP and result.
- [ ] Entity graph: units, buildings, towers — id, side, card, form, position, HP.
- [ ] Card id / form mapping for 16.635.
- [ ] Extract a 16-byte prologue for every hook you keep.
- [ ] Verify each offset against the binary before building.

**Exit:** a written offset table with a verified prologue per entry.

---

## 2. Reader build (weeks 2–4)

- [ ] Fork RoyaleHarness; add a version switch keyed on `libg` SHA-256.
- [ ] Move offsets into a generated version header.
- [ ] Port the reader hooks; keep the probe read-only.
- [ ] Build and install the reader; confirm it reports the expected `libg` hash.
- [ ] Emit 20 Hz frames: elixir, hand, cycle, entities, towers, result.
- [ ] Cross-check 50 frames against the screen (manual labels).

**Exit:** `ObservationV1` frames from the live client, with no write path into the game.

---

## 3. Executor (weeks 3–5)

- [ ] Reuse RoyaleHarness's ADB two-touch path (hand slot, then tile).
- [ ] Use the real delay: a card takes effect ~21 ticks (about 1 s) after the play.
- [ ] Reserve elixir for plays already in flight before allowing the next one.
- [ ] Confirm each play by a hand change, not by optimism; log taps that did nothing.
- [ ] Latency budget: observation -> tap under 100 ms.

**Exit:** three manual matches where every tap produced a play and nothing double-played.

---

## 4. Capture (weeks 4–6)

This is the deliverable the crforge track consumes.

- [ ] Define the capture format: reader frames + plays + result, one file per battle.
- [ ] Make it convertible to the schema `crforge_env.label_replays` accepts.
- [ ] Record 20 hand-played matches first; verify labelling works before scaling.
- [ ] Then run `--watch` on ladder games: 500+ battles with the decks you care about.
- [ ] Tag by deck, side, result, and rule/data version.

**Exit (M3):** 500+ labelled 16.635 games, convertible, reproducible.

---

## 5. Deploy (weeks 5–8)

- [ ] `play.py`-style loop: model picks, executor taps, confirmation returns.
- [ ] `--queue` for ladder, `--watch` for capture.
- [ ] Reconnect / abort handling tested.
- [ ] Run Everlight zero-shot with Hog 2.6, then General with X-Bow 3.0.
- [ ] Log every decision + input for later comparison with crforge.

**Exit:** a V4/Everlight bot playing live Null's matches unattended.

---

## 6. Validate (week 8)

- [ ] One full live battle completes.
- [ ] No illegal taps, no crashes.
- [ ] Hand, elixir, towers, result match the screen.
- [ ] Reconnect works after killing the game.
- [ ] Save logs, reader hashes, capture files.
- [ ] Publish the frame-parity report against crforge (feeds `ROADMAP.md` M2).

---

## Later — do not block the first bot

- [ ] Combat-event hooks.
- [ ] Full catalog diff for new cards, evos, heroes.
- [ ] Pattern scanning and a version registry.
- [ ] 20+ unattended games.

---

## Notes

- Null's has essentially no anti-tamper, unlike the official client. That is why this track is
  weeks and not months.
- Do not port the full 33-hook probe. A live bot needs reads and taps.
- The 15.535.13 client is dead for live play. Everything here is 16.635.1.
- If this track stalls, the crforge track still yields a trainer — but with no human data and no
  reality check. That is the outcome to avoid.
