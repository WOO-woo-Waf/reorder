@echo off
setlocal

chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "TARGET_DIR=%~1"
if "%TARGET_DIR%"=="" set "TARGET_DIR=%SCRIPT_DIR%"
if "%TARGET_DIR:~-1%"=="\" set "TARGET_DIR=%TARGET_DIR:~0,-1%"

if not exist "%TARGET_DIR%" (
  echo [ERROR] Target folder does not exist: "%TARGET_DIR%"
  exit /b 10
)

echo [CLEANUP] target=%TARGET_DIR%
echo [CLEANUP] removing extract artifacts: success, intermediate

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$root = (Resolve-Path -LiteralPath '%TARGET_DIR%').Path;" ^
  "$artifactNames = @('success', 'intermediate');" ^
  "foreach ($name in $artifactNames) {" ^
  "  $path = Join-Path $root $name;" ^
  "  if (Test-Path -LiteralPath $path -PathType Container) {" ^
  "    Write-Host ('[REMOVE] ' + $path);" ^
  "    Remove-Item -LiteralPath $path -Recurse -Force;" ^
  "  }" ^
  "}" ^
  "do {" ^
  "  $removed = 0;" ^
  "  Get-ChildItem -LiteralPath $root -Directory -Recurse -Force | Sort-Object FullName -Descending | ForEach-Object {" ^
  "    if (-not (Get-ChildItem -LiteralPath $_.FullName -Force | Select-Object -First 1)) {" ^
  "      Write-Host ('[EMPTY] ' + $_.FullName);" ^
  "      Remove-Item -LiteralPath $_.FullName -Force;" ^
  "      $removed++;" ^
  "    }" ^
  "  }" ^
  "} while ($removed -gt 0);" ^
  "Write-Host '[CLEANUP] done';"

set "RC=%errorlevel%"
echo Done. rc=%RC%
exit /b %RC%
