# Session handoff — 2026-11-15 — Null's Royale 16.635.1

> **Read [`ROADMAP.md`](ROADMAP.md) first.** It is the single plan.
> [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) is the bulk-training plan of record.
> This file is the next-session starting point.

> **Upstream 2026-11-19 — read before a training run.**
> [`docs/upstream-2026-11-19.md`](docs/upstream-2026-11-19.md) collects the
> actionable items: reward written back on the play action (RoyaleFish
> `9179bc5`), the brute-force → drill recipe, the held-out-defence release gate,
> and the crforge 16.635.13 move (rules/data unchanged; **16.635.3 no longer
> playable after a crforge update**; old-client tag `8be539ea`).
> **`A-hog26-v738`, `A-hog26-v98` and `B-general-v49` were deleted upstream**;
> all five weights are backed up in `.resource/upstream-keep/royalefish/models/`
> with a SHA256 manifest and restore commands. The league runbook below still
> points at `.resource/RoyaleFish/models/A-hog26-v738.pt` — if that path is
> missing after a pull, restore it from the keep folder first.
>
> **Target is pinned:** Null's Royale stays on client **16.635.1** for the next
> few months (decision 2026-11-19). Do not start a 16.635.10/13 port or a replay
> re-capture; the live client range is out of scope.

## Bottom line (2026-11-19, session 54)

The two-micro `card_still_in_slot` refusal was a **tick collision, not a policy
defect**, and it is fixed on the native gate path.  A V4 decision can carry two
micro-actions with the same `execute_offset_ticks`; the engine runs one action
per seat per tick, so the second play stayed in its hand slot.  The crforge
producer already spaced collisions (`plan_window_ticks`); the native evaluator
did not.

1. **The fix.**  `native_runner/training/v4/action_window.py` adds
   `separate_micro_action_ticks` (shift the later micro-action to the next free
   tick; keep the sampled delay bin; count shifts and drops).  `evaluate.py`
   applies it on both seats and records `micro_action_tick_shifts` /
   `micro_actions_dropped` per game and in `summary.json`; a rejection row now
   carries up to three distinct `rejected_examples`, so a refusal names its
   seat, card and hand.  `serve_policy.py` applies the same rule before a
   client can queue the actions.
2. **Seed 101 is clean.**  `ck-step-325` vs `B-general-v275`,
   `xbow-vs-hog26-evo`, sampled: the C5 point recorded **1 unique refusal /
   24 windows**; the same seed after the fix is **0 rejected, 1 shift,
   0 dropped**, illegal 0, terminated
   (`runs/native-two-micro-check/two-micro-fix-seeds101-101/summary.json`).
   `FINDINGS.md` §3.97.
3. **Gate:** `test_training_v4_action_window.py` **9 passed** plus the evaluator
   and service wiring tests (`test_evaluate_and_serve.py` **5 passed**); the
   nine related v4 suites are **81 passed**; ruff (E,F) clean.

**Next session:** `ck-step-305` still needs its own **150 paired games**
(seeds 101–250, both seats, `n4-vs-seed` runbook above) before any promotion
claim — the fix must be in the evaluator that runs it.  A laptop game is ~25
minutes, so use the rented four-box shape; do not run it serially.  `325` stays
the recorded negative.  Still open from session 53: the capture lock/pre-capture
for the overlapped loop, the 1 illegal crforge producer action (still needs
per-lane attribution in the producer, not the evaluator), NVIDIA MPS, the
8-vCPU question, and filing the C2 report.

## Bottom line (2026-11-19, session 53)

The plan executed and the model path is **negative**: the A6000 overlapped loop
fails its throughput gate, and the 50-update league run is **not promotable** —
**42–108 (28.0 %, Wilson [21.4, 35.7])** against `B-general-v275`, 0 illegal,
10 unique `card_still_in_slot` refusals, both seats below 45 %.  The C4 question
is closed (training stays **serial + `reset`**), the league is wired and trained,
and C6 has a real 150-game block.

1. **Overlapped loop — FAIL, so no pipeline.**  At 64 lanes the wall is
   67.2 → 66.4 s but the collector loses to **0.898×**; at 128 lanes the wall is
   93.4 → 82.6 s (**1.13×**) but the collector loses to **0.868×**.  The gate
   requires ≥0.9× collection, so both fail.  The arm runs eager fp16 because
   **full-graph capture is invalidated while the update worker is in flight**
   (`cudaErrorStreamCaptureInvalidated` on the next new live width); a capture
   lock or a pre-captured shape set is the fix, not a training-mode change.
   `speedup_vs_serial_equivalent` (1.16/1.35) is the contended-clock artefact;
   the honest walls are above.  `FINDINGS.md` §3.92.
2. **First league run — 50 updates, one process.**  64 lanes × 50 segments × 64
   decisions = 409,600 lane-decisions; **50 updates / 400 optimizer steps**,
   50 distinct behaviour ids, **0 stale**, return 0.339 → 0.468, ACT 6.96 %,
   wall 1,503.6 s, median update 10.25 s.  Its strict counter saw **1 illegal
   lane-action**, so the producer training gate is false until that edge is
   attributed.  The learner-only PPO update needed a device fix: the league
   mask was built on CPU and the first learner-only update indexed CUDA rows
   with it (`crforge_bridge/rollout.py`,
   `native_runner/training/v4/distributed_ppo.py`, commit `f275539`).
   `FINDINGS.md` §3.94.
3. **C6 — the native block is negative.**  150 paired games, seeds 101–250,
   both seats, X-Bow vs Hog 2.6 evo config: **42–108**, win rate **0.280
   [0.214, 0.357]**, seats **22.7 % / 33.3 %**, illegal 0, truncated 0,
   **rejected 346 window events / 10 unique**, all `card_still_in_slot`, 10
   games.  Gate: **FAIL** on win rate, Wilson lower bound, seat floor and zero
   rejected.  The return trace is a value-fit diagnostic, not a score; the
   crforge league's 46-53 was the honest preview.  `FINDINGS.md` §3.96.
4. **C5 — four-game points, not a ranking.**  Steps 285/295/305/315/325 were
   **2/3/4/2/1** wins of 4 (all illegal 0); step 305 was the only 4–0, step 325
   was 1–3 and carried the refusal.  The 150-game block says the final
   checkpoint is worse, not that 305 is promotable: **gate 305 on its own 150
   games** before any claim (`runs/crforge-n4/ck-step-305.pt` is local).
   `FINDINGS.md` §3.95.
5. **C2 — the host is no longer the unknown.**  On a fresh affected host
   (Xeon 2.10 GHz, JDK 21.0.13.1+2) the engine still exits 139 with
   `segfault at c` and its exact history **1528 → 5296 (C1) → 2848**
   (`handler_table_offset 2760`, `unstable_if` 1); the phased driver reaches the
   recompile and compiles **2896**, 0/3 crashes.  An `--phased-n` sweep
   (48–256) never produces 2848 or a crash.  The gap is the driver's compilation
   shape, not the host; the engine run remains the report's reproduction.
   `FINDINGS.md` §3.93.
6. **The native gate now runs in parallel.**  `native_block_run.sh` takes
   `PORT` / `ENGINE_INDEX` / `DEVICE`; 15 ten-game chunks on **4 boxes × 4
   workers** finished 150 games in **~40 min at 77.5 s/game** of throughput
   (serial would be ~12 h).  The account quota is **4 instances**, so 10+
   snapshot clones were not creatable; the `n4-base` snapshot was deleted with
   the boxes at cleanup.

**Next session:** gate `ck-step-305` on 150 games (and `ck-step-325` only as the
already-recorded negative).  Fix the two-micro refusal before another gate: the
crforge producer shifts colliding plays with `plan_window_ticks`, the native
`PolicySessionV4` path does not, and the gate refuses the second play as
`card_still_in_slot`.  Do not promote on the return trace.  Still open: the
capture lock/pre-capture for the overlapped loop, the 1 illegal producer action,
NVIDIA MPS, the 8-vCPU question, and filing the C2 report.

## Session 53 plan (executed above)

The C2 reproducer is a side thread with its own one-command runbook above. The
model's next gate is **N4**, and a long run must not start until two things land.

1. **C4's last throughput gate: the A6000 overlapped loop.** Raw GPU scaling is
   measured (758 lane-decisions/s at 128 lanes; one process at 64–128 lanes, not
   2–4 × 8). The open half is the pipeline: on CPU `--overlap-update` was
   **1.03× wall, 0.65× collection, 5/6 updates stale** and failed. On the card the
   update is 5.5 s against ~39 s of collection, so it may pay (bounded ~27 %).
   Gate: `tools/crforge/overlap_compare.py` at 64/128 lanes (the producer defaults
   `--warmup-forward` on) must keep the collection rate and beat the serial wall;
   `multiproc.py` prices the server arm. If it fails, the training run is serial
   and `reset` stays the default.
2. **The opponent league, wired into `tools/crforge/self_play.py`.** The producer
   was pure self-play: one checkpoint, both seats, fixed X-Bow deck.
   `native_runner/training/v4/league.py` already has `PolicyLeagueV4` and PFSP
   weighting; the first half of the crforge wiring is landed below. N4 needs
   seat 1 to be a frozen league member (`B-general-v275`, `A-hog26-v738`, history
   snapshots) while the learner stays on X-Bow; only the learner's rows enter the
   PPO update. A run that does not do this trains an X-Bow mirror, not a
   specialist.
3. **First gated run (the fast league path is now landed).** 50 updates, 64
   lanes in one process, `--fast-forward --opponent-league ...`, reset cadence,
   `--checkpoint-every 10`; C5 `parity_curve.py` on 4-game blocks, then C6:
   **≥150 native paired games, both seats**, vs `B-general-v275` (and
   `A-hog26-v738` as a canary). Promote only at **≥55 % with the Wilson lower
   bound above 50 %**, no benchmark more than 3 points below the champion,
   `rejected 0`, and no behaviour regression (ACT rate, plays/game, time at 10
   elixir). Never promote on a crforge metric and never rank a run on the crforge
   return. The two-model decision server is a **scale-out** item (multi-process),
   not a blocker for this single-process run.

Evidence for the plan: `ROADMAP_CRFORGE.md` C4–C6, `ROADMAP.md` N4,
`docs/native/16.635.1/FINDINGS.md` §3.62–§3.67 and §3.87.

**Landed 2026-11-20 — the league's first half.** `crforge_bridge/league.py`
loads the native `v4-ppo-policy-league.v1` JSON, samples with PFSP and records
results; `tools/crforge/self_play.py` now takes `--opponent-checkpoint`,
`--opponent-league` and `--learner-seat`, plays the other seat with the frozen
model, and stores the opponent's transitions while masking them out of the PPO
loss (`CrforgeRolloutStore(train_rows=...)`, learner rows only). CPU smoke:
2 lanes × 2 segments with `B-general-v275` on seat 1 — **8 valid rows = 4
decisions × 2 learner rows**, 2 finite updates, 0 rejected.

**Also landed — the opponent deck and fixed-opponent fast-forward.**
`--opponent-deck-id` / `--opponent-deck-json` (with `--deck-pool`) set the
opponent seat's deck from the deck pool; the engine gets it at reset, the
opponent tensorizer is built for it, and a league whose entries share one deck
uses it automatically. Mixed-deck leagues are refused unless one deck is forced.
A **fixed** opponent can also use `--fast-forward` (a second `FastForwardV4` with
`rows = count`), and the league fast-forward path copies each new member's
weights **in place** into the persistent opponent model so the CUDA graphs are
reused instead of recaptured at every segment. Smoke: `A-hog26-v738` on the
`523b64…` Hog 2.6 deck (`cannon-ev1`, `skeletons-ev1`, `musketeer-hero`,
`hog-rider`, …) — **0 illegal, 1 ACT**; CPU fast-forward enabled (fp32); a
2-segment league rotated `B-general` → `A-hog` on that deck with the fast path.

**Local 8-update league smoke (2026-11-20).** 4 lanes × 8 segments × 8 decisions,
reset cadence, the same B-general/A-hog league on the Hog 2.6 deck: **8/8 updates
pass, gate pass**, 32 valid rows/segment (8 decisions × 4 learner rows),
ACT **27/495 (5.45 %)**, 4 cadence checkpoints (steps 277–283) in
`runs/crforge-league-smoke/`. The C5 point on `ck-step-277.pt` came back
**3–1 of 4 vs `B-general-v275`**, illegal 0, rejected 0 — a schedule proof, not a
ranking (4 games).

**Next run — A6000, one process.** Build the league once (both entries share
one deck), then run:

```bash
HOG=523b6428074d4689904add37912722f47182bc6a1679dd89c5d50a19292263d3
python tools/crforge/league.py create --anchor checkpoints/General/checkpoint-step-00000460.pt \
    --output runs/crforge-n4/league.json
python tools/crforge/league.py add runs/crforge-n4/league.json \
    .resource/RoyaleFish/models/B-general-v275.pt --deck-id "$HOG"
python tools/crforge/league.py add runs/crforge-n4/league.json \
    .resource/RoyaleFish/models/A-hog26-v738.pt --deck-id "$HOG"

python tools/crforge/self_play.py --count 64 --segments 50 --decisions-per-segment 64 \
    --opponent-league runs/crforge-n4/league.json --fast-forward --device cuda \
    --ppo-update --update-state-policy reset \
    --save-checkpoint runs/crforge-n4/ck.pt --checkpoint-every 10 \
    --output runs/crforge-n4/run.json
```

Then the C6 block (150 paired games, both seats). The committed gate config
puts the locked X-Bow at seat 0 and Hog 2.6 at seat 1; the mirror config is the
seed-only plumbing gate, not this one.

```bash
tools/native_analysis/native_block_run.sh n4-vs-seed \
    runs/crforge-n4/ck-step-325.pt \
    .resource/RoyaleFish/models/B-general-v275.pt \
    tools/native_analysis/match-configs/xbow-vs-hog26-evo.json \
    101 150 10 runs/native-n4-gate
.venv/bin/python tools/native_analysis/native_block.py --name n4-vs-seed \
    --output runs/native-n4-gate/block.json runs/native-n4-gate/n4-vs-seed-seeds*
.venv/bin/python tools/native_analysis/promotion_gate.py \
    --block runs/native-n4-gate/block.json \
    --output runs/native-n4-gate/gate.json
```

Read `win_rate_a.ci_low`, `by_a_owner` and the illegal/rejected totals from
`block.json`; do not promote on the crforge run. `promotion_gate.py` applies the
≥55 % point estimate, the Wilson lower bound above 0.5, a 0.45 seat floor, zero
illegal/rejected and a 2 % truncation cap, and exits **0 / 1 / 2** for
PASS / FAIL / INSUFFICIENT. `xbow-vs-hog26-evo.json` is the form-faithful
A-hog26 deck (`cannon-ev1`, `skeletons-ev1`, `musketeer-hero`); the older
`xbow-vs-hog26.json` keeps the base forms.

Gate: 36 targeted tests, 223 in the bridge suite, ruff clean. Still open: a
two-model decision-server arm, per-lane opponents, and per-segment decks (the
engine keeps a lane's deck across autoresets, so a league must share one deck).

## Bottom line (2026-11-19, session 52)

The C2 reproducer now replays the **engine's compile history**: a one-sided C2
body (1528 bytes on Temurin 21.0.13 — the engine's exact size), the engine's own
`unstable_if` trap at **bci 120**, an in-range null slot, and a recompile whose
body matches the engine's 2848-byte one trap for trap and branch for branch.  The
crash body's fast path has **no null test at all**, and no earlier driver ever
recompiled `of`.  Both local JDKs reach that shape 3/3, but keep the null test,
so they allocate where the affected host faults; the remaining gap is one
`--mode phased` run on an affected host.

1. **The engine's history is two C2 bodies, not one.**  From
   `runs/crforge-c4/s51-a6000/hotspot-pid5285.log.gz`: **1528** bytes at 24.989 s
   (count 8604, every guard one-sided), retired by `unstable_if
   action='reinterpret'` at **bci 120** (`if (of[n] == 0)`) at 33.993 s, C1
   **5296** at 34.002 s, then C2 **2848** at 65.488 s (count 15093) — the crash
   body.  The trap is the assignment branch, so bci 23 and bci 54 were still
   zero-taken at the recompile.
2. **The fault is a dropped null test, with the grow path compiled.**  In the
   2848 body's fast path, `of = byKind[kind]` is loaded at `0x…ed82` and
   `of.length` at `0x…ed87` with no test in between; bci 23 and 62 are traps,
   bci 120 is compiled, and the body has a handler table the 1528 body lacks —
   the `of == null` grow path is compiled while its test is gone.  No
   implicit-exception entry covers the load, so the crash call is an **in-range
   null slot** (`kind < byKind.length`, `byKind[kind] == null`) and faults at
   `0xc` (report §4.3).
3. **The driver replays it.**  `SeatIdsOfRepro phased` seeds `kinds` populated
   kinds plus a null tail, warms every assigned slot, lets the one-sided body
   install, clears and re-reads an assigned slot (the engine's bci-120 flip),
   fills one in-range null slot (bci 54 taken, bci 23 not), warms in 20 ms-spaced
   chunks so the compiler thread installs C1 and then C2, and only after that
   shows the recompiled body a second in-range null slot.  3/3 per JDK, 0
   crashes: **1528 → 2896/2904** (Temurin 21.0.13; the engine is 1528 → 2848) and
   **872 → 1880** (Temurin 25.0.5).  The recompiled body matches the engine's
   shape — traps at bci 23/62, branches at 54/120, handler table — and locally
   the null test is present; the affected host's body is that shape with the test
   gone.
4. **The tool now carries the bodies.**  `hotspot_repro.py --mode phased` (plus
   `--phased-n`, `--phased-settle-ms`, `--rounds`, `--kinds`) always adds
   `-XX:+UnlockDiagnosticVMOptions -XX:+LogCompilation` and records every
   compilation of `of` — size, count, install stamp — in each arm;
   `phased.c2_bodies` and `phased.reached_recompile` make "did the driver reach
   the defect" readable after the temp build is gone, and the verdict requires
   the second body in this mode (`verdict.c2_recompiled`), so a run that never
   installed it is **inconclusive (exit 2)**, not a host negative.
   `--log-compilation` turns the same record on for `quick`/`soak`.
5. **Gate:** `test_hotspot_repro.py` **14 passed** (5 new: the phased history is
   in the source, the reader takes `size` not the bytecode's `bytes`, phased
   args, both bodies, and a phased run without the second body is inconclusive);
   ruff (E,F) clean.  Evidence: `runs/crforge-c4/s52/hotspot-phased.json`.
6. **Still open:** one `--mode phased` run on an affected host (a crash there
   makes the report self-contained), filing at bugreport.java.com, NVIDIA MPS
   (an image with the control daemon), the 8-vCPU question and the A6000
   overlapped-loop gate.  The producer-padding question is **closed** (§3.91):
   a producer captures 7–13 graphs over 96 decisions (one per live width, not
   per decision), so padding to the server's ladder saves 4–7 one-time captures
   while adding masked compute to every decision — do not pad.

**Next run — one affected box, one command.**  Rent the session-51 class (6 vCPU,
1×A6000, Ubuntu 22.04.5, `/usr/lib/jvm/java-21-openjdk-amd64`) and run:

```bash
.venv/bin/python tools/crforge/hotspot_repro.py \
    --jdks /usr/lib/jvm/java-21-openjdk-amd64 \
    --mode phased --repeats 3 \
    --output runs/crforge-c4/hotspot-phased.json
```

Expected on an affected host: exit **0**, at least one `c2` arm crashed, controls
(`c1`, `-Xint`) clean, `bytecode_identical: true`, and `phased.c2_bodies` showing
the first body then the recompiled one (`[1528, 2848]` on that JDK).  A clean run
is exit **1** only with `reached_recompile: true`; exit **2** means the recompile
never installed — rerun, do not record a negative.  If it reproduces, capture
`dmesg`, whether an `hs_err` exists, and the arm's `stderr_tail`, then file at
bugreport.java.com from `HOTSPOT_C2_SEATIDS.md`.

See `FINDINGS.md` §3.91 (the padding decision), §3.90 (this reproducer), §3.89
(the sweep), §3.88 (the reproduction and the false negative), §3.87 (the parity
cadence) and §3.86 (the driver).

## Bottom line (2026-11-18, session 51)

The C2 miscompile is **reproduced on a second host**, and the report now has the
evidence it lacked: the fault is inside the **level-4 nmethod of
`SeatIds.of`, at offset `0x277`**, and the miscompiled body loads a null
`int[]`'s length (`mov 0xc(%r12,%r9,8),%ebp` → `segfault at c`).  The
**stand-alone reproducer is a false negative**: on the very host whose engine
crashes **3/3** it compiles `of` at level 4 and passes (2/2), because its own C2
body is **3968 bytes** where the engine's is **2848**.  Session 50's "the trigger
is the host CPU" is therefore **withdrawn**; the flag is not a per-host
workaround.

1. **A fresh Thunder A6000 reproduces the engine crash 3/3.**  Same class as
   session 49 (6 vCPU, 1×A6000, $0.35/h, Ubuntu 22.04.5, kernel
   `6.12.115-thunder-sandbox`, CPU `Intel Xeon @ 2.10 GHz`, microcode `0x1`,
   AVX2 and **no AVX-512**), but a **later JDK** — Ubuntu
   `21.0.13.1+2` rather than `21.0.13.0.3` — so the defect is not fixed in
   `.1`.  Session 49's shape (4 lanes × 2 seats × 3 × 24, CPU producer,
   `--ppo-update`, default flags) completes segment 0 and dies in segment 1
   with `segfault at c`, exit 139, no `hs_err`, 3/3.
2. **The fault is `of`'s own C2 nmethod, deterministically at `0x277`.**  Each
   run compiles `of` at level 4 twice — 1528 bytes at ~25 s, **2848 bytes at
   62–65 s** — and only the second faults: compile_id 3001 (stamp 65.488 s,
   count 15093) and 2922 (stamp 62.065 s, count 13864), both at offset `0x277`.
   With `hsdis` (`libhsdis0-fcml` linked as `$JAVA_HOME/lib/hsdis-amd64.so`),
   `-XX:CompileCommand=print` gives the instruction:
   `43 8b 6c cc 0c` = `mov 0xc(%r12,%r9,8),%ebp`, a null `int[]` length at
   address `0xc`.  This supersedes session 49's "the copy is inlined in the
   caller" reading; the `exclude` arm needs re-running before the report leans on
   it.
3. **The probe is clean on that host — a recorded false negative.**  `soak 70 s
   --new-every 20000 --repeats 2`: 2/2 C2 arms at level 4, bytecode identical,
   0 crashes, controls clean.  Its level-4 body is 3968 bytes, not the engine's
   2848: the driver's warm-then-new-kind loop reaches a different compilation.
   So `hotspot_repro.py` exit 1 means "this probe did not crash", never "this
   host is clean", and session 50's second box was almost certainly affected.
4. **C1 still clears it on this host.**  `--jvm-arg=-XX:TieredStopAtLevel=1`:
   3/3 segments, gate pass, 563 eligible lane-decisions, 28 ACT (4.97 %),
   0 illegal, 0 rejected.  The flag stays on every CPU producer that can meet a
   new entity kind after `of` is recompiled at level 4 — not on "an affected
   host class".
5. **The report is filable except for a self-contained reproducer.**  The text
   (`docs/native/16.635.1/HOTSPOT_C2_SEATIDS.md`) now carries the second
   observation, the nmethod offset, the assembly, and the driver's negative
   stated explicitly.  Filing option: use §3.1 (the engine run) as the
   reproduction; the open work is a driver that reaches the 2848-byte
   compilation.
6. **The warmup-corrected sweep is done on two cards.**  3 segments × 32
   decisions, `--warmup-forward`, fp16 + full graph, one process: steady
   lane-decisions/s on the A6000 are **203 / 372 / 428 / 535 / 758** at
   **8 / 16 / 32 / 64 / 128** lanes (`T` 0.079 → 0.338 s/decision), and on an
   L40 **361 / 508 / 651 / 841 / 988** (`T` 0.044 → 0.259).  The 8→128 ratio is
   **3.7×** on the A6000 (2.7× on the L40): the old "0.34× at 8 lanes" was
   one-time cost, and the measured 128-lane steady state is **758 ld/s** against
   session 33's 490 prediction (different packet).  First segment is still slow
   (8 lanes 91.8 vs 203.1; 128 lanes 619.5 vs 758.4).
7. **MPS is still untested, now with a reason.**  The `base` container has no
   `nvidia-cuda-mps-control` and the toolkit does not ship it; it needs the
   driver's control daemon.  Blocked on the image, not on the technique.
8. **Still open:** a self-contained reproducer for the C2 report, filing it,
   NVIDIA MPS (image with the control daemon), the 8-vCPU question, the A6000
   overlapped-loop gate, and whether to pad a producer's width to the server's
   ladder.

See `FINDINGS.md` §3.89 (the sweep), §3.88 (the reproduction and the false
negative), §3.87 (the parity cadence), §3.86 (the reproducer) and §3.85 (the JIT
defect and the flag).

## Bottom line (2026-11-18, session 50)

The C2 miscompile has a **reproducer**, a **host check** and **one recorded
negative**.  `tools/crforge/hotspot/SeatIdsOfRepro.java` is the engine's
`SeatIds.of` verbatim (its `javap` instruction stream is identical to the built
jar's, 117 slots) and `tools/crforge/hotspot_repro.py` runs it in three arms
(default flags, C1, `-Xint`) and only counts a pass when
`-XX:+PrintCompilation` shows the **194-byte level-4** nmethod.  On this laptop
both `.resource` JDKs (21.0.13, 25.0.5) compile it at level 4 and **nothing
crashes** — quick 6/6 and a 70 s soak — and a fresh Thunder `base` with **the
affected host's exact JDK** (21.0.13 Ubuntu) is clean too, even with 1,000 new
kinds: the trigger has narrowed to the **host CPU**, which session 49 did not
record.  The flag stays: the card is the affected host class.

1. **The reproducer is checked, not eyeballed.**  `of` is the crforge body
   verbatim (`SeatIds.java` at `c4721bd9`, `IDS_PER_KIND` = 1,000,000); the tool
   diffs its `javap -c` against `.resource/.../env-1.0-SNAPSHOT.jar` and records
   `identical: true` (117 instruction slots, same opcodes, constant-pool indices
   folded), and `PrintCompilation` reports the same `SeatIdsOfRepro::of
   (194 bytes)` level-4 nmethod the box's `LogCompilation` named.
2. **The host check has a shape for the box.**  `quick` warms kinds then reads a
   new one; `soak` draws random ids for `--seconds` and adds a kind every
   `--new-every` calls (the box crashed 65.1 s in, so the default is 70 s).  Exit
   0 = reproduced, 1 = negative, 2 = inconclusive; an arm that never compiled
   `of` at level 4, a timeout, a crashing control or a failed bytecode check is
   `inconclusive`, never a pass.
3. **This laptop is a recorded negative.**  i5-1235U, kernel `7.0.0-43`: quick
   mode **6/6** C2 arms compiled level 4, **0 crashes**, controls clean; `soak`
   70 s per arm clean as well.  Same JDK majors and same bytecode as the box, so
   the difference is host or build; the affected host is Temurin 25.0.5 **and**
   Ubuntu 21.0.13, this one is Temurin 21.0.13 **and** 25.0.5.
4. **A fresh Thunder `base` is a second negative, and the CPU is now the
   suspect.**  The same class of box was rented again (6 vCPU, kernel
   `6.12.115-thunder-sandbox`, Ubuntu 22.04.5) and it runs **the affected host's
   exact JDK** (`21.0.13.0.3+1-1-22.04.1-Ubuntu`): quick 6/6 clean, soak 70 s
   clean, and a 70 s soak with **1,000 new kinds** (a fresh null slot every
   20,000 calls) clean both inlined and with `-XX:CompileCommand=dontinline`,
   plus a C2-only arm.  Its CPU is an **Intel Xeon @ 2.10 GHz, AVX2, no
   AVX-512**.  That fixes the JDK build, the kernel, the tier, the bytecode and
   the trigger shape, so the host CPU is what is left — and session 49 did not
   record `lscpu`.  The box was deleted after the run (evidence in
   `runs/crforge-c4/s50-thunder/`).
5. **One driver defect, found by the box.**  `kind * 1_000_000` is an `int`, so
   2,147 kinds overflowed and the soak died with
   `ArrayIndexOutOfBoundsException: Index -2146` before reaching the case under
   test.  The driver caps at 1,000 kinds; the throw was the driver's, not the
   JVM's.
6. **Gate:** `test_hotspot_repro.py` **8 passed**, bridge suite **191**;
   ruff clean (E,F) on the tool and the test.  Evidence:
   `runs/crforge-c4/hotspot-seatids.json`, `hotspot-seatids-soak.json`,
   `s50-thunder/`.
7. **Next on this thread:** find a host that reproduces it (`hotspot_repro.py
   --mode soak --seconds 70 --new-every 20000`, exit 0 = found), **record
   `lscpu`**, then capture `-XX:+LogCompilation` + an `hsdis` disassembly and file
   at bugreport.java.com from `docs/native/16.635.1/HOTSPOT_C2_SEATIDS.md`.
8. **Still open:** a reproducing host for the C2 report, NVIDIA MPS, the 8-vCPU
   question, the A6000 overlapped-loop gate, and whether to pad a producer's
   width to the server's ladder.

See `FINDINGS.md` §3.87 (the parity cadence), §3.86 (the C2 reproducer), §3.85
(the JIT defect and the flag), §3.84 (the card), §3.83 (the shape bug), §3.82
(the push) and §3.81 (the server on CPU).

**Also this session — the native parity cadence.**  C5's "50–150 matches every N
updates" now has both halves: `self_play.py --checkpoint-every N` writes
`ck-step-<n>.pt` mid-run (3.0–3.1 s per 193 MiB save, outside the segment clock),
and `tools/native_analysis/parity_curve.py` gates those checkpoints on the same
seeds against one reference and prices the cadence.  Measured: a 4-game point is
**315 s = 27.9 updates**, so a 10 % budget allows a gate **every 279 updates**;
the 50-game gate C5 asks for is **~350 updates**, i.e. every 3,500 updates at
10 % or every 1,400 at 25 %.  The two smoke points (4 games, seeds 101–104) were
2–2 and 1–3 with overlapping intervals — a schedule proof, not a ranking.  Gate:
`test_parity_cadence.py` 6 passed, bridge suite **191**.

## Bottom line (2026-11-18, session 49)

The card's producer-after-update segfault is **not crforge's loop and not the
weight push**.  It is the rented box's HotSpot C2 compiler miscompiling
`org.crforge.env.SeatIds.of` on the producer's CPU path (the shape a
decision-server arm runs); the miscompiled body is entered when a new entity
kind allocates its `int[]`, and `-XX:TieredStopAtLevel=1` (C1 only) clears it.
That option is now carried by `--jvm-arg` on the producer and every training
harness.  With it the card's training gate closes and the session-38 128-lane
CUDA trainer re-runs under torch 2.12.1.

1. **The crash is the engine's JIT, on the CPU half.**  Session 48's "card"
   control was a `--device cpu` producer (a decision-server arm keeps the
   producer on the CPU half; its 25 s update and 423 ms forward are CPU numbers).
   Re-run on a fresh Thunder `base` (torch 2.12.1+cu130, OpenJDK 21.0.13): the
   same SIGSEGV, `segfault at c`, after segment 0; the rollout is bit-identical
   to session 48's.  Every `--device cuda` arm (eager, fp16, full graph,
   overlapped) completes.
2. **The faulting code is C2's `SeatIds.of`.**  `-XX:+LogCompilation` maps the
   fault PC to the level-4 nmethod of `org.crforge.env.SeatIds.of(II)I`; the
   bytecode compares `n` with `of.length`, the compiled body compares it with
   the previous array's length and loads `of.length` through a null `of`.  The
   nmethod is installed 65.1 s into the run (count 14,406), i.e. once the engine
   has been hot long enough, and the crash follows the next new entity kind.
   Guards: `-Xint` passes, C1 passes, default C2 crashes 6/6; JDK 21.0.13 and
   Temurin 25.0.5 both crash; `-XX:-UseSuperWord`, `-XX:UseAVX=2` and
   `-XX:CompileCommand=exclude,...,SeatIds::of` do not help.
3. **The workaround is one flag, priced.**  `--jvm-arg=-XX:TieredStopAtLevel=1`
   (the `=` form: the value starts with `-`); `multiproc.py`,
   `overlap_compare.py`, `cadence_compare.py` and `learning_horizon.py` forward
   it.  At 128 lanes the engine step is **51.3 → 106.9 ms** per decision
   (+55.6 ms, +108 %); at the server arm's 8 rows it is **4.4–8.9 ms against
   5.6** (noise).  The update is not the cause: the reproduction is a run long
   enough for the method to become hot and a board that then shows a new
   entity kind; the `--device cuda` arms sampled a different board and did not
   meet that case (the 128-lane trainer passes 3 segments under C2).  So only a
   producer that meets the case on its board pays the flag.
4. **The card's training gate passes.**  4 lanes × 2 seats × 3 segments × 24
   decisions through the CUDA server with `--verify-decision-server`: 576
   lane-decisions, 24 ACT (4.4 %), **0 illegal, 0 rejected, gate pass**.  The
   push chain is exact: server `04a50b…` labels segment 0 (`fb4acf…`), push
   `c198425…` labels segment 1 (`b878470…`), push `4a043a…` labels segment 2
   (`2b167a…`).  Pushes are 74,153,397 bytes; pack 44–51 ms, round trip
   1.17 s, server apply 519–545 ms.
5. **The session-38 trainer runs under this torch build.**  128 lanes × 2 seats
   × 3 × 32, full CUDA graph, lane minibatch 16: 24,358 lane-decisions, 710 ACT
   (2.91 %), 308–344 ld/s, 9.2–10.5 s per update, **0 illegal, 0 rejected, gate
   pass**.
6. **Rental:** one Thunder `base` (6 vCPU, 1×A6000 48 GB, $0.35/h), torch
   2.12.1+cu130, OpenJDK 21.0.13 (Temurin 25.0.5 for the A/B).  This is still
   not a promotion: the native gate is unchanged and negative.
7. **Still open:** NVIDIA MPS, the 8-vCPU question, the A6000 overlapped-loop
   gate, the native parity cadence, whether to pad a producer's width to the
   server's ladder, and a HotSpot bug report for the C2 miscompile.

See `FINDINGS.md` §3.85 (the JIT defect and the flag), §3.84 (the card), §3.83
(the shape bug), §3.82 (the push), §3.81 (the server on CPU) and §3.74 (native
gate).

## Bottom line (2026-11-18, session 48) — superseded by session 49

The server's card half is measured: it **wins where the design said it would**
(1.38× the multiproc floor at 32 lanes/producer; 1.16× at six producers on six
vCPU) and loses where a producer's forward is launch-bound (0.68× at four
lanes).  Training behind the server works: the weight push is landed and gated.

1. **The weight push closes the server's training loop.**  A producer pushes its
   updated state dict at every segment boundary; the server copies it into its
   own parameters **in place** (captured graphs, staging and pinned mirrors keep
   their objects) and re-hashes the result.  Three refusals stand in front of the
   copy: checkpoint sha256, the client's `base_weights_digest` (the weights it
   last sampled with — a lost push is stale data, not a version to paper over),
   and the incoming bytes' digest, computed the way `weights_digest` walks a
   model (`state_digest`).  `--ppo-update`/`--overlap-update` are no longer
   refused with `--decision-server`.  Measured: 74,153,397 bytes, client pack
   **51.6 ms**, round trip **193.2 ms**, server copy + re-hash **57.1 ms**, once
   per segment; serial and overlapped probes both pass their gates on this host
   and the acked digest labels the segment's rollout.  **On the card the probe
   does not finish a segment — and the control clears the push: the same probe
   with no server at all (`--ppo-update`, in-process) segfaults at the same point
   after segment 0's update**, so that box's producer-after-update path is a
   pre-existing defect, not the push (no JVM fatal-error log; `dmesg` shows
   `segfault at c`).  The card half of the training gate is therefore open, and
   the session-38 1-process CUDA trainer should be re-run under this torch build
   before any training claim on a rented box.
2. **The card's gate passes at six producers and at 32 lanes, not at four.**  At
   session 47's shape (4 lanes = 8 rows/producer): 6 producers coalesce
   **3.14 requests/round** and the forward falls to **36.8 ms per request**
   against 49.7 unbatched (−26 %) — the gate is passed — and the aggregate
   **272.4 ld/s beats the per-process floor's 235.7 (1.16×)**.  At four,
   `1.91` requests/round buy **no per-request gain** (49.9 against 49.7): an
   8-row forward is launch-bound, and the aggregate is 0.68× the floor.
3. **The row axis is the real crossover.**  Four producers at 16 lanes: the
   server's per-request forward is already 102.0 ms against a ~190 ms
   single-request round trip, yet the aggregate is 0.62× the floor (232.3 against
   372.8) because a 175.6 ms round serializes the card while four 32-row forwards
   would overlap it.  At **32 lanes** (64 rows/producer) the CPU halves (~215 ms)
   hide behind the 124 ms round and the server is **736.7 against 534.4
   (1.38×)**, 1.5 requests/round, 83.9 ms per request.
4. **The card found a real shape bug, latent since session 43.**  `FastForwardV4`
   keyed its staging and its graphs by the row count, so the first batch fixed
   the group/child width; a board that then widened (troops spawn) raised
   `staging axis 8 exceeds capacity 1` — in the producer's `--fast-forward` path
   *and* in the server (whose `_forwards` was row-keyed, so its width buckets
   never reached the graph vocabulary).  The key is now the full shape
   (`(rows, groups, children)` for the producer, the whole bucket tuple for the
   server); a run captures one graph per shape it visits (10 at 4 lanes, ladder-
   bounded on the server).  Regression test included.
5. **`--no-validate` is part of the card's shape, one session later.**  The first
   arm left per-decision contract validation on and read a **2.3 s** forward at
   four producers — session 38's device-sync defect in a new place.  The harness
   now also forwards `--fast-forward`/precision/graph flags to the in-process
   arm, so both arms differ in batching, not in precision.
6. **Rental:** one Thunder `base` (6 vCPU, 1×A6000 48 GB, $0.35/h), torch
   2.12.1+cu130.  No native work: the promotion gate is unchanged and negative.
7. **Still open:** the rented box's producer-after-update segfault (open it
   first — it blocks the card's training gate), NVIDIA MPS, the 8-vCPU question
   (dominated until `g` falls), the A6000 overlapped-loop gate, the native parity
   cadence, and whether to pad the producer's width to the server's ladder to cut
   its capture count.

See `FINDINGS.md` §3.84 (the card), §3.83 (the shape bug), §3.82 (the push),
§3.81 (the server on CPU) and §3.74 (native gate).

## Decisions — what changed

### 2026-11-19 (session 52) — the reproducer replays the engine's compile history, and the fault body drops the null test

Session 51 filed the C2 defect under "reproduces in the engine, driver is a false
negative" and named the missing piece: a driver that reaches the 2848-byte
compilation.  This session reads the engine's compile history, maps the fault
body's fast path, and makes the driver replay that history.

- **The engine's history.**  `of` is compiled by C2 twice: **1528** bytes at
  24.989 s (count 8604) and **2848** bytes at 65.488 s (count 15093), with an
  `unstable_if action='reinterpret'` at **bci 120** (`if (of[n] == 0)`) at
  33.993 s and a C1 level-3 body (5296) in between.  The trap is the assignment
  branch — not a grow branch, not the null check — so bci 23 and bci 54 were
  still zero-taken at the second compile.  The first body is the one where every
  guard is one-sided; a driver that only makes `of` hot compiles that shape and
  is done.
- **The faulting fast path.**  Disassembling the 2848 body: `of = byKind[kind]`
  at `0x…ed82`, `of.length` at `0x…ed87`, **no test between them**.  bci 23 and
  bci 62 are uncommon traps, bci 120 is compiled, and the body has a **handler
  table** (offset 2760) the 1528 body lacks — the `of == null` grow path is
  compiled, while the test that reaches it is gone.  A null `of` faults at `0xc`
  and is not converted to an NPE.  The crash call is an **in-range null slot**
  (`kind < byKind.length`, `byKind[kind] == null`), a kind first seen on that
  seat after a higher kind already grew `byKind` — not a fresh `Arrays.copyOf`
  slot.  Session 49's "stale length comparison" reading is withdrawn.
- **The driver.**  `SeatIdsOfRepro phased` seeds `kinds` populated kinds plus a
  null tail (so `byKind` is long enough for later kinds), warms every assigned
  slot, waits for the one-sided body, clears and re-reads an assigned slot (the
  engine's bci-120 flip), fills one in-range null slot (bci 54 taken, bci 23 not
  — this compiles the grow path), warms in short chunks spaced by 20 ms so the
  compiler thread can install C1 and then C2 (one long loop races the compiler
  queue and often ends before the recompile), then shows the recompiled body a
  second in-range null slot.  The chunked rewarm is load-bearing: 12 chunks × 1
  round is 10/10 on the scratch harness; 64 rounds of one loop is 0/10.
- **The measurement.**  3/3 per JDK, 0 crashes: Temurin 21.0.13 **1528 →
  2896/2904** (the engine is 1528 → **2848**), Temurin 25.0.5 **872 → 1880**.
  On 21.0.13 the recompiled body is within 48–56 bytes of the engine's and its
  parse matches field for field: traps at bci 23/62, compiled branches at bci
  54/120, handler table.  The one difference is the test — locally the second
  in-range null slot is tested and allocated; on the affected host the body
  faults.  The host/JIT build is now the only variable left.
- **The tool.**  `--mode phased` (`--phased-n` 512, `--phased-settle-ms` 300,
  `--rounds` 12, `--kinds` 8) always logs compilations and records every
  compilation of `of` per arm: compile id, compiler, level, nmethod size, count,
  install stamp.  The artifact gains `phased.c2_bodies` and
  `phased.reached_recompile`; `--log-compilation` enables the same record for
  `quick`/`soak`.  `--rounds`/`--kinds` now resolve per mode (quick 200000/2,
  phased 12/8).
- **Padding a producer's width: no.**  The recorded full-graph sweeps capture
  **7–13** shapes at 8–128 lanes over 96 decisions, one per live width
  (`groups == children`, rows fixed by lanes).  The server's ladder would map
  1–14 onto six keys, so padding saves 4–7 **one-time** captures while adding
  masked compute to every decision; the whole first-segment excess at 128 lanes
  is 1.2 s, captures included.  The ladder exists to join producers; a single
  producer has nothing to join.  Item closed (§3.91).
- **Gate:** `test_hotspot_repro.py` **14 passed** (the phased history is in the
  source, `_nmethods` reads `size` not `bytes`, `_mode_args` for phased,
  `_phased_summary` requires both bodies, and the phased verdict requires the
  recompile); ruff (E,F) clean on the tool and the test.  Evidence:
  `runs/crforge-c4/s52/hotspot-phased.json`.

### 2026-11-18 (session 51) — the C2 miscompile reproduces, the probe is a false negative, and the host-CPU narrowing is withdrawn

Session 50 left the defect with no second observation, a clean probe on a second
box, and a conclusion that the trigger was the CPU.  Renting that class again and
running the **engine** (not the probe) reproduces it 3/3, and running the probe on
the same host passes — so the conclusion was an artefact of the probe.

- **The engine, not the probe, is the reproducer.**  Session 49's shape (4 lanes ×
  2 seats × 3 segments × 24 decisions, CPU producer, `--ppo-update`, default JVM
  flags) crashes 3/3 on a fresh Thunder A6000: segment 0's update completes, the
  next segment dies with `segfault at c`, exit 139, no `hs_err`.  The host is
  `Intel(R) Xeon(R) Processor @ 2.10GHz`, microcode `0x1`, AVX2, no AVX-512,
  Ubuntu 22.04.5, kernel `6.12.115-thunder-sandbox`, JDK Ubuntu
  `21.0.13.1+2` — a **later** patch than session 49's `21.0.13.0.3`.
- **The fault maps to `of`'s own level-4 nmethod, at the same offset every
  time.**  Each run compiles `of` twice at level 4: 1528 bytes at ~25 s and
  **2848 bytes at 62–65 s**; the crash is in the second, at offset **`0x277`**
  (compile_id 3001, stamp 65.488 s, count 15093; compile_id 2922, stamp
  62.065 s, count 13864).  With `hsdis` installed, `-XX:CompileCommand=print`
  gives `43 8b 6c cc 0c` = `mov 0xc(%r12,%r9,8),%ebp`: the length load of a null
  `int[]`, i.e. `segfault at c`.  Session 49's "the miscompiled copy is inlined
  in the caller" does not hold for this body — the `exclude` arm is marked as
  needing a re-run.
- **The probe is a false negative on the crashing host, and the reason is
  visible.**  `hotspot_repro.py --mode soak --seconds 70 --new-every 20000
  --repeats 2` reaches level 4 in 2/2 C2 arms, passes the bytecode check and
  crashes 0 times.  Its own level-4 `of` is **3968 bytes** where the engine's is
  **2848** — a different compilation of the same 194-byte method.  So exit 1 is
  "the probe did not crash", never "the host is clean", and **session 50's
  host-CPU narrowing is withdrawn** (its second box was almost certainly
  affected).  The same probe is also clean on an L40 host.
- **The artifact now carries the CPU.**  `hotspot_repro.py` records the model
  name, microcode and the full `/proc/cpuinfo` flag list beside the vector-flag
  subset; session 49 kept none of them, and the flags stopped mattering the
  moment the engine reproduced on the same AVX2-only class.
- **C1 is confirmed on the second host.**  `--jvm-arg=-XX:TieredStopAtLevel=1`:
  3/3 segments, gate pass, 563 eligible lane-decisions, 28 ACT (4.97 %),
  0 illegal, 0 rejected.  The flag's scope is every CPU producer that can meet a
  new entity kind after `of` is recompiled at level 4, not a host class.
- **The report gains its missing evidence and keeps one gap.**  A second
  observation, the nmethod offset and the assembly are in
  `docs/native/16.635.1/HOTSPOT_C2_SEATIDS.md` §4.2; what is still missing is a
  **self-contained** reproducer that reaches the 2848-byte compilation.  Until
  then the engine run is the reproduction, and the driver's negative is stated in
  the report so a triager does not read it as "cannot reproduce".
- **Gate:** `test_hotspot_repro.py` **9 passed** (one new: the host block carries
  model, microcode and vector flags); ruff (E,F) clean on the tool and the test.
  Evidence: `runs/crforge-c4/s51-a6000/`.

### 2026-11-18 (session 51b) — the warmup-corrected sweep: 3.7× from 8 to 128 lanes, and MPS is blocked by the image

Session 33 found the 8-decision scaling runs were 65–82 % one-time cost and said
to re-measure with `--warmup-forward`.  This is that measurement on two host
classes, plus the reason the MPS item is still open.

- **The shape.**  `--warmup-forward` (default) + 3 segments × 32 decisions,
  `--no-rollout --no-validate`, fp16 + full CUDA graph, one process, so segment 2
  is steady.  A6000: `Intel Xeon 2.10 GHz`; L40: `AMD EPYC`, $0.79/h.
- **The numbers.**  Steady lane-decisions/s, A6000: **203 / 372 / 428 / 535 /
  758** at **8 / 16 / 32 / 64 / 128** lanes (`T` 0.079 / 0.086 / 0.149 / 0.239 /
  0.338 s per decision; forward 11.9 → 70.9 ms).  L40: **361 / 508 / 651 / 841 /
  988** (`T` 0.044 → 0.259; forward 8.8 → 54.6 ms).  The 8→128 ratio is **3.7×**
  and **2.7×**.
- **The reading.**  The first segment is still the slow one (8 lanes 91.8 vs
  203.1 steady; 128 lanes 619.5 vs 758.4), so one-time cost is real but no longer
  the whole story at 32 decisions.  The old "0.34× at 8 lanes" was that cost; the
  steady ratio is 0.27× (203/758).  The measured 128-lane steady state is
  **758 ld/s**, against session 33's `F ≈ 4.0 s + 523 ms/decision = 490` — a
  different packet, not a like-for-like gain.
- **The card comparison.**  At 128 lanes the L40 is 1.30× the A6000 for 2.26×
  the price: per process, 0.312 matches/s against 0.407, i.e. ≈$312/1M against
  ≈$539/1M.  The A6000 remains the cheaper card until producers multiply.
- **MPS is blocked by the image, not the technique.**  `nvidia-cuda-mps-control`
  is not in the `base` template's container and is not part of the CUDA toolkit —
  it comes with the driver.  The item needs an image where the control daemon is
  reachable.
- **Gate:** 5/5 shapes per host exit 0, 0 illegal, 0 rejected, 0 failed rounds;
  no crash in any short run.  Evidence: `runs/crforge-c4/s51-sweep-summary.json`,
  `s51-a6000/`, `s51-l40/`.

### 2026-11-18 (session 50b) — the native parity cadence: a checkpoint every N updates, and the gate priced in updates

C5 said "50–150 native matches every N updates" and N was undefined, because the
trainer only wrote a checkpoint at the end of a run.  Both halves are in, and the
price says the cadence is a budget decision.

- **Mid-run checkpoints.**  `self_play.py --checkpoint-every N` writes
  `<--save-checkpoint stem>-step-<n><suffix>` every N updates (refused without
  `--save-checkpoint`, refused negative).  The save runs **outside the segment
  clock** and costs **3.0–3.1 s** for a 193 MiB checkpoint; each segment records
  `checkpoint_cadence_s`, so a cadence cannot quietly inflate throughput.
- **The curve is paired and the gate reads its own checkpoints.**
  `tools/native_analysis/parity_curve.py` runs `native_block_run.sh` per
  checkpoint against one reference on **the same seeds**, merges each point with
  `native_block.summarize` (Wilson interval, seat split, illegal/rejected) and
  refuses a point whose chunks are not one experiment.  Smoke, 4 games per point,
  seeds 101–104, mirror X-Bow, checkpoints 277/279 vs `B-general-v275`: **2–2**
  and **1–3**, overlapping intervals, 0 illegal, 0 rejected, gate complete — a
  schedule proof, not a ranking.
- **The cadence is `gate / (budget × update)`.**  Measured: a 4-game point is
  **315 s = 27.9 updates** (77.9–79.6 s/game), an update is **10.3–11.3 s**, so a
  10 % budget allows a gate **every 279 updates**.  The 50-game gate C5 asks for
  is **~350 updates**, hence every **3,500** updates at 10 % and **1,400** at
  25 %.  On this shape the gate is milestone-scale; a tighter cadence needs a
  smaller block (10 games ≈ 790 s ≈ 70 updates), not a shorter interval.  Quote
  the games per gate with the cadence.
- **Gate:** `test_parity_cadence.py` **6 passed**; bridge suite **191 passed**;
  ruff (F) clean.  Evidence: `runs/crforge-c4/s50/train-cadence.json`,
  `runs/native-parity-s50/curve.json`.

### 2026-11-18 (session 50) — the C2 miscompile gets a reproducer, and both hosts tried are negatives

Session 49 named the defect on one rented box and left it there: no reproducer, no
second observation, and a workaround that costs +108 % of the engine step.  This
session turns the finding into something runnable and records the negative.

- **The reproducer is the engine's method.**  `tools/crforge/hotspot/SeatIdsOfRepro.java`
  is `SeatIds.of` verbatim (`SeatIds.java` @ `c4721bd9`, `IDS_PER_KIND` inlined as
  1,000,000), with a `quick` driver (warm kinds, then a new one) and a `soak`
  driver (random ids over live kinds, a new kind every *N* calls).  The tool runs
  `javap -p -c` on the reproducer and on the built engine jar and compares the
  instruction streams: **117 slots, identical opcodes** (constant-pool indices
  folded away).  `-XX:+PrintCompilation` shows `SeatIdsOfRepro::of (194 bytes)`
  at **level 4**, the same method shape the box's `LogCompilation` recorded.
- **The reading is conservative.**  `hotspot_repro.py` runs `c2` (default),
  `c1` (`-XX:TieredStopAtLevel=1`) and `interpreter` (`-Xint`) arms, all with
  `-XX:+PrintCompilation`.  A pass is only a pass when the level-4 nmethod is in
  the output; a timeout, an uncompiled method, a crashing control arm or a failed
  bytecode check makes the run `inconclusive`.  Exit 0/1/2 = reproduced/negative/
  inconclusive, so a CI or a rented box can treat it as a gate.
- **Two negatives, and the second one narrows the trigger.**  This laptop
  (i5-1235U, kernel `7.0.0-43`) is clean with JDK 21.0.13 and 25.0.5: **6/6**
  default-flag arms reached level 4, **0 crashed**, and the 70 s soak is clean.
  A fresh Thunder `base` (6 vCPU, kernel `6.12.115-thunder-sandbox`, Ubuntu
  22.04.5, **the affected host's exact JDK build** `21.0.13.0.3+1-1-22.04.1-Ubuntu`)
  is clean too: quick 6/6, soak 70 s, and a 70 s soak with **1,000 new kinds**
  both inlined and `-XX:CompileCommand=dontinline`, plus a C2-only arm.  That
  fixes the JDK build, the kernel, the tier, the bytecode and the trigger shape,
  so the remaining variable is the **CPU** (that box: Intel Xeon 2.10 GHz, AVX2,
  no AVX-512) — and session 49 did not keep `lscpu`, so the affected host's model
  is unknown.  "Thunder `base`" is a host class, not a CPU: renting it again is
  not a reproduction path, `hotspot_repro.py` is the probe.  The box was deleted
  after the run; evidence in `runs/crforge-c4/s50-thunder/`.
- **The box found a driver defect.**  `kind * 1_000_000` overflows an `int`, so
  2,147 kinds threw `ArrayIndexOutOfBoundsException: Index -2146` in `ids[seat]`
  before the case under test was reached.  The driver caps at 1,000 kinds; the
  throw was the driver's, not the JVM's.
- **The report is drafted, not filed.**  `docs/native/16.635.1/HOTSPOT_C2_SEATIDS.md`
  holds the text (summary, environment, reproducer, `dmesg`/`nmethod` evidence,
  the bytecode listing, the flags that do not clear it, and the local negative)
  and the one step that is missing: a run on the affected box with
  `-XX:+LogCompilation` and, if possible, an `hsdis` disassembly.
- **Gate:** `test_hotspot_repro.py` **8 passed**; ruff clean (E,F) on the tool
  and the test.  Evidence in `runs/crforge-c4/hotspot-seatids*.json`.

### 2026-11-18 (session 49) — the crash is the JIT, not the push, and one flag clears it

Session 48 left one blocker: the producer segfaulted after a CPU PPO update on
the card, with and without the server, so the push's continuation was untested.
This session reproduced it on a fresh box, named the defect, made the workaround
a flag, and closed the gate.

- **The s48 "control" was the CPU path.**  A decision-server arm keeps the
  producer on the CPU half, so its 25 s update and 423 ms at 8 rows are CPU
  numbers; the "in-process" control was the same CPU producer with no server.
  `--device cuda --ppo-update` completes in every arm (eager, fp16, full graph,
  overlapped), so the crash was never a CUDA or torch defect.
- **Reproduced, then named.**  A fresh Thunder `base` (6 vCPU, 1×A6000, torch
  2.12.1+cu130, OpenJDK 21.0.13) crashes 6/6 in the CPU producer, with
  `segfault at c` and no `hs_err` (the JVM declines the signal).
  `-XX:+UnlockDiagnosticVMOptions -XX:+LogCompilation` maps the fault PC to the
  **level-4** nmethod of `org.crforge.env.SeatIds.of(II)I`; the
  bytecode loads `of.length` at bci 61, but the compiled body compares `n`
  against the previously loaded `byKind.length` and dereferences a null `of`.
  `-Xint` and C1 pass; C2 crashes.  Temurin 25.0.5 crashes like 21.0.13;
  `-XX:-UseSuperWord`, `-XX:UseAVX=2` and `-XX:CompileCommand=exclude,…SeatIds::of`
  do not clear it.  The development host (i5-1235U, JDK 25, torch 2.5.1) does
  not reproduce, so this is a host/JIT-specific miscompile, not a crforge loop.
- **The update is the setting, not the cause.**  The reproduction is a
  CPU-updating run: long enough for `SeatIds.of` to reach C2 (nmethod installed
  at 65.1 s, count 14,406) and its deterministic board then shows a new entity
  kind.  A `--device cuda` arm samples a different board and did not meet that
  case; the defect lives in the compiled method, so any long CPU run on this
  host that meets it crashes.
- **The flag.**  `self_play.py --jvm-arg=…` (repeatable; the `=` form keeps a
  leading `-` from being read as an option) passes the option to the **first**
  JVM start — `jvm.start()` runs before `_vector()` and a later start is a no-op
  — and `multiproc.py`, `overlap_compare.py`, `cadence_compare.py` and
  `learning_horizon.py` forward it.  Cost at 128 lanes: engine step 51.3 →
  106.9 ms (+55.6, +108 %); at 8 rows 5.6 → 4.4–8.9 ms (noise).  The 128-lane
  CUDA trainer runs under C2, so the cost is paid only by a CPU-updating
  producer.
- **The card's training gate closes.**  4 lanes × 2 seats × 3 segments × 24
  decisions, CPU producer + CUDA server, `--verify-decision-server`,
  `--jvm-arg=-XX:TieredStopAtLevel=1`: 576 lane-decisions, 24 ACT (4.4 %),
  0 illegal, 0 rejected, gate pass, and the push chain labels each segment:
  `04a50b… → fb4acf…`, `c198425… → b878470…`, `4a043a… → 2b167a…` (raw digest →
  the `policy_state_id` it hashes to with the checkpoint id).  Push cost:
  74,153,397 bytes, pack 44–51 ms, round trip 1.17 s, apply 519–545 ms.
- **The 1-process CUDA trainer is re-verified.**  128 lanes × 2 seats × 3 × 32,
  full graph, lane minibatch 16: 24,358 lane-decisions, 710 ACT (2.91 %),
  308–344 ld/s, 9.2–10.5 s per update, 0 illegal, 0 rejected, gate pass.  That
  is the session-38 shape on torch 2.12.1.
- **Gate:** bridge suite **177 passed** (3 new: two JVM-option forwarding
  tests and the multiproc forwarding test); ruff check clean on the touched
  files.  Evidence in `runs/crforge-c4/s49/`.

### 2026-11-18 (session 48) — the card prices the server, and the push lets it train

Session 47 left one measured negative and two named holes: the GPU arm and the
weight push.  Both are closed; the card arm also found a latent shape bug.

- **The server's paying region is measured, not assumed.**  Four producers on
  one A6000, `--no-validate`, fp16 + graphs on both arms: 4 lanes/producer is
  0.68× the per-process floor (one forward is launch-bound at ~50 ms and the
  round doubles it), 16 lanes is 0.62× (the join is a per-request win but a
  175.6 ms round cannot be overlapped the way four 32-row forwards are), 32
  lanes is **1.38×** (736.7 against 534.4, 1.5 requests/round, 83.9 ms per
  request against a 188.2 ms single-request round trip).  At six producers on
  six vCPU the 4-lane arm flips too: **272.4 against 235.7 (1.16×)** — the server
  is how you add collectors past the host's cores.
- **The gate's two halves are both necessary.**  At 16 lanes the per-request
  forward falls 1.9× and the aggregate still loses; at 4 lanes/6 producers
  requests coalesce 3.14/round and it wins.  Quote the shape with the verdict.
- **The push.**  `DecisionClient.push_weights` / `ensure_weights` pack the
  state as one row-major record per dtype (shapes in the schema), the server
  copies in place and verifies the client's digest both before and after, and a
  `stats` reader is allowed to skip the weight check so a supervisor survives a
  push.  The producer pushes at the segment boundary **before** the rollout store
  is labelled, so the store's `policy_state_id` is the digest the server acked.
  Measured cost: 74 MB, pack 51.6 ms, round trip 193.2 ms, server apply 57.1 ms
  (CPU; on the card the round trip is 1.19 s because both digest passes walk
  74 MB of CUDA parameters through host memory).  **Card caveat:** the card's
  training probe segfaults after its update *with or without* a server, so the
  push's card-side continuation is untested, not disproved.
- **The shape bug.**  Row-keyed staging (`self._staged[rows]`) was correct only
  while the record was padded to 48/160 (pre-session-43).  Live width made the
  first batch's width the graph's capacity, so a board that widened overflowed
  it — in `--fast-forward` and in the server's `_forwards`.  Both are keyed on
  the full shape now; `test_a_board_that_widens_gets_its_own_staging` pins it.
- **Harness.**  `multiproc.py` gained `--fast-forward` (forwarded to the
  in-process arm with the same precision/graph flags), and its docstring now
  states why the card arm must pass `--no-validate`.
- **Gate:** bridge suite **174 passed** (5 new: 4 on the push/record round trip,
  1 on the shape key, plus the harness forwarding test); ruff clean on the
  touched files; the two server arms are `--verify-decision-server`-pass, 0
  failed rounds, 0 illegal, 0 rejected.

### 2026-11-17 (session 47) — the server is one host's forward, and its gate is the action

Session 46 named two levers: the A6000 width question (needs a rental) and the
multiproc/decision-server path.  This session builds the second one, gates it,
and measures what a CPU-only host can measure.

- **Shape, not a fixed record.**  The producer sends live width (session 43), so
  the server pads every request in a round to the widest ladder bucket
  (`GROUP_BUCKETS`/`CHILD_BUCKETS`/`RELATION_BUCKETS`/`EFFECT_BUCKETS`) and the
  joined row axis to the next power-of-two multiple of the producer's rows.  A
  run therefore captures a handful of graphs, not one per board, and the join is
  one `torch.cat`.  The live-prefix copy is the only extra work on the server
  (`assemble_ms` **1.4 ms** at 8 rows, both seats).
- **The wire is one buffer per dtype.**  `PackedRecord` packs a nested record
  into row-major buffers with a schema that restores views; the payloads are one
  send per dtype and the receiver restores views into the received bytes.  A
  decision at 8 rows round-trips in **108 ms** at torch's default thread count
  (**187 ms** pinned; the forward is the whole cost), with client pack
  **1.0 ms** and unpack **0.4 ms**.  A plain pickle of tensors was not used: it
  allocates a tensor per field on every decision.
- **Two gates, both behavioural.**  Identity: the handshake compares the
  checkpoint sha256 and the weight digest, so a stale server cannot label samples
  with this run's policy state.  Sampling: `--verify-decision-server` seeds the
  server's round and requires the same action sequence from the eager path; the
  round pads live widths, so equality also proves the bucket padding is inert.
  Measured: all six action fields equal, numeric deltas 0.0.
- **The state is the client's.**  The server keeps no carry: the request carries
  the recurrent state and the reply replaces it, so a round mixes producers
  without mixing episodes.  A `next_state` is advanced in place only by the
  caller, exactly as the in-process table is.
- **One request per round at two producers; a linear forward at four.**  The
  producers are anti-phased (the CPU half is short, the forward is long), so at
  two producers requests do not share the 2 ms window and the server serialises
  them; at four the window does coalesce (**1.92 requests/round, max 4**, row
  buckets 8/16/32) and the round's forward grows with the rows it joined
  (**210 ms/request against 187**).  The aggregate is ~40 ld/s at every N — one
  forward's throughput.  That is the measured negative of §3.40 again: do not
  raise the window.  On the card the same round is flat in rows, which is the
  only shape where this design pays.
- **Thread pinning is part of the measurement.**  Unpinned, each process opens a
  12-thread torch pool on this host and the two-producer aggregate falls from
  **0.95× to 0.67×** while the server's 8-row forward inflates 187 → 233 ms.
  `multiproc.py --omp-threads` (default 1) pins every process; on the card it
  matters less, but the arm must not read a thread-pool fight as the
  architecture.
- **The seeded-window bug.**  A seeded request must be served alone; when it
  shared a queue with other producers the round was refused and the server died.
  The queue is split into plain and seeded groups, and a failed round now replies
  errors to its clients and keeps serving.  Regression test: two clients behind
  one barrier, one seeded.

### 2026-11-17 (session 46) — the channel write stays; the row index was the cost

Session 45 named child feature assembly as the next term and proposed a packed
channel write that kept the last-axis layout.  The measurement kills the
candidate and finds the cheaper term beside it.

- **The packed write is slower, in every form.**  Child features are
  `(lanes, live children, 72)` and only 11 columns are written.  Current strided
  writes **0.116 ms**; `np.zeros` then `out[..., :11] = np.stack(columns, -1)`
  **0.146**; `np.empty`, packed assign and zero the tail **0.175**; a
  feature-major buffer with one transpose copy **0.322**.  The arithmetic is
  0.043 and the zeros 0.030, so the rest is the 11 strided stores.  A packed
  build replaces them with a contiguous 11-wide block plus one more strided copy
  — it can only add a pass.  The group slab (14 of 22 columns) orders the same
  way, 0.126 against 0.157.  Session 38's transposed negative was not a width
  artefact.
- **The gather paid for its index form, not its data.**  `_assemble` gathered 35
  row fields with `values[lane_index, order[:, :width]]`; NumPy rebuilds a
  broadcast index tuple on every call.  A row field is `(lanes, row width)`, so
  `values.ravel()[lane * width + column]` is the same selection with one index,
  built once per order (`flat_offsets = lane * width`, then one add).  Velocity
  is the one 3-D field and now gathers both components with one
  `reshape(-1, 2)[flat]` instead of two gathers plus `np.stack`.  Isolated, 35
  gathers **0.310 → 0.058 ms**; alternating pair A/B **0.195 ms** per decision
  at 128 lanes; phases `group_gathers` 0.213 → 0.111, child features 0.482 →
  0.334.
- **The row scatter is the same split.**  `_CompactRows.scatter` / `table` use
  `out.ravel()[flat] = values`; **0.0031 → 0.0015 ms** per call (701 live rows),
  `entity_rows` 0.643 → 0.55 in the build A/B.  `gather_form` on the builder is
  the measurement switch (`"flat"` producer, `"two_index"` the old form); both
  records are asserted equal in the tool and the two-index path is not a
  semantic.
- **The pair build and its phases.**  Two builds → one: **0.940 → 0.635 ms** at
  8 lanes (32.5 %), **1.321 → 0.907** at 16 (31.3 %), **1.925 → 1.577** at 128
  (18.0 %), **4.546 → 3.964** at 256 (12.8 %).  The 128-lane phase table sums to
  **1.366 ms, 95.0 %** of a same-loop wall: rows 0.306, child features 0.186,
  group features 0.171, zeroing 0.167, child order/rank 0.166, orders 0.124,
  targeted 0.072, group gathers 0.067, group rank 0.047, index scatter 0.046,
  record 0.014.  The gather saving at 8 lanes is 0.056 ms — read the paired mean
  there, the block medians do not separate it.
- **Gate:** `batched_tensorize.py --seats 0 1` **3,008/3,008** lane-decisions,
  0 mismatches, 5,196 micro-actions, **0.0282 ms/lane-decision** (21.2×), end to
  end **0.0441**; seed 2 **3,008/3,008**, 5,361 actions, **0.0317** (21.4×),
  0.0469.  Bridge suite **148 passed**; ruff clean.  The `--ppo-update` probe
  (4 lanes × 2 seats × 2 segments × 24 decisions, lane minibatch 4, sequence
  chunk 8) is **bit-identical** to session 45: KL 1.90e-3 / −5.85e-3, clip
  2.08 % / 1.56 %, returns 0.338085 / 0.2, 18/375 ACT, checkpoint 277
  reload-identical (digest `eb7009aa…`), 2/2 on-policy, 0 stale, gate pass,
  0 illegal / 0 rejected.
- **One tooling fix.**  The phase share is now against a wall timed in the same
  loop as the phases: this host's segments drift by more than 50 %, and a wall
  timed elsewhere in the run made the share read 56–115 % before it was fixed.
- **Next term:** the pair build is 1.577 ms and `_entity_rows` is the largest
  term (0.306); the gathers are 0.186 + 0.067.  The rest is small-array NumPy
  call overhead over ~150 calls.  The section is ~2.2 ms of a ~254 ms CPU half,
  so the next CPU dollar is not here: the A6000 width question (does the group
  encoder's width term exist on the card) and the multiproc/decision-server path
  are the levers.  If this section is revisited, batch the per-field allocations
  into one multi-field row buffer; do not pack the channel write.

### 2026-11-17 (session 45) — both actor seats' group sections come from one build

## Decisions — what changed

### 2026-11-17 (session 45) — both actor seats' group sections come from one build

Session 44 closed the reserved-axis work and named the next move: batch the
group field writes across the two seats, or make one packed assembly pass.  The
producer was building the section twice per decision, once per actor seat.

- **The seats share almost every row.**  They differ in a row's position (the
  perspective flip), the sign of its velocity, and whether a group's owner is
  `self` or `enemy`; every id, hitpoint, table value, sort key, count, slot map
  and rank is the same.  `_entity_rows` is now `_compact_rows` (axis) +
  `_core_rows` (shared values) + `_seat_rows` (position/extent/velocity), and
  `build_pair` stacks the two seats' rows and runs the assembly once at
  `2 × lanes`.  `_assemble` takes a per-lane `actor_owner` array; that is the
  only owner change.  `_check_pair` refuses mismatched configs, capacities or
  entity tables.
- **The measurements.**  Two single-seat builds against one pair, same process,
  by shape: **8 lanes 0.838 → 0.569 ms (32.1 %)**, **16 lanes 1.240 → 0.855
  (31.1 %)**, 128 lanes 2.234 → 1.683 (24.7 %), 256 lanes 3.643 → 3.248
  (10.8 %).  A single seat is 1.015 ms at 128 lanes, so the pair costs 1.66×
  one seat where two builds cost 2×.  The saving is the duplicated work
  (orders, targeted mask, rank scatters, seat-independent feature arithmetic),
  which is why it is largest at the producer's own width and shrinks as
  per-element work dominates.
  The pair's phase table at 128 lanes (rows 0.349, child features 0.281, group
  features 0.206, zeroing 0.171, child order/rank 0.164, gathers 0.145, orders
  0.129, targeted 0.075, index 0.062, rank 0.049, record 0.014) sums to
  **97.6 %** of the timed call.
- **The producer uses it everywhere.**  `tensorize_seats` builds the pair once
  and hands each seat its section; `VectorTensorizer.tensorize` /
  `build_sections` accept a prebuilt `groups`.  `groups_ms` is now recorded once
  per **decision**, not once per seat — the one `cpu_half` field whose meaning
  changed.  `whole_ms.tensorize` is untouched by the accounting.
- **Gate:** `batched_tensorize.py --seats 0 1` (pair path) **3,008/3,008**
  lane-decisions, 0 mismatches, 5,196 micro-actions, **0.0291 ms/lane-decision**
  (22.5×), end to end **0.0440**; seed 2 **3,008/3,008**, 5,361 actions,
  **0.0316** (21.0×), 0.0477; the same host with `--no-pair` is **0.0324**
  (19.7×), 0.0474.  Bridge suite **148 passed** (two new tests: pair equals two
  single builds; a mismatched table is refused); `test_training_v4*` **457
  passed, 8 skipped**; ruff clean.  The `--ppo-update` probe is **bit-identical**
  to session 44's (KL 1.90e-3 / −5.85e-3, clip 2.08 % / 1.56 %, return 0.338085
  / 0.2, 18/375 ACT, checkpoint 277 reload-identical, 2/2 on-policy, 0 stale,
  0 illegal / 0 rejected).
- **Next term:** the group build is 0.84 ms per seat at 128 lanes, and the
  largest remaining term is child feature assembly (0.28).  A packed channel
  write is the candidate, but session 38 measured the transposed
  `(features, lanes, groups)` form as a loss (1.02 against 0.56 ms), so the next
  attempt must keep the last-axis layout in one store.  The A6000 width question
  is still open.  *(Session 46: the packed channel write loses in every form
  (0.116 against 0.146/0.175/0.322); the real cost was the two-array advanced
  index behind the 35 row gathers, 0.195 ms per pair decision, and the pair
  build is 1.925 → 1.577 ms.  See `FINDINGS.md` §3.80.)*

### 2026-11-17 (session 44) — the entity rows are compact, and the build can name its own terms

Session 43 closed the pad and left the remaining gather/feature assembly as the
next term.  Measuring it needed a breakdown, and the breakdown named
`_entity_rows` — which was still writing the 200-row axis on the way out.

- **The row view is compact.**  `_entity_rows` returns lane-major
  `(lanes, widest live count)` fields plus `counts` and `slots` (the entity slot
  behind each column).  The padded columns keep the old fills and are unread.
  `_orders`, `_child_order`, `_targeted_mask` and `build` already consumed an
  include mask and flat indices, so their logic is unchanged; only the meaning
  of an index (compact column vs entity slot) moved.  The dense
  `group_index`/`child_index` the record needs are scattered back once.
- **`build(phases=...)` is the new microscope.**  Every term is timed with one
  `perf_counter` pair boundary when the caller passes a dict; the producer
  passes nothing, so the flag costs one `if`.  The phase sum is checked against
  the timed call, and `cpu_half_micro.py` records the table.
- **The measurements.**  `_entity_rows` **0.43 → 0.18 ms**, build
  **1.35 → 1.00 ms** per seat on the settled fixture; the pre-session-41 dense
  form is 0.995 ms in the same process, so the compact rows are **5.5×** that.
  Build phases: rows 0.217, child features 0.156, group features 0.121, child
  order/rank 0.112, zeroing 0.092, group gathers 0.081, orders 0.082, targeted
  0.050, group rank 0.033, index scatter 0.036, record 0.013.
- **The oracle path still pads and still compares.**  `build(live_slots=False)`
  densifies the compact rows (`_dense_entity_rows`) and runs the session-42
  full-capacity assembly; `pad_group_section` still projects the live record
  onto it, and the digest gate compares every field including the tail.
- **Gate:** `batched_tensorize.py` **3,008/3,008** lane-decisions, both seats, 0
  mismatches, **0.0345 / 0.0490 ms/lane-decision** (18.5× / 12.6×), end to end
  **0.0499 / 0.0638**; bridge suite **146 passed**; native PPO/trainer subset 49
  passed, 4 skipped; ruff clean; producer probe 2/2 on-policy, 0 stale, gate
  pass, 0 illegal / 0 rejected, checkpoint reload-identical and the same KL
  trace as session 43 (the record did not move).
- **Negative:** `np.copyto(where=...)` for the masked zeroing is **0.184 ms
  against 0.092**, so the fancy assignment stays.
- **Next term:** the build is 1.00 ms with no reserved-axis work left; the
  remaining terms are feature assembly (~0.28), rows (~0.22), order/rank/mask
  (~0.23), zeroing (~0.09) and gathers (~0.08).  A further CPU win needs
  batching field writes across both seats or one packed assembly pass, not
  another single-field rewrite.  The A6000 width question from session 43 is
  still open.  *(Session 45: the two seats' sections are one build now, 2.234 →
  1.683 ms per decision, 24.7 %.  See `FINDINGS.md` §3.79.)*

### 2026-11-17 (session 43) — the group record keeps its live width, and the pad leaves the producer

Session 42 left the fixed-capacity pad as the named next term and said to
measure it first.  It measured at **0.28–0.60 ms** of the live build
(**1.27–1.54 ms**) and **7.5 MB** per seat — and removing it also removed the
padded rows from the model's forward.

- **The pad, measured.**  `cpu_half_micro.py` now replicates the old pad field
  by field on the live arrays and asserts the result equals the oracle's tail:
  48 group + 160 child slots from 11 and 11 live rows is **7.5 MB** of fresh
  zeroed arrays per seat.  Four same-process runs: pad 0.580 / 0.596 / 0.302 /
  0.276 ms against build 1.362 / 1.544 / 1.271 / 1.352 ms, i.e. the padded
  build is 1.20–1.43× the unpadded one.  The spread is allocator/page state;
  the copy volume is fixed.
- **The record carries the live width.**  `VectorGroupBuilder.build` returns
  the batch's live group/child width on the producer path; the padded
  full-capacity form stays as `live_slots=False` for the A/B and as the oracle.
  Nothing downstream needed a fixed width: the model sizes its token set,
  `belongs` and attention from `groups.mask`; `validate` checks capacities as
  maxima; the relation builder derives group rows from the record; the PPO
  update pads records to the chunk maximum with
  `concatenate_padded_tensor_records`.
- **The gate projects, it does not weaken.**  `pad_group_section` (sections) and
  `pad_group_record` (records) grow a live record to the configured capacity
  with the oracle's tail values — zeros, `OWNER_NEUTRAL` owner, `False` masks.
  `lane_batch` gained `group_capacity`/`child_capacity` and the digest gate
  passes them; `compare_group_section` pads internally, so every field,
  including the tail and the digest, is still compared byte for byte.
- **The forward A/B: 27–34 % on CPU.**  `cpu_half_micro.py` times
  `model.encode_observation` on the same batch live and padded to 48/160:
  **1.63 / 1.71 / 1.77 s live against 2.49 / 2.47 / 2.44 / 2.38 s padded** at
  128 rows.  The width change is not only a build win; it removes masked tokens
  from the group encoder.  This is this host's CPU; the A6000 was reported flat
  in rows and its width term is unmeasured.
- **Gate:** `batched_tensorize.py` **3,008/3,008** lane-decisions, both seats, 0
  mismatches, 5,196 micro-actions, **0.0476 ms/lane-decision** (13.8×), end to
  end **0.0650**; seed 2 **3,008/3,008**, 5,361 actions, **0.0522** (12.4×),
  0.0673; bridge suite **145 passed**; ruff clean.  The variable-width record
  updates in the real loop: a 4-lane × 2-seat × 2-segment × 24-decision
  `--ppo-update` probe is **2/2 on-policy**, 0 stale, gate pass, 0 illegal / 0
  rejected, checkpoint reload-identical.
- **Next CPU term:** the remaining `build` is 1.27–1.54 ms per seat:
  `_entity_rows` 0.37, the three sort/mask functions 0.17, and the rest is the
  live-width gather/feature assembly.  Do not look for another dense pass —
  there is none left in this section.  The open box question is whether the
  group encoder's width term exists on the A6000.  *(Session 44: `_entity_rows`
  was still scattering into the 200-row axis; it is compact now, 0.43 → 0.18
  ms, build 1.00 ms.  See `FINDINGS.md` §3.78.)*

### 2026-11-17 (session 42) — the group sort keys at live width, and the fixture that hid the board

Session 41 closed `_entity_rows`; session 40's list left the two sort keys and
`_targeted_mask` as the remaining dense passes over the 200-row entity axis.
They are done — and measuring them exposed a fixture defect that changes how
every micro number since session 37 should be read.

- **`_targeted_mask`: 0.356 → 0.083 ms** (4.3×).  The lane-tagged key array and
  the `searchsorted` are built from the live rows only.  Every invalid or tower
  slot carries `entity_id == -1`, which tags as `0xFFFFFFFF` and cannot equal a
  non-negative target id, so those slots are false in both forms — the dense
  batch-wide key arrays were ~33× the work for the same answer.
- **`_orders` 0.114 → 0.065 ms, `_child_order` 0.170 → 0.079 ms.**  `build`
  reads at most the lane's retained width from each order, so `_slot_order`
  sorts only the included slots (live entities, or child candidates) and returns
  them as entity indices; the padded tail repeats row 0 and is masked off, which
  is exactly what the invalid tail was.  `build(live_slots=False)` pads the axis
  to 48/160 so the session-39 A/B still measures the wide assembly.
- **The fixture bug: there was never a live board.**  `cpu_half_micro.py`
  stepped 60 ticks, then played for 15, while `FIRST_PLAYABLE_TICK` is **90** —
  so every play was refused and the "settled board" was its six towers.  The
  recorded `live_entities_mean: 6.0` was `entity_valid`, i.e. towers; the
  non-tower width was **0**, so `live_g` was 1.  It now waits 95 ticks, plays 15,
  settles 40: **11.5 live entities mean, 17 max**.  Consequences are recorded in
  `FINDINGS.md` §3.76; the key one is that sessions 37–41's micro section numbers
  are empty-board numbers, and their wins were width/padding wins.
- **The session-39 and -41 changes survive the correction.**  On the live board
  the group `build` is **6.99 → 2.18 ms** (3.2×; the empty fixture said 2.82×),
  and `_entity_rows` is **1.297 → 0.762 ms** (1.70×; the empty fixture said
  3.65×).  Both are same-process A/Bs, so both statements are true of their own
  batch — the fixture, not the change, was the problem.
- **Gate:** `batched_tensorize.py` **3,008/3,008** lane-decisions, both seats, 0
  mismatches, 5,196 micro-actions, **0.0660 ms/lane-decision** (11.2×) and seed 2
  at **3,008/3,008** / 5,361 / 0.0634 ms (11.8×); bridge suite **145 passed**;
  ruff clean; a 2-update producer probe is 2/2 on-policy, |KL| 7.46e-5, 0
  illegal.
- **Next CPU term:** on the corrected fixture `build` is 2.18 ms, of which the
  three functions above are 0.23, `_entity_rows` 0.76, and the rest is the
  gather/feature assembly plus the fixed-capacity pad to 48 group / 160 child
  slots (~3 MB of fresh zeroed arrays per build).  The padding is the next
  candidate; measure it before rewriting it.  *(Session 43: measured at
  0.30–0.60 ms and 7.5 MB per seat, and the producer no longer pads; see
  `FINDINGS.md` §3.77.)*

### 2026-11-17 (session 41) — `_entity_rows` builds at live width, and the group section follows

Session 40 left one CPU term named (`_entity_rows`) and no native question to
open.  The term was **dense over the reserved entity axis**: 128 lanes × 200
slots.  *(Session 42 found the fixture claimed ~6 live entities but had towers
only; the live width is real on a real board, just not on that fixture.)*  It is
now assembled at the live rows and scattered into one zero row per field.

- **1.10 → 0.30 ms** at 128 lanes with 6 live entities (3.65×), both forms run
  back to back on the same batch in one process (`runs/crforge-c4/
  cpu-half-micro-s41.json`).  *(Corrected in session 42: that fixture had zero
  non-tower entities.  On a live board the same change is **1.297 → 0.762 ms**,
  1.70×.)*  The group section falls with it, **2.68 → 1.98
  ms** per seat; the same tool's `groups` section mean is **6.38 → 5.50
  ms/seat** across runs (same shape, different process, so the host is in that
  half).
- **The largest single term was an index conversion, not a gather.**  Every
  `table[rv]` on an int32 row map makes NumPy convert all 25,600 entries to
  `intp` first; six int64 gathers measured **0.42 ms**, and the same six with one
  pre-converted `intp` map **0.19 ms**.  The live-row form removes the wide
  gather entirely (nine gathers on 640 live rows: **0.29 → 0.065 ms** offline).
- **No new expression forms.**  The `where` masks become one `np.zeros`/`np.full`
  plus a live-row scatter, which is what those masks were producing for the
  invalid slots.  Velocity is the one place the dict differs off the live rows:
  an invalid slot's velocity is now zero instead of a copy, and it was never
  readable (`velocity_valid` is false there and the group/child assembly masks
  it).  `cpu_half_micro.py` keeps the dense form as the oracle and asserts the
  two agree on every field, live rows for velocity.
- **Gate:** `batched_tensorize.py` **3,008/3,008** lane-decisions, both seats, 0
  mismatches, 5,196 micro-actions, **0.0725 ms/lane-decision** (12.0× the
  per-lane oracle) and seed 2 repeats it at **3,008/3,008** / 5,361 /
  0.0779 ms (11.3×); bridge suite **145 passed**; ruff clean; a 2-update
  producer probe is 2/2 on-policy with |KL| 7.46e-5, 0 illegal, gate pass.
  The per-lane wall is this host's (its oracle is also slower than session 39's:
  0.869 vs 0.706 ms), which is why the claim is the same-process A/B.
- **Next CPU term:** the group section still builds its sort keys and
  `_targeted_mask` over the full 200-row axis (offline: `_orders` 0.11,
  `_child_order` 0.18, `_targeted_mask` 0.27 ms).  `_targeted_mask` is the
  cheapest of the three to move to live width; the gathers are still not worth a
  rewrite.

### 2026-11-17 (session 40) — the 50-game gate says no; the 4–0 was the matchup

Session 39 left exactly one question — does the pilot beat the seed it started
from, natively — plus an n = 2 baseline that could not answer it.  Both are now
50-game blocks on the same rig, seeds 101–150, sampled actions, paired seats,
0 illegal, 0 rejected, 0 truncated (`FINDINGS.md` §3.74).

- **The pilot loses to its seed: 19–30, 1 draw.**  Mirror X-Bow (the learner
  deck on both seats), so only the policy differs: **38.8 %** [26.4–52.8],
  p = 0.15; seats 11–14 and 8–16.  A 16-update 1e-5 run with |KL| ≤ 8.6e-5
  should not move a policy far, and it did not: the upper bound rules out a
  gain larger than ~3 points.  The pilot is **not promoted**.
- **The 4–0 smoke measured the deck matchup, not the policy.**  `evaluate`
  swaps decks *with the policy*: A keeps `deck0` at either seat.  The smoke was
  pilot-on-X-Bow against seed-on-Hog in all four games; the handoff's "A's deck"
  column and "each policy won its home-deck game" reading were wrong.  Rows now
  record `decks_by_owner`, an evaluator test pins the mapping, and the gate block
  is a mirror.  The s39 table is corrected in place.
- **The baseline is 49–1 for the seed, and it is a canary failure.**
  `B-general-v275` beat `A-hog26-v738` 49–1 on the recorded fixed decks (25–0 at
  seat 0, 24–1 at seat 1); 49 games ended at tick 3,601, the loss at 6,104.  Hog
  made more actions (median 41.5 vs 31) and was never refused, so this is not a
  stall.  It does not dispute hog26's published 71.9 % ±2.6 — that is **vs its
  own start** — but it makes the checkpoint unusable as the learner's bar.  The
  seed mirror is the policy gate now; hog26 stays a canary with that caveat.
  Session 39's n = 2 1–1 was a 3.9 %-likely coin flip under 98 %; the §3.40
  2–2 used the standard deck on *both* seats and never measured this matchup.
- **The instrument is fine.**  **100/100 games** clean across the two blocks,
  ~75–90 s/game, no retries in the pilot block; the one transient was a machine
  restart that killed the chunk (resumed by seed) — lifecycle, not the rig.
- **Tooling.**  `tools/native_analysis/native_block.py` merges evaluator chunks
  and reports the decisive-game win rate with a Wilson interval, an exact
  two-sided p-value, the per-seat split and consistency warnings (checkpoint,
  ruleset, native build, match shape, duplicate (seed, a_owner), contiguous
  seeds).  `native_block_run.sh` runs a block in chunks behind a fresh engine
  host with one retry and a resume seed.  `--sample` stays required for these
  sparse-action checkpoints.
- **Gate:** `test_native_block.py` (3) + `test_evaluate_and_serve.py` (2) pass;
  ruff clean on the touched files (E,F).  100 native games with 0 illegal, 0
  rejected, 0 truncated.

### 2026-11-17 (session 39) — both tracks run now; the box waits for a native signal

The session split the work instead of renting: one bounded crforge pilot and a
native-parity baseline start now, the CPU half is fixed while they run, and the
provider/8-vCPU question is answered by arithmetic and deferred until the pilot
shows a native win-rate signal.

- **Groups were paying for reserved slots, not live rows.** A settled 128-lane
  board carries **6 live entities** per lane, while the record reserves 48
  groups and 160 children. `VectorGroupBuilder.build` now assembles
  `live_g`/`live_c` slots and pads to the fixed capacity once. One-process A/B
  on the same batch: **7.57 → 2.68 ms** (2.82×, records asserted equal); the
  producer's section mean is **13.18 → 6.01 ms/seat**. The digest gate is
  unchanged: **3,008/3,008** lane-decisions, both seats, 0 mismatches,
  **0.0546 ms/lane-decision** (12.95× the oracle; the session-37 record was
  0.0811, 7.46×). The "batch the 35 gathers by dtype" rewrite is withdrawn:
  at live width the gathers are small, and `_entity_rows` (1.16 ms at 6 live
  entities) is the next term.
- **The bounded pilot trains and checkpoints.** `runs/crforge-c4/
  self-play-pilot-s39.json`: 4 lanes × 2 seats × 16 segments × 24 decisions,
  **16/16 on-policy updates**, |KL| ≤ 8.6e-5, clip 0, all finite, 102 ACT /
  2,953 eligible (**3.45 %**), **0 illegal, 0 rejected**, gate **pass**.
  Checkpoint `pilot-s39-checkpoint.pt` is step **291** (275 + 16), reloads to
  the same weight digest and is 203 MB. The return is 0.330 → 0.308 over the 16
  windows (last-5 0.293 vs first-5 0.226); every window is a truncation, so
  this is a value-fit trace, **not** a learning or win-rate claim.
- **The native baseline is the gate and it is separate.** The seed
  `B-general-v275` played `A-hog26-v738` on the locked X-Bow deck (seat 0
  X-Bow, seat 1 Hog 2.6; paired seeds, sampled actions) through the x86_64
  native probe: **2 games, 1–1, 0 illegal, 0 rejected, 0 truncated, both
  terminals**, 182 s.  *(Corrected in session 40: the evaluator swaps decks
  with the **policy**, so each policy kept its own deck in both games; the
  session-40 50-game block is 49–1.)*  n = 2 is a mechanics baseline, not a
  ranking.  A 50-game block is ~75 min and is session 40's first item.  crforge
  is never a promotion source.
- **The pilot passed its first native smoke, and it is not a promotion.** The
  saved checkpoint beat the seed it started from **4–0** (seeds 11–14, both
  seats, 0 illegal, 0 rejected; `runs/native-parity-s39/pilot-vs-seed-4games/`).
  At n = 4 with sampled actions and 16 updates at 1e-5, that is a direction, not
  a win-rate signal (1-in-8 two-sided tail under an even prior).  The 50-game
  paired block is the gate; no rental follows from this.  *(Session 40: the
  block is **19–30**; the 4–0 was the deck matchup.)*
- **Provider/8-vCPU is still arithmetic.** At the recorded full-graph terms
  ($43.8/M measured, 2.221 matches/s at 6 vCPU) an 8-vCPU box at $0.43/h needs
  **2.73 matches/s** to break even — 1.23× the measured rate, above the 1.842
  predicted ceiling, with the sweep already saturating at four producers. That
  is a measurement to take **after** a native signal, not a rental now, and not
  a multi-box spend.
- **Gate:** bridge suite **145 passed** (14 fast-forward), **3,008/3,008**
  digest-identical, ruff clean on the touched files.

### 2026-11-17 (session 38) — the box confirms the record, and the full-call graph is the fastest forward

Session 38's first two items were one rental: re-measure the 128-lane producer
with session 37's record, then settle `--full-cuda-graph`.  The box is a Thunder
`base` (6 vCPU, 1×A6000, $0.35/h, torch 2.12.1+cu130), 128 lanes × 2 seats ×
32 decisions, `OMP_NUM_THREADS=1`.

- **Decode is 11.7 ms, down from 68.**  Steady fp16 `--no-cuda-graphs`: tensorize
  105.2, **decode 11.7** (`decode_only 5.3 + record 5.0 + plan 0.4`), step 126.3,
  facts 13.2, forward 161.1, `T = 417.6 ms` against session 36's 425.1 (tensorize
  98, decode 68, step 113, facts 12, forward 133).  The record fix is confirmed;
  this box's other buckets were slower, so the single-process wall barely moved.
- **Six producers: 1.254 → 1.512 matches/s (+20.6 %), $77.5 → $64.3/1M.**  The
  sweep is 0.478 / 0.940 / 1.477 / 1.512 at 1/2/4/6, retention 0.50 at six,
  **1.17× the predicted 1.293 ceiling**, and the tool calls the binding
  **shared**.  At six the CPU half is **547 ms, not 728** (2.15× vs 2.49×); the
  forward slowdown is also milder (1.78× vs 1.95×).  Removing ~1,300 syncs per
  decision removed a contention amplifier, not just 56 ms of work.  Quote the
  box's own alternating A/B: the forward ranged 102–183 ms across runs, and the
  first GPU run after an idle period was the slowest.
- **The full-call graph passes and is the fastest shape.**  `--full-cuda-graph`
  measured **forward 102–106 ms, `T` 0.338–0.347 s**, against partial graph
  131–149 / 0.384–0.416 and eager fp16 139–183 / 0.407–0.448.  Verification is
  0/256 mismatches on all six fields (fp16 log-prob Δ 3.6e-7, entropy 4.1e-6,
  value 4.0e-4).  It captures encode + core + the sampled decode, so the eager
  decode and its staging leave the packet.
- **The full-graph sweep is 2.221 matches/s at six producers, $43.8/1M.**  The
  same 1/2/4/6 sweep with `--full-cuda-graph` is 0.592 / 0.957 / 1.700 / **2.221**
  (retention 0.65), **1.21× the 1.842 predicted ceiling**, saturated at four;
  at six the forward is 163 ms and the CPU half 408 ms against the no-graph
  sweep's 290 / 547.  The graph removes work *and* contention: **+47 % over the
  no-graph 1.512**, **$64.3 → $43.8/1M** (2,428: $87.6).  This is the provider
  number; the card is still shared-bound, not cleanly serialized.
- **The full-call gate needed a policy-independent probe.**  It compared the
  sampled log-probabilities of two replays of the warmup batch; an initial board
  samples almost every row as WAIT, so identical actions cannot separate a frozen
  stream from a healthy one.  The gate now captures a one-value `torch.rand` with
  the decode and makes **that** the authority (`crforge_bridge/fast_forward.py`);
  the log-prob delta is still recorded.  Torch 2.12 also names the default
  generator before capture (`register_generator_state`; a deprecated no-op on
  2.14+).  With both, the two full runs report **probe Δ 0.1616** and
  **log-prob Δ 13.86** and pass.  Whether the earlier refusals were frozen
  graphs or mere false refusals was not isolated; the probe is the gate, the
  registration is the version shield.
- **Session 36's "the graph costs 34 ms" is not reproduced.**  A partial graph
  won both alternated pairs here (0.403 vs 0.448, 0.384 vs 0.407); the full graph
  won by more.  The CPU half swings 10.7–42 ms run to run (interleave), so a
  single cross-session comparison cannot settle it.
- **Two CPU negatives are measured and not adopted.**  The `(features, lanes,
  groups)` transpose is **1.02 ms against 0.56 ms** for the current last-axis
  writes; `§3.70`'s "next attempt" is withdrawn.  Masking the nine entity-table
  gathers in place saves **0.12 ms**, below the section's noise; it stays as the
  micro tool's B-side.
- **The box found a real GPU defect in the PPO update.**  With lane minibatching,
  `distributed_ppo_update_v4` indexed a CPU `lane_indices` with the CUDA
  `active_lanes`; the index now moves to `lane_indices.device`.  On top of that
  fix, the overlapped loop at 128 lanes × 3 × 32 (full graph, lane-minibatch
  16) is **92.1 s serial vs 86.2 s overlapped (1.07×)**: collection falls
  468 → 380 lane-decisions/s (0.81×), 2 of 3 updates are stale, and **the gate
  fails**.  The worker's backward and the collector's forward share one A6000,
  so 5.9 s of the 39.6 s update is hidden and the rest is paid in contention —
  the GPU form of session 32's CPU result.  It stays a gated option, not a
  default.
- **Gate:** bridge suite **144 passed** (14 fast-forward) and native PPO
  (`test_training_v4_ppo_padding`, `test_training_v4_learning`) **36 passed**;
  ruff clean on the touched files.

Evidence: `runs/crforge-c4/cloud-thunder-a6000-s38.json` (raw under
`runs/crforge-c4/cloud/`), `runs/crforge-c4/self-play-s38-*.json`,
`runs/crforge-c4/cpu-half-micro-s38.json`, `FINDINGS.md` §3.71,
`ROADMAP_CRFORGE.md` §4.

### 2026-11-17 — the CPU half is profiled: the 68 ms decode bucket was ~1,300 GPU syncs

Session 37's first item needed no rental: profile the CPU half, then make it
fall.  The profile is now part of the producer and the biggest term was a
Python defect, not arithmetic.

- **The box's decode bucket was `record_action`, not the decoder.**  The record
  wrote one tensor element per lane per micro-action per field, and each
  `.item()`/`.cpu()` on a sampled tensor is a device sync: at 128 lanes × 2
  seats × 2 micro-actions × 6 fields that is ~1,300 syncs per decision, which is
  the recorded 68 ms.  It is one NumPy pass now (UID match, gather, write), with
  the candidate columns converted once per decision by `candidate_tables()` and
  shared with the decoder: **4.32 → 0.11 ms** on CPU at 128 lanes over 40
  repeats.  The decoder itself is 0.6 ms.
- **tensorize is the row builders, and groups dominate.**  At 128 lanes × 2
  seats on this laptop the sections are groups 17–30, relations 3–6, candidates
  2.5–4.5, cards 1–2.4, towers 0.9–2.1, planes 1.1–1.7, empty 0.6, scalars
  0.2–0.5 and interleave 4–9 ms (the range is the live entity count); engine
  steps 31–37, facts 8–12.  Groups' own time is its **feature assembly** (one
  strided write per feature into a `(lanes, groups, channels)` array), not the
  gather.
- **Two plausible optimisations measured as noise and one was reverted.**  The
  "one copy instead of two" interleave (write into `dst[seat::seats]`) is not
  faster: `torch.stack(dim=1)` already writes once into the interleaved
  destination and the reshape is a view.  `np.take_along_axis` → advanced
  indexing measured 0.46 ms one run and −0.07 the next.  Both are kept in
  `tools/crforge/cpu_half_micro.py` as B-sides, not claimed as wins.
- **Every artifact now carries the half.**  Per segment: `cpu_half` with the
  eight sections, the interleave, and `decode_only`/`record`/`plan`; and
  `--profile-collection` adds a cProfile top list by cumulative and own time.
  They sit beside `ms`, never inside it, so `timing_accounting.py` still
  reconciles the bucket sum against the wall.
- **`--full-cuda-graph` is wired and refuses a frozen stream.**  One capture
  covers encode + core + `select_action_from_context`; before the first decision
  two replays of the same batch must draw different log-probabilities, or the
  flag is refused.  That is a behavioural gate, not a torch-version claim:
  `CUDAGraph::capture_begin()` registers the **default** generator
  unconditionally, so a captured `Categorical.sample()` advances on replay, and
  `register_generator_state()` is a deprecated no-op in newer torch builds.  A
  build without the cooperation now fails loudly instead of sampling one
  frozen stream.
  `--verify-fast-forward` still requires fp32 action equality.  The wall is a
  box measurement.  *(This supersedes session 36's "a captured sampler would
  freeze its RNG": the default generator is registered by the capture itself.)*
- **Decision:** the provider sweep stays paused.  One term of the 291 → ~108 ms
  target is gone; the rest is groups (feature assembly), the engine step and
  facts.  The box re-measure and the six-producer sweep are session 38's first
  items, not a second rental on the strength of a CPU-side rewrite.
- **Gate:** `batched_tensorize.py` **304/304 lane-decisions digest-identical**
  (both seats, 762 micro-actions through the new record); bridge suite gains
  `test_cpu_half.py` (6) and three full-graph tests; ruff clean.

See the session-37 section below, `FINDINGS.md` §3.70 and
`ROADMAP_CRFORGE.md` §4.

### 2026-11-16 — the `ever_server` forward is ported in-process and measured on the A6000: 183 → 133 ms, but 6 producers gain only 3 %

The forward port is in (`crforge_bridge/fast_forward.py`, `self_play.py
--fast-forward`), gated numerically on CPU and then measured on one rented
6-vCPU A6000 (Thunder `base`, $0.35/h). The four tricks are the roadmap's four;
`net.py`'s TCP/pickle protocol is not ported.

- **fp16 is the win; the CUDA Graph is not, at this shape.** 128 lanes × 2 seats
  × 3 × 32, probe shape (`--no-rollout --no-validate`), steady last segment:
  forward **182.1 ms eager → 132.9 ms fp16 without graphs** (−27 %) and
  136.1 ms with graphs. The graph path is **34 ms of wall slower** (T 459.7 vs
  425.1 ms) because it captures only encode+core and the pinned staging is paid in
  the CPU half while the eager decode stays outside. `--no-cuda-graphs` is the
  better configuration until the decode is captured or several decisions are
  batched. Verification passed on every run (actions exactly equal; fp16
  log-prob Δ 6e-7, entropy 5.1e-6, value 3.8e-4).
- **The sampler stays eager, and on the box that is a choice, not a limit.**
  torch 2.5.1 (this laptop) has no `CUDAGraph.register_generator_state`; the box
  runs 2.12.1, which does. The port still captures only the deterministic half:
  it keeps the gate comparing identical RNG draws, and it keeps one code path
  for both torch versions. Capturing the sampled decode is now a specific,
  version-gated option rather than an unknown.
- **A fixed 65–77 ms per forward on CPU was a static table.** `encode_observation`
  rebuilt the whole effect vocabulary and its mechanic profiles on every call;
  they depend only on the weights. `UniversalCardPolicyV4.static_effect_memory`
  now caches them by tensor identity+version, refreshes in place so a captured
  graph reads current tables, and **bypasses the cache whenever gradients are
  enabled** so PPO still trains those embeddings. CPU: 65–77 ms → 0.1 ms per
  forward. GPU: only ~2 ms — at 256 rows the forward is compute-bound, not
  fixed-overhead-bound.
- **The predicted ceiling moves; the measured rate barely does.** `g` falls
  0.366 → 0.310 and the GPU ceiling **1.33 → 1.563 matches/s (+17 %,
  $62.2/1M predicted)**. But six concurrent producers measure **1.254 matches/s
  (0.80× the ceiling), $77.5/1M** against the pre-port **1.215 / $80.0** — a
  **3 %** realized gain. At six producers the forward slows 1.95× and the CPU
  half 2.49× (retention 0.43): the box is **shared-contended**, not cleanly
  GPU-serialized, and the CPU half is still the gate.
- **The CPU half is now the whole story.** Per decision at 128 lanes: tensorize
  **98 ms**, forward **133**, decode **68**, engine steps **113**, facts **12** =
  ~425 ms. Rollout storage (the training shape) adds **188 ms**, so a training
  run is 613 ms/decision with a 139 ms forward. The forward port is necessary,
  not sufficient — unchanged from the roadmap's arithmetic.
- **CUDA is exercised, not inferred.** Graph capture, pinned staging, fp16,
  autocast, and the in-place state-table commit all ran on the box; only the
  two-agent path (`--overlap-update`) was not re-run there.

See the session-36 section below, `FINDINGS.md` §3.69 and
`ROADMAP_CRFORGE.md` §4.

### 2026-11-16 — the provider decision is one comparison: `N_proc` vs `G × E / g`

The next session is a cloud-provider sweep, so the capacity equation is now
executable instead of arithmetic on a whiteboard:

```
matches/s ≈ 0.211 × min(N_proc, G × E / g) / T
```

- **The equation reproduces the recorded A6000 run, so its terms are pinned.**
  At 128 lanes × 32 decisions the run's steady `T` is **0.6468 s** and its median
  forward is **157.4 ms**, so `g = 0.2434` and one card serves
  **`1/g = 4.11` processes** — the roadmap's "run ~4 without changes". At one
  process the equation gives **0.326 matches/s**, the ROADMAP's 0.33; at 6 vCPU
  it gives **1.34 matches/s** and **$72.6 / 1M matches** at $0.35/h, the low end
  of the roadmap's 6–8-process row. The recorded 6-vCPU box is **GPU-bound**:
  the card saturates at ~4.1 processes.
- **A live 6-vCPU A6000 lands on the same GPU ceiling from a faster CPU.**
  Thunder `base`, 2026-11-16: `T = 0.4287 s`, median forward **158.4 ms**,
  `g = 0.3695`, so `G × E / g = 2.71` processes; CPU ceiling 2.95, **GPU ceiling
  1.331** matches/s. One process is 0.492; six concurrent producers measured
  **1.215** (0.91× the ceiling), and the sweep's rate check confirms the
  equation. The recorded box was slower (`T = 0.6468`, 4.11 processes) but its
  GPU ceiling was **1.339**: the rate ceiling is the forward's, not the CPU's.
  Only the process count moves with `T`. $73.0/M at 6 vCPU predicted,
  **$80.0/M measured** (1,214 convention).
- **8 vCPU is dominated on the A6000 at this packet.** The same card cannot
  exceed the 1.331 ceiling; at $0.43/h (+23 %) its best case is **$89.7/M**,
  above the 6-vCPU measured **$80.0/M**. Break-even would need **1.493
  matches/s**, above the ceiling. Rent 6, not 8, until `g` falls.
- **`tools/crforge/cloud_capacity.py` runs the comparison per provider.** It
  measures `T` and `g` from the producer (`--run`), scores a recorded artifact
  (`--artifact`, with `--cpus`/`--gpus`), and validates the ceiling with
  concurrent producers (`--sweep-procs 1 2 4 6`): it checks the process
  crossover **and** the measured aggregate against the GPU rate ceiling
  (0.8–1.2×), and separates a forward slowdown (GPU) from a CPU-half slowdown
  (cores). It prints the equation term by term, both rate conventions (1,214
  and 2,428), $/1M at both predicted and measured throughput, and the provider
  adjustment.
- **The steady state is the default, not an option.** A run is 128 lanes × 3
  segments × 32 decisions with the producer's warmup, so the first decision's
  5 s setup cannot be read as GPU demand. The tool warns when an artifact is
  shorter than 16 decisions or was written with `--no-warmup-forward` (session
  34's 65–82 % one-time cost), and it does not treat a non-zero producer *gate*
  exit as a throughput failure: a shape that never acts still times the loop.
- **The rule for the sweep:** rent vCPUs up to `G × E / g`, then GPUs. Below
  that crossover the GPU is idle; above it, extra vCPUs queue. At the current
  packet the A6000 crossover is ~4.1 processes, so a 4-vCPU + A6000 box is
  balanced, a 6-vCPU box is GPU-bound, and an 8-vCPU box needs MPS or a batched
  decision server (or a lower `g`) before its last cores can pay.

See the session-35 section below and `ROADMAP_CRFORGE.md` §4.

### 2026-11-16 — the 157 ms is one forward field; "port ever_server" is four inference tricks

Two different things, separated so the next session does not conflate them:

- **157 ms is `ms.inference_ms`, not the packet.** In the recorded A6000
  128-lane × 2-seat run the per-decision buckets are tensorize 81, **inference
  157**, decode 97, step 75, facts 9 and ~228 unattributed (the mean-median
  residual, §3.67) = 647 ms. The field is the model forward **plus its per-call
  overhead**, measured per decision round.
- **157 → 21.5 ms is 7.3× and it is the physical $20/1M target.** At $0.35/h,
  $20 per 1M **physical** matches (the 2,428 convention) needs a 21.7 ms
  forward; 21.5 ms is the rounded 7.3× target. Under the 1,214 convention the
  same $20 is **43.4 ms (3.6×)**. Quote the convention or the target moves 2×.
- **The other ~490 ms is separate CPU work and a second gate.** tensorize 81 +
  decode 97 + engine steps 75 + facts 9 + ~228 residual. A 21.5 ms forward
  gives a ~9.7 matches/s (1,214) GPU ceiling, but at 6 vCPU the CPU half caps
  it at **~2.5 matches/s** on the recorded box (~4.3 on the live faster CPU).
  Six vCPUs feed the faster card only if the CPU half falls to **~108 ms**
  (`T = N_proc × F`) or `N_proc` rises to ~14 on the live half (~24 on the
  recorded one). The forward port is necessary, not sufficient.
- **The port is four inference tricks, not the server.** Source
  `.resource/RoyaleFish/royalefish/ever_server.py` (with `ever_packed.py` and
  `net.py`); target `tools/crforge/self_play.py` → `bridge_session.py` →
  `PolicySessionV4`, the in-process forward. Take: **fp16 with an fp32
  action-equality check**, **CUDA graphs at several batch sizes**, **one
  GPU-side LSTM state table**, and **layout/pack computed once and reused**.
  Discard `net.py`'s TCP/pickle wire protocol: crforge has no separate GPU
  process. Sparse trajectories, row-packed updates and the loader are the
  separate training-side `ever_*` item.
- **Gate:** fp16/graph replay reproduces the fp32 sampled actions and log-probs
  on the same batch, and the artifact reports `ms.inference_ms` at 128 lanes
  **with the CPU half beside it**. A lower forward with a flat CPU half is not a
  throughput win.

See `ROADMAP_CRFORGE.md` §4 for the bucket table and the `T = N_proc × F`
arithmetic.

### 2026-11-19 — the horizon's return is a value fit, not a score; the A6000's 228 ms was a statistic

Session 33 asked for the reward share before any cadence is ranked on the
simulator's return, and session 32 left the A6000's ~228 ms/decision
unattributed.  Both are measured, and the second is not work at all.

- **The share is 0.2–0.8 % of a typical update.**  Per update,
  `(reward_abs_sum / rows) / |return_mean|`: the six 50-update runs have median
  shares **1.7e-3 … 8.1e-3** (3-seed means 4.7e-3 `reset`, 4.3e-3 `carry`), and
  only **5 of 300 updates** reach the 50 % ranking threshold — every one a
  terminal.  `reward_abs_max` is ~1e-3 typically and 0.83–0.94 at the terminals.
- **The best-return run never took a tower.**  `carry` seed 1 — the one-seed
  leader at 0.5803 — has **no terminal, no truncation, and no reward above
  0.0033** in 1,200 decisions per lane (about one match).  A score cannot rank
  the run with no game result first; the return is the boundary value fit.
- **Longer windows do not rescue it.**  The 24/60/120-decision probe rises
  **3.1×** from 24 to 60 decisions (median share 8.2e-3 → 2.6e-2) and then
  **stops** (2.5e-2 at 120, 0.97×).  Even a 120-decision window, which at 16
  updates is longer than a whole match, keeps the typical update ~20× below the
  50 % bar and reward-dominated in only **19 %** of updates.  The return is a
  **value-fit diagnostic**, not a score.
- **The 228 ms is a mean-median mismatch, not missing work.**  Every timing
  bucket is a median and the wall is a mean.  One cold first decision costs
  **4.9–5.5 s at every lane count from 8 to 128**; at 32 decisions that same
  setup amortises to the **227 ms** that was called unattributed.
  Two 128-lane shapes solve **F ≈ 4.0 s + 523 ms/decision = 490 ld/s** steady
  state against the recorded 395.8 (1.24×).  All six 8-decision scaling runs are
  **65–82 % one-time cost**, so their GPU/laptop rates — including "0.34× at
  8 lanes" — are not steady-state results and must be re-measured.
- **Decision:** `reset` remains the training default because it is the
  seed-stable cadence, **not** because its simulator return is higher.  Do not
  rank a cadence on the return; native parity (50–150 matches every N updates)
  is unchanged and is still the only promotion gate.  A simulator-side learning
  signal must observe the game — terminal/crown/tower rate over windows that
  contain episodes — not the GAE return.  The A6000 scaling and overlapped-loop
  gates are re-run with `--warmup-forward` before they size anything.
- **Landed:** horizon reward-share fields; `tools/crforge/reward_share.py`;
  `tools/crforge/timing_accounting.py`; producer `ms_mean`/`ms_max`/`ms_first`
  and residual accounting; `--warmup-forward` (default) primes the sampled and
  decoded path and restores the torch/numpy/CUDA RNG, `--no-warmup-forward`
  reproduces the old timings.  Bridge suite **105 passed**; crforge trainer
  **13 passed**; ruff clean.

See the session-34 section below and `FINDINGS.md` §3.67.

### 2026-11-19 — the overlapped loop is correct; on CPU it is not a training default

`--overlap-update` collects segment `k+1` while segment `k` updates, on a second
model in a worker thread. The mechanics are proven and gated — the collector's
weights are refused if they moved during a segment, and every boundary copy is
verified byte-for-byte — but the CPU trade is bad: **103.8 s serial vs 100.7 s
overlapped (1.03×), collection 29.2 → 18.9 lane-decisions/s (0.65×), and 5 of 6
updates stale** (|KL| **4.4e-3** against 6.2e-5 on-policy, clip **2.9 %**).

- **Why the gain is ~3 %:** the per-decision forward and the PPO backward are both
  CPU-bound on this host, so the worker steals cores from the collector. Both
  halves inflate (collection 1.55×, update 1.39×) and the update is paid for in
  contention instead of hidden. `tools/crforge/overlap_compare.py` therefore gates
  on the collection rate, not just the wall, and this run **fails** it.
- **The tax is the wave's tax, measured directly.** The trainer now records the
  sampling id against the id the update actually started from, so a pipelined
  stale update is a number rather than an inference (the old id-chain classifier
  could not see it). A 3 % wall gain does not buy 71× the on-policy KL.
- **Decision:** `reset` remains the default trainer, `carry` the deploy-shape
  candidate, `wave` a diagnostic, and the pipeline a **gated option**, not a
  default. It is not deleted: this host is the wrong substrate. On the A6000 the
  update is the *short* side (5.5 s against ~39 s of collection at the gate
  shape, §3.62), so the collector's forward is not competing for the CPU cores a
  backward uses. The ceiling is bounded arithmetic (~27 % at 128 lanes × 32
  decisions if the update scales linearly), and the 2,048-frame OOM in §3.62 has
  to be solved before it is reachable. That measurement is open.

See the session-32 section below and `FINDINGS.md` §3.64.

### 2026-11-19 — the one-seed `carry` lead does not survive two more seeds

Session 32 asked for ≥2 more seeds before ranking `reset` against `carry`; seeds 2
and 3 are in, and the ranking reverses. Same shape, checkpoint and 1e-5,
`tools/crforge/learning_horizon.py`: 4 lanes × 50 segments × 24 decisions, CPU,
`OMP_NUM_THREADS=4`, `runs/crforge-c4/learning-horizon.json`.

| last-10 return | `reset` | `carry` | delta |
| --- | ---: | ---: | ---: |
| seed 1 | 0.4951 | **0.5803** | +0.0852 |
| seed 2 | **0.4888** | 0.3411 | −0.1477 |
| seed 3 | **0.4640** | 0.3062 | −0.1578 |
| 3-seed mean | **0.4827** | 0.4092 | **−0.0734** |

- **`carry` wins 1 of 3 seeds, `reset` 2 of 3, and the seed is not a nuisance
  parameter here.** Seed 1's +0.085 was the outlier: `carry`'s last-10 return
  spans 0.306–0.580 across seeds (range 0.274) against `reset`'s 0.464–0.495
  (range 0.031). One seed cannot rank a cadence with that spread — which is what
  session 32 refused to do, and this is the measurement that says it was right.
- **Both still rise in every run.** Per-run delta is positive in all six (reset
  +0.267 / +0.227 / +0.193; carry +0.354 / +0.090 / +0.080), but `carry`'s slope
  collapses at seeds 2–3 (1.87e-3 and 1.36e-3 per update against reset's 4.79e-3
  and 4.47e-3). ACT does not separate them either: 3-seed means 8.4 % vs 9.3 %,
  and the per-seed order flips.
- **What the trace can and cannot say.** All 300 updates (6 runs × 50) are
  on-policy and pass the training gate; walls are equal (886.8 vs 877.6 s). The
  trace's `reward_sum` is identically zero by construction — the per-seat reward
  is antisymmetric, so it cancels — but the run's own segment summaries show
  **48–49 of every 50 updates with a nonzero reward**, so the return curve is
  not rewardless. A repeat of reset/seed 2 with the new per-update
  `reward_abs_max` trace reproduced the same curve exactly (0.2619 → 0.4888) and
  measured it: 48/50 updates nonzero, median max |reward| 1.0e-3, seven updates
  at ≥ 0.05 (a princess tower's worth), and the two terminals at 0.90/0.92. So
  reward is present but small in most 24-decision windows, and the boundary
  bootstrap still dominates the return. This ranks a simulator return, not a
  win rate; native parity remains the only promotion gate.
- **Decision:** `reset` stays the training default. `carry` is **demoted from
  "leading candidate" to unresolved**: it is the deployed inference shape and
  still worth measuring, but not with this shape. The one-seed result must not be
  cited as evidence for it. The next horizon must report the reward share per
  update (it is non-zero but small here), use longer segments only if that share
  is too small to rank, run on ≥3 seeds, and keep native parity every N updates.

See the session-33 section below and `FINDINGS.md` §3.66.

### 2026-11-19 — `carry` leads the 50-update horizon; the default waits for more seeds

The cadence question session 31 deferred is now measurable: 50 updates at one
shape and seed, both on-policy, `tools/crforge/learning_horizon.py`.

- **Both cadences learn the simulator's return.** `reset` 0.228 → 0.495,
  `carry` 0.226 → **0.580**. `carry` leads on the last-10 mean (+0.085), the
  slope (+2.2e-3/update), last-5 (0.622 vs 0.511) and best-5, at the same wall.
  Its ACT rate also more than doubles (4.2 % → 12.0 %) against `reset`'s 7.4 %.
- **The first update is identical in both runs** (return 0.330, KL 7.5e-5): the
  only difference is where the recurrence is cut, which is the control this
  comparison needs.
- **What this is not:** a win-rate claim, a native result, or a ranking. It is one
  seed and one 4-lane match per run; `value_explained_variance` is unusable at 8
  rows × 24 decisions (a low-variance segment sent it to −11), and native parity
  was not run.
- **Decision:** `reset` stays the training default. `carry` is the leading
  candidate and the subject of the next horizon run: ≥2 more seeds, then 50–150
  native matches every N updates. Do not flip the default from one seed.
- **Updated 2026-11-19 (session 33):** seeds 2 and 3 reverse this ranking
  (`reset` 0.4827 vs `carry` 0.4092 over three seeds; `carry` wins 1/3, range
  0.274 against reset's 0.031). The bullet above is kept as the one-seed record;
  the three-seed result is the decision entry above and `FINDINGS.md` §3.66.

See the session-32 section below and `FINDINGS.md` §3.65.

### 2026-11-19 — the cadence decision: `reset` trains, `wave` is a diagnostic

The update-boundary policy was the last open choice from session 29, and its
evidence was a wave run and a reset run at **different shapes** (48 vs 64
decisions), which cannot be compared. `tools/crforge/cadence_compare.py` now runs
all three policies at one shape, checkpoint and seed list and classifies every
update as **on-policy** (the weights that sampled its rollout are the weights the
update starts from) or **stale** (an earlier update in the same wave already
replaced them).

- **Measured tax:** 4 lanes × 6 segments × 24 decisions × 2 seeds. Wave's three
  stale updates have |KL| **5.4e-3** (160× the 3.4e-5 on-policy mean) and clip
  **3.0 %** against **0** on-policy; per seed |KL| 1.0e-2 / 5.9e-4 and clip 1.9 %
  / 4.2 %, never 0. The run's first update is identical to reset's and
  carry's first update at the same seed — the control that the classifier is
  right.
- **No throughput or reuse gain:** all three run 6 updates for 6 segments and
  ~106 s (collection ~40 s, update ~66 s). `updates_per_sampling_id` is 2.0 for
  wave against 1.0, which is data age, not reuse.
- **Decision:** `reset` stays the training default; `carry` stays the
  deployment-shape candidate and is ranked on a learning horizon later (its
  updates are on-policy and it did not clip); `wave` is a **diagnostic**, not a
  trainer. The only cadence change with a wall payoff is collecting the next wave
  while the previous one updates, which needs a frozen weights copy and a worker
  thread — a throughput change, not a state policy.
- **Caveat:** the 8-lane × 40-decision shape put this 14 GB host into swap and
  killed the session twice; the recorded comparison is 4 lanes × 6 × 24 at
  `OMP_NUM_THREADS=4`. KL/clip are fractions, so the classification holds; the
  walls are this host's.

See the session-31 section below and `FINDINGS.md` §3.63.

### 2026-11-19 — the A6000 measures the producer, not the model

One hour on a rented RTX A6000 (48 GB, 6 vCPU, $0.35/h) ran the C4 loop on CUDA
for the first time. The GPU works and is mostly idle: **the per-decision Python
path is the ceiling, and the C4 gate shape is the wrong shape for a GPU.**

- **Three defects the CPU hid, all fixed:** the producer never passed
  `--no-validate` into `sample_for_ppo_rollout`, so the model's contract
  validation ran every decision (on GPU every check syncs: 372.9 → **88.5 ms** at
  16 rows); the timing buckets around the forward did not
  `torch.cuda.synchronize()`, so the forward's cost was reported as decode; and
  `CrforgeRolloutStore` built its masks and rewards on CPU while values/log-prob
  came from CUDA, so the first GPU update raised a device mismatch. A fourth,
  found on the box: **cuDNN refuses an RNN backward in `eval()` mode**, so the
  trainer now runs each update in `train()` and restores the collection mode.
- **The forward is flat in rows on GPU** (88.5 / 95.8 / 92.2 / 107.0 / 141.0 ms
  at 16 / 32 / 64 / 128 / 256 rows) and ~linear on the laptop. End-to-end
  lane-decisions/s: **0.34× the laptop at 8 lanes, 1.06× at 32, 2.15× at 64**,
  and **395.8/s at 128 lanes × 32 decisions** (0.33 matches/s), because the box's
  six vCPUs run the CPU half 2–13× slower than this laptop.
- **The update is where the GPU pays: 5.5–6.1 s against 44.9 s (7.5×)** at the
  1,024-frame gate shape, gate pass, checkpoint step 277 reload-identical, and
  `--resume` continues it to step 279 with the moments restored.
- **A 48 GB card OOMs** on a 64-row × 32-decision (2,048-frame) update with the
  default `--ppo-lane-minibatch 0` / `--ppo-sequence-chunk 32`: minibatch lanes
  and shorten the chunk before scaling rows.
- **Revises the plan of record:** the roadmap's "GPU-saturated ~51 matches/s"
  is not reachable from this producer (~2 matches/s even with six processes).
  One process at 64–128 lanes is the shape; the lever is the per-decision
  tensorize/decode/step path and CUDA graphs, not a bigger card.

See `FINDINGS.md` §3.62 for the tables.

### 2026-11-19 — a C4 run resumes from its own checkpoint

`--resume` makes a saved crforge checkpoint a starting point, not just an
artifact: `crforge_bridge/training.py` gains `optimizer_signature` (hashes
AdamW's settings *and* every moment tensor), `CrforgeResumeState` and
`load_resume_state`; the producer gains `--resume` (with `--ppo-update`) and
records a `training.resume` block.

- **The optimizer state is not optional.** A checkpoint with no
  `optimizer_state_dict`, no `crforge.loop` block, or weights that disagree with
  the digest it recorded is **refused** — a restart of Adam with stale momentum
  is a different run, not a continuation. The seed `B-general-v275` is refused
  by name.
- **The step counter had to be rebased.** Seeding the loop's `updates` count and
  keeping `update_step = source + updates` double-counts the previous run; the
  save now writes `base_update_step + updates`, and a resume rebases
  `base_update_step` to `saved_step - prior_updates`. A resumed run that started
  at 275 with 2 updates, resumed, and ran 2 more saves step **279** with
  `resume.update_step_continued: true`.
- **The gate counts only this process's updates.** `training.updates` is
  cumulative; `training.updates_this_run` is what `two_consecutive_updates`
  reads, so a resume that runs one update does not pass on the previous run's
  work.
- **The cadence must be stated again and must match.** `--update-state-policy`
  and `--wave-segments` come from the CLI, so a resume whose recorded policy
  differs is refused instead of silently changing when the optimizer steps.
- **Measured:** 4 lanes × 2 seats × 2 segments × 24 decisions, source
  **275 → 277** (gate pass, checkpoint reload-identical), resume
  **277 → 279**, 135 MB of Adam moments restored, both updates finite, 0
  rejected. Evidence: `runs/crforge-c4/self-play-resume-source.json`,
  `-resume.json`; `FINDINGS.md` §3.61.
- **Still open:** CUDA/MPS throughput, and the wave/update cadence decision,
  which needs the GPU box's throughput numbers first.

### 2026-11-19 — the C4 producer is a training loop: one optimizer, a stated state policy

`crforge_bridge/training.py` replaces the per-segment gate update with a real
loop, and `tools/crforge/self_play.py --ppo-update` drives it:

- **One AdamW for the whole run.** It is created once, steps in place, and its
  moments survive every segment and wave. The seed checkpoint
  (`B-general-v275`) carries no optimizer state, so a fresh Adam is the honest
  start; `--save-checkpoint` writes the optimizer and the loop counters with the
  weights.
- **The update boundary is a flag, not an accident.** `--update-state-policy`
  is `reset` (default: zero the recurrent state after each update — the LSTM
  never holds state from weights it no longer has), `carry` (keep it; the
  deployed inference shape and what session 28 measured), or `wave` (freeze the
  behaviour weights for `--wave-segments` segments, update at the wave end, then
  reset — the native collect-then-update cadence).
- **The learning rate is the native one.** The first loop default was `1e-4`,
  ten times the native trainer's `1e-5`; four updates at that rate collapsed ACT
  from 45 to 0 of ~1,000 eligible lane-decisions per segment. At `1e-5` the ACT
  rate stays at the seed policy's level. AdamW `eps 1e-5`, weight decay 0, clip
  0.1, value coefficient 0.5, entropy 0, as the native path.
- **The gate is two consecutive finite updates.** `training.trace` records
  reward/return/value/KL/clip/gradient per update plus the behaviour hash before
  and after, and `training.pass` needs `updates >= 2`, every row finite, and the
  saved checkpoint to reload to the same weight bytes
  (`distinct_behaviour_ids` shows the policy moved). A partial wave at the run
  end is flushed and recorded.
- **The artifact is `crforge-self-play.v3`.** Per-segment `updates` replace the
  singular `update`, and a top-level `training` block carries the config, the
  trace, the behaviour ids and the checkpoint record. `rollout.update` is the
  aggregate the old gate read.
- **Still open:** CUDA/MPS throughput (this host is CPU-only), a resumed run
  (the checkpoint has the optimizer state; the loader path is not wired yet) and
  the native parity cadence.

See the session-29 section below and `FINDINGS.md` §3.60.

### 2026-11-19 — C4's rollout can be updated, including a mid-segment autoreset

`distributed_ppo_update_v4` no longer requires one episode per recurrent lane.
It splits the time axis at every row where any lane begins a new episode
(`_episode_segments`) and chunks each contiguous slice separately (`_episode_chunks`),
so the fused LSTM replay never sees a reset after step 0; lanes that did not reset
keep their carried state across the split. The GAE already stopped its trace at the
truncation. The producer now runs the update itself (`--ppo-update`): each finalized
segment goes straight into the same `StoredRolloutV4` the native path consumes.

- **Gate-shape update green:** `runs/crforge-c4/self-play-update.json` — 8 lanes ×
  64 decisions, **45 ACT / 983 eligible (4.58 %)**, **0 rejected**, one update over
  **1,024 frames** (64 × 16), `approx_kl 2.9e-5`, `clip_fraction 0`, gradients finite.
- **Multi-episode lane green:** `runs/crforge-c4/self-play-update-rollout.json` —
  8 lanes × 2 × 60 with `--max-ticks 600`: **1,920 lane-decisions**, **0 rejected**,
  **16 mid-rollout `episode_start` rows** (8 lanes × 2 seats) and the matching 16
  truncations in segment 1; both segment updates pass (960 frames each), GAE finite.
- **A defect the update exposed:** the store kept `detach()`/`reshape()` of tensors
  sampled under `inference_mode`, which are still inference tensors; PPO backward
  refused them (`Inference tensors cannot be saved for backward`). The store now
  clones them out of inference mode, with a regression test.
- **Behaviour identity is now the weights, not the checkpoint file.** Each segment
  binds a hash of the parameters/buffers it sampled with; the autoreset run records
  two ids (`3835a22f…` then `899ef703…`), so the second update cannot silently
  replay the first policy's behaviour.
- **Pins:** `--ppo-update` creates a fresh AdamW per segment (a gate, not the
  training loop, so momentum does not span segments), and it updates between
  segments while the producer carries the recurrent state across them — a real
  trainer has to choose that boundary policy explicitly. The negative
  `value_explained_variance` on segment 1 is recorded, not hidden: one epoch is a
  mechanics gate, not a learning claim.
- **Still open:** CUDA/MPS throughput, the native parity cadence, and a real
  training loop (persistent optimizer, reset-or-carry state policy, reward/eval
  curve). CUDA remains impossible on this host (CPU-only torch, no `nvidia-smi`).

See the session-28 section below and `FINDINGS.md` §3.59.

### 2026-11-19 — C4 stores a rollout, and the reward is the native reward

`crforge_bridge/rollout.py` turns `tools/crforge/self_play.py` from a probe into
a collector: every decision stores the stacked observation, the sampled action,
the behaviour log-probability, the behaviour value and a reward, and each
segment finalizes into the shared `StoredRolloutV4` — the same GAE
(`generalized_advantage_estimate`) and validation the native PPO path uses.
Storing is the default; `--no-rollout` keeps the throughput probe.

- **The reward is `BattleEnvV1._rewards` rebuilt from the facts:** the terminal
  result plus potential-based shaping over crowns and tower damage
  (`shaping_beta 0.05`, `gamma_per_decision 0.9997`). It is gated against the
  native `_potential`/`_rewards` on the same towers and against the engine's own
  terminal reward on every ended lane (**0 mismatches** in the gate run). A
  time-limit cut stores no shaping term, because the engine has already
  autoreset the lane.
- **Gate shape green with storage on:** 8 × 10 × 64 = **10,240 lane-decisions**,
  **429 ACT / 9,877 eligible (4.34 %)**, **0 rejected**, **37.8
  lane-decisions/s** (39.0 probe-only), forward still 94 % of the packet.
  Storage costs ~3.2 ms/decision and 57 MiB per 64-decision segment (16 rows,
  fp16), 573 MiB for the ten-segment run.
- **Autoreset lanes are stored correctly:** the 4-lane `--max-ticks 600` run
  stores 4 autoresets as 8 mid-rollout truncations and 8 `episode_start` rows at
  the following decision, with finite GAE.
- **The second micro-action is exercised by a run, not just a unit test:**
  `--force-second-micro` appends a *different* legal play at the sampled play's
  own delay bin — **22 forced, 18 shifted, 4 dropped, 0 illegal**. The first
  version duplicated the sampled play and the engine refused it: the action's
  slot was closed at the next tick (the queued play, or a tile the play
  invalidated). The probe now picks another hand slot and a tile its own
  placement grid opens.
- **Open at the time (closed in session 28):** `distributed_ppo_update_v4`
  required one episode per lane, so a segment that autoresets a lane could not
  feed it yet; the update now splits at episode boundaries (see the entry above).
  CUDA/MPS throughput and the native parity cadence are unchanged.

See the session-27 section below and `FINDINGS.md` §3.58.

### 2026-11-19 — upstream PR #12 (visual decks, exact offline duels) merged

`FirstLight_CR#12` (SeiShonagon520) is in `main` as `f5d9485`. What it changes
for us:

- **Exact offline AI duels.** `cr_native_env.queue_hand_action_next_tick` now
  resumes the renderer only if it found it running, and the duel loop enables
  `synchronize_native_render_steps`, so both policies decide on the same
  observation boundary and each round advances exactly five ticks instead of
  following wall-clock inference. A model play is queued on the frozen clock
  rather than through the pause/resume helper.
- **Duel diagnostics.** The decision log gains full decision ms and elixir, the
  result gains rejected-action counts and mean decision ms, and a failed duel
  writes `result.json` with the error instead of only a traceback.
- **Model warmup in duels** (`policy_session.warmup`): kernels are primed during
  the paused setup, then the recurrent state and action history are reset.
- **Deck UI and art.** A visual deck picker (`visual_deck.py`, bundled card
  images, 7.5 MB), duel-mode configuration (`duel_modes.py`), and searchable card
  library in `user_interface.py`. Not used by training; harmless.
- **`deck_roles_from_form_availability`** projects a deck that exceeds the V4
  role contract (≤2 heroes, ≤2 evolutions, ≤3 total) instead of raising, and
  warns. The locked learner deck is inside the contract (one hero, two
  evolutions), so training inputs are unchanged; special modes are marked
  experimental.

Conflicts (2) were resolved to keep both sides: our `HandActionRejected` →
`action_rejected` event wraps the PR's synchronized-step branch in
`battle_env.py`, and our ruleset-manifest/combat-ring gate stays beside the PR's
explicit `mode_timeline` in `offline_duel.py`.

**Unchanged blocker:** native-render on 16.635.1 still SIGSEGVs in the render
gate (§3.31), so `offline_agent`/`offline_duel` remain headless-only here until
that is fixed; this PR is the right code for when it is. The headless
evaluator, PPO and crforge paths are untouched. Validated: 106 native tests
across the touched modules + the 66-test crforge suite.

### 2026-11-19 — C4's producer loop runs; the forward is the whole packet

`tools/crforge/self_play.py` is the first crforge self-play producer: one
`VectorBattle` of 8 lanes, both actor seats in **one** recurrent forward, both
seats' sampled actions decoded back into engine indices, and the engine stepped
**one tick at a time** so a second micro-action keeps its own delay bin
(`plan_window_ticks` in `crforge_bridge/vector_actions.py`). The C4 gate shape
passes — 10,240 lane-decisions, **429/9,877 eligible lanes acted (4.34 %)**,
**0 illegal, 0 faults, 0 rejected**, **39.0 lane-decisions/s**, 97.6
env-steps/s on CPU (`runs/crforge-c4/self-play.json`, `FINDINGS.md` §3.57).

The measurement that decides the next step: the **forward is 359.7 ms of a
~410 ms decision (88 % of wall, 94 % of the timed packet)**; facts 1.7,
tensorize 13.4, both decodes 3.2, five engine steps 6.4. The bridge half is
done; C4's remaining work is GPU throughput and rollout storage/PPO, not more
CPU tuning.

Two defects were found by running it, both recorded: the warmup loop's
`np.min(initial=0)` (which seeds the reduction and pinned the minimum to zero,
so the loop stepped ~57 matches) and the previous-action record being an
inference tensor (so `reset_rows` and a future PPO write raised outside
`inference_mode`). The per-lane tick clock and `VectorTensorizer.reset_rows`
landed as the autoreset plumbing. See the session-26 section below.

### 2026-11-19 — C4's action path is closed, and there is no producer yet

The model's sampled `ActionSequenceV4` now decodes straight into the vector
engine's flat action index for both seats, in one NumPy pass:
`crforge_bridge/vector_actions.py`, gated at **3,008/3,008 lane-decisions**
against the stock per-lane decoder, **0.000232 ms/lane-decision**, **0 engine
rejects** (`runs/crforge-c4/vector-decode.json`, `FINDINGS.md` §3.56). Frames:
model → game follows the tensorizer perspective, game → engine follows
`Schema.toSeatX/toSeatY`; the composition is one x flip for both seats.

**This also corrects session 24's next-session note.** There is no crforge
producer to wire the stacked tensorizer into: `producer.py` is the **IL**
producer over native `ObservationV1` lanes, and the PPO worker is native too. C4
needs a new crforge self-play loop, and the two halves it needs are now gated —
stacked tensorize (C3-B) and stacked decode. See the session-25 section below.

### 2026-11-19 — the game-frame fix is closed; both actor seats pass the digest gate

The native model consumes **game** coordinates; crforge fed the **seat frame**,
so every crforge board was horizontally mirrored relative to the native path.
The conversion now happens once per layer: the facts layer writes game
coordinates, the bridge converts entity rows at one point, each seat's pending
point and placement mask are converted once, and every row builder is
actor-seat aware. The old evidence and the new proof:

| path | play | observed unit |
| --- | --- | --- |
| native x86_64 16.635.1 | Skeletons at game `(6500, 10500)` | **(6499, 11079)**, (5997, 10212), (6998, 10212) |
| crforge before the fix | same play | `18000 - x` (the seat frame) |
| crforge after the fix | same play | **the same three game points** |

The engine's internal action/mask space stays seat-framed (the play was
addressed with seat cell `(11, 10)`), while the facts-layer pending point is
game-framed (`pending_x = 6499`). `tools/crforge/frame_crosscheck.py` runs that
comparison against a live engine and records it in
`runs/crforge-c3/frame-crosscheck.json` (**status pass**); the result is repeated
in `FINDINGS.md` §3.55.

`batched_tensorize.py --seats 0 1` gates both owners of every lane against their
own stock tensorizer: **3,008/3,008 lane-decisions digest-identical** (1,504 per
seat), **0.0811 ms/lane-decision** stacked vs 0.605 per lane (**7.46×**),
**0.1029 ms/lane-decision end to end** (~9,719 decisions/s); seed 2 reproduces
it at **3,008/3,008**, 0.0787, 8.50×, 0.0992. The seat-1 gate found and fixed
two defects: relation tower tokens now resolve through the tower model-row map,
and group mean velocity reproduces the stock accumulator's signed zero.
**C4 is unblocked.** Evidence: `runs/crforge-c3/batched-tensorize.json`,
`-seed2.json`, `FINDINGS.md` §3.55.

### 2026-11-19 — C3-B closed: the batched packet is 7–8.5× the stock tensorizer

The facts-only path produces the whole `UniversalSemanticBatchV4` for every
lane: `crforge_bridge/vector_tensorize.py` (stacked record + batched previous
action), gated by `tools/crforge/batched_tensorize.py` on 256 learner-deck lanes
× 40 decisions, now for **both actor seats**. **3,008/3,008 lane-decisions are
`batch_digest`-identical** to the per-lane tensorizer, field by field, with
sampled actions recorded on both paths. Combined timing per lane-decision:
**facts 0.0160 (0.0320 ms/lane, shared by both actors) + tensorize 0.0811 +
record 0.0058 = 0.1029 ms** — ~9,719 decisions/s per process, under C1's
0.5 ms/lane gate and a **7.46×** speedup on the tensorizer half (seed 2:
8.50×, 0.0992 ms). The original
single-seat C3-B record was **1,504/1,504** at 0.118 ms (7.75×).
Evidence: `runs/crforge-c3/batched-tensorize.json`, `-seed2.json`,
`FINDINGS.md` §3.54–§3.55.

### 2026-11-15 — crforge is the bulk trainer; the native engine is the gate

The native path is not the throughput path, and the reason is the **Python
per-packet data path**, not the engine: one lane is ~8–10 ms
(`step` 8.7 = engine round 3.1 + observe 4.0 + masks 1.6, plus tensorize), which
is **0.082 matches/s per process** and **4 % GPU utilisation** on the A6000. The
engine, the loop and the model are done; the data path is the lever.

crforge replaces the engine round with a share of one 256-lane JVM call and
returns observations zero-copy as NumPy. Measured today on this laptop
(`crforge_env.bench`, ladder, 16.635.8 tables, load ~11 — a floor):

| threads (256 lanes) | decisions/s | matches/s |
|---:|---:|---:|
| 1 | 3,154 | 2.6 |
| 4 | 7,304 | 6.0 |
| 8 | 15,791 | 13.0 |

That is **4–21× the native raw channel per process** (0.61 matches/s) and
**32–159× the native training floor** (0.082) before the observation adapter is
counted. The recorded M4 run is 18.5 at 1 thread and 52.5 at 4 (30–86× raw). So:
**bulk-train on crforge, promote on native.**

**But the simulator is not the whole win.** A straight per-lane port of the
existing observation builder is ~1.4× (≈7 ms/lane). The 10–30× exists only if the
`ObservationV1`/mask/tensorizer build is vectorized across the 256-lane batch:
**≤0.5 ms/lane is ≤128 ms per batch** (≈2,000 decisions/s); at 1 ms/lane it is
1,000 decisions/s. Today's bridge encoder is ~13 ms **per lane** → ~77
decisions/s — slower than the native floor.

So the plan of record is gated on one measurement (C1), and it does not delete or
idle the engine:

1. **C0 (1–2 d, $0):** close the last MiniPekka `26000018` reject; 20-game run,
   `illegal: 0`, `truncated: 0`.
2. **C1 (1 d, $0):** 256-lane encoder spike, per-lane vs vectorized. Gate:
   **≥1,000 decisions/s per process** (≤1 ms/lane at 256 lanes); target
   ≤0.5 ms/lane for the full 10–30×. If it misses twice, fix the encoder; do not
   add lanes or GPUs.
3. **C2–C4 (3–4 w, ~$2):** resident adapter → vectorized observation → batch/PPO;
   gate ≥0.8 matches/s per process and a 10-segment run with 0 rejected.
4. **C5 (continuous):** 50–150 native matches per N updates and after every
   adapter change; a persistent win-rate gap freezes the checkpoint.
5. **C6–C7:** ≥150 native paired games, then a 20-game live canary on Null's.

Two hard rules: **never promote on crforge**, and **parity is continuous, not a
final exam**. The extracted engine stays warm as the gate and the deploy rig; the
A51 stays as the ARM64 oracle and deploy target. **Official servers are out of
scope.**

Full gates, stop conditions and fidelity pins:
[`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §0–§4.

### 2026-11-15 — learner deck: X-Bow 3.0; opponent pool varies

The learner plays one deck; variety comes from the **opponent pool**, not from
training a generalist. `B-general-v275` is the init and a frozen opponent.

Locked deck (highest-use X-Bow in `runs/deck-pool.json`, 2,983 uses):

```
x-bow · tesla-ev1 · archers-ev1 · knight-hero · skeletons · electro-spirit · fireball · the-log
tower: tower-princess (159000000)
```

**Support check: pass.** All eight are in the verified 16.635 release
(`card_support.json`, 123 cards) and in `crforge_catalog.json`; the forms exist
as `Archer_EV1`, `Tesla_EV1`, `Knight_hero`. The open item is the **tower troop**
(`SideSetup` at C2).

Opponent pool: frozen `B-general-v275` (deck-pool sampling), `A-hog26-v738`
(71.9 % ±2.6 / 1,147), mirror X-Bow, 2–3 historical learner checkpoints
(PFSP-weighted), and the hard decks (Mortar, Evo Witch, Minion Giant,
Golem/beatdown, Log Bait, Graveyard). Fixed levels, both seats, paired seeds.

**This does not change C0–C3** (deck-agnostic). Hog 2.6 is not the learner; it is
the published bar and the fallback canary.

**Data (see `ROADMAP.md` §6.1):** the 252k corpus already contains ~**5,700 X-Bow
games** (2.25 % of deck uses). Expert replays are a tier-2 input, in this order:
drills/reward mining (hours), then a BC warm-start (2–5k games) **only if** a
100–200-game native eval of `B-general-v275` on the locked X-Bow deck is **<40 %**;
always meta/opponent modelling. Mass scraping is not worth it.

### 2026-11-15 — RoyaleFish upstream (`c42108d`; README + media only since `da9c8cf`; superseded 2026-11-19 by `9179bc5`, see the top of this file)

- **The November 2026 Hog 2.6 replay set is cancelled at the source.** RoyaleAPI
  closed replay access for everyone on **2026-11-16** (Discord login plus a
  hand-reviewed request form, no stated turnaround). RoyaleFish's collector stopped
  at a few hundred games and the Hugging Face upload is **postponed with no date**.
  Treat the set as unavailable: no tier-2 Hog IL anchor and no meta snapshot from
  it, and an X-Bow collector is blocked by the same wall. The local 252k corpus is
  unaffected.
- **A v1881 learned defense from real human attacks.** 52,632 human attacks replayed
  card-for-card as "ghosts", four attempts each, rewarded for beating its own
  earlier attempts; 2,666 ghosts held out. On those unseen attacks: defense score
  0.53 → **0.75**, princess towers lost per game 0.47 → **0.25**, fully clean
  defenses 11 % → **19 %**; ~1,900 updates and 99 % of the 17.5M weights changed.
  Open: it under-attacks and rarely commits the Hog, and its Fireball leads moving
  troops. **The checkpoint is not uploaded yet.** A template for the Hog opponent,
  not a change to the X-Bow learner.
- **No code, data, model or table change** in either commit, so the `data_16635/`
  catalogs and the `B-general-v275` / `A-hog26-v738` pins are unchanged. `Blade` is
  no longer mentioned in the README.
- **15.535 → 16.635 model converter is coming.** It remaps **card rows for the
  same architecture**, so it helps older V4-family checkpoints — not
  FirstLight-Zero's **X-Bow 3.0** (1.17M, mirror-only, 75-tensor custom
  architecture), which stays a reference/opponent. Verify forms + a native parity
  sample before trusting any conversion.
- **The league is no longer frozen.** RoyaleFish now co-trains a learning
  generalist B + older versions + 10 % mirror + focus decks + drills. A frozen
  opponent can be memorized; adopt this shape for X-Bow.
- **New models (not X-Bow):** A v1290 (Hog) uploading, **Frost** (Knight-EV1 /
  Hero Ice Wizard / Tesla-EV1 / Bats / Minion Giant / Skeletons / Barbarian
  Barrel / Fireball) in 1–2 days, anti-2.6 at ~81 %.
- **Throughput:** ~2,500–3,000 games/h on a 5060 Ti + V100, ~55k decisions from
  ~64 games per update; **NVIDIA MPS** nearly doubled games/h. That is the
  `ever_*` recipe working — include MPS in C4.

### 2026-11-14 — why the target changed

The previous plan optimized **engine throughput** by moving to the x86_64
`cr-native` 15.535.29 runtime. That was the right call for throughput, but it
optimized the wrong variable: **Null's runs 16.635.1 ARM64**, and the 15.535.29
x86 engine cannot connect to Null's ladder.

The repo already has the direct path:

- **FirstLight V4 General** — `checkpoints/General/checkpoint-step-00000460.pt`,
  12,684,339 params, `ppo-league-snapshot`, trained across decks.
- **IL_Replay** — `datasets/royaleapi/`, 252,238 replays / 17,836,160 actions,
  already local.
- **16.635.1 offline APK + cluster APK + probe** — `builds/cr-ai-16.635.1.apk`,
  `builds/cr-engine-cluster-16.635.1.apk`,
  `native_runner/probe/out/libcrprobe-16.635.1.so`.
- **A51 cluster/deploy tooling** — `deploy_a51.sh`, `launch_cluster_a51.sh`.
- **V4 IL/PPO stack** — `native_runner/training/v4/`.

**R0 is retired.** It is a third observation contract with no trained
checkpoint, no local corpus and no 16.x adapter. Its code/evidence is archived;
do not start another R0 run.

**Primary track:** 16.635.1 on the A51 with the V4 General model.
**Proxy lab:** cr-native 15.535.29 x86, for throughput/reward/parity only.

> **2026-11-14 (late): the x86_64 16.635.1 engine now runs on this host**
> through `.resource/Clashback` + `crx`, with a native probe port and scripted
> parity seeds 1–5 matching the A51 corpus. The A51 remains the ARM64
> deployment oracle. See §3.35 and "Next session" step 1.

> **2026-11-14 (session 2): first GPU PPO update ran; the A51 is the wrong
> training engine.** 1×A6000 (ThunderCompute) completed a full update with
> **0 rejected actions** and a saved checkpoint, but the learner only reached
> **19 lane-decisions/s (0.04 matches/s)**. Measured at the PPO 5-tick decision
> cadence, four A51 engines cap at **0.26 matches/s**, while **one x86_64
> process does 0.61 matches/s** and is CPU-bound. Two config bugs also blocked
> the gate: the flat `gate_temperature 0.2` drove `ACT` to 0 over 5,120 ticks,
> and the trainer hard-requires a 7,695/20 deck pool the local corpus cannot
> produce. The GPU box was deleted after the smoke (~$0.23). See
> "GPU smoke 2026-11-14 — results and what they mean".

> **2026-11-14 (session 4): the x86_64 PPO worker path works, and 16.635
> checkpoints now train through it.** The `Error -5 … truncated stream` decode
> failure was a **host-side zlib resolution bug** (`libz` preloaded
> `RTLD_LOCAL`, so crx's missing-symbol stub answered `compress2` with `Z_OK`
> and a zero-byte output). The engine spec now carries the checkpoint's
> `competitive_release` + `model_config`, so engine-node tensorizers build the
> 16.635 catalogs and the 21-column schema instead of the pinned 15.535/16-column
> default. `B-general-v275` collects through the CPU worker on one x86 engine
> with 0 UNKNOWN effects (29 lane-decisions/s, model-time bound). See
> `FINDINGS.md` §3.37. The next lever is still the learner loop's one-packet-per-
> forward batching, not the transport.

> **2026-11-14 (session 5): batching measured; host engines are multi-process.**
> The 20–40 ms window advice was wrong for the A51: a window shorter than one
> engine round-trip (~100–150 ms) re-batches the same two-engine cohort every
> time. At `CR_PPO_INFERENCE_BATCH_WAIT_MS=250`, four A51 engines coalesce
> **3.88 packets/forward and 33 forwards instead of 64**; CPU wall time stays
> flat (the 17.5M model is the local bottleneck), which is exactly the
> per-forward cost the 4 %-utilised GPU was idling on. `cr_probe_attach` now
> initialises engine identity, so `crx_engine_host.py --engine-index N` gives
> each x86 host process its own port (`26789 + N`) and control CPU — verified
> with two concurrent engines. The same session confirmed the §3.37 caveat:
> B-general at gate temperature **1.0** reproducibly trips a native action
> failure (env 5), while the same seeded run at **0.2** is clean. See
> `FINDINGS.md` §3.38. *(Superseded by session 6 below: the delay-aware port is
> in and gate 1.0 now collects cleanly in six segments.)*

> **2026-11-14 (session 6): delay-aware candidate legality is ported, and
> full-rate ACT collection passes at gate temperature 1.0.** `B-general-v275`
> declares `delay_aware_legality: true`, but the decoder and `BattleEnvV1` were
> still applying the 15.535 decision-tick rule. Both now check elixir at the
> touch delay bin (`max(e0, min(10, e0 + regen*tick)) - spent`) with the match
> multiplier, the environment exports `touch_window_elixir` /
> `affordable_from_bin`, and compound validation runs in touch order. Gates:
> **1×A51 8 lanes × 5 seeds: 5/5 segments**, **4×A51 8 lanes (the §3.38 failing
> shape): 28.5 lane-decisions/s, 1/1**, x86 1 engine: 23.9/s, 0 rejected and
> **3,979 native command parses with 0 failures**. One post-port failure
> (`env 6`) did not reproduce in six later segments and is not claimed fixed;
> `CR_PPO_FAILURE_DUMP` now captures the failing actions if it returns. See
> `FINDINGS.md` §3.39.

> **2026-11-14 (session 7): the batching window is a dead end; the closed loop is
> the critical path.** The combined config the GPU job would use — gate 1.0 +
> delay-aware + 250 ms — halves forwards (321 → 160) and reaches 3.98–4.00
> packets/forward, but is **net slower** than the 2 ms default (271 s vs
> 288–331 s): the added wait exceeds the saved per-forward cost because the CPU
> model is row-bound, not forward-bound. **Keep 2 ms.** On a GPU the forward is
> ~2 ms, so a fixed 250 ms window can only lose; the paid run's 416 ms/packet was
> ~390 ms of loop bookkeeping, not forward count. The 16.635 evaluator was also
> fixed (release scope for the ruleset manifest and `BattleEnvV1`, release-aware
> `runtime_scope` counts); seeds 7 and 21 ran `B-general-v275` vs
> `A-hog26-v738` to native terminals with 0 rejected. A **10-segment stress gate**
> at the chosen config is 10/10 clean (4,097 native parses, 0 failures, no
> `env 6`), so the GPU gate is unblocked. See `FINDINGS.md` §3.40.

> **2026-11-15 (session 8): the learner loop is pipelined, and the ladder's
> ceilings were measured against the wrong workload.** A single `ppo-inference`
> thread with one batch in flight now stages receives during the forward, and
> boundary bootstraps share that thread (10/10 segments, 20,480 owner frames,
> clean). With a cheap forward the non-model wall falls 14 %; with the real
> 17.5M checkpoint the loop is neutral, because the forward is 98 % of wall and
> both loops idle only 0.19 s/segment once warm. The important result is
> diagnostic: the per-packet floor is **32 ms native step + observation build,
> 14 ms tensorize, 6 ms channel**, so the raw channel "ceilings" in the old
> ladder (0.61/s per engine, 2.44/s for four) overstate the training rate ~7x —
> four engines measured 0.736 aggregate because one Python client decodes every
> lane. The gates (1x >=0.45/s, 8x >=2.5/s) are not reachable on this worker
> path; the next lever is the worker's per-packet cost, not the loop. See
> `FINDINGS.md` §3.41.

> **2026-11-15 (session 11): the training observation is lean, and contract
> machinery is gone from the policy path.** `policy_features_only` now builds
> every contract through a non-validating `construct_contract` inside a
> `lean_contracts()` scope: no validation, no `FrozenMapping` freeze walk, no
> provenance trees, no projectile-velocity rewrite. The hand runtime contract
> and native form aliases are cached per tick, and every observation cache is
> keyed by `(native identity, lean)` so validated callers never see a lean
> object. Paired A/B (8 alternating runs): **CPU 11.75 → 7.81 ms/packet
> (median −28…−34 %), 78 → 113 lane-packets/s**; `_entities` −60 %,
> `_extract_towers` −44 %, `_players` −55 %. At 8 lanes: 10.6/12.5 →
> 8.2/9.4 ms. The validated path is unchanged (parity seeds and
> evaluator default to `lean=False`), and a new tensorizer test asserts the lean
> observation produces bit-identical tensors. Next: fuse the rich frame straight
> into tensor rows — the remaining cost is the native round trip (~3 ms) and
> `_rich_object`/`_phase_object` binding, not contracts. See `FINDINGS.md`
> §3.43.

---

## Session 39 results (2026-11-17) — both tracks in parallel

**Read first:** the decision entry above, `FINDINGS.md` §3.72 and
`ROADMAP_CRFORGE.md` §4.

The session did what the handoff's bottom line asks: the pilot and the native
baseline are the work now, the CPU half moves while they run, and the box is not
rented on an arithmetic hope.

### What landed

- **`VectorGroupBuilder.build(..., live_slots=True)`** assembles only the live
  group/child slots and pads to the record's fixed capacity once.  The previous
  full-capacity form stays callable (`live_slots=False`) as the micro tool's
  oracle, so the A/B is one process on one batch.
- **`cpu_half_micro.py` measures the group build** (`group_build`:
  full-capacity vs live-slot ms, entity count, and a field-by-field equality
  assertion), beside the existing interleave, gather, mask and record A/Bs.
- **`test_live_width_padding_matches_full_capacity`** pins the padded tail,
  ranks and indices against the full-capacity build and the per-lane oracle on
  a partially filled board.

### Measured — the CPU half

| group build, 128 lanes × 2 seats (6 live entities/lane) | ms |
| --- | ---: |
| full capacity (48 groups / 160 children) | 7.57 |
| live slots | **2.68** |
| saved | **4.89** (2.82×) |
| `_entity_rows` (unchanged) | 1.16 |

The producer section mean across the before/after runs is **13.18 → 6.01
ms/seat**; the non-group sections are flat-to-slower in the after run, so the
change, not a quieter host, is the delta.  The digest gate is
`runs/crforge-c4/batched-tensorize-s39.json`: **3,008/3,008** lane-decisions,
both seats, **0 mismatches**, 5,196 recorded micro-actions, **0.0546
ms/lane-decision** against the 0.706 oracle (**12.95×**; session 37 was 0.0811
and 7.46×), C3's 0.5 ms/lane gate still passes.  Evidence:
`runs/crforge-c4/cpu-half-micro-s39.json`.

### Measured — the bounded pilot

`runs/crforge-c4/self-play-pilot-s39.json`, 4 lanes × 2 seats × 16 segments ×
24 decisions, CPU, `OMP_NUM_THREADS=4`, one AdamW, `reset`:

- **16/16 updates on-policy**, |KL| ≤ 8.6e-5, clip 0, all finite,
  `distinct_behaviour_ids 16`, training gate **pass**.
- **102 ACT / 2,953 eligible (3.45 %)**, **0 illegal, 0 rejected**, 3,072
  lane-decisions; collection **108.1 s** (28.4 lane-decisions/s), update
  **232.2 s**, wall 340.3 s.
- Checkpoint **step 291** (275 + 16), 203 MB, `reload_match: true`,
  weights digest `ef6edc74…`.  A native load resolves the same 16.635.1
  release and 16,874,349 parameters.
- Return 0.330 → 0.308 over 16 windows (last-5 0.293 vs first-5 0.226).  Every
  segment is a truncation and there is no terminal, so this is a value-fit
  trace, **not** a learning or win-rate claim.  It is a pilot: the loop trains,
  saves and reloads; the signal it can produce is still native.

### Measured — the native-parity baseline

`runs/native-parity-s39/baseline-smoke-2games/`: `B-general-v275` (policy A)
against `A-hog26-v738` (policy B), paired seeds, sampled actions (`--sample`),
x86_64 16.635.1 probe (`CR_NATIVE_BUILD_ABI=x86_64`,
`CR_PROBE=native_runner/probe/out/x86_64/libcrprobe.so`), 2 games in **182 s**.
Decks: seat 0 the locked X-Bow
(`26000001, 26000084, 28000000, 26000000, 26000010, 27000006, 28000011,
27000008`, forms `1,0,0,2,0,1,0,0`), seat 1 Hog 2.6 (`27000000, 28000000,
26000021, 26000038, 26000030, 26000014, 26000010, 28000011`, base forms).

| game | a_owner | A's deck | B's deck | winner | tick | actions | illegal | rejected |
| ---: | ---: | --- | --- | --- | ---: | --- | ---: | ---: |
| 1 | 0 | X-Bow | Hog 2.6 | **A** | 3,601 | 33 / 41 | 0 | 0 |
| 2 | 1 | X-Bow | Hog 2.6 | **B** | 6,124 | 85 / 80 | 0 | 0 |

**1–1, 0 illegal, 0 rejected, 0 truncated, both terminals.**  The mechanics
question is answered: the locked deck plays natively through the extracted
engine, with no refuses and a real terminal.  It does **not** rank the policies,
and the deck column is the correction session 40 made: the evaluator swaps decks
*with* the policy, so A kept the locked X-Bow at seat 1 too and each policy
played its own deck in both games.  A 1–1 at n = 2 was a coin flip, not a
measurement of skill (the 50-game baseline in session 40 is 49–1).
The first 8-game attempt had the probe's transport die mid-match after game 1;
that was under concurrent CPU work, and this clean run is the recorded baseline.
At 182 s for two games, a 50-game paired block is ~75 min — the next session's
first item, not a multi-box spend.

### Measured — pilot vs seed on native (exploratory)

`runs/native-parity-s39/pilot-vs-seed-4games/`: the pilot checkpoint (policy A)
against the seed it started from (policy B), same rig, same match config,
`--sample`, seeds 11–14, 4 games.

| game | a_owner | A's deck | B's deck | winner | tick | actions | illegal | rejected |
| ---: | ---: | --- | --- | --- | ---: | --- | ---: | ---: |
| 1 | 0 | X-Bow | Hog 2.6 | **A** | 3,601 | 32 / 37 | 0 | 0 |
| 2 | 1 | X-Bow | Hog 2.6 | **A** | 3,601 | 39 / 33 | 0 | 0 |
| 3 | 0 | X-Bow | Hog 2.6 | **A** | 3,601 | 31 / 40 | 0 | 0 |
| 4 | 1 | X-Bow | Hog 2.6 | **A** | 6,109 | 81 / 74 | 0 | 0 |

**4–0 for the pilot, 0 illegal, 0 rejected, 0 truncated.**  It won at both
seats, but it played the locked X-Bow in all four games while the seed played
Hog 2.6, so the smoke compared a policy **and** a matchup.  It is still **not a
win-rate signal**: n = 4, sampled actions, and a 16-update run at the native
1e-5 learning rate should not move a policy this far.  Under an even prior,
4–0 has a 1-in-8 two-sided tail.  Session 40's mirror-X-Bow block is the gate,
and the pilot is **19–30** there — the 4–0 was the deck matchup.  Nothing is
rented on this.

### Still open

- **The group section's fixed-capacity pad** is the next CPU term (session 42
  closed `_entity_rows`, `_targeted_mask` and both sort keys).  `build` is
  2.18 ms on a live board; the pad to 48 group / 160 child slots is the largest
  remaining piece of it.  Measure first.
- **The micro fixture measured a towers-only board until session 42** — do not
  cite a pre-session-42 `cpu_half_micro.py` section mean as a live-board number.
- **The overlapped loop** still needs a separate card or a batched decision
  server; it is not a default on one A6000.
- **The 8-vCPU question** is a measurement gated on a native signal: 2.73
  matches/s at $0.43/h is the break-even, above the measured 2.221 and the
  1.842 predicted ceiling.
- **Native parity cadence**, unchanged: 50–150 matches per N updates and after
  every adapter change.

### Next session (session 40) — executed; see the session-40 section above

1. **Widen the native comparison to a paired 50-game block** before ranking
   anything: seed vs A-hog26 (the baseline) and seed vs the pilot checkpoint
   (the 4–0 smoke above).  Keep the 2-game and 4-game runs as the mechanics
   controls.  At ~90 s/game this is ~75 min per 50, not a rental.
2. **If the pilot is promoted to a training run**, use ≥3 seeds and the
   `reset` cadence, and record the native eval every N updates.
3. **Only then rent** for the 8-vCPU measurement — one box, one shape, the
   `cloud_capacity.py` sweep; no multi-box run before a native win-rate signal.
4. Keep the group live-width change; do not add a second gather form.  The next
   CPU term is `_entity_rows`.

## Session 40 results (2026-11-17) — the 50-game gate, and it is negative

**Read first:** the decision entry above and `FINDINGS.md` §3.74.

Session 40 did the handoff's item 1 and nothing else that touches the native
host: two 50-game blocks on the x86_64 16.635.1 rig, seeds 101–150, sampled
actions, paired seats.  The pilot block is the gate; the baseline block is the
anchor.

### What landed

- **`tools/native_analysis/native_block.py`** — merges evaluator chunk
  directories into one block.  It reports the decisive-game A win rate with a
  Wilson 95 % interval, an exact two-sided binomial test against even, the split
  by A's seat, and warnings for a mixed checkpoint/ruleset/native build/match
  shape, a duplicated `(seed, a_owner)` schedule or a non-contiguous seed range.
  The artifact is `native-match-block.v1`.
- **`tools/native_analysis/native_block_run.sh`** — runs a block in chunks, each
  behind a fresh engine host, with one retry and a stop-at-this-seed on failure
  so a resumed block never invents a game.
- **`evaluate` rows record `decks_by_owner`**, and a test pins the deck/seat
  mapping.  The evaluator swaps decks *with the policy* (`deck0` stays with A),
  which the session-39 write-up had read backwards.
- **`tools/native_analysis/match-configs/mirror-xbow.json`** — the locked X-Bow
  deck on both seats, forms `1,0,0,2,0,1,0,0`, so a pilot-vs-seed block differs
  only by policy.

### Measured — the gate

`runs/native-parity-s40/pilot-vs-seed-mirror.json` (five chunks, seeds 101–150):

| term | value |
| --- | ---: |
| result (A = pilot) | **19–30**, 1 draw |
| A win rate | **38.8 %** (95 % CI 26.4–52.8), p = 0.15 |
| by A seat | 11–14 (seat 0), 8–16 (seat 1) |
| illegal / rejected / truncated | 0 / 0 / 0 |
| draw | seed 108, tick 6,068, `draw_or_unexposed_tiebreak` |

The pilot is not better than its seed, and the CI says it is not better by more
than ~3 points.  This is the intended reading of a small pilot, and it closes
session 39's 4–0 as a matchup effect.

### Measured — the baseline anchor

`runs/native-parity-s40/seed-vs-hog26.json` (seeds 101–150, the recorded fixed
decks: seed on the locked X-Bow, `A-hog26-v738` on Hog 2.6):

| term | value |
| --- | ---: |
| result (A = seed) | **49–1** |
| A win rate | **98.0 %** (95 % CI 89.5–99.6), p = 9e-14 |
| by A seat | 25–0 (seat 0), 24–1 (seat 1) |
| terminal ticks | 49 at 3,601, the loss at 6,104 |
| illegal / rejected / truncated | 0 / 0 / 0 |

Hog acted *more* than the seed (median 41.5 vs 31 actions) and was never refused,
so the result is not a stall.  It is not a contradiction of hog26's published
71.9 % ±2.6 — that figure is against its own start — but it does mean that
checkpoint is not a bar for the learner deck in this rig.  Session 39's 2-game
1–1 was a 3.9 %-likely coin flip under 98 %.

### Still open

- **A training run that can actually move the policy.** More updates at the
  native 1e-5, ≥3 seeds, `reset`; the pilot's 16 updates are below the
  resolution of a 50-game block, so it cannot be the promotion vehicle.
- **Replace hog26 as the bar.**  The seed mirror is the policy gate; keep hog26
  as a canary, and find a reference that is competitive with the seed on its own
  deck before calling a 50-game block a bar reading.
- **The 8-vCPU measurement** stays deferred until a native win rate exists.
- **`_entity_rows`** was the next CPU term and is done in session 41
  (1.297 → 0.762 ms on a live board); `_targeted_mask` and both sort keys are
  done in session 42 (0.64 → 0.23 ms).  The fixed-capacity pad is next.
- **Native parity cadence**, unchanged: 50–150 matches per N updates and after
  every adapter change.

## Session 42 results (2026-11-17) — the group sort keys at live width, and a fixture that had no board

**Read first:** the decision entry above and `FINDINGS.md` §3.76.

Session 41's plan was one line: `_targeted_mask`, then the two sort keys.  Doing
it exposed a defect in the micro fixture that had been understating (and
mis-describing) the CPU half since session 37.

### What landed

- **`VectorGroupBuilder._targeted_mask`** builds its lane-tagged key arrays and
  `searchsorted` from the live rows.  Invalid and tower slots all carry
  `entity_id == -1` (`0xFFFFFFFF` after the tag), which can never equal a
  non-negative target id, so they are false in both forms.
- **`_slot_order`, used by `_orders` and `_child_order`**, sorts only the
  included slots and returns them as entity indices in a `(lanes, live width)`
  array.  `build` reads at most that width from each order; the padded tail
  repeats row 0 and is masked off, exactly as the invalid tail was.
  `build(live_slots=False)` still pads the order axis to 48/160 so the
  session-39 wide/live A/B keeps measuring the wide assembly.
- **`cpu_half_micro.py` now waits past the playable cooldown** — it played at
  tick 61 with `FIRST_PLAYABLE_TICK == 90`, so every play was refused and the
  "settled board" was six towers with zero live entities.  It waits 95 ticks,
  plays 15, settles 40: **11.5 live entities mean, 17 max**.
- **`cpu_half_micro.py` gates all three new forms** against their pre-session-42
  implementations (`_dense_targeted_mask`, `_dense_orders`, `_dense_child_order`)
  on the per-lane retained prefix, and records every timing in `group_sort` /
  `group_build`.

### Measured — the same-process A/B

`runs/crforge-c4/cpu-half-micro-s42.json`, 128 lanes × 2 seats, live board
(11.5 entities mean), 20 repeats, `OMP_NUM_THREADS=4`:

| term | dense | live | speed |
| --- | ---: | ---: | ---: |
| `_targeted_mask` | 0.356 ms | **0.083 ms** | 4.3× |
| `_orders` | 0.114 ms | **0.065 ms** | 1.76× |
| `_child_order` | 0.170 ms | **0.079 ms** | 2.15× |
| `build` (live slots) | 6.987 ms wide | **2.183 ms** | 3.2× |
| `build` full capacity | 6.987 ms | — | oracle |
| `_entity_rows` (session 41) | 1.297 ms | **0.762 ms** | 1.70× |

The three session-42 terms are **0.64 → 0.23 ms** per seat.

### Measured — the gates

- `runs/crforge-c4/batched-tensorize-s42.json`: **3,008/3,008** lane-decisions,
  both seats, **0 mismatches**, 5,196 micro-actions, **0.0660
  ms/lane-decision** (11.2× the 0.739 oracle), gate pass.
- `-s42-seed2.json`: **3,008/3,008**, 5,361 actions, 0.0634 ms (11.8×).
- `runs/crforge-c4/self-play-s42-probe.json`: 4 lanes × 2 seats × 2 segments ×
  24 decisions with `--ppo-update`, **2/2 on-policy**, |KL| 7.46e-5 (the same
  first update as sessions 39 and 41), 0 illegal, training gate pass.
- Bridge suite **145 passed**; ruff clean on the touched files.

### Corrected record

The micro tool's section means from sessions 37–41 were measured on a
towers-only board (`live_g = 1`).  On the corrected fixture the same sections
are groups 8.75, relations 5.83, planes 2.90, candidates 2.13, cards 1.80,
towers 1.44 ms/seat — so **the old section ranking was measured on the wrong
board**, though `groups` was and remains the largest.  The producer's own
`cpu_half` blocks were always on real matches (128-lane runs recorded groups
22–33 ms/seat), so `T = N_proc × F` and the provider arithmetic are unaffected;
it is the micro tool that was wrong.  `cpu_half_micro.py` is also now the only
place these shims are compared, and it compares both forms on the same batch.

### Next session (session 43)

1. **The fixed-capacity pad in `VectorGroupBuilder.build`** is the next term:
   `build` is 2.18 ms, the three native-row functions are 0.23, `_entity_rows`
   0.76, and the rest is gather/feature assembly plus zero-filling 48 group and
   160 child slots (~3 MB of fresh arrays per build).  Measure the pad before
   rewriting it; gate on `cpu_half_micro.py` + the `batched_tensorize.py` digest.
2. **Re-take the section ranking on the corrected fixture** before choosing a
   second section — every pre-session-42 micro section number is an empty-board
   number.
3. **The training run that can move the policy** is still the real open item
   (more updates at 1e-5, ≥3 seeds, `reset`, native eval every N updates).
4. **Do not rent** for the 8-vCPU measurement before a native win-rate signal.

## Session 41 results (2026-11-17) — `_entity_rows` builds at live width

**Read first:** the decision entry above and `FINDINGS.md` §3.75.

Session 40's still-open list named one CPU term; this session did that term and
nothing that touches the native host or spends money.

### What landed

- **`VectorGroupBuilder._entity_rows` assembles at the live rows.**
  `valid` indexes a flat `live` array once per call; every field is built from
  the 640-ish live values and scattered into one zero row
  (`np.zeros`/`np.full` + `out[live] = ...`).  The nine per-vocabulary tables
  are gathered with a live-row map, so the int32 → `intp` index conversion that
  NumPy repeated on every call is paid once.
- **`cpu_half_micro.py` keeps the dense form as the oracle.**  The tool now
  carries `_dense_entity_rows` (the session-40 implementation) and asserts the
  two forms agree field by field, dtype included, live rows for velocity; it
  records `entity_rows_ms`, `entity_rows_dense_ms` and
  `entity_rows_saved_ms` beside the existing group-build A/B.

### Measured — the same-process A/B

`runs/crforge-c4/cpu-half-micro-s41.json`, 128 lanes × 2 seats, 6 live
entities, 20 repeats, `OMP_NUM_THREADS=4`:

| term | session 40 | session 41 | delta |
| --- | ---: | ---: | ---: |
| `_entity_rows` | **1.098 ms** | **0.301 ms** | −0.797 (3.65×) |
| group `build` (live slots) | 2.684 ms | **1.976 ms** | −0.708 |
| group `build` (full capacity) | 7.572 ms | 7.353 ms | oracle, unchanged |

The same tool's `groups` section mean moves **6.38 → 5.50 ms/seat** across runs;
that half is a different process on a loaded host, so it is reported, not
claimed.

### Measured — the gates

- `runs/crforge-c4/batched-tensorize-s41.json`: **3,008/3,008** lane-decisions,
  both seats, **0 mismatches**, 5,196 recorded micro-actions, **0.0725
  ms/lane-decision** (11.99× the 0.869 oracle), C3's 0.5 ms/lane gate passes.
- `-s41-seed2.json`: **3,008/3,008**, 5,361 actions, 0.0779 ms/lane-decision
  (11.33×).  The per-lane walls are this host's — its oracle is also slower than
  session 39's (0.869 vs 0.706) — so the A/B above, not the wall, is the claim.
- `runs/crforge-c4/self-play-s41-probe.json`: 4 lanes × 2 seats × 2 segments ×
  24 decisions with `--ppo-update`, **2/2 on-policy**, |KL| 7.46e-5 (the same
  first update as session 39), 0 illegal, training gate pass.
- Bridge suite **145 passed**; ruff clean on the touched files.

### Next session (session 42) — executed; see the session-42 section above

1. **`_targeted_mask` at live width** — the largest of the three remaining dense
   passes over the 200-row entity axis (offline 0.27 ms at 128 lanes), then the
   two sort keys (`_orders` 0.11, `_child_order` 0.18).  Gate on
   `cpu_half_micro.py` (same-process A/B) and the `batched_tensorize.py` digest.
2. **A training run that can move the policy** stays the real open item: more
   updates at 1e-5, ≥3 seeds, `reset`, with the native eval every N updates.
   The pilot's 16 updates are below the resolution of a 50-game block.
3. **Do not rent** for the 8-vCPU measurement before a native win-rate signal.


## Session 38 results (2026-11-17) — the box re-measure and the full-call graph

**Read first:** the decision entry above, `FINDINGS.md` §3.71 and
`ROADMAP_CRFORGE.md` §4.

### What landed

- **A captured random probe is the RNG gate.**  `_GraphSlot` captures a
  one-value `torch.rand` with the sampled decode; `_assert_graph_rng_advances`
  compares that, not the log-probabilities, because an initial board samples
  almost every row as WAIT.  Torch 2.12 also names the default generator before
  capture (`register_generator_state`); 2.14+ registers lazily and the call is a
  no-op.  The artifact records `random_probe_delta_between_replays`,
  `max_log_prob_delta_between_replays` and
  `sampled_graph_generator_registration`.
- **`overlap_compare.py` drives the forwarded loop.**  It gains `--fast-forward`,
  `--no-cuda-graphs`, `--full-cuda-graph` and `--ppo-lane-minibatch`, and records
  them in the artifact's shape block.  `cloud_capacity.py` gains
  `--full-cuda-graph` (recorded in `settings`) so the provider sweep can use the
  fastest forward.
- **A GPU device bug in the PPO minibatch.**  `distributed_ppo_update_v4` moved
  `active_lanes` to `lane_indices.device`; lane minibatching previously crashed
  on a CPU/CUDA index mix.
- **`cpu_half_micro.py` measures the entity-table mask** (nine fields, in-place
  vs `where`): 0.12 ms saved, below noise, not adopted.
- **Bridge suite 144 passed (14 fast-forward), native PPO 36 passed**, ruff clean
  on the touched files.

### Measured — the 128-lane producer and the six-producer sweep

`runs/crforge-c4/cloud-thunder-a6000-s38.json`, fp16 `--no-cuda-graphs`, steady
last segment: `T = 417.6 ms`, forward 161.1, **decode 11.7** (decode_only 5.3 +
record 5.0 + plan 0.4), tensorize 105.2, steps 126.3, facts 13.2; CPU half
254.5 ms.  Session 36's 68 ms decode is gone; this box's other buckets are
slower, so the single-process wall is 425.1 → 417.6 ms.

| producers | `T` s | per-proc | aggregate | retention | forward × | CPU × | $/1M |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.4411 | 0.478 | 0.478 | 0.95 | 1.12 | 1.02 | 203 |
| 2 | 0.4489 | 0.470 | 0.940 | 0.93 | 1.00 | 1.12 | 103 |
| 4 | 0.5710 | 0.369 | 1.477 | 0.73 | 1.35 | 1.38 | 65.8 |
| 6 | 0.8376 | 0.252 | **1.512** | 0.50 | 1.78 | 2.15 | **64.3** |

The equation's one-process terms are `g = 0.390`, 2.56 processes, **1.293**
ceiling; the six-producer aggregate is **1.17× that ceiling** and **+20.6 % over
session 36's 1.254**. At six the CPU half is 547 ms, not 728; the sync storm was
a contention amplifier. The binding is still **shared**, not clean GPU
serialization.

### Measured — the graph A/B

Alternating, same box, same shape, warmup on:

| run | graphs | full | `T` s | forward ms | CPU half ms | verify |
| --- | --- | --- | ---: | ---: | ---: | --- |
| ab1-nograph | off | — | 0.448 | 165.2 | 278.0 | pass |
| ab1-partial | on | no | 0.403 | 145.5 | 256.9 | pass |
| ab2-nograph | off | — | 0.407 | 138.8 | 251.7 | pass |
| ab2-partial | on | no | 0.384 | 132.6 | 249.6 | pass |
| **fullprobe1** | on | **yes** | **0.347** | **102.0** | 244.3 | pass |
| partial3 | on | no | 0.398 | 143.6 | 255.0 | pass |
| **fullprobe2** | on | **yes** | **0.338** | **106.5** | 231.5 | pass |
| partial4 | on | no | 0.416 | 149.1 | 268.2 | pass |

The full graph is **40 ms of forward / 40–78 ms of wall** better than the
partial graph, and session 36's "+34 ms" is not reproduced. Verification:
0/256 mismatches, fp16 log-prob Δ 3.6e-7, entropy 4.1e-6, value 4.0e-4; RNG
probe Δ 0.1616, log-prob Δ 13.86. The forward ranged 102–183 ms across runs, so
a cross-session single run cannot rank the shapes; the alternated pairs can.

### Measured — the full-graph process sweep

`cloud_capacity.py --run --fast-forward --full-cuda-graph --sweep-procs 1 2 4 6`
(`runs/crforge-c4/cloud-thunder-a6000-s38-fullgraph.json`):

| producers | `T` s | per-proc | aggregate | retention | forward ms | CPU ms | $/1M |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.3562 | 0.592 | 0.592 | 1.04 | 105.8 | 250.4 | 164 |
| 2 | 0.4410 | 0.478 | 0.957 | 0.84 | 114.4 | 326.6 | 102 |
| 4 | 0.4967 | 0.425 | 1.700 | 0.75 | 135.6 | 361.2 | 57.2 |
| 6 | 0.5702 | 0.370 | **2.221** | 0.65 | 162.7 | 407.5 | **43.8** |

The primary terms are `T = 0.3709 s`, forward 114.5 ms, `g = 0.309`, **3.24
processes**, a **1.842** predicted ceiling; six producers measure **2.221**,
**1.21× the ceiling**, and the case saturates at four. At six the forward is
163 ms (1.54×) and the CPU half 408 ms (1.63×), against the no-graph sweep's
290 / 547 — the graph removes work *and* contention. **+47 % over the no-graph
1.512 and $64.3 → $43.8/1M** (2,428: $87.6). The binding is still **shared**.

### Measured — the overlapped loop (full graph, lane-minibatch 16)

| mode | wall s | collection s | update s | collection ld/s | stale |
| --- | ---: | ---: | ---: | ---: | ---: |
| serial | 92.1 | 52.5 | 39.6 | 468.2 | 0 |
| overlap | **86.2** | 64.7 | 50.5 | **379.9** | 2 |

**1.07× wall, 0.81× collection, gate fail.** The worker's backward and the
collector's forward share one A6000; 5.9 s of the 39.6 s update is hidden and
the rest is contention. Stale |KL| 0.076, clip 8.4 % (the 0.404 on-policy KL is
16 optimizer steps per segment, a mechanics run, not a learning claim).

### Still open

- **Groups' 44 ms.** The feature transpose is slower; the cost is
  `_entity_rows` / the 35 gathers. Batch the gathers by dtype, or share the
  seat-invariant fields between the two builders. **(Closed in session 39: the
  padded capacity, not the gather form, was the cost — live-width slots measure
  7.57 → 2.68 ms in one process.  `_entity_rows` is the remaining term; the
  gather-by-dtype rewrite is withdrawn.  See the session-39 entry.)**
- **The overlapped loop needs a separate card or a batched decision server.**
  It is not a default on one A6000.
- **The 8-vCPU question is re-opened by the full-graph rate.** The 6-vCPU box is
  $43.8/M measured; an 8-vCPU box at $0.43/h needs more than 2.221 × 0.43/0.35
  = 2.73 matches/s to beat it. That is above the measured 2.221 but not obviously
  above the card's ceiling, so it is a measurement, not arithmetic.
- **Native parity cadence, unchanged.**

### Next session (session 39) — executed; see the session-39 section above

1. Reduce the group gathers (batch by dtype) and gate on `batched_tensorize.py`;
   do not transpose the feature block.
2. Re-run the provider comparison at the full-graph 2.221 / $43.8 terms; 6 vCPU
   remains the SKU unless an 8-vCPU measurement clears 2.73 matches/s.
3. Keep the pipeline a gated option; do not default it on one card.
4. Native parity cadence, unchanged.

## Session 37 results (2026-11-17) — the CPU half, profiled

**Read first:** the decision entry above, `FINDINGS.md` §3.70 and
`ROADMAP_CRFORGE.md` §4.

### What landed

- **`VectorTensorizer.tensorize(..., timings=...)`** accumulates every row
  builder's share; `VectorTensorizer.record_action` takes an optional
  `candidate_tables()` conversion so the decode and the record share it, and it
  writes the whole record in one NumPy pass.
- **`interleave_rows`** moves into `crforge_bridge/vector_tensorize.py` (testable,
  shared) with the measured note that `stack` + `reshape` is already one copy.
- **The producer's `cpu_half` block**: per segment, the eight sections, the
  interleave, and the decode split; `--profile-collection` writes the cProfile
  top list.  `--full-cuda-graph` wires the sampled graph.
- **`tools/crforge/cpu_half_micro.py`**: the same-process A/B for the shims
  (interleave, group gather, `record_action`), which is how a reverted
  "optimisation" stays documented instead of being re-tried.

### Measured — the sections at 128 lanes × 2 seats (laptop CPU, live board)

| section | ms (run A, 20 reps) | ms (run B, 40 reps) |
| --- | ---: | ---: |
| groups | 30.3 | 16.6 |
| relations | 6.4 | 3.3 |
| candidates | 4.5 | 2.5 |
| cards | 2.4 | 1.1 |
| towers | 2.1 | 0.9 |
| planes | 1.7 | 1.1 |
| empty | 0.7 | 0.6 |
| scalars | 0.5 | 0.2 |
| interleave | 9.0 | 4.2 |
| engine steps | 31–37 | |
| facts | 8–12 | |

The 3.9× that would put the half at ~108 ms is not in one place: groups' feature
assembly, the JVM step call and the facts read are the three terms.

### Measured — the shims, in one process (`cpu_half_micro.py`, 40 repeats)

| shim | before | after | verdict |
| --- | ---: | ---: | --- |
| `record_action` (128 rows acting) | 4.32 ms | **0.11 ms** | 38×, and it removes the GPU syncs the box recorded as `decode` |
| two-seat interleave | 4.17 ms (`stack`) | 4.25 ms (direct write) | no win; reverted |
| 35 group gathers | 0.90 ms (`take_along_axis`) | 0.71 ms (advanced) | within run-to-run noise |

### Measured — the producer's buckets, same shape, before/after code

| bucket (ms mean) | before | after |
| --- | ---: | ---: |
| tensorize | 77.9 | 41.6 |
| decode | 3.4 | 2.7 |
| step | 41.2 | 31.3 |
| facts | 9.6 | 7.8 |

The wall is inference-dominated on this host (9.3 s → 7.9 s for the CPU fp32
forward at 256 rows), so ~10 % of that is host load, visible in the unchanged
buckets.  The micro A/B, not this table, is the attribution; the producer numbers
are the absolute state the box re-measure compares against.

### Still open

- **The box re-measure.**  `record_action` is the term the box can confirm:
  decode was 68 ms there.  Run the 128-lane producer, then the 1/2/4/6 sweep,
  then the overlapped loop.
- **`--full-cuda-graph` wall.**  The gate is in; the capture, its replay cost and
  whether it beats `--no-cuda-graphs` are box measurements.  The frozen-RNG
  refusal is the safety net if a build does not register the generator.
- **Groups.**  Build the feature block as one `(features, lanes, groups)` stack
  and transpose once, instead of ~20 strided writes per seat.
- **Steps and facts.**  The engine step is 31–37 ms here (5 JVM calls/decision at
  128 lanes); facts is the engine→numpy read.
- **Native parity cadence, unchanged.**

### Next session (session 38) — executed; see the session-38 section above

1. **Re-measure on the A6000** at 128 lanes × 3 segments × 32 decisions with the
   new record: `decode` should collapse from 68 ms, and the six-producer sweep
   should move for the first time since 1.215.  Record both, and the
   `cpu_half.breakdown_ms_mean` block, in `runs/crforge-c4/`.
2. **Then `--full-cuda-graph --verify-fast-forward`** on the same box; the RNG
   gate and the action-equality gate both have to pass, and the wall is compared
   against `--no-cuda-graphs`.
3. **Overlapped loop on the box** (session 36 item 4): 128 lanes, forward 133 ms,
   update the short side.
4. **Groups' feature assembly** — the largest remaining CPU term; gate it on the
   `batched_tensorize.py` digest, not on a wall alone.
5. **Native parity cadence, unchanged.**

## Session 36 results (2026-11-16) — the in-process forward port, measured on the A6000

**Read first:** the decision entry above, this section, `FINDINGS.md` §3.69, and
`ROADMAP_CRFORGE.md` §4.

Session 36's items 1–3 (provider sweep, the 8/32/64/128-lane A6000 table, the
overlapped loop on the A6000) each need a rental and are not touched here. Item
4, the `ever_server` forward port, does not: the code path, its numerical gate
and the artifact fields land now; the GPU wall stays a box measurement.

### What landed

- **`crforge_bridge/fast_forward.py` — `FastForwardV4`.** One object owns
  everything the producer's forward needs between decisions:

  | trick | in-process shape |
  | --- | --- |
  | precision | `torch.autocast` fp16 on CUDA / bf16 opt-in on CPU; log-prob, entropy and value restored to fp32 |
  | graphs | one `torch.cuda.CUDAGraph` per padded row count, captured lazily; **encode+core only** |
  | state table | one fp32 `RecurrentPolicyStateV4` for the run; `next_state` is copied into it, the graph reads/writes fixed buffers |
  | layout/pack once | the padded device batch is built once per row count and refilled by a valid-slice copy; CUDA uses a pinned host mirror and `non_blocking` |

- **The sampler is deliberately outside the graph.** torch 2.5.1 has no
  graph-safe generator (`CUDAGraph.register_generator_state` is absent), so a
  captured `sample_for_ppo_rollout` would replay the same draws. The port
  captures `forward` and calls the new
  `UniversalCardPolicyV4.select_action_from_context(batch, context, sample=...)`
  eagerly; the graph still covers encode + recurrent core, and `sample=False`
  is the producer's `--greedy` path.
- **Producer flags:** `--fast-forward`, `--inference-precision
  {auto,fp32,fp16,bf16}`, `--no-cuda-graphs`,
  `--verify-fast-forward`. `auto` is fp16 on CUDA and fp32 on CPU (bf16 on this
  CPU is 17.5× slower, below). `--verify-fast-forward` needs the warmup batch
  and aborts before segment 0 if the actions differ.
- **Artifact:** `inference.fast_forward`, `inference.verification`,
  `inference.sampling` and `inference.steady_state` (the last segment's mean
  decision, mean inference, share, CPU half, unaccounted). The shape block keeps
  `warmup_forward` and the timing buckets unchanged.
- **Tests:** `crforge_bridge/tests/test_fast_forward.py` (10) pins precision
  resolution, the staged valid prefix, sampled equality with eager fp32, the
  second decision's carry, a forward that does not advance the carry, `rebind`,
  the verification pass and its RNG/state restoration, the bf16 report's
  self-consistency, the row-count refusal and the disabled-graph description.
  The whole crforge bridge suite is **133 passed**; the V4 model/session suites
  are **27 passed**. Ruff clean.

### Measured — the CPU control

8 lanes × 2 seats (16 rows) × 1 segment × 8 decisions, warmup on, `--no-rollout`,
`OMP_NUM_THREADS=4`:

| run | inference ms (mean) | tensorize | decode | step | facts | verification |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| eager fp32 (baseline) | 488.1 (median) | 7.5 | 1.5 | 4.0 | 1.2 | — |
| fast fp32 | **491.4** | 9.4 | 2.1 | 7.3 | 1.7 | 0/16 mismatches, all deltas 0.0 |
| fast bf16 | **8,576.3** | 28.9 | 1.9 | 6.9 | 1.3 | 0/16 mismatches, log-prob 1.1e-4, value 1.25e-3 |

Three results:

1. **The fp32 fast path is the eager path plus one copy.** The state-table
   commit costs ~0.5 % of the forward; the verification is exact. That is the
   control the port needs: any GPU gain measured later is the precision/graph
   half, not a changed graph of computation.
2. **bf16 on a CPU without BF16/AMX is not the trick.** 8,576 vs 491 ms
   (17.5× slower) while still passing the equality gate. It proves the
   half-precision seam can run end to end on this host; it does not argue for
   `--inference-precision bf16` as a speed option. `auto` never selects it.
3. **The steady-state block does its job on a shape that has not warmed fully.**
   The training smoke (4 lanes × 2 × 4) reports a 56 ms unaccounted mean and a
   143.8 ms CPU half; the probe-shaped run reports 0.0 unaccounted. The field is
   a diagnostic, not a gate.

Evidence: `runs/crforge-c4/fast-forward-cpu-baseline.json`,
`-fp32.json`, `-bf16.json`, `-train.json`.

### Measured — the A6000, 128 lanes × 2 seats × 3 × 32

Rented Thunder `base`: 6 vCPU, 1×A6000 48 GB, $0.35/h, torch 2.12.1+cu130,
Java 21, `OMP_NUM_THREADS=1`. Probe shape `--no-rollout --no-validate`, steady
last segment (the buckets below are means):

| run | T ms | forward ms | CPU half ms | g | 1/g | GPU ceiling | CPU ceiling | verify |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| eager fp32 (no port) | 497.6 | 182.1 | 315.5 | 0.366 | 2.73 | 1.157 | 2.543 | — |
| **fp16, no graph** | **425.1** | **132.9** | 292.2 | 0.313 | 3.20 | **1.588** | 2.978 | pass |
| fp16 + graph | 459.7 | 136.1 | 323.6 | 0.296 | 3.38 | 1.550 | 2.755 | pass |
| fp32 + graph | 494.9 | 169.0 | 325.8 | 0.342 | 2.93 | 1.249 | 2.557 | pass |
| fp16 + graph, rollout | 613.5 | 139.2 | 474.3 | 0.227 | 4.41 | 1.517 | 2.063 | pass |

`g`, ceilings and cost are the `cloud_capacity.py` terms: GPU ceiling
`0.2109 × min(6, 1/g) / T`, CPU ceiling `0.2109 × 6 / T`, 1,214 convention.
Verification on every fast run: **0/256 action mismatches** on all six fields,
fp16 log-prob Δ ≤ 6e-7, entropy 5.1e-6, value 3.8e-4; the fp32 graph run is
exact (Δ 0, 0, 1.2e-7).

Four results:

1. **fp16 removes 49 ms of the forward; the graph removes nothing and costs
   wall.** The forward falls 182 → 133 ms (−27 %), but graph replay is 136 ms
   and T is 34 ms *worse* than the same fp16 path eager (459.7 vs 425.1). The
   graph captures only encode+core; its pinned staging is charged to the CPU
   half (323.6 vs 292.2) while the eager decode stays outside. **Make
   `--no-cuda-graphs` the measured default** until the decode is captured or
   several decisions are batched.
2. **The static-table cache is a CPU fix, not a GPU one.** It removes 65–77 ms
   per forward on the laptop CPU and ~2 ms on the A6000 (fp16 graph 138.5 →
   136.1). At 256 rows the forward is compute-bound.
3. **The CPU half is the packet.** tensorize 98 + decode 68 + step 113 +
   facts 12 + forward 133 ≈ 425 ms. Rollout storage adds **188 ms** — more than
   the forward — so a training run is 613 ms/decision with a 139 ms forward.
4. **The predicted ceiling rises 17 %, the realized rate 3 %.** See the sweep
   below: 1.254 measured at six producers (0.80× the 1.563 ceiling),
   $77.5/1M against $80.0 before.

### Measured — the corrected lane table and the process sweep

`tools/crforge/cloud_capacity.py --run --fast-forward --no-cuda-graphs` at
8/32/64/128 lanes, then 1/2/4/6 concurrent 128-lane producers:

| lanes × 2 seats @ 32 | T s | g | GPU slots | GPU ceiling | $/1M |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 8 | 0.2041 | 0.749 | 1.34 | 0.086 | $1,127 |
| 32 | 0.2508 | 0.546 | 1.83 | 0.385 | $253 |
| 64 | 0.3049 | 0.440 | 2.27 | 0.786 | $124 |
| **128** | **0.4348** | **0.310** | **3.22** | **1.563** | **$62.2** |

The corrected table is the warmup-on one the session-34 note asked for; the
small-lane rows are dominated by the fixed per-decision CPU cost, not the card.

| producers | T mean | per-proc m/s | aggregate | retention | forward × | CPU × |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.4780 | 0.441 | 0.441 | 0.91 | 1.07 | 1.11 |
| 2 | 0.5854 | 0.360 | 0.721 | 0.74 | 1.38 | 1.33 |
| 4 | 0.7615 | 0.277 | 1.108 | 0.57 | 1.58 | 1.83 |
| 6 | 1.0098 | 0.209 | **1.254** | 0.43 | 1.95 | 2.49 |

Verdict: **saturated at 2 processes**, bound **shared**, measured **0.80× the
GPU ceiling**, equation confirmed. At six producers both halves slow together
(forward 1.95×, CPU 2.49×); the port's 17 % predicted headroom is eaten by
contention, and the box is still **GPU-bound at 3.2 processes**. Measured cost
at six: **$77.5/1M** (1,214) or $155.0/1M (2,428) — 3 % better than the
pre-port $80.0, not the 22 % the ceiling suggests.

### The training smoke

4 lanes × 2 seats × 2 segments × 4 decisions, `--ppo-update --fast-forward
--verify-fast-forward`, CPU:

- `training.pass: true`; two finite updates; `approx_kl 1.6e-6` and
  `stale: false` on the first (no update preceded it), clip 0.
- The gate is `false` only on `eligible_act_rate 0.0` — 64 lane-decisions and
  four decisions per segment is a shape that never acts, which is not a
  throughput failure (the cloud probe makes the same distinction).
- The state table is rebound at the segment boundary (`reset` default), and
  `--overlap-update` touches only `trainer.model`, so the collector's captures
  remain valid.

### Still open

- **The CPU half.** tensorize 98, decode 68, engine steps 113, facts 12 ms at
  128 lanes. `T = N_proc × F` needs the half at ~108 ms for six vCPUs to feed
  the 133 ms forward; that is a 3.9× reduction on tensorize+decode+step, not a
  forward change.
- **The 157 → 21.5 ms target is now 133 ms.** fp16 took a quarter; the rest is
  the forward's real FLOPs at 256 rows. A larger batch (more lanes) amortises
  them per row; a smaller model or a distilled head is the other lever.
- **Graphs pay only if the decode is captured.** torch 2.12 has
  `CUDAGraph.register_generator_state`; torch 2.5.1 does not. A version-gated
  full-call graph is the next experiment, with the same action-equality gate.
- **MPS is not wired**, and `--overlap-update` was not re-run on the box.
- **Graph capacity is fixed** at 512 active effects / 2,048 relation edges per
  row; a shape that exceeds it raises rather than falling back.

### Next session (session 37)

1. **De-Python the CPU half.** Profile tensorize (98 ms) and decode (68 ms) at
   256 rows; the target is ~108 ms for the whole half at six processes. Do not
   rent more vCPUs until it moves: the box is GPU-bound at 3.2 processes.
2. **Then re-run the sweep** at the same shape. The process crossover moves
   only when the half falls; the 6-vCPU decision stands until then.
3. **Version-gate a full-call CUDA Graph** (torch 2.12 only) covering the
   sampled decode with `register_generator_state`, gated by the same
   `--verify-fast-forward` equality check. The current partial graph is 34 ms of
   wall worse; a full-call graph is the only form that can pay.
4. **Overlapped loop on the box** (session 32 item 1): 128 lanes, now that the
   forward is 133 ms and the update is the short side.
5. **Native parity cadence, unchanged.**

## Session 35 results (2026-11-16) — the provider decision is one comparison: `N_proc` vs `G × E / g`

**Read first:** the decision entry above, this section,
`FINDINGS.md` §3.68, and `ROADMAP_CRFORGE.md` §4.

### What landed

- **`tools/crforge/cloud_capacity.py`** — the provider probe. It reads a
  producer artifact (or runs the producer itself) and applies
  `matches/s ≈ 0.211 × min(N_proc, G × E / g) / T` term by term. `--run`
  measures 128 lanes × 3 segments × 32 decisions with the warmup on; `--lanes`
  sweeps the shape; `--sweep-procs 1 2 4 6` launches that many producers
  concurrently to validate the crossover; `--compare` scores the saved
  provider reports. `--artifact` scores a recorded run and requires `--cpus`
  and `--gpus`, because an artifact does not carry the host's vCPU/GPU count.
- **The rates are reported in both repo conventions.** The equation and
  `ROADMAP_CRFORGE.md` use **1,214 lane-decisions per match** (both seats); the
  producer artifact's own `matches_per_s` divides by two seats (2,428). The
  report carries both and the $/1M for both, so a provider quote cannot move
  2× on an unstated convention.
- **Cold-state guards.** The tool warns when an artifact has fewer than 16
  decisions, was written with `--no-warmup-forward`, or records a first forward
  more than 2× its median. It uses the last segment as the steady state and the
  **median** forward (not the mean, which contains the cold decision) for `g`.
- **A non-zero producer gate is not a throughput failure.** A short shape that
  never acts exits 1 because `eligible_act_rate == 0`; the probe still reads its
  artifact. Only a missing artifact aborts a measurement.
- **Tests:** `crforge_bridge/tests/test_cloud_capacity.py` (18) pins the
  recorded A6000 arithmetic, the CPU-only and partitioned-card cases, the
  balance band, the last-segment/median rules, the cold warnings, both rate
  conventions and the sweep saturation verdict. Ruff clean.
- **Recorded:** `runs/crforge-c4/cloud-capacity-a6000.json` (scored from
  `gpu-forward-scaling-128-long.json` with 6 vCPU / 1 A6000 / $0.35/h) and
  `runs/crforge-c4/cloud-thunder-a6000-6vcpu.json` (live Thunder 6-vCPU A6000,
  with the raw producers under `-6vcpu/raw/`).

### Measured — the A6000 terms reproduce the recorded 0.33

`runs/crforge-c4/gpu-forward-scaling-128-long.json`, 128 lanes × 32 decisions,
steady last segment:

| term | value |
| --- | ---: |
| `T` (decision wall) | 0.6468 s |
| median forward | 157.4 ms |
| `g` (forward share) | 0.2434 |
| `1/g` (processes per A6000) | **4.11** |
| one-process matches/s (1,214) | **0.326** (ROADMAP 0.33) |
| one-process matches/s (2,428) | 0.163 (artifact field 0.163) |
| 6 vCPU: CPU ceiling | 1.956 matches/s |
| 6 vCPU: GPU ceiling | **1.339 matches/s** |
| 6 vCPU: verdict | **GPU-bound** |
| $/1M at $0.35/h (1,214 / 2,428) | **$72.6 / $145.2** |

The GPU ceiling is the equation at `min(6, 4.11) = 4.11`; the CPU ceiling is
the same rate at `N_proc = 6`. The card is the smaller side, so two of the six
vCPUs are the price of oversubscription, not extra throughput. This is the
roadmap's 6–8-process row (1.3–1.6 matches/s, $75–92) at its low end, now with
the terms that produce it.

### Measured — a live 6-vCPU A6000, and the GPU ceiling is the invariant

`runs/crforge-c4/cloud-thunder-a6000-6vcpu.json` (raw producer artifacts under
`-6vcpu/raw/`): Thunder `base`, 6 vCPU, 1×A6000 48 GB, $0.35/h, Python 3.12,
torch 2.12.1+cu130, 128 lanes × 3 × 32, `OMP_NUM_THREADS=1`.

| term | value |
| --- | ---: |
| `T` (decision wall) | 0.4287 s |
| median forward | 158.4 ms |
| CPU half of `T` | 270.3 ms |
| `g` (forward share) | 0.3695 |
| `G × E / g` | **2.71 processes** |
| CPU ceiling at 6 vCPU | 2.952 matches/s |
| **GPU ceiling** | **1.331 matches/s** |
| one process | 0.492 matches/s |
| predicted / measured at 6 | 1.331 / **1.215** (0.91×) |
| $/1M predicted / measured (1,214) | **$73.0 / $80.0** |
| $/1M physical (2,428) | $146.1 / $160.0 |

The concurrent sweep:

| producers | `T` mean | per-process | aggregate | retention | forward × | CPU × |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.4488 s | 0.470 | 0.470 | 0.96 | 1.01 | 1.07 |
| 4 | 0.8279 s | 0.255 | 1.020 | 0.52 | 1.90 | 1.95 |
| 6 | 1.0418 s | 0.202 | **1.215** | 0.41 | 2.37 | 2.47 |

Three results matter:

1. **The GPU rate ceiling is the invariant.** The recorded box and this one have
   different CPUs (`T` 0.6468 vs 0.4287) and therefore different process
   capacities (4.11 vs 2.71), but the same forward (157.4 vs 158.4 ms) and the
   same ceiling (1.339 vs **1.331** matches/s). A provider comparison should
   quote the ceiling, not the process count; a faster CPU raises `N_proc`'s side
   of the `min` until the card binds, then buys nothing.
2. **The process bound is soft; the rate bound is not.** At 6 producers both
   halves slow together (forward 2.37×, CPU 2.47×), so the sweep calls the
   binding **shared**, not clean GPU serialization. The aggregate nevertheless
   reaches **0.91×** the GPU ceiling, so `min()` is a useful conservative bound
   and the *rate* is the decision quantity. The sweep verdict now checks both:
   process saturation and `measured aggregate / GPU ceiling` in 0.8–1.2.
3. **8 vCPU cannot pay on this card.** Break-even against the 6-vCPU measured
   $80.0/M at $0.43/h needs **1.493 matches/s**, above the 1.331 ceiling. The
   `tnr modify` path also refuses "A6000 with 1 GPU(s)" at 8 vCPU, so the
   dominance proof stands without a second rental. Revisit only when `g` falls
   (batched forward/MPS) or the 228 ms CPU packet leaves.

### Measured — the probe's live path ran on this laptop

`--run --lanes 8 --segments 1 --decisions-per-segment 8 --device cpu --cpus 8
--gpus 0 --omp-threads 4`: `T = 0.527 s`, median forward **506 ms (96 % of the
decision)**, per-process 0.025 matches/s, **CPU-bound by construction**,
0.200 matches/s ceiling at 8 processes. That is the CPU-only control the tool
needs: with no GPU the forward is CPU work, the GPU term is absent, and the
report says so instead of inventing a `g`. The forward's 96 % share is also the
reason a GPU moves this workload at all on a box without one.

The concurrent path ran too: 1 then 2 producers at 8 lanes × 1 × 4 on the same
CPU host. Aggregate went 0.019 → 0.026 matches/s, retention **0.686**, with the
forward half **1.46×** and the CPU half **1.53×** — both moved, so the report
names **CPU contention** (`predicted capacity: no GPU term`,
`confirms equation: n/a`), not a GPU cap. That is the distinction the sweep
exists for: a GPU-bound provider moves the forward half and holds the CPU half.
The shape is a smoke, not a provider result; the rented boxes get the default
128-lane run.

### The provider template

```bash
# on each rented box: measure, validate the process ceiling, save the report
.venv/bin/python tools/crforge/cloud_capacity.py --run \
    --provider <name> --price-per-hour <p/h> --sweep-procs 1 2 4 6 \
    --output runs/crforge-c4/cloud-<name>.json

# the corrected 8-128-lane steady-state table (session 34 item 1)
.venv/bin/python tools/crforge/cloud_capacity.py --run \
    --lanes 8 32 64 128 --provider <name> --price-per-hour <p/h>

# compare every saved provider
.venv/bin/python tools/crforge/cloud_capacity.py --compare \
    runs/crforge-c4/cloud-*.json
```

The default 128-lane × 3 × 32 shape is ~90 s at the A6000 rate plus startup; the
four-lane-count sweep is ~4× that. A provider comparison is only meaningful at
the same `--lanes`, `--segments`, `--decisions-per-segment` and rate convention.

**Setup notes for a fresh Thunder `base` box (verified 2026-11-16):** the template
has Python 3.12 and torch 2.12.1+cu130, but **no Java** and none of the bridge's
optional Python packages. The crforge jars are **class file 65**, so it needs
**Java 21**, not 17 (`UnsupportedClassVersionError` otherwise); install
`openjdk-21-jre-headless`, `jpype1`, `orjson`, `pyarrow`, `gymnasium` and
`pettingzoo`; put `native_runner/releases/16.635.1.json` in the bundle (without
it the loader says "known releases: []") and the built tables at
`~/.crforge/tables/16.635.8`. `PYTHONPATH=.resource/crforge/python` exposes
`crforge_env`. The minimal transfer is code + `.resource/crforge/env` +
`.resource/crforge/python` + the 16.635.8 tables + `B-general-v275.pt`; the
795 MB `Clashback` tree and `data_16635/` are not on this path.

### Found by running it

1. **The equation's two terms explain both A6000 boxes, without a fit.** The
   recorded box: `0.211 / 0.6468 = 0.326` (the 0.33) and `1/0.2434 = 4.11`.
   The live box: `0.211 / 0.4287 = 0.492` and `1/0.3695 = 2.71`, with a
   **1.331** ceiling and **1.215** measured at six producers (0.91×). The terms
   are the producer's own median forward and mean wall, so a provider
   measurement is one run, not a model.
2. **The GPU ceiling is invariant to the CPU; the process count is not.** Both
   A6000s have the same forward (157–158 ms) and the same **~1.33 matches/s**
   ceiling, but the faster live CPU moved `G × E / g` from 4.11 to 2.71. Quote
   the ceiling for a provider decision, not "processes per card": a faster CPU
   raises `N_proc` until the card binds, then buys nothing.
3. **The rate convention is a 2× decision variable.** 1.331 matches/s is
   $73.0 / 1M under the 1,214 convention and $146.1 / 1M for physical matches
   under 2,428; the measured 1.215 is $80.0 / $160.0. A provider quote that
   compares one to the other is off by 2×; the tool prints both.
4. **The process bound is soft; the rate bound is not.** At six producers both
   halves slow together (forward 2.37×, CPU 2.47×) — the sweep names that
   **shared**, not clean GPU serialization — but the aggregate still reaches
   0.91× the GPU ceiling. The verdict checks the rate as well as the process
   count, so a host that crosses `G × E / g` early does not read as a failed
   equation.
5. **6 vCPU beats 8 vCPU on the A6000 at this packet.** The 8-vCPU best case is
   the same 1.331 ceiling; at $0.43/h it is **$89.7/M**, above the 6-vCPU
   measured **$80.0/M**. Break-even needs 1.493 matches/s, which the card
   cannot deliver at `g = 0.37`. (`tnr modify` also refused "A6000 with 1 GPU(s)"
   at 8 vCPU, so the proof is arithmetic, not a second rental.)
6. **The 8-decision scaling artifacts are not steady state.** Their one-time
   cost is 65–82 % of the wall (session 34), and the probe now refuses to
   present them silently: it warns on the short segment and the missing warmup.
   The corrected table is produced by `--run --lanes 8 32 64 128`, not by
   scoring the old artifacts.

### Next session (session 36)

1. **Run the probe on every candidate provider** at one shape and one rate
   convention; save `runs/crforge-c4/cloud-<provider>.json` and the
   `--sweep-procs` rows. Choose by $/1M at the equation's binding side, not by
   GPU model or utilisation. **Thunder 6-vCPU A6000 is done** (1.331 ceiling,
   1.215 measured, $80.0/M measured); **8 vCPU is dominated** and is not worth a
   rental until `g` falls. Modal is not authenticated on this host yet
   (`python3 -m modal setup`); it is the way to test a CPU-rich config
   (`N_proc > G × E / g`) and a non-A6000 card.
2. **Re-measure the A6000 at 8/32/64/128 lanes with `--warmup-forward`** (session
   34 item 1). The probe is the harness; the 128-lane row is already the verdict
   shape.
3. **Then the overlapped loop on the A6000** (session 32 item 1, still open) at
   128 lanes, using the same probe to check whether the update is hidden. The
   2,048-frame OOM in §3.62 still has to be solved first.
4. **Port the four `ever_server` inference tricks into the in-process forward**
   (`self_play.py` → `bridge_session.py` → `PolicySessionV4`): fp16 + fp32
   action equality, multi-size CUDA graphs, one GPU-side LSTM state table, and
   layout/pack once. Do **not** port `net.py`'s TCP/pickle wire protocol. The
   target is 157 → 21.5–22 ms `ms.inference_ms` for ~$20/1M **physical** at
   $0.35/h; under 1,214 the same $20 is 43.4 ms. The port is not a win by
   itself: the CPU half must fall to ~108 ms at 6 vCPU (or `N_proc` rise) before
   the faster forward can be fed. Gate on action/log-prob equality and report
   the CPU half beside the forward.
5. **Native parity cadence, unchanged:** 50–150 native matches every N updates
   and after every adapter change; crforge is never a promotion gate.

## Session 34 results (2026-11-19) — the return is a value fit; the A6000's 228 ms was a statistic

**Read first:** this section, `FINDINGS.md` §3.67, and the session-33 section
below.

### What landed

- **The horizon reports the reward share of its return.**
  `tools/crforge/learning_horizon.py` now derives `reward_abs_max` per update
  from the trace or, for older artifacts, from the segment summary's
  `reward_min`/`reward_max`, and records, per run: `reward_abs_max_median`,
  `reward_share_median`/`mean`/`max`,
  `updates_reward_share_at_least_threshold` and `reward_source`.  The comparison
  carries `reward_share_median`, `reward_share_threshold` and
  `return_rankable_on_reward_share`.  `--reuse` regenerated both horizon
  artifacts from the existing runs.
- **`tools/crforge/reward_share.py`** — the shape probe at 24/60/120 decisions
  per segment, same update count and seeds, with the share's growth over the
  baseline and a conservative verdict (a shape ranks only when its median update
  and at least half its updates are reward-dominated).
- **Timing accounting in the producer.**  `tools/crforge/self_play.py` now
  records `ms_mean`, `ms_max` and `ms_first` next to the median buckets, plus
  `segment_boundary_ms`, `collection_ms_per_decision`, `accounted_ms_mean` and
  `unaccounted_ms_mean` per segment.  `--warmup-forward` (default) primes the
  sampled and decoded path on the real batch before segment 0 and restores the
  torch/numpy/CUDA RNG in a `finally`, so the action stream is unchanged
  (validated: cold vs warm at 4 lanes × 2 × 12 with a stored rollout agree on
  8 ACT / 192 eligible and on every per-segment value/log-prob/reward summary);
  `--no-warmup-forward` reproduces the old timings.
- **`tools/crforge/timing_accounting.py`** — reads producer artifacts, reports
  the median/mean reconciliation and solves `ms = S + F/decisions` from two
  segment lengths at one lane count.
- **Tests:** `test_learning_horizon.py` gains the reward-share and
  trace-vs-segment-source cases; new `test_reward_share.py` (4) and
  `test_timing_accounting.py` (4).  **Bridge suite 105 passed**; the crforge
  trainer suite 13 passed; ruff clean on the touched files.

### Measured — the reward share at 24 decisions (six 50-update runs)

`reward_share = (reward_abs_sum / rows) / |return_mean|` per update.  Same
4-lane × 24-decision × 50-update runs and seeds as session 33
(`runs/crforge-c4/learning-horizon.json`):

| policy | seed | updates w/ reward | median max \|reward\| | run max \|reward\| | median share | max share | updates ≥ 50 % | last-10 return |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `reset` | 1 | 48/50 | 1.02e-3 | 0.906 | 4.9e-3 | 1.89 | 1 | 0.4951 |
| `reset` | 2 | 48/50 | 1.01e-3 | 0.922 | 6.5e-3 | 1.68 | 2 | 0.4888 |
| `reset` | 3 | 48/50 | 1.01e-3 | 0.931 | 2.6e-3 | 0.83 | 2 | 0.4640 |
| `carry` | 1 | 48/50 | 5.8e-4 | **0.003** | 1.7e-3 | 0.02 | 0 | **0.5803** |
| `carry` | 2 | 49/50 | 9.8e-4 | 0.881 | 3.2e-3 | 2.51 | 1 | 0.3411 |
| `carry` | 3 | 48/50 | 1.01e-3 | 0.935 | 8.1e-3 | 5.25 | 1 | 0.3062 |

3-seed median share: **0.47 % `reset`, 0.43 % `carry`**; **5 of 300 updates**
reach 50 %, all terminal.  `carry` seed 1 — the one-seed leader — has no terminal,
no truncation and no reward above 0.0033 in 1,200 decisions/lane, and the highest
return of all six runs.  `return_rankable_on_reward_share: false`.

### Measured — longer windows flatten at ~2.5 %

Same `reset` cadence, 16 updates, `runs/crforge-c4/reward-share.json`:

| decisions/segment | seeds | median share | max share | updates ≥ 50 % | median max \|reward\| | wall |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 24 | 1, 2 | 8.2e-3 | 5.8e-2 | 0 / 32 | 1.01e-3 | 291 s |
| 60 | 1, 2 | **2.6e-2** | 1.5 | 5 / 32 (16 %) | 2.8e-3 | 715 s |
| 120 | 1 | **2.5e-2** | 2.8 | 3 / 16 (19 %) | 2.6e-3 | 1,631 s |

The share grows 3.1× from 24 to 60 and then is flat: the accumulated reward and
the return it is measured against grow together.  The 120-decision/seed-2 run
was stopped after 5 of 16 segments once the trend was clear; the artifact lists
it under `missing_runs` with `gate.complete: false`, and the verdict does not
depend on it.

### Measured — the A6000's "unattributed 228 ms" is one cold decision

Every bucket is a median; `collection_wall_s / decisions` is a mean.  One cold
decision of 4.9–5.5 s at **every** batch size from 8 to 128 lanes is the
difference; at 32 decisions it amortises to the 227 ms that was called
unattributed.  Two 128-lane shapes solve **F ≈ 4.0 s + 523 ms/decision = 490
ld/s** steady state against the recorded 395.8 (1.24×).
All six `gpu-forward-scaling-*` runs are 8 decisions long, so 65–82 % of each is
one-time cost; their end-to-end rates — including "0.34× at 8 lanes" — are not
steady-state results.  The CPU control (64 lanes × 2 × 8) closes the loop: the
residual from the means is **0.0 ms in all four segments**, the cold first
segment costs **+3.48 s** against the second (4,139.0 vs 3,704.6 ms/decision),
and `--warmup-forward` removes it (3,630.0 ms/decision at segment 0, first
inference no longer the max).  `runs/crforge-c4/timing-accounting-{gpu,cpu}.json`,
`runs/crforge-c4/cpu-timing-{cold,warm}.json`, `FINDINGS.md` §3.67.

### Found by running it

1. **The return's best run never took a tower.** `carry` seed 1 has the highest
   last-10 return (0.5803) and no terminal, no truncation and no reward above
   0.0033 in 50 segments. That is not a close call the mean hid; the run's whole
   reward mass is ~0.2 % of its return. The one-seed lead was a value fit.
2. **Longer windows do not fix a bootstrap-dominated return.** 24 → 60 decisions
   triples the share; 60 → 120 is flat, because the reward and the return it is
   measured against grow together. The window at 120 decisions is already longer
   than a match, and 13 of 16 updates are still not reward-dominated.
3. **The 228 ms was the statistic.** Summing median buckets against a mean wall
   invented 35 % "unattributed" work at 128 lanes and 65–82 % at the 8-decision
   scaling shapes. The producer had the data to show it — `ms_first` and
   `ms_mean` now record it.
4. **The fixed cost is batch-size independent.** 4.9–5.5 s from 8 to 128 lanes
   is setup (lazy kernels/allocator), not work that scales with rows. That is
   why it survived every shape and looked like a per-decision cost.
5. **A stopped probe is still evidence.** `--aggregate-only` reports missing
   runs and `gate.complete: false` instead of re-collecting or pretending the
   shape set is complete; the 120/seed-2 run was stopped once the flat trend
   was measured.

### Next session (session 35)

> **Updated 2026-11-16 (session 35):** item 1 is now the first live use of
> `tools/crforge/cloud_capacity.py --run --lanes 8 32 64 128`; the provider
> sweep is the new item 0 in the session-35 section. Items 2–4 are unchanged.

1. **Re-measure the A6000 with `--warmup-forward`** at 8–128 lanes and record
   `ms_mean`/`ms_first`: the 8-decision scaling table is 65–82 % one-time cost,
   so the gate-shape GPU/laptop ratio and the overlapped-loop gate both need the
   corrected steady state.
2. **Then the overlapped loop on the A6000** (session-32 item 1, still open).
3. **Native parity cadence, unchanged:** 50–150 native matches every N updates
   and after every adapter change; crforge is never a promotion gate.
4. **Do not rank a cadence on the simulator return.**  If a simulator-side
   learning signal is needed, use a statistic that observes the game (terminal
   rate, towers/crowns) over windows that contain episodes, not the GAE return.

## Session 33 results (2026-11-19) — seeds 2 and 3 reverse the horizon ranking

**Read first:** this section, `FINDINGS.md` §3.66, and the session-32 section below.

### What landed

- **`tools/crforge/learning_horizon.py` — the paired-seed block.** The artifact
  now carries `comparison.paired_seeds`: one `reset`/`carry` pair per seed, the
  winner, `carry_leads_seeds`, `reset_leads_seeds`, `mean_carry_minus_reset` and
  `seeds_agree`. A mean over seeds can hide a sign flip; this makes the flip the
  reported result. `--reuse` regenerates it from the per-run artifacts.
- **`reward_abs_max` in the trainer trace, and a retrospective reward check.**
  `reward_sum` is identically zero because the per-seat reward is antisymmetric
  (`[s, -s]`), so it was never a reward signal. `crforge_bridge/training.py` now
  records the per-update maximum absolute reward; the horizon reads that when
  present and otherwise falls back to each segment's `reward_abs_sum`, so the
  six seed-1..3 artifacts (which predate the trace field) already report **48–49
  of every 50 updates with a nonzero reward**. A diagnostic repeat of reset/seed
  2 with the new trace reproduced the curve exactly (0.2619 → 0.4888) and
  measured it: median max |reward| **1.0e-3**, seven updates ≥ **0.05**, the two
  terminals at **0.90/0.92**. Reward is present; it is just small in most
  24-decision windows (`runs/crforge-c4/horizon/reset-seed2-rewardprobe.json`).
- **Tests:** `test_learning_horizon.py` gains the agreement and split-ranking
  cases plus the null-vs-zero reward signal; the trainer test asserts
  `reward_abs_max`. The two files: **19 passed**; ruff clean.

### Measured — 3 seeds, 50 updates, one shape

4 lanes × 24 decisions, `OMP_NUM_THREADS=4`, CPU, `runs/crforge-c4/learning-horizon.json`
(per-run artifacts `runs/crforge-c4/horizon/{reset,carry}-seed{1,2,3}.json`):

| measurement | `reset` | `carry` |
| --- | ---: | ---: |
| last-10 return, seed 1 | 0.4951 | **0.5803** |
| last-10 return, seed 2 | **0.4888** | 0.3411 |
| last-10 return, seed 3 | **0.4640** | 0.3062 |
| last-10 return, 3-seed mean | **0.4827** | 0.4092 |
| last-10 range across seeds | 0.031 | **0.274** |
| first-10 mean | 0.2535 | 0.2346 |
| delta (last − first) | +0.2291 | +0.1747 |
| slope/update | 5.06e-3 | 3.79e-3 |
| ACT last-10, per seed | 7.40 / 6.17 / 11.51 % | 12.02 / 9.72 / 6.22 % |
| ACT mean | 8.36 % | 9.32 % |
| wall mean | 886.8 s | 877.6 s |
| updates / stale / gate | 150 / **0** / pass | 150 / **0** / pass |

`comparison.paired_seeds` reports **carry leads 1/3, reset 2/3, mean delta
−0.0734, seeds agree: false**.

### Found by running it

1. **The one-seed lead was an outlier, not a small edge.** Seed 1's +0.085
   becomes −0.148 and −0.158 at seeds 2 and 3. Session 31 refused to rank from
   six updates; this is the measurement that says one seed was not enough
   either. The paired-seed block exists so the next reader sees the flip without
   recomputing it.
2. **The spread is the result, not just the mean.** `reset` lands in a 0.031 band
   across seeds; `carry` spans 0.274. `carry` also has the two lowest single-seed
   slopes (1.87e-3, 1.36e-3). A cadence whose seed spread is 9× the other's
   cannot be ranked on one 4-lane match, whatever the mean says.
3. **Both cadences rise within every run, and that is all this shape supports.**
   All six deltas are positive, and 48–49 of every 50 updates contain a nonzero
   reward, but the reward is ~1e-3 against a return of ~0.4 in a typical window,
   so the boundary value bootstrap dominates the curve. The run shows that the
   value/return estimate moved; it is not a win rate, and native parity is
   unchanged and was not run.
4. **The trace's reward field was misread by its name.** `reward_sum` is zero by
   ant symmetry for every possible match, which is why the first version of the
   horizon's "no reward" check would have fired on every run. The segment
   summary's `reward_abs_sum` (and now the trace's `reward_abs_max`) is the field
   that answers the question: the reward is present in 48/50 updates of every
   recorded run, and the probe's terminal reaches 0.92.

### Next session (session 34)

1. **Report the reward share before ranking on the return.** It is non-zero in
   48–49 of 50 updates (median max |reward| 1.0e-3, terminals 0.92), but it is
   small against a ~0.4 return, so most of the curve is the boundary bootstrap.
   Decide from a per-seed table that includes `reward_abs_max` whether longer
   segments (60–120 decisions) make the reward share large enough to rank;
   otherwise treat the return as a value-fit diagnostic, not a score.
2. **Native parity cadence, unchanged:** 50–150 native matches every N updates
   and after every adapter change; crforge is never a promotion gate.
3. **Measure the overlapped loop on the A6000** at 64–128 lanes (session-32 item
   1, still open): wall, collection rate, stale KL/clip, concurrent
   forward/backward contention.
4. **Profile the ~228 ms/decision the A6000 session left unattributed** at 128
   lanes (session-32 item 4, still open).

## Session 32 results (2026-11-19) — the overlapped loop, and `reset` vs `carry` over a horizon

**Read first:** this section, `FINDINGS.md` §3.64–§3.65, and the session-31
section below.

### What landed

- **`crforge_bridge/training.py` — `OverlapUpdater`.** A second model plus a
  worker thread; `submit(rollout, segment, expected_policy_state_id)` joins the
  previous update, checks the collector still hashes to the segment's sampling
  id, copies the updated weights into the collection model in place
  (`copy_weights`, verified byte-for-byte), and starts the next update. `finish()`
  joins the last update and records the final sync.
- **Every update now records whether it was on-policy.** `_update` stores
  `sampling_policy_state_id`, `policy_state_id_at_update_start` and `stale`; the
  gate reports `stale_updates`, `on_policy_updates` and `all_updates_on_policy`.
  This is what makes a *pipelined* stale update measurable — the id-chain
  classifier in `cadence_compare.py` cannot see it, because the row's "before" id
  is the sampling id. The classifier now prefers the measured field.
- **`tools/crforge/self_play.py --overlap-update`** and the `pipeline` block on
  the artifact (boundaries, join/sync/in-flight clocks, identity checks, stale
  count, serial-equivalent wall, speedup). `--resume` copies the resumed weights
  into the collector before segment 0; `wave` + overlap is refused.
- **`tools/crforge/overlap_compare.py`** — the gate at one shape and seed list.
  Serial `reset` vs overlapped `reset`; it fails when the collection rate does
  not survive (`>= 0.9×` serial), even if the wall is slightly lower.
- **`tools/crforge/learning_horizon.py`** — `reset` vs `carry` for ≥50 updates at
  one shape and seed: return/reward curve, ACT-rate curve, slope, wall. Native
  parity is explicitly *not* run (crforge is never a promotion source).
- **Tests:** `crforge_bridge/tests/test_overlap.py` (4 tests: stable collector,
  moved-collector refusal, second-model requirement, boundary clocks);
  trainer tests for the measured stale verdict and the in-place copy; the cadence
  classifier test for the measured verdict. Bridge suite **90 passed**; native
  learning + trainer suite **46 passed / 4 skipped**; ruff clean on the touched
  files.

### Measured — the pipeline is correct and CPU-negative

4 lanes × 6 segments × 24 decisions, 2 seeds, CPU, `OMP_NUM_THREADS=4`,
`runs/crforge-c4/overlap-compare.json`:

| measurement | serial `reset` | overlapped `reset` |
| --- | ---: | ---: |
| updates / stale | 6 / **0** | 6 / **5** |
| wall | **103.8 s** | **100.7 s** (1.03×) |
| collection / update wall | 39.4 / 64.4 s | **60.9 / 89.6 s** |
| collection rate | **29.2 ld/s** | **18.9 ld/s (0.65×)** |
| abs KL on-policy / stale | 4.63e-5 / — | 6.20e-5 / **4.40e-3** |
| clip fraction stale | — | **2.92 %** |
| collection identity / weight bytes | — | **pass / pass** |
| gate | — | **fail** (collection rate) |

The pipeline hides the update structurally (its own ledger says 150.5 s serial
equivalent over 100.7 s), but under CPU contention both halves inflate, so the
honest comparison is 103.8 → 100.7 s. The tax is the wave's: 5/6 updates stale,
71× the on-policy KL, 2.9 % clip. The recorded decision is therefore *not* to
adopt it as the default; the A6000 is the shape to measure next, where the update
is the short side.

### Measured — `reset` vs `carry` over 50 updates

> **One-seed record; superseded by the three-seed result in session 33** (`reset`
> 0.483 vs `carry` 0.409, `carry` wins 1/3). See `FINDINGS.md` §3.66.

4 lanes × 24 decisions, 50 updates, seed 1, CPU, `OMP_NUM_THREADS=4`,
`runs/crforge-c4/learning-horizon.json`:

| measurement | `reset` | `carry` |
| --- | ---: | ---: |
| updates / stale | 50 / **0** | 50 / **0** |
| return: first-10 mean | 0.2280 | 0.2260 |
| return: last-10 mean | 0.4951 | **0.5803** |
| return: delta / slope | +0.2672 / 5.93e-3 | **+0.3543 / 8.14e-3** |
| return: last-5 / best-5 | 0.511 / 0.518 | **0.622 / 0.652** |
| ACT: first-10 → last-10 | 3.62 % → 7.40 % | 4.16 % → **12.02 %** |
| lane-decisions / wall | 9,600 / 899.4 s | 9,600 / 872.4 s |
| training gate | pass | pass |

The first update is identical in both runs (return 0.330, KL 7.5e-5), the control
that only the recurrence boundary differs. Both curves rise over 50 updates;
`carry` leads on the last-10 mean (+0.085), slope and both tail windows, at the
same wall (27 s is host noise). **One seed is not a ranking**, and
`value_explained_variance` is unusable at this shape (it swings to −11 on a
low-variance segment). `reset` therefore stays the default; `carry` is the
leading candidate for the next horizon run (≥2 more seeds + native parity).

### Found by running it

1. **The first smoke run failed with an in-place autograd error, and it was a
   real race.** The producer read `trainer.pending` before joining the worker's
   last update, then flushed the same rollout concurrently. The join now happens
   before any trainer state is read. The pipeline is only safe because the worker
   is the sole writer while in flight.
2. **A 3 % wall gain is not worth 5/6 stale updates.** The measured stale KL and
   clip reproduce session 31's wave numbers, which is the control: the tax is a
   property of the data age, not of this implementation.
3. **The gate had to include the collection rate.** A wall-only gate would have
   called a 3 % win a pass while the collector lost 35 % of its throughput. The
   handoff's criterion ("same lane-decisions/s") is the right one.
4. **The pipeline is a GPU shape, not a CPU one.** On this host the forward and
   the backward share cores; on the A6000 the update is 7.5× faster and the
   collector's forward is not competing for the CPU cores a backward uses. The
   same code has a bounded ~27 % ceiling there at 128 lanes × 32 decisions (and
   §3.62's 2,048-frame OOM must be solved first), so the measurement is open.

### Next session (session 33)

1. **Measure the pipeline on the A6000** at 64–128 lanes: wall, collection rate,
   stale KL/clip, and whether a concurrent forward and backward contend on one
   device. Do not adopt it from the CPU result.
2. **Extend the horizon to ≥2 more seeds and native parity.** Seed 1 already
   shows `carry` leading (last-10 return 0.580 vs 0.495, slope 8.14e-3 vs
   5.93e-3, ACT 12.0 % vs 7.4 %); `reset` stays the default until the seeds agree
   and 50–150 native matches per checkpoint back the ranking. See `FINDINGS.md`
   §3.65. **Done 2026-11-19 (native parity not run):** seeds 2 and 3 reverse the
   ranking — `reset` 0.4827 vs `carry` 0.4092, `carry` wins 1/3 and spans 0.274
   against reset's 0.031. `reset` stays the default; see the session-33 section
   and `FINDINGS.md` §3.66.
3. **Native parity cadence, unchanged:** 50–150 native matches every N updates
   and after every adapter change; crforge is never a promotion gate.
4. **Profile the ~228 ms/decision the A6000 session left unattributed** at 128
   lanes (tensorize 81 + forward 157 + decode 97 + steps 75 of a 647 ms decision).

## Session 31 results (2026-11-19) — the cadence decision: `reset` trains, `wave` is a diagnostic

**Read first:** this section, `FINDINGS.md` §3.63, and the session-30 items above.

### What landed

- **`tools/crforge/cadence_compare.py`** — the cadence gate. It runs the producer
  once per policy and seed at one shape, then classifies each update on-policy or
  stale from the trainer's own behaviour-id chain
  (`policy_state_id_before` vs the previous update's `policy_state_id_after`).
  The artifact carries per-run summaries, per-policy means, the comparison
  deltas, the shared `substrate` block (runs from different substrates are
  refused) and the exact shape. `--reuse` re-aggregates existing per-run
  artifacts without re-collecting.
- **`crforge_bridge/tests/test_cadence_compare.py`** (4 tests): a wave's second
  update is stale and a reset's is not, a trace/update count mismatch is
  refused, and the wave/stale comparison fields are what the decision reads.
- **`ROADMAP_CRFORGE.md` §6** carries the proven command.

### Measured

4 lanes × 6 segments × 24 decisions, 2 seeds, CPU, `OMP_NUM_THREADS=4`,
`runs/crforge-c4/cadence-compare.json`:

| measurement | reset | carry | wave(2) |
| --- | ---: | ---: | ---: |
| updates / stale | 6 / **0** | 6 / **0** | 6 / **3** |
| lane-decisions / ACT | 1,152 / 3.52 % | 1,152 / 3.30 % | 1,152 / 3.63 % |
| abs `approx_kl` on-policy | 4.63e-5 | 2.77e-5 | 3.36e-5 |
| abs `approx_kl` stale | — | — | **5.38e-3** |
| `clip_fraction` on-policy | **0** | **0** | 0 |
| `clip_fraction` stale | — | — | **3.04 %** |
| updates per sampling id | 1.0 | 1.0 | 2.0 |
| collection / update / wall | 40.0 / 65.9 / 105.9 s | 40.2 / 66.2 / 106.5 s | 39.9 / 65.8 / 105.7 s |
| training gate | pass | pass | pass |

Wave per seed: stale |KL| 1.02e-2 / 5.88e-4 and stale clip 1.91 % / 4.17 %; the
stale half never clips 0. The run's first update matches reset's and
carry's first update exactly (7.46e-5 at seed 1, 4.93e-5 at seed 2), because all
three sampled the same first segment with the same weights.

### Found by running it

1. **The wave tax is structural and it is on the stale half.** The first update of
   a wave is on-policy by construction; every later one replays data sampled by
   weights an earlier update in the same wave replaced. The KL and the clipping
   are both there, in both seeds.
2. **Wave buys neither throughput nor sample reuse.** Six updates for six segments
   in every policy, and the walls are within noise. `updates_per_sampling_id` of
   2.0 is data age, not reuse; the second update sees different data, not the
   same data twice.
3. **`reset` vs `carry` is not decided here — and cannot be.** Both are on-policy
   and neither clips; carry's |KL| is if anything lower, which is a property of
   the trace, not a learning claim at 6 updates and 1e-5. The ranking needs a
   learning horizon (≥50 updates, fixed seed, native parity every N updates), not
   this gate.
4. **The wall split names the next lever.** Collection 40 s vs update 66 s at this
   shape: the optimizer is 62 % of the CPU wall. No cadence overlaps them today,
   because every policy flushes synchronously. The wave's structure is the only
   one that *could* overlap, but that needs a frozen weights copy (or a second
   model) and a worker thread — a throughput change, not a state policy.
5. **Shape discipline cost a session.** The first attempt at 8 lanes × 40
   decisions put this 14 GB host into swap and killed the session twice (producer
   peak ~5.8 GB against 4 GB of swap). The comparison runs detached and resumable
   with `--reuse`; the recorded shape is the smaller one.

### Next session (session 32)

1. **Overlap collection with the update.** The only cadence change with a measured
   wall payoff. Gate: same lane-decisions/s with the update hidden behind the next
   wave, on CPU and CUDA, and the behaviour identity of the collecting pass
   unchanged during the overlap. **Done 2026-11-19:** `--overlap-update` is
   implemented and gated; on CPU it wins 3 % wall but loses 35 % collection rate
   and 5/6 updates are stale, so the gate **fails**. The GPU measurement remains
   open. See the session-32 section and `FINDINGS.md` §3.64.
2. **Rank `reset` against `carry` on a learning horizon.** Same shape and seed,
   ≥50 updates, reward/return and native parity every N updates; the 6-update
   trace above is not a learning claim. **Done 2026-11-19 (native parity not
   run):** `tools/crforge/learning_horizon.py` runs 50 updates for both policies;
   see the session-32 section and `FINDINGS.md` §3.65.
3. **Profile the ~228 ms/decision the A6000 session left unattributed** at 128
   lanes (tensorize 81 + forward 157 + decode 97 + steps 75 of a 647 ms decision).
   That is the per-decision Python packet, which §3.62 named as the ceiling.
4. **Native parity cadence, unchanged:** 50–150 native matches every N updates and
   after every adapter change; crforge is never a promotion gate.

## Session 29 results (2026-11-19) — the C4 loop trains: one optimizer, a stated state policy

**Read first:** this section, `FINDINGS.md` §3.60, and the session-28 section
below.

### What landed

- **`crforge_bridge/training.py` — `CrforgeTrainer`.** One AdamW for the whole
  run (it is created once and never rebuilt), an explicit update-boundary
  policy, a reward/return trace per update, the two-update gate, and the
  loop-counter block a checkpoint saves. `tools/crforge/self_play.py` now only
  collects, steps, and asks the trainer to advance.
- **`--update-state-policy {carry,reset,wave}`.** `reset` (default) zeroes the
  recurrent state after each update; `carry` keeps it (deployed inference shape,
  session-28 behaviour); `wave` freezes the behaviour weights for
  `--wave-segments` segments, updates at the wave end, then resets. A partial
  wave at the run end is flushed and recorded. Each stored segment still gets
  its own update, because a segment cut is a truncation with its own bootstrap.
- **The gate is two consecutive finite updates.** `training.trace` records
  `reward_sum`, `return_mean/std`, value explained variance, `approx_kl`,
  `clip_fraction`, `gradient_norm`, the optimizer step and the behaviour hash
  before/after; `training.pass` needs `updates >= 2`, every row finite, every
  identity check passed, and the checkpoint (when saved) to reload to the same
  weight bytes. `training.behaviour_ids` and `distinct_behaviour_ids` show the
  policy moved.
- **`--save-checkpoint` writes a V4 checkpoint** with the persistent optimizer,
  `update_step = source + updates` and `training_stage="crforge-c4-ppo"`, then
  reloads it with `load_policy_v4` and compares weight digests. 203 MB per save
  (weights + Adam moments), so it is a local `runs/` artifact.
- **New PPO knobs** (`--ppo-value-coefficient`, `--ppo-entropy-coefficient`,
  `--ppo-max-grad-norm`, `--ppo-adam-eps`, `--ppo-weight-decay`) with the native
  trainer's values, and `--ppo-learning-rate` now defaults to the native
  **1e-5**.
- **Artifact schema `crforge-self-play.v3`**: per-segment `updates` replace the
  singular `update`, and a top-level `training` block carries the config, trace,
  behaviour ids and checkpoint record. `rollout.update` stays as the aggregate
  the old gate read.
- **Tests:** `native_runner/tests/test_training_v4_crforge_trainer.py` (7
  tests). `test_training_v4_learning.py` plus the new file: **40 passed / 4
  skipped**; bridge suite **74 passed**; ruff clean.

### Measured

Reset policy (the default), `runs/crforge-c4/self-play-train-reset.json`, 8
lanes × 4 segments × 64 decisions:

| measurement | value |
| --- | ---: |
| lane-decisions / ACT | **4,096 / 139 of 3,987 (3.49 %)** |
| illegal / faults / rejected | **0 / 0 / 0** |
| updates / optimizer steps / behaviour ids | **4 / 4 / 4** |
| collection rate | 47.6 lane-decisions/s |
| update wall | 44.6 / 46.0 / 45.1 / 44.9 s |
| `return_mean` trace | 0.227 → 0.258 → 0.296 → 0.256 |
| `value_explained_variance` trace | −0.001, −1.03, 0.258, 0.304 |
| checkpoint update step / reload | **279** / **digest match** |

Wave policy, `runs/crforge-c4/self-play-train-wave.json`, 8 lanes × 4 segments ×
48 decisions, `--wave-segments 2`:

| measurement | value |
| --- | ---: |
| lane-decisions / ACT | **3,072 / 103 of 2,944 (3.50 %)** |
| illegal / faults / rejected | **0 / 0 / 0** |
| updates / optimizer steps / behaviour ids | **4 / 4 / 4** |
| collection under one frozen id | **2 ids** (`fb4acf…` updates 1–2, `b85a91…` 3–4) |
| update wall | 66.9 s at seg 1, 65.9 s at seg 3; 0 at seg 0/2 |
| `return_mean` trace | 0.253 → 0.198 → 0.263 → 0.278 |
| `approx_kl` / `clip_fraction` | 4.1e-5, **−7.8e-4**, 9e-6, **−1.4e-3** / 0, **4.7 %**, 0, **4.8 %** |

Autoreset lanes with `carry`, `runs/crforge-c4/self-play-train-autoreset-carry.json`,
4 lanes × 2 segments × 60 decisions, `--max-ticks 600`:

| measurement | value |
| --- | ---: |
| lane-decisions / ACT | **960 / 28 of 795 (3.52 %)** |
| mid-rollout `episode_start` / truncations / resets | **8 / 8 / 4** |
| updates / behaviour ids | **2 / 2** |
| `return_mean` / value explained variance | 0.248 → 0.159 / −0.194, 0.109 |
| `approx_kl` / `clip_fraction` | 6.1e-5 / 4e-6, 0 / 0 |
| rejected | **0** |

Forced probe in the gate set, `runs/crforge-c4/self-play-two-micro-probe.json`
(8 lanes × 40 decisions, `--force-second-micro alternate --no-rollout`): **22
forced, 18 shifted, 4 dropped, 0 illegal, gate pass**, and `training.enabled`
false because it stores no rollout.

### Found by running it

1. **The learning rate default was the whole collapse.** `1e-4` (10× the native
   trainer) took ACT from 45 → 14 → 0 → 0 of ~1,000 eligible lane-decisions in
   four updates. The loop was correct; the rate was not the native one. The
   `1e-4` artifact is kept as evidence
   (`runs/crforge-c4/self-play-train-reset-lr1e-4.json`).
2. **A wave is slightly off-policy by construction.** The two updates in a wave
   share the sampled weights, but the second replays data the first already
   moved away from: KL −7.8e-4 and clip fraction 4.7 % in wave 1. Small, real,
   and the price of one optimizer over batched segments.
3. **The checkpoint reload is a cheap, strong gate.** The saved payload carries
   the Adam moments (~203 MB); reloading it and comparing weight bytes is what
   proves `--save-checkpoint` wrote the trained policy and not the seed.
4. **Wave mode holds rollouts in memory.** Two 48-decision segments at 16 rows
   are ~86 MiB pending per wave; the update wall then lands in one segment's
   ledger (the artifact keeps the collection and update clocks separate).

### Next session (session 30)

1. **Resume a run.** The checkpoint carries the optimizer and loop counters;
   load it with `load_actor_critic_checkpoint`, continue `update_step`, and gate
   a resumed run on the same two-update trace. **Done 2026-11-19:** `--resume`
   loads the Adam moments and the counters, rebases `base_update_step`, and the
   gate reads `updates_this_run`; source 275 → 277, resume 277 → 279
   (`runs/crforge-c4/self-play-resume.json`). See the decision entry at the top
   and `FINDINGS.md` §3.61.
2. **CUDA path + throughput.** Still CPU-only here (`torch 2.5.1+cpu`, no
   `nvidia-smi`). On the A6000, run the reset gate shape at 8 and 32 rows and log
   lane-decisions/s plus the update's forward/backward split (the update, not
   collection, is now the larger wall). **Done 2026-11-19:** the update is
   **7.5× faster** (5.5 s vs 44.9 s at 1,024 frames) and the collection is
   **slower** at the gate shape (26.3 ld/s vs 47.6) — the box wants one process
   at 64–128 lanes, where it reaches 395.8 ld/s. Three device defects found and
   fixed. See the top decision entry and `FINDINGS.md` §3.62.
3. **Native parity cadence, unchanged:** 50–150 native matches every N updates
   and after every adapter change; crforge is never a promotion gate.
4. **Keep the forced probe in the gate set** (`--force-second-micro`, paired with
   `--no-rollout`): the sampled policy still emits one micro-action.
5. **Decide the wave/update cadence for real training.** The wave buys frozen
   behaviour data and pays with in-wave off-policy drift; the reset policy buys
   clean identity and pays with a recurrence cut every segment. Pick one from
   measurement, not from this gate. **Done 2026-11-19:** `reset` stays the
   training default, `carry` is the deploy-shape candidate for a learning-horizon
   run, and `wave` is a diagnostic — its stale half clips 3.0 % against 0 and
   costs 160× the on-policy KL with no wall or reuse gain. See the decision entry
   at the top, the session-31 section and `FINDINGS.md` §3.63.

## Session 28 results (2026-11-19) — C4's rollout goes through the update

**Read first:** this section, `FINDINGS.md` §3.59, and the session-27 section
below.

### What landed

- **`distributed_ppo_update_v4` accepts mid-rollout episode starts.**
  `_episode_segments(episode_start)` returns the contiguous slice bounds; a slice
  starts on a boundary row, where `episode_start` zeroes the lanes that ended.
  `_episode_chunks` cuts each slice into `sequence_chunk_steps` TBPTT chunks, so
  a chunk can never straddle a reset. The old "must contain one episode each"
  rejection is gone; the fused replay's own `validate` guard still refuses a
  mid-reset *within one call*, which is the invariant the split preserves.
- **`crforge_bridge/rollout.py` escapes inference mode for the stored
  log-probability and value.** The producer samples under `inference_mode`;
  `detach`/`reshape` keep that tensor kind and PPO's backward refuses it. A
  `_rollout_float` helper clones outside inference mode (as
  `TensorRecordV4.to_storage` does) and checks finiteness.
- **`tools/crforge/self_play.py --ppo-update`** runs one
  `distributed_ppo_update_v4` over each finalized segment's `StoredRolloutV4`, on
  the same reward, GAE and validation as the native path. New settings:
  `--ppo-epochs`, `--ppo-lane-minibatch`, `--ppo-sequence-chunk`,
  `--ppo-clip-range`, `--ppo-learning-rate`. Fresh AdamW per segment (gate only);
  a real trainer keeps one.
- **Weights-hash behaviour identity.** `_policy_state_id` hashes the checkpoint id
  and every parameter/buffer at the segment start, so an update between segments
  changes the next segment's identity. Confirmed in the autoreset artifact:
  `3835a22f…` then `899ef703…`.
- **Artifact schema stays `crforge-self-play.v2`**, adds `rollout.update`
  (`segments_updated`, `mid_rollout_episode_starts`, `multiple_episodes_per_lane`,
  per-segment metrics), `rollout.totals.mid_rollout_episode_starts`,
  `behavior_state_ids`, per-segment `update_wall_s` / `collection_wall_s`, and
  `gate.update_pass` folded into the run's `pass`.
- **Tests:** `test_distributed_ppo_replays_a_mid_rollout_episode_start` (two lanes,
  reset at step 2, `sequence_chunk_steps=3`, `validate=True`, `approx_kl 0`) and
  `test_store_escapes_inference_mode_for_the_ppo_update` (store built under
  `inference_mode`, then a backward through its tensors). Native learning suite
  **37 passed / 4 skipped**; bridge suite **74 passed**; the new bridge/producer
  modules and `distributed_ppo.py` are ruff clean (the native test and `model.py`
  files keep their pre-existing import-sort warnings).

### Measured

Gate-shape update, `runs/crforge-c4/self-play-update.json` (8 lanes × 1 segment ×
64 decisions):

| measurement | value |
| --- | ---: |
| lane-decisions | **1,024** (64 × 16 rows) |
| ACT / eligible | **45 / 983 (4.58 %)** |
| illegal / faults / rejected | **0 / 0 / 0** |
| update frames / epochs | **1,024 / 1** |
| update wall | **51.9 s** |
| collection wall / rate | 27.0 s / **37.9 lane-decisions/s** |
| full wall (collect + update) | 78.9 s |
| `approx_kl` / `clip_fraction` | **2.9e-5 / 0** |
| gradient norm | 1.147 |

Multi-episode lane, `runs/crforge-c4/self-play-update-rollout.json` (8 lanes ×
2 segments × 60 decisions, `--max-ticks 600`):

| measurement | value |
| --- | ---: |
| lane-decisions | **1,920** |
| ACT / eligible | **56 / 1,559 (3.59 %)** |
| illegal / faults / rejected | **0 / 0 / 0** |
| mid-rollout `episode_start` rows | **16** (8 lanes × 2 seats, segment 1) |
| mid-rollout truncations | **16 rows** (8 lanes) |
| updates | **2**, 960 frames each |
| update wall | **87.9 s** (42.8 + 45.1) |
| collection wall / rate | 39.5 s / 48.6 lane-decisions/s (not a throughput claim) |
| `approx_kl` | **2.8e-5 / 1.0e-6** |
| `clip_fraction` | 0 / 0 |
| gradient norm | 1.298 / 1.247 |
| behaviour ids | **2** (`3835a22f…`, `899ef703…`) |

The plain gate shape and the autoreset shape are different runs; the first is the
"one update over the stored gate-shape rollout" gate, the second is the path a
lane takes when the engine cuts and autoresets it mid-segment.

### Found by running it

1. **The inference-tensor bug was real and would have blocked every update.** The
   store validated shape and finiteness but never exercised autograd; the first
   backward failed. It is now covered by a unit test, and the producer's stored
   tensors are ordinary training data.
2. **The gate-shape update is not a learning claim.** One epoch from the behaviour
   policy gives `approx_kl ≈ 1e-5` and `clip_fraction 0`; segment 1 of the
   autoreset run even has negative `value_explained_variance` (−0.59). That is
   expected on 60–64 steps with one update each; the gate is that the replay,
   reward, GAE and backward are correct.
3. **Updating between segments changes the behaviour.** The producer carries the
   recurrent state and previous-action record across the update. The store now
   records the weight hash per segment, but a real trainer still has to choose
   whether to reset the recurrence at an update boundary or collect a whole wave
   with a frozen policy.
4. **Collection throughput is unchanged by storage/update code.** 37.9
   lane-decisions/s on the plain gate shape matches session 27's 37.8; the update
   is a separate wall cost (51.9 s for 1,024 frames) that the artifact reports
   separately rather than folding into the producer rate.

### Next session (session 29) — make it a training loop

> **Session 29 did items 1 and 4** (`FINDINGS.md` §3.60): `crforge_bridge/training.py`
> keeps one AdamW for the run, `--update-state-policy` states the recurrence
> boundary (`reset`/`carry`/`wave`), and the gate is two consecutive finite
> updates with a reward/return trace and a checkpoint reload. Item 2 (CUDA) needs
> a GPU box; item 3 (parity cadence) is unchanged. See the session-29 section
> above.

1. **Persistent optimizer + state policy.** Keep one AdamW across segments, and
   choose explicitly between resetting the recurrence at an update boundary and
   collecting whole waves with a frozen behaviour model. Gate: two consecutive
   updates and a finite reward/return trace. **Done 2026-11-19.**
2. **CUDA path + throughput.** Still CPU-only here (`torch 2.5.1+cpu`, no
   `nvidia-smi`). On the A6000, run the same gate shape at 8 and 32 rows and log
   lane-decisions/s plus the update's forward/backward split.
3. **Native parity cadence, unchanged:** 50–150 native matches every N updates
   and after every adapter change; crforge is never a promotion gate.
4. **Keep the forced probe in the gate set** (`--force-second-micro`, paired with
   `--no-rollout`): the sampled policy still emits one micro-action. **Done
   2026-11-19** (22 forced, 18 shifted, 4 dropped, 0 illegal).

## Session 27 results (2026-11-19) — C4 stores a rollout; the native reward and the second micro-action

**Read first:** this section, `FINDINGS.md` §3.58, and the session-26 section
below.

### What landed

- **`crforge_bridge/rollout.py`** — the C4 rollout store. Per decision it
  retains the stacked observation, the sampled `ActionSequenceV4`, the behaviour
  log-probability and value, the reward (`(lanes, seats)`, flattened to
  `lane * seats + seat`) and the per-lane `episode_start` / `terminated` /
  `truncated` masks. `finalize(boundary_value=...)` returns a validated
  `StoredRolloutV4`, so C4 shares the native GAE and minibatch contract instead
  of growing a second rollout format.
- **`PotentialShapingReward`** — `BattleEnvV1._rewards` from the vectorized
  facts: terminal result + potential shaping (crowns, king weight 3, princess 1,
  antisymmetric, zero terminal potential), with `CrforgeRewardConfig` pinning
  `shaping_beta` and `gamma_per_decision`. A `max_ticks` cut stores a zero
  shaping term because the lane has already autoreset.
- **`rollout_next_values`** — next-step values with the segment-end truncation
  bootstrap and no trace across a terminal; a mid-rollout time-limit cut
  bootstraps from zero.
- **`tools/crforge/self_play.py`** — the post-window facts encode moved to the
  end of the decision (it is both the reward state and the next observation);
  the terminal result is read on the tick it is written, before the autoreset
  clears it; each segment does one boundary forward and finalizes its rollout;
  new flags `--no-rollout`, `--shaping-beta`, `--gae-lambda`,
  `--force-second-micro {off,shift,drop,alternate}`. Artifact schema is now
  `crforge-self-play.v2` with a `rollout` block and a `reward_result_mismatches`
  gate.
- **The forced two-action probe** appends a *different* legal play from the open
  candidates at the first play's own delay bin: shift when the first play runs
  before the window's last tick, drop when it already runs there. It is refused
  with rollout storage (the stored sampled action would not be what ran).
- **`crforge_bridge/tests/test_rollout.py`** — 7 tests, including the native
  `_rewards` differential and a live inference-mode record.

### Measured

Gate shape with storage, `runs/crforge-c4/self-play-rollout.json`:

| measurement | value |
| --- | ---: |
| decisions / lane-decisions | 640 / **10,240** |
| ACT / eligible | **429 / 9,877 (4.34 %)** |
| illegal / faults / rejected | **0 / 0 / 0** |
| reward vs engine-terminal mismatches | **0** |
| lane-decisions per second | **37.8** (probe-only: 39.0) |
| wall | 270.7 s (probe-only: 262.4 s) |
| tensorize / forward / decode / five steps | 19.2 / **355.0** / 3.4 / 6.7 ms |
| post-window facts / reward + store write | 1.83 / 3.18 ms |
| stored | 64 × 16 rows, 57.2–57.4 MiB/segment, **573.1 MiB** |
| episode starts / terminals / boundary truncations | 16 / 0 / 16 |
| GAE | finite, advantage mean −0.068, std 0.110 |

Autoreset store (`--count 4 --segments 2 --decisions-per-segment 60
--max-ticks 600`, `runs/crforge-c4/self-play-autoreset-rollout.json`): **4
autoresets**, **8 mid-rollout truncations**, **8 `episode_start` rows at the next
decision**, 0 terminals, 0 illegal, GAE finite.

Second micro-action (`--count 8 --decisions-per-segment 40
--force-second-micro alternate --no-rollout`,
`runs/crforge-c4/self-play-two-micro.json`): **22 forced, 18 shifted, 4 dropped,
0 illegal, gate pass**.

### Found by running it

1. **A duplicate micro-action is illegal.** The first forced probe repeated the
   sampled play one tick later; the engine refused it because the queued hand
   slot is closed at the next tick. The probe now appends a different slot.
2. **The reward/result agreement check earns its keep.** The producer asserts
   `vector.rewards == terminal result` on every ended lane; it is now a gate
   field (`reward_result_mismatches`, 0 in both runs).
3. **Storage is a real resource:** ~0.9 MiB per decision at 16 rows. A
   64-decision segment is ~57 MiB, so a store must be finalized per segment (as
   the producer does) rather than accumulated.

### Next session (session 28) — make the rollout updatable

1. **PPO update over an episode-boundary rollout.** `distributed_ppo_update_v4`
   rejects an `episode_start` after step 0; either teach it per-lane episode
   boundaries (`evaluate_recurrent_sequence` already takes `episode_start` per
   step and GAE already stops the trace), or have the producer end a lane at its
   terminal. Gate: one update over the stored gate-shape rollout, 0 rejected.
   **DONE in session 28:** the update splits at every boundary row and the
   producer runs it with `--ppo-update`; see the session-28 section above.
2. **CUDA path + throughput.** The forward is still 88 % of wall (355 ms at 16
   rows on CPU). Run the same gate shape on the A6000 and with MPS, and log
   lane-decisions/s at 8 and 32 rows.
3. **Native parity cadence, unchanged:** 50–150 native matches every N updates
   and after every adapter change; crforge is never a promotion gate.
4. **Keep the forced probe in the gate set.** The sampled policy still emits one
   micro-action, so `--force-second-micro` is the only run that exercises the
   collision/drop path.

## Session 26 results (2026-11-19) — the C4 producer loop and its gate

**Read first:** this section, `FINDINGS.md` §3.57, and the session-25 section
below.

### What landed

- **`tools/crforge/self_play.py`** — the crforge self-play producer. One
  `VectorBattle` (`--count` lanes), the checkpoint's model, two
  `VectorTensorizer`s and two `VectorActionDecoder`s. Per decision: `reset_rows`
  for autoreset lanes → `encode` → two stacked `tensorize` calls → interleave to
  `lane * 2 + seat` → **one** `sample_for_ppo_rollout` with a `count * 2`
  recurrent state and a per-row `episode_start` → two decodes + `record_action`
  → five engine steps. It counts env-steps/s, facts/tensorize/inference/decode
  ms, ACT rate, micro-action timings, resets, illegals and faults.
- **`plan_window_ticks`** (`crforge_bridge/vector_actions.py`) — the second
  micro-action's timing. The engine is stepped one tick at a time, so each
  micro-action is given at `offset - 1` inside the five-tick window; a collision
  shifts the later action one tick, a full window drops it, and both are
  counted. A checkpoint whose window is shorter than its delay bins is refused.
- **The autoreset plumbing the loop needs:**
  `crforge_bridge/vector_observation.py` tracks its tick offset **per lane** and
  clears that lane's derived `age_ms`/velocity when a lane restarts;
  `VectorTensorizer.reset_rows(mask)` clears only the lanes that started a new
  episode.
- **Owned previous-action record** — `record_action` used to leave an inference
  tensor in the record when sampling inside `inference_mode`, which made
  `reset_rows` (and any future PPO write) raise. `_empty_previous_action` and the
  sampled `gate`/`micro_action_count` copies are now built under
  `torch.inference_mode(False)`.

### Measured — the gate shape passes

`runs/crforge-c4/self-play.json` (8 lanes × 2 seats, 10 segments × 64 decisions,
validated every decision, CPU):

| measurement | value |
| --- | ---: |
| decisions / lane-decisions | 640 / **10,240** |
| ACT lane-decisions / eligible | **429 / 9,877 (4.34 %)** |
| illegal / faults / rejected | **0 / 0 / 0** |
| lane-decisions per second | **39.0** |
| env steps per second (lane-ticks) | **97.6** |
| wall (10 segments) | 262.4 s |
| facts + reset rows, ms/decision | 1.73 |
| stacked tensorize (both seats), ms/decision | 13.41 |
| **recurrent forward, ms/decision** | **359.7** |
| both decodes + `record_action`, ms/decision | 3.19 |
| five engine steps, ms/decision | 6.36 |

`runs/crforge-c4/self-play-autoreset.json` (4 lanes, 2 × 60 decisions,
`--max-ticks 600`): **4 autoresets, 29/790 eligible lanes acted (3.67 %),
0 illegal, 0 faults**. The gate shape starts at tick 90 and covers only 3,200
ticks, so it never reaches a terminal; the short-episode run is the one that
proves a mid-window autoreset does not send the old episode's actions into the
new one.

**The forward is 94 % of the timed packet** (~22.5 ms/actor row; 359.7 ms
against 24.7 ms for facts, tensorize, decode and stepping together), and 88 %
of the 410 ms wall per decision. A first run of the same shape measured 43.4
lane-decisions/s (235.8 s); the spread between runs is host noise, not a code
change. Facts, tensorize, decode and stepping are done; the
next C4 lever is GPU throughput and the rollout store. For scale: the native PPO
worker is **29 lane-decisions/s** (1 engine × 8 lanes, §3.37) and this CPU
producer is **39.0** on the same model.

Two honesty notes on the artifact: the sampled policy chose **no two-action
decisions**, so the collision/drop counters are 0 (the rule is unit-gated), and
the tool stores no rollout — it is a collection/throughput probe, and the
artifact says so.

### Next session (session 27) — make C4 a trainer

> **Session 27 did items 1 and 4** (`FINDINGS.md` §3.58): the producer stores a
> validated `StoredRolloutV4` per segment behind the native reward, and
> `--force-second-micro` exercises the shift/drop path through the engine. Item
> 2 (CUDA) needs a GPU box; item 3 (parity cadence) is unchanged. The update
> itself is still open — it has to honour an `episode_start` after step 0. See
> the session-27 section above.

1. **Rollout storage.** Store the per-decision packet (batch, state, sampled
   action, log-prob, value, reward, `episode_start`) and compute returns/GAE on
   autoreset boundaries. The recurrent state is already per row and the previous
   action is already the training record. **Done 2026-11-19.**
2. **CUDA path + throughput.** Run the same gate shape on the A6000 (and with
   MPS, the RoyaleFish `ever_*` recipe). The forward is 88 % of CPU wall, so
   batch/device are the only remaining knobs; log lane-decisions/s at 8 and 32
   rows.
3. **Native parity cadence, unchanged:** 50–150 native matches every N updates
   and after every adapter change; crforge is never a promotion gate.
4. **Second micro-action exercise.** The sampled policy produced none; either
   find a checkpoint/state that emits two actions or drive the loop with a forced
   two-action sequence so the collision/drop counters are exercised end to end.
   **Done 2026-11-19** (`--force-second-micro`: 22 forced, 18 shifted, 4
   dropped, 0 illegal).

## Session 25 results (2026-11-19) — C4's missing half: the batched action decode

**Read first:** this section, `FINDINGS.md` §3.56, and the session-24 section
below.

### The finding first: there is no C4 producer to wire into yet

Session 24's next-session note said to replace `producer.py`'s per-lane
`tensorize`/`record_action` pair with the stacked call. That is not the shape of
the tree:

- `native_runner/training/v4/producer.py` is the **IL replay** producer. It drives
  native `BattleEnvV1` lanes and its tensorizers consume `ObservationV1`
  contracts, which is the layer crforge removed. A stacked `VectorTensorizer`
  needs `VectorObservationBatch`, so it cannot be dropped into that file.
- The only crforge loops are the evaluation/parity tools; the PPO worker
  (`async_cluster_self_play.py`) also tensorizes native `ObservationV1` rows.

So C4 needs a **new crforge self-play producer**, and the missing half of it was
the action path: C3-B stacks the observation, but nothing turned the model's
sampled `ActionSequenceV4` back into a `VectorBattle` action. The stock decoder
is per lane, over `ObservationV1`, with contract validation. That half landed
this session.

### What changed

- **`crforge_bridge/vector_actions.py`** — `VectorActionDecoder.decode(batch,
  sequence, candidates, seat=...)` turns a stacked sequence into the engine's
  flat index in one NumPy pass: `0` waits,
  `1 + slot * rows * cols + row * cols + col` plays a hand slot on a seat tile.
  It emits one row per micro-action with the stock decoder's execute offset
  (`1 + delay_offset_ms // 50` ticks), the candidate row it resolved, and a
  `masked` flag from the engine's own mask.
  - **The frame rule is derived, not hard-coded.** `model -> game` follows the
    tensorizer perspective; `game -> engine` follows `Schema.toSeatX/toSeatY`
    (x for seat 0, y for seat 1). Under the default perspective that composes to
    one **x flip for both seats**; the game cell `(6, 10)` of the native
    cross-check decodes to seat `(11, 10)` (seat 0) and `(6, 21)` (seat 1).
  - **Envelope:** deploy candidates only. The bridge emits no ability source
    and no Mirror runtime, so an ABILITY/Mirror candidate or a non-GRID target
    raises instead of guessing. Subcell offsets and the second play's timing are
    not in the vector action index; both are recorded as C4 fidelity pins.
  - `engine_cell_from_game` / `engine_action_index` /
    `engine_action_from_native` are shared by the gate and the tests, so the
    oracle cannot drift from the decode.
- **`tools/crforge/vector_decode.py`** — the gate. 256 real learner-deck lanes,
  sampled sequences per seat (one micro-action, plus a second at the 100 ms bin
  when a second candidate is open), every index/offset/candidate row compared
  with the stock per-lane decoder on the same facts; then the decoded indices
  are **stepped into the engine** so a refusal or illegal count fails the run.
- **`crforge_bridge/tests/test_vector_actions.py`** — 7 tests (seat frame pin,
  both seats against the stock decoder over a played sequence, a two-micro row
  with its delay offset, WAIT rows, unknown UID, closed-mask reporting). Bridge
  suite **62 tests**, ruff clean.

### Measured (256 lanes × 40 decisions, `--seats 0 1`)

`runs/crforge-c4/vector-decode.json`:

| measurement | combined | seat 0 | seat 1 |
| --- | ---: | ---: | ---: |
| lane-decisions identical to the stock decoder | **3,008/3,008** | 1,504/1,504 | 1,504/1,504 |
| decoded deploy rows | 1,417 | 710 | 707 |
| two-micro-action rows | 629 | 317 | 312 |
| …accepted by the stock oracle | 567 | 281 | 286 |
| mask-closed indices | **0** | 0 | 0 |
| engine illegal / refusals | **0 / 0** | | |
| batched decode, ms/lane-decision | **0.000232** | 0.000213 | 0.000222 |
| per-lane stock decode, ms/lane-decision | 0.758 | | |
| speedup on the decode half | **3,266×** | | |

The 62 oracle refusals are the live shadow contract rejecting a sampled
two-play pair; they are counted, not compared. Every accepted lane matched on
index **and** offset, and every applied index was open in the engine mask.

### Next session (session 26) — the C4 producer loop

1. **Build `tools/crforge/self_play.py`.** One `VectorBattle` (8 lanes), the
   checkpoint's model, two `VectorTensorizer`s and two `VectorActionDecoder`s.
   Per decision: facts → two stacked `tensorize` calls → stack the 16 actor rows
   → one `sample_for_ppo_rollout` with a `lanes * 2` recurrent state and a
   per-row `episode_start` → two decodes → `engine_actions`, then `step`.
   Log env-steps/s, facts/tensorize/inference/decode ms, and `illegal`,
   `refusals`, `eligible_act_rate`.
2. **Decide the second micro-action's timing.** The vector action index is one
   action per seat per step. Either step one tick at a time (`decision_ticks=1`,
   apply slot `k` at its execute offset, which fits bins 0–3 inside the 5-tick
   window) or close the second play in this envelope and record the pin. Do not
   drop it silently; the gate must show which choice was made.
3. **C4 gate, unchanged:** 8 lanes/process, 2–4 processes, 10 segments,
   `rejected 0`, `eligible_act_rate > 0`, native eval not degrading. Include
   NVIDIA MPS (the RoyaleFish `ever_*` recipe).
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 24 results (2026-11-19) — frame fix closed; both seats gated; C4 unblocked


**Read first:** this section, the top entries, and `FINDINGS.md` §3.55.

### What changed

- **`tools/crforge/batched_tensorize.py`** now takes `--seats 0 1`: one
  `VectorTensorizer` and one stock tensorizer per actor, per-seat
  `reference_scalar_observation(..., seat=...)` and
  `reference_action_mask(..., seat=..., model_frame=False)`, and combined timing
  (facts once + both tensorizes + both record calls) per lane-decision. The
  artifact is schema `v2` with `by_seat` equivalence and timing blocks.
- **`crforge_bridge/vector_rows.py`** fixes two seat-1 defects the gate found:
  - `VectorRelationBuilder.build(...)` now takes the `VectorTowerSection` and
    resolves tower sources/targets through `tower_index`. The stock relation
    token for a tower is its **model row**, not its native id; under a flip
    they differ, so every seat-1 tower edge was wrong. The section comparators
    also gained `seat`, and the relation/tower tests now gate actor 1.
  - Group mean velocity now adds `0.0` before the scale divide, reproducing the
    stock accumulator's signed-zero rule (`0.0 + -0.0 == +0.0`). `torch.equal`
    accepts the two; `batch_digest` hashes bytes, which is how the gate caught
    it.
- **`tools/crforge/frame_crosscheck.py`** — the native cross-check. It plays
  the same card at the same game point in the x86_64 engine and in a one-lane
  `VectorBattle`, advances one tick at a time, and compares the first spawned
  frame. `runs/crforge-c3/frame-crosscheck.json` is **status pass**:
  native and crforge both at `(5997, 10212)`, `(6499, 11079)`, `(6998, 10212)`;
  facts-layer pending `(6499, 10500)` (game frame) for a play addressed with
  seat cell `(11, 10)`.
- `test_vector_tensorize.py` parameterizes the played-sequence digest over both
  seats; `test_vector_rows.py` adds the actor-1 relation/tower gate, the tower
  token-map pin and the signed-zero velocity test. Bridge suite **55 tests**,
  ruff clean.

### Measured (256 lanes × 40 decisions, `--seats 0 1`)

`runs/crforge-c3/batched-tensorize.json`:

| measurement | combined | seat 0 | seat 1 |
| --- | ---: | ---: | ---: |
| lane-decisions digest-identical | **3,008/3,008** | 1,504/1,504 | 1,504/1,504 |
| stacked tensorize, ms/lane-decision | **0.0811** | 0.0823 | 0.1180 |
| per-lane oracle, ms/lane-decision | 0.605 | 0.600 | 0.684 |
| speedup | **7.46×** | 7.29× | 5.79× |
| batched `record_action` | 0.0058 | 0.0057 | 0.0070 |
| **end to end** | **0.1029 ms/lane-decision** | | |

Facts are 0.0320 ms/lane (shared by both actors); ~9,719 decisions/s per
process. Seed 2 reproduces it: **3,008/3,008**, 0.0787 ms/lane-decision,
**8.50×**, **0.0992 ms/lane end to end**, both seats at ~0.079. Seat 1's slower
first run tracked the oracle's slower timing on the same loaded host; quote
paired runs, not a fixed seat ratio.

### Next session (session 25) — C4

> **Session 25 corrected item 1's premise and landed the missing half.** There is
> no crforge producer to wire into: `producer.py` is the IL producer over native
> `ObservationV1` lanes, and the PPO worker is too. C4 needs a new crforge loop,
> and its action path — stacked `ActionSequenceV4` → engine action index — is now
> `crforge_bridge/vector_actions.py`, gated at **3,008/3,008** against the stock
> decoder for both seats (`runs/crforge-c4/vector-decode.json`). See the
> session-25 section above and `FINDINGS.md` §3.56.

1. **Wire the stacked batch into the C4 producer.** `producer.py` calls
   `tensorize`/`record_action` per lane per owner today; replace that pair with
   one call per decision using two `VectorTensorizer`s (one per actor) or extend
   `VectorTensorizer` to both seats. The batched `ActionSequenceV4` is the row
   source.
2. **C4 gate, unchanged:** 8 lanes/process, 2–4 processes, 10 segments,
   `rejected 0`, `eligible_act_rate > 0`, native eval not degrading; log
   env-steps/s, tensorize ms, inference ms. Include NVIDIA MPS (the RoyaleFish
   `ever_*` recipe).
3. **Run the native cross-check** (`tools/crforge/frame_crosscheck.py`, engine
   first) after every adapter change and alongside the 256-lane gate.
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 23 (committed as 2234c79) — seat 1 and the game-frame fix

**Read first:** this section, the top entries, and `FINDINGS.md` §3.55.

The session started on seat 1 and immediately found a frame divergence that had
to land first. The changes below were a coherent checkpoint; session 24 finished
the gate and the measurement (see the session-24 section above).

### The finding

The native model input is **game coordinates**; the crforge bridge and the
vector facts layer were feeding the **seat frame**. Measured:

| path | play | observed unit |
| --- | --- | --- |
| native x86_64 (`CR_NATIVE_BUILD_ABI=x86_64`) | Skeletons on game cell `(6,10)` | **(6500, 10500)** |
| crforge resident | same play via `BattleEnvV1` | **(11002–12003, ~10212–11079)** |

The vector pending header for the same play is `pending_x = 11501` for game
`6500`, and the engine's action/mask space is seat-framed
(`Schema.toSeatX(0, x) = 18000 - x`). `BattleEnvV1.action_mask` builds model masks
in game coordinates, so the crforge model was seeing a mirrored board with an
unmirrored mask. C0/C2's bridge-vs-bridge parity is unaffected as a legality
record; its model-frame claim was never valid.

### What changed (in the working tree)

- **`crforge_bridge/observe.py`** — `entity_rows(..., seat=0)` converts each
  seat's rows to game coordinates (`x`, `targetX`; seat 1 converts `y`,
  `targetY`). There is one conversion point for the bridge.
- **`crforge_bridge/engine.py`** — `_bind_holder_identity` uses the now-game
  rows directly (the old `toSeatX/toSeatY` un-mirror was the workaround for the
  wrong frame).
- **`crforge_bridge/vector_observation.py`** — the facts layer writes **game
  coordinates**: entity `x` (and target x) are `WORLD_WIDTH - seat0_x`; derived
  velocity follows; each seat's pending point is converted once (seat 0 x, seat
  1 y); `placement_rows(seat)` returns the seat's mask flipped to the game frame
  (still a view).
- **`crforge_bridge/vector_rows.py`** — all row builders are actor-seat aware
  (`_actor_seat(tensorizer)`, no more seat-0 refusals). Card pending cells apply
  the owner-signed subcell offset and the actor perspective; candidate placement
  applies `flip_x` / `flip_y`; scalars read the actor seat's elixir, crowns and
  overflow. `reference_*` take `seat`; `reference_action_mask(...,
  model_frame=False)` leaves the native game grid for the stock `tensorize`
  (which applies the perspective itself), while the section oracles keep the
  default model-frame grid. `_PlayerView` / `_ObservationView` / `_MaskView` are
  now dataclasses so the stock perspective transform can copy them for a
  seat-1 digest oracle.
- **`tools/crforge/encoder_spike.py`** — the per-lane control converts pending
  and entity/target x too, so the C1 spike still compares like with like.
- **tests** — the seat-0-only refusal tests became seat-mismatch/invalid-seat
  tests; the pending-cell fixture is game-frame; the Tesla play helper converts
  its chosen game cell back to the engine's seat-frame action index.

### Status at session 23 end

- `crforge_bridge/tests`: **52 passed** at that point.
- Seat-0 digest spot-check after the change (64 lanes × 12 decisions, verify 8):
  **152/152 digest-identical**, 0.105 ms/lane tensorize, **0.148 ms/lane** end to
  end, 6.65× (`/tmp/opencode/seat0-after-frame.json`).
- **Left open at that point:** the 256 × 40 artifact predated the frame change,
  `batched_tensorize.py` had no `--seats`, and `FINDINGS.md` §3.55 was not
  written. **Session 24 closed all three** (see the session-24 section).

### Session 24 carried these (now done)

1. `--seats 0 1` with one tensorizer per actor and combined end-to-end timing.
2. Seat 1 gated against the stock actor-1 tensorizer; it found the relation
   tower-token and signed-zero velocity defects.
3. 256 × 40 re-run for both seats; native cross-check
   (`tools/crforge/frame_crosscheck.py`).
4. **Next:** C4, with the producer's per-lane pair replaced by the stacked
   `VectorTensorizer` call. Do not scale lanes/GPUs before C4's own gate.
5. **Parity cadence:** unchanged — 50–150 native matches every N updates and
   after every adapter change; never promote on crforge.

## Session 22 results (2026-11-19) — C3-B closed: the facts-only tensorize clears the gate

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §2 (C3),
`FINDINGS.md` §3.54.

### What changed

The last C3-B piece was the wiring: a `tensorize` that assembles the per-lane row
sections into the record the model consumes, with no contracts, no observation
projection and no per-lane Python. `crforge_bridge/vector_tensorize.py`:

- **`VectorTensorizer`** compiles `VectorEntityTables` and every section builder
  once (cards, groups, towers, effects/events, candidates, relations, spatial
  planes, scalars) and `tensorize(batch)` calls them in the tensorizer's own
  order, returning one `UniversalSemanticBatchV4` with **lanes as the batch
  dimension**. Sections already carry the lane axis, so the join is a reference,
  not a stack; only relations keep their common padded width and mask.
- **The previous-action record is batched.** `record_action(sequence, batch)`
  takes lanes as rows, resolves each sampled UID against that lane's candidate
  rows and fills one `PreviousActionV4` for every lane, replacing it wholesale
  (the same snapshot semantics as the stock method).
- **`lane_batch(batch, lane)`** slices the stacked record back to the exact
  one-row tensors the per-lane path emits (relations trimmed to the live count,
  every fixed section at V4 capacity). That slice is the digest projection, not
  a training path.
- **`batch_digest`** now lives here (one definition for the gate and the C3
  profiler) and `compare_semantic_batches` names the first differing field when a
  digest differs.
- `reference_observation` / `reference_scalar_observation` gained
  `episode_id` / `owner` / `tier` and pass `tower_troop_id` through, so the stock
  `tensorize` entry point can be driven from the same facts; the section oracles
  are unchanged.
- `VectorGroupSection.lane` / `VectorCardSection.lane` complete the lane slice
  set (towers, planes, scalars, effects/events, candidates and relations already
  had one).

Scope is unchanged: seat 0 and the bridge's no-event envelope. This is the
packet assembly, not a widened envelope.

### Measured (256 lanes, learner deck, 40 decisions, verify 32 after step 0)

`runs/crforge-c3/batched-tensorize.json`:

| path | total | ms/lane |
| --- | ---: | ---: |
| stacked facts-only `tensorize` | **20.65 ms** | **0.0807** |
| per-lane stock `tensorize` (prebuilt references) | 159.95 ms | 0.6248 |

**Equivalence: pass, 1,504/1,504 lane-decisions** (256 at step 0 + 39 × 32
after), compared field by field including dtype and shape, and
`batch_digest`-identical. A sampled deploy action was recorded on both paths
every step, so `previous_action` was exercised, and the stacked record ran the
stock `UniversalSemanticBatchV4.validate` at step 0. Busiest lane: 14 active
groups, 42 relation edges, 4 candidates.

**End to end: 0.118 ms/lane** — facts layer 0.0315 + tensorize 0.0807 + batched
`record_action` 0.0060 — i.e. **~8,460 decisions/s per process** against the
0.5 ms/lane gate, a **7.75×** speedup on the tensorizer half. A second seed
(`--seed 2`) reproduced it: 1,504/1,504, 0.0803 ms/lane, 8.0×, 0.117 ms/lane
end to end. This is the measurement the C1/C3 record was waiting for: the
≤0.5 ms/lane claim is now supported by a digest gate, not by section
arithmetic.

`crforge_bridge/tests/test_vector_tensorize.py` adds 4 tests (10-decision played
digest equivalence with recorded actions, relation-only trimming at fixed V4
capacity, per-lane previous-action resolution, and a broken-lane comparison
guard). The bridge suite is **52 tests**, all passing; ruff clean on the new
files.

### Next session (session 23) — C4's batched self-play loop

> **Closed in session 24:** the frame fix landed (commit `2234c79`) and both
> seats now pass the whole-record digest gate (**3,008/3,008**), with the
> x86_64 native cross-check passing. See the session-24 section above and
> `FINDINGS.md` §3.55. The remaining work is C4.

1. **Seat 1 is the open scope item.** The facts layer and every builder are
   seat 0 only, but self-play tensorizes both owners of each lane. Add a seat
   parameter (or a second `VectorTensorizer` with `actor_owner=1`) and gate its
   digest the same way before the loop uses it.
2. **Wire the stacked batch into the C4 producer.** `producer.py` calls
   `tensorize`/`record_action` per lane per owner today; the batched
   `VectorTensorizer` replaces that pair with one call per decision, and the
   batched `ActionSequenceV4` is the natural row source.
3. **C4 gate, unchanged:** 8 lanes/process, 2–4 processes, 10 segments,
   `rejected 0`, `eligible_act_rate > 0`, native eval not degrading; log
   env-steps/s, tensorize ms, inference ms. Include NVIDIA MPS (the RoyaleFish
   `ever_*` recipe).
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 21 results (2026-11-19) — C3-B's row build is complete

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §2 (C3),
`FINDINGS.md` §3.50–§3.53.

### What changed

C3's next section after candidates is `_towers` (0.145 of a 0.992 CPU
ms/decision). `crforge_bridge/vector_rows.py` now builds it for every lane of a
`VectorBattle`:

- **`VectorTowerBuilder`** — one gather over a per-lane six-row view, one sort
  (`owner != actor`, then model kind, then native id), then the health/target
  block: `hitpoints_ratio`, `shield_over_max_hitpoints`, `active`,
  `has_visible_target`, `king_activated`, `hitpoints_over_5000` and
  `max_hitpoints_over_5000`, plus the tile position and the kind-sized extent
  (half-extent 2.0 king, 1.5 otherwise). Attack-state and tower-troop-runtime
  columns stay zero, as the per-lane envelope leaves them.
- **The model view flips the kinds, not just the coordinates.** `_towers` sorts
  by `_model_tower_kind`, which swaps the princesses under `flip_x`; the batched
  build maps native kind `[0, 1, 2]` to model `[0, 2, 1]` under a flip and uses
  the same map for the emitted `tower_type`. The mirror test pins it.
- **Destroyed vs absent are different states.** A destroyed tower keeps its row
  with zero HP and the original maximum (ratio 0, `active` 0); a row that leaves
  the shared scene drops out and is padded exactly as the oracle pads
  (`tower_type` 0, troop 0, `owner_type` 2, zero vectors, `mask False`).
- **`VectorTowerSection.tower_index`** is the `entity id -> model row` map the
  tensorizer's `_relations` consumes as `tower_token_by_entity`; `lane(n)`
  slices one lane back to the exact `TowerSetV4` + dict the per-lane path emits.
- `_tower_slots` is now vectorized — the scan that fed both the tower and
  relation builds was a per-lane Python loop.

Scope is unchanged: seat 0 and the bridge's no-event envelope. Two facts are
pinned rather than derived: **`status` is always empty** (no `tower_activate`
events, so `king_activated` is 0), and **`tower_troop_id` is a static build
input** (`159000000` Tower Princess for the learner deck, `None` = no tower
troop), recorded on the gate artifact.

### Measured (256 lanes, learner deck, 40 decisions)

`runs/crforge-c3/batched-towers.json`:

| path | total | ms/lane |
| --- | ---: | ---: |
| batched tower section | **0.267 ms** | **0.00104** |
| per-lane `_towers` oracle | 8.92 ms | 0.0349 |

**Equivalence: pass**, `mismatches: []` over 40 decisions, all six towers on
every lane, **61,440 active tower rows** and **61,263 rows with a visible
target**. **33.5×** the per-lane oracle (three runs: 0.00104–0.00113 vs
0.0349–0.0372, so 32–33.5×). Section arithmetic only: replacing the live 0.145 share projects
**0.848 ms/decision** with groups, cards, relations and candidates also
batched, before spatial planes and scalars.

`crforge_bridge/tests/test_vector_rows.py` adds 4 tower tests (18 in the file at
that point): the 50-decision equivalence, the model-view flip, the static troop
pin (pinned, absent, a non-competitive id, a non-zero seat) and the
destroyed/absent row rules.

### Spatial planes — landed in the same session

`_spatial_planes` (0.117) is next in the section order and landed right after the
towers: `VectorSpatialPlaneBuilder` in the same module, with the archetype
`is_airborne` / `static_damage_basis` / persistent-root facts compiled into
`VectorEntityTables` (the tiles' child kinds were already there).

- **Towers and ownerless rows are not plane entities.** A towers-only board is an
  all-zero tensor; a neutral row produces the same planes as a removed row.
- **Float32 accumulation is part of the contract.** Two entities can share a
  cell; `np.add.at` with float64 operands narrows once and differs by one ULP
  from the per-lane loop's sequential float32 adds. The addends are cast to
  float32 before the scatter.
- **Flatten first.** The first version masked the full `(lanes, entities)` grid
  for every expression: **0.0188 ms/lane (2.44×)**. Working on the kept entities
  in one dimension is **0.00392 ms/lane (12.7×)**.

`runs/crforge-c3/batched-spatial-planes.json` (256 lanes × 40 decisions):

| path | total | ms/lane |
| --- | ---: | ---: |
| batched explicit planes | **1.004 ms** | **0.00392** |
| per-lane `_spatial_planes` oracle | 12.78 ms | 0.0499 |

**Equivalence: pass** over 40 decisions; 35 frames carried a building, 28,224
ground cells and 24,866 building cells exercised. Section arithmetic: replacing
the live 0.117 share projects **0.879 ms/decision** with the other five sections
batched, before `_scalars` and the empty effects/events.

3 more tests (`test_vector_rows.py` now 21, bridge suite **43**, ruff clean),
including a **forced Tesla** so the footprint fill is pinned deterministically —
the random policy alone never fielded a building.

### Match scalars — landed in the same session

`_scalars` is the smallest named section (**0.0140 CPU ms/decision** in the
shipped profile). `VectorScalarBuilder` writes the 18 match-wide numbers from the
batch plus the mode timeline; `_PlayerView` gained `crowns`/`elixir_exact` and
`_ObservationView` gained `time`/`phase`/`metadata` so the per-lane oracle reads
the same projection.

- **Two clocks, one timeline.** `BattleEnvV1` takes phase and multiplier from the
  native tick and the remaining clock from the canonical tick. `batch.tick` is
  canonical and the builder takes a `native_tick_offset` (0 for the vector gate
  and a fresh match). The 6,000-tick schedule is compiled once:
  normal → double (2,400) → overtime (3,600) → 3× (4,800) → tiebreak (6,000).
- **The gate runs the whole oracle chain.** `_scalars` takes the group/child
  overflow and candidate count as arguments, so `compare_scalar_section` runs the
  stock `_card_sets` → `_groups` → `_candidates` chain and passes the oracle's own
  counts.

`runs/crforge-c3/batched-scalars.json` (256 lanes × 40 decisions):

| path | total | ms/lane |
| --- | ---: | ---: |
| batched match scalars | **0.0733 ms** | **0.000286** |
| per-lane `_scalars` oracle | 0.970 ms | 0.00379 |

**Equivalence: pass** over 40 decisions; own elixir reached 10.0, up to 4
candidates and 14 visible entities. **13.2×** the per-lane oracle. Section
arithmetic: replacing the live 0.0140 share projects **0.978 ms/decision** with
all seven sections batched. The window stays in the normal phase, recorded
honestly as `phases_seen: ["normal"]`; a unit test pins the
double/overtime/3×/tiebreak columns at synthetic ticks on both paths.

3 more tests (file now 24, bridge suite **46**, ruff clean).

### Empty effects/events — landed in the same session

The bridge envelope carries no active effects and no combat events, so both
sections are the stock empty records widened to the lane count:
`VectorEmptySectionBuilder` (zero effect rows; events keep their 32-row capacity
with `owner_type` neutral, group/token addresses `-1` and `mask` False). Two
small view fixes were needed for the oracle path (`_TowerView.effect_states`,
`_ObservationView.events`).

`runs/crforge-c3/batched-empty-sections.json` (256 lanes × 40 decisions):

| path | total | ms/lane |
| --- | ---: | ---: |
| batched empty records | **0.0362 ms** | **0.000141** |
| per-lane `_active_effects` + `_events` oracle | 5.17 ms | 0.0202 |

**Equivalence: pass** over 40 decisions with both records validated; zero effect
and event rows, 32 padded event rows per lane. **143×** the per-lane oracle;
replacing the live 0.0983 share projects **0.894 ms/decision**. 2 more tests
(file now 26, bridge suite **48**, ruff clean).

Every named row section now has a batched build and a gate. Only the wiring
remains.

### Next session (session 22) — C3-B's last piece

> **Session 22 did item 1:** `crforge_bridge/vector_tensorize.py` assembles the
> stacked `UniversalSemanticBatchV4` and
> `tools/crforge/batched_tensorize.py` proves **1,504/1,504 lane-decisions
> digest-identical** (values, shapes, dtypes) at **0.118 ms/lane end to end**.
> C3-B is closed; the next work is C4. See the session-22 section above and
> `FINDINGS.md` §3.54.

1. **Wire it up:** a facts-only `tensorize` returning one
   `UniversalSemanticBatchV4` per lane, `batch_digest`-identical to the per-lane
   path on a captured sequence; only then claim ≤0.5 ms/lane end to end.
3. **Do not** widen the batch input, add lanes or GPUs while the row build is
   incomplete; the facts layer is 0.03 ms/lane and is not the cost.
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 20 results (2026-11-19) — C3-B's candidate section

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §2 (C3),
`FINDINGS.md` §3.49.

### What changed

C3's next section after cards and relations is `_candidates` (0.30 of a 0.992
CPU ms/decision). `crforge_bridge/vector_rows.py` now builds it for every lane of
a `VectorBattle`:

- **`VectorCandidateBuilder`** — one row per legal hand slot: card token, runtime
  form and cost, own-card row, the native 32×18 placement grid, the building
  half-footprint and `model_subcell_offset` the decoder needs, and the `(variant,
  visible, effective, slot)` UID. Rows are packed in hand order, which is the
  order `_candidates` walks `player.hand`.
- **`CandidateGeometry`** — the per-card static placement facts
  (`native_building_footprint` + `building_placement_profile`), compiled once per
  deck. They are the same facts `BattleEnvV1._cached_placement_entry` derives
  from `card_placement_mask`; the live legality of a tile stays the vector's own
  mask view.
- **The slot rule is the environment's**, not crforge's tile block: in hand, play
  not queued (`hand_pending`, the flag the bridge exports as `pendingHandSlots`),
  cost fits this decision's effective elixir, and at least one legal tile.
  crforge keeps a slot open while its play waits to run; `BattleEnvV1` closes it.
  Measured: after queueing a 4-cost Tesla with 10 elixir, the native slot still
  showed 222 legal tiles while the candidate count correctly dropped 4 → 3.
- **`reference_action_mask`** projects the mask `_candidates` reads (open slots,
  one entry per hand card with `row_major` as the `[32][18]` sequence, the exact
  effective elixir), and `compare_candidate_section` runs the stock `_card_sets`
  → `_groups` → `_candidates` chain against it.
- **`tools/crforge/batched_candidates.py`** is the gate: 256 learner-deck lanes,
  the checkpoint's own `model_config`, `ActionCandidatesV4` validation every
  step, 60 WAIT warmup decisions past the start cooldown, paired timing.

Scope is unchanged: **deploy rows only**. `players.abilityRuntime` is `null` in
the bridge's rich player record, so `ability_sources` is empty on both paths;
Mirror is refused because it needs the actor-private runtime contract the frame
does not carry.

### Measured (256 lanes, learner deck, 40 decisions)

`runs/crforge-c3/batched-candidates.json`:

| path | total | ms/lane |
| --- | ---: | ---: |
| batched candidate section | **0.221 ms** | **0.00086** |
| per-lane `_candidates` oracle | 13.60 ms | 0.0531 |

**Equivalence: pass**, `mismatches: []` over 40 decisions; up to 4 candidates per
lane, **5,181 queued-play slots** and **1,171 building rows** exercised (the two
cases that matter). **61×** the per-lane oracle (two full runs: 0.00084–0.00086 vs
0.0524–0.0531, 61–62×). Section arithmetic only: replacing the live 0.300 share
projects **0.693 ms/decision** with groups, cards and relations also batched,
before towers, spatial planes and scalars.

`crforge_bridge/tests/test_vector_rows.py` adds 4 tests (candidate equivalence
with record validation and coverage asserts, the slot rule against a hand-built
frame, the 2×2/3×3 building geometry, the Mirror refusal);
`compare_lane_facts` now checks `hand_pending` against `pendingHandSlots`. The
bridge suite is **36 tests**, all passing, ruff clean.

### Next session (session 21) — C3-B's remaining sections

> **Session 21 finished the row build:** `_towers`, `_spatial_planes`,
> `_scalars` and the empty effects/events landed in
> `crforge_bridge/vector_rows.py` with their own gates, equivalence pass (33.5×,
> 12.7×, 13.2× and 143× the per-lane oracles). Only the facts-only `tensorize`
> wiring carries to session 22; see the session-21 section above and
> `FINDINGS.md` §3.50–§3.53.

1. **`_towers` (0.145).** Two envelope facts to carry: `status` is always empty
   because `tower_activate` events are not supplied, and `tower_troop_id` is not
   in the facts — pass it as a static build input (`159000000` for the learner
   deck) and record it as a fidelity pin. Destroyed towers need the initial max
   HP carried across frames.
2. **Then `_spatial_planes` (0.117), `_scalars` and the empty effects/events.**
3. **Wire it up:** a facts-only `tensorize` returning one
   `UniversalSemanticBatchV4` per lane, `batch_digest`-identical to the per-lane
   path on a captured sequence; only then claim ≤0.5 ms/lane end to end.
4. **Do not** widen the batch input, add lanes or GPUs while the row build is
   incomplete; the facts layer is 0.03 ms/lane and is not the cost.
5. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 19 results (2026-11-19) — C3-B's card and relation sections

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §2 (C3),
`FINDINGS.md` §3.48.

### What changed

C3's next two sections after `_groups` are `_card_sets` (0.33 of a 0.992 CPU
ms/decision) and `_relations` (0.14). `crforge_bridge/vector_rows.py` now builds
both for every lane of a `VectorBattle` from the session-17 facts layer:

- **`VectorCardBuilder`** — the actor's eight-row card set (layout, roles and
  vocabulary from the tensorizer's own cached layout; runtime form from the hand
  form) and the opponent's nine-row set (eight zero rows plus the masked
  unknown-card row, because the bridge sends no events and `revealed_cards`
  stays empty). The **21-column** 16.635 schema also fills the actor's in-flight
  command: a pending row sets `pending_execution` and its target cell, with the
  seat-frame mirror `(18000 - pending_x) // 1000 - offset` and
  `pending_y // 1000 - offset`, where `offset` is the even-footprint shift
  (0.5 for a 2×2 Tesla); `pending_ticks_remaining` is zeroed because
  `B-general-v275` declares `neutralize_pending_ticks_remaining`. A **waiting
  champion ability** sets `ability_pending` on the champion's card row from the
  new facts-layer champion-slot block (`ability_command_ticks`, …), because
  crforge keeps ability commands out of the pending-card rows.
- **`VectorRelationBuilder`** — `entity -> target` / reverse,
  `tower -> target` / reverse (a tower can target an entity, and a tower is a
  target token) and `card -> group`, sorted and de-duplicated with one
  `np.unique` over a packed `(lane, source, target, relation)` key.
  `VectorRelationSection.lane(n)` slices a lane back to the exact
  `(1, count)` record for a future digest comparison.
- `reference_observation` projects player/tower/pending rows from the same
  facts, so the stock sections remain the oracle (the tests are not a shared
  shortcut).
- `tools/crforge/batched_cards.py` is the gate. It reads the **checkpoint's own**
  `contract.model_config` — 21 card-runtime columns for `B-general-v275`, not the
  default 16 — so the pending columns are exercised.

### Measured (256 lanes, learner deck, 40 decisions)

`runs/crforge-c3/batched-cards.json`:

| section | batched ms/lane | oracle ms/lane | speedup |
| --- | ---: | ---: | ---: |
| cards | **0.00161** | 0.0718 | **44.5×** |
| relations | **0.00984** | 0.0522 | **5.30×** |

**Equivalence: pass** — `card_mismatches: []`, `relation_mismatches: []`; 256
lanes at step 0 and lanes 0–31 after, up to 14 entities, 4 pending rows and 40
edges per lane. Four full runs measured 0.00122–0.00161 / 0.0718–0.0835 and
0.0098–0.0108 / 0.0522–0.0612, so quote the artifact and compare paired runs.
These are section arithmetic: replacing the live profile's 0.33 + 0.14 shares
projects **0.46 ms/decision** further, before towers, candidates, scalars and
spatial planes.

`crforge_bridge/tests/test_vector_rows.py` adds 5 tests (21-column card
equivalence over 50 decisions, relation equivalence over 50 decisions, the
pending-cell mirror rule for a troop and a 2×2 Tesla offset, the waiting
ability's `ability_pending` column, the tracker guard);
`test_vector_observation.py` adds the champion-slot projection. The bridge suite
is **32 tests**, all passing, ruff clean.

### Next session (session 20) — C3-B's remaining sections

> **Session 20 did item 1:** `_candidates`/placement landed in
> `crforge_bridge/vector_rows.py` with `tools/crforge/batched_candidates.py`,
> equivalence pass (61× the per-lane oracle). Items 2–6 carry to session 21; see
> the session-20 section above and `FINDINGS.md` §3.49.

1. **`_candidates` / placement (0.30).** The facts layer carries the native
   placement-mask view, but a candidate row also needs the hand-slot runtime
   (`effective_cost`, `form_code`, `footprint_width_tiles`,
   `footprint_height_tiles`, `model_subcell_offset`,
   `source_native_hand_slot`) that `BattleEnvV1.action_mask` builds today. Add
   those facts (still per-lane mask metadata, not a per-lane contract) and
   batch the rows; keep the per-lane `_candidates` as the oracle.
2. **Then `_towers` (0.145).** Note two envelope facts: `status` is always
   empty because `tower_activate` events are not supplied, and
   `tower_troop_id` is not in the facts — pass it as a static build input
   (`159000000` for the learner deck) and record it as a fidelity pin. Destroyed
   towers need the initial max HP carried across frames.
3. **Then `_spatial_planes` (0.117), `_scalars` and the empty effects/events.**
4. **Wire it up:** a facts-only `tensorize` returning one
   `UniversalSemanticBatchV4` per lane, `batch_digest`-identical to the per-lane
   path on a captured sequence; only then claim ≤0.5 ms/lane end to end.
5. **Do not** widen the batch input, add lanes or GPUs while the row build is
   incomplete; the facts layer is 0.03 ms/lane and is not the cost.
6. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 18 results (2026-11-19) — C3-B's first section: batched causal groups

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §2 (C3),
`FINDINGS.md` §3.47.

### What changed

C3's largest section is `_groups` + children (0.543 of a 0.992 CPU
ms/decision). `crforge_bridge/vector_rows.py` now builds that section for every
lane of a `VectorBattle` from the session-17 facts layer in one NumPy pass:

- `VectorEntityTables` compiles the per-vocabulary policy semantics once:
  base card, archetype (`form:<Name>`, then character/building/area node ids),
  child type, footprint/radius, runtime form, singleton group type and the
  source-card vocabulary id;
- `VectorGroupBuilder.build(batch)` fills the `BattleGroupSetV4` group/child
  tensors with the lane count as the batch dimension, plus slot-keyed
  `group_index` / `child_index` maps and both overflow counts;
- `reference_entities` projects `EntityStateV1` rows from the same facts and
  `oracle_groups` runs the stock `_groups` on them. The oracle resolves
  archetype, child kind, extent, radius and runtime form through its own
  helpers, so only the batched tables are under test.

The scope is the bridge's no-event envelope: **singleton groups** (the bridge
sends no causal-group reference), the bridge's raw `entity_kind` rule
(cardless rows are `effect`), no ability sources, projectiles, attack/movement/
visibility runtime or events, and **seat 0**. Towers are the separate six-row
section and stay out of the entity groups. `tools/crforge/batched_groups.py` is
the gate.

### Measured (256 lanes, learner deck, 40 decisions)

`runs/crforge-c3/batched-groups.json`:

| path | total | ms/lane |
| --- | ---: | ---: |
| batched group section | **13.81 ms** | **0.0539** |
| per-lane `_groups` oracle | 56.50 ms | 0.2207 |

**Equivalence: pass**, 40 decisions, 256 lanes at step 0 and lanes 0–31 after,
`mismatches: []`. **4.09×** the per-lane oracle; the busiest lane held 14
entities, all retained (overflow 0). This is the section arithmetic only:
replacing the live profile's 0.543 ms share with 0.0539 projects **0.503
ms/decision**, but the facts-only path still has to build cards, candidates,
relations, towers, scalars and spatial planes.

Two ordering rules were easy to get wrong and the test caught both:

1. **Group relation is local, child relation is global.** A singleton group's
   term is "this entity has no target"; a child's is "this entity is not
   anyone's target" — and it is built from the whole lane.
2. **Targets can be towers.** A skeleton attacking a princess tower names a
   target id that is not an entity; the lane-tagged membership test must miss
   it.

`crforge_bridge/tests/test_vector_rows.py` adds 5 tests (50-decision
equivalence, forced two-group/three-child overflow, empty-board mask/neutral
owner, the reference-entity projection, the seat-0 guard). The bridge suite is
**26 tests**, all passing; ruff clean.

### Next session (session 19) — finish C3-B's row build

> **Session 19 did part of item 1:** `_card_sets` and `_relations` landed in
> `crforge_bridge/vector_rows.py` with their own gate
> (`tools/crforge/batched_cards.py`), equivalence pass. `_candidates`/placement,
> `_towers`, `_spatial_planes`, `_scalars` and the payload write remain. See the
> session-19 section above and `FINDINGS.md` §3.48.

1. **`_card_sets` (0.33), then `_candidates`/placement (0.30), `_relations`
   (0.14)** from the same facts, each against its own oracle slice. Keep the
   bridge's empty `revealed_cards` until a separate fidelity gate changes it.
2. **Then `_towers` (0.145), `_spatial_planes` (0.117), `_scalars`** and the
   empty effects/events. Together with the group section these are the whole
   packet.
3. **Wire it up:** a facts-only `tensorize` returning one
   `UniversalSemanticBatchV4` per lane, `batch_digest`-identical to the per-lane
   path on a captured sequence; only then claim ≤0.5 ms/lane end to end.
4. **Do not** widen the batch input, add lanes or GPUs while the row build is
   incomplete; the facts layer is 0.03 ms/lane and is not the cost.
5. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 17 results (2026-11-18) — the vectorized observation facts layer landed

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §2 (C3),
`FINDINGS.md` §3.46.

### What changed

C3-B's named precondition was a vectorized `ObservationV1`; what existed was the
C1 spike's private `PolicyFrame`, which carries no towers, no placement rows and
none of the derived entity state. `crforge_bridge/vector_observation.py` now
fills a fixed-size `VectorObservationBatch` from `VectorBattle.observations` /
`masks` for every lane:

- both seats' header scalars (elixir, crowns, refusals, pending count,
  `enemy_seen` / evolution, per-seat own/enemy tower HP);
- both seats' hand / deck / cycle / pending rows and the packed
  `card_parameter` with the bridge's own slot resolution;
- all six towers as fixed rows selected by the pinned arena entity ids, with a
  header HP fallback for an enemy health bar the shared scene reports unknown;
- the native placement masks as a `(lanes, seats, 4, 32, 18)` **view** of
  `VectorBattle.masks` (the candidate-placement half of the tensorizer);
- derived `age_ms` and `velocity`, restarting when a row's entity id changes.

`tools/crforge/vector_observation_spike.py` is the gate and
`crforge_bridge/tests/test_vector_observation.py` pins it (equivalence, mask
view, fixed tower layout, age/velocity rule). See `FINDINGS.md` §3.46.

### Measured (256 lanes, 16.635.8, learner deck, 200 × 3)

`runs/crforge-c3/vector-observation.json`:

| threads | vectorized facts | decisions/s | bridge decoders | speedup | equivalence |
| ---: | ---: | ---: | ---: | ---: | --- |
| 1 | **0.0317 ms/lane** | 31,555 | 0.1518 ms/lane | **4.8×** | pass |
| 8 | **0.0342 ms/lane** | 29,282 | 0.1619 ms/lane | **4.7×** | pass |

The control is the bridge's full per-lane fact decode, so the ratio is 4.8×,
not C1's 19× for the smaller frame; the absolute cost is ~30× below the
tensorizer. 9–13 policy entities per lane and all six towers; ~260k legal
placement cells across 256 lanes at the peak.

### The gate has not moved

This is the precondition, not C3-B. Nothing here builds `ObservationV1`
contracts or tensorizes, so C1's **0.713 ms/lane** projection and the C3
profile's **0.99–1.07 CPU ms/decision** replay mean still describe the packet.

Two findings make the next step smaller than it looked:

1. **crforge's causal groups are all singletons.** The bridge's rich envelope
   carries no combat events, and `_causal_group_by_entity` is fed only by them,
   so `build_causal_groups` returns one group per entity ordered by the existing
   priority tuple. The batched group build is a sort, not a graph walk.
2. **The tracker collapses to an elixir schedule.** With no events, opponent
   card states stay `UNKNOWN_INITIAL` and `revealed_cards` stays empty, so the
   only tracker term the tensorizer reads is the enemy interval
   `(0, min(10, start + generated))`.

### Next session (session 18) — the batched row build, now on a verified input

> **Session 18 started it:** the batched group/children section landed and is
> 4.09× the per-lane oracle (0.0539 vs 0.2207 ms/lane at 256 lanes), equivalent
> over 40 decisions. The rest of this list is still the plan. See the session-18
> section above and `FINDINGS.md` §3.47.

1. **C3-B:** batch `_groups` + children first (0.54 ms/decision, the largest
   section) from `VectorObservationBatch`; keep the per-lane tensorizer as the
   digest oracle. Then `_card_sets` (0.33), `_candidates`/`placement_rows`
   (0.30) and `_relations` (0.14). Target ≤0.5 ms/lane, `batch_digest`-identical
   row-for-row.
2. **Do not** widen the batch input or add lanes until the row build is under
   the gate; the facts layer is 0.03 ms/lane and is not the cost.
3. **Fidelity note:** the bridge ignores the vector's `enemy_seen` columns
   (`revealed_cards` comes from events it never receives). Reproduce today's
   empty set in the first row build; fixing the gap is a separate digest gate.
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 16 results (2026-11-17) — the learner-deck blocker is closed; a run refusal is a match code

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §0–§4,
`FINDINGS.md` §3.45.

### The X-Bow tensorizer failure was the Knight hero

The C3 open item (`entity_kind='effect'`, `native_data_global_id=1`) was not a
missing archetype. Two bridge encoding bugs:

- hero **unit** rows are `<Card>Hero` (`KnightHero`), not `_hero`, so
  `_nearest_card_id` left `cardId=0` and `BattleEnvV1` classified the row as
  `effect`;
- `_bind_holder_identity` read the LogicData global id as a signed Java `int`
  (`-437504191`), rejected it as non-positive, and left the placeholder
  `dataGlobalId=1`. The catalog keys the unsigned bit pattern
  (`3857463105 → form:Knight_hero`).

Fixes: strip `Hero` in `crforge_bridge/observe.py`; mask the global id to
`0xFFFFFFFF` in `crforge_bridge/engine.py`. Tests: a live hero-deploy test, a
pure form-row classification test, and the match-code test below.

### The one remaining reject was a decided match, mislabelled

The first post-hero-fix 2-game mirror still had one distinct reject in game 2 (15
window counts): a queued Electro Spirit refused at its run tick. `H_LAST_RUN_REFUSAL` is
a **`LadderMatch`** code, but the bridge named it from the *giving*-refusal enum:
code 4 is `LadderMatch.OVER` (`START_COOLDOWN` in the wrong namespace). The play
lost a race with a crown-advantage end.

Fixes: name run refusals from the `LadderMatch` constants (read from the pinned
class), and resolve a queued play refused with `OVER` as
`action_canceled_terminal` — the give-stage `MATCH_OVER` path already does this.
`evaluate` now names both rejection shapes and adds `rejected_unique_total`.

### Result: learner deck is rollout-clean

`runs/crforge-c3/learner-deck/` — locked X-Bow deck, mirror, `B-general-v275`,
sampled, bridge transport:

| game | seed | terminal | tick | illegal | rejected |
| --- | ---: | --- | ---: | ---: | ---: |
| 1 | 7 | crown_advantage | 3,681 | 0 | 0 |
| 2 | 8 | crown_advantage | 4,032 | 0 | 0 |

Both games are decision-identical to the pre-fix run, so the fix changed no play.
Tests: `crforge_bridge` 17, `test_battle_env_rich_telemetry` 86 (+55 other
battle-env), tensorizer 72, evaluate 2 — all pass.

The learner deck also passes the C2 parity check: `resident_parity.py --deck
learner --decisions 60` is **60/60 identical** (resident 5.91 ms, bridge 7.33 ms,
paired 1.29×; parity pass, C2's 2× gate still belongs to C3) in
`runs/crforge-c2/learner-parity.json`.

The learner-deck tensorizer is **1.171 CPU ms/decision** (median 1.202, 90
decisions, entities median 5 / max 11, resident transport) in
`runs/crforge-c3/learner-deck/tensorizer-profile.json` — slightly above the
evaluator deck's 0.992 because the X-Bow list carries more spells, evolutions and
a hero, not because of the fix.

### Next session (session 17) — C3-B is still the throughput gate

1. **C3-B, unchanged:** a batched row build across lanes from a vectorized
   `ObservationV1` (which does not exist yet — that is the real precondition),
   `batch_digest`-identical to the per-lane path. Target ≤0.5 ms/lane.
   **Precondition landed in session 17:** `crforge_bridge/vector_observation.py`
   is the vectorized facts layer (0.032 ms/lane, equivalence pass); the row
   build itself is still open. See the session-17 section above.
2. **C3-A:** only if a compiled helper is cheap in this tree; there is no
   Cython/build extension today, so treat it as a design task, not a quick win.
3. **C4** can now use the learner deck: `profile_tensorizer.py --resident
   --deck learner` and `resident_parity.py --deck learner` are the entry points.
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 15 results (2026-11-16) — C2 landed: parity holds, the 2× gate was never the socket

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §0–§4.

### What landed

- **`native_runner/crforge_resident.py`** — `CrforgeResidentEnv`, the full
  `NativeClashEnv` surface (`create_match`, `observe`, `observe_atomic`,
  `observe_rich`, `step`, `queue_*_at`, `attest`, `status`, `close_transport`)
  over an **in-process** `crforge_bridge.engine.Engine`. Only `_request` changes:
  it calls the bridge's own `dispatch()`, so parity is a property of
  construction, not a test result. `status()` adds `resident: true`, `lanes: 1`,
  `transport: "in-process"` so no artifact confuses it with the 256-lane vector.
  `BattleEnvV1` detects it exactly as it detects the bridge (`engine: crforge`).
- **`tools/crforge/resident_parity.py`** — the C2 gate. The resident leg decides
  with a seeded policy, the bridge leg **replays those exact actions**, and the
  tool compares per-decision native envelope, per-owner observation, mask,
  entity/tower/event counts, rewards and terminals; then it re-runs both on fresh
  episodes and times `env.step` in **one alternating loop** (this host's absolute
  ms move ~2×, so the per-decision paired ratio is the estimator).
- **Two shared engine cost fixes** (`crforge_bridge/engine.py`, both apply to the
  bridge too, both bit-identical):
  - **run-refusal reads are windowed.** `step` copied both 7,024-int seat
    observations on *every* tick to read one header index. crforge clears
    `last_run_refusal` when it opens a decision, so a code can only be observed on
    the post-step tick a *given* play's run produced; the bridge now watches
    `{runTick, runTick + 1}` per given command. All other ticks read nothing.
  - **one row decode per capture.** `_capture` decoded seat 0 twice
    (`decode_rows` + again inside `entity_rows`) and `observe` decoded it a third
    time for `result`; `entity_rows` now accepts the decoded rows, and the capture
    is memoized by `(generation, tick, command sequence, done)` so the two
    pre-decision observes of one tick share one decode.
- **Tests:** `crforge_bridge/tests/test_resident.py` (50-step receipt-for-receipt
  equality against a second engine, stale-cache guard, close/reconfigure, the
  shared-row `entity_rows` equivalence) and a `resident_env` fixture.

### Parity: pass

**60/60 decisions identical** in every run whose parity artifact was kept (six
60-decision runs). One earlier run in a three-run loop diverged at decision 0
(`owners` only, native envelope matched) and was **not reproduced** in the next
eight runs; that run had no artifact, so it stays a recorded open question, not a
known defect. The comparison covers the ordinary, rich and atomic envelopes
(validated on the resident side, raw on the bridge side) plus masks, rewards and
terminals.

### Throughput: 1.2–1.5×, the 2× gate is missed

Paired `env.step` on the evaluator deck, `policy_features_only=True`, seed 1:

| run | resident | bridge (same process) | paired ratio |
| --- | ---: | ---: | ---: |
| artifact of record | 3.46 ms (289/s) | 5.15 ms (194/s) | **1.33×** |
| 1 | 4.80 ms (209/s) | 5.97 ms (168/s) | **1.27×** |
| 2 | 3.94 ms (254/s) | 6.30 ms (159/s) | **1.53×** |
| 3 | 4.23 ms (236/s) | 5.18 ms (193/s) | **1.21×** |
| earlier loop | 4.55 / 3.48 / 4.70 | 5.87 / 4.21 / 6.07 | 1.32 / 1.19 / 1.23 |

Controls on the same host: the **HEAD bridge** in the same harness is **7.08 ms**
(141 decisions/s), and the bridge in its **deployed shape** (own process,
`python -m crforge_bridge`) is **6.70 ms** (149/s). So the socket + JSON round
trip is **~1.2–1.7 ms of a ~4–6 ms decision**. The other 3–4 ms is
`BattleEnvV1`: observation build, action masks, placement-mask validation and the
atomic envelope — exactly C3's row build.

**Verdict:** the resident adapter is correct and worth keeping (it removes the
wire, the duplicate decode, and a dependency on a listening process), but it
cannot be 2× while the step is Python-bound. **C2's throughput gate moves to C3.**
Do not add lanes, processes or GPUs to close it.

### Next session (2026-11-17, session 16) — C3's row build, then C4

1. **C3-B (the throughput gate, now owned here):** fill the fixed-size rows for
   all lanes in one pass from the VectorBattle frame — groups, children,
   candidates, relations, cards, towers, effects, events — and keep the batch
   `batch_digest`-identical to the per-lane path on a captured sequence. Target
   **≤0.5 ms/lane**; the C2 2× gate is met when `BattleEnvV1.step` on the
   resident adapter drops under ~2.5 ms/decision.
2. **C3-A (only if it is the cheap win):** port `_groups` to a Cython/C helper
   over plain arrays; gate ≥4× on that section before porting more.
3. ~~**Learner-deck blocker:** the X-Bow deck fails closed in the tensorizer on
   `entity_kind='effect'`, `native_data_global_id=1`. Classify or filter
   bridge-only effect rows before any learner-deck rollout.~~ **Done
   2026-11-17:** the row was the Knight hero with a signed Java global id; see
   the session-16 section above and `FINDINGS.md` §3.45. The learner deck
   finishes 2/2 games with `illegal 0`, `rejected 0`.
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

### Session 14 record (previous)

**Read next:** the session-14 record below, then `FINDINGS.md` §3.43–§3.44.

## Session 14 results (2026-11-16) — C0 closed; C3 says the tensorizer is Python ceremony

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §0–§4,
`FINDINGS.md` §3.43–§3.44.

### C0 — closed, 20/20 clean

`runs/crforge-c0-20261116/soak20-rerun/`: **20/20 games terminated** (seeds 7–26,
both seats alternating), **illegal 0 / truncated 0 / rejected 0**, every game a
`king_tower_destroyed` terminal. With the 2-game `evaluate2` re-run and the
9-of-20 partial soak, the MiniPekka `26000018` class has not returned since the
give-tick legality fix. **C0's gate is met: crforge may bulk-train once C3
lands.**

### C1 record corrected — the spike's scripted policy never played

`tools/crforge/encoder_spike.py` picked a legal cell with
`isinstance(entry, dict)` on `placement_masks` values, which are
`FrozenMapping`: the guard never fired, the scripted policy stayed on `WAIT`,
and the recorded tensorizer number was measured on a board with no troops.
Fixed to `Mapping`, plus the owner-signed `model_subcell_offset` that building
placements require (`building 27000006 requires native subcell offset`). The
encoder half of C1 is unaffected — vectorized facts are 0.0145 ms/lane vs 0.289
per-lane at the default shape; the tensorizer half is re-measured below.

### C3 attribution — the packet is 3,754 Python calls per decision

New tool `tools/crforge/profile_tensorizer.py` drives a live policy episode
(`B-general-v275`, evaluator deck, both seats, sampled), captures each decision's
observation **and** the `ActionSequenceV4` the policy recorded, then replays only
the tensorizer half — `tensorize` + `record_action` — under `cProfile`. It proves
an A/B with a capture digest plus a per-decision tensor hash, and reports CPU
ms/decision next to wall time.

| measurement | value |
| --- | --- |
| stock `tensorize` + `record_action` | **1.069 CPU ms/decision** |
| shipped tree | **0.992 CPU ms/decision** (median 1.000) |
| decisions in the run | 128 (terminal at tick 725) |
| entities per decision | median 8, max 11 |
| events per observation | ≈99 — the tracker walks all of them |
| Python calls per decision | **3,754** |

Sections (stock, cumulative ms/decision): `_groups` 0.725, `_card_sets` 0.390,
`_candidates` 0.300, `_card_runtime_features` 0.189, `tracker.update` 0.172,
`_towers` 0.145, `_relations` 0.142, `_spatial_planes` 0.117, `_events` 0.090.
Per-decision ceremony: 330 `isinstance`, 217 contract-mapping `__getitem__`,
214 `_finite`, 195 `FrozenMapping.get`, 117/101/85 `max`/`min`/`sum`.
**~35 % of profiled time is builtin call overhead plus the frozen-mapping layer**
(28 % builtins, 4 % `contracts.py`, 3 % `Mapping.get`); the builder's own bytecode
is the other half. This is bookkeeping around small arrays, not array math.

Shipped, all bit-identical (same `batch_digest` on the same capture): `_finite`
fast path, cached actor deck layout, per-frame player lookup, single-pass group
aggregation. Profiled: `_groups` **0.725 → 0.543**, `tensorize` 2.227 → 1.875.
**End-to-end: within noise** — two alternating pairs gave −5.6 % and +13.5 %,
and the host's run-to-run spread is ±15 %, so there is no throughput claim. A
NumPy row-build variant was measured and **reverted**: digest-identical, flat
end-to-end, extra allocation surface for nothing.

### C1 record re-run at the default shape — C3's gate is met

`runs/crforge-c1/encoder-spike.json` (256 lanes × 200 × 3, tables 16.635.8,
content `062a11e0…`): vectorized facts **0.0165 ms/lane = 60,702 decisions/s**
(19.1× the per-lane control's 0.315), frames equivalent. Existing path with the
shipped tensorizer: cold `observe` **0.400**, in-session `tensorize` **0.697**;
**projected end-to-end 0.713 ms/lane = 1,402 decisions/s = 1.16 matches/s per
process**, against C3's ≥0.8 gate. All C1 gates pass. threads 8 is the same
(0.0177 ms/lane, 19.9×, 0.714 ms/lane).

Two numbers, two shapes — quote the right one: the C1 tool reports the *minimum*
single decision over 100 decisions of a seed-1 game (0.697 ms), the C3 profiler
the *mean over a whole replay* of a busier game (0.992 ms, median 8 entities).
Size runs with **0.99–1.07 ms/decision**; use 0.71 ms/lane only as the gate
measurement.

**Open item for C2:** the same path on the X-Bow learner deck fails closed in the
tensorizer — `public entity resolved to UNKNOWN archetype: entity_kind='effect',
native_data_global_id=1`. Twenty evaluator-deck games never produced it. The
resident adapter must classify or filter bridge-only effect rows before a
learner-deck rollout is trusted; C1 stays on the evaluator deck.

**Verdict.** The C3 gate is met; the ≤0.5 ms/lane target is not. Tuning is
exhausted — the packet is 3,754 Python calls/decision. The next lever is
structural, not incremental (`FINDINGS.md` §3.44).

## Next session (2026-11-17, session 15) — superseded by session 16 above

Session 15 did item 3 (C2) and measured it; items 1–2 carry into session 16's
plan. Kept for the original wording of the C3-A/C3-B gates:

1. **C3-A, probe the ceiling cheaply:** port **one** section, `_groups`, to a
   Cython/C helper over plain arrays and measure it against the Python row on the
   same captured decisions (the profiler replays them). Gate: **≥4× on that
   section** before porting the rest. If it cannot beat the row build by 4×, go
   straight to C3-B.
2. **C3-B, the real win:** batch the row build across lanes — one pass over 256
   lanes filling the same fixed-size arrays (groups, children, candidates,
   relations, cards, towers, effects, events) from the vectorized frame. Target
   ≤0.5 ms/lane; keep the batch `batch_digest`-identical to the per-lane path on
   a captured sequence before trusting it.
3. ~~**C2 resident adapter:** `native_runner/crforge_resident.py` over
   `LiveBattle`/`VectorBattle` with the `NativeClashEnv` surface; deck pool +
   `SideSetup`; acceptance = 50+ identical observations/masks/rewards and 2× env
   throughput.~~ **Done 2026-11-16:** parity 60/60, throughput 1.2–1.5× — the
   remaining 2× is C3.
4. **Parity cadence:** 50–150 native matches every N updates and after every
   adapter change; never promote on crforge.

## Session 13 results (2026-11-16) — C0 fix landed; C1 says the tensorizer is the packet

**Read first:** this section, [`ROADMAP_CRFORGE.md`](ROADMAP_CRFORGE.md) §0–§4,
`FINDINGS.md` §3.42–§3.43.

### C0 — the MiniPekka reject was a delay-bin mismatch

The soak reproduced the last reject class immediately: MiniPekka `26000018`
(owner 1) with `policy_delay_offset_bin: 3` at native tick 840; crforge refused
`NOT_ENOUGH_ELIXIR` (`givenTick 840`, `runTick 860`) with 3.952 elixir against a
cost of 4.

Root cause: `BattleEnvV1` applied the native five-bin touch window to crforge.
The native probe validates a touch when it arrives, so bins 0–4 may spend the
next four ticks of regeneration. crforge's `PlayerCommands.covers` reads
`wholeElixir() - promised` at the tick the command is given, with no future
regeneration. The mask opened the slot on bin 3; the bridge injected it at the
give tick.

Fix in `native_runner/battle_env.py`:

- `BattleEnvV1.runner_engine` is resolved once (`engine=` or `status().engine`);
  a crforge endpoint gets a **one-bin** window (`_touch_window_elixir_raw` now
  takes `bins=`). The native engine keeps five bins.
- `_action_touch_delay_bin` returns 0 on crforge, so compound decisions validate
  each touch at the give tick.
- The mask reserves `max(this process's pending, player.elixirReservedRaw)`,
  i.e. crforge's own promise accounting.
- Regression tests: `test_crforge_validates_at_the_give_tick_not_a_future_touch_bin`,
  `test_crforge_uses_the_engines_reserved_elixir_header`.

Evidence: `runs/crforge-c0-20261116/soak20/` — **9 of 20 games completed at
wrap-up, all clean**: `illegal_total: 0`, `truncated: 0`, `rejected_total: 0`,
`games_with_rejections: 0`, seeds 7–15. `summary.json` records the interruption
honestly; **finish the remaining 11 games before a bulk run**. The 2-game
`evaluate` re-run is clean: **2/2 completed, illegal 0, truncated 0, rejected
0** (`runs/crforge-c0-20261116/evaluate2/`). `evaluate` now reports
`illegal_total`, `rejected_total`, `games_with_rejections` and
`rejected_reasons`; rejected commands are never folded into "illegal".

### C1 — vectorized facts are cheap; the V4 tensorizer is the packet

`tools/crforge/encoder_spike.py` runs one 256-lane `VectorBattle`, encodes the
same seat rows with the bridge's per-lane path and with a NumPy frame built from
`v.observations` + `v.masks`, and asserts field equality on a tick. Artifact:
`runs/crforge-c1/encoder-spike.json`.

| shape | vectorized ms/lane | decisions/s | per-lane ms/lane | speedup |
|---|---:|---:|---:|---:|
| 256 lanes, 1 thread | 0.028 | 35,780 | 0.542 | 19.4× |
| 256 lanes, 8 threads | 0.031 | 32,258 | 0.573 | 18.5× |

Existing bridge path on the same deck (cold `BattleEnvV1.observe` +
`UniversalObservationTensorizerV4`): **observe 0.647, tensorize 1.413,
observe+tensorize 2.060 ms/lane**. Projected end-to-end (vectorized frame +
tensorizer): **1.44 ms/lane = 694 decisions/s** (1.4× today).

**Verdict.** The observation builder is no longer the lever: vectorized facts
are ~0.03 ms/lane (35k decisions/s), so C1's encoder gate passes by >30×. The
end-to-end 1 ms/lane gate does **not** pass yet, because
`UniversalObservationTensorizerV4` is ~1.0–1.4 ms/lane and now dominates. Do not
add lanes, processes or GPUs; the next work is feeding the tensorizer from the
frame (C3).

Caveat: these are a 40-step × 1-repeat smoke under session load, not the
200 × 3 quiet-host run; re-run the tool with its default shape before quoting
them as the C1 record. The qualitative split is unambiguous.

The old "~13 ms/lane bridge encoder" was a transport/JSON + full-assembly
number, not the in-process observation build; measured `observe` is ~0.6 ms and
the bridge's `observe_atomic` `_capture` ~0.8 ms.

### Session 13's next-session plan — superseded by session 14 (above)

1. ~~**Finish C0**: run the remaining 11 games~~ — **done, 20/20 clean**
   (session 14).
2. ~~**C3 (blocking the C1 record)**: feed `UniversalObservationTensorizerV4`
   from the vectorized frame~~ — **attributed and partly reduced**; see
   `FINDINGS.md` §3.44. The end-to-end gate now sits at this host's noise floor,
   so C3 continues structurally (C3-A/C3-B in the session-15 plan above).
3. **C2 resident adapter** — still open, unchanged.
4. **Parity cadence** — unchanged.

### Stop conditions

- C1's **end-to-end** gate misses after two iterations → fix the tensorizer, do
  not scale.
- Any crforge/native divergence that cannot be attributed to the adapter → stop
  and use the native gate. Do not "train through it".
- The engine is not deleted, not idled, and not the fallback for evidence. It is
  the evidence.

### Superseded — session 12 plan (fuse the rich frame into tensor rows)

This remains useful **native-path** work (the gate and the fallback), but it is no
longer the bulk path. Do it only if the native engine must become competitive
again, or opportunistically while C1/C2 land, since the vectorized tensorizer is
shared.

### What session 11 changed

Step 2 of the data-path plan is done. `policy_features_only` builds every
contract through `construct_contract` inside a `lean_contracts()` scope, so the
training path no longer validates, freezes or records provenance; the hand
runtime contract and form aliases are cached, and observation caches are keyed
by `(identity, lean)`. Paired A/B: **11.75 → 7.81 CPU ms/packet** (median
−28…−34 %), **78 → 113 lane-packets/s**; `_entities` −60 %, `_extract_towers`
−44 %, `_players` −55 %. The full suite and the tensorizer-parity test pass; the
validated evaluator/parity path is unchanged. See `FINDINGS.md` §3.43 for the
per-section table and the gate caveat (this host's absolute numbers move ~2x
with load, so use paired runs).

### The shape of the remaining packet

One lane, quiet host: `step` 8.7 = `_advance_headless` 3.1 (engine round +
atomic rich capture), `observe` 4.0 (`_entities` 1.4, `_extract_towers` 1.0,
`_players` 0.3, events 0.5, mask ~1.7), and ~1.6 masks/bookkeeping. cProfile has
no contract machinery left in the top 20: the hot rows are `_rich_object` /
`_phase_object` JSON binding, socket `recv`, `resolve_attack_runtime` and
placement geometry. That is the fused-path target.

### What session 8 changed (historical)

`async_cluster_self_play.py` is now pipelined: one `ppo-inference` thread with
at most one batch in flight, the coordinator stages every completed receive
while the forward runs, and segment-boundary bootstraps use the same thread.
The 2 ms coalescing window is kept and also applied at the job boundary. All
1,293 tests pass and a 10-segment real-checkpoint run on 4 x86 engines completed
cleanly. With a cheap (synthetic) forward the loop is 7 % faster on wall and
14 % on non-model time; with the real 17.5M checkpoint it is neutral — the
forward is 98 % of wall. It is a prerequisite for the GPU/8-engine rig, not a
CPU throughput win.

### The corrected throughput reality

The old gate ladder used ceilings measured on the raw resident channel, which
does no observation decode and no tensorization. Those are ~90 % of a real
packet:

| Rig (8 lanes, 2 ms window) | Old "ceiling" | **Measured training floor** |
| --- | --- | --- |
| 1× x86 | 0.61/s | **0.082/s** (0.138 in profiler units) |
| 2× x86 | 1.22/s | 0.10/s |
| 4× x86 (only 2 real cores here) | 2.44/s | 0.15–0.16/s |
| 4× A51 | 0.26/s | 0.05/s (unchanged) |

Per packet on one x86 engine: **32 ms native step + observation build, 14 ms
tensorize, 6 ms channel** — 51 ms median / 82 ms mean. The 2.5/s goal at 8
engines needs ~21 ms/packet, i.e. the worker cost must roughly halve.
Pipelining cannot do that; it removes ~12 ms/packet of coordinator idle.

**After §3.43 (lean training observation), paired on this host:** one lane
**78 → 113 lane-packets/s** (stock → lean), i.e. ~0.11 → ~0.16 ladder-matches/s
per process at 720 decisions/match; the 8-lane engine shape is 97–112
lane-packets/s. This is the observation half of the packet; the native round
trip (~3 ms) and rich telemetry binding are the remaining thin-path target.

### Judgment — fix the data path, then scale

The engine, the loop and the model are done. The single thing that is ~5× behind
the field is the per-decision **data path**, and it is the only fix that
multiplies every engine, every future run and every GPU hour at zero recurring
cost. Checked in `.resource` on 2026-11-15:

| | FirstLight-Zero | daybreak-cr | us |
| --- | --- | --- | --- |
| Concurrency | 8 resident per collector, many collectors (the 10/s figure needs ~16 collectors = **128 slots**) | 13–50 collectors, **one game each** | 4 engines × 8 lanes |
| Per-decision data | compact fixed arrays (26 scalars, 64×14 units, 5 options) | same arrays built by a **C extension** (`cr/native_features.c`); `CR_NATIVE_FEATURES=0` is the Python path | `ObservationV1` objects + 21-column tensorizer in Python |
| Serving | one server for the farm, CUDA graph per batch bucket | one decision server per GPU, all waiting collectors per call | local forward, ~2 packets/forward |
| Model | 1.17M, pruned to one mirror | 1.26M / 3.4M / 8.0M | 17.5M |

**The arithmetic.** 9.85 matches/s ÷ 128 slots = **0.077 matches/s per slot**.
Our raw engine (no observation work) is **0.081**; our real loop is **0.017**.
So they run at the engine's raw limit and we are ~5× above it. Their engine is
not faster — their per-decision overhead is nearly zero. (The §3.25 note that
"9.85 matches/s is 16 slots × 0.62" is wrong by ~7× and is superseded here.)

If 40 ms of observation+tensor work becomes ~10 ms, a packet is ~25 ms and one
engine goes **0.082 → ~0.26 ladder-matches/s**. **8 engines on real cores ≈
2.1/s ≈ 180 k matches/day**, i.e. FirstLight's whole 1,450-round run in ~8 days.
That is the goal reached with 8 engines and a thin path, not with one engine and
a fantasy ceiling.

**Scale-out (from the 2026-11-19 C4 measurement).** The 0.33 is per process, and
the forward is 24 % of its packet, so N processes are CPU-additive but GPU-shared:
~4 processes fill one A6000 without changes, and 8 need MPS plus a batched
decision server. A current-packet 8-vCPU instance is therefore **1.3–2.6
matches/s** ($46–92 per 1M matches) against 0.33 and $295 today. The engine-only
2.8 matches/s is per process, not per host. Order: multiproc, then wire C3-B,
then de-Python the 228 ms, then graphs/state, then more boxes at ~1 A6000 per
8–16 vCPU. Evidence and the $/M table: `ROADMAP_CRFORGE.md` §4.

### Session 10 — attribution done, two fixes shipped (2026-11-15)

Step 1 is finished. New tool
`tools/native_analysis/profile_step_breakdown.py` runs the worker's own batch
shape (decode lanes → `begin_round` → lane thread pool → `finish_round`) with a
scripted play-every-4 policy and reports per-method wall time plus a sequential
cProfile with per-module totals.

**One lane, 16.635.1, current tree:** `step` **17.1 ms**, of which `observe`
**10.7 ms (62 %)** — `_entities` 4.7, `_extract_towers` 2.7, `action_mask` 1.9,
`_players` 0.9, `_events_for` 0.8 — and the engine/channel round 5.0 ms.
cProfile puts ~34 % of self time in `contracts.py` plus builtin type checks and
~11 % in `rich_telemetry_adapter.py` + `phase_runtime.py`: the build is dominated
by validated, deeply frozen Python objects, not game logic. The tensorizer never
reads entity/tower `runtime_provenance`, `effects`, `shield_state`,
`capture_runtime`, `periodic_attack_modifier`, `internal` or `movement_target`.

Shipped (both measured):
- **scalar-first `contracts._freeze`** — paired A/B, median single-lane
  **48.1 → 57.4 pkt/s (+19 %)**, CPU **19.0 → 15.7 ms/packet**.
- **one tower projection per tick** — `step` now asks for the rich projection
  and `observe`/`action_mask`/`_derive_events` reuse it (was two projections of
  the same tick). `--trace-towers` confirms one computation per tick.

**Lanes do not scale.** Same engine at `--lanes 1/2/4/8`, process CPU measured
with `getrusage`: the process consumes **0.90–0.94 of one core at every lane
count**; lanes buy at most ~1.5×. The worker is GIL-bound, so lanes only overlap
the engine wait. Throughput is **processes (engines)**, not lanes — the earlier
"8 lanes ≈ 70 lane-decisions/s ≈ 0.082 matches/s" is one Python core, not the
engine.

### Session 11 — lean training observations shipped (2026-11-15)

Step 2 is done; see `FINDINGS.md` §3.43.

- `policy_features_only` constructs every contract with `construct_contract`
  inside a `lean_contracts()` scope: no `__post_init__` validation, no
  `FrozenMapping` freeze walk, no provenance trees, no projectile-velocity
  rewrite.
- `project_runtime` / `project_player_runtime` take `lean=True` and route every
  typed runtime state (and the phase/attack state) through the same constructor;
  `unknown_provenance` is one cached shared record per field universe.
- `_native_form_aliases` is cached per snapshot identity and
  `_hand_runtime_contract` per validated hand selection (16 → 1 projections per
  packet); all observation caches are keyed by `(identity, lean)`.
- Paired A/B, 8 alternating runs: CPU **11.75 → 7.81 ms/packet** (per-pair
  median −28 %, up to −37 %), **78 → 113 lane-packets/s**. `_entities` 3.43 →
  1.37, `_extract_towers` 1.84 → 1.02, `_players` 0.60 → 0.27.
  `_advance_headless` is flat — it is engine + rich capture, not observation.
- Validated path unchanged: `observe()` defaults to `lean=False`; the scripted
  parity seeds and evaluator take exactly the old path. New tests pin that a
  lean observation tensors bit-identically and that `cls(...)` still validates.

### Task order — native fallback (superseded for bulk, kept for the gate)

| # | Step | Effort | Gate |
| --- | --- | --- | --- |
| 1 | ~~Micro-profile `BattleEnvV1.step`~~ (**done, §3.42**) | — | top-5 functions = the whole observation build |
| 2 | ~~Thin the observation for training~~ (**done, §3.43**) — non-validating constructor, no provenance/unused states under `policy_features_only`, cached hand contract, lean-keyed caches. | done | paired **11.75 → 7.81 CPU ms/packet (−28…−34 %)**; validated parity path untouched; the absolute 8 ms number depends on host load |
| 3 | **Fuse the rich frame into tensor rows** — the remaining packet is native round trip (~3 ms) plus `_rich_object`/`_phase_object` binding; skip the intermediate Python contracts (daybreak's `native_features.c` shape). Per-lane pooling is deprioritised: lanes are GIL-bound and now a small fraction. | 3–5 days | **CPU ≤ 5 ms/packet** and tensorizer parity |
| 4 | **One short GPU session** (~1 h, ~$1): validate the pipelined inference thread on CUDA and record the real end-to-end number | 1 h | no graph/stream errors, ≥0.5/s on 1× x86 |
| 5 | **Move to a real 8-core host** for 8 engines (the engine is a plain x86_64 ELF; this laptop has 2 P-cores). Eight engines = eight processes, which is the parallelism that actually scales. | 1 day | 8 processes ≈ 2/s aggregate |
| 6 | **400+ paired games on the native engine**, then a **bounded specialist fine-tune** (X-Bow 3.0 or Hog 2.6) before the generalist league | days of GPU | Wilson intervals, 0 rejected |

**In parallel, rules permitting:** play ~20 games of `A-hog26-v738` /
`B-general-v275` on Null's ladder and record the results. It costs almost
nothing and measures the one risk throughput cannot answer — how far official
16.635 training transfers to Null's custom balance. Learn that this week, not
after a month of training.

### Do not (native-path notes; the crforge line is superseded)

- Do not chase 2.5 matches/s as a target in itself. Decisions/day at a gateable
  quality is the metric; 2/s with the 17.5M model is already a serious budget.
- ~~Do not start crforge now.~~ **Superseded 2026-11-15:** crforge is the bulk
  path after the C1 encoder gate. The original objection stands as a risk, not a
  prohibition: it needs a vectorized V4 observation adapter and is never a valid
  gate. See the top decision and `ROADMAP_CRFORGE.md`.
- Do not farm phones. The A51 stays the ARM64 parity/deploy oracle.
- Do not run a long GPU fine-tune before steps 1–3: at today's packet cost the
  GPU idles 96 % of the time waiting on Python.
- Do not quote `resident_batch_throughput` matches/s as a training rate. Four
  engines gave 0.736 aggregate because one Python client decodes every lane.
- Do not expect loop pipelining to lift a row-bound CPU forward (§3.40); it
  pays only when the forward is fixed-cost (GPU) and there are enough engines
  that the coordinator would be the serial resource.
- Do not raise `CR_PPO_INFERENCE_BATCH_WAIT_MS` — still a measured loss.
- Do not add lanes expecting throughput: the worker is GIL-bound (0.90–0.94 of
  one core at 1–8 lanes, best run 1.5×). Add processes (engines) instead.
- Do not measure the data path with wall time on this laptop while a browser is
  running; use CPU ms/packet and the section shares, and compare **paired
  runs** — absolute CPU ms/packet moved ~2x during 2026-11-15 (§3.43), so a
  stock number taken an hour earlier is not a baseline.
- Do not build a validated/evaluator observation through `construct_contract`
  or inside `lean_contracts()`: the training shortcut is only valid for
  in-process policy rows, and the observation caches are keyed by
  `(native identity, lean)` precisely so the two cannot mix.
- Do not run the new inference thread on CUDA until graph capture/replay,
  autocast and the `model_streams` waits are confirmed there.
- Everything else in "Do not do" below still applies.

### Start state

```bash
# x86 engines, one per core, ports 26789+N.  Kill by PID: `pkill -f` also
# matches the shell that runs it.
for i in 0 1 2 3; do
  setsid .venv/bin/python tools/native_analysis/crx_engine_host.py \
      --engine-index "$i" --seconds 3600 > /tmp/opencode/engine$i.log 2>&1 &
done
# Worker processes take the FIRST N allowed CPUs, so pin the profiler's group
# away from the engine hosts when both run on this laptop.
taskset -c 4-11 .venv/bin/python tools/native_analysis/profile_cluster_collection.py \
    --engines 4 --lanes 8 --steps 120 --diagnostic-timing --allow-cpu-sharing

# §3.42 attribution: one lane on one core, cProfile of 60 sequential steps.
# Engine 0 owns CPU 0, so pin the profiler to an E-core and leave the engine alone.
taskset -c 4 .venv/bin/python tools/native_analysis/profile_step_breakdown.py \
    --checkpoint .resource/RoyaleFish/models/B-general-v275.pt \
    --engines 1 --lanes 1 --steps 100 --warmup 5 --profile-steps 60 \
    --play-every 4 --profile-dump /tmp/opencode/step.prof \
    --output runs/step-breakdown/lane1.json
# scaling proof: repeat with --lanes 1/2/4/8 and read cpu_ms_per_packet
# §3.43 pairing: this host's absolute CPU ms moves ~2x with background load, so
# alternate stock/lean (`git checkout` the four source files vs this tree) and
# compare each pair.  runs/step-breakdown/ab-{stock,lean}-*.json is the 8-pair
# one-lane set, ab8-*.json the 8-lane worker shape.

A51, if needed for a parity comparison only:
`./launch_cluster_a51.sh --engines 1 --base-port 26799`.

---

## Ecosystem audit — 2026-11-14 (`CRkonwledagebase`)

`.resource/CRkonwledagebase` indexes **58 projects, 13 capabilities, 90
project×capability relations** with R0–R4 evidence grades. Its own headline is
that **nothing reaches R3/R4**: most entries are author-documented (R1) and a few
are fixed-commit static reviews (R2). Read it as a map, not a parts bin.
(`.resource/REA` is a generic reverse-engineering MCP, not a CR project.)

### Scoreboard for "X-Bow 3.0 beats top players, any deck, Null's or official"

| Component | Best public asset | Evidence | Where we stand |
| --- | --- | --- | --- |
| 16.635 rules at speed | **crforge** 16.635.3/8: 127k steps/s/thread, held tick-for-tick to recorded battles; **RoyaleFish** x86_64 16.635.1 probe, 16 matches/process | author conformance set (43,807 battles); R2 interface audit | **behind** — training runs on 4 phone engines |
| Native ARM64 16.635.1 | our A51 probe: touch hook, 32 resident lanes, attested manifest | verified on device | **ahead of the field** |
| Deck-general policy | RoyaleFish `B-general-v275` — 17.5M params, **16.635 data**, 275 updates | paired evals of its sibling | **behind** — ours is 12.7M on 15.535.86 data, step 460 |
| Specialist policy | RoyaleFish `A-hog26-v738` **71.9% ±2.6** vs its start; Log Bait and anti-2.6 exploiters | 1,000+ game gates | behind, but the recipe is public |
| X-Bow 3.0 | FirstLight-Zero `v1450`: **1.17M params, self-play only, 97.5% vs a 16.8M IL model** | author-reported, 40 games, **mirror only, 15.535.13** | not started |
| Live ladder play | daybreak-cr `play.py --queue --losses N` (Null's); ClashAI vision+taps; Hasty-CR; RoyaleBot `rb-build 0.1.2` for Null's 16.635.1 | all author-documented | **ahead** on in-process control, behind on ladder orchestration |
| Ladder win rate | **ClashAI**: 14,060 trophies / 4,499 matches; Ranked **32.7% → 42.7%** over 150–629 games | author-published | — |

### The honest answer

**Nobody has published a bot that beats top players.** The best public Ranked
number is ~43%, i.e. it is still losing. FirstLight-Zero's 97.5% is an X-Bow
*mirror* result. Hasty-CR states it plainly: *"No trained policy has beaten a
human. One match played by a person found the pathing bug thirty million
training steps never did."* The goal is unclaimed, and the field sits at roughly
"loses narrowly to strong humans".

### The unlock we were missing

The community already ported **this exact client build to x86_64**, and the
artifacts are already in `.resource`:

| Thing | Value |
| --- | --- |
| Official x86_64 `libg.so` 16.635.1 | `.resource/Clashback/game/engine/libg.so`, sha `c4721bd9…fd6539`, BuildID `4b3bf061…` |
| Our ARM64 `libg.so` 16.635.1 | sha `0b580222…e4eaa`, BuildID `179ac4bd…`, `apk_version_code 160635001` |
| Probe port | `RoyaleFish/patches/probe_x86_64_16635.diff`: x86 hooks, `native_call_x86_64.cpp`, x86↔ARM `logical_{funcs,offsets,vtables}.inc`, resident 16-match engine, NDK bionic build |
| 16.635 catalogs | `RoyaleFish/patches/native_runner_16635.diff` + `RoyaleFish/data_16635/` (123 cards, 453 archetypes, 26 abilities) |
| 16.635 models | `RoyaleFish/models/B-general-v275.pt`, `A-hog26-v738.pt` (17,506,419 params each) |

Their tables and ours agree independently: their patch annotates
`0x10f76b0 /* arm 0x10a7230 */`, and our `offset-diff.json` resolves
`kGoldenKnightHop` to `0x010a7230` at **EXACT** confidence. Same client build,
two ports, same answer.

**What that buys:**

1. A **16.635.1 native x86_64 engine on this host** — no phone, no Android UI,
   16 matches per process — the target ruleset at training scale.
2. A **second relocation table** that closed our 37 AMBIGUOUS + 8 of 10 MISSING
   anchors (N2) without new reverse engineering — **done, §3.33**.
3. **Published 16.635 baselines** instead of our stale General, once the
   framework patch is in. Their model config differs (LSTM 768 vs our 512, 5 vs
   3 spatial res blocks, 16.635 catalogs), so it is a patch, not a drop-in.

### X-Bow 3.0 specifics

- `runs/deck-pool.json` holds **111 X-Bow decks, 2.25% of 443,381 uses**. The
  current meta list (`archers-ev1, knight-hero, tesla-ev1, skeletons,
  electro-spirit, fireball, the-log, x-bow`) is the top one at 2,983 uses.
- FirstLight-Zero plays the classic no-evo list (`XBow, Knight, Archers, Tesla,
  Skeletons, ESpirit, Fireball, Log`) and proves the archetype is learnable from
  **self-play alone**, without human data. It does not prove ladder strength.
- The transfer recipe is daybreak-cr's: one-deck specialist, PFSP league, focus
  + hard decks, an IL anchor (`bc_index`, `bc_coef 0.2`), 55% promotion gate.

### Consequence for the plan

Training on the A51 is now the weakest link, not the strongest. Reorder:
**x86 16.635 → adopt 16.635 catalogs/models → GPU eval volume → X-Bow
specialist → ladder.** The A51 keeps its job as the deploy/parity rig, which is
still the only public in-process ARM64 16.635.1 path.

---

## Adopt from the field — gap → source → action

Take the artifact where it is licensed and version-matched; take the **method**
where it is not. Effort: S < 1 day, M 2–5 days, L 1–2 weeks.

| Gap | Source (license) | Take exactly | Effort / viability | Step |
| --- | --- | --- | --- | --- |
| 16.635 catalogs & model config | RoyaleFish `data_16635/` + `native_runner_16635.diff` (Apache-2.0) | data root + framework patch; 123 cards, 453 archetypes, 26 abilities | S — mechanical, already validated upstream | 1.1 |
| Native 16.635.1 engine at scale | RoyaleFish `probe_x86_64_16635.diff` (Apache-2.0) + `Clashback/game/engine/libg.so` (license unknown, **game asset — local use only**) | x86 call path, `x86_hook.h`, `logical_*.inc`, resident multimatch | M — our tree is already 16.635.1 ARM | 1.2–1.3 |
| 37 AMBIGUOUS / 10 MISSING anchors | RoyaleFish `logical_{funcs,offsets,vtables}.inc` | **done** — `crosscheck_royalefish.py` + `royalefish-overrides.json`; 0 AMBIGUOUS left (§3.33) | S — free second opinion, `0x10a7230` agrees | 6 |
| Modern baselines | `B-general-v275`, `A-hog26-v738` (Apache-2.0) | load + run as eval opponents and as the starting point | S — after 1.1 | 1.1 / 4 |
| Training throughput | RoyaleFish `ever_server/collector/learner/packed/loader` (Apache-2.0) | fp16 with action-equality check, CUDA graphs at several batch sizes, central LSTM state table, sparse trajectories, row-packed updates, loader process | M — **C4 deliverable**; the GPU is the limiter now | C4 |
| Bulk-training backend | crforge `python/` env (Apache-2.0) | `VectorBattle(256, 8)` + tick-exact 16.635.3/8 core | **plan of record** — needs the C1 vectorized adapter; never the final gate | C0–C4 |
| Specialist / league recipe | daybreak-cr settings + `loop.py` (license unknown → **reimplement**) | PFSP, focus + hard-deck shares, `bc_index`/`bc_coef 0.2` anchor, 55% promotion gate, rollback loop | M | 4 / X-Bow |
| X-Bow 3.0 policy | FirstLight-Zero (Apache-2.0) | self-play recipe, feature/model shape, eval protocol; **checkpoints are 15.535.13 — retrain on 16.635, do not load** | M | after 5 |
| Evaluation discipline | ClashAI `RESULTS.md` (license unknown → method) | Wilson intervals, ≥150-game samples per gate, held-out decks, σ reported per comparison | S — pure protocol | 4 |
| Anti-self-deception | Hasty-CR (MIT) | parameter-perturbation probes: flag wins that depend on one card, tile or timing | S — good gate for crforge-trained policies | 4 |
| Ladder orchestration | daybreak-cr `play.py --queue --losses N`, RoyaleBot `rb-build 0.1.2` (MIT), ClashAI | queue/pending-elixir budget, receipt handling, anti-blind-retry | M — reference only, we control the client in-process | N5 |
| Snapshots / lookahead | Clashback `native_snapshots.py` (license unknown) | per-tick encode/load for MCTS or teacher search | L — deferred until the policy is strong | deferred |
| Remaining RE tooling | REA MCP (MIT) | Ghidra/IDA workflows for the leftover anchors | S — optional convenience | 6 |

**Do not take:** leaked clients or redistributed APKs/assets; `rb-build`
binaries as a dependency; any 15.535-labelled weights as 16.635 weights;
in-engine win rates as ladder evidence.

### Keep the A51 connected?

Yes — but as a **parity oracle**, not the training engine.

- It is the only ARM64 in-process 16.635.1 rig we have, and the x86 port needs a
  ground truth from the *same* client build (`160635001`).
- It does **not** need to be running. Leave it plugged in and authorised
  (`adb`, `svc power stayon usb`); launch engines on demand with
  `./launch_cluster_a51.sh --engines 1` for single-match parity, `--engines 4`
  only for capacity comparisons.
- Before starting the port, capture a **parity corpus** on the A51 (step 1.0
  below) so most of the x86 validation can run offline. That is the highest-value
  use of the phone this week.
- Keeping four engines hot 24/7 buys nothing and re-exposes the §3.28 idle
  control-socket wedge and thermal drift.

---

## Current state

| Item | State |
| --- | --- |
| A51 device | **attached** — `SM-A515U`, Android 13, `arm64-v8a`, serial `R58NA0QTHWV` |
| 16.635.1 client | `nullsroyale.rel.free`, `libg.so` SHA `0b580222…e4eaa`, content `16.635.93` |
| 16.635.1 probe | **`b80f2bdb…`** — RoyaleFish-corrected core table **plus the ARM 16.635 rich-vtable anchors**; 9/9 runner hooks install, no mismatch; evaluator now matches x86 (§3.36) |
| 16.635.1 APK | built (`builds/cr-ai-16.635.1.apk`, 841 MB) |
| Cluster APK | `builds/cr-engine-cluster-16.635.1-rf.apk` (`17893d1c…`) built **and installed**; 1 engine attested with the new probe; 4-engine cold-ready path unchanged |
| Probe port evidence | `docs/native/16.635.1/FINDINGS.md` — §3.26 ruleset binding, §3.27 evaluation, §3.28 N0 soak, §3.30 N3 throughput, §3.31 render-gate crash, §3.33 RoyaleFish cross-check, §3.34 corrected core table, §3.36 ARM observation parity |
| General checkpoint | ready, 12.68M params |
| IL_Replay | ready locally, 252,238 replays |
| V4 evaluator | working on 16.635.1; **use `--sample`** (argmax stands still) |
| N0 soak | **10/10 clean**, 0 problems, 93 abilities / 0 rejections, 0 crashes |
| General vs IL baseline | **General 2–0**, 0 rejected, 2/2 normal terminals |
| General vs Hog 2.6 #1 | **General 0–2**, 0 rejected, 2/2 normal terminals (Hog converts by tick 3601 in game 2) |
| General vs Hog 2.6 #2 | **General 0–2**, both in overtime (6120/6118), 0 rejected, 2/2 normal terminals (§3.29) |
| N3 A51 throughput | **4×8: 12.3–14.4 matches/s** at 600 ticks/RPC (a transport ceiling; **0.26** at the PPO 5-tick cadence); 4×16: 14.0–14.5; stable, no crashes (§3.30) |
| A51 → PPO wiring | `a51_engine_node.py` + `launch_ppo_a51.sh` landed; manifest accepted by the topology builder |
| A51 parity corpus | **re-captured on `b80f2bdb…`** — `runs/parity-a51/` + `manifest.json`; scripted seeds unchanged, evaluator now 3601/4794 like x86, capacity 1.918 matches/s, deterministic, 0 rejected (§3.36) |
| A51 node manifest | re-attested on `b80f2bdb…`: `runs/a51-node0/engine-node-ready.json`, 4 engines, `parallel_matches: 32`, digest `ce9d4f85…`; topologies rebuilt |
| PPO deck pool | built: `runs/deck-pool.json`, 7,089 decks / 443,381 uses |
| PPO topology | validated for 1 GPU / 6 vCPU (`worker-cpus 0,1,2,3`, `control-cpus 4`) |
| PPO trainer on this host | **CPU-blocked** (collector is CUDA/NCCL-only), but a **full update ran on a rented A6000** with 0 rejected actions (`runs/gpu-smoke-20261114/`); throughput is the open problem, not correctness |
| x86 PPO worker path | **working** — zlib is preloaded `RTLD_GLOBAL` in `crx_engine_host.py` (a `RTLD_LOCAL` preload made crx's stub return `Z_OK` with a zero-byte payload); the host now fails closed if the probe's zlib imports are unresolved (§3.37) |
| 16.635 remote training | **working on CPU** — engine specs carry `competitive_release` + `model_config`; `B-general-v275` collects through the worker with 0 UNKNOWN effects, 29 lane-decisions/s, 1 engine × 8 lanes (§3.37) |
| Learner-loop batching | **measured; not a win** — 250 ms + gate 1.0 coalesces **3.98–4.00 packets/forward, 321 → 160 forwards**, but the added wait makes it **slower than 2 ms** (271 s vs 288–331 s, §3.40). Keep 2 ms; session 8 then pipelined the loop instead of widening the window (§3.41) |
| Full-rate ACT collection | **working** — `delay_aware_legality` ported (decoder + env + expert, §3.39); gate 1.0 is clean on 5/5 single-engine seeds, the 4-engine shape, and a **10/10 stress gate** (4,097 native parses / 0 failures, 16 consecutive clean segments). The one post-port `env 6` did not recur; `CR_PPO_FAILURE_DUMP` stays armed |
| 16.635.1 x86_64 engine | **running on this host** — `.resource/Clashback/game/engine/libg.so` (sha `c4721bd9…`) booted headless by the crx ELF loader in 0.3 s; no Android/bionic bundle needed |
| 16.635.1 x86_64 probe | **built** — `native_runner/probe/out/x86_64/libcrprobe.so` (`cd303311…`), `--abi x86_64`; all runner hooks install, `attest` reports `production_ready: true` |
| x86_64 multi-engine host | **working** — `crx_engine_host.py --engine-index N` binds engine N to port `26789+N` and CPU N; two concurrent engines verified with one worker run (§3.38) |
| x86_64 parity corpus | **scripted seeds 1–5 and the General-vs-IL evaluator both reproduce `runs/parity-a51`** — identical ticks/actions on both seats after the A51 vtable fix (§3.36) |
| 16.635 catalogs | **adopted as a verified release** — `native_runner/data/competitive-16.635/` + `native_runner/releases/16.635.1.json`; all 5 catalog ids match `B-general-v275` |
| 16.635 models | **`B-general-v275` and `A-hog26-v738` load and play on the A51** (17.5M, LSTM 768, schema 21; 0 rejected in the play smoke) |
| Cloud GPU | **1× A6000 @ $0.35/h ran and was deleted** (~$0.23). Re-rent only for C4/C5 and promotion runs, after C1; the 2026-11-14 smoke sat at **4 %** utilisation |
| Decision-rate reality | native: A51 4×8 @ 5 ticks = **0.26 matches/s** (raw channel); one x86_64 process = **0.61** raw, **0.082** measured *training* floor; learner loop = **0.04–0.05**. crforge: **2.6 / 6.0 / 13.0** matches/s at 1/4/8 threads, 256 lanes, this laptop (2026-11-15 bench); 18.5 / 52.5 on an M4 Pro. Raw ceilings exclude observe + tensorize |
| **crforge bulk path** | **activated 2026-11-15** — `VectorBattle` is the bulk trainer after C0–C3; the native engine is the gate, the parity oracle and the deploy rig. **C0 closed 2026-11-16 (20/20 clean)**. The 10–30× is conditional on the C3 row build (≤0.5 ms/lane); a per-lane port is ~1.4× |
| **Learner deck** | **X-Bow 3.0, locked 2026-11-15** — `x-bow · tesla-ev1 · archers-ev1 · knight-hero · skeletons · electro-spirit · fireball · the-log`, Princess tower `159000000`. All cards/forms pass the 16.635 + crforge catalog check; **rollout-clean 2026-11-17** (2/2 games, `illegal 0`, `rejected 0`) after the Knight-hero global-id fix. Deck constants live in `tools/crforge/bridge_session.py` |
| **Substrate pins** | Clashback `002aa8c` / libg `c4721bd9…` (build `160635001`, local use only) = **the gate**; RoyaleFish `9179bc5` (README-only since `da9c8cf`; models backed up in `.resource/upstream-keep/`) = catalogs + `B-general-v275`/`A-hog26-v738` + `ever_*` (C4 recipe); crforge tables 16.635.8 + data lock. Record all four in every checkpoint artifact |
| Training observation | **lean — done (§3.43)** — non-validating `construct_contract` + `lean_contracts()` scope under `policy_features_only`, no provenance/freeze/validation, cached hand contract and form aliases, `(identity, lean)`-keyed observation caches. Paired A/B: **11.75 → 7.81 CPU ms/packet**, **78 → 113 lane-packets/s** on one x86 engine; 8 lanes 10.6/12.5 → 8.2/9.4 ms. Validated path and parity unchanged |
| Loop pipelining | **done, CPU-verified** — one `ppo-inference` thread, one batch in flight, receives staged during the forward (§3.41). Non-model wall −14 % with a cheap forward, neutral with the 17.5M model (forward = 98 % of wall). 10/10 segments clean. CUDA path unverified until a GPU run |
| Training-floor gates | **superseded by §3.43** — the remaining packet is ~3 ms native round trip + rich binding + placement, not observation contracts. The 0.45/2.5 gates assumed raw-channel ceilings that skip ~90 % of a real packet (§3.41); the observation half of that gap is now closed |
| cr-native proxy | working, 15.535.29 only |
| crforge simulator | `.resource/crforge/` — 16.635.3/8 tables, `VectorBattle` 1–256 lanes, zero-copy NumPy; core 127k ticks/s/thread. Bench: `PYTHONPATH=.resource/crforge/python .venv/bin/python -m crforge_env.bench …` |
| crforge bridge | `crforge_bridge/` — 152/152 cards, 118 forms, `unmapped: []`; evaluation only (20–30 decisions/s with full `BattleEnvV1` assembly) |
| crforge resident adapter | **`native_runner/crforge_resident.py`** — `CrforgeResidentEnv` presents the whole `NativeClashEnv` surface over an in-process `Engine` (bridge `dispatch()`, no socket); `status()` reports `resident: true` / `lanes: 1` |
| C2 parity + throughput | **parity pass, throughput missed** — `tools/crforge/resident_parity.py`: **60/60 decisions identical** (native/rich/atomic envelopes, masks, rewards, terminals); paired `env.step` **1.21–1.53×** the bridge (resident 3.9–4.8 ms, bridge 4.2–7.3 ms). The wire is 1.2–1.7 ms of a 4–6 ms decision; the 2× gate is C3. **Learner deck 2026-11-17: 60/60 identical, paired 1.29×** (`runs/crforge-c2/learner-parity.json`) |
| crforge engine cost | **two shared fixes (2026-11-16)** — run-refusal header reads windowed to `{runTick, runTick+1}` instead of both seats every tick, and one row decode per capture (memoized by generation/tick/command sequence). Both bit-identical to the bridge |
| C0 legality soak | **20/20 clean, 2026-11-16** — `runs/crforge-c0-20261116/soak20-rerun/`: illegal 0, truncated 0, rejected 0, all `king_tower_destroyed` (seeds 7–26) |
| C1 encoder spike | **measured at the default shape** — `runs/crforge-c1/encoder-spike.json`, 256 lanes × 200 × 3: vectorized facts **0.0165–0.0177 ms/lane** (56–61k decisions/s) vs per-lane 0.32–0.35 (**19–20×**), equivalence pass; projected end-to-end **0.713 ms/lane = 1.16 matches/s per process** (**C3 gate met**) |
| C3 tensorizer profile | **measured, 2026-11-16** — `runs/crforge-c3/tensorizer-profile.json`; `tensorize`+`record_action` **0.992 CPU ms/decision** (stock 1.069), entities median 8, **3,754 Python calls/decision**, ~35 % of profiled time in builtin calls plus the frozen-mapping layer (`FINDINGS.md` §3.44) |
| Learner-deck rollout | **clean 2026-11-17** — `runs/crforge-c3/learner-deck/`: 2/2 games, `illegal 0`, `rejected 0`, terminals at ticks 3,681 / 4,032. The tensorizer failure was the Knight hero's signed LogicData global id, not an effect row (`FINDINGS.md` §3.45) |
| Run-refusal naming | **fixed 2026-11-17** — `H_LAST_RUN_REFUSAL` is a `LadderMatch` code (4 = `OVER`), not a `PlayerCommands.Refusal` ordinal; queued plays refused with `OVER` now resolve as `action_canceled_terminal` (`FINDINGS.md` §3.45) |
| `tools/crforge/profile_tensorizer.py` | **new** — replays a live policy episode's tensorizer half under cProfile; capture digest + per-decision `batch_digest` prove an A/B is bit-identical |
| Vectorized observation facts | **new 2026-11-18, extended 2026-11-19** — `crforge_bridge/vector_observation.py` fills a fixed-size batch from `VectorBattle` for all lanes: both seats' headers/cards, all six towers, placement-mask views, champion ability slots (command ticks = a waiting ability), derived `age_ms`/`velocity`. **0.0317 ms/lane** (31,555 decisions/s) at 256 lanes/1 thread, **4.8×** the bridge's per-lane decoders, equivalence pass (`runs/crforge-c3/vector-observation.json`, `FINDINGS.md` §3.46). It is the precondition for C3-B, not the row build |
| Batched group row build | **new 2026-11-19** — `crforge_bridge/vector_rows.py` builds `_groups`+children for all lanes from the facts layer; `VectorEntityTables` compiles card/archetype/child/extent/runtime-form semantics once. **Equivalence pass** over 40 decisions (256 lanes step 0, 32 after), `mismatches: []`; **0.0539 ms/lane** vs 0.2207 per-lane oracle (**4.09×**) at 256 lanes, max 14 entities/lane (`runs/crforge-c3/batched-groups.json`, `FINDINGS.md` §3.47). Section only — the rest of C3-B is open |
| Batched card row build | **new 2026-11-19** — `VectorCardBuilder` fills the actor's eight-row set and the opponent's nine-row set for all lanes, including the **21-column** pending-execution schema (`pending_execution`, target cell with the even-footprint offset, ability pending; tick column neutralised by the checkpoint). **0.00161 ms/lane** vs 0.0718 oracle (**44.5×**), equivalence pass, 4 pending rows max (`runs/crforge-c3/batched-cards.json`, `FINDINGS.md` §3.48) |
| Batched relation row build | **new 2026-11-19** — `VectorRelationBuilder` fills target/reverse-target and card→group edges; tower sources/targets resolve through `VectorTowerSection.tower_index` (the model row, not the native id — the seat-1 fix); `VectorRelationSection.lane(n)` slices back to the per-lane `(1, count)` record. **0.00984 ms/lane** vs 0.0522 oracle (**5.30×**), equivalence pass, 40 edges max (`FINDINGS.md` §3.48, §3.55) |
| Batched candidate row build | **new 2026-11-19** — `VectorCandidateBuilder` fills one row per legal hand slot: card/form/cost, own-card row, the native 32×18 placement grid, building half-footprint + `model_subcell_offset`, UID; `CandidateGeometry` compiles the card-static placement facts once. Slot rule is `BattleEnvV1`'s (in hand, not queued, affordable, ≥1 legal tile), not crforge's tile block. **0.00086 ms/lane** vs 0.0531 oracle (**61×**), equivalence pass over 40 decisions, 5,181 queued slots and 1,171 building rows exercised (`runs/crforge-c3/batched-candidates.json`, `FINDINGS.md` §3.49) |
| Batched tower row build | **new 2026-11-19** — `VectorTowerBuilder` fills the six-row tower section per lane: actor-first/model-kind/native-id order, tile position, kind-sized extent, HP ratio, active and visible-target flags; the model view swaps the princess kinds under a flip. `VectorTowerSection.tower_index` is the `_relations` tower-token map. Pins: `status` empty, `tower_troop_id` static (`159000000`). **0.00104 ms/lane** vs 0.0349 oracle (**33.5×**), equivalence pass over 40 decisions, 61,440 active rows and 61,263 targeted rows (`runs/crforge-c3/batched-towers.json`, `FINDINGS.md` §3.50) |
| Batched spatial planes | **new 2026-11-19** — `VectorSpatialPlaneBuilder` fills the 14-channel board per lane: per-side ground/airborne/building/projectile occupancy, hitpoints, shield and threat; a building fills its footprint, the health/threat channels always mark the entity's cell, and towers/ownerless rows are skipped. Archetype `is_airborne` / `static_damage_basis` / persistent-root facts are compiled into `VectorEntityTables`. Float32 addends preserve the per-lane loop's sequential rounding. **0.00392 ms/lane** vs 0.0499 oracle (**12.7×**), equivalence pass over 40 decisions, 35 building frames (`runs/crforge-c3/batched-spatial-planes.json`, `FINDINGS.md` §3.51) |
| Batched match scalars | **new 2026-11-19** — `VectorScalarBuilder` fills the 18 match scalars per lane: remaining clock, phase and elixir multiplier from the compiled mode timeline, own/enemy elixir and crowns, visible-entity count, group/child/candidate/event ratios, reserved elixir and the static actor identity; no tracker (public `[0,10]` bound) and no events. `_PlayerView` gained `crowns`/`elixir_exact`, `_ObservationView` gained `time`/`phase`/`metadata`. **0.000286 ms/lane** vs 0.00379 oracle (**13.2×**), equivalence pass over 40 decisions (`runs/crforge-c3/batched-scalars.json`, `FINDINGS.md` §3.52) |
| Batched empty records | **new 2026-11-19** — `VectorEmptySectionBuilder` widens the stock zero-row `ActiveEffectSetV4` and masked `EventSetV4` to the lane count; the event padding keeps `owner_type` 2 and group/token `-1`, the sentinels the tensorizer validates. **0.000141 ms/lane** vs 0.0202 oracle (**143×**), equivalence pass over 40 decisions (`runs/crforge-c3/batched-empty-sections.json`, `FINDINGS.md` §3.53) |
| Batched-candidate gate | **new 2026-11-19** — `tools/crforge/batched_candidates.py`; steps 256 real learner-deck lanes past the start cooldown, validates `ActionCandidatesV4` every step, compares every candidate tensor against the stock `_card_sets` → `_groups` → `_candidates` chain, then times both paired |
| Batched-tower gate | **new 2026-11-19** — `tools/crforge/batched_towers.py`; 256 learner-deck lanes, `TowerSetV4` validation and full section equivalence every step, and the two fidelity pins recorded on the artifact |
| Batched-spatial-plane gate | **new 2026-11-19** — `tools/crforge/batched_spatial_planes.py`; 256 learner-deck lanes, full-plane equivalence every step with the per-frame caches cleared per lane exactly as `tensorize` does, paired timing, building/ground cell coverage counters |
| Batched-scalar gate | **new 2026-11-19** — `tools/crforge/batched_scalars.py`; 256 learner-deck lanes, the stock `_card_sets` → `_groups` → `_candidates` chain supplies the oracle's own overflow/candidate counts, phases seen recorded honestly, paired timing |
| Batched-empty gate | **new 2026-11-19** — `tools/crforge/batched_empty_sections.py`; 256 learner-deck lanes, both empty records validated and compared field for field every step, paired timing |
| Batched-group gate | **new 2026-11-19** — `tools/crforge/batched_groups.py`; steps real learner-deck vector lanes, compares every group/child tensor against the stock `_groups` oracle on facts-projected `EntityStateV1` rows, then times both paired over all 256 lanes |
| Batched-card gate | **new 2026-11-19** — `tools/crforge/batched_cards.py`; reads the checkpoint's own `model_config` (21 card columns for `B-general-v275`), compares card and relation tensors against the stock sections every step, then times both paired. This is the gate that proves the 16.x schema, not the default 16-column one |
| Batched-row tests | `crforge_bridge/tests/test_vector_rows.py` (26 tests): 50-decision group equivalence, forced overflow, empty-board masks, reference-entity projection, seat-0 guard, 21-column card equivalence, relation equivalence, pending-cell mirror rule, waiting-ability column, tracker guard, 50-decision candidate equivalence with record validation, the candidate slot rule (pending / unaffordable / empty tile block), the 2×2 vs 3×3 building geometry, the Mirror refusal, tower equivalence, the tower model-view flip, the static tower-troop pin, destroyed/absent tower rows, spatial-plane equivalence with a forced Tesla, the archetype projection for airborne/threat, towers-only/neutral rows, scalar equivalence, the standard phase schedule, the tracker refusal, empty effect/event equivalence with record validation, the empty-section seat guard. Bridge suite **66 tests**, all pass |
| **C4 self-play producer** | **new 2026-11-19** — `tools/crforge/self_play.py` plays one `VectorBattle` (8 lanes), both actor seats in one recurrent forward, decodes both seats back into engine indices, and steps the engine one tick at a time so the second micro-action keeps its delay bin (`plan_window_ticks`). **Gate shape 8 × 10 × 64: 10,240 lane-decisions, 429/9,877 eligible lanes acted (4.34 %), 0 illegal / 0 faults / 0 rejected, 39.0 lane-decisions/s, 97.6 env-steps/s on CPU**; the forward is 359.7 of ~410 ms per decision. Autoreset run (4 lanes, `--max-ticks 600`): 4 resets, 29/790 acted, 0 rejected. Rollout storage on by default since session 27 (`--no-rollout` is the probe). `runs/crforge-c4/self-play.json`, `-autoreset.json`, `FINDINGS.md` §3.57, §3.58 |
| **C4 rollout storage** | **new 2026-11-19** — `crforge_bridge/rollout.py`: the native PPO reward (terminal result + potential shaping over crowns/tower damage, gated against `BattleEnvV1._rewards` and against the engine's own terminal reward) and one validated `StoredRolloutV4` per segment with a boundary bootstrap. **Gate shape unchanged: 10,240 lane-decisions, 429/9,877 ACT (4.34 %), 0 rejected, 37.8 lane-decisions/s; 0 reward/terminal mismatches; 57.2–57.4 MiB per 64-decision segment, 573.1 MiB total.** Autoreset run: 4 autoresets → 8 mid-rollout truncations + 8 `episode_start` rows, GAE finite. Stored log-prob/value now escape `inference_mode` so PPO can backprop them. **`runs/crforge-c4/self-play-rollout.json`, `-autoreset-rollout.json`, `FINDINGS.md` §3.58** |
| **C4 PPO update** | **new 2026-11-19, gate green** — `distributed_ppo_update_v4` splits the time axis at every episode boundary (`_episode_segments`/`_episode_chunks`), so a mid-segment autoreset replays as contiguous slices while non-resetting lanes keep their state; the fused replay still refuses a mid-reset inside one call. `self_play.py --ppo-update` runs one update per finalized segment over the same `StoredRolloutV4`, and the store binds a weights hash (`3835a22f…` → `899ef703…` across the update). **Gate shape 8 × 64: 1,024 lane-decisions, 45/983 ACT (4.58 %), 0 rejected, 1,024 update frames, `approx_kl` 2.9e-5, clip 0, gradient norm 1.15.** **Multi-episode 8 × 2 × 60 with `--max-ticks 600`: 1,920 lane-decisions, 56/1,559 ACT (3.59 %), 0 rejected, 16 mid-rollout `episode_start` rows, 2 × 960-frame updates, both pass.** Fresh AdamW per segment (gate only); negative segment-1 value EV (−0.59) recorded as a mechanics gate, not a learning claim (`runs/crforge-c4/self-play-update.json`, `-update-rollout.json`, `FINDINGS.md` §3.59) |
| **Second micro-action probe** | **new 2026-11-19** — `self_play.py --force-second-micro {shift,drop,alternate}` appends a different legal play at the sampled play's own delay bin; a duplicated play is refused by the engine (the queued slot closes). 8 lanes × 40 decisions: **22 forced, 18 shifted, 4 dropped, 0 illegal, gate pass** (`runs/crforge-c4/self-play-two-micro.json`) |

### State at handoff (processes are ephemeral)

No long-lived probe endpoint is needed. Both oracles start on demand:

```bash
# x86_64 engine + probe on this host (port 26789)
.venv/bin/python tools/native_analysis/crx_engine_host.py --seconds 1800
# independent host engines: 26789+N and control cpu N
for i in 0 1 2 3; do
  .venv/bin/python tools/native_analysis/crx_engine_host.py \
      --engine-index "$i" --seconds 1800 &
done
# A51 ARM64 oracle (phone attached; engine 0 fullscreen)
./launch_cluster_a51.sh --engines 1 --window-mode plain --base-port 26799
```

The x86 host boots in ~0.3 s and can be restarted at will. The A51 waits for
`adb`; re-run the launcher if the phone slept. The parity gate is reproducible
without the phone:

```bash
.venv/bin/python tools/native_analysis/parity_manifest.py \
    --corpus runs/parity-x86 --compare runs/parity-a51/manifest.json \
    --output runs/parity-x86/manifest.json   # exit 0 / "reproduces: True"
```

`runs/parity-a51/` is the archived ARM corpus, `runs/parity-x86/` the matching
x86_64 candidate; both are gitignored but local. The reproducible tools are
`crx_engine_host.py`, `scripted_xbow_match.py`,
`resident_batch_throughput.py`, `multi_decision_match.py`,
`parity_manifest.py` and `tests/test_parity_manifest.py`.

The 4-engine A51 cluster used for re-attestation was stopped at the end of the
session. To resume GPU prep:

```bash
./launch_cluster_a51.sh --engines 4
.venv/bin/python tools/native_analysis/a51_engine_node.py \
    --serial R58NA0QTHWV --engines 4 --output runs/a51-node0/engine-node-ready.json
.venv/bin/python -m native_runner.training.v4.build_ppo_multinode_topology \
    --node-ready runs/a51-node0/engine-node-ready.json \
    --output runs/topology-a6000-1gpu.json --trainer-job-id 1 \
    --trainer-host "$(hostname -s)" --gpus 1 \
    --worker-cpus 0,1,2,3 --control-cpus 4
```

The 4-engine attestation digest is `ce9d4f85…`; the 2-GPU topology needs
`--control-cpus 4,5` (one control CPU per rank).

### N3 — the windowing bug that capped throughput

> **Unit caveat (2026-11-15):** the 12–14 matches/s below is a **600-tick
> transport ceiling**, not a PPO training rate. At the 5-tick cadence the same
> 4×8 A51 shape is **0.26 matches/s**, and its learner loop **0.04–0.05**. It is
> kept as evidence for the cpuset fix, not as a throughput claim.

Every engine except the resumed activity lands in Android's `background`
cpuset, which is pinned to **CPUs 0–1** (little cores). The default
`launch_cluster_a51.sh` therefore gave one fast lane and one ~3x slower lane.
Keeping all engines visible fixes it:

- engine 0 fullscreen, engine 1 split-screen-secondary, engines 2+ freeform
  (plus `force_resizable_activities` / freeform settings);
- big-core CPU map `4,5,6,7`;
- `cached_apps_freezer disabled` (the §3.28 idle control-socket wedge).

`launch_cluster_a51.sh --window-mode visible` (default) does this and reports
each engine's schedtune cgroup. One A51 now supplies ~12–14 matches/s at the
PPO shape (4 engines × 8 lanes) and ~14.5 at 4×16 — well above host CPU
inference (~1 match/s), so **no cloud engine cluster is needed**.


### 16.635.1 port status (verified on the A51)

From `docs/native/16.635.1/FINDINGS.md`:

- Touch JNI hook is **port-ready** (`0x0156f790`).
- Hand/deck model offsets and the replay-controller trio are recovered.
- The probe is build-selectable for 16.635.1; required anchors fail closed.
- Relocation: **53 EXACT, 23 LIKELY, 70 MANUAL, 0 AMBIGUOUS, 2 MISSING**
  (2026-11-14 cross-check with RoyaleFish, §3.33). The two remaining rows are
  `kTranslatedSha256InitializerCall` and `kCombatHealEffectReturn`.
- **Cold configure is open**: the content-ready byte is `0x720` (§3.13), and a
  fresh `MatchConfig` reaches `ready/matchReady=true`.
- **Live command injection works**: two-sided card plays, abilities, entity/HP
  observation and the scripted X-Bow policy all run with 0 rejections
  (§3.14–§3.22).
- **Rich telemetry is mixed**: object/phase/ability/HP streams are live, but the
  native hook rings (combat events, phase/special-movement/action-movement,
  character state, visibility, remaining, tower troops) report `unavailable` on
  this build. `observe_rich` still validates because those streams fail closed.
- The V4 evaluator now binds the live 16.635.1 identity explicitly
  (`native_runner/versions/native-16.635.1.json`, §3.26); the local card catalog
  remains the pinned 15.535.86 release, with the level-11 parity caveat.

This is finite reverse-engineering work with existing tools, not a missing model
or dataset. The relocation table is now closed (0 AMBIGUOUS, 2 genuinely
missing — §3.33/§3.34); the remaining N2 items are installing the rich hook
rings from the recovered 16.635 offsets and fixing the render gate. They do not
block N1 evaluation or a CPU baseline.

---

## Session status — N0, N1 and N3 passed

Done on the attached `SM-A515U` (`R58NA0QTHWV`):

1. **Engine bring-up.** Two engines cold-ready for N0/N1
   (`nullsroyale.rel.free:engine0/1`, ports `26789`/`26790`), later four for
   N3 (`26789`–`26792`), identity verified against the 16.635.1 pin.
   Operational: keep `adb shell svc power stayon usb` on. After long idle a
   background engine's control socket can stop answering while the process is
   still alive (seen on `:engine0`); `launch_cluster_a51.sh` force-stops and
   relaunches both engines, which restores it. The launcher now also disables
   `cached_apps_freezer`, which is the likely cause.
2. **Scripted soak.** 10/10 matches, 0 problems, 93 abilities with 0 rejections,
   empty crash buffer (`FINDINGS.md` §3.28).
3. **`observe_rich`.** Validates; object/HP/phase/ability streams live, native
   hook rings `unavailable` as expected (§3.26/§3.27).
4. **N1 baseline set.** General **2–0 over IL**, **0–2 to Hog26-specialist1**,
   both seats, sampled, 0 rejected actions, all normal terminals (§3.27,
   §3.29). General's volume is healthy; the Hog specialist converts earlier.
5. **N3 throughput.** Four engines on big cores in visible windows give
   **12.3 matches/s** at 4×8 lanes and **14.0–14.4** at 4×16, stable over
   eight runs, no crashes. A backgrounded engine is pinned to CPUs 0–1 and
   loses ~3x; `--window-mode visible` fixes it (§3.30).
6. **A51 → PPO wiring.** `tools/native_analysis/a51_engine_node.py` attests the
   cluster and writes a `v4-ppo-engine-node.v1` manifest; `launch_ppo_a51.sh`
   launches the cluster, attests it and starts `remote_ppo_worker_server`. The
   manifest builds a valid `v4-ppo-multinode-topology.v1` topology.
7. **`offline_agent.py` / `offline_duel.py`** now bind the live 16.635.1
   ruleset and honour `combat_ring_hooks: false` (they no longer request the
   combat-ring feed). **They still crash on the native-render path**: the probe
   SIGSEGVs in `cr_render_gate_swap_buffers+996` (§3.31). Use the headless
   evaluator; fixing the render gate is an N2 item.

**Blocker for a local PPO smoke:** the production collector is CUDA-graph-only
(`_persistent_rollout_graph_v4` raises when a graph is unavailable; the trainer
uses NCCL and `torch.cuda.*` unconditionally). There is no CPU path, so the
first real PPO segment/update needs the rented GPU. The A51 node plumbing is
ready for it.

## GPU smoke 2026-11-14 — results and what they mean

Run: ThunderCompute 1× RTX A6000 (48 GB), 6 vCPU, 48 GB RAM, driver 610,
`torch 2.5.1+cu121` in a venv; A51 over a reverse SSH tunnel on `29900`
(`-R 127.0.0.1:29900:127.0.0.1:29900`). Evidence: `runs/gpu-smoke-20261114/`
(`metrics.jsonl`, `config.json`, `ppo-smoke.log`).

| Gate | Result |
| --- | --- |
| Trainer ↔ worker ↔ engine wiring | **pass** — 4 engines, 32 lanes, tunnel transparent |
| `native_rejected_actions` | **0** |
| Checkpoint | `update-00000001.pt`, 145,702,430 B, sha `9566a110…02654` |
| Optimizer / update step | written at a strict boundary (resume contract saved) |
| GPU peak allocation | **6.33 GiB** (not the <2 GB estimated; still trivial for 48 GB) |
| GPU utilisation during collection | **4 %** — the engine feeds it |
| All 32 matches reaching terminal | **no** — 32/32 truncated by the segment budget |
| `eligible_act_rate` | **0.0** — 5,120 eligible ticks, all `WAIT` |
| Wall time for one 40 s segment | 266 s collection + 11 s update |
| Cost | ~$0.23 for the whole session |

### Two configuration bugs, both fixed

1. **Gate temperature.** `DEFAULT_GATE_TEMPERATURE_START = 0.20` is flat and
   sharpens an already-passive gate (General takes `ACT` ≈ 6 % at 5 ticks).
   Result: 0 actions in 5,120 eligible ticks, i.e. no policy signal at all.
   The launcher now honours `CR_PPO_GATE_TEMPERATURE_{START,END,ANNEAL_UPDATES}`;
   collect at `1.0` and anneal to `0.2`.
2. **Deck pool.** The trainer requires **exactly 7,695 decks / 20 focus decks**.
   The local corpus yields 7,089 at `--minimum-uses 5` and 8,530 at 4 — the
   upstream number is unreachable locally. Added `--allow-unverified-deck-pool`
   (still requires ≥5,000 decks + exactly 20 focus decks) and
   `native_runner/training/v4/select_focus_decks.py`, which derives a
   deterministic focus list from observed usage.

Also fixed: `CR_PPO_SEED` defaults to 0, which trips the positivity check — set 1.

### Why the A51 cannot train this policy

The resident channel costs ~150 ms per framed request regardless of payload, and a
PPO decision is only 5 ticks. Measured with `resident_batch_throughput.py`:

| Engine | Config | Capacity | Notes |
| --- | --- | --- | --- |
| 4× A51 | 8 slots, 5 ticks | **0.26 matches/s** | 0.042 / 0.040 / 0.092 / 0.085 per lane; makespan 0.161 |
| 4× A51 | 8 slots, 600 ticks (old §3.30) | 12–14 matches/s | amortises the same fixed cost 120× |
| 1× x86_64 | 8 slots, 5 ticks | **0.61 matches/s** | 296 actions/s |
| 1× x86_64 | 16 slots, 5 ticks | **0.616 matches/s** | 299 actions/s, CPU-bound at ~3,700 slot-ticks/s |

So the §3.30 number was a coarse-tick transport ceiling, not a training rate.
Inside the learner loop the A51 is worse still: **19 lane-decisions/s**, i.e. a
21× gap on top of the 0.26 ceiling. Per-iteration accounting for one packet:
~416 ms wall, of which policy forward is 27 ms and assembly 4 ms; the rest is the
closed loop (engine round + worker decode + action application). The receive
executor already runs one thread per engine, so parallel reception is not the
problem; the loop still resolves **one packet per forward**
(`inference_mean_frame_packets: 1.0`, batch ≈16 rows, `_INFERENCE_BATCH_WAIT_SECONDS = 0.002`).

### What to do next (CPU-only first — no GPU spend)

> **Historical (2026-11-14).** Items 1–4 below are done; item 3's 250 ms window
> was later measured as a net loss (§3.40) and the loop was pipelined instead.
> The live next-session plan is the session-13 section at the top of this file.

**Done 2026-11-14 (session 3):** the CPU profiling harness exists and the real
loop runs without a GPU. See "Learner-loop profiling" below for numbers and for
the three new blockers it exposed.

**Done 2026-11-14 (session 4):** all three blockers are closed (`FINDINGS.md`
§3.37). The x86 engine is now a *training* substrate, not just a control/eval
oracle: `profile_real_collection.py --checkpoint B-general-v275.pt` collects a
48-decision, 8-lane segment through the CPU PPO worker with 0 UNKNOWN effects,
0 fallbacks, and the 16.635 catalogs/21-column schema in place.

1. **Profile the learner loop without a GPU — done.** Transport floor and the
   real loop are both measured; remaining cost is the CPU forward.
2. **Make the x86 engine the training substrate — done.** `B-general-v275`
   collects on one local engine. The official x86_64 build is content 16.635.8,
   so it trains **16.635-contract** checkpoints (B-general/A-hog26); a
   15.535-contract checkpoint still needs the A51 or an adaptation step.
3. **Learner-loop batching — done (`FINDINGS.md` §3.38).** The window must be
   about one engine round-trip, not 20–40 ms. With four A51 engines and 8 lanes,
   `CR_PPO_INFERENCE_BATCH_WAIT_MS=250` gives **3.88 packets/forward and 33
   forwards instead of 64**; 2/30/100 ms stay at 2.0, and 150 ms only reaches
   2.56. CPU wall time is flat (model-bound), so the win is per-forward fixed
   cost — the thing the GPU paid while idle. Use 250 ms for the GPU smoke and
   re-measure if engine count or lanes change. Multi-engine x86 now works:
   `crx_engine_host.py --engine-index N` → port `26789+N`, CPU N.
4. **Delay-aware candidate legality — done (`FINDINGS.md` §3.39).** The
   decoder, environment action mask and compound validator now check elixir at
   the touch delay bin with the match multiplier, matching the 16.635 engine.
   Gate temperature 1.0 is clean: 5/5 single-engine seed segments, the 4-engine
   shape at 28.5 lane-decisions/s, 3,979 native command parses and 0 failures.
   One failure seen immediately after the port did not reproduce in six later
   segments; `CR_PPO_FAILURE_DUMP` now records the actions if it returns, and
   the probe's `CRNativeProbe` parse log separates injection from capture.
5. **Re-measure before renting.** Rent only when a local segment completes in
   <10 min; the GPU is idle 96 % of the time at the current feed rate.

### Learner-loop profiling — 2026-11-14 (session 3)

Two tools, both CPU-only and both committed:

| Tool | What it measures |
| --- | --- |
| `tools/native_analysis/profile_cluster_collection.py` | transport floor — speaks the worker protocol, answers every frame with a scripted all-WAIT batch; no model, no storage |
| `tools/native_analysis/profile_real_collection.py` | the real `collect_async_cluster_wave_v4` loop on CPU (model + tensorizers + rollout storage) |

`async_cluster_self_play.py` now falls back to `_EagerRolloutSamplerV4` on a
non-CUDA device instead of requiring a CUDA graph, so the collector runs locally.
Covered by `native_runner/tests/test_training_v4_cpu_sampler.py`.

| Measurement | Packets/s | Per packet | Notes |
| --- | --- | --- | --- |
| Scripted, 1× A51, 8 lanes | **4.25** | 235 ms | transport + worker only |
| Scripted, 4× A51, 8 lanes | **10.6** | 94 ms | 2.66/engine, not 4 — already serialising |
| Scripted, 4× A51 + 30 ms model | **9.75** | 103 ms | model cost is *not* the problem |
| Scripted, 1× x86_64, 8 lanes | **13.3** | 75 ms | 3× the A51 per decision |
| Real loop, 1× A51, CPU model | **1.68** | 596 ms | model 256 ms/packet on CPU |
| Real loop, 4× A51, CPU model | **1.54** | 649 ms | trainer-bound: `ready_wait` is only 22 % |
| **Real loop, 4× A51, A6000 (the paid run)** | **2.4** | **416 ms** | `ready_wait` 91 %, GPU 4 % |

Subtracting model time, the non-model cost is **~340 ms/packet on CPU and ~390 ms
on the GPU box** — the same number, so the CPU harness reproduces the paid run's
bottleneck. A scripted reply costs 235 ms/packet, so ~100–150 ms is loop
bookkeeping on top of the engine.

**Diagnosis:** the loop keeps exactly one packet in flight per engine and
replies serially, so per-engine latency is paid end to end. `ready_wait` grows
with engine count and `_INFERENCE_BATCH_WAIT_SECONDS = 0.002` never lets ready
packets coalesce at engine speed. With four engines the trainer is the serial
resource; the GPU is idle while it waits.

### Three new blockers found while profiling

1. **The x86 worker path is broken — FIXED 2026-11-14 session 4
   (`FINDINGS.md` §3.37).** `profile_cluster_collection.py` against the local
   x86 engine failed on the first batch with `Error -5 while decompressing data:
   incomplete or truncated stream`. It was not the engine: crx resolves probe
   imports through `dlsym(RTLD_DEFAULT)`, and `crx_engine_host.py` had preloaded
   `libz.so.1` **`RTLD_LOCAL`**. crx's missing-symbol stub then returned
   `0 == Z_OK` for `compress2`, so every "compressed" batch entry shipped as a
   zero-byte zlib payload. Fixed by preloading zlib `RTLD_GLOBAL`; the host now
   also refuses to attach the probe if `compress2`/`compressBound` are
   unresolved. Probe hashes are unchanged.
2. **The worker was hard-wired to the 16-column schema — FIXED.** `build_episode_tensorizers_v4` was called without a model config, so a 16.635
   checkpoint failed with `mat1 and mat2 shapes cannot be multiplied (128x16 and
   21x128)`. `AsyncResidentEngineSpecV4` now carries `competitive_release` and
   `model_config`, derived in `collect_async_cluster_wave_v4` from the actor
   model so a remote worker cannot disagree with it. Mixed anchor/history
   releases are rejected at startup.
3. **The 15.535 catalogs cannot tensorize some 16.635 entities — FIXED for
   16.635-contract checkpoints.** `active public effect resolved to UNKNOWN:
   buff:…` on the 16.635.8 x86 engine. The worker now binds the checkpoint's
   release (`use_release`) around engine construction and reset, because
   `BattleEnvV1` re-verifies the semantic subset against the *active* release's
   form evidence. `B-general-v275` now collects with 0 UNKNOWN effects. A
   15.535-contract checkpoint (General) still fails on 16.635 effect ids — that
   is correct and means the x86 substrate needs a 16.635 checkpoint.

---

## Next session — start here

> **Historical (2026-11-14).** Steps 1–5 in this section are done; the live
> next-session plan is the **session-13** section at the top of this file
> (C0 legality, then the C1 encoder spike). Kept for the runbooks and reference
> numbers.

### 0. Already done at this handoff (no compute spent)

| Step | Result |
| --- | --- |
| **RoyaleFish cross-check (§3.33)** | **done** — 145 named x86→ARM rows compared; **0 AMBIGUOUS** left (was 33), 8 of 10 MISSING recovered; 7 core corrections, 56 adopted overrides |
| **Corrected core table on device (§3.34)** | **done** — probe `db9d5609…`, APK `9e345ad3…`; 9/9 hooks install, scripted seeds 1–2 identical to §3.32, 0 rejected, 0 crashes |
| **ARM observation parity (§3.36)** | **done** — probe `b80f2bdb…`, APK `17893d1c…`; 16.635 rich vtables emitted for ARM; General-vs-IL evaluator now **3601 / 4794** on both seats, identical to x86; `runs/parity-a51` re-captured and re-pinned |
| Hog26-2 result | **0–2**, both overtime (6120/6118), 0 rejected — in `FINDINGS.md` §3.29 |
| **A51 parity corpus (1.0)** | **done** — `runs/parity-a51/` + `manifest.json`; seeds 1–5 + seed-1 repeat, General 2–0 IL (`--sample`), 1×8 capacity; deterministic, 0 rejected — `FINDINGS.md` §3.32 |
| Deck pool | `runs/deck-pool.json` — **7,089 decks**, 443,381 player uses from 252,238 replays |
| A51 cluster | 4 engines launched and attested on `26789`–`26792` this session; **stopped at handoff** (idle phone, launch on demand) |
| Node manifest | `runs/a51-node0/engine-node-ready.json`, probe `b80f2bdb…`, `parallel_matches: 32`, digest `ce9d4f85…`; topologies rebuilt |
| Worker service | `remote_ppo_worker_server` on `127.0.0.1:29900` |
| Topology (1 GPU / 6 vCPU) | builds and validates: `runs/topology-a6000-1gpu.json` |
| Topology (2 GPU / 6 vCPU) | builds and validates: `runs/topology-a6000-2gpu.json` |

All of it is CPU/phone work. Build the deck pool and IL cache **before** the
meter starts.

### 1. x86_64 16.635.1 — **done** (see §3.35 and §3.36)

The engine runs on this host through the crx ELF loader; the probe builds with
`--abi x86_64`; scripted X-Bow seeds 1–5 **and** the General-vs-IL evaluator
reproduce `runs/parity-a51` decision-for-decision. Reproduce with:

```bash
.venv/bin/python tools/native_analysis/crx_engine_host.py --seconds 1800 &
.venv/bin/python tools/native_analysis/scripted_xbow_match.py --seed 1 \
    --output runs/parity-x86/x86-seed1.json
CR_PROBE=native_runner/probe/out/x86_64/libcrprobe.so CR_NATIVE_BUILD_ABI=x86_64 \
  .venv/bin/python tools/native_analysis/parity_manifest.py \
    --corpus runs/parity-x86 --compare runs/parity-a51/manifest.json \
    --output runs/parity-x86/manifest.json
# evaluator gate: must print the same 3601 / 4794 rows as runs/parity-a51
CR_PROBE=native_runner/probe/out/x86_64/libcrprobe.so CR_NATIVE_BUILD_ABI=x86_64 \
  .venv/bin/python -m native_runner.training.v4.evaluate \
    --checkpoint-a checkpoints/General/checkpoint-step-00000460.pt \
    --checkpoint-b checkpoints/IL/checkpoint-step-00029396.pt \
    --games 2 --sample --seed 7 --port 26789 \
    --output runs/parity-x86/x86-model-general-vs-il.json
```

**RESOLVED — evaluator parity (§3.36).** The root cause was the one-sided
vtable fix described in §3.35: on ARM the rich-classification constants were
still gated to x86_64, so the probe fell back to the 15.535 literals
(`entities[].entity_kind`, `projectile_state`, `runtime_provenance`). The ARM
16.635 header now emits the nine `offset-diff.json` vtable anchors, the probe
selects them on both ABIs, and the A51 runs probe `b80f2bdb…` / cluster APK
`17893d1c…`.

General-vs-IL, seed 7, both seats, `--sample`: game 1 **tick 3601, actions
25/27**, game 2 **tick 4794, actions 40/37** — identical to x86_64. Scripted
seeds 1–5 are unchanged; `runs/parity-a51` was re-captured and the ARM pin now
records `b80f2bdb…`; `out/libcrprobe.so` was refreshed to match. Do not
resurrect option B (mirroring the stale table on x86).

1. **1.0 — Parity corpus from the A51 — DONE (`FINDINGS.md` §3.32).**
   `runs/parity-a51/` holds seeds 1–5, a seed-1 repeat (decision-identical
   after dropping latency), two `evaluate.py --sample` games (General 2–0 IL)
   and one `resident_batch_throughput.py` run (2.07 matches/s, 1×8).
   `runs/parity-a51/manifest.json` archives the probe/checkpoint/APK hashes and
   the determinism check. The phone may now idle; re-capture only if the probe
   or client changes. The x86 port must reproduce the corpus:

   ```bash
   .venv/bin/python tools/native_analysis/parity_manifest.py \
       --corpus <x86-corpus> --compare runs/parity-a51/manifest.json \
       --output <x86-corpus>/manifest.json   # exit 0 == gate passed
   ```
2. **1.1 — 16.635 catalogs adopted; `B-general-v275` loads and plays — DONE.**
   Do **not** apply `RoyaleFish/patches/native_runner_16635.diff`: it is based on
   a different 15.535 tree and gets 16.635 data through by disabling SHA checks.
   Instead, the 16.635.1 catalogs are now a first-class, verified release:

   - descriptor `native_runner/releases/16.635.1.json`, data
     `native_runner/data/competitive-16.635/` (1.5 MB);
   - `native_runner/competitive_release.py` selects releases with a
     `contextvars` context, so 15.535 and 16.635 bundles coexist in one process;
   - the compiled catalogs were adopted from `RoyaleFish/data_16635`
     (Apache-2.0) and verified against `B-general-v275`'s contract. Three repair
     steps made the bundle self-consistent without changing catalog semantics:
     stale `model_catalogs.input_catalog_ids`, stale 15.535 hashes inside
     `form_evidence`, and an incorrect manifest. Full detail:
     `docs/native/COMPETITIVE_RELEASES.md`.
   - all five built catalog ids equal the published checkpoint contract
     (`card 8c0baac9…`, `ability 128b6679…`, `entity_archetype 140e32c1…`,
     `effect a5bf4db4…`, `mechanic_profile 00b73f61…`).

   `load_policy_v4()` now reads the checkpoint contract: it resolves the release
   by catalog ids, builds `ModelConfigV4` from `contract.model_config`, and loads
   strictly. `B-general-v275` and `A-hog26-v738` load as **17,506,419 params,
   LSTM 768, spatial 128/5, runtime schema 21**; General/IL still load as
   12,684,339 params on the 16-column schema.

   **Play smoke passed (A51, seed 11, one game):** B-general vs General, both
   loaded in one process, `native_final_result` at tick 6089, 56/55 actions,
   **0 rejected**, empty crash buffer. General won this single game — that is a
   plumbing gate, not a strength result. `contract_compatible()` tolerates only the declared
   additive config keys, so an older checkpoint still loads and a real dimension
   change still fails.

   The two schema additions this required:
   - `CARD_RUNTIME_FEATURE_NAMES_V2` (16 + 5 pending-execution columns) and the
     tensorizer block that fills them from `observation.pending_actions`;
   - the additive `ModelConfigV4` fields (`neutralize_pending_ticks_remaining`,
     `delay_aware_legality`, `card_id_gate`, `ablate_*`).

   **Caveat (updated 2026-11-14 session 6):** `delay_aware_legality` is now
   branched on in the decoder, the environment action mask and compound
   validation (§3.39). Only the ablation/gate switches are still carried for
   exact round-tripping. B-general collects at full ACT rate with 0 rejected
   actions; its *strength* is still unvalidated until a paired gate runs.

   Note for future catalog work: recompiling from the local `Clashback` assets
   does **not** reproduce these ids — those assets are a different 16.635
   content revision (all 258 source-file hashes differ, and only 25 abilities
   bind). The compiled 16.635 set is the version-matched source for now.
3. **1.2 — x86 probe — DONE.** `native_runner/probe/out/x86_64/libcrprobe.so`
   builds with `native_runner/probe/build_probe.sh --target-build 16.635.1
   --abi x86_64`. Added the x86 call path, `x86_hook.h`, the logical
   offset/function/vtable tables, and `build_16_635_1_layout_x86.h`. The rich
   rings stay ARM-only exactly as on the A51; the JSON path needed its own key
   ABI (see §3.35 — the mirror of §3.34's trap).
4. **1.3 — host — DONE, without the bionic bundle.** `.resource/Clashback/vendor/crx`
   maps the x86_64 engine into a plain Linux process with glibc shims. The probe
   attaches with `cr_probe_attach(libg_base)`; `tools/native_analysis/crx_engine_host.py`
   boots the engine, installs the presentation guards, attaches the probe and
   serves the usual `cr-native-control.v1` port. The pinned 15.535.29 bionic
   runtime is no longer needed for 16.635.1.
5. **1.4 — GATE: PASSED.** Scripted X-Bow seeds 1–5 reproduce the A51 corpus
   decision-for-decision (`first_difference: null`), 0 rejected actions, normal
   terminals, and the seed-1 repeat is deterministic. After the A51 vtable fix
   (§3.36) the General-vs-IL evaluator also matches: both seats terminate at
   the same ticks/actions as x86_64 (3601 and 4794), with 0 rejected actions.
   `attest` is `production_ready` against
   `native_runner/versions/native-16.635.1-x86_64.json`. The x86 engine is now a
   **ruleset, scripted and evaluator** oracle.

The phone is no longer the training engine. The GPU line below is about
evaluation volume and PPO updates, not about rescuing a 4-engine farm. The phone
stays plugged in as the ARM64 oracle (`--engines 1` on demand).

### 2. Then buy this: 1× RTX A6000, `--gpus 1` — **done; box deleted, re-rent only when the engine feed justifies it**

| Option | $/h | Hours for $20 | Verdict |
| --- | --- | --- | --- |
| **RTX A6000 ×1** | **0.35** | **~57** | **Used for the first smoke; 6.33 GiB peak, 4 % utilisation** |
| RTX A6000 ×2 | 0.35 claimed | ~57 | Only if the 0.00 second GPU survives checkout — and still run `--gpus 1` |
| L40 ×1 / ×2 | 0.79 / 1.58 | 25 / 12.7 | 2.3× price for FLOPs this model cannot use |
| A100 80 GB ×1 / ×2 | 1.09 / 2.18 | 18 / 9 | 3.1× price; 80 GB VRAM and 24 vCPU are both irrelevant |

**Why the A6000 is not a compromise.** The model is 12.7M params today (17.5M
with the 16.635 config): ~70 MB fp32 weights, ~210 MB with Adam moments, and
activations for `--lane-minibatch-size 384` × `--sequence-chunk-steps 16` are
small — the update fits well under 2 GB, so 48 GB is 20–40× headroom. The load
is set by the engine, not the GPU:

| Engine | Actor rate | Batched-forward budget |
| --- | --- | --- |
| 4 A51 engines × 8 lanes | ~15.6k decisions/s | ~2 ms @ 32 lanes |
| x86_64, 16 matches/process × 8 | ~62k decisions/s | ~2 ms @ 128 lanes |

Either way Ampere clears it with the CUDA-graph replay path in
`async_cluster_self_play.py`; a bigger card buys latency margin the engine
cannot consume. `torch==2.5.1` + cu121 covers sm_86 (A6000) and sm_89 (L40).

**Do not start on two ranks.** `--gpus N` becomes
`torch.distributed.run --nproc-per-node=N` and splits the engines across ranks
(`rank_engine_indices [[0,2],[1,3]]` for the A51). That adds NCCL init and DDP
averaging to the first smoke for throughput the engine cannot produce. Split
ranks later, when the x86 farm is large enough that one GPU is genuinely
saturated — that is an evaluation/update problem, not a training-start problem.

**The $0.00 second A6000 is not a plan.** It is almost certainly the per-unit
price echoed onto the second slot. Read the provider's hourly estimate before
starting. If a 2-GPU box genuinely bills $0.35/h, take it and still pass
`--gpus 1` — a free spare GPU helps `serve_policy`/evaluation, not training.

**Skip the A100 host.** In remote-worker mode the tensorizers run on the
engine-node host; the GPU box only runs the rank, and `_bind_rank_cpus` needs
one worker CPU per engine plus ≥1 control CPU. With 4 engines,
`--worker-cpus 0,1,2,3 --control-cpus 4` validates on 6 vCPU. 24 vCPU/192 GB
buys nothing. If the x86 engine runs *on the GPU box*, CPU:GPU ratio flips and
16 vCPU is the useful upgrade — check that after step 1, not before.

### 3. Pre-flight on the GPU box, before a long run

- torch **2.5.1 + cu121** wheel, not the local `+cpu` build.
- The A51 is behind NAT and the trainer needs **one** inbound port (`29900`).
  Reverse-tunnel it from this laptop instead of exposing anything:
  `ssh -N -R 29900:127.0.0.1:29900 <gpu-box>`
  The manifest already records `remote_worker_host: 127.0.0.1`, so the tunnel is
  transparent — no manifest edit and no `--worker-host 0.0.0.0`.
- `--trainer-host` must equal `hostname -s` **on the GPU box**; the local `.env`
  has empty `CR_PPO_*`, so export `CR_PPO_DECK_POOL`, `CR_PPO_CHECKPOINT`,
  `CR_PPO_TOPOLOGY`, `CR_PPO_OUTPUT_DIR`, `CR_AI_PYTHON` there (environment
  wins over `.env`).
- ~20 GB disk (repo + 1.8 GB dataset + checkpoints).
- Confirm preemption policy; if spot, `--snapshot-every 1`.
- Keep the phone awake: `adb shell svc power stayon usb`.

Budget plan: 3–4 h (~$1.4) for the smoke + a few hundred paired eval games,
then stop and decide. Evaluation volume, not engine hours, is what $20 buys:
at ~2 s/game on an A6000 a 400-game paired gate is minutes, versus ~5 min/game
on this host's CPU.

### 4. N3 — done; reproduce or extend

```bash
./launch_cluster_a51.sh --engines 4          # visible windows, big cores 4-7
.venv/bin/python tools/native_analysis/resident_batch_throughput.py \
    --ports 26789,26790,26791,26792 --slots 16 --batches 10 --ticks 600 --actions \
    --output /tmp/opencode/resident_4x16.json
adb -s R58NA0QTHWV logcat -b crash -d
```

Expect ~14 matches/s capacity and a ~13.9 makespan floor. Record any change in
`FINDINGS.md` §3.30. The next lever is GPU inference, not more engines.

### 5. The GPU smoke — **done 2026-11-14**, see the GPU-smoke section above

The procedure that actually works (ThunderCompute box, A51 over a tunnel):

```bash
# on this host
./launch_cluster_a51.sh --engines 4
./launch_ppo_a51.sh --skip-launch --engines 4 --worker-host 127.0.0.1 --output-dir runs/a51-node0
setsid ssh -N -o ExitOnForwardFailure=yes \
    -R 127.0.0.1:29900:127.0.0.1:29900 <box>      # worker service
setsid ssh -N -R 127.0.0.1:26789:127.0.0.1:26789 <box>   # evaluator only

# on the box
.venv/bin/python -m native_runner.training.v4.build_ppo_multinode_topology \
    --node-ready runs/a51-node0/engine-node-ready.json --output runs/topology.json \
    --trainer-job-id 1 --trainer-host "$(hostname -s)" --gpus 1 \
    --worker-cpus 0,1,2,3 --control-cpus 4
export SLURM_JOB_ID=1 CR_AI_PYTHON=$PWD/.venv/bin/python CR_PPO_SEED=1
export CR_PPO_ALLOW_UNVERIFIED_DECK_POOL=1
export CR_PPO_GATE_TEMPERATURE_START=1.0 CR_PPO_GATE_TEMPERATURE_END=0.2 \
       CR_PPO_GATE_TEMPERATURE_ANNEAL_UPDATES=20
export CR_PPO_INFERENCE_BATCH_WAIT_MS=250   # ~one A51 engine step (§3.38)
bash native_runner/training/v4/launch_ppo_multinode_training.sh \
    runs/ppo-smoke runs/topology.json 1 40      # 1 update, 40 s segment
```

Non-obvious requirements: `SLURM_JOB_ID` must equal the topology's
`trainer_slurm_job_id` (there is no Slurm; just export it), the seed must be >0,
and the pool must be the 20-focus variant
(`select_focus_decks.py --deck-pool runs/deck-pool.json --top 20 ...`).

The 40 s segment is a **decision budget** (`segment_decision_steps = seconds*4`
per lane), not wall time: 40 s = 160 decisions/lane ≈ 5,120 frames.

Gate the run on: all 32 resident matches completing (needs ≥180 s at 720
decisions/match), **0 rejected actions**, `native_final_result` terminals, and a
saved checkpoint with a matching SHA/optimizer step. Then run 2–3 evaluator games
at `--device cuda` against IL and Hog26-2 before deciding on the fine-tune
length. Do not train on 15.535 labels.

### 6. N2 — the remaining port work

Resolve the `AMBIGUOUS`/`MISSING` anchors and install the rich hook rings
(combat events, movement/character/visibility/remaining, tower troops). Follow
§5 of `FINDINGS.md`; validate each anchor with `cr_elf.py` / `diff_builds.py`.

**Free head start — DONE (§3.33/§3.34).** The RoyaleFish table closed every
AMBIGUOUS anchor and 8 of 10 MISSING ones. The rich-subsystem offsets are now in
`offset-diff.json`; installing the rings is mechanical: emit a per-subsystem
16.635 layout header (combat, phase, special/action movement, character state,
remaining, visibility, tower troops), drop that subsystem's
`#if !defined(CR_PROBE_TARGET_16_635_1)` guard, and re-attest one subsystem at a
time. Validate each with a prologue match plus a live `observe_rich` sample. The
ARM inline hook copies 16 bytes verbatim, so a hook whose first 16 bytes contain
a branch target outside the steal window needs a relocated trampoline (most
ring anchors are calls/returns, not branch entries, so this is a small set).

**New, concrete N2 blocker:** the native-render gate SIGSEGVs on 16.635.1
(`cr_render_gate_swap_buffers` → replay JSON setter, §3.31). Fix the
replay-manager `+0x20` controller slot / setter `+0x90` manager read, then
re-attest `render_gate` with a rendered-match smoke. Until then,
`offline_agent.py` / `offline_duel.py` are headless-only in practice. This is
cosmetic for training: deprioritize it behind step 1.


---

## General baseline

`checkpoints/General/checkpoint-step-00000460.pt`

| Field | Value |
| --- | --- |
| Parameters | 12,684,339 |
| Contract | `universal-card-policy.v4` |
| Observation | `universal-semantic-observation.v4` |
| Action | `universal-candidate-action.v4`, 2 micro-actions, 50 ms offsets |
| Decision cadence | 5 ticks |
| Training stage | `ppo-league-snapshot`, step 460 |
| SHA-256 | `2fb7a94fa5df0f12bd72433874487bc7d84a4efb2a6dac827d7adff0a5eef083` |

Other checkpoints are opponents/baselines:

- `checkpoints/IL/checkpoint-step-00029396.pt`
- `checkpoints/active IL/checkpoint-step-00000030.pt`
- `checkpoints/2_6hog_expert/hog26-specialist1.pt`
- `checkpoints/2_6hog_expert/hog26-specialist2.pt`

**Published 16.635 baselines** (loadable now through the 16.635.1 release):

| Model | Deck | Params | Notes |
| --- | --- | --- | --- |
| `.resource/RoyaleFish/models/B-general-v275.pt` | many | 17,506,419 | 275 updates on 16.635 data; best public generalist; **loads** (step 1.1) |
| `.resource/RoyaleFish/models/A-hog26-v738.pt` | Hog 2.6 | 17,506,419 | **71.9% ±2.6 vs its start over 1,147 paired games**; same release/schema as B-general |
| `.resource/FirstLight-Zero/checkpoints/firstlight-zero-v1450.pt` | X-Bow 3.0 | ~1.17M | self-play only, **15.535.13**, mirror-only result |

The General model was trained on 252k official-game replays; Null's may have
custom balance/cards. Verify content compatibility before assuming transfer.

---

## Data — no scraping needed to start

`datasets/royaleapi/` is the full **IL_Replay** dataset:

- 252,238 replays, 17,836,160 actions, 1.8 GB
- `replays/part-*.parquet`, `actions/part-*.parquet`, `manifest.json`
- Format: `royaleapi-battle-replay.v1`

Sources and limits:

| Source | What it gives | Use |
| --- | --- | --- |
| **Local IL_Replay** | 252k replays / 17.8M actions | generalist pretraining/IL, already here |
| **Official Clash Royale API** | battle logs only (last ~25), no replays | meta/opponent stats, not IL |
| **RoyaleAPI downloader** (`.resource/royaleapi-downloader`) | live 16.x ladder replays, structured JSON | fresh official-game data; follow site terms |
| **Hugging Face** | IL_Replay (local), optional replay/checkpoint datasets | extras only |
| **Null's self-play/captures** | the actual target distribution | the data that matters for the ladder |

Do not mix 15.535.29 self-play labels with 16.x IL labels without an explicit
adaptation step. Do not call self-play actions “expert” for IL.

Deck diversity: use `native_runner/training/v4/build_ppo_deck_pool.py` on
`datasets/` (exact forms + tower troops), then the V4 league/opponent scheduler.
No scraping is required for the first fine-tune.

---

## Training pipeline

The local V4 stack:

| Step | Command |
| --- | --- |
| Build IL cache | `python -m native_runner.training.v4.cache_builder datasets/royaleapi datasets/cache --source-count 0 --ports 26789 --resident-slots 1 --verify-roundtrip` |
| IL smoke | `bash native_runner/training/v4/launch_stateful_il.sh --index … --train-tags … --output-dir runs/il-smoke --epochs 1 --max-groups 1 --save-every-groups 1` |
| Deck pool | `python -m native_runner.training.v4.build_ppo_deck_pool --dataset-root ./datasets --output ./runs/deck-pool.json --minimum-uses 5` |
| Policy service | `python -m native_runner.training.v4.serve_policy --checkpoint checkpoints/General/checkpoint-step-00000460.pt` |

PPO caveat: the upstream launchers assume AVD/engine-ready topology. The A51
cluster is not wired into that topology yet; adding an A51 engine-ready path is
the first N3 task. Until then, use the evaluator and a single-engine PPO smoke.

Training rules:

- Initialize from General; do not freeze it as a mirror opponent.
- Keep rejected actions at zero; reject the rollout otherwise.
- Evaluate paired seeds, both seats, fixed decks/levels.
- Record checkpoint SHA-256, dataset/tag selection, deck pool, seed and config.

---

## Cloud and credits

**Decision: 1× RTX A6000 @ $0.35/h, `--gpus 1`, ~57 h of runway for $20.**
The GPU is not the bottleneck; the engine is.

- **RTX A6000 48 GB — $0.35/hr.** 12.7M (or 17.5M on the 16.635 config) params
  fit in <2 GB; 48 GB is headroom, not a reason to buy more compute. Cheapest
  per useful hour and the only GPU this plan needs.
- **L40 $0.79/hr and A100 80 GB $1.09/hr are 2–3× the price for FLOPs and VRAM
  the engine cannot feed** (~15.6k decisions/s on 4 A51 engines, ~2 ms per
  batched forward). They only make sense if the x86 farm grows by an order of
  magnitude, which is a later decision.
- **A "$0.00" second GPU must be verified at checkout** before it influences
  any plan; until then assume the second unit double-bills.
- **GCP $300 / Azure $100.** Useful only if you need a high-vCPU host with a GPU
  or a cloud Android/AVD cluster. New accounts often have **zero GPU quota**;
  check before planning. Free-trial terms apply per account.
- **One inbound port is all the networking you need.** Reverse-SSH `29900` from
  this laptop; keep the worker bound to `127.0.0.1`.
- **Spend order:** x86 16.635 engine, catalogs, deck pool, IL cache first
  (CPU, free), then GPU hours for evaluation volume and PPO updates. Never bill
  GPU hours to CPU-bound prep.

Scale options, in order of leverage:

1. **x86_64 16.635.1 native engine on this host** — 16 matches/process, no
   phone, same ruleset. The correct replacement for phone-bound training.
2. **crforge 16.635.3/8** — 127k steps/s/thread, tick-exact to recorded
   battles, `VectorBattle` Python env. Highest raw throughput; needs a V4→crforge
   observation adapter, and it is a reimplementation.
3. **More A51-class ARM64 devices** — cheapest per *deployable* engine, but
   hardware-bound and now the wrong axis for training.
4. **Cloud x86 host with KVM + ARM64 Android image/AVD cluster** — only if the
   deployment target itself must be scaled.

---

## Risks

1. **16.x port completeness** — the rich hook rings and several core anchors
   remain; N2 may take longer than N0/N1.
2. **Engine throughput** — no longer the limiter: one A51 gives ~12–14
   matches/s (§3.30). The remaining risk is a regression into the `background`
   cpuset; the launcher now verifies it. GPU policy inference is the cap.
3. **Host inference, not the phone** — one CPU core spends ~217 ms per policy
   forward; a full evaluation game is ~5 min. Continuous training needs a GPU.
4. **Argmax is passive** — both checkpoints give `ACT` only ~6% probability at
   the 5-tick cadence, so deterministic decoding stands still. Use `--sample`
   for evaluation and check gate entropy before live deployment.
5. **Catalog adaptation** — the device runs content 16.635.93 while the local
   default catalogs are pinned at 15.535.86. Deck ids/forms/costs match at
   level 11; Electro Spirit HP differs (215 vs 217). **Resolved for loading:**
   16.635 is a selectable, verified release (`native_runner/releases/16.635.1.json`)
   adopted from `RoyaleFish/data_16635` with a repaired manifest, and
   `B-general-v275` loads against it. The local `Clashback` 16.635 `csv_logic`
   assets are a *different content revision* and do not reproduce those catalog
   ids, so do not assume a recompile equals the published models. See
   `docs/native/COMPETITIVE_RELEASES.md`.
6. **Null's content** — official-game 16.x data may not match Null's custom
   balance/cards. Verify the fingerprint. Note FirstLight-Zero and RoyaleFish
   both train on official 16.635 tables, so Null's custom balance remains the
   one unmeasured domain gap.
7. **Ladder rules** — automated play may be prohibited; confirm before using a
   live account.
8. **Cloud quotas** — GCP/Azure GPU quota may be zero on new accounts.
9. **Native-render gate crash** — 16.635.1 SIGSEGVs in
   `cr_render_gate_swap_buffers` (§3.31). Headless evaluation/training is
   unaffected; the offline renderer and any live-render demo are blocked.
10. **GPU pricing is not a quote** — the "0.00" second GPU is almost certainly
    a display artifact. Verify the hourly estimate before starting a run, and
    treat preemption as real: `--snapshot-every 1` on spot.
11. **NAT between the GPU host and the A51** — the trainer needs inbound
    `29900`. The reverse SSH tunnel from this laptop is the supported path; a
    dropped tunnel looks like an engine timeout mid-segment. `tcp_endpoint_proxy.py`
    is the fallback, not a public bind.
12. **Simulator self-evaluation** — Hasty-CR's own warning: 93% against its own
    engine, and one human match found a bug 30M training steps had missed.
    In-engine win rates are not evidence. Paired gates must run on the native
    16.635.1 engine (A51 or x86), never only on crforge.
13. **Model-quality gap, not an engine gap** — the best public Ranked result is
    ~43% (ClashAI). Nothing in the ecosystem is above water vs top humans yet.
    Budget the work as a league/opponent/eval problem, and expect the first
    honest target to be "beats General and the Hog specialists reliably", not
    "beats top ladder".
14. **Candidate legality gap — closed.** `delay_aware_legality` is now branched
    on in the decoder, the environment action mask and the compound validator
    (§3.39), and gate temperature 1.0 collects cleanly. The residual risk is
    diagnostic, not semantic: one post-port `env 6` failure did not reproduce,
    and the resident batch error string still collapses injection, step-bound
    and capture failures. Capture it with `CR_PPO_FAILURE_DUMP` and
    `adb logcat -s CRNativeProbe` if it returns.

---

## Retired / deferred

- **R0 elite-v3** — retired. Archived in
  `docs/native/R0_CANONICAL_TREE.md`,
  `tools/native_bringup/smoke_r0_model.py`,
  `tools/native_bringup/verify_r0_batched.py`. Do not start an R0 run.
- **R0 checkpoint/data/CUDA/adapter gates** — retired with R0.
- **cr-native 15.535.29 as critical path** — demoted to proxy lab.
- **Custom ARM64 `libg` loader** — retired; 16.635.1 uses the offline APK/probe.
- **“16.635.1 is parity only”** — reversed; it is the primary target.
- **Linux AVD/MuMu/phone clusters** — only a scale option if the A51 is
  insufficient.
- **Native as the primary bulk trainer** — superseded 2026-11-15. The Python
  per-packet data path caps it at 0.082 matches/s and 4 % GPU. It is retained as
  the gate, the parity oracle, the deploy rig and the fallback; do not delete or
  idle it.
- **“crforge cannot be the trainer”** — reversed 2026-11-15: it is the bulk
  backend after C1; never a gate.
- **Official Supercell servers** — out of scope, not deferred. Different client,
  version and protocol; a legal/ToS question, not an engineering milestone.
- **“100 % GPU utilisation” as a goal** — retired as a target; the metric is
  matches/s per dollar at a gateable quality. Saturation is a diagnostic.

---

## Key paths

| Path | Purpose |
| --- | --- |
| `ROADMAP.md` | The single plan |
| `docs/native/16.635.1/FINDINGS.md` | 16.635.1 probe port evidence log (§3.35 = x86_64 host) |
| `tools/native_analysis/crx_engine_host.py` | Boot the x86_64 16.635.1 engine on Linux (crx) and attach the probe; `--engine-index N` for multiple host engines |
| `tools/native_analysis/profile_real_collection.py` | Real V4 collector on CPU. `--synthetic-model` runs the checkpoint's contract at narrow widths (isolates loop from forward); `--segments N` exercises boundary bootstrap + resume |
| `tools/native_analysis/profile_cluster_collection.py` | Scripted transport floor. `--diagnostic-timing` splits each worker round into decode / native step / tensorize / channel |
| `runs/loop-pipeline-20261115/` | Session-8 evidence: scripted decomposition, old-vs-new loop A/B, 10-segment stress |
| `runs/step-breakdown/` | §3.42 step attribution and §3.43 paired stock/lean observation evidence (`ab-*`, `ab8-*`) |
| `tools/native_analysis/profile_step_breakdown.py` | Per-method `BattleEnvV1.step` sections + sequential cProfile; use it for paired A/B on this host |
| `native_runner/probe/build_16_635_1_layout_x86.h` | Generated x86_64 core anchors |
| `native_runner/versions/native-16.635.1-x86_64.json` | x86_64 build pin (select with `CR_NATIVE_BUILD_ABI=x86_64`) |
| `native_runner/probe/out/x86_64/libcrprobe.so` | x86_64 probe (build artifact, pinned by hash) |
| `ROADMAP_CRFORGE.md` | **Bulk-training plan of record**: C0–C7 gates, fidelity contract, economics, CI/parity role |
| `.resource/crforge/python/crforge_env/vector.py` | `VectorBattle(count, threads)` — 256-lane zero-copy NumPy vector; `LadderConfig`, `SideSetup` |
| `.resource/crforge/python/crforge_env/bench.py` | `crforge_env.bench` — env-steps/s, ticks/s, obs MB by batch size; the C1 baseline |
| `.resource/crforge/env/build/install/env` | Installed JVM env distribution for `VectorBattle` |
| `~/.crforge/tables/16.635.8` | Pinned game tables (also 16.635.3); record the lock with every run |
| `crforge_bridge/` | `BattleEnvV1` bridge for evaluation/parity; 152/152 cards, 118 forms. `native_runner/crforge_resident.py` presents the same surface in-process |
| `crforge_bridge/vector_rows.py` | **C3-B row build** — `VectorGroupBuilder` (`_groups`+children; `build_pair` assembles both actor seats from one shared row core), `VectorCardBuilder` (own/opponent sets incl. the 21-column pending schema), `VectorTowerBuilder` (the six-row tower section + the tower model-row map), `VectorSpatialPlaneBuilder` (the 14-channel explicit planes), `VectorScalarBuilder` (the 18 match scalars), `VectorEmptySectionBuilder` (the empty effect/event records), `VectorCandidateBuilder` (legal hand slots, native placement grid, building geometry), `VectorRelationBuilder` (target/reverse/card→group edges, tower tokens through `tower_index`) over a whole `VectorBattle` batch; every builder is actor-seat aware; `reference_observation` / `reference_action_mask` project the per-lane oracle input from the same facts |
| `tools/crforge/batched_groups.py` | **C3-B group gate** — per-lane `_groups` oracle vs the batched section; writes `runs/crforge-c3/batched-groups.json` |
| `tools/crforge/batched_cards.py` | **C3-B card/relation gate** — reads the checkpoint's own `model_config` (21 card columns), verifies both sections against the stock methods, then times them paired; writes `runs/crforge-c3/batched-cards.json` |
| `tools/crforge/batched_towers.py` | **C3-B tower gate** — 256 learner-deck lanes, `TowerSetV4` validation and full section equivalence every step, paired timing, the two envelope pins on the artifact; writes `runs/crforge-c3/batched-towers.json` |
| `tools/crforge/batched_spatial_planes.py` | **C3-B spatial-plane gate** — 256 learner-deck lanes, full-plane equivalence every step (per-frame caches cleared per lane), paired timing and coverage counters; writes `runs/crforge-c3/batched-spatial-planes.json` |
| `tools/crforge/batched_scalars.py` | **C3-B scalar gate** — 256 learner-deck lanes, full scalar equivalence with the oracle's own group/candidate counts, paired timing; writes `runs/crforge-c3/batched-scalars.json` |
| `tools/crforge/batched_empty_sections.py` | **C3-B empty-record gate** — 256 learner-deck lanes, both records validated and compared every step, paired timing; writes `runs/crforge-c3/batched-empty-sections.json` |
| `tools/crforge/batched_candidates.py` | **C3-B candidate gate** — 256 learner-deck lanes past the start cooldown, `ActionCandidatesV4` validation and full section equivalence every step, paired timing; writes `runs/crforge-c3/batched-candidates.json` |
| `tools/crforge/batched_tensorize.py` | **C3-B end-to-end gate** — 256 learner-deck lanes × 40 decisions, `--seats 0 1` drives one `VectorTensorizer` and one stock tensorizer per actor, compares whole records field by field plus `batch_digest`, records actions on both paths, and reports combined timing per lane-decision; two seats go through the producer's `tensorize_seats` pair path (`--no-pair` measures the per-seat path); writes `runs/crforge-c3/batched-tensorize.json` |
| `tools/crforge/frame_crosscheck.py` | **Native frame gate** — one play at the same game point in the x86_64 engine and a one-lane `VectorBattle`, compared on the first spawned frame; also records the facts-layer pending point; writes `runs/crforge-c3/frame-crosscheck.json` |
| `crforge_bridge/vector_actions.py` | **C4 action path** — one NumPy pass from a stacked `ActionSequenceV4` to the engine's flat index (`0` wait, `1 + slot*rows*cols + row*cols + col`), for both seats, every micro-action with its execute offset; `plan_window_ticks` maps those offsets onto the engine ticks of one decision window (collision shift / drop counted); deploy-only envelope; reports closed mask indices instead of repairing them |
| `tools/crforge/vector_decode.py` | **C4 action gate** — 256 learner-deck lanes, both seats, every index/offset/candidate row against the stock per-lane decoder on the same facts, then the decoded indices are stepped into the engine; writes `runs/crforge-c4/vector-decode.json` |
| `tools/crforge/self_play.py` | **C4 producer** — one `VectorBattle` of `--count` lanes, both actor seats in one recurrent forward, both seats decoded back into engine actions, engine stepped one tick at a time so each micro-action keeps its delay bin; counts ACT rate, resets, illegals/faults and the per-stage ms; writes `runs/crforge-c4/self-play.json` |
| `crforge_bridge/tests/test_encoder_spike.py` | Pins the C1 encoders to the bridge's own facts and card-parameter packing |
| `runs/crforge-c0-20261116/` | C0 legality soak: 20 games, `illegal`, `truncated`, `rejected_total` counters |
| `runs/crforge-c1/` | C1 encoder-spike JSON (`encoder-spike.json`), default shape 256 × 200 × 3 |
| `runs/crforge-c3/` | C3 tensorizer profiles: `tensorizer-profile.json` (shipped), `ab-stock.json`, `ab-optimized.json` (reverted NumPy variant), `ab-pair{1,2}-{stock,python}.json`; the C3-B section gates `vector-observation.json`, `batched-groups.json`, `batched-cards.json`, `batched-candidates.json`, `batched-towers.json`, `batched-spatial-planes.json`, `batched-scalars.json`, `batched-empty-sections.json`; the both-seat end-to-end gate `batched-tensorize.json` (+ `-seed2.json`) and the native frame check `frame-crosscheck.json`; `learner-deck/` holds the locked-deck match config, the clean 2-game rollout and its tensorizer profile |
| `runs/crforge-c4/` | C4 artifacts: `vector-decode.json` (batched action decode vs the stock per-lane decoder, both seats, 3,008/3,008), `self-play.json` (producer gate shape: 10,240 lane-decisions, 0 rejected), `self-play-autoreset.json` (terminal/autoreset run: 4 resets, 0 rejected) |
| `tools/crforge/bridge_session.py` | Shared live bridge episode: evaluator and **locked X-Bow learner deck** constants (`DECK`, `LEARNER_DECK`, form availability), tower troop, release scope, one session per seat — used by all crforge tools; takes `port=` (socket bridge) or `native=` (resident adapter) |
| `tools/crforge/resident_parity.py` | **C2 deliverable** — resident vs bridge, decision for decision: parity digests (native/rich/atomic, masks, rewards, terminals) + paired `env.step` timing; writes `runs/crforge-c2/resident-parity.json` |
| `tools/crforge/profile_tensorizer.py` | **C3 deliverable** — replays a live policy episode's `tensorize`+`record_action` under cProfile; capture digest + per-decision `batch_digest` prove an A/B is bit-identical; `--profile-decisions 0` is the timing-only A/B mode |
| `tools/crforge/encoder_spike.py` | **C1 deliverable** — 256-lane `VectorBattle`, per-lane bridge facts vs vectorized NumPy frame, equivalence check, encode/tensorize split; writes `runs/crforge-c1/encoder-spike.json` |
| `docs/native/16.635.1/*.json` | Relocation, field offsets, vtables, overrides |
| `tools/native_analysis/` | A51 parity/probe tools |
| `tools/native_analysis/a51_engine_node.py` | Attest the A51 cluster → `v4-ppo-engine-node.v1` manifest |
| `launch_ppo_a51.sh` | Cluster + manifest + remote PPO worker service |
| `tools/native_analysis/resident_batch_throughput.py` | Resident-channel capacity measurement |
| `tools/native_analysis/scripted_xbow_match.py` | Scripted X-Bow match on the probe |
| `tools/native_analysis/parity_manifest.py` | Archive/verify/compare an A51↔x86 parity corpus |
| `runs/parity-a51/` | A51 ground-truth corpus + `manifest.json`, re-captured on `b80f2bdb…` (§3.32, §3.36) |
| `runs/parity-x86/` | x86_64 candidate corpus; reproduces every A51 entry (§3.36) |
| `docs/native/COMPETITIVE_RELEASES.md` | Selectable competitive data releases, 16.635 adoption |
| `native_runner/releases/16.635.1.json` | 16.635.1 release descriptor |
| `native_runner/data/competitive-16.635/` | Verified 16.635 catalogs (1.5 MB) |
| `tools/native_analysis/adopt_compiled_release.py` | Adopt a compiled bundle as a verified release |
| `deploy_a51.sh`, `launch_cluster_a51.sh` | Install/launch the 16.635.1 cluster |
| `builds/cr-ai-16.635.1.apk` | 16.635.1 offline APK |
| `builds/cr-engine-cluster-16.635.1.apk` | 16.635.1 cluster APK |
| `native_runner/probe/out/libcrprobe-16.635.1.so` | 16.635.1 probe |
| `native_runner/training/v4/` | V4 IL/PPO stack, evaluator, deck pool, league |
| `checkpoints/General/checkpoint-step-00000460.pt` | Primary baseline |
| `datasets/royaleapi/` | IL_Replay corpus (252k/17.8M) |
| `.resource/royaleapi-downloader/` | Optional fresh 16.x replay downloader |
| `docs/native/R0_CANONICAL_TREE.md` | R0 archive (retired) |
| `.resource/cr-native-sandbox/` | 15.535.29 proxy lab |
| `docs/native/PHASE0_BRINGUP.md` | cr-native runbook (proxy only) |
| `docs/native/PHASE2_BOTTLENECK.md` | cr-native throughput evidence |
| `.resource/CRkonwledagebase/` | Ecosystem index (58 projects, R0–R4 evidence) |
| `.resource/Clashback/` | **Gate substrate** (commit `002aa8c`): `game/engine/libg.so` (build `160635001`, sha `c4721bd9…`), `vendor/crx` ELF loader, `neo_replay` tick cache/snapshots. License unknown — **local use only, never commit or redistribute**. **Upstream unreachable since 2026-11-15 (404); this checkout is the only copy** |
| `.resource/RoyaleFish/` | Apache-2.0 (commit `9179bc5`; README + media only since `da9c8cf`): `data_16635/` (adopted), `B-general-v275` / `A-hog26-v738` (league seed + gate opponents), `royalefish/ever_*` (**C4 throughput recipe**), patches (reference only). **Upstream deleted v738/v98/v49 on 2026-11-19; all five models are in `.resource/upstream-keep/`** |
| `.resource/upstream-keep/` | Provenance-pinned backup outside the resource checkouts: all RoyaleFish models, media, the new README, and the crforge PPO example/compatibility snapshots. `SHA256SUMS.txt` + `README.md` (restore commands) |
| `.resource/FirstLight-Zero/` | X-Bow 3.0 self-play checkpoints (15.535.13) |
| `.resource/daybreak-cr/` | IL→PPO→live-Null's pipeline with league/bc recipe |
| `runs/deck-pool.json` | 7,089 decks / 443,381 uses (111 X-Bow) |

---

## Do not do

- **Do not start a bulk run before C0.** One reject (MiniPekka `26000018`) was
  still open at the 2026-11-15 handoff; a rollout with a reject is invalid.
- **Do not full-send vectorization before the C1 number.** A straight per-lane
  port is ~1.4×, not 10×. Measure `ms/lane` for per-lane and vectorized builds
  first; if the gate misses twice, fix the encoder, not the lane count.
- **Do not delete, retire or idle the native engine.** It is the promotion gate,
  the differential-parity oracle and the deploy rig. Keep it runnable; exercise
  it on a fixed cadence.
- **Do not validate parity only at the end.** 50–150 native matches every N
  updates and after every adapter change; a persistent win-rate gap freezes the
  checkpoint.
- **Do not chase 100 % GPU utilisation.** The metric is matches/s per dollar at a
  gateable quality; saturation is a diagnostic, and the model is launch-bound.
- **Do not multiply 0.33 matches/s by cores without the forward's 24 % GPU share.**
  Eight processes ask for ~194 % of one A6000, so they land near **1.3–1.6/s**
  unless MPS + a batched decision server serve all waiting collectors per call;
  with that they reach **2.0–2.6/s**. The 2.8/s engine-only floor is **per
  process**, not per host (`ROADMAP_CRFORGE.md` §4).
- **Do not quote a crforge matches/s without its decisions/match convention.**
  The same 128-lane run is **0.33** at 1,214/match (one seat) and **0.163** at
  2,428/match (both seats); the artifact writes the latter.
- **Do not plan for official Supercell servers.** Different client, version and
  protocol; a legal/ToS question, not a later milestone.
- **Do not treat the 2026-11-15 laptop bench as a cloud number.** It ran under
  load ~11 on a `powersave` i5-1235U; quote it as a floor and re-measure on the
  box before sizing a run.
- Do not train R0 or start an R0 data/checkpoint/adapter run.
- Do not use the x86 15.535.29 engine as the source of deployable Null's
  weights without an explicit adaptation step. (The **16.635.1 x86_64** engine
  is a different case: same client build, so it is a training substrate for the
  same ruleset.)
- Do not apply `RoyaleFish/patches/probe_x86_64_16635.diff` as-is: its base is
  the 15.535.1 ARM64 probe, while our tree is already 16.635.1 ARM64. Port the
  x86 call path and tables, not the retarget.
- Do not treat in-engine or mirror win rates as ladder evidence. Hasty-CR: 93%
  vs its own engine, and one human match found a bug 30M steps missed.
- Do not gate a promoted checkpoint only on crforge; gate on the native
  16.635.1 engine.
- Do not run native-render (`offline_agent.py`, `offline_duel.py`) on 16.635.1:
  the probe SIGSEGVs in the render gate (§3.31). The headless evaluator/PPO path
  is safe.
- Do not spend cloud credits before the x86 16.635 engine runs. (N0/N1 pass; the
  next spend is evaluation volume, after step 1.)
- Do not evaluate or deploy these checkpoints with deterministic argmax: it
  collapses to `WAIT`. Use `--sample` or verify gate entropy first. The PPO
  collector needs the same care: `gate_temperature 0.2` produced `ACT` = 0 over
  5,120 eligible ticks. Collect at 1.0 and anneal down.
- Do not read §3.30's 12–14 matches/s as a training decision rate: it was
  measured at 600 ticks per RPC. At the PPO 5-tick cadence four A51 engines are
  worth **0.26 matches/s** and one x86_64 process **0.61**.
- Do not build a gate tensorizer from a bare `ModelConfigV4()` when the
  checkpoint declares its own config: the default is the **16-column** card
  runtime schema, while `B-general-v275` declares **21** (the pending-execution
  columns). Read `contract.model_config` (`config_from_contract`) — the card
  gate does. The group section is unaffected because only that field differs.
- Do not time the tensorizer on a scripted policy without recording its actions:
  the public tracker's hand/cycle partition check fails, and `isinstance(entry,
  dict)` on `placement_masks` values (which are `FrozenMapping`) silently leaves
  the scripted policy on `WAIT`, so the "measurement" is an empty board. Use
  `tools/crforge/profile_tensorizer.py`, which replays the real recorded
  actions.
- Do not quote a min-of-decisions `tensorize` number as the packet cost. The C1
  tool reports the cheapest single decision; the replay mean on a busy board is
  0.99–1.07 ms/decision and is the number to size a run with.
- Do not expect another round of Python tuning to reach ≤0.5 ms/lane: the packet
  is 3,754 calls/decision and the reductions that remove calls measured flat
  end-to-end (FINDINGS §3.44).
- Do not treat a crforge hand slot's tile block as candidate legality. crforge
  keeps the slot open while its play waits to run (the card is still in hand and
  a second try can be *given*); `BattleEnvV1` closes it with `pending_execution`,
  and the batched candidate rows follow the environment: in hand, not queued,
  affordable at this decision's elixir, and at least one legal tile. A row set
  built from the tile block alone emits a candidate the bridge would never offer
  (`FINDINGS.md` §3.49).
- Do not order or type tower rows from the native kind alone. `_towers` sorts and
  emits `tower_type` by the **model** kind, which swaps the princesses under a
  horizontal flip; a build that only mirrors coordinates puts the princess rows
  in the wrong order and mislabels them. Also keep "destroyed" (a zero-HP row,
  maximum preserved) distinct from "absent" (padding) — `FINDINGS.md` §3.50.
- Do not accumulate the explicit spatial planes with float64 `np.add.at`
  operands: the per-lane loop adds each entity's float32 value to a float32
  plane, so a cell shared by two entities differs by one ULP. Cast the addends to
  float32 before the scatter. And do not mask the whole `(lanes, entities)` grid
  for every expression — flatten to the kept entities once; that alone was
  2.44× → 12.7× (`FINDINGS.md` §3.51).
- Do not drop the empty records' padding sentinels: `EventSetV4` validates
  `owner_type` 2 and group/token addresses `-1` on masked rows, and effects keep
  the parent-index `-1`; a zeroed record fails `validate` (`FINDINGS.md` §3.53).
- Do not take `_scalars`' phase and remaining clock from the same tick. The
  phase/multiplier come from the **native** tick and the remaining clock from the
  **canonical** tick; `batch.tick` is canonical and the builder takes the
  encoder's first raw tick as `native_tick_offset`. And do not feed the scalar
  oracle the batched group/candidate counts it is meant to check — run the stock
  chain (`FINDINGS.md` §3.52).
- Do not read `resident_batch_throughput.py` matches/s as a training rate. It is
  a raw transport ceiling: four engines measured 0.736 aggregate because one
  Python client decodes every lane, while the real worker floor is ~0.08
  matches/s per engine (§3.41). Convert units explicitly (720 decisions vs
  6,068 ticks per match, 1.69x apart).
- Do not expect loop pipelining to raise a row-bound CPU forward. It pays on a
  fixed-cost (GPU) forward and at engine counts where the coordinator would
  otherwise be serial (§3.40–§3.41).
- Do not run the pipelined collector's inference thread on CUDA until graph
  capture/replay, autocast and the `model_streams` waits are confirmed there;
  it is CPU-verified only.
- Do not raise `CR_PPO_INFERENCE_BATCH_WAIT_MS` to coalesce engines. 250 ms does
  hit 4.0 packets/forward, but the wait costs more than the saved forward: it is
  net slower than 2 ms on both row-bound CPU and a ~2 ms GPU forward (§3.40).
  Keep the default and pipeline the loop instead.
- Do not collect `B-general-v275` at gate temperature 1.0 without the
  delay-aware legality port — it is now in place (§3.39). If an
  `env N ... resident batch step or atomic capture failed` appears again, set
  `CR_PPO_FAILURE_DUMP` and watch `adb logcat -s CRNativeProbe`; the current
  probe error string still conflates injection, step-bound and capture.
- Do not name `H_LAST_RUN_REFUSAL` from `Schema.refusalNames()`: that list is the
  *giving* refusal namespace (`PlayerCommands.Refusal`), while the header is a
  `LadderMatch` code. They overlap at 4 — `OVER` in the match, `START_COOLDOWN`
  in the wrong list — so a decided match was reported as a cooldown. A queued
  play refused with `OVER` is a terminal cancel, not a rejected command
  (`FINDINGS.md` §3.45).
- Do not rent a GPU until a local segment completes in <10 min and C1 has a
  measured number; at 0.04 matches/s the A6000 idles at 4 %.
- Do not assume one A51 matches the upstream 1,528-match training scale.
- Do not mix 15.535.29 labels with 16.x labels.
- Do not mix the ARM64 and x86_64 build pins: `CR_NATIVE_BUILD_ABI` selects
  `native-16.635.1-x86_64.json`; the A51 must stay on the unsuffixed pin.
- Do not load the x86_64 probe through `JNI_OnLoad` on the host: crx mappings
  are anonymous. Use `cr_probe_attach(libg_base)` from `crx_engine_host.py`.
- Do not preload the probe's zlib `RTLD_LOCAL`. crx resolves imports with
  `dlsym(RTLD_DEFAULT)` and substitutes a stub that returns 0 for missing
  symbols, so `compress2` "succeeds" with a zero-byte output and every
  compressed batch entry is truncated (`FINDINGS.md` §3.37).
- Do not mix competitive releases (15.535 vs 16.635) in one training run or on
  one engine: a single engine tensorizes every actor row, so the learner, the IL
  anchor and every historical opponent must share the catalog release and model
  config. The trainer and collector now fail closed instead of shape-erroring.
- Do not expect a 15.535-contract checkpoint (General/IL) to tensorize on the
  x86_64 engine: its content is 16.635.8 and unresolved effect ids fail closed.
  Train 16.635-contract checkpoints there; keep the 15.535 pair for the A51 or
  an explicit adaptation step.
- Do not rebuild `native_runner/probe/out/x86_64/libcrprobe.so` without
  regenerating the pin's `probe_sha256` (attestation is fail-closed).
- Do not gate a 16.635 rich-classification anchor on `__x86_64__`.  Both
  `build_16_635_1_layout*.h` headers must carry the same vtable names and the
  probe must select them on both ABIs; the stale 15.535 fallback silently
  degrades the observation (§3.35–§3.36, enforced by
  `tests/test_probe_build_layout.py`).
- Do not run automated ladder play without confirming the server's rules.
- Do not treat `IL_Replay` as retired: it is the generalist corpus now.
- Do not commit large APKs/datasets.

---

## Appendix — historical facts worth keeping

**cr-native 15.535.29 proxy (not the deployment path):**

- 1 h soak passed; 8 workers is the knee; 12 is oversubscribed on the 10-core
  laptop.
- Engine-only at 8 workers: 36.4k ticks/s ≈ 9.6 matches/s at 100 ticks/RPC.
- 516k loop at 8 workers: 0.074 matches/s (1 tick/decision), 0.277 (4),
  0.522 (8).
- R0 CPU inference: 125–269 ms/pass; not viable without a GPU.
- Evidence: `builds/native/phase0-20261114T000821Z/`,
  `docs/native/PHASE2_BOTTLENECK.md`.

**16.635.1 parity (A51):**

- Towers: King 4824, Princess 3052 at level 11.
- Deck ids/forms/costs and starting elixir match 15.535.29.
- Unit HP matches except Electro Spirit (A51 215 vs native 217).
- Hero Knight ability state/charges/elixir match.
- Evidence: `builds/native/parity-unit-hp-15.535.29-vs-16.635.1-L11.json`.

**R0 archive:** 36,854,175 params, schema `825408c3…4d44`, CPU
batched-equivalence PASS. Retired.
