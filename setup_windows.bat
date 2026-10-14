@echo off
setlocal DisableDelayedExpansion
call "%~dp0scripts\windows\setup.bat" "%~dp0"
exit /b %ERRORLEVEL%
