@echo off
setlocal enabledelayedexpansion

rem Prompt user to input the extension
set /p extension=Please enter the extension to add (without the dot): 

rem Check if the user entered an extension
if "%extension%"=="" (
    echo You did not enter an extension.
    pause
    exit /b
)

rem Iterate through all files in the current directory
for %%f in (*) do (
    rem Get the filename
    set "filename=%%~nf"
    set "fileextension=%%~xf"
    
    rem If the file has no extension, add the user-specified extension
    if "!fileextension!"=="" (
        ren "%%f" "%%f.%extension%"
    )
)

echo All files without an extension have been renamed to include .%extension% extension.
pause
