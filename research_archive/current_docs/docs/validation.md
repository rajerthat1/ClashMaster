# Validation

Run these checks after installation, when changing an engine or checkpoint, and
before starting a training run or publishing changes.

## Python and models

```sh
python -m pip check
python tools/check_install.py
```

`pip check` should report no dependency conflicts. `check_install.py` verifies
the data hashes, loads each included checkpoint, and prints `Ready: 5 models`.

## Tests

```sh
python -m pip install pytest
python -m pytest native_runner/tests -q
python -m pytest crforge_bridge/tests -q
```

The tests cover environment contracts, card mechanics, observations and actions,
native layouts, cache encoding, IL and PPO updates, and model serving. Set
`CR_INPUT_APK` to enable original-APK fingerprint checks. CUDA tests need a CUDA
device, and native C++ tests need `g++`; tests with unavailable prerequisites are
skipped.

## Interface

```powershell
./launch_interface.cmd --diagnose
./launch_interface.cmd
```

Check that the configured VM is found, a match starts at level 11, and both
manual and model actions execute. When diagnosing a problem, use
`runs/interface/firstlight-interface.log` together with the build and
installation receipts.

## Offline duel modes

- The deck editor has eight illustrated slots with searchable card and form
  selectors. Duplicate selections are blocked.
- The 7x random-evolution mode deals and previews both decks. Starting a match
  preserves the preview; the random-deck button redeals.
- AI-versus-AI duels warm up while paused, reset policy state, then advance
  exactly five ticks between decisions.
- Decision logs include latency and elixir. Results include action rejections
  and mean decision latency. Engine failures are written to `result.json`.

Unit checks do not start a VM or connect to the game:

```powershell
python -m pytest native_runner/tests/test_duel_modes.py `
  native_runner/tests/test_training_v4_offline_duel.py `
  native_runner/tests/test_training_v4_policy_session.py `
  native_runner/tests/test_user_interface.py -q
```

Manual acceptance is separate: with a dedicated VM available, finish a standard,
7x, Ramp Up, and All Evolution match; inspect action rejections and five-tick
decision spacing; then stop, restart, and switch modes. Do not run this while
the VM is in use by another project.

## Release files

Before publishing repository changes, run:

```sh
python tools/check_release.py
```

This checks the distribution file set, checkpoint and data hashes, the
configuration template, and ignore rules. A successful result ends with
`0 errors`.
