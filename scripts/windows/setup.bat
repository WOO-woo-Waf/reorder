@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "REPO_DIR=%~f1"
if not defined REPO_DIR for %%I in ("%~dp0..\..") do set "REPO_DIR=%%~fI"
if not exist "%REPO_DIR%\pyproject.toml" (
  echo [ERROR] Repository not found: "%REPO_DIR%"
  exit /b 10
)
if defined REORDER_PYTHON goto explicit_python
set "BASE_PYTHON=py -3"
call :probe_base_python
if not errorlevel 1 goto python_ready
set "BASE_PYTHON=python"
call :probe_base_python
if errorlevel 1 goto missing_python
goto python_ready

:explicit_python
set "BASE_PYTHON="%REORDER_PYTHON%""
call :probe_base_python
if errorlevel 1 goto missing_python

:python_ready
if not exist "%REPO_DIR%\.venv\Scripts\python.exe" (
  %BASE_PYTHON% -m venv "%REPO_DIR%\.venv"
  if errorlevel 1 exit /b 1
)
"%REPO_DIR%\.venv\Scripts\python.exe" -m pip install -e "%REPO_DIR%"
if errorlevel 1 exit /b %ERRORLEVEL%
"%REPO_DIR%\.venv\Scripts\python.exe" -c "import sys, pyzipper, reorder_engine.beta; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 exit /b %ERRORLEVEL%
echo Setup complete. Root BAT files will use this repository's .venv.
exit /b 0

:missing_python
echo [ERROR] Python 3.10+ is required. Install it or set REORDER_PYTHON.
exit /b 12

:probe_base_python
%BASE_PYTHON% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
exit /b %ERRORLEVEL%
