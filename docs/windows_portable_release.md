# Windows 便携版使用与发布

本文档说明如何使用、重新编译和发布 `reorder-extract-windows` 便携版。

最终使用者下载 ZIP 后的具体操作，见 [Windows 便携版用户使用指南](portable_user_guide.md)。

如果要把 ZIP 上传到 GitHub Release 并提供公网下载链接，见 [GitHub Release 下载与使用说明](github_release_download.md)。

## 产物结构

构建后产物目录默认位于：

```text
dist/reorder-extract-windows/
```

核心文件：

- `reorder-extract.exe`: 主程序，不要求用户安装 Python 或 conda。
- `_internal/`: PyInstaller 打包的 Python 运行时依赖，必须和 EXE 放在一起。
- `extract_here.bat`: 双击运行入口。
- `target_folder.txt`: 目标解压目录配置。默认 `.`，表示处理本工具目录。
- `config.json`: 便携版配置，默认从产物目录读取 `resources/` 和 `tools/`。
- `resources/passwords.txt`: 密码库，一行一个密码，支持 `#` 注释。
- `resources/keywords.txt`: 关键字库，一行一个条目，支持 `#` 注释。
- `tools/7zip/`: 便携 7-Zip CLI。
- `tools/rar/`: WinRAR/RAR CLI 文件。
- `tools/bandizip/`: Bandizip CLI 文件。

## 使用方式

最简单方式：

1. 把压缩包放进 `dist/reorder-extract-windows/`。
2. 双击 `extract_here.bat`。
3. 结果会写入目标目录下的 `final/`、`success/`、`failed/`、`intermediate/` 等目录。

处理固定目录：

1. 打开 `target_folder.txt`。
2. 写入目标目录，例如：

```text
D:\DownloadLink\unzip-buff
```

3. 双击 `extract_here.bat`。

命令行方式：

```powershell
.\reorder-extract.exe --folder D:\DownloadLink\unzip-buff
```

## 密码库和关键字库维护

只需要维护这两个文件：

```text
resources/passwords.txt
resources/keywords.txt
```

格式规则：

- 一行一个条目。
- 空行会忽略。
- `#` 开头的行会作为注释忽略。
- 建议使用 UTF-8 保存。

重新发布时，构建脚本会把仓库内 `resources/` 下的这两个文件复制到产物目录。

## 本机构建环境

当前 Windows 构建使用 conda 环境：

```powershell
conda run -n reorder python --version
```

首次构建前确认 PyInstaller 已安装在 `reorder` 环境：

```powershell
conda run -n reorder python -m pip install pyinstaller
```

运行测试：

```powershell
$env:PYTHONPATH="$PWD\src"
conda run -n reorder python -m unittest discover -s tests
```

## 重新编译便携目录

从仓库根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows_dist.ps1
```

也可以使用 BAT 包装入口：

```bat
build_windows_dist.bat
build_windows_dist.bat 2026.06.23
```

指定版本号：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows_dist.ps1 -Version 2026.06.23
```

输出：

```text
dist/reorder-extract-windows/
```

构建脚本会：

- 使用 `conda run -n reorder python -m PyInstaller` 打包 `src/reorder_engine/portable.py`。
- 生成 `reorder-extract.exe` 和 `_internal/`。
- 复制 `resources/passwords.txt`、`resources/keywords.txt`。
- 复制最小可运行的 7-Zip、RAR、Bandizip CLI 文件。
- 写入便携版 `config.json`、`extract_here.bat`、`target_folder.txt`、`README.txt`、`VERSION.txt`。

## 一键发布 ZIP

从仓库根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release_windows_dist.ps1 -Version 2026.06.23
```

也可以使用 BAT 包装入口：

```bat
release_windows_dist.bat 2026.06.23
```

输出：

```text
releases/reorder-extract-windows-2026.06.23.zip
```

发布脚本会依次执行：

1. 运行 unittest。
2. 重新构建 `dist/reorder-extract-windows/`。
3. 运行产物自检：

```powershell
.\dist\reorder-extract-windows\reorder-extract.exe --prepare-tools --self-check
```

4. 创建临时 zip 并用产物 EXE 真实解压一次。
5. 生成 `releases/*.zip`。

跳过测试或 smoke test：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release_windows_dist.ps1 -Version 2026.06.23 -SkipTests
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release_windows_dist.ps1 -Version 2026.06.23 -SkipSmoke
```

## 版本发布建议

推荐发布流程：

1. 更新代码。
2. 更新 `resources/passwords.txt` 和 `resources/keywords.txt`。
3. 运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release_windows_dist.ps1 -Version YYYY.MM.DD
```

4. 把 `releases/reorder-extract-windows-YYYY.MM.DD.zip` 交付给使用者。
5. 使用者解压 ZIP 后只维护：

```text
target_folder.txt
resources/passwords.txt
resources/keywords.txt
```

上传到 GitHub Release 后，公网下载链接形如：

```text
https://github.com/WOO-woo-Waf/reorder/releases/download/<tag>/<zip-name>
```

## 注意事项

- 不要只复制 `reorder-extract.exe`，必须复制整个产物目录。
- 不要删除 `_internal/`、`tools/`、`config.json`。
- 7-Zip、RAR、Bandizip 是外部工具。对外分发前确认许可证允许分发对应文件。
- 不要把个人授权文件打包或提交，例如 WinRAR 的 `rarreg.key`。
- `dist/`、`build/`、`releases/` 都是生成物目录，不建议提交到 Git。
