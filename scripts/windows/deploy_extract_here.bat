@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "REPO_DIR=%~f1"
set "OUTPUT_ARG=%~2"
if not defined OUTPUT_ARG (
  set "OUTPUT_FILE=%USERPROFILE%\Desktop\extract_here.bat"
) else (
  if /i "%OUTPUT_ARG:~-4%"==".bat" (
    set "OUTPUT_FILE=%OUTPUT_ARG%"
  ) else (
    set "OUTPUT_FILE=%OUTPUT_ARG%\extract_here.bat"
  )
)
for %%I in ("%OUTPUT_FILE%") do set "OUTPUT_FILE=%%~fI"
if exist "%OUTPUT_FILE%" goto output_exists
if not exist "%REPO_DIR%\scripts\windows\launcher.bat" (
  echo [ERROR] Repository launcher not found: "%REPO_DIR%\scripts\windows\launcher.bat"
  exit /b 10
)
set "REORDER_DEPLOY_REPO=%REPO_DIR%"
set "REORDER_DEPLOY_OUTPUT=%OUTPUT_FILE%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy_extract_here.ps1"
if errorlevel 1 (
  echo [ERROR] Could not create "%OUTPUT_FILE%". Existing files are never overwritten.
  exit /b 13
)
echo Created "%OUTPUT_FILE%" bound to repository "%REPO_DIR%".
echo The input folder defaults to the generated BAT's own directory.
exit /b 0

:output_exists
echo [ERROR] Refusing to overwrite existing path: "%OUTPUT_FILE%"
exit /b 13
