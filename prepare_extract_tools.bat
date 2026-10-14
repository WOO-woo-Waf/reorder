@echo off
setlocal DisableDelayedExpansion
set "LAUNCHER_DIR=%REORDER_WORK_DIR%"
if not defined LAUNCHER_DIR set "LAUNCHER_DIR=%~dp0"
if not exist "%LAUNCHER_DIR%\scripts\windows\launcher.bat" goto invalid_workdir
"%LAUNCHER_DIR%\scripts\windows\launcher.bat" prepare "%~f0"
exit /b %ERRORLEVEL%
:invalid_workdir
echo [ERROR] Invalid REORDER_WORK_DIR or launcher not found: "%LAUNCHER_DIR%"
exit /b 10
