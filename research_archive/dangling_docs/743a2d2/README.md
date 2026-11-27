# ClashMaster

Clash Royale AI research workspace targeting a single-deck (Hog 2.6) bot.

The project is built on top of
[FirstLight_CR](https://github.com/Jaasssoooonnnnn/FirstLight_CR), which provides the native battle
engine, the IL + PPO training stack, the 252k replay dataset and the released checkpoints.

- **[ROADMAP.md](ROADMAP.md)** — the full plan, phase by phase, from environment setup to a Hall of
  Fame push.
- **`FirstLight_CR/`** — git submodule pointing at
  [rajerthat1/FirstLight_CR](https://github.com/rajerthat1/FirstLight_CR), a fork of upstream. Your
  code changes and the agent's changes go here; upstream fixes are merged in.

## Clone

```bash
git clone --recurse-submodules https://github.com/rajerthat1/ClashMaster.git
cd ClashMaster

# If you cloned without --recurse-submodules:
git submodule update --init --recursive
```

The replay dataset is **not** in git (1.8 GB). Download it from
[VanguardX101/IL_Replay](https://huggingface.co/datasets/VanguardX101/IL_Replay) into
`FirstLight_CR/datasets/royaleapi/{replays,actions}/`. Checkpoints are already tracked by the
submodule.

## Staying in sync with upstream

```bash
./scripts/sync-firstlight.sh
```

The script fetches `Jaasssoooonnnnn/FirstLight_CR`, merges `upstream/main` into the fork's `main`,
pushes the fork, and commits the updated submodule pointer here. Resolve conflicts inside
`FirstLight_CR/` as usual.

Remotes inside the submodule:

- `origin` → `https://github.com/rajerthat1/FirstLight_CR` (your fork)
- `upstream` → `https://github.com/Jaasssoooonnnnn/FirstLight_CR` (the source project)

## Layout

| Path | Purpose |
| --- | --- |
| `FirstLight_CR/` | Forked training stack (submodule) |
| `.resource/` | Other locally cloned reference repos, ignored by git |
| `ROADMAP.md` | Project plan and risk register |
