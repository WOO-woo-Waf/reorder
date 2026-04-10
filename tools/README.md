# tools 目录（可选）

把你要“随项目携带”的解压/还原工具放在这里，代码会优先从该目录加载。

建议文件名（Windows）：
- `tools/7zip/7z.exe`（推荐）

## Apate 伪装还原（解密准备）

若文件经 [Apate](https://github.com/rippod/apate) 伪装（例如实为压缩包却带图片头），可先用本目录脚本还原再解压：

- **脚本**：[`apate.py`](apate.py)
- **说明**：[`APATE.md`](APATE.md)（算法说明、上游仓库链接、`python tools/apate.py <文件>` 用法）

注意：
- `7z` 通常来自 7-Zip；若本机和项目内都找不到，归序会尝试联网下载并解包到 `tools/7zip/`。
- 若你不想随项目携带，也可以把 7-Zip 放进 PATH，代码同样能探测到。
