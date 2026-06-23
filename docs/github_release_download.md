# GitHub Release 下载与使用说明

本文档说明如何把 Windows 便携版 ZIP 发布到 GitHub，并让别人通过公网链接下载使用。

最终使用者下载 ZIP 后的具体操作，见 [Windows 便携版用户使用指南](portable_user_guide.md)。

## 发布方式选择

推荐使用 **GitHub Releases**，不要把编译产物提交到 Git。

原因：

- `dist/` 和 `releases/` 是生成物，体积较大，不适合进源码历史。
- GitHub Release 附件适合放可下载 ZIP。
- Release 会生成稳定下载链接。

公开下载链接格式：

```text
https://github.com/WOO-woo-Waf/reorder/releases/download/<tag>/<zip-name>
```

示例：

```text
https://github.com/WOO-woo-Waf/reorder/releases/download/portable-20260623-163109/reorder-extract-windows-20260623-163109.zip
```

如果仓库是公开仓库，任何人都能通过链接下载。如果仓库是私有仓库，只有有权限的人能下载。

## ZIP 里面包含什么

发布 ZIP 是完整便携包，包含运行所需内容：

- `reorder-extract.exe`
- `_internal/` Python 运行时依赖
- `tools/7zip/`
- `tools/rar/`
- `tools/bandizip/`
- `resources/passwords.txt`
- `resources/keywords.txt`
- `extract_here.bat`
- `target_folder.txt`
- `config.json`
- `README.txt`

使用者不需要安装 Python，不需要安装 conda。

注意：`resources/passwords.txt` 会被包含在 ZIP 中。发布到公网前确认里面没有不能公开的密码或私有信息。

## 生成发布 ZIP

从仓库根目录运行：

```bat
release_windows_dist.bat 20260623-163109
```

或使用 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release_windows_dist.ps1 -Version 20260623-163109
```

输出示例：

```text
releases/reorder-extract-windows-20260623-163109.zip
```

发布脚本会自动执行：

- unittest
- PyInstaller 编译
- 便携包自检
- 真实解压 smoke test
- 生成 ZIP

## 上传到 GitHub Release

### 方法一：网页上传

1. 打开 GitHub 仓库：

```text
https://github.com/WOO-woo-Waf/reorder
```

2. 进入 `Releases`。
3. 点击 `Draft a new release`。
4. 创建 tag，例如：

```text
portable-20260623-163109
```

5. Release title 可写：

```text
Reorder Extract Windows portable-20260623-163109
```

6. 上传 ZIP：

```text
releases/reorder-extract-windows-20260623-163109.zip
```

7. 发布 Release。

发布后，下载链接就是：

```text
https://github.com/WOO-woo-Waf/reorder/releases/download/portable-20260623-163109/reorder-extract-windows-20260623-163109.zip
```

### 方法二：GitHub CLI 上传

先安装 GitHub CLI：

```text
https://cli.github.com/
```

登录：

```powershell
gh auth login
```

生成 ZIP 后运行：

```bat
publish_github_release.bat releases\reorder-extract-windows-20260623-163109.zip portable-20260623-163109
```

或使用 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\publish_github_release.ps1 `
  -ZipPath .\releases\reorder-extract-windows-20260623-163109.zip `
  -Tag portable-20260623-163109
```

如果不传参数，脚本会自动选择 `releases/` 下最新的 `reorder-extract-windows-*.zip`。

## 使用者下载后怎么用

1. 下载 ZIP。
2. 解压到任意目录，例如：

```text
D:\Tools\reorder-extract-windows
```

3. 打开解压后的目录。
4. 如果要处理当前目录，把压缩包放进这个目录，双击：

```text
extract_here.bat
```

5. 如果要处理另一个目录，编辑：

```text
target_folder.txt
```

写入目标目录，例如：

```text
D:\DownloadLink\unzip-buff
```

然后双击：

```text
extract_here.bat
```

6. 解压结果在目标目录中：

```text
final/
success/
failed/
intermediate/
reorder_engine.extract.log
reorder_engine.extract.tools.log
```

7. 后续只需要维护：

```text
resources/passwords.txt
resources/keywords.txt
target_folder.txt
```

## 源码和编译产物的关系

提交到 Git 的内容：

- `src/`
- `tests/`
- `docs/`
- `scripts/`
- `*.bat`
- `config.json`
- `resources/`

不提交到 Git 的内容：

- `build/`
- `dist/`
- `releases/`

编译产物通过 GitHub Release 附件发布，不进入源码仓库。
