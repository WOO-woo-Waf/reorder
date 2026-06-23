@echo off
setlocal
chcp 65001 >nul

set "VERSION=%~1"
if "%VERSION%"=="" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\release_windows_dist.ps1"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\release_windows_dist.ps1" -Version "%VERSION%"
)
set "RC=%errorlevel%"

echo Done. rc=%RC%
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
