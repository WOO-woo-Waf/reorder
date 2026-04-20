@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM 只处理形如 xxx.z.00 / xxx.z.01 这类名字
for %%F in ("*.z.*") do (
    for /f "tokens=1-3 delims=." %%a in ("%%~nxF") do (
        if /I "%%b"=="z" (
            set "raw=%%c"

            REM 如果是 1~2 位数字(00,01,1,9等)，转换成从 001 开始的三位数
            if "!raw:~2,1!"=="" (
                REM 00 -> 001, 01 -> 002, 02 -> 003 ...
                set /a num=1!raw!-99
                set "pad=00!num!"
                set "seq=!pad:~-3!"
            ) else (
                REM 已经是三位及以上，就直接取最后三位做序号
                set "pad=00!raw!"
                set "seq=!pad:~-3!"
            )

            set "newname=%%a.7z.!seq!"

            REM 避免把自己重命名成同名/覆盖已有文件
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
