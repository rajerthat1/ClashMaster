param(
    [string]$NdkRoot = "",
    [string]$Output = (Join-Path $PSScriptRoot "out\libcrprobe.so"),
    [ValidateSet("15.535.13", "16.635.1")]
    [string]$TargetBuild = "15.535.13"
)

$ErrorActionPreference = "Stop"
. (Join-Path (Split-Path -Parent $PSScriptRoot) "local_config.ps1")
if (-not $PSBoundParameters.ContainsKey("NdkRoot")) { $NdkRoot = Get-LocalSetting "CR_NDK_ROOT" }
if (-not $PSBoundParameters.ContainsKey("Output") -and $env:CR_PROBE) { $Output = $env:CR_PROBE }
$compiler = Join-Path $NdkRoot "toolchains\llvm\prebuilt\windows-x86_64\bin\aarch64-linux-android24-clang++.cmd"
$source = Join-Path $PSScriptRoot "cr_replay_probe.cpp"
$touchSource = Join-Path $PSScriptRoot "native_touch_interceptor.cpp"
$assemblySource = Join-Path $PSScriptRoot "native_call_arm64.S"
$remainingAssemblySource = Join-Path $PSScriptRoot "remaining_runtime_arm64.S"
$outputPath = [IO.Path]::GetFullPath($Output)
$outputDirectory = Split-Path -Parent $outputPath

if (-not (Test-Path -LiteralPath $compiler)) {
    throw "Missing NDK compiler: $compiler"
}
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$targetDefines = @()
if ($TargetBuild -eq "16.635.1") {
    $targetDefines += "-DCR_PROBE_TARGET_16_635_1"
}

& $compiler `
    -std=c++17 `
    -O2 `
    -fPIC `
    -fvisibility=hidden `
    -Wall `
    -Wextra `
    @targetDefines `
    -shared `
    "-Wl,-z,max-page-size=16384" `
    -o $outputPath `
    $source `
    $touchSource `
    $assemblySource `
    $remainingAssemblySource `
    -llog `
    -ldl `
    -lz

if ($LASTEXITCODE -ne 0) {
    throw "AArch64 probe compilation failed with exit code $LASTEXITCODE"
}

Get-Item -LiteralPath $outputPath | Select-Object FullName, Length, LastWriteTime
