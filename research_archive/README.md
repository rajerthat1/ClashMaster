# ClashMaster research archive

Recovered and compiled on 2026-11-23 from the full git history of
`/home/raj/Desktop/ClashMaster` for writing a research paper.

Everything here was reconstructed from git objects (nothing was edited). The
repository no longer ships most of these documents on `main`; they were
recovered from the commits that deleted them.

## What is here

| Folder | Contents |
| --- | --- |
| `deleted/` | Every tracked file ever deleted in the repo (41 files), restored at its last surviving version, preserving original paths. Includes `HANDOFF.md`, `ROADMAP*.md`, `FINDINGS.md`, all `jason_notes/`, `issue.txt`, `AIFeed.txt`, `training_history.md`, native phase docs, capture format, build/launch scripts, old checkpoints and media. |
| `versions/` | Every distinct historical version of the key documents (328 versions), one `.txt` per commit where the content changed. Each doc folder has an `INDEX.tsv` (date, commit, blob hash). |
| `snapshot_pre_deletion/` | The complete planning/doc tree as it existed at commit `2ea1958` (2026-11-19), immediately before `5be13cb` deleted it. |
| `dangling_docs/` | Docs found only in unreachable/dangling commits (`git fsck --lost-found`), grouped by commit. These are work that was never merged into `main`. |
| `current_docs/` | The surviving documentation on `main` today, copied for side-by-side comparison. |
| `git_history/` | Commit log (oneline + full stat), branches, reflog, dangling commits, renames, deletion timeline, deleted-file manifest, key-doc history, working-tree status. |
| `research_data/` | Small research-result files copied from `runs/` (all files < 200 KB; 804 files, ~14 MB), plus misc untracked files (`daybreak.pdf`, `README.md.bak`, `docs/live_official_client.md`) and full inventories of `runs/` and `artifacts/`. |
| `PAPER_SOURCE_NOTES.md` | A map of the recovered material to likely paper sections/themes. |

## The big deleted documents

| File | Lines | What it is |
| --- | ---: | --- |
| `deleted/HANDOFF.md` | 6,878 | Session-by-session engineering log from 2026-11-15 to 2026-11-19 (sessions 39–54). Decisions, measurements, failures, C2/C3/C4 work, league runs, gates, reproducibility details. |
| `deleted/docs/native/16.635.1/FINDINGS.md` | 7,706 | Static-analysis notebook for `libg.so` 16.635.1: binary identity, tooling, verified anchors/offsets, hook relocation, game-state layout, probe wiring, parity, throughput, evaluator, N0–N4 work. |
| `deleted/ROADMAP.md` | 888 | Full research/program roadmap: target change rationale, milestones, architecture, workstreams A–F, proxy track, risks, QA harness. |
| `deleted/ROADMAP_CRFORGE.md` | 1,069 | crforge bulk-training backend plan: baseline, stages/gates, fidelity contract, economics/capacity equation, CI/parity role, proven commands. |
| `deleted/docs/native/16.635.1/HOTSPOT_C2_SEATIDS.md` | 432 | The C2 `SeatIds.of` miscompile investigation and reproducer. |
| `deleted/docs/native/PHASE0_BRINGUP.md` | 269 | Phase 0 bring-up. |
| `deleted/docs/native/PHASE2_BOTTLENECK.md` | 241 | Measured throughput/bottleneck analysis (engine vs JSON-RPC vs inference). |
| `deleted/docs/native/LINUX_BIONIC_RUNTIME.md` | 177 | Linux/Bionic runtime port notes. |
| `deleted/docs/native/COMPETITIVE_RELEASES.md` | 116 | Versioned competitive data releases and catalog hashes. |
| `deleted/docs/native/R0_CANONICAL_TREE.md` | 119 | R0 canonical decision/tree description. |
| `deleted/docs/native/16.635.1/ROYALEFISH_CROSSCHECK.md` | 193 | RoyaleFish x86 relocation cross-check. |
| `deleted/docs/capture_format.md` | 317 | Live-capture schema (`.crfcapture.jsonl.zst`) and replay conversion contract. |
| `deleted/docs/training_history.md` | 90 | Model lineage: IL → Active IL → General → Hog 2.6 specialists, with measured results. |
| `deleted/jason_notes/*.txt` | 107 | Narration/notes for five JasonMoonW videos: simulator, live memory reading, architecture & LLM reverse-engineering workflow, A* pathfinding, native-engine RL. |
| `deleted/issue.txt` | — | Feasibility/cost analysis of targeting the official Supercell client vs Null's Royale. |
| `deleted/AIFeed.txt` | — | Short working note about the crforge repo, ADB/emulator, and API-token constraints. |

Smaller recovered items: `ROADMAP_CRFORGE_ADAPTER.md`, `ROADMAP_EXTRACTED.md`,
`ROADMAP_LIVE_B_TO_C.md`, `ROADMAP_NULLS16_PROBE.md`, `README.upstream.md`,
`README.zh-CN.md`, `resources.txt`, `LICENSE`, `NOTICE`,
`build_cluster_apk.sh`, `build_offline_apk.sh`, `deploy_a51.sh`,
`launch_cluster_a51.sh`, `launch_ppo_a51.sh`, `scripts/sync-upstream.sh`,
three deleted PNG/GIF assets, and four old `.pt` checkpoints.

## Recovery method

```bash
# list every file deleted anywhere in history
git log --all --diff-filter=D --name-only --pretty=format:

# for each path, find the newest commit that still contains it, then extract
git log --all --full-history --format=%H -- <path>
git show <commit>:<path>
```

- Deletions came from 8 commits; the big one is
  `5be13cb chore(repo): revamp docs and remove planning clutter` (2026-11-19),
  which removed ~20k lines across planning docs.
- `git_history/deleted_files_manifest.tsv` records, per file: deleting commit,
  deletion date, last containing commit, and recovered byte size.
- `git_history/deletion_timeline.md` groups deletions by commit.
- No commits or tags were rewritten; the repo is untouched except for this new
  untracked `research_archive/` folder.

## Notes and caveats

- Dates are git author dates; the repo timeline is 2026-09-06 → 2026-11-23
  (208 commits).
- `deleted/checkpoints/` contains four old `.pt` files (~185 MB total). They are
  included because they were deleted, but are binaries and may require the
  original training code to interpret.
- `deleted/docs/assets/battle-montage.gif` is ~12 MB.
- `versions/` contains duplicate-free content per commit; use `INDEX.tsv` to map
  a version to its commit.
- `dangling_docs/` comes from unreachable commits. Some are superseded WIP; they
  are kept because they may contain measurements that never landed.
- `research_data/runs_small/` excludes files ≥ 200 KB. For those, see
  `research_data/runs_inventory.tsv` (sizes and paths) and the live `runs/`
  directory (2.9 GB).
- `research_data/artifacts_inventory.tsv` inventories `artifacts/` (666 MB)
  without copying it.
