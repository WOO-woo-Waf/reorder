# Beta 脚本使用说明（重要：危险操作提示）

本说明对应入口：`python -m reorder_engine.beta`（以及仓库根目录的 `beta_here.bat` 启动器）。

新增两套快捷脚本：
- `beta_basic_2000mb.bat`：基础版（2000MB 阈值 + basic 深解模式，严格避免把小文件重复当压缩包）
- `beta_smart_identify.bat`：智能识别版（smart 深解模式，可调阈值/候选数，带失败回原兜底）

目标：你把脚本指向一个**测试用的下载目录**，它会把目录里的压缩包/分卷尽量识别出来，自动轮询多个解压器与密码，尽可能“全解出来”，并把结果按成功/失败分流。

> 强烈建议：只在“专门的测试目录”里运行。不要在工作区、项目目录、重要资料目录运行。

---

## 1. 这个脚本会做什么（按阶段）

### 阶段 A：可选展平（flatten）
- 默认行为：把子文件夹内的文件移动到目标目录根部（便于统一尝试解压）。
- 触发条件：`config.json -> beta.flatten.enabled=true` 且未传 `--no-flatten`。
- 记录：会在日志里出现 `FLATTEN:`。

### 阶段 B：扫描并分组（SCAN / GROUP）
- 扫描目标目录下的文件（默认把“几乎所有文件”当成可解压候选，只排除 `beta.exclude` 配置的控制文件）。
- 分卷分组：支持常见分卷模式，并支持伪后缀（例如 `a.7z.001.pdf` 会按 `a.7z.001` 逻辑分组）。
- 记录：会输出 `SCAN:`，并在后续对每组输出 `EXTRACT[...]`。

### 阶段 C：预处理（只针对“压缩包候选文件”）
为了提升可解压性，会对“疑似压缩包文件名”做两类操作：
- **伪后缀截断**：例如 `a.7z.001.pdf` → `a.7z.001`、`a.part01.rar.txt` → `a.part01.rar`。
- **轻量清洗（仅 base 部分）**：只清洗“可读 base”，保留 `.part01/.7z.001/.z01/.r00` 等技术尾巴，避免破坏分卷规则。

> 注意：此阶段不会改普通 payload 文件名（例如图片/视频/PDF），只会动“压缩包候选文件”。

### 阶段 D：解压（多工具 × 多密码轮询）
- 解压器优先级：通常先 7-Zip，然后（若配置）UnRAR/WinRAR CLI、Bandizip。
- Bandizip 默认：**启用**（只要 `tools.bandizip.exe` 能找到/存在）。如担心 Bandizip 弹 GUI 密码框，可用 `--disable-bandizip` 或设置环境变量 `DISABLE_BANDIZIP=1`。
- 密码轮询：先尝试无密码，再按密码列表逐个尝试，直到成功或判定无需继续（例如缺分卷）。
- 记录：
  - 主流程：`PIPE: EXTRACT[OK|FAIL] entry=... tool=...`
  - 外部工具完整输出：`TOOL: ...`（会在终端实时滚动，也会写到 `reorder_engine.tools.log`）

### 阶段 E：深度解压（可选）
- 作用：对第一层解压产物继续递归寻找“内部压缩包”，直到满足“像最终产物”的判据或达到最大层数。
- 开关：`config.json -> beta.deep_extract.enabled=true`。
- 记录：`DEEP-EXTRACT[...]`、`DEEP[OK|FAIL]`。

### 阶段 F：归档与分流（MOVE / FINAL / FAILED）
- 成功：把原始压缩包/分卷移动到 `success/archives/`。
- 失败：把该组移动到 `failed/`（缺分卷例外：会保留在原地，方便补齐后重试）。
- 深度解压启用时：最终产物会移动到 `final/<package>/`。

**关键保证：最终产物不改名**
- 解压出来的 payload **文件名/目录名**保持原样；如果出现同名冲突，会被分流到 `.../_duplicates/...` 目录下，不会用 `(1)` 这种方式改名。
- `final/` 目录下的“展平（FLATTEN）”在发生目录冲突时也会按同样策略分流到 `_duplicates`，而不是改目录名。

---

## 2. 危险操作清单（务必阅读）

以下操作都可能造成“目录结构变化 / 文件移动 / 文件名变化 / 写入配置”，务必只在测试目录执行：

1) **展平（flatten）**
- 会把子目录文件移动到根目录；可能改变你原有目录结构。
- 建议：第一次运行用 `--no-flatten` 或 `--dry-run` 先观察。

2) **就地重命名（只对压缩包候选文件）**
- 为了让压缩包更容易被识别/解压，会对压缩包文件名做“伪后缀截断/清洗”。
- 失败时会尽量回滚，但仍然属于高风险操作。

3) **文件移动（success/failed/intermediate/final）**
- 成功/失败都会移动文件到不同目录。

4) **自动下载 7-Zip（如果启用且未配置）**
- 会联网下载并写入 `tools/7zip/`，并写回 `config.json`。

5) **`--self-check`（强危险，不建议默认启用）**
- 会执行外部工具命令以探测版本/输出。
- 还可能在配置为空时把探测到的 exe 路径写回 `config.json`。

仓库内两个 bat（`beta_here.bat` / `run_in_this_folder.bat`）已经改为：默认不执行 `--self-check`，只有你显式 `set SELF_CHECK=1` 才会启用。

---

## 3. 日志：你会在终端看到什么

Beta 采用“双日志通道”：
- **主日志**（结构化、偏短）：
  - 默认文件：`<目标目录>/reorder_engine.log`
  - 同时输出到当前终端
  - 前缀示例：`PIPE: SCAN ...`、`PIPE: EXTRACT[OK] ...`、`PIPE: MOVE ...`

- **工具日志**（外部工具完整输出）：
  - 默认文件：`<目标目录>/reorder_engine.tools.log`
  - 同时输出到当前终端（实时滚动）
  - 前缀示例：`TOOL: ...`

bat 启动器说明：
- `beta_here.bat` / `run_in_this_folder.bat` 现在默认**不重定向** stdout/stderr，所以会在窗口里实时打印日志。
- 为了防止双击后窗口一闪而过，bat 默认会 `pause`；如不想暂停可在运行前设置 `set NO_PAUSE=1`。

---

## 4. 运行方式（推荐顺序）

### 4.1 先 dry-run 看计划（最安全）
```powershell
python -m reorder_engine.beta --folder "D:\TEST\downloads" --workdir "D:\buff\reorder" --dry-run --no-flatten
```

### 4.2 小批量真实运行
```powershell
python -m reorder_engine.beta --folder "D:\TEST\downloads" --workdir "D:\buff\reorder" --no-flatten
```

### 4.2.1 临时禁用 Bandizip（推荐用于排查/避免 GUI）
```powershell
python -m reorder_engine.beta --folder "D:\TEST\downloads" --workdir "D:\buff\reorder" --no-flatten --disable-bandizip
```

### 4.3 需要深度解压
在 `config.json` 里把：
- `beta.deep_extract.enabled=true`
- 适当调整 `max_depth/min_archive_mb/final_single_mb`

然后再运行同上命令。

---

## 5. 密码库：txt / Excel（PSST 列）

### 5.1 txt 密码库
- 一行一个密码
- 空行忽略；以 `#` 开头视为注释
- 会自动去重（保留原顺序）

### 5.2 Excel 密码库（.xlsx/.xlsm）
- 把 `config.json -> paths.passwords` 指向 Excel 文件
- 表头行包含列名 `PSST`（大小写不敏感）
- 其下每格一个密码；会自动去重（保留原顺序）

依赖：需要安装 `openpyxl`（见 `requirements.txt`）。

---

## 6. 常见问题定位

- 只看主流程：打开 `reorder_engine.log`
- 需要看外部工具为什么失败：打开 `reorder_engine.tools.log`
- 遇到 `MISSING-VOLUME`：说明缺分卷，补齐后可重跑
- 看到 `_duplicates`：说明最终输出出现同名冲突；脚本会保名并分流目录
