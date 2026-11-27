# Paper source notes — what is in the archive and where to use it

A working map from the recovered material to likely paper sections. Everything
below points into `research_archive/`; section numbers refer to the recovered
documents themselves.

## Suggested paper framing

The archive supports a systems/agent paper with four nested contributions:

1. **A custom headless simulator** for fast RL data collection (Jason notes #1–#3).
2. **A memory-reading / native-engine bridge** that makes the real game a
   training and validation environment (Jason notes #2, #4, #5; `FINDINGS.md`).
3. **A vectorized bulk-training backend (`crforge`) plus a native parity gate**,
   with measured throughput and parity methodology (`ROADMAP_CRFORGE.md`,
   `PHASE2_BOTTLENECK.md`, HANDOFF sessions 39–54).
4. **A V4 actor-critic agent and league pipeline**, including a negative N4
   promotion result and a low-level JIT miscompile that had to be diagnosed
   before training could proceed (`training_history.md`, `HANDOFF.md`,
   `HOTSPOT_C2_SEATIDS.md`).

## Where each theme lives

| Theme | Primary sources | Notes |
| --- | --- | --- |
| Project history / motivation | `deleted/jason_notes/6_18.txt`, `7_28.txt`, `8_10.txt`, `8_28.txt`, `9_12.txt` | Five video scripts by JasonMoonW. Covers the semester-long simulator, the decision to read live memory, architecture/LLM-assisted reverse engineering, A* pathfinding, and using the native engine for high-speed RL. `8_10.txt` has the cleanest origin story and design rationale. |
| Simulator design and early RL | `deleted/jason_notes/8_10.txt`, `8_28.txt` | ~3,000-line Python headless simulator, 50× real time, 100k matches in four days, 47/122 cards, entity/component design, Stable-Baselines3, pathing. |
| Live memory reading | `deleted/jason_notes/7_28.txt`, `9_12.txt`; `deleted/docs/native/16.635.1/FINDINGS.md` §3.1–3.5 | Reading game state directly vs computer vision; hooking the native engine. |
| Binary analysis / offsets | `deleted/docs/native/16.635.1/FINDINGS.md` | 7,706 lines: binary identity, tooling, verified anchors, hook relocation, hand/deck model, replay controller, GameState constructor store map, field shifts. `docs/native/16.635.1/*.json` in `current_docs/` holds the machine-readable layouts. |
| Target-version migration | `deleted/ROADMAP.md` "why the deployment target changed"; `deleted/docs/native/COMPETITIVE_RELEASES.md`; `deleted/issue.txt` | Why the project moved from 15.535.13 to 16.635.1; versioned data releases and catalog hashes; official-client feasibility/ban-risk analysis. |
| Phase 0 bring-up | `deleted/docs/native/PHASE0_BRINGUP.md`, `R0_CANONICAL_TREE.md` | Bring-up sequence and canonical tree. |
| Throughput / bottleneck science | `deleted/docs/native/PHASE2_BOTTLENECK.md`; `FINDINGS.md` §3.18, §3.30, §3.38–3.44; `ROADMAP_CRFORGE.md` §1, §4 | Engine is not the bottleneck; JSON-RPC boundary, observation work, and inference are. Measured ticks/s, matches/s, per-decision costs, capacity equation. |
| Native parity methodology | `FINDINGS.md` §3.32–3.37, §3.55; `deleted/docs/native/16.635.1/ROYALEFISH_CROSSCHECK.md` | A51 parity corpus, deterministic gates, x86_64 port, ARM64 observation parity, two-seat digest gate. |
| Bulk training backend (crforge) | `deleted/ROADMAP_CRFORGE.md`; `FINDINGS.md` §3.46–3.62; HANDOFF sessions 39–54 | C1–C6 stages, gates, vectorized observation facts, batched action decode, C4 producer/PPO loop, rollout store, rewards. |
| Low-level compiler bug | `deleted/docs/native/16.635.1/HOTSPOT_C2_SEATIDS.md`; HANDOFF sessions 50–52; `git_history/` commits around `2d68f02`, `998d7a8`, `78b591a`, `aa550d7`, `466a3ea` | C2 `SeatIds.of` miscompile: reproducer, fault-body mapping, negatives on fresh hardware, JIT flag workaround. A strong "engineering/debugging" contribution or appendix. |
| Agent training lineage | `deleted/docs/training_history.md` | IL → Active IL → General → Hog 2.6 specialist 1 → specialist 2. Includes the fixed-IL exploitation detour (296/306 wins vs a passive opponent), PPO setup (1,528 concurrent matches, 40-game-second rollouts, 8 GPUs), and evaluation conditions. |
| League / evaluation protocol | `deleted/ROADMAP.md` milestones N0–N5; HANDOFF session 53–54; `deleted/ROADMAP_CRFORGE.md` | Paired-game gates, Wilson intervals, seat splits, illegal-action counts. The first 50-update league was negative: 42–108 (28.0 %, Wilson [21.4, 35.7]) vs `B-general-v275`. |
| A51 / device deployment | `deleted/docs/native/PHASE2_BOTTLENECK.md`; `FINDINGS.md` §3.24–3.25; `deleted/launch_cluster_a51.sh`, `deploy_a51.sh`, `launch_ppo_a51.sh`, `build_cluster_apk.sh`, `build_offline_apk.sh` | Cpusets, control-lane affinity, multi-engine aggregation, cluster launch. |
| Live capture / behavior cloning | `deleted/docs/capture_format.md` | Versioned capture schema, header fields (client/data version, content SHA, libg SHA, reader build), frame format, replay conversion contract. |
| Data / card catalogs | `current_docs/docs/native/16.635.1/*.json`; `deleted/docs/native/COMPETITIVE_RELEASES.md`; `current_docs/competitive-16.635_manifest.json` | Offset diffs, vtables, champion layout, form evidence, catalog IDs/hashes. |
| Compute economics | `deleted/ROADMAP_CRFORGE.md` §4; `deleted/ROADMAP.md` §9; HANDOFF sessions 45–50 | A6000 rental decision, provider equation, measured producer/forward costs, why CPU batching/ MPS did or did not help. |

## High-value quantitative claims to verify before citing

These are all in the recovered docs; re-check them against the code/commits
because some were later superseded.

- Native x86_64: **0.61 matches/s raw / 0.082 training floor per process**;
  crforge **2.6–13.0 matches/s** on a laptop (1–8 threads), **18.5–52.5** on an
  M4 Pro; A51 **12–14 matches/s** is a 600-tick transport ceiling (0.26 at the
  PPO 5-tick cadence). (`ROADMAP.md` N3, 2026-11-15.)
- Host engine at 100 ticks/RPC: **36.4k ticks/s at 8 workers, 40.2k at 12**;
  ≈9.6/10.6 matches/s at 3,790 ticks/match. (`PHASE2_BOTTLENECK.md`.)
- Decision path: JSON-RPC serialization ≈ half the worker time, parsing ≈ a
  third, JNI < 10 %; fast-json is **1.5–2.1×** faster; 4–8 ticks/decision lifts
  the loop from **0.074 to 0.277/0.522 matches/s** at 8 workers.
  (`PHASE2_BOTTLENECK.md`.)
- C3-B batched packet: **7–8.5×** the stock tensorizer. (HANDOFF session 51.)
- Native parity cadence: **27.9 updates per gate** (ratio, not a count).
  (HANDOFF session 50b/commit `634b7c0`.)
- First N4 league gate: **42–108 (28.0 %)**, Wilson **[21.4, 35.7]**, seats
  22.7 % / 33.3 %, 0 illegal, 10 unique `card_still_in_slot` refusals.
  (HANDOFF session 53; `ROADMAP.md` N4 status.)
- Fixed-IL detour: **80.10 %** wins, but **296/306** wins came from matches
  where IL took ≤3 non-WAIT actions. (`training_history.md` §2.)
- Capacity model in the current README:
  `matches/s ≈ 0.211 × min(N_proc, G×E/g) / T`, with the 2026-11-23 example
  `1.34 m/s`. (README.)

## Historical versions worth diffing

`versions/` is useful for showing how claims evolved, not just what survived.

- `versions/HANDOFF.md/` — 50+ snapshots; the per-session "Bottom line"
  sections are appended, so diffs show what was learned in each session.
- `versions/docs/native/16.635.1/FINDINGS.md/` — largest version set; useful
  for offset corrections (e.g. `hand +0x210 → +0x220` debates, ready byte
  `0x6e8 → 0x720`, allocation `0x230 → 0x240`).
- `versions/ROADMAP.md/`, `versions/ROADMAP_CRFORGE.md/` — plan revisions,
  superseded compute decisions, and stage/gate changes.
- `snapshot_pre_deletion/` — one coherent tree at the peak of the planning
  docs, easier to read than many diffs.

## Method / reproducibility material

- `git_history/commits_full_stat.txt` — every commit with changed files and
  line counts; good for a "development process" figure.
- `git_history/deleted_files_manifest.tsv` and `deletion_timeline.md` — when and
  why docs disappeared.
- `git_history/dangling_commits.tsv` + `dangling_docs/` — unmerged experiments
  and measurements that never reached `main`.
- `research_data/runs_small/` — 804 small JSON/log/jsonl outputs from real runs
  (parity, step breakdowns, gates). `research_data/*_inventory.tsv` lists the
  rest with sizes.
- `deleted/build_cluster_apk.sh`, `build_offline_apk.sh`, `deploy_a51.sh`,
  `launch_cluster_a51.sh`, `launch_ppo_a51.sh` — exact cluster/build commands,
  useful for an appendix.

## Caveats

- Some numbers in roadmaps are targets, not results; the docs usually label
  them ("gate", "target", "plan of record"). Prefer HANDOFF/FINDINGS measured
  sections for results.
- HANDOFF and FINDINGS contain superseded claims by design. The top of each
  file is the latest state; later sections may be obsolete.
- The recovered `.pt` checkpoints predate the current V4 contract and may not
  load without the matching code/data release. Treat them as artifacts, not
  reproducible baselines, unless you pin the corresponding commit.
- `jason_notes/` are spoken-style video narration, not peer-reviewed prose;
  they are best used for motivation/history, with technical claims cross-checked
  against FINDINGS/ROADMAP.
