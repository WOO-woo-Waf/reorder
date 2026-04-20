
setlocal enabledelayedexpansion

:: 设置解压码
set password=2333

:: 遍历当前目录中的所有文件
for %%f in (*.*) do (
    :: 获取文件名（不包括扩展名）
    set "filename=%%~nf"
    :: 获取扩展名
    set "extension=%%~xf"
    
    :: 如果文件扩展名为.PDF，则重命名文件
    if /i "!extension!"==".PDF" (
        ren "%%f" "!filename!"
    )
)

echo 所有文件名中的.PDF后缀已移除。


