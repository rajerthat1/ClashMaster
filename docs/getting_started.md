# Getting started

This guide installs the offline engine from a fresh checkout. You supply the
original Null's Royale APK; the repository does not include it.

## Requirements

| Component | Requirement |
| --- | --- |
| Desktop | Windows with Python 3.12 and Tkinter |
| Emulator | MuMu Player, Android 12 instance, root enabled, ARM64 support |
| Game | Null's Royale, arm64-v8a: 15.535.13 by default, or 16.635.1 with `-TargetBuild 16.635.1` |
| Build tools | JDK 17, Android NDK, and SDK build-tools |

Build tools are only needed to create the offline APK. Running an APK that was
already built for this version does not require them; keep its matching
`.build.json` file next to the APK.

## 1. Set up Python

Run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill the local paths in `.env`. The main settings are:

| Setting | Value |
| --- | --- |
| `CR_PYTHON`, `CR_PYTHONW` | Absolute paths to `.venv/Scripts/python.exe` and `pythonw.exe` |
| `CR_MUMU_MANAGER` | Absolute path to `MuMuManager.exe` |
| `CR_ADB` | Absolute path to Android SDK `adb.exe` |
| `CR_VM_INDEX`, `CR_VM_NAME` | Index and exact name of the MuMu instance |
| `CR_ADB_SERIAL` | `127.0.0.1:<adb_port>` for that instance |
| `CR_JAVA`, `CR_KEYTOOL` | JDK executables |
| `CR_BUILD_TOOLS`, `CR_NDK_ROOT` | SDK build-tools directory and NDK root |

Find the instance name and ADB port with MuMu Manager:

```powershell
$manager = 'C:\path\to\MuMuManager.exe'
$vmIndex = 4
& $manager info --vmindex $vmIndex | ConvertFrom-Json |
  Select-Object index, name, adb_host_ip, adb_port
```

The scripts use the index, name, and ADB address to select the same instance.
Leave unrelated `.env` settings blank.

## 2. Update the original game

1. Put the unmodified APK at `user-provided/nulls-royale.apk`, or pass its path
   with `-InputApk`.
2. Install it in the selected MuMu instance and open it once so the client
   writes its `update/` folder. The pinned `15.535.86` content is no longer
   served; the client writes `update/fingerprint.json` for the base `15.535.3`
   pack instead. Close the game after that file appears.
3. Run the checks and build commands below in order.

`check_original_game.ps1` validates the local and installed APKs, Android 12,
and the downloaded resources. A mismatch stops the process.

```powershell
./check_original_game.ps1 -InputApk ./user-provided/nulls-royale.apk
./build_offline_engine.ps1 `
  -InputApk ./user-provided/nulls-royale.apk `
  -OutputApk ./build/cr-ai-offline.apk
./install_offline_engine.ps1 -Apk ./build/cr-ai-offline.apk
```

`build_offline_engine.ps1` defaults to `-TargetBuild 15.535.13` and reads the
matching engine hash from `docs/native/<build>/build.json`. Pass
`-TargetBuild 16.635.1` when building for that client.

The installer accepts the pinned `15.535.86` update or the content bundled in
the APK (`content_mode: apk-base`). Base content supports every match but cannot
satisfy the ruleset manifest, so attested training and evaluation need the
pinned update. For base content, select a location that exists there, for
example `CR_NATIVE_LOCATION=15000002`; the default `15000199` is available only
in `15.535.86`.

## 3. Open the interface

```powershell
./launch_interface.cmd
```

Use `./launch_interface.cmd --diagnose` to check that the configured instance is
found before starting a match. Verify the installation with the
[validation guide](validation.md).
