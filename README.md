# ClashMaster

Clash Royale AI research workspace targeting a single-deck (**Hog 2.6**) bot that can push to
Hall of Fame.

This repository is a fork of
[FirstLight_CR](https://github.com/Jaasssoooonnnnn/FirstLight_CR), which provides the native battle
engine, the IL + PPO training stack, the 252k replay dataset and the released checkpoints. The fork
keeps upstream history and adds this project's roadmap and experiments on top.

- **[ROADMAP.md](ROADMAP.md)** — the full plan, phase by phase, from environment setup to a Hall of
  Fame push.
- **[README.upstream.md](README.upstream.md)** — the original FirstLight_CR documentation, including
  model choices and training history links.

## Clone

```bash
git clone https://github.com/rajerthat1/ClashMaster.git
cd ClashMaster
```

The replay dataset is **not** in git (1.8 GB). Download it from
[VanguardX101/IL_Replay](https://huggingface.co/datasets/VanguardX101/IL_Replay) into
`datasets/royaleapi/{replays,actions}/`. The five checkpoints are already tracked in `checkpoints/`.

## Staying in sync with upstream

```bash
./scripts/sync-upstream.sh
```

or manually:

```bash
git fetch upstream
git merge upstream/main
git push origin main
```

Resolve conflicts as usual. Remotes:

- `origin` → `https://github.com/rajerthat1/ClashMaster` (this project)
- `upstream` → `https://github.com/Jaasssoooonnnnn/FirstLight_CR` (source project)
- `fork` → `https://github.com/rajerthat1/FirstLight_CR` (pristine mirror, used for upstream PRs)

## Layout

| Path | Purpose |
| --- | --- |
| `native_runner/` | Engine transport, battle environment, IL/PPO training, evaluator |
| `checkpoints/` | Five released models (IL, Active IL, General, Hog 2.6 specialists) |
| `docs/` | Installation, training, architecture, evaluation, policy API |
| `datasets/` | Local replay data, ignored by git |
| `ROADMAP.md` | Project plan and risk register |
| `.resource/` | Other locally cloned reference repos, ignored by git |
