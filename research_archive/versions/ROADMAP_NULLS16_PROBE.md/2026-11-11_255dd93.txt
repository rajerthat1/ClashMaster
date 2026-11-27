# Null's 16.635.1 — Simple Checklist

Goal: get the bot working on Null's 16.635.1 with the least work.

**Time at 10–15 h/week:** live bot 2–4 months · FirstLight training/eval 3–6 months · both 4–8 months.

## Pick one target

- [ ] **Live bot only** — content files + P0 probe/reader + Daybreak. Recommended if you already have a checkpoint and just want it playing.
- [ ] **Training/eval only** — content files + full probe + FirstLight. No Daybreak.
- [ ] **Both** — do the live bot first, then add training.

---

## 0. Freeze and acquire

- [ ] Save the working 15.535.13 APK, update folder, probe, and `build/cr-ai-offline.apk`.
- [ ] Record SHA-256 for the APK, `libg.so`, probe, `fingerprint.json`, `assets.scdb`, and update files.
- [ ] Get the unmodified Null's 16.635.1 APK.
- [ ] Check package `nullsroyale.rel.free`, ABI `arm64-v8a`, and version code.
- [ ] Open it once in a disposable Android 12 root instance; let content download; close it.
- [ ] Copy `/data/user/0/nullsroyale.rel.free/update` off the device.
- [ ] Record 16.635.1 hashes in `native_runner/versions/16.635.1.json`.
- [ ] Capture a clean boot `logcat`; confirm the `libg.so` path.
- [ ] Confirm JDK 17, NDK 27.3+, build-tools 37, Python 3.12.
- [ ] Choose host: Linux + Windows MuMu, Linux + rooted ARM64 phone, or Windows + WSL2.
- [ ] Optional: install REA (`npx rea-agents setup`) and configure Ghidra/JADX.

---

## 1. Content (2–4 weeks)

- [ ] Keep `fingerprint.json`, `assets.scdb`, and `csv_logic/` from the update folder.
- [ ] Check whether your deck uses only the existing 122 cards / 62 forms.
- [ ] If yes: **skip the full catalog diff** for now.
- [ ] If no: run `tools/content_probe.py` and diff 15.535.86 → 16.635.1.
- [ ] Update only the card forms your deck actually uses.
- [ ] Confirm the client loads and the content hashes match.

---

## 2. P0 offset map (2–4 weeks)

- [ ] Extract `libg.so`; record SHA-256 and build-id.
- [ ] Locate the 9 runner lifecycle functions.
- [ ] Locate tick, context A/B, state new/delete/ctor/setup/destroy.
- [ ] Locate JSON parse, command parse/append/queue, snapshot/deserialize.
- [ ] Locate command queue, card selection, tilemap, and native touch.
- [ ] Extract a 16-byte prologue for every P0 hook.
- [ ] Write `native_runner/versions/16.635.1/offsets.json`.
- [ ] Verify every offset against the binary before building.
- [ ] Optional: use REA for candidate functions and pseudocode, then verify each one.

---

## 3. P0 probe/reader (2–4 weeks)

- [ ] Add a version switch to the probe build.
- [ ] Add a Linux `build_probe.sh` wrapper if not using Windows.
- [ ] Move offsets/prologues into a generated version header.
- [ ] Port the P0 runner hooks.
- [ ] Port the native touch interceptor.
- [ ] Build and install `libcrprobe.so`.
- [ ] Confirm `hook_install_ok=true`.
- [ ] Confirm `content_ready=true` and `content_assets_mapped=true`.
- [ ] Complete one cold-configure match with `truncated: 0`.

---

## 4. Pick one front end

### A. Daybreak live bot (recommended for live-only)

- [ ] Create `~/cr-clients/nulls-16/client.json`.
- [ ] Fill package, activity, serial, `reader`, `device_reader`, `manager_rva`, `root_context`.
- [ ] Add `check.version_code` and `check.libg_sha256`.
- [ ] Write the reader: open `/proc/<pid>/mem`, resolve `libg.so`, read manager/root context.
- [ ] Emit 20 Hz frames: elixir, hand, cycle, entities, towers, result.
- [ ] Compile the reader, push it, `chmod 755`, verify SHA-256.
- [ ] Run `play.py --client nulls-16 --watch` for three battles.
- [ ] Run `--queue` with the model; verify two-touch plays and hand refill.
- [ ] Check latency is under 100 ms.

### B. FirstLight training/eval

- [ ] Make `supported_engine.json` version-aware.
- [ ] Update `offline_build.py`, `offline_install.py`, `attestation.py`, `ruleset.py`.
- [ ] Update the GameApp/Titan bootstrap anchors.
- [ ] Build and install `cr-ai-offline.apk`.
- [ ] Run the 16.635.1 version of `check_original_game.ps1`.
- [ ] Run the demo cache with `--verify-roundtrip`.
- [ ] Run a 40-game evaluation; require `rejected_actions: 0` and `truncated: 0`.
- [ ] Run `serve_policy.py` and one manual model match.

---

## 5. Validate one battle

- [ ] One full live battle completes.
- [ ] No illegal taps.
- [ ] No crashes.
- [ ] Hand, elixir, towers, and result match the screen.
- [ ] Reconnect works after killing the game.
- [ ] Save the logs, probe attestation, and reader hashes.

---

## 6. Later — do not block the first bot

- [ ] Add combat-event hooks.
- [ ] Add phase-runtime hooks.
- [ ] Add remaining, tower-troop, visibility, and movement hooks.
- [ ] Run the full catalog diff for new cards, evos, and heroes.
- [ ] Fine-tune V4 for new forms.
- [ ] Add pattern scanning and a version registry.
- [ ] Run 20+ unattended games.
- [ ] Add CI tests for offsets, hashes, and content identity.

---

## Notes

- Linux is fine for content, RE, probe build, APK build, and training. Windows is only needed for MuMu.
- Linux-only: use a rooted ARM64 phone or a Windows VM for MuMu; port the `.ps1` scripts.
- REA speeds up Phase 2, but every offset still needs a prologue and runtime check.
- Do not port all 33 hooks for the first battle. P0 is enough to get it playing.
