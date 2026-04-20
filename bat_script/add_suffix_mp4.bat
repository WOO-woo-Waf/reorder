@echo off
setlocal enabledelayedexpansion

rem 遍历当前目录下的所有文件
for %%f in (*) do (
    rem 获取文件名
    set "filename=%%~nf"
    set "extension=%%~xf"
    
    rem 如果文件没有后缀名，添加.mp4后缀
    if "!extension!"=="" (
        ren "%%f" "%%f.mp4"
    )
)

echo All files without an extension have been renamed to include .mp4 extension
pause
