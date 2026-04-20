@echo off
setlocal enabledelayedexpansion

rem Prompt user to enter the WinRAR path
set /p rarpath=Please enter the full path to WinRAR (e.g., D:\RAR\WinRAR.exe): 

rem Check if the user entered a valid path
if not exist "%rarpath%" (
    echo Invalid path. Exiting...
    pause
    exit /b
)

rem Prompt user to choose the extraction method
echo Please choose the extraction method:
echo 1. Extract to the current directory
echo 2. Extract to a folder with the same name as the archive
set /p choice=Enter your choice (1 or 2): 

rem Check if the user entered a valid choice
if "%choice%" NEQ "1" if "%choice%" NEQ "2" (
    echo Invalid choice. Exiting...
    pause
    exit /b
)

rem Prompt user to enter the password
set /p password=Please enter the password for the archives: 

rem Function to delete a file with retries
:delete_file
set file_to_delete=%1
set retry_count=0
:retry
if exist "%file_to_delete%" (
    del "%file_to_delete%"
    if exist "%file_to_delete%" (
        set /a retry_count+=1
        if %retry_count% LEQ 5 (
            timeout /t 2 > nul
            goto retry
        ) else (
            echo Failed to delete %file_to_delete%. Another process might be using the file.
        )
    )
)
exit /b

rem Iterate through all archive files in the current directory
for %%f in (*.rar *.zip *.7z) do (
    rem Get the filename without the extension
    set "filename=%%~nf"
    
    rem Extract based on the user's choice
    if "%choice%"=="1" (
        rem Extract to the current directory
        "%rarpath%" x -p%password% -o+ "%%f" "."
    ) else (
        rem Extract to a folder with the same name as the archive
        if not exist "!filename!" mkdir "!filename!"
        "%rarpath%" x -p%password% -o+ "%%f" "!filename!\"
    )

    rem Check if the extraction was successful
    if errorlevel 1 (
        echo Failed to extract %%f. Wrong password or corrupted archive.
    ) else (
        rem Delete the archive if extraction was successful
        call :delete_file "%%f"
    )
)

echo All archives have been processed.
pause
