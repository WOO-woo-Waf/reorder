param(
  [string]$Python = "python",
  [string]$SevenZip = "",
  [switch]$PortableOnly,
  [switch]$SkipDependencies,
  [switch]$SkipEngineStage,
  [switch]$SkipFrontendBuild,
  [string]$TauriCli = ""
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $repo "runtime\desktop-build-venv"
$buildPython = Join-Path $venv "Scripts\python.exe"
function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
  & $Exe @Arguments
  if ($LASTEXITCODE -ne 0) { throw "$Exe failed with exit code $LASTEXITCODE" }
}
Push-Location $repo
try {
  if (!(Test-Path $buildPython)) { Invoke-Checked $Python @("-m", "venv", $venv) }
  if (!$SkipDependencies) {
    Invoke-Checked $buildPython @("-m", "pip", "install", "-r", (Join-Path $PSScriptRoot "requirements-desktop-windows.lock.txt"))
  }
  $stageArgs = @((Join-Path $PSScriptRoot "stage_desktop_engine.py"))
  if ($SevenZip) { $stageArgs += @("--seven-zip", $SevenZip) }
  if ($SkipEngineStage) { $stageArgs += @("--validate-only") }
  Invoke-Checked $buildPython $stageArgs
  Invoke-Checked $buildPython @((Join-Path $PSScriptRoot "collect_desktop_licenses.py"))
  # Exact docs/diagrams/license selection shared with the portable assembler. This
  # replaces the old wildcard copy so stale generated files cannot leak into a build;
  # the previous trees are snapshotted under artifacts\desktop\resource-backups
  # (git-ignored) before they are rebuilt, never deleted in place.
  $resources = Join-Path $repo "apps\desktop\src-tauri\resources"
  Invoke-Checked $buildPython @((Join-Path $PSScriptRoot "stage_desktop_resources.py"), "--repo", $repo, "--resources", $resources)
  $env:CARGO_BUILD_JOBS = "2"
  Push-Location (Join-Path $repo "apps\desktop")
  try {
    if (!$SkipDependencies) { Invoke-Checked "npm.cmd" @("ci", "--no-audit", "--no-fund") }
    if (!$SkipFrontendBuild) { Invoke-Checked "npm.cmd" @("run", "check") }
    $arguments = @("build", "--features", "custom-protocol")
    if ($PortableOnly) { $arguments += "--no-bundle" }
    $override = Join-Path $repo "runtime\desktop-bundle.config.json"
    $overrideJson = '{"bundle":{"useLocalToolsDir":true}}'
    if ($SkipFrontendBuild) {
      if (!(Test-Path "dist\index.html")) { throw "Frontend dist is missing." }
      $overrideJson = '{"build":{"beforeBuildCommand":null},"bundle":{"useLocalToolsDir":true}}'
    }
    [IO.File]::WriteAllText($override, $overrideJson, [Text.UTF8Encoding]::new($false))
    $arguments += @("--config", $override)
    if ($TauriCli) { Invoke-Checked "node.exe" (@($TauriCli) + $arguments) }
    else { Invoke-Checked "npm.cmd" (@("run", "tauri", "--") + $arguments) }
  } finally { Pop-Location }
  Invoke-Checked $buildPython @((Join-Path $PSScriptRoot "package_desktop.py"))
} finally { Pop-Location }
