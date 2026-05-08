@echo off
setlocal

chcp 65001 >nul

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\package_reorder_skill.ps1" -RepoRoot "%ROOT%"
exit /b %ERRORLEVEL%
