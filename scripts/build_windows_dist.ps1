param(
  [string]$OutputRoot = "dist",
  [string]$Name = "reorder-extract-windows",
  [string]$CondaEnv = "reorder",
  [string]$Version = "dev"
)

$ErrorActionPreference = "Stop"

$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
$distParent = Join-Path $repo $OutputRoot
$appDir = Join-Path $distParent $Name
$workDir = Join-Path $repo "build\pyinstaller"
$specDir = Join-Path $repo "build\pyinstaller-spec"

Remove-Item -LiteralPath $appDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $distParent, $workDir, $specDir | Out-Null

$env:PYTHONPATH = Join-Path $repo "src"

function Write-Utf8NoBom {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Content
  )
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Invoke-BuildPython {
  param([string[]]$Arguments)
  if ($CondaEnv) {
    & conda run -n $CondaEnv python @Arguments
  } else {
    & python @Arguments
  }
  if ($LASTEXITCODE -ne 0) {
    throw "Python command failed with exit code $LASTEXITCODE"
  }
}

Invoke-BuildPython @(
  "-m", "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onedir",
  "--name", "reorder-extract",
  "--console",
  "--paths", (Join-Path $repo "src"),
  "--distpath", $distParent,
  "--workpath", $workDir,
  "--specpath", $specDir,
  (Join-Path $repo "src\reorder_engine\portable.py")
)

$builtDir = Join-Path $distParent "reorder-extract"
if (Test-Path $appDir) {
  Remove-Item -LiteralPath $appDir -Recurse -Force
}
Move-Item -LiteralPath $builtDir -Destination $appDir

New-Item -ItemType Directory -Force -Path `
  (Join-Path $appDir "resources"), `
  (Join-Path $appDir "tools\7zip"), `
  (Join-Path $appDir "tools\rar"), `
  (Join-Path $appDir "tools\bandizip") | Out-Null

Copy-Item -LiteralPath (Join-Path $repo "resources\passwords.txt") -Destination (Join-Path $appDir "resources\passwords.txt") -Force
Copy-Item -LiteralPath (Join-Path $repo "resources\keywords.txt") -Destination (Join-Path $appDir "resources\keywords.txt") -Force

$sevenRoot = Join-Path $repo "tools\7zip\Files\7-Zip"
$sevenDest = Join-Path $appDir "tools\7zip"
foreach ($file in @("7z.exe", "7z.dll", "License.txt", "readme.txt")) {
  $src = Join-Path $sevenRoot $file
  if (Test-Path $src) {
    Copy-Item -LiteralPath $src -Destination (Join-Path $sevenDest $file) -Force
  }
}

$rarCandidates = @(
  "D:\RAR\Rar.exe",
  "D:\RAR\UnRAR.exe",
  "C:\Program Files\WinRAR\Rar.exe",
  "C:\Program Files\WinRAR\UnRAR.exe",
  "C:\Program Files (x86)\WinRAR\Rar.exe",
  "C:\Program Files (x86)\WinRAR\UnRAR.exe"
)
foreach ($candidate in $rarCandidates) {
  if (Test-Path $candidate) {
    $rarSourceDir = Split-Path $candidate -Parent
    foreach ($file in @("Rar.exe", "UnRAR.exe", "License.txt", "Rar.txt", "readme.txt")) {
      $src = Join-Path $rarSourceDir $file
      if (Test-Path $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $appDir ("tools\rar\" + $file)) -Force
      }
    }
    break
  }
}

$bandizipCandidates = @(
  "D:\bandzip\bz.exe",
  "D:\Bandizip\bz.exe",
  "C:\Program Files\Bandizip\bz.exe",
  "C:\Program Files (x86)\Bandizip\bz.exe"
)
foreach ($candidate in $bandizipCandidates) {
  if (Test-Path $candidate) {
    $bandizipSourceDir = Split-Path $candidate -Parent
    foreach ($file in @("bz.exe", "ark.x64.dll", "ark.x64.lgpl.dll", "config.ini", "VersionNo.ini")) {
      $src = Join-Path $bandizipSourceDir $file
      if (Test-Path $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $appDir ("tools\bandizip\" + $file)) -Force
      }
    }
    break
  }
}

$config = @{
  version = 1
  beta = @{
    flatten = @{
      enabled = $true
      exclude_dirs = @("success", "extracted", "intermediate", "final", "failed", "error_files", "deferred_volumes", "archives_success", "resources", "tests", "tools", "__pycache__", "_internal")
      allowed_roots = @()
      allow_inside_project_repo = $false
    }
    exclude = @{
      names = @("config.json", "reorder-extract.exe")
      exts = @(".bat", ".cmd", ".ps1", ".py", ".json", ".md", ".log")
    }
    guess_suffixes = @(".7z", ".zip", ".rar", ".tar", ".tgz", ".tar.gz", ".gz", ".bz2", ".xz")
    log_passwords = $true
    extractor_order = @("7z", "unrar", "bandizip")
    preserve_payload_names = $true
    duplicates_dir_name = "_duplicates"
    path_compress = $true
    rules = @{ max_restore_rounds = 3 }
    deep_extract = @{ enabled = $true; max_depth = 4; min_archive_mb = 100; final_single_mb = 200 }
  }
  paths = @{
    keywords = "resources/keywords.txt"
    passwords = "resources/passwords.txt"
  }
  tools = @{
    seven_zip = @{
      exe = "tools\7zip\7z.exe"
      auto_download = $false
      download = @{ source = "7-zip.org"; prefer = "msi-x64"; timeout_sec = 60 }
      install_dir = "tools/7zip"
    }
    unrar = @{ exe = "tools\rar\Rar.exe" }
    bandizip = @{ exe = "tools\bandizip\bz.exe" }
  }
}

$configJson = $config | ConvertTo-Json -Depth 20
Write-Utf8NoBom -Path (Join-Path $appDir "config.json") -Content ($configJson + [Environment]::NewLine)

$bat = @'
@echo off
setlocal
chcp 65001 >nul
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
pushd "%APP_DIR%"
set "TARGET_DIR=%~1"
if "%TARGET_DIR%"=="" (
  if exist "%APP_DIR%\target_folder.txt" (
    set /p TARGET_DIR=<"%APP_DIR%\target_folder.txt"
  )
)
if "%TARGET_DIR%"=="" set "TARGET_DIR=%APP_DIR%"
if "%TARGET_DIR%"=="." set "TARGET_DIR=%APP_DIR%"
"%APP_DIR%\reorder-extract.exe" --folder "%TARGET_DIR%"
set "RC=%errorlevel%"
popd
echo Done. rc=%RC%
pause
exit /b %RC%
'@
$bat | Set-Content -LiteralPath (Join-Path $appDir "extract_here.bat") -Encoding ASCII
"." | Set-Content -LiteralPath (Join-Path $appDir "target_folder.txt") -Encoding ASCII
Write-Utf8NoBom -Path (Join-Path $appDir "VERSION.txt") -Content ($Version + [Environment]::NewLine)

$readme = @"
Reorder Extract Windows Portable
================================

Version: $Version

Quick start:
1. Put archives in this folder, or edit target_folder.txt to point at another folder.
2. Double-click extract_here.bat.
3. Results are written under final, success, failed, intermediate, and log files in the target folder.

Editable files:
- target_folder.txt: default "." means this portable tool folder. Absolute paths such as D:\DownloadLink\unzip-buff are supported.
- resources\passwords.txt: password library, one password per line. Lines starting with # are ignored.
- resources\keywords.txt: keyword library, one item per line. Lines starting with # are ignored.

Do not remove:
- reorder-extract.exe
- _internal\
- config.json
- tools\

CLI:
  reorder-extract.exe --folder D:\path\to\archives

Logs are written into the target folder:
  reorder_engine.extract.log
  reorder_engine.extract.tools.log
"@
Write-Utf8NoBom -Path (Join-Path $appDir "README.txt") -Content ($readme + [Environment]::NewLine)

Write-Host "Built: $appDir"
