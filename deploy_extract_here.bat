@echo off
setlocal DisableDelayedExpansion
call "%~dp0scripts\windows\deploy_extract_here.bat" "%~dp0" %*
exit /b %ERRORLEVEL%
