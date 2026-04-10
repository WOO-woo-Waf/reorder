@echo off
setlocal

chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "WORK_DIR=%REORDER_WORK_DIR%"
if "%WORK_DIR%"=="" set "WORK_DIR=%SCRIPT_DIR%"
if not exist "%WORK_DIR%\pyproject.toml" (
  if exist "%SCRIPT_DIR%\pyproject.toml" set "WORK_DIR=%SCRIPT_DIR%"
)
if not exist "%WORK_DIR%\pyproject.toml" (
  echo [ERROR] Invalid WORK_DIR: "%WORK_DIR%"
  echo Set REORDER_WORK_DIR to the repository path, for example:
  echo   set REORDER_WORK_DIR=D:\buff\reorder
  exit /b 10
)

if "%WORK_DIR:~-1%"=="\" set "WORK_DIR=%WORK_DIR:~0,-1%"
set "PYTHONPATH=%WORK_DIR%\src;%PYTHONPATH%"

set "TARGET_DIR=%~1"
if "%TARGET_DIR%"=="" set "TARGET_DIR=%SCRIPT_DIR%"
if "%TARGET_DIR:~-1%"=="\" set "TARGET_DIR=%TARGET_DIR:~0,-1%"

if "%CONDA_ENV%"=="" set "CONDA_ENV=reorder"
if "%NO_PAUSE%"=="" set "NO_PAUSE=0"

echo [EXTRACT] folder=%TARGET_DIR% workdir=%WORK_DIR%
pushd "%WORK_DIR%"
conda run -n %CONDA_ENV% --no-capture-output python -m reorder_engine.beta ^
  --workdir "%WORK_DIR%" ^
  --folder "%TARGET_DIR%" ^
  --deep-extract ^
  --preserve-payload-names ^
  --log "%TARGET_DIR%\reorder_engine.extract.log" ^
  --tool-log "%TARGET_DIR%\reorder_engine.extract.tools.log"
set "RC=%errorlevel%"
popd

echo Done. rc=%RC%
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%
