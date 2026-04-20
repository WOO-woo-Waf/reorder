@echo off
setlocal enabledelayedexpansion

rem Prompt user to input the extension to remove
set /p extension=Please enter the extension to remove (without the dot): 

rem Check if the user entered an extension
if "%extension%"=="" (
    echo You did not enter an extension.
    pause
    exit /b
)

rem Iterate through all files in the current directory with the specified extension
for %%f in (*.%extension%) do (
    rem Get the filename without the extension
    set "filename=%%~nf"
    
    rem Check if the file without extension already exists
    if exist "%%filename%%" (
        echo File %%filename%% already exists. Skipping renaming of %%f.
    ) else (
        rem Rename the file to remove the extension
        ren "%%f" "%%~nf"
    )
)

echo All files with the .%extension% extension have had their extension removed.
pause
