# 归序（ReOrder Engine）

“归序”是一个面向 **离线下载数据整理** 的自动化流水线：对杂乱的压缩包集合进行 **识别 → 统一命名 →（可选）解密准备 → 调用本地解压工具解压**，并为大量不可预期的文件名/分卷格式提供可扩展的策略体系。

> 你原来叫“解压助手”。这里换成更委婉抽象的名字：**归序**（把混乱归于秩序）。

## 目标（先实现阶段 1 + 2）
- 阶段 1：文件名清洗/重命名（可插拔策略；关键字库来自 `resources/keywords.txt`）
- 阶段 1（可选）：文件准备/解密（先留好策略接口与管线位置）
- 阶段 2：分卷识别与分组 + 调用本地解压工具（7z）解压到目标目录
- 阶段 3：只预留扩展点（例如：输出整理、校验、归档、二次处理），不做具体实现

## 快速运行（骨架可跑）
> 默认不需要第三方 Python 包；建议用 `pip install -e .` 让 `src/` 布局可直接运行。

- 示例：扫描目录、清洗并重命名、按策略尝试解压

```powershell
cd reorder_engine
pip install -e .
python -m reorder_engine --input D:\Downloads\data --output D:\Downloads\out --tool auto --passwords .\resources\passwords.txt
```

参数说明见：`python -m reorder_engine --help`

## 作为 Codex Skill 使用
本仓库自带 repo-local skill：`.agents/skills/reorder-engine/`。别人拿到仓库后，可以把它复制到自己的 Codex skills 目录，让后续智能体按本项目的流程工作：

```powershell
Copy-Item -Recurse .agents\skills\reorder-engine "$env:USERPROFILE\.codex\skills\reorder-engine"
```

之后可以对智能体说：`使用 $reorder-engine 处理这个仓库`。这个 skill 会指引智能体使用 `prepare_extract_tools.bat`、`config.json`、`tools/`、`resources/` 和 `src/reorder_engine/` 里的现有机制，不需要重新摸索项目结构。

如果要生成一个可交付 ZIP 包，运行：

```bat
package_reorder_skill.bat
```

输出为 `dist/reorder-engine-skill.zip`。它包含 repo-local skill、项目源码、测试、资源文件和工具准备脚本；`dist/` 是生成物目录，不建议直接提交 ZIP。

## Beta：把当前目录“尽量解出来”并分流
当你把 [reorder_engine/beta_here.bat](reorder_engine/beta_here.bat) 放到某个下载目录并双击运行时：
- 默认先展平：把子文件夹里的文件移动到当前目录（不递归保留结构）
- 使用 bat 所在目录作为输入目录
- 就地解压到该目录
- 将处理过的文件按结果移动到 `success/` 与 `failed/`
- 若目录下没有 `7z.exe` 且未配置，会自动联网下载 7-Zip 并写入该目录的 `config.json`

失败处理：
- 若某个文件/分卷解压失败，会尽量把该批次文件名回滚成原样，再移动到 `failed/`

排除列表（可配置）：
- Beta 会把“几乎所有文件”当作可解压候选；但会按 `config.json` 的 `beta.exclude.names/exts` 排除控制文件

命令行等价：

```powershell
python -m reorder_engine.beta --folder D:\Downloads\data
```

关闭展平（不移动子目录文件）：

```powershell
python -m reorder_engine.beta --folder D:\Downloads\data --no-flatten
```

## 外部依赖
- Windows 环境（优先）
- 7-Zip（推荐）；归序可在首次运行时自动下载并配置（需要联网）
- 归序会把“调用外部工具”封装成类；你可以在配置里指定可执行文件路径。

### Windows 解压工具准备
已有机制：7z 会按 `config.json -> tools/7zip/ -> PATH -> 联网下载 7-Zip MSI` 的顺序准备。MSI 会被解包到 `tools/7zip/`，实际路径会写回 `config.json` 的 `tools.seven_zip.exe`。

一键准备/探测：

```bat
prepare_extract_tools.bat
```

等价命令：

```powershell
python -m reorder_engine.beta --workdir D:\buff\reorder --folder D:\buff\reorder --prepare-tools --self-check
```

该命令会：
- 递归查找 `tools/7zip/` 下的 `7z.exe`，兼容 MSI 解包后的 `tools/7zip/Files/7-Zip/7z.exe`
- 查找 `tools/rar/`、`tools/bandizip/`，也会读取 `tools/_packages/winrar-cli.zip`、`tools/_packages/bandizip-cli.zip` 并解包到对应目录
- 兜底探测常见安装目录：`D:\RAR`、`D:\bandzip`、`C:\Program Files\WinRAR`、`C:\Program Files\Bandizip`
- 找到后把 `tools.unrar.exe`、`tools.bandizip.exe` 写回 `config.json`

版本兼容性：7-Zip 的 `7z x -y -o<dir> [-pPASSWORD] <archive>` 比较稳定；RAR/UnRAR 的 `x -y -p...` 也相对稳定；Bandizip 的 `bz x -y -o:<dir> -p:<password>` 更依赖具体版本。升级工具后建议跑一次 `prepare_extract_tools.bat` 看 self-check 输出，真实解压失败时看 `reorder_engine.tools.log`。

RAR/Bandizip 打包到 Git 的建议：先确认许可证允许随项目分发；不要提交个人授权文件，例如 `rarreg.key`。如果只是私有仓库自用，可把必要 CLI 文件压成 ZIP 放到 `tools/_packages/`：

```powershell
New-Item -ItemType Directory -Force tools\_packages
Compress-Archive -Force -Path D:\RAR\Rar.exe,D:\RAR\UnRAR.exe,D:\RAR\7zxa.dll,D:\RAR\License.txt,D:\RAR\Rar.txt -DestinationPath tools\_packages\winrar-cli.zip
Compress-Archive -Force -Path D:\bandzip\bz.exe,D:\bandzip\Bandizip.exe,D:\bandzip\ark.x64.dll,D:\bandzip\ark.x64.lgpl.dll,D:\bandzip\data,D:\bandzip\langs -DestinationPath tools\_packages\bandizip-cli.zip
```

后续新机器克隆后运行 `prepare_extract_tools.bat`，会自动解包这些 ZIP 并写入配置。

### 随项目携带工具（可选）
把 `7z.exe`、`Rar.exe`/`UnRAR.exe`、`bz.exe` 放到 [tools/README.md](tools/README.md) 所说的 `tools/` 目录，代码会优先使用这些二进制文件。

### 配置文件
默认使用项目根目录的 [reorder_engine/config.json](reorder_engine/config.json)。首次运行若找不到 `7z.exe`，会尝试联网从 7-zip.org 下载 MSI 并解包到 `tools/7zip/`，然后把路径写回 `config.json`。

### 关键字库与密码库格式（txt）
- 一行一个条目
- 空行会忽略
- 以 `#` 开头的行作为注释忽略
- 建议 UTF-8 编码保存

### 支持的常见格式
底层使用 7-Zip：除 `7z/zip/rar` 外，通常也支持 `tar/gz/bz2/xz/tgz/tar.gz` 等（具体以你安装的 7-Zip 版本为准）。

### 解压器轮询
当 `--tool auto` 时，会使用已配置/已下载的 `7z`，并结合密码库逐个尝试直到成功。

## 目录结构
- `src/reorder_engine/`：核心代码
- `resources/keywords.txt`：关键字库（按行维护）
- `tests/`：预留

更多需求与架构见 [REQUIREMENTS.md](REQUIREMENTS.md)。
