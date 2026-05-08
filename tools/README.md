# tools 目录（可选）

把你要“随项目携带”的解压/还原工具放在这里，代码会优先从该目录加载。

建议文件名（Windows）：
- `tools/7zip/7z.exe` 或 `tools/7zip/Files/7-Zip/7z.exe`（推荐）
- `tools/rar/Rar.exe` 或 `tools/rar/UnRAR.exe`
- `tools/bandizip/bz.exe`

也可以把私有自用的工具包放到：
- `tools/_packages/winrar-cli.zip`
- `tools/_packages/bandizip-cli.zip`

然后运行项目根目录的 `prepare_extract_tools.bat`。脚本会把 ZIP 解包到 `tools/rar/`、`tools/bandizip/`，并把找到的 EXE 路径写回 `config.json`。

注意：WinRAR/RAR 与 Bandizip 是第三方软件。提交 ZIP 到 Git 前请确认许可证允许分发，不要提交个人授权文件，例如 `rarreg.key`。

## Apate 伪装还原（解密准备）

若文件经 [Apate](https://github.com/rippod/apate) 伪装（例如实为压缩包却带图片头），可先用本目录脚本还原再解压：

- **脚本**：[`apate.py`](apate.py)
- **说明**：[`APATE.md`](APATE.md)（算法说明、上游仓库链接、`python tools/apate.py <文件>` 用法）

注意：
- `7z` 通常来自 7-Zip；若本机和项目内都找不到，归序会尝试联网下载并解包到 `tools/7zip/`。
- 若你不想随项目携带，也可以把 7-Zip 放进 PATH，代码同样能探测到。
