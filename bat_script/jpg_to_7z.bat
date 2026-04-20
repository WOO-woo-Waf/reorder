@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

rem 处理 .jpg 文件
for /r %%f in (*.jpg) do (
    set "name=%%~nf"
    set "folder=%%~dpf"
    set "oldname=%%~nxf"
    set "newname=!name!.7z"

    pushd "!folder!"
    echo [JPG] 正在重命名: "!oldname!" → "!newname!"
    ren "!oldname!" "!newname!"
    popd
)

rem 处理 .pdf 文件
for /r %%f in (*.pdf) do (
    set "name=%%~nf"
    set "folder=%%~dpf"
    set "oldname=%%~nxf"
    set "newname=!name!.7z"

    pushd "!folder!"
    echo [PDF] 正在重命名: "!oldname!" → "!newname!"
    ren "!oldname!" "!newname!"
    popd
)

echo 所有 .jpg 和 .pdf 文件已重命名为 .7z！
pause
