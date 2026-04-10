@echo off
setlocal

chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "WORK_DIR=%REORDER_WORK_DIR%"
if "%WORK_DIR%"=="" set "WORK_DIR=D:\buff\reorder"
if not exist "%WORK_DIR%\pyproject.toml" (
  if exist "%SCRIPT_DIR%\pyproject.toml" set "WORK_DIR=%SCRIPT_DIR%"
)
if not exist "%WORK_DIR%\pyproject.toml" (
  echo [ERROR] Invalid WORK_DIR: "%WORK_DIR%"
  echo Set env REORDER_WORK_DIR to your repo path, for example:
  echo   set REORDER_WORK_DIR=D:\buff\reorder
  exit /b 10
)

if "%WORK_DIR:~-1%"=="\" set "WORK_DIR=%WORK_DIR:~0,-1%"
set "PYTHONPATH=%WORK_DIR%\src;%PYTHONPATH%"

set "TARGET_DIR=%~1"
if "%TARGET_DIR%"=="" set "TARGET_DIR=%WORK_DIR%"
if "%TARGET_DIR:~-1%"=="\" set "TARGET_DIR=%TARGET_DIR:~0,-1%"

if "%CONDA_ENV%"=="" set "CONDA_ENV=reorder"

set "ARCHIVE_MIN_MB=2000"
set "DEEP_MIN_MB=2000"
set "DEEP_FINAL_SINGLE_MB=2000"
set "DEEP_MAX_DEPTH=4"

echo [BASIC] folder=%TARGET_DIR% threshold=%ARCHIVE_MIN_MB%MB
pushd "%WORK_DIR%"
conda run -n %CONDA_ENV% --no-capture-output python -m reorder_engine.beta ^
  --workdir "%WORK_DIR%" ^
  --folder "%TARGET_DIR%" ^
  --archive-mode wide ^
  --archive-min-mb %ARCHIVE_MIN_MB% ^
  --deep-extract ^
  --deep-mode basic ^
  --deep-max-depth %DEEP_MAX_DEPTH% ^
  --deep-min-archive-mb %DEEP_MIN_MB% ^
  --deep-final-single-mb %DEEP_FINAL_SINGLE_MB% ^
  --deep-max-candidates 1 ^
  --log "%TARGET_DIR%\reorder_engine.basic.log" ^
  --tool-log "%TARGET_DIR%\reorder_engine.basic.tools.log"
set "RC=%errorlevel%"
popd

echo Done. rc=%RC%
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
