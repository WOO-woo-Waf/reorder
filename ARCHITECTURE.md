# 归序（ReOrder Engine）架构与扩展指南

本文件解释当前项目的分层设计、每个目录的职责，以及后续如何扩展“清洗/还原/解压/后处理”等策略。

## 1. 总体分层（强约束）

- `domain/`：领域模型（`dataclass`）
  - 只放“数据结构与结果结构”，不做 IO、不调外部命令。
- `interfaces/`：策略接口（抽象类/协议）
  - 定义“能力边界”，例如：清洗策略、还原策略、解压策略、分卷分组策略、阶段扩展点。
- `services/`：业务服务（编排/组合策略）
  - 把多个策略组合成可执行的业务动作，例如：加载关键字库、清洗文件名、分卷分组、解压尝试、失败分流。
- `infrastructure/`：基础设施（与 OS/外部工具交互）
  - 只处理 `subprocess`、下载、文件系统细节、工具探测等。
- `pipelines/`：管线/入口编排
  - 面向“一个用户动作”的 end-to-end 流程，如：CLI 流程、beta 文件夹一键流程。

**扩展原则**：新增能力优先加在 `interfaces/` + `services/`，与 OS 相关的部分放 `infrastructure/`，避免把 subprocess/下载逻辑散落在服务中。

## 2. 目录说明

- `src/reorder_engine/domain/`
  - `models.py`：核心模型（VolumeSet、ExtractionRequest/Result、配置 dataclass 等）

- `src/reorder_engine/interfaces/`
  - `cleaning.py`：文件名清洗/归一化接口
  - `decrypting.py`：解密/准备接口 + 还原接口（RestorerStrategy）
  - `extracting.py`：解压器接口
  - `grouping.py`：分卷分组接口
  - `stages.py`：阶段3扩展点（PipelineStage）

- `src/reorder_engine/services/`
  - `config.py`：配置加载/写回（config.json）
  - `keywords.py`：关键字库加载
  - `passwords.py`：密码库加载
  - `cleaning.py`：清洗服务 + 安全重命名
  - `archive_naming.py`：拆分“可清洗 base”与“分卷技术尾巴”，避免重命名破坏分卷
  - `discovery.py`：发现候选压缩包（用于常规流程）
  - `grouping.py`：分卷分组默认实现
  - `decrypting.py`：解密/准备服务（默认直通）
  - `restoring.py`：还原/修复服务（默认直通）
  - `extracting.py`：解压服务（支持密码轮询/多解压器轮询）

- `src/reorder_engine/infrastructure/`
  - `command_runner.py`：外部命令统一执行器
  - `tools.py`：各解压器 CLI 封装（7z/unrar/bandizip，按配置/项目 tools/ / PATH 探测）
  - `sevenzip_bootstrap.py`：7z 自举（首次运行联网下载并解包到 tools/7zip/）

- `resources/`
  - `keywords.txt`：清洗关键字库（按行维护）
  - `passwords.txt`：解压密码库（按行维护）

- `tools/`
  - 可选放置二进制工具（尤其是 7z 的本地副本）

## 3. 配置层（config.json）

配置入口：项目根目录 `config.json`。

- `paths.keywords`：关键字库路径（相对/绝对）
- `paths.passwords`：密码库路径（相对/绝对）
- `tools.seven_zip.exe`：7z.exe 路径（若为空则自动探测/下载）
- `tools.unrar.exe`、`tools.bandizip.exe`：可选（beta/未来扩展用）

扩展建议：
- 新增工具时，在 config.json 增加一个 `tools.<name>.exe` 节点；在 `services/config.py` 增加 dataclass 映射。

## 4. 如何新增策略

### 4.1 新增清洗规则
1) 新建类实现 `FilenameCleanerStrategy`
2) 在管线中把该策略加入 `FilenameCleaningService(cleaners=[...])`

### 4.2 新增还原/修复工具
1) 实现 `RestorerStrategy`：识别可处理文件 + 调用外部还原工具生成可解压文件
2) 注册进 `RestorationService([...])`

### 4.3 新增解压器
1) 实现 `ExtractorStrategy`
2) 在 `ExtractionService(extractors=[...])` 注册
3) 如果需要密码轮询，提供 `extract_with_password` 方法（ExtractionService 会自动调用）

## 5. 管线（Pipeline）

当前有两条典型管线：

- 常规 CLI 管线：`python -m reorder_engine ...`
  - 以“识别压缩包候选”为主（不会强行把所有文件当压缩包）

- Beta 文件夹一键管线：`python -m reorder_engine.beta --folder <dir>`
  - 更激进：把文件夹内的文件尽量“变成可解压的样子”（例如后缀猜测），逐个尝试解压，成功/失败分流
  - 解压输出：默认就地解压到该目录
  - 分流：将“处理过的文件（压缩包/分卷）”移动到 `success/` 或 `failed/`
