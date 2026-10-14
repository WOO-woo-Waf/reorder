# 星绫解封 · Hoshiribbon

> 把压缩的次元，一层层展开。

面向二次元收藏者的本地桌面整理工具：将下载的资源按文件组识别，直接读取选中原包，优先原地调整后缀与还原；成品发布后同盘独占移动原包归档，跨盘保留原包位置。支持普通压缩包、分卷和部分伪装/嵌入格式；具体方式、效果和限制见 [格式支持指南](docs/desktop-format-support.md)。手动批处理优先，文件在本机处理。

## Windows 桌面 0.3.3（磁盘读写优先，便携预发布）

0.3.3 进一步减少磁盘读写：普通包直接读取，优先原地后缀调整，失败且没有可用结果才做原地 Apate / 嵌入封面 / restoreAB 还原；成品与临时数据都在所选工作文件夹。**同盘原包用独占移动归档，跨盘原包保留当前位置并登记结果，不再整包复制**；桌面 restoreAB 走原地修复，**任何恢复都不生成整包副本**。解压报错但只要本次已解出至少一个非空文件，就保留结果并停止继续尝试密码/工具与深挖，状态显示“完成，有警告”（内容可能损坏或缺失，需人工核对）。随包 7-Zip 由 25.01 受控升级到 26.04（固定目录 `tools/7zip/26.04/`，25.01 历史保留），UnRAR 7.13.0 与 Bandizip 7.40.0.1 不变。公开密码仍是单一明文本 `passwords.txt`（首次播种 115 条，可编辑、删除或清空，清空并重启不补回），界面多行框不再用黑点遮罩，日志保留原文。

已发布：[v0.3.3 Release](https://github.com/WOO-woo-Waf/reorder/releases/tag/v0.3.3) · [便携 ZIP](https://github.com/WOO-woo-Waf/reorder/releases/download/v0.3.3/Hoshiribbon-0.3.3-windows-x64.zip) · [SHA-256](https://github.com/WOO-woo-Waf/reorder/releases/download/v0.3.3/SHA256SUMS.txt)。解压整个目录，运行 `Start-Portable.cmd`；本版不提供新安装器。完整方式与限制见 [格式支持指南](docs/desktop-format-support.md)、[用户指南](docs/desktop-user-guide.md)、[工具来源](docs/desktop-tool-research.md)、[实施状态](docs/desktop-status.md)。

## Windows 桌面 0.3.2（历史预发布）

0.3.2 取消开始处理前的整包副本：普通包直接读取，必要的伪装还原和后缀改名沿用旧版原地方式。解压、恢复派生产物和工具临时数据全在指定工作文件夹，同盘归档与成品发布优先移动；跨盘归档**当时**仍需校验复制（0.3.3 起改为保留原包位置）。文件夹、原名保留与嵌套规则继续复用旧管线。已发布：[v0.3.2 Release](https://github.com/WOO-woo-Waf/reorder/releases/tag/v0.3.2) · [便携 ZIP](https://github.com/WOO-woo-Waf/reorder/releases/download/v0.3.2/Hoshiribbon-0.3.2-windows-x64.zip) · [SHA-256](https://github.com/WOO-woo-Waf/reorder/releases/download/v0.3.2/SHA256SUMS.txt)。解压到新目录，运行 `Start-Portable.cmd`。

## Windows 桌面 0.3.1（历史预发布）

用户于 2026-10-10 确认当前修复并授权发布 **0.3.1 Windows x64 便携版**。下载：[Release 页面](https://github.com/WOO-woo-Waf/reorder/releases/tag/v0.3.1) · [便携 ZIP](https://github.com/WOO-woo-Waf/reorder/releases/download/v0.3.1/Hoshiribbon-0.3.1-windows-x64.zip) · [SHA-256 校验和](https://github.com/WOO-woo-Waf/reorder/releases/download/v0.3.1/SHA256SUMS.txt)。解压到新目录后运行 `Start-Portable.cmd`；本版不提供新版安装器。

已发布 Windows x64 **预发布（0.3.0）**：[Release 页面](https://github.com/WOO-woo-Waf/reorder/releases/tag/v0.3.0) · [安装包](https://github.com/WOO-woo-Waf/reorder/releases/download/v0.3.0/Hoshiribbon_0.3.0_x64-setup.exe) · [便携 ZIP](https://github.com/WOO-woo-Waf/reorder/releases/download/v0.3.0/Hoshiribbon-0.3.0-windows-x64.zip) · [SHA-256 校验和](https://github.com/WOO-woo-Waf/reorder/releases/download/v0.3.0/SHA256SUMS.txt)。

0.3.1 的更新要点：

- **公开密码库改为一个可直接编辑的明文文件**：首次运行在数据目录生成 `passwords.txt`，写入随包的 **115 条公开密码**；之后完全由你维护——可查看、添加、编辑、删除或清空。界面的“从文件追加”是**追加**，“保存密码列表”才整体替换；空行忽略，重复、空格和 `#` 都按字面内容保留，不去重、也不当作注释。
- **不再使用系统凭据后端，也不加密或脱敏**：密码文件是明文，日志不再把密码替换成 `[密码已隐藏]`。归档密码以公开数据处理，可直接查看和编辑。
- **“工作文件夹”统一承载输入输出与中间目录**：所选文件夹下直接放 `final\`、`success\archives\`、`error_files\`、`deferred_volumes\`，副本、恢复与解压的临时工作区也在 `intermediate\` 下；解压工具进程的 TEMP/TMP/TMPDIR 与当前目录同样落在这个文件夹内。软件复用已存在的同名目录，但从不覆盖已有文件，冲突项安全改名或进入 `_duplicates`。
- **伪装分卷已补齐还原路径**：逐卷还原 Apate 伪装，并对完整卷组预检；授权的 BG57 两卷副本已在 Windows 解压成功，两个原件哈希不变。其他样本效果仍由你人工验收。
- **工作文件夹会被记住**：选择一次后写入设置，重启后自动恢复。旧数据目录 `work\` 里的历史内容按只读遗留保留，供人工恢复，软件不再往那里写入。
- 自带 Python 引擎、**7-Zip 25.01、UnRAR 7.13、Bandizip CLI 7.40.0.1**，无需另装 Python 或解压工具。Bandizip 按所有者已取得书面分发许可的声明纳入固定依赖，包含所需 DLL 和公开许可说明。
- 内置公开库的 **8 条关键词**：关键词仅清理成品顶层名称，**默认关闭**。
- 星夜少女背景、浅紫粉主题，可选择本地图片并调整遮罩、模糊或关闭背景；素材与来源见 [视觉说明](docs/desktop-artwork.md)。
- 用户：[操作与人工验收指南](docs/desktop-user-guide.md)、[实际交付与机器检查记录](docs/desktop-status.md)、[第三方组件](docs/desktop-third-party.md)。
- 制作方：[代码阅读指南](docs/desktop-code-guide.md)、[语言指南](docs/desktop-language-guide.md)、[面向对象架构图](docs/desktop-design.md)。
- 构建：`scripts/build_desktop_windows.ps1`；Tauri 2 + Svelte 5 + TypeScript + Vite、Rust 宿主、Python 引擎、SQLite。桌面端包裹现有引擎策略，不缩减旧管线的分组、成品命名与嵌套整理能力。Windows x64 首交付。

运行需要 Windows 10/11 x64 与系统 WebView2，程序尚未签名。用户已确认本轮修复并同意发布；其他真实资源、干净机器启动、真实跨盘与大盘性能仍需按需验收，工程检查范围与限制在实施状态中列明。跨平台路线保持，浏览器、监听和 Agent 留后续。

## 保留的 ReOrder Engine / CLI

内部包名 `reorder_engine` 与应用 identifier `io.reorder.desktop` 保留。**旧 BAT 仍是正式入口**，与桌面复用解压、分卷、后缀、还原和结果保留策略。2026-10-14 的源码更新补齐异机路径与原地 restoreAB；已发布的桌面 0.3.3 ZIP 不随 Git 更新。

### 在另一台 Windows 机器运行旧 BAT

完整拉取或解压仓库，保留 `src/`、`scripts/`、`resources/`、`tools/`。安装 Python **3.10 或更新版本**后，在仓库目录执行一次：

```bat
setup_windows.bat
prepare_extract_tools.bat
```

`setup_windows.bat` 创建仓库自己的 `.venv` 并安装 Python 核心依赖；运行 BAT 不需要 Node、Rust 或桌面构建。工具准备使用 Git 中固定的 **7-Zip 26.04 / UnRAR 7.13 / Bandizip CLI 7.40.0.1**，不用在新机器另装它们。初次安装 Python 依赖需要网络，准备固定工具可离线进行。

如果 `D:\Downloaded Link\unzip-buff` 就是完整仓库，可以直接运行：

```bat
"D:\Downloaded Link\unzip-buff\extract_here.bat"
```

也可以指定待处理目录：

```bat
extract_here.bat "D:\Downloaded Link\待处理"
```

如果该目录只想放一个 BAT，而程序仓库在别处，在**程序仓库**中执行：

```bat
deploy_extract_here.bat "D:\Downloaded Link\unzip-buff"
```

这会生成绑定当前仓库的外部入口；双击外部 `extract_here.bat` 默认处理它所在目录。换机器或移动程序仓库后重新部署一次。已有入口不应被静默覆盖。也可将根入口复制过去，并先设置 `REORDER_WORK_DIR` 为完整仓库路径。

| 入口 | 实际行为 |
| --- | --- |
| `extract_here.bat` | 默认处理 BAT 所在目录；深解压、保留内容原名 |
| `beta_here.bat` / `run_in_this_folder.bat` | 原 Beta 入口，先探测工具；嵌套选项读取配置 |
| `beta_basic_2000mb.bat` | basic；通用候选与内层阈值 2000 MB，深度 4，每层最多 1 个候选 |
| `beta_smart_identify.bat` | smart；默认阈值 128 / 128 / 512 MB，深度 6，每层最多 3 个候选；保留原环境变量覆盖 |
| `prepare_extract_tools.bat` | 仅准备和探测工具，不处理目录里的压缩包 |

basic/smart 的大小阈值用于通用未知文件和嵌套候选筛选，**不表示已识别的小压缩包一律跳过**。候选上限约束每层选择，不改变单个包的必要后缀、工具与密码尝试顺序。`--archive-mode` 当前只支持 `wide`；动态库清理默认关闭，开启时只作用于本次完整解出的 DLL，部分结果保留时不清理。

路径含空格、中文时给参数加双引号。程序默认取入口自己的仓库，`REORDER_WORK_DIR` 可显式指定仓库，失效时明确报错；不会自动改用 `D:\buff\reorder` 的旧代码。Python 可通过 `REORDER_PYTHON` 指定；兼容 `REORDER_CONDA_ENV` / `CONDA_ENV`，默认优先复用仓库 `.venv`。自动调用时设 `NO_PAUSE=1`；BAT 保留引擎真实退出码。

## 目标（先实现阶段 1 + 2）
- 阶段 1：文件名清洗/重命名（可插拔策略；关键字库来自 `resources/keywords.txt`）
- 阶段 1（可选）：文件准备/解密（先留好策略接口与管线位置）
- 阶段 2：分卷识别与分组 + 调用本地解压工具（7z）解压到目标目录
- 阶段 3：只预留扩展点（例如：输出整理、校验、归档、二次处理），不做具体实现

## 快速运行（骨架可跑）
安装项目依赖后，建议用 `pip install -e .` 让 `src/` 布局可直接运行。

- 示例：扫描目录、清洗并重命名、按策略尝试解压

```powershell
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

`--folder` 是输入和结果的工作文件夹，`--workdir` 是程序、配置和词库的仓库目录。两个参数可以不同，BAT 会正确传递它们。

- 先按配置决定是否展平输入：旧 CLI 保留该能力，桌面不预展平。源码仓库默认有展平保护；关闭展平用 `--no-flatten`。
- 对原包直接读取；没有可用结果才尝试原地后缀、必要的 Apate / 嵌入封面 / restoreAB 还原。
- 解压到目标内 `intermediate\`，成品通过同盘移动发布到 `final\`；原包归 `success\archives\`，失败归 `error_files\<分类>\`，缺卷归 `deferred_volumes\`。
- 工具的 TEMP/TMP/TMPDIR 和当前目录在目标内专用临时目录，不预复制整包。已有目录复用，已有文件冲突不覆盖。
- 非零退出但至少有一个非空普通文件：保留结果、报告警告，停止密码/工具重试和继续深挖。空文件不算结果；取消、超时和严重写盘错误不能冒充成功。
- 原包可能因必要还原被改写；失败时尽力回滚改名和可逆变换，无备份时无法承诺原始字节可恢复。

直接调用引擎的例子：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m reorder_engine.beta --workdir "$PWD" --folder "D:\Downloads\data" --no-flatten --deep-extract
```

控制文件按 `config.json` 中 `beta.exclude.names/exts` 排除。CLI 成品、原件和工具日志留在输入工作文件夹，不使用桌面任务数据库。

### restoreAB 合并文件

项目保留合并文件的 Python 恢复策略：文件前面可能是 PDF、图片或媒体内容，后面嵌入 ZIP、RAR 或 7z。先尝试直接读取和后缀，再按识别结果选择对应还原；2026-10-14 起 BAT/Beta 也使用原地 restoreAB，不生成整包恢复副本。ZIP32 偏移会修复；ZIP64、不一致或超限布局拒绝写入。AES ZIP 识别使用 `pyzipper`，密码来自当前可编辑词库。

`resources/restoreAB.exe` 仅保留为历史手工 GUI 工具；自动管线使用 Python 实现，不启动该 GUI。

## 外部依赖

- Windows 优先，Python ≥3.10，核心依赖 `pyzipper`；见上方 `setup_windows.bat`。
- 默认固定工具来自 Git：7-Zip 26.04、UnRAR 7.13、Bandizip CLI 7.40.0.1。许可、版本和哈希见 [第三方说明](docs/desktop-third-party.md) 与两份 `scripts/desktop-*.lock.json` 工具锁。
- 显式且有效的外部工具路径仍优先；未配置时验证并准备固定依赖。历史自动写回的标准 `tools/7zip/7z.exe` 或 `tools/7zip/Files/7-Zip/7z.exe` 会迁移到锁定的 26.04；固定路径每次重验。存在正式工具锁但依赖损坏时明确报错，不启动损坏依赖、不先展平输入，也不偷偷用宿主旧版本。
- 不含工具锁的旧部署仍兼容 `tools/`、`tools/_packages/`、宿主安装与 PATH 回退；7-Zip 自动下载由配置控制。

### Windows 解压工具准备

```bat
prepare_extract_tools.bat
```

该命令仅准备工具并显示版本。7-Zip 使用锁定目录；UnRAR / Bandizip 的已锁 ZIP 离线展开到 `runtime\cli-tools\tools\`，包含运行 DLL 与许可。工具路径按仓库相对路径写回配置，换机器不依赖开发机安装目录；原有显式路径无效时重新发现。

默认工具顺序是 `7z → unrar → bandizip`；格式识别可以让 RAR 优先 UnRAR、某些后缀优先 Bandizip，`beta.extractor_order` 继续可配置。工具选择和恢复模式是两套顺序，换工具不会先复制整个原包。实际失败查看目标目录下对应主日志与 `.tools.log`。

### 配置文件

CLI 默认使用仓库 `config.json`，相对路径相对 `--workdir` 解析。默认词库为 `resources/passwords.txt` 和 `resources/keywords.txt`，可以直接修改或清空，不自动补回。已有个人配置保留；异机的自定义外部词库路径需要同步文件或改为新机器上的路径。工具发现可能写回配置；运行后 `git status` 中的配置变化不应覆盖你自己的设置。

### 关键字库与密码库格式（txt）
- 一行一个条目
- 空行会忽略
- 以 `#` 开头的行作为注释忽略
- 建议 UTF-8 编码保存

> 这一节描述的是 **CLI/Beta** 读取的关键字与密码文件。桌面版 0.3.1 的公开密码文件是另一套规则：它只是“每行一个密码”的明文本，`#` 和空格都按字面内容保留，不去重、也不当注释；细节见 [用户指南第 9 节](docs/desktop-user-guide.md)。

### 支持的常见格式
底层使用 7-Zip：除 `7z/zip/rar` 外，通常也支持 `tar/gz/bz2/xz/tgz/tar.gz` 等（具体以你安装的 7-Zip 版本为准）。

### 解压器轮询
当 `--tool auto` 时，会使用已配置/已下载的 `7z`，并结合密码库逐个尝试直到成功。

## 目录结构
- `src/reorder_engine/`：核心代码
- `resources/keywords.txt`：关键字库（按行维护）
- `tests/`：预留

更多需求与架构见 [REQUIREMENTS.md](REQUIREMENTS.md)。
