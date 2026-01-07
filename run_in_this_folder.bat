@echo off
setlocal

REM Launcher bat: copy this file into ANY target folder, then double-click.
REM It will treat the folder where this bat lives as the target folder.
REM Workdir (this repo) is fixed and hard-coded below.

REM Prefer UTF-8 output
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

REM ====== FIXED WORKDIR (repo folder) ======
set "WORK_DIR=D:\buff\unzip-clean-rename\reorder_engine"

REM Target folder: default to the folder where this bat lives
set "TARGET_DIR=%~dp0"
if "%TARGET_DIR:~-1%"=="\" set "TARGET_DIR=%TARGET_DIR:~0,-1%"

REM Log files live in TARGET_DIR
set "LOG_FILE=%TARGET_DIR%\reorder_engine.bat.log"
echo [%date% %time%] START folder=%TARGET_DIR% workdir=%WORK_DIR%> "%LOG_FILE%"

if not exist "%WORK_DIR%\pyproject.toml" (
  echo [%date% %time%] ERROR: WORK_DIR does not look like the repo folder: "%WORK_DIR%">> "%LOG_FILE%"
  echo WORK_DIR invalid: "%WORK_DIR%"
  echo Edit this bat and set WORK_DIR to your repo path.
  exit /b 10
)

REM Run from WORK_DIR so python can import reorder_engine reliably
pushd "%WORK_DIR%"

REM Requires: conda env 'unzip' + reorder-engine installed (pip install -e .)
conda run -n unzip --no-capture-output python -m reorder_engine.beta --workdir "%WORK_DIR%" --folder "%TARGET_DIR%" --self-check --log "%TARGET_DIR%\reorder_engine.log" 1>> "%LOG_FILE%" 2>>&1
set "RC=%errorlevel%"

popd

echo [%date% %time%] END rc=%RC%>> "%LOG_FILE%"
echo Done. See logs: "%LOG_FILE%" and "%TARGET_DIR%\reorder_engine.log"
exit /b %RC%
