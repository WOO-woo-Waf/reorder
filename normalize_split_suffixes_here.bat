@echo off
setlocal

chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "TARGET_DIR=%~1"
if "%TARGET_DIR%"=="" set "TARGET_DIR=%SCRIPT_DIR%"
if /I "%TARGET_DIR%"=="/dry-run" (
  set "DRY_RUN=1"
  set "TARGET_DIR=%SCRIPT_DIR%"
) else (
  set "DRY_RUN=0"
)
if "%TARGET_DIR:~-1%"=="\" set "TARGET_DIR=%TARGET_DIR:~0,-1%"

if not exist "%TARGET_DIR%" (
  echo [ERROR] Target folder does not exist: "%TARGET_DIR%"
  exit /b 10
)

echo [NORMALIZE-SPLIT] target=%TARGET_DIR%
if "%DRY_RUN%"=="1" echo [NORMALIZE-SPLIT] dry-run only

set "TARGET_DIR_ENV=%TARGET_DIR%"
set "DRY_RUN_ENV=%DRY_RUN%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$root = (Resolve-Path -LiteralPath $env:TARGET_DIR_ENV).Path;" ^
  "$dryRun = $env:DRY_RUN_ENV -eq '1';" ^
  "$archiveExts = @('7z', 'zip', 'rar');" ^
  "$tailExts = @('zip', 'jpg', 'jpeg', 'png', 'webp', 'mp4', 'mkv', 'avi', 'mov', 'exe');" ^
  "$renames = New-Object System.Collections.Generic.List[object];" ^
  "foreach ($ext in $archiveExts) {" ^
  "  foreach ($bare in Get-ChildItem -LiteralPath $root -File -Force | Where-Object { $_.Name -match ('(?i)\.' + [regex]::Escape($ext) + '$') }) {" ^
  "    foreach ($tail in $tailExts) {" ^
  "      $disguisedName = $bare.Name + '.' + $tail;" ^
  "      $disguised = Join-Path $root $disguisedName;" ^
  "      if (-not (Test-Path -LiteralPath $disguised -PathType Leaf)) { continue }" ^
  "      $target1 = Join-Path $root ($bare.Name + '.001');" ^
  "      $target2 = Join-Path $root ($bare.BaseName + '.' + $ext + '.002');" ^
  "      if ((Test-Path -LiteralPath $target1) -or (Test-Path -LiteralPath $target2)) {" ^
  "        Write-Host ('[SKIP] target exists for pair: ' + $bare.Name + ' + ' + $disguisedName);" ^
  "        continue;" ^
  "      }" ^
  "      $renames.Add([pscustomobject]@{ Source=$bare.FullName; Target=$target1 }) | Out-Null;" ^
  "      $renames.Add([pscustomobject]@{ Source=$disguised; Target=$target2 }) | Out-Null;" ^
  "      break;" ^
  "    }" ^
  "  }" ^
  "}" ^
  "if ($renames.Count -eq 0) { Write-Host '[NORMALIZE-SPLIT] no disguised split suffix pairs found'; exit 0 }" ^
  "$sources = @{}; $targets = @{};" ^
  "foreach ($r in $renames) {" ^
  "  if ($sources.ContainsKey($r.Source) -or $targets.ContainsKey($r.Target)) { throw ('duplicate rename plan: ' + $r.Source + ' -> ' + $r.Target) }" ^
  "  $sources[$r.Source] = $true; $targets[$r.Target] = $true;" ^
  "  Write-Host ('[PLAN] ' + [IO.Path]::GetFileName($r.Source) + ' -> ' + [IO.Path]::GetFileName($r.Target));" ^
  "}" ^
  "if ($dryRun) { Write-Host '[NORMALIZE-SPLIT] dry-run done'; exit 0 }" ^
  "$tempRenames = New-Object System.Collections.Generic.List[object];" ^
  "try {" ^
  "  foreach ($r in $renames) {" ^
  "    $tmp = Join-Path $root ([IO.Path]::GetFileName($r.Source) + '.renametmp.' + [guid]::NewGuid().ToString('N'));" ^
  "    Rename-Item -LiteralPath $r.Source -NewName ([IO.Path]::GetFileName($tmp));" ^
  "    $tempRenames.Add([pscustomobject]@{ Temp=$tmp; Final=$r.Target; Original=$r.Source }) | Out-Null;" ^
  "  }" ^
  "  foreach ($r in $tempRenames) {" ^
  "    Rename-Item -LiteralPath $r.Temp -NewName ([IO.Path]::GetFileName($r.Final));" ^
  "    Write-Host ('[RENAMED] ' + [IO.Path]::GetFileName($r.Final));" ^
  "  }" ^
  "} catch {" ^
  "  Write-Host ('[ERROR] ' + $_.Exception.Message);" ^
  "  foreach ($r in $tempRenames) {" ^
  "    if ((Test-Path -LiteralPath $r.Temp) -and -not (Test-Path -LiteralPath $r.Final)) {" ^
  "      Rename-Item -LiteralPath $r.Temp -NewName ([IO.Path]::GetFileName($r.Original)) -ErrorAction SilentlyContinue;" ^
  "    }" ^
  "  }" ^
  "  exit 20;" ^
  "}" ^
  "Write-Host '[NORMALIZE-SPLIT] done';"

set "RC=%errorlevel%"
echo Done. rc=%RC%
exit /b %RC%
