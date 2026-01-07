# 归序（ReOrder Engine）需求与架构（v0.1）

## 1. 项目命名
- 默认名称：**归序（ReOrder Engine）**
- 命名意图：不直白强调“清洗/清道夫”，而是抽象表达“把混乱数据归于秩序”。

（如你还想更“高大上”，可选：
- **穹序（Aether Order）**：更抽象更“科幻”；
- **归档序列（Archive Order）**：更偏工程化；
- **序匣（Order Vault）**：更偏产品化。）

## 2. 问题背景
- 输入：一个目录下大量不同后缀的压缩包（zip/7z/rar 等），包含：
  - 单文件压缩包
  - 多文件分卷/分段压缩包（命名差异可能极细微，例如括号、空格、大小写等）
- 目标：
  1) 识别压缩包及其分卷集合
  2) 进行文件名清洗/标准化，并安全重命名（避免冲突）
  3) 调用本地解压工具（7z、Bandizip等）解压到指定输出目录
  4) 支持不同策略：清洗策略、分卷识别策略、解压策略、（可选）解密策略
  5) 为未来更多格式/规则留出扩展接口（阶段3）

## 3. 范围与阶段
### 阶段 1（实现）—— 文件名清洗 + 文件准备
- 关键字库：使用 txt 按行维护（例如 `resources/keywords.txt`）
- 清洗能力：
  - 删除/替换关键字
  - 统一括号形态与空白
  - 去除下载站水印等冗余片段
  - 输出一个“稳定的归一化名称”供后续分卷识别与解压
- 文件准备/解密：
  - 按扩展名、特征或规则选择解密策略（例如 mp4 解密）
  - v0.1 只提供接口与占位实现（不强行假设解密规则）

### 阶段 1.5（实现骨架）—— 还原/修复/格式还原（在解压前）
- 背景：部分压缩包可能被“加壳/改后缀/轻度加密/格式篡改”，需要调用本地还原软件先恢复
- 设计：作为独立策略层 `RestorerStrategy`，默认直通；你后续可以新增“按扩展名/特征调用某个还原工具”的实现

### 阶段 2（实现）—— 分卷识别 + 解压
- 必须支持：
  - 单卷压缩包
  - 常见分卷：
    - `name.part01.rar` / `name.part1.rar`
    - `name.rar` + `name.r00`/`name.r01`…
    - `name.7z.001` / `name.zip.001` / `name.001`
    - `name.z01` + `name.zip`
- 分卷识别重点：
  - 归一化：忽略极细微差异（如 `()`、`（）`、空格、大小写、某些尾随序号）
  - 分组：把同一组分卷聚合成 `VolumeSet`，确定入口文件（entry）

- 解压密码库：
  - 使用 txt 按行维护（例如 `resources/passwords.txt`）
  - 解压执行时按顺序轮询：先无密码，再逐个密码尝试，直到成功或耗尽

- 解压器策略：
  - v0.1 默认以 7-Zip 为核心解压器
  - 若未配置且本地不存在 `7z.exe`：允许在首次运行时联网下载（从 7-zip.org 获取 MSI 并解包到项目 `tools/7zip/`），然后写回配置文件
  - 对于分卷/单文件：只对集合的 entry 文件触发解压（分卷其余文件会被工具自动读取）

### 阶段 3（仅留接口）—— 后处理/归档/校验
- 预留 `PostProcessHook` 或 `PipelineStage` 扩展点

## 4. 非目标（v0.1）
- 不做 GUI
- 不做云端/数据库
- 不保证破解/通用解密算法（只做可插拔框架）

## 5. 关键设计：面向对象 + 策略模式
### 5.1 分层结构
- **domain**：纯数据模型（dataclass），不依赖外部工具
- **interfaces**：策略协议（清洗/解密/解压/分卷分组）
- **services**：业务编排（发现、清洗重命名、分组、选择解压策略、执行）
- **infrastructure**：与 OS/外部命令交互（subprocess）
- **pipelines**：流水线入口（阶段1/2/3的组合）

### 5.2 核心对象（概要）
- `KeywordRepository`：从 txt 读关键字
- `FilenameCleanerStrategy`：单个清洗策略接口
- `FilenameCleaningService`：组合多个策略，产出新文件名
- `SafeRenamer`：安全重命名（冲突处理、可回滚记录）
- `ArchiveDiscoveryService`：扫描目录找候选压缩包/分卷
- `VolumeGroupingStrategy`：把候选文件分组成 `VolumeSet`
- `ExtractorStrategy`：解压策略接口
  - `SevenZipExtractor`
  - `BandizipExtractor`
- `ExtractionService`：选择可用解压器并执行
- `PipelineOrchestrator`：串联阶段1/2/3

## 6. 输入/输出
- 输入：`--input` 指定目录（递归可选）
- 输出：`--output` 指定解压目标目录
- 中间产物：重命名日志（映射表）、分卷分组结果（可选）

## 7. 失败处理与可观测性
- 每一步输出结构化日志（stdout）
- 外部工具失败：记录命令、退出码、stderr；对单个集合失败不中断全局（默认）

## 8. 配置
- 外部工具路径：7z/bandizip 可从参数或环境变量传入
- 关键字库路径：默认 `resources/keywords.txt`
- 密码库路径：默认 `resources/passwords.txt`
- 随项目携带工具：优先从项目 `tools/` 目录探测（再回退到 PATH）

## 9. 可扩展点
- 新的清洗规则：实现 `FilenameCleanerStrategy`
- 新的解密器：实现 `DecryptorStrategy`
- 新的解压器：实现 `ExtractorStrategy`
- 新的分卷规则：实现 `VolumeGroupingStrategy`
- 阶段3新处理：实现 `PipelineStage`/hook
