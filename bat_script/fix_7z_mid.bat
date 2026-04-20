@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM 扫描当前目录中名字里第二段为 z 的文件：形如 xxx.z.001 / xxx.z.00
for %%F in ("*.z.*") do (
    for /f "tokens=1-3 delims=." %%a in ("%%~nxF") do (
        REM 只处理第二段确实是 z 的
        if /I "%%b"=="z" (
            set "newname=%%a.7z.%%c"

            REM 避免重命名到自己或覆盖已有文件
            if /I not "%%~nxF"=="!newname!" (
                if not exist "%%~dpF!newname!" (
                    echo REN "%%~nxF"  "!newname!"
                    ren "%%F" "!newname!"
                ) else (
                    echo 目標文件已存在，跳過："%%~nxF"
                )
            )
        )
    )
)

endlocal
echo 完成。
pause
