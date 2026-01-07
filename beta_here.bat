@echo off
setlocal

REM Prefer UTF-8 output
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

REM Workdir is fixed: this repo folder (where config/tools/keywords/passwords live)
set "WORK_DIR=%~dp0"
REM 去掉末尾的反斜杠（否则在某些命令转发/解析下可能把引号带进参数）
if "%WORK_DIR:~-1%"=="\" set "WORK_DIR=%WORK_DIR:~0,-1%"

REM Target folder: passed in as %1; default to WORK_DIR if not provided
set "TARGET_DIR=%~1"
if "%TARGET_DIR%"=="" set "TARGET_DIR=%WORK_DIR%"
if "%TARGET_DIR:~-1%"=="\" set "TARGET_DIR=%TARGET_DIR:~0,-1%"

REM Requires: conda env 'unzip' + reorder-engine installed (pip install -e .)
set "LOG_FILE=%TARGET_DIR%\reorder_engine.bat.log"
echo [%date% %time%] START folder=%TARGET_DIR% workdir=%WORK_DIR%> "%LOG_FILE%"

REM 用 conda run 更稳：避免 bat 里 conda activate 失效导致其实没跑起来
pushd "%WORK_DIR%"
conda run -n unzip --no-capture-output python -m reorder_engine.beta --workdir "%WORK_DIR%" --folder "%TARGET_DIR%" --self-check --log "%TARGET_DIR%\reorder_engine.log" 1>> "%LOG_FILE%" 2>>&1
popd

echo [%date% %time%] END rc=%errorlevel%>> "%LOG_FILE%"
echo Done. See logs: "%LOG_FILE%" and "%TARGET_DIR%\reorder_engine.log"
exit /b %errorlevel%
