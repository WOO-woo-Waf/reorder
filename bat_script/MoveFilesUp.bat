@echo off
setlocal enabledelayedexpansion

REM Get the current directory
set "current_dir=%cd%"

REM Iterate over all folders in the current directory
for /d %%d in ("%current_dir%\*") do (
    REM Iterate over all files in the folder
    for %%f in ("%%d\*") do (
        REM Move files to the current directory
        move "%%f" "%current_dir%"
    )
)

echo All files have been successfully moved up one level.
pause

echo Remove all empty folders.

REM Remove all empty folders
for /d %%d in ("%current_dir%\*") do (
    rd "%%d"
)
pause

