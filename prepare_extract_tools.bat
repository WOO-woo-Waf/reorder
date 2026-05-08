@echo off
setlocal

chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PYTHONPATH=%ROOT%\src;%PYTHONPATH%"

pushd "%ROOT%" >nul

python -m reorder_engine.beta --workdir "%ROOT%" --folder "%ROOT%" --prepare-tools --self-check
set "RC=%ERRORLEVEL%"

popd >nul
exit /b %RC%
