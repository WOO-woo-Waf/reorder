@echo off
setlocal enabledelayedexpansion

REM 函数：删除空文件夹
:delEmptyFolders
for /d %%d in (*) do (
    pushd "%%d"
    call :delEmptyFolders
    popd
    REM 检查文件夹是否为空，如果为空则删除
    if not "%%~fd"=="%cd%" (
        rd "%%d" 2>nul
        if errorlevel 1 (
            echo 删除文件夹 %%d 失败，可能是因为文件夹不是空的
        ) else (
            echo 已删除空文件夹 %%d
        )
    )
)
exit /b

REM 调用函数
call :delEmptyFolders
