@echo off
setlocal
chcp 65001 >nul

set "ZIP_PATH=%~1"
set "TAG=%~2"

if "%ZIP_PATH%"=="" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\publish_github_release.ps1"
) else (
  if "%TAG%"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\publish_github_release.ps1" -ZipPath "%ZIP_PATH%"
  ) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\publish_github_release.ps1" -ZipPath "%ZIP_PATH%" -Tag "%TAG%"
  )
)
set "RC=%errorlevel%"

echo Done. rc=%RC%
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
