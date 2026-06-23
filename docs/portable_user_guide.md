# Windows 便携版用户使用指南

这份指南面向下载 `reorder-extract-windows-*.zip` 的使用者。使用者不需要安装 Python、conda、7-Zip、WinRAR 或 Bandizip。

## 1. 下载

从 GitHub Release 下载 ZIP，链接通常长这样：

```text
https://github.com/WOO-woo-Waf/reorder/releases/download/<tag>/reorder-extract-windows-<version>.zip
```

如果下载后 Windows 提示文件来自互联网，可以在 ZIP 文件属性里解除阻止，或者直接解压后运行。

## 2. 解压

把 ZIP 解压到一个固定目录，例如：

```text
D:\Tools\reorder-extract-windows
```

解压后目录里应该能看到：

```text
reorder-extract.exe
extract_here.bat
target_folder.txt
config.json
README.txt
resources\
tools\
_internal\
```

不要只拿走 `reorder-extract.exe`。必须保留整个目录，尤其是 `_internal\`、`tools\`、`resources\` 和 `config.json`。

## 3. 选择要处理的文件夹

有两种方式。

方式一：处理工具目录本身。

1. 把压缩包放进解压后的工具目录。
2. 保持 `target_folder.txt` 内容为：

```text
.
```

3. 双击 `extract_here.bat`。

方式二：处理指定目录。

1. 打开 `target_folder.txt`。
2. 写入要处理的目录，例如：

```text
D:\DownloadLink\unzip-buff
```

3. 保存文件。
4. 双击 `extract_here.bat`。

也可以直接命令行运行：

```powershell
.\reorder-extract.exe --folder D:\DownloadLink\unzip-buff
```

## 4. 运行后会发生什么

程序会对目标目录里的压缩包执行以下流程：

- 展平子目录里的文件。
- 识别 ZIP、RAR、7Z、分卷、伪装扩展名、部分嵌套压缩包。
- 按密码库逐个尝试密码。
- 调用随包的 7-Zip、RAR、Bandizip 命令行工具解压。
- 尽量递归处理大文件中的嵌套压缩包。
- 把结果整理到目标目录下。

运行日志会显示在窗口里，也会写入目标目录：

```text
reorder_engine.extract.log
reorder_engine.extract.tools.log
```

## 5. 输出目录

目标目录里常见输出：

```text
final\
success\
failed\
intermediate\
_duplicates\
```

含义：

- `final\`: 最终解出的内容，优先看这里。
- `success\archives\`: 已成功处理的原始压缩包。
- `failed\`: 处理失败的文件。
- `intermediate\`: 中间解压目录，排查问题时看。
- `_duplicates\`: 重名或重复文件的临时处理目录。

## 6. 维护密码库

密码库文件：

```text
resources\passwords.txt
```

格式：

```text
# 注释行会忽略
password1
password2
```

规则：

- 一行一个密码。
- 空行会忽略。
- `#` 开头的行会忽略。
- 修改后不需要重新编译，下一次运行会自动读取。

## 7. 维护关键字库

关键字库文件：

```text
resources\keywords.txt
```

格式同样是一行一个条目，支持 `#` 注释。修改后不需要重新编译。

## 8. 常见问题

### 双击后窗口一闪而过

优先用 `extract_here.bat`，不要直接双击 `reorder-extract.exe`。BAT 会在结束后暂停，方便看错误。

### 没有解出文件

检查：

- `target_folder.txt` 是否写对目录。
- 目标目录里是否真的有压缩包。
- `reorder_engine.extract.log`。
- `reorder_engine.extract.tools.log`。

### 密码不对

把新密码追加到：

```text
resources\passwords.txt
```

然后重新运行 `extract_here.bat`。

### 想换一个处理目录

只改：

```text
target_folder.txt
```

不用移动工具目录，也不用重新下载。

## 9. 可以删除什么

运行完成后，如果确认不再排查问题，可以删除目标目录里的：

```text
intermediate\
reorder_engine.extract.log
reorder_engine.extract.tools.log
```

不要删除工具目录里的：

```text
_internal\
tools\
resources\
config.json
reorder-extract.exe
extract_here.bat
```
