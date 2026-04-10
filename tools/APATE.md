# Apate 伪装文件还原（`apate.py`）

## 作用

部分下载资源会使用 **[Apate](https://github.com/rippod/apate)** 做「格式伪装」：在文件**开头**换上图片/视频等「面具」文件头，把**真实文件头**挪到文件**末尾附近并逆序存储**，再在**最后 4 字节**写入面具头长度（小端 `uint32`）。常见现象是扩展名看起来像 `.jpg` / `.png`，实际内层是压缩包等。

本目录下的 **`apate.py`** 提供与 Apate 官方 **`Program.Reveal`** 等价的 **Python 就地还原**：去掉尾部附加数据，并把文件开头恢复为原始头，便于后续用 7-Zip 等工具正常打开。

## 上游项目（算法来源）

| 项目 | 说明 |
|------|------|
| [rippod/apate](https://github.com/rippod/apate) | 开源「文件格式伪装」工具（.NET / Windows GUI）。 |
| 对应实现 | [`apate/Program.cs`](https://github.com/rippod/apate/blob/main/apate/Program.cs) 中的 **`Reveal`**、以及伪装时配套的 **`Disguise`** / **`ReverseByteArray`** / `maskLengthIndicatorLength`（末尾 4 字节长度标记）。 |

`apate_official_reveal()` 即按上述 `Reveal` 的**常规分支**（面具长度合理时）用 Python 复刻，便于在归序流水线或脚本里调用，无需安装 Apate GUI。

## 使用方式

### 命令行（推荐）

在项目根目录执行：

```powershell
python tools/apate.py "D:\path\to\disguised.zip.jpg"
```

成功时退出码为 `0`，失败为 `1`。**默认直接改写原文件**，请先备份。

### 在其它 Python 代码里调用

`apate.py` 不是已安装的包模块，推荐用**子进程**调用（与工作目录无关、行为与手动运行一致）：

```python
import subprocess
import sys
from pathlib import Path

repo = Path(r"D:\buff\reorder_out_of_order")  # 改为你的仓库根目录
script = repo / "tools" / "apate.py"
target = Path(r"D:\path\to\file.zip.jpg")
r = subprocess.run([sys.executable, str(script), str(target)], cwd=repo)
ok = r.returncode == 0
```

若必须在同进程内调用，可用 `importlib.util.spec_from_file_location` 按路径加载 `tools/apate.py` 后执行其中的 `apate_official_reveal`。

## 算法概要（与上游一致）

1. 读取文件**最后 4 字节**，小端解析为 `mask_head_length`（面具头字节数）。
2. 在 `file_size - 4 - mask_head_length` 处读取长度为 `mask_head_length` 的一段字节（即**被逆序存放的原始文件头**）。
3. 将该段字节**再逆序**，得到真实原始头。
4. 将文件截断到 `file_size - 4 - mask_head_length`，去掉尾部补丁。
5. 在文件偏移 `0` 处写入还原后的原始头。

## 与归序（reorder_engine）的关系

- 当前 **`78780c5` 主线代码**里的 Beta/解密管线**尚未**自动调用本脚本；若你希望「解压前自动尝试 Apate 还原」，需要在 `DecryptorStrategy` / 管线中增加一步：对命中特征的候选文件调用 `apate_official_reveal` 后再交给 7z。
- 在此之前，可用手动或批处理对可疑文件先运行 `tools/apate.py`，再跑归序解压。

## 风险与合规

- **原地修改文件**：务必先复制备份；错误输入可能导致文件不可用。
- Apate 用于**绕过格式检测**的场景可能涉及平台规则与法律风险；请仅在**有权处理的数据**上使用，并遵守仓库许可证与当地法规。

## 参考链接

- Apate 仓库：<https://github.com/rippod/apate>
- `Reveal` 源码：<https://github.com/rippod/apate/blob/main/apate/Program.cs>
