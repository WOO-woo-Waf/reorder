@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "MODE=%~1"
set "ENTRY_FILE=%~f2"
for %%I in ("%~dp0..\..") do set "REPO_DIR=%%~fI"
set "WORK_DIR=%REORDER_WORK_DIR%"
if not defined WORK_DIR set "WORK_DIR=%REPO_DIR%"
if not exist "%WORK_DIR%\pyproject.toml" goto invalid_workdir
if not exist "%WORK_DIR%\src\reorder_engine\beta.py" goto invalid_workdir
for %%I in ("%WORK_DIR%") do set "WORK_DIR=%%~fI"
if "%WORK_DIR:~-1%"=="\" set "WORK_DIR=%WORK_DIR%."
set "PYTHONPATH=%WORK_DIR%\src;%PYTHONPATH%"

set "TARGET_DIR=%~3"
if not defined TARGET_DIR set "TARGET_DIR=%~dp2"
if defined TARGET_DIR for %%I in ("%TARGET_DIR%") do set "TARGET_DIR=%%~fI"
if "%TARGET_DIR:~-1%"=="\" if not "%TARGET_DIR:~1%"==":\" set "TARGET_DIR=%TARGET_DIR:~0,-1%"
if "%TARGET_DIR:~-1%"=="\" set "TARGET_DIR=%TARGET_DIR%."
if not defined NO_PAUSE set "NO_PAUSE=0"

call :select_python
if errorlevel 1 goto python_unavailable

if /i "%MODE%"=="extract" goto extract
if /i "%MODE%"=="self-check" goto self_check
if /i "%MODE%"=="basic" goto basic
if /i "%MODE%"=="smart" goto smart
if /i "%MODE%"=="prepare" goto prepare
echo [ERROR] Unknown launcher mode: "%MODE%"
exit /b 11

:extract
echo [EXTRACT] folder="%TARGET_DIR%" workdir="%WORK_DIR%"
%PYTHON_COMMAND% -m reorder_engine.beta --workdir "%WORK_DIR%" --folder "%TARGET_DIR%" --deep-extract --preserve-payload-names --log "%TARGET_DIR%\reorder_engine.extract.log" --tool-log "%TARGET_DIR%\reorder_engine.extract.tools.log"
set "RC=%ERRORLEVEL%"
goto finish

:self_check
echo [SELF-CHECK] folder="%TARGET_DIR%" workdir="%WORK_DIR%"
%PYTHON_COMMAND% -m reorder_engine.beta --workdir "%WORK_DIR%" --folder "%TARGET_DIR%" --self-check --log "%TARGET_DIR%\reorder_engine.log"
set "RC=%ERRORLEVEL%"
goto finish

:basic
set "ARCHIVE_MIN_MB=2000"
set "DEEP_MIN_MB=2000"
set "DEEP_FINAL_SINGLE_MB=2000"
set "DEEP_MAX_DEPTH=4"
echo [BASIC] folder="%TARGET_DIR%" threshold=%ARCHIVE_MIN_MB%MB
%PYTHON_COMMAND% -m reorder_engine.beta --workdir "%WORK_DIR%" --folder "%TARGET_DIR%" --archive-mode wide --archive-min-mb %ARCHIVE_MIN_MB% --deep-extract --deep-mode basic --deep-max-depth %DEEP_MAX_DEPTH% --deep-min-archive-mb %DEEP_MIN_MB% --deep-final-single-mb %DEEP_FINAL_SINGLE_MB% --deep-max-candidates 1 --log "%TARGET_DIR%\reorder_engine.basic.log" --tool-log "%TARGET_DIR%\reorder_engine.basic.tools.log"
set "RC=%ERRORLEVEL%"
goto finish

:smart
if not defined ARCHIVE_MIN_MB set "ARCHIVE_MIN_MB=128"
if not defined DEEP_MIN_MB set "DEEP_MIN_MB=128"
if not defined DEEP_FINAL_SINGLE_MB set "DEEP_FINAL_SINGLE_MB=512"
if not defined DEEP_MAX_DEPTH set "DEEP_MAX_DEPTH=6"
if not defined DEEP_MAX_CANDIDATES set "DEEP_MAX_CANDIDATES=3"
if not defined CLEANUP_DYNAMIC_LIBS set "CLEANUP_DYNAMIC_LIBS=0"
if not defined DYNAMIC_LIB_PATTERNS set "DYNAMIC_LIB_PATTERNS=*dynamic*config*.dll;*dyn*config*.dll;*动态*配置*.dll"
echo [SMART] folder="%TARGET_DIR%" archive_min=%ARCHIVE_MIN_MB%MB deep_min=%DEEP_MIN_MB%MB
if "%CLEANUP_DYNAMIC_LIBS%"=="1" goto smart_cleanup
%PYTHON_COMMAND% -m reorder_engine.beta --workdir "%WORK_DIR%" --folder "%TARGET_DIR%" --archive-mode wide --archive-min-mb %ARCHIVE_MIN_MB% --deep-extract --deep-mode smart --deep-max-depth %DEEP_MAX_DEPTH% --deep-min-archive-mb %DEEP_MIN_MB% --deep-final-single-mb %DEEP_FINAL_SINGLE_MB% --deep-max-candidates %DEEP_MAX_CANDIDATES% --log "%TARGET_DIR%\reorder_engine.smart.log" --tool-log "%TARGET_DIR%\reorder_engine.smart.tools.log"
set "RC=%ERRORLEVEL%"
goto finish
:smart_cleanup
%PYTHON_COMMAND% -m reorder_engine.beta --workdir "%WORK_DIR%" --folder "%TARGET_DIR%" --archive-mode wide --archive-min-mb %ARCHIVE_MIN_MB% --deep-extract --deep-mode smart --deep-max-depth %DEEP_MAX_DEPTH% --deep-min-archive-mb %DEEP_MIN_MB% --deep-final-single-mb %DEEP_FINAL_SINGLE_MB% --deep-max-candidates %DEEP_MAX_CANDIDATES% --cleanup-dynamic-libs --dynamic-lib-patterns "%DYNAMIC_LIB_PATTERNS%" --log "%TARGET_DIR%\reorder_engine.smart.log" --tool-log "%TARGET_DIR%\reorder_engine.smart.tools.log"
set "RC=%ERRORLEVEL%"
goto finish

:prepare
echo [PREPARE] workdir="%WORK_DIR%"
%PYTHON_COMMAND% -m reorder_engine.beta --workdir "%WORK_DIR%" --folder "%WORK_DIR%" --prepare-tools --self-check
set "RC=%ERRORLEVEL%"
goto finish

:finish
echo Done. rc=%RC%
if /i "%MODE%"=="extract" if not "%NO_PAUSE%"=="1" pause
if /i "%MODE%"=="basic" if not "%NO_PAUSE%"=="1" pause
if /i "%MODE%"=="smart" if not "%NO_PAUSE%"=="1" pause
exit /b %RC%

:invalid_workdir
echo [ERROR] Invalid REORDER_WORK_DIR: "%WORK_DIR%"
echo Set REORDER_WORK_DIR to a repository containing pyproject.toml and src\reorder_engine\beta.py.
exit /b 10

:python_unavailable
echo [ERROR] No usable Python 3.10+ with pyzipper and reorder_engine was found.
echo Run setup_windows.bat, or set REORDER_PYTHON / REORDER_CONDA_ENV.
exit /b 12

:select_python
set "PYTHON_COMMAND="
if defined REORDER_PYTHON (
  set "PYTHON_COMMAND="%REORDER_PYTHON%""
  call :probe_python
  if errorlevel 1 exit /b 1
  exit /b 0
)
set "SELECTED_CONDA_ENV=%REORDER_CONDA_ENV%"
if not defined SELECTED_CONDA_ENV set "SELECTED_CONDA_ENV=%CONDA_ENV%"
if defined SELECTED_CONDA_ENV (
  set "PYTHON_COMMAND=call conda run -n "%SELECTED_CONDA_ENV%" --no-capture-output python"
  call :probe_python
  if errorlevel 1 exit /b 1
  exit /b 0
)
if exist "%WORK_DIR%\.venv\Scripts\python.exe" (
  set "PYTHON_COMMAND="%WORK_DIR%\.venv\Scripts\python.exe""
  call :probe_python
  if not errorlevel 1 exit /b 0
)
set "SELECTED_CONDA_ENV=reorder"
call :probe_conda
if not errorlevel 1 exit /b 0
set "SELECTED_CONDA_ENV=unzip"
call :probe_conda
if not errorlevel 1 exit /b 0
set "PYTHON_COMMAND=py -3"
call :probe_python
if not errorlevel 1 exit /b 0
set "PYTHON_COMMAND=python"
call :probe_python
exit /b %ERRORLEVEL%

:probe_conda
set "PYTHON_COMMAND=call conda run -n "%SELECTED_CONDA_ENV%" --no-capture-output python"
call :probe_python
exit /b %ERRORLEVEL%

:probe_python
%PYTHON_COMMAND% -c "import sys, pyzipper, reorder_engine.beta; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
exit /b %ERRORLEVEL%
