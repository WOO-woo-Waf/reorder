# 桌面首版代码阅读指南（面向熟悉 Python／Java／OO／C++ 的制作方）

更新时间：2026-10-08（星绫解封 0.3.0 更新）。本指南基于工作区中的**实际源码**逐文件核对，不是按设计稿想象。
阅读前请先确认基线：

- 仓库 Windows 路径 `D:\buff\reorder`，WSL 路径 `/mnt/d/buff/reorder`，分支 `main`。
- 核对时 `HEAD = 07b561fe5088cf15828f4c499a913472207f7405`（`fix: complete source archive lifecycle routing`）。
- 提交与检查状态统一见 [desktop-status.md](desktop-status.md)；源码持续调整时行号可能移动，阅读时优先按链接后的符号名定位。
- 配套文档：[桌面设计](desktop-design.md)、[实施状态](desktop-status.md)、[产品方案](product_plan.md)、[架构图源](diagrams/architecture.json)。
- 用户操作见 [用户指南](desktop-user-guide.md)，格式与效果边界见 [格式指南](desktop-format-support.md)。

## 0. 这份文档解决什么问题

你会写的代码（Python/Java/C++）和这里的技术栈（TypeScript/Svelte/Rust/Python）差在语法，不差在对象组合。真正需要重新建立的是三件事：

1. **跨进程边界**：界面、Rust 宿主、Python 引擎是三个运行体，只能通过序列化协议通信，不能共享对象指针或引用。
2. **谁拥有资源**：子进程、文件句柄、SQLite 连接、密码、工作区目录分别归谁，什么时候释放。
3. **一个界面动作的完整链路**：从按钮点下去，到最终落在哪个文件、哪个方法、哪个协议帧、哪张数据库表。

本指南按“先地图、再对象、再逐个按钮、再生命周期与错误、最后开发构建与扩展”的顺序展开。

## 1. 先建立地图：三个运行体与依赖方向

首版由三层组成：

- **前端（界面层）：** Svelte 5 + TypeScript，运行在 WebView 里。入口 [apps/desktop/src/main.ts](../apps/desktop/src/main.ts)，根组件 [apps/desktop/src/App.svelte](../apps/desktop/src/App.svelte)。
- **宿主（壳）：** Rust + Tauri 2，负责开窗、文件对话框、拖放、启动并监管 Python 引擎。入口 [apps/desktop/src-tauri/src/main.rs](../apps/desktop/src-tauri/src/main.rs) 调用 [lib.rs](../apps/desktop/src-tauri/src/lib.rs) 的 `run()`。
- **引擎（核）：** Python 包 `reorder_engine`，进程内是普通对象；它对前端只有一个 JSON-RPC 入口。启动入口 [src/reorder_engine/desktop_engine.py](../src/reorder_engine/desktop_engine.py)。

依赖方向（单向前进，不允许反向调用）：

```text
App.svelte
  └─ DesktopController            (apps/desktop/src/lib/desktop-controller.ts)
       └─ EngineClient 接口        (apps/desktop/src/lib/engine-client.ts)
            └─ TauriEngineClient   → invoke('engine_request')
                 └─ Rust EngineBridge            (apps/desktop/src-tauri/src/engine_bridge.rs)
                      └─ Python JsonRpcServer     (src/reorder_engine/infrastructure/json_rpc.py)
                           └─ EngineFacade        (src/reorder_engine/application/facade.py)
                                ├─ PlanService        (application/planning.py)
                                ├─ JobRunner          (application/jobs.py)
                                │    └─ PackageProcessor (application/processing.py)
                                │         ├─ BetaFolderPipeline (services/beta_pipeline.py)
                                │         └─ FileTransaction    (infrastructure/file_transaction.py)
                                └─ JobRepository      (infrastructure/job_repository.py) → SQLite
```

对照图源：[diagrams/architecture.svg](diagrams/architecture.svg)、[diagrams/classes.svg](diagrams/classes.svg)、[diagrams/sequence.svg](diagrams/sequence.svg)、[diagrams/states.svg](diagrams/states.svg)。设计文档 [desktop-design.md](desktop-design.md) 第 2、3 节画的是同一张依赖图，读代码时以本文引用的**实际文件与行**为准。

两条硬约束，先记住：

1. **stdout 只走协议。** 见 [desktop_engine.py:20-21](../src/reorder_engine/desktop_engine.py)：进程一进来就把 `sys.stdout` 改成 stderr，真正的协议写在 `sys.stdout.buffer` 上。任何 `print()` 调试都不会污染协议帧。
2. **Rust 和 Python 各有一份方法白名单。** Rust 侧 [engine_bridge.rs:17-21](../apps/desktop/src-tauri/src/engine_bridge.rs) 的 `allowed_method`，Python 侧 [facade.py:20-22](../src/reorder_engine/application/facade.py) 的 `METHODS`。新增方法必须**两处同时改**，这是附带的纵深防御，不是自动同步。

## 2. 推荐的读代码顺序

不要从 `App.svelte` 一头扎到底。按“一条竖线走通，再横着扩”的顺序读：

1. **协议与数据契约**：先读 [contracts.ts](../apps/desktop/src/lib/contracts.ts)（TS 侧字段）与 [application/models.py](../src/reorder_engine/application/models.py)（Python 侧 pydantic 模型）。这两份是同一套 DTO 的两种语言写法，**手工保持同步**。
2. **引擎入口与路由**：[json_rpc.py](../src/reorder_engine/infrastructure/json_rpc.py) → [facade.py](../src/reorder_engine/application/facade.py)。看懂 `dispatch` 的一张 if 链，就掌握了全部 IPC 方法。
3. **前端状态与工作流**：[engine-client.ts](../apps/desktop/src/lib/engine-client.ts) → [desktop-controller.ts](../apps/desktop/src/lib/desktop-controller.ts)。Controller 是“界面用例层”，等价于你在 Java 里写的 `XxxService`，区别于业务引擎。
4. **宿主桥接**：[lib.rs](../apps/desktop/src-tauri/src/lib.rs) → [engine_bridge.rs](../apps/desktop/src-tauri/src/engine_bridge.rs)。重点看子进程启动、帧读写线程、请求-响应表、退出处理。
5. **任务与文件事务**：[jobs.py](../src/reorder_engine/application/jobs.py) → [processing.py](../src/reorder_engine/application/processing.py) → [file_transaction.py](../src/reorder_engine/infrastructure/file_transaction.py) → [job_repository.py](../src/reorder_engine/infrastructure/job_repository.py)。这是数据完整性的核心。
6. **既有业务管线**：[services/beta_pipeline.py](../src/reorder_engine/services/beta_pipeline.py)。这是被复用的老内核，桌面只在外层做“工作在副本上、发布后归档原件”。
7. **界面绑定**：[App.svelte](../apps/desktop/src/App.svelte)。最后读，因为此时你已经知道每个按钮背后会走哪条链路。
8. **基础设施细节**：[command_runner.py](../src/reorder_engine/infrastructure/command_runner.py)、[process_control.py](../src/reorder_engine/infrastructure/process_control.py)、[archive_safety.py](../src/reorder_engine/infrastructure/archive_safety.py)、[secret_store.py](../src/reorder_engine/infrastructure/secret_store.py)、[settings_repository.py](../src/reorder_engine/infrastructure/settings_repository.py)、[desktop_paths.py](../src/reorder_engine/infrastructure/desktop_paths.py)、[engine_lock.py](../src/reorder_engine/infrastructure/engine_lock.py)。

## 3. 对象组合与资源所有权

### 3.1 Python 引擎对象（进程内）

组合关系在 [facade.py:26-35](../src/reorder_engine/application/facade.py) 的 `EngineFacade.__init__` 一次搭好：Facade 拥有 `SettingsRepository`、`SecretStore`、`JobRepository`、`PlanService`、`PackageProcessor`、`JobRunner`。这是纯组合，没有继承基类，也没有工厂层。

| 对象 | 定义位置 | 拥有的资源 / 生命周期 |
|---|---|---|
| `EngineFacade` | [facade.py:25](../src/reorder_engine/application/facade.py) | 组合其他对象；`close()` 逆序释放 runner → repository → lock |
| `JsonRpcServer` | [json_rpc.py:13](../src/reorder_engine/infrastructure/json_rpc.py) | stdin/stdout 缓冲区；只在服务循环里存活 |
| `EngineLock` | [engine_lock.py:8](../src/reorder_engine/infrastructure/engine_lock.py) | 数据目录的进程锁文件；OS 在进程死亡时自动释放 |
| `SettingsRepository` | [settings_repository.py:15](../src/reorder_engine/infrastructure/settings_repository.py) | 用户数据目录下的 `settings.json`；内存缓存 + 原子写 |
| `SecretStore` | [secret_store.py:9](../src/reorder_engine/infrastructure/secret_store.py) | 密码元组 + 可选系统凭据后端；**不落明文文件** |
| `JobRepository` | [job_repository.py:18](../src/reorder_engine/infrastructure/job_repository.py) | 一个 SQLite 连接（`check_same_thread=False`）+ `RLock`；唯一写入口 |
| `PlanService` | [planning.py:51](../src/reorder_engine/application/planning.py) | 无持久资源；持有分组策略实例 |
| `JobRunner` | [jobs.py:15](../src/reorder_engine/application/jobs.py) | 一个工作线程、一个队列、每任务一个取消事件 |
| `PackageProcessor` | [processing.py:37](../src/reorder_engine/application/processing.py) | 无持久资源；每个包临时造工作区、Runner、管线 |
| `FileTransaction` | [file_transaction.py:60](../src/reorder_engine/infrastructure/file_transaction.py) | 一个包的文件句柄与动作状态；包级对象 |

### 3.2 谁工作，谁监管

- **引擎监管工具**：一次工具调用生成一个 `ExternalCommandRunner`，它在 [command_runner.py:59-61](../src/reorder_engine/infrastructure/command_runner.py) `Popen` 一个子进程，输出由一个后台线程读入有界队列，期限检查在主循环里独立进行。
- **宿主监管引擎**：Rust 的 `EngineBridge::start`（[engine_bridge.rs](../apps/desktop/src-tauri/src/engine_bridge.rs)）拉起 Python 子进程；独立 writer 持有 stdin 并消费有界队列，两个读线程分别处理 stdout 协议和 stderr 诊断。连接失效会释放 pending，下一业务请求可建立新引擎，旧任务按 SQLite 中断记录处理。
- **一个工作线程**：`JobRunner` 只开一个 `reorder-worker` 线程（[jobs.py:25-26](../src/reorder_engine/application/jobs.py)），**协议线程与工作线程是分开的**，所以取消能立刻被读到。

### 3.3 前端的“所有权”是订阅，不是资源

`DesktopController` 自己持有一份不可变快照 `DesktopState`，通过 `subscribe`（[desktop-controller.ts:25-28](../apps/desktop/src/lib/desktop-controller.ts)）通知界面。`App.svelte` 在 `onMount` 里订阅并在卸载时 `dispose()`（[App.svelte:52-70](../apps/desktop/src/App.svelte)）。轮询定时器每秒触发一次 `refresh()`（[desktop-controller.ts:51](../apps/desktop/src/lib/desktop-controller.ts)），这是一个刻意选择的“适度轮询”，不是推送。

## 4. 逐个按钮追到文件／方法／协议／数据库

五个按钮都在 [App.svelte](../apps/desktop/src/App.svelte)。下表把每个按钮的调用点、Controller 方法、IPC 方法、到达的 Python 方法与落库位置串起来。

| 按钮（界面行） | Controller 方法 | 协议方法 | Python 关键方法 | 数据库／文件 |
|---|---|---|---|---|
| 扫描 [App.svelte:90](../apps/desktop/src/App.svelte) | `prepare()` [desktop-controller.ts:69](../apps/desktop/src/lib/desktop-controller.ts) | `plans.create` | `EngineFacade.dispatch` [facade.py:74](../src/reorder_engine/application/facade.py) → `PlanService.create` [planning.py:60](../src/reorder_engine/application/planning.py) | `plans` 表（[job_repository.py:48](../src/reorder_engine/infrastructure/job_repository.py) `save_plan`） |
| 开始处理 [App.svelte:90](../apps/desktop/src/App.svelte) | `start()` [desktop-controller.ts:81](../apps/desktop/src/lib/desktop-controller.ts) | `jobs.start` | `dispatch` [facade.py:83](../src/reorder_engine/application/facade.py) → `JobRunner.submit` [jobs.py:33](../src/reorder_engine/application/jobs.py) | `jobs` 表（[job_repository.py:64](../src/reorder_engine/infrastructure/job_repository.py) `create_job`） |
| 取消 [App.svelte:90](../apps/desktop/src/App.svelte) | `cancel()` [desktop-controller.ts:90](../apps/desktop/src/lib/desktop-controller.ts) | `jobs.cancel` | `dispatch` [facade.py:124](../src/reorder_engine/application/facade.py) → `JobRunner.cancel` [jobs.py:48](../src/reorder_engine/application/jobs.py) | `jobs` 状态改 `cancelling` + `events` 表 |
| 重试 [App.svelte:90](../apps/desktop/src/App.svelte) | `retry()` [desktop-controller.ts:128](../apps/desktop/src/lib/desktop-controller.ts) | `jobs.retry` | `dispatch` [facade.py:86](../src/reorder_engine/application/facade.py) → `JobRunner.retry` [jobs.py:58](../src/reorder_engine/application/jobs.py) | 新 `plans` + 新 `jobs`（`retry_of` 关联旧任务） |
| 保存设置 [App.svelte:116](../apps/desktop/src/App.svelte) | `saveSettings()` [desktop-controller.ts:140](../apps/desktop/src/lib/desktop-controller.ts) | `settings.update` | `dispatch` [facade.py:57](../src/reorder_engine/application/facade.py) → `SettingsRepository.update` [settings_repository.py:32](../src/reorder_engine/infrastructure/settings_repository.py) | `settings.json`（用户数据目录），**不入库** |

下面逐个展开。

### 4.1 扫描

界面：[App.svelte:90](../apps/desktop/src/App.svelte) 的 `ScanLine` 按钮 `onclick={() => controller.prepare()}`。

链路：

1. `DesktopController.prepare`（[desktop-controller.ts:69-80](../apps/desktop/src/lib/desktop-controller.ts)）先校验有输入和输出目录，然后发 `plans.create`，并把返回的 `plan.packages` 渲染成预览。它同时生成一个 `startKey = crypto.randomUUID()`，供“开始”做幂等键。
2. `TauriEngineClient.request`（[engine-client.ts:11-14](../apps/desktop/src/lib/engine-client.ts)）调用 `invoke('engine_request', { method, params })`。
3. Rust `engine_request`（[lib.rs:17-21](../apps/desktop/src-tauri/src/lib.rs)）经由 `bridge(...)` 惰性启动引擎，然后 `EngineBridge.request`（[engine_bridge.rs:113](../apps/desktop/src-tauri/src/engine_bridge.rs)）写一帧到子进程 stdin，等待对应 `id` 的回包。
4. Python `JsonRpcServer.handle`（[json_rpc.py:17](../src/reorder_engine/infrastructure/json_rpc.py)）解析帧、校验 `jsonrpc`/`id`/`method`/`params`，转给 `EngineFacade.dispatch`。
5. `dispatch` 命中 `plans.create`（[facade.py:74-82](../src/reorder_engine/application/facade.py)）：给未传 `options` 的请求补上当前设置，调 `PlanService.create`。
6. `PlanService.create`（[planning.py:60-110](../src/reorder_engine/application/planning.py)）**只读扫描**：校验输出/输入路径必须绝对且不含符号链接（`reject_links` [planning.py:20](../src/reorder_engine/application/planning.py)），扫目录或收文件，按父目录分组后交给分组策略 `DefaultVolumeGroupingStrategy`，最后给每个源成员做 `source_snapshot`（记录 size/mtime_ns/device/inode）。它**不移动、不删除任何文件**。
7. 结果存进 `plans` 表（`save_plan`）。返回给界面的是**压缩预览**：`name` 截断到 200 字符，`members` 只给数量，`bytes` 给合计（[facade.py:80-82](../src/reorder_engine/application/facade.py)）。源身份快照留在引擎里，不出协议。

【实际源码】幂等与源快照关键行，[planning.py:31](../src/reorder_engine/application/planning.py)：

```python
return SourceSnapshot(path=str(path.resolve()), size=stat.st_size, mtime_ns=stat.st_mtime_ns,
                      device=stat.st_dev, inode=stat.st_ino)
```

### 4.2 开始处理

界面：[App.svelte:90](../apps/desktop/src/App.svelte) 的 `Play` 按钮 `onclick={() => controller.start()}`。

链路：

1. `DesktopController.start`（[desktop-controller.ts:81-89](../apps/desktop/src/lib/desktop-controller.ts)）要求已有 `plan` 和 `startKey`，发 `jobs.start`，参数是 `plan_id` + `idempotency_key`。
2. `dispatch` 命中 `jobs.start`（[facade.py:83-85](../src/reorder_engine/application/facade.py)）：从库里取回完整计划，交给 `JobRunner.submit(plan, key)`。
3. `JobRunner.submit`（[jobs.py:33-46](../src/reorder_engine/application/jobs.py)）：先按 key 查重（同 key 且同 plan 直接返回旧任务），再 `planner.validate_sources(plan)` **重验源快照**，然后 `create_job` 建一条 `queued` 任务，注册取消事件，把任务压进队列。工作线程随后把它置为 `running`（[jobs.py:104](../src/reorder_engine/application/jobs.py)）。
4. `create_job`（[job_repository.py:64-79](../src/reorder_engine/infrastructure/job_repository.py)）写入 `jobs` 表，`request_key` 唯一约束保证幂等。

注意协议行为：`jobs.start` 只是**入队后立即返回快照**，不会阻塞到处理完成。这很关键——Rust 侧单次请求有 60 秒超时（[engine_bridge.rs:133](../apps/desktop/src-tauri/src/engine_bridge.rs)），所以长任务必须靠 `jobs.get` 轮询，不能靠一次阻塞调用。

【实际源码】源重验，[planning.py:34-40](../src/reorder_engine/application/planning.py)：

```python
def validate_source(snapshot: SourceSnapshot) -> None:
    try:
        current = source_snapshot(Path(snapshot.path))
    except OSError as exc:
        raise EngineError("INPUT_CHANGED", "输入文件已不可用，请重新扫描。") from exc
    if current != snapshot:
        raise EngineError("INPUT_CHANGED", "扫描后输入文件发生变化，请重新扫描。")
```

### 4.3 取消

界面：[App.svelte:90](../apps/desktop/src/App.svelte) 的 `Square` 按钮 `onclick={() => controller.cancel()}`（仅在 `running` 时出现）。

链路：

1. `DesktopController.cancel`（[desktop-controller.ts:90-98](../apps/desktop/src/lib/desktop-controller.ts)）发 `jobs.cancel`，随后立刻 `refresh(true)` 拉一次快照。
2. `JobRunner.cancel`（[jobs.py:48-56](../src/reorder_engine/application/jobs.py)）只做两件事：给该任务的 `threading.Event` 置位，并把任务状态改成 `cancelling`。**它不杀线程**。
3. 真正生效点在两个地方：
   - 工作线程进入每个包之前的检查（[jobs.py:106-108](../src/reorder_engine/application/jobs.py)），未开始的包直接标 `cancelled`。
   - 处理过程中：`ExternalCommandRunner` 主循环（[command_runner.py:99-100](../src/reorder_engine/infrastructure/command_runner.py)）和复制循环（[file_transaction.py:42-43](../src/reorder_engine/infrastructure/file_transaction.py)）都会周期检查取消事件，命中即抛 `ProcessingCancelled`。
4. 取消在**安全边界**生效：`FileTransaction.route_sources`（[file_transaction.py:157-186](../src/reorder_engine/infrastructure/file_transaction.py)）在改源文件的那一段**不检查取消**，避免“删了一半源文件却报告 cancelled”。设计文档 [desktop-design.md](desktop-design.md) 第 6 节对此有说明。
5. 关闭窗口时会先尝试取消：`App.svelte` 的 `onCloseRequested`（[App.svelte:62-67](../apps/desktop/src/App.svelte)）在任务运行中弹确认框。

### 4.4 重试

界面：[App.svelte:90](../apps/desktop/src/App.svelte) 的 `RotateCcw` 按钮，带可重试数量徽标。

链路：

1. `DesktopController.retry`（[desktop-controller.ts:128-139](../apps/desktop/src/lib/desktop-controller.ts)）只挑出状态在 `retryable` 集合里的包（`failed/deferred/cancelled/interrupted`，定义在 [contracts.ts:34](../apps/desktop/src/lib/contracts.ts)），发 `jobs.retry`，附带新幂等键。
2. `JobRunner.retry`（[jobs.py:58-86](../src/reorder_engine/application/jobs.py)）做过更严的校验：只允许旧任务里 `failed/deferred/cancelled/interrupted` 的包；`needs_review`（需要人工核对）被明确排除。它把**已路由的失败原件路径**当成重试输入（`routed` 映射，[jobs.py:74-81](../src/reorder_engine/application/jobs.py)），而不是被恢复过的副本；然后复制出一份新计划、新 `package_id`，以 `retry_of` 关联旧任务。
3. 成功后走 `submit`，因此同样会重验源、同样受幂等 key 保护。

【实际源码】可重试集合（前后端各一份），[contracts.ts:34](../apps/desktop/src/lib/contracts.ts)：`failed/deferred/cancelled/interrupted`；`needs_review` 不在其中。

### 4.5 保存设置

界面：[App.svelte:116](../apps/desktop/src/App.svelte) 的“保存设置”按钮。`editSettings`（[App.svelte:36-39](../apps/desktop/src/App.svelte)）先深拷贝一份草稿 `structuredClone(...)`，所以你改的是草稿，点保存才落到引擎。

链路：

1. `DesktopController.saveSettings`（[desktop-controller.ts:140-146](../apps/desktop/src/lib/desktop-controller.ts)）发 `settings.update`，参数是把 `DesktopSettings` 平铺展开。
2. `dispatch` 命中 `settings.update`（[facade.py:57-60](../src/reorder_engine/application/facade.py)）：先 `_require_idle()`（任务运行时拒绝改设置，抛 `BUSY`），再用 `DesktopSettings.model_validate(params)` 严格校验，最后 `SettingsRepository.update`。
3. `SettingsRepository.update`（[settings_repository.py:32-45](../src/reorder_engine/infrastructure/settings_repository.py)）用临时文件 + `os.fsync` + `os.replace` 做原子替换，写进用户数据目录的 `settings.json`。

设置里的 `revision()`（[settings_repository.py:28-30](../src/reorder_engine/infrastructure/settings_repository.py)）是 `<settings.json 的 sha256>`。计划里存了扫描时的 `settings_revision`，开始处理时若不一致就抛 `SETTINGS_CHANGED`（[planning.py:131-132](../src/reorder_engine/application/planning.py)）——这就是界面上那句“设置已保存，请重新扫描”的技术来源。

### 4.6 附：打开结果目录

界面：[App.svelte:91](../apps/desktop/src/App.svelte) 的“打开结果目录”。它不走 `engine_request`，而是 `controller.openResult()`（[desktop-controller.ts:168-172](../apps/desktop/src/lib/desktop-controller.ts)）→ `TauriEngineClient.openResult` → Rust `open_result` 命令（[lib.rs:23-37](../apps/desktop/src-tauri/src/lib.rs)）。Rust 会先问引擎 `results.get` 拿到登记路径，**再独立校验**目标路径必须等于输出根或落在登记列表里（[lib.rs:28-36](../apps/desktop/src-tauri/src/lib.rs)），然后才交给 opener 插件打开。这是“双人复核”：Python 登记，Rust 再验。

## 5. 协议与数据契约

### 5.1 帧格式

- UTF-8 JSON-RPC 2.0，一行一帧（JSON Lines）。
- 每帧上限 1 MiB：Python 侧常量 [json_rpc.py:10](../src/reorder_engine/infrastructure/json_rpc.py)、Rust 侧常量 [engine_bridge.rs:13](../apps/desktop/src-tauri/src/engine_bridge.rs) 都为 `1024*1024`。
- Rust 读线程要求帧以 `\n` 结束、`jsonrpc=="2.0"`、`id` 是数字（[engine_bridge.rs:66-89](../apps/desktop/src-tauri/src/engine_bridge.rs)）；不符合就结束读线程并把所有待响应请求标为“引擎连接已结束”。
- Python 服务循环用 `reader.readline(MAX_FRAME+1)`；超长帧会吞掉到下一个换行再回 `FRAME_TOO_LARGE`（[json_rpc.py:46-59](../src/reorder_engine/infrastructure/json_rpc.py)）。

### 5.2 DTO 是手工同步，不是自动生成

**重要**：Python 的 `application/models.py` 用 pydantic 定义契约（`ConfigDict(extra="forbid", strict=True)`，[models.py:16-17](../src/reorder_engine/application/models.py)），TS 的 `src/lib/contracts.ts` 是**手写 interface**。两边没有任何代码生成器，字段名、状态枚举都要人工保持一致。改动一侧务必同步另一侧，并检查测试 [tests/test_desktop_engine.py](../tests/test_desktop_engine.py) 与前端 [desktop-controller.test.ts](../apps/desktop/src/lib/desktop-controller.test.ts)。不要假设有 DTO 自动生成步骤。

状态枚举两边各有一份：

- Python：[models.py:6-13](../src/reorder_engine/application/models.py)（`PackageState`、`JobState` 及两个终态集合）。
- TS：[contracts.ts:1-3](../apps/desktop/src/lib/contracts.ts) 与 [contracts.ts:32-34](../apps/desktop/src/lib/contracts.ts)。

### 5.3 协议方法表与实际实现

方法白名单在 [facade.py:20-22](../src/reorder_engine/application/facade.py)，实际分派在 [facade.py:46-133](../src/reorder_engine/application/facade.py)：

| 方法 | 分派行 | 说明 |
|---|---|---|
| `system.info` | [facade.py:52-55](../src/reorder_engine/application/facade.py) | 版本 `0.3.0`、`protocol_version=1`、平台、能力、工具与密码状态 |
| `settings.get` / `settings.update` | [facade.py:56-60](../src/reorder_engine/application/facade.py) | 读／写设置；写要求空闲 |
| `passwords.replace` / `passwords.import` | [facade.py:61-73](../src/reorder_engine/application/facade.py) | 替换或从 UTF-8 文件导入；导入限 512 KiB 普通文件 |
| `plans.create` | [facade.py:74-82](../src/reorder_engine/application/facade.py) | 只读扫描 |
| `jobs.start` | [facade.py:83-85](../src/reorder_engine/application/facade.py) | 入队 |
| `jobs.retry` | [facade.py:86-87](../src/reorder_engine/application/facade.py) | 新任务 |
| `jobs.list` | [facade.py:88-95](../src/reorder_engine/application/facade.py) | 最近任务，每项只带前 5 个包 |
| `jobs.events` | [facade.py:96-104](../src/reorder_engine/application/facade.py) | 有界事件页 + 轻量快照 |
| `jobs.logs` | [facade.py:105-119](../src/reorder_engine/application/facade.py) | 脱敏日志分页，单行截断 |
| `jobs.get` / `jobs.cancel` | [facade.py:122-125](../src/reorder_engine/application/facade.py) | 快照／取消 |
| `results.get` | [facade.py:126-132](../src/reorder_engine/application/facade.py) | 输出根 + 已登记的现存路径 |

### 5.4 数据库表

Schema 在 [job_repository.py:31-46](../src/reorder_engine/infrastructure/job_repository.py) 建：

- `plans(id, body)`：整条计划的 JSON。
- `jobs(id, plan_id, request_key UNIQUE, body)`：任务快照 JSON，`request_key` 唯一约束撑起幂等。
- `events(job_id, seq, body)`：状态事件；`_save` 时同步递增 `last_seq`，并删除 `seq < last_seq-1000` 只保留最近约 1000 条（[job_repository.py:93-102](../src/reorder_engine/infrastructure/job_repository.py)）。
- `actions(id, job_id, package_id, phase, body)`：文件动作日志，是崩溃恢复的依据。

连接配置：`PRAGMA journal_mode=WAL`、`PRAGMA foreign_keys=ON`、`user_version` 只在 0/1 时接受（[job_repository.py:24-30](../src/reorder_engine/infrastructure/job_repository.py)）。所有写路径都持 `RLock`，`JobRepository` 是唯一写入口。

## 6. 文件生命周期与“没有自动 reconcile”

这是首版最需要看懂的一段，也是数据完整性所在。

### 6.1 一个包的处理阶段

在 `PackageProcessor.process`（[processing.py:45-122](../src/reorder_engine/application/processing.py)）里：

1. **准备工作区**：工作区是 `data_root/work/<job_id>/<package_id>`（[processing.py:52](../src/reorder_engine/application/processing.py)）。先按 `source_bytes*2` 估算磁盘空间，不够就 `DISK_FULL`（[processing.py:55-58](../src/reorder_engine/application/processing.py)）。
2. **复制原件到工作区**：逐成员 `validate_source` 后 `copy_verified`（[processing.py:61-63](../src/reorder_engine/application/processing.py)）。`copy_verified`（[file_transaction.py:33-57](../src/reorder_engine/infrastructure/file_transaction.py)）用 `open("xb")` 私有写、边写边算 sha256、`fsync`、再读回复算比对，不一致就 `COPY_MISMATCH` 且**不删原件**。
3. **在工作副本上跑老管线**：`BetaFolderPipeline` 在 `workspace` 上产生 `workspace/final`、`workspace/error_files`、`workspace/deferred_volumes`、`workspace/success/archives`、`workspace/intermediate`。注意：老管线往 `workspace/success/archives` 移动的只是**副本**，后面会连同工作区一起丢弃。
4. **发布成品**：只有 `succeeded`/`partial` 才发布，来源是 `workspace/{final,error_files,deferred_volumes}`（[processing.py:105-106](../src/reorder_engine/application/processing.py)）。发布用 `FileTransaction.publish`（[file_transaction.py:107-147](../src/reorder_engine/infrastructure/file_transaction.py)）：先建临时 `.partial`，走硬链接独占安装（失败退回复制），中途任何异常都清理临时文件，且**从不覆盖已存在目标**，同名冲突改投 `_duplicates/<job_id>/<package_id>/`（[file_transaction.py:88-98](../src/reorder_engine/infrastructure/file_transaction.py)）。
5. **归档真实原件**：`route_sources`（[file_transaction.py:157-186](../src/reorder_engine/infrastructure/file_transaction.py)）按包结果选目的地：成功/部分 → `success/archives`；缺卷 → `deferred_volumes/<package_id>`；纯失败 → `error_files/<category>`（[processing.py:108-114](../src/reorder_engine/application/processing.py)）。关键是顺序：**先把整卷都复制校验好，再统一删源**，删除发生在取消边界之外。
6. **收尾**：`finally`（[processing.py:116-121](../src/reorder_engine/application/processing.py)）在无未完成动作且未开 `keep_workspace` 时删除工作区；如果还有未完成动作，会保留工作区供人工恢复。

### 6.2 动作日志的相位

每个文件动作先写意图再执行，相位序列是：

```text
prepared → copied / published → source_removed → committed
                                            （异常时为 abandoned）
```

定义与更新见 [job_repository.py:129-143](../src/reorder_engine/infrastructure/job_repository.py) 与 [file_transaction.py:100-105](../src/reorder_engine/infrastructure/file_transaction.py)。设计文档里提到的“SQLite commit ≠ 文件系统事务”就是这个意思：**数据库提交不等于磁盘上真的做完了**，恢复时要回查真实路径与身份。

### 6.3 重启恢复：`recover_interrupted`，而不是自动 reconcile

引擎每次启动，`JobRunner.__init__` 会调用 `repository.recover_interrupted()`（[jobs.py:24](../src/reorder_engine/application/jobs.py)）。它的逻辑在 [job_repository.py:150-164](../src/reorder_engine/infrastructure/job_repository.py)：

- 找出所有**未完成动作**（`phase NOT IN ('committed','abandoned')`）。
- 涉及这些动作的包 → `needs_review`（需要人工核对）；其余处于中间态（`queued/preparing/extracting/publishing/archiving`）的包 → `interrupted`。
- 对应任务 → `needs_review` 或 `interrupted`。

代码里没有自动回滚、自动重做文件或 `FileTransaction.reconcile()`。正式类图已经对齐实现；恢复只更新任务/包状态、登记保留工作区，再由用户人工核对或明确重试。未决动作按 `(job_id, package_id)` 区分，不能把旧任务动作套到同包 ID 的新任务。

落地实现是 `recover_interrupted()` + `incomplete_actions()`；`JobRunner._register_retained_workspaces` 将中断副本登记为已提交的 `recovery_note`，让结果查询可返回人工检查位置。日志设备失败只写通用 stderr 诊断，不阻断文件处理或终态。

## 7. 错误层级与状态机

### 7.1 三层错误

**第一层：协议错误**（[json_rpc.py:30-39](../src/reorder_engine/infrastructure/json_rpc.py)）。

| 情况 | JSON-RPC code | 业务 code |
|---|---|---|
| 非法 JSON | `-32700` | `PARSE_ERROR` |
| 请求结构不对 | `-32600` | `INVALID_REQUEST` |
| 方法不存在 | `-32601` | `METHOD_NOT_FOUND` |
| 参数类型/范围错（pydantic `ValidationError`） | `-32602` | `INVALID_PARAMS` |
| 未预期异常 | `-32603` | `INTERNAL_ERROR` |
| 帧过大／响应过大 | `-32000` | `FRAME_TOO_LARGE` / `RESPONSE_TOO_LARGE` |

**第二层：业务错误**。`EngineError(code, message)` 定义在 [errors.py:4-9](../src/reorder_engine/application/errors.py)，取消是它的子类 `ProcessingCancelled`（code `CANCELLED`，[errors.py:12-14](../src/reorder_engine/application/errors.py)）。`EngineError` 在 [json_rpc.py:34-36](../src/reorder_engine/infrastructure/json_rpc.py) 被映射为 `-32000` 并保留业务 code 在 `error.data.code`。实际会出现的业务 code 包括：`BUSY`、`INVALID_OUTPUT`、`INVALID_INPUT`、`NO_INPUT`、`BATCH_TOO_LARGE`、`INPUT_CHANGED`、`SETTINGS_CHANGED`、`UNSAFE_PATH`、`UNSAFE_FILE`、`UNSAFE_OUTPUT`、`UNSAFE_ARCHIVE`、`OUTPUT_LIMIT`、`OUTPUT_CONFLICT`、`ARCHIVE_UNREADABLE`、`COPY_MISMATCH`、`DISK_FULL`、`TOOL_MISSING`、`TOOL_START_FAILED`、`TOOL_TIMEOUT`、`GROUP_MISMATCH`、`PLAN_NOT_FOUND`、`JOB_NOT_FOUND`、`PACKAGE_NOT_FOUND`、`DATABASE_VERSION`、`ENGINE_IN_USE`、`SHUTDOWN_PENDING`、`SECRET_STORE_FAILED`、`INVALID_PASSWORDS`、`INVALID_PASSWORD_FILE`、`IDEMPOTENCY_CONFLICT`、`RETRY_NOT_ALLOWED`、`PROCESSING_FAILED`。

**第三层：进程/工具错误**。外部工具的超时被编码成 `exit_code == 124`（[command_runner.py:153](../src/reorder_engine/infrastructure/command_runner.py)），取消编码成 `130`。Rust 侧把 Python 的 `error.data.code` 与 message 拼成 `"CODE: message"` 再交给 TS（[engine_bridge.rs:77-80](../apps/desktop/src-tauri/src/engine_bridge.rs)），前端最终把字符串放进 `DesktopState.error`（[desktop-controller.ts:37](../apps/desktop/src/lib/desktop-controller.ts)）显示。

### 7.2 包里异常如何归并成状态

工作线程里，若 `processor.process` 抛异常，会按“是否有未提交动作”决定终态（[jobs.py:116-126](../src/reorder_engine/application/jobs.py)）：

```text
有未完成或已提交动作 → needs_review
否则若是 ProcessingCancelled → cancelled
否则 → failed（code 取 EngineError.code，否则 PROCESSING_FAILED）
```

整个任务跑完后，任务状态由包的集合推导（[jobs.py:127-138](../src/reorder_engine/application/jobs.py)）：任意包 `needs_review` → 任务 `needs_review`；全 `succeeded` → `succeeded`；全 `cancelled` → `cancelled`；否则 `partial`（纯 `failed/interrupted` 归 `failed`）。工作线程自身崩了才把任务置 `interrupted`（[jobs.py:139-140](../src/reorder_engine/application/jobs.py)）。

### 7.3 状态机

包状态集合见 [models.py:6-10](../src/reorder_engine/application/models.py)，任务状态见 [models.py:11](../src/reorder_engine/application/models.py)。图形化的状态迁移在 [diagrams/states.svg](diagrams/states.svg)。要点：

- 取消是**独立终态**，不会被伪装成密码错误（[desktop-design.md](desktop-design.md) 第 5 节）。
- `partial` 与 `needs_review` 语义不同：前者是“有部分输出但流程正常结束”，后者是“发布/归档结果无法确定，需人工核对”。

## 8. 开发与构建入口

### 8.1 开发（dev）

前端开发服务器（[package.json:6-12](../apps/desktop/package.json)）：

```bash
npm run dev        # vite --host 127.0.0.1，端口 1420（vite.config.ts:7）
npm run tauri dev  # 同时起 Tauri 宿主
```

开发态下 Python 引擎是**直接跑源码**，不是冻结 exe。看 `EngineBridge::start`（[engine_bridge.rs:39-51](../apps/desktop/src-tauri/src/engine_bridge.rs)）：如果找不到打包好的 `resources/engine/reorder-engine(.exe)`，且是 debug 构建，就用 `REORDER_PYTHON`（默认 `python`）执行 `-m reorder_engine.desktop_engine`，并把 `PYTHONPATH` 指向 `<repo>/src`。因此本机需要能 import 到 `reorder_engine`，依赖见 [pyproject.toml](../pyproject.toml) 的 `desktop` extras（`pydantic`、`keyring`）与 `pyzipper`。

Rust 与前端配置：[tauri.conf.json](../apps/desktop/src-tauri/tauri.conf.json)（窗口、CSP、bundle）、[capabilities/main.json](../apps/desktop/src-tauri/capabilities/main.json)（`core:default`、`core:window:allow-destroy`、文件对话框与确认；没有 shell/fs 插件权限）、[Cargo.toml](../apps/desktop/src-tauri/Cargo.toml)、[vite.config.ts](../apps/desktop/vite.config.ts)、[svelte.config.js](../apps/desktop/svelte.config.js)、[tsconfig.json](../apps/desktop/tsconfig.json)、[index.html](../apps/desktop/index.html)、[build.rs](../apps/desktop/src-tauri/build.rs)。

### 8.2 构建（Windows 首交付）

构建脚本 [scripts/build_desktop_windows.ps1](../scripts/build_desktop_windows.ps1) 的步骤：

1. 建/复用 `runtime/desktop-build-venv`，按 [scripts/requirements-desktop-windows.lock.txt](../scripts/requirements-desktop-windows.lock.txt) 安装。
2. 跑 [scripts/stage_desktop_engine.py](../scripts/stage_desktop_engine.py)：用 PyInstaller `--onedir` 冻结引擎，把结果拷到 `apps/desktop/src-tauri/resources/engine`，并带上 7-Zip（含 `License.txt`，缺 license 会报错，脚本行 51-56）。
3. 收集第三方许可；[stage_desktop_resources.py](../scripts/stage_desktop_resources.py) 按具名指南、图表与许可 manifest 暂存资源，旧资源先保存快照。在 `apps/desktop` 里 `npm ci` → `npm run check` → Tauri build，显式使用 `custom-protocol`、Cargo jobs=2 和项目 `target/.tauri` 工具缓存（`-PortableOnly` 时加 `--no-bundle`）。
4. [package_desktop.py](../scripts/package_desktop.py) 从明确的 EXE、引擎、指南和许可输入组装新便携目录与 ZIP，生成逐文件 manifest 和发行包 SHA-256；已有运行目录不会被覆盖。

默认执行完整步骤。`-SkipDependencies`、`-SkipEngineStage`、`-SkipFrontendBuild` 只供确认对应输入仍是当前源码的制作者复用有效产物；改 Python 后必须重新 stage，改前端后必须重新 build。`-TauriCli` 可指定另一个已安装 CLI，避免 WSL 与 Windows 原生 npm 依赖互相替换。

`-SkipEngineStage` 仍检查 7-Zip 本体/DLL、许可、Apate 和冻结运行时核心文件；NSIS 与 ZIP 使用同一资源清单。当前构建脚本交付 Windows x64，其他平台的冻结与打包仍待对应平台实现和验收。

正常启动将数据放入 Tauri 用户目录；`Hoshiribbon.exe --portable`（或 `Start-Portable.cmd`）将数据放入 EXE 旁 `data/`，验证可写，并强制密码仅保存在会话中。

冻结入口是 [scripts/desktop_engine_entry.py](../scripts/desktop_engine_entry.py) → [desktop_engine.py](../src/reorder_engine/desktop_engine.py) 的 `main()`。发行包含固定 7-Zip、UnRAR、Bandizip CLI 与公开词库；私人 `passwords.txt`、用户 `config.json` 和 `restoreAB.exe` 排除。工具身份由 [desktop-tools.lock.json](../scripts/desktop-tools.lock.json) 固定，公开词库由 [desktop-defaults.lock.json](../scripts/desktop-defaults.lock.json) 固定；stage、validate、package 校验身份。仅资源变动可使用 tools-only，Python代码变动必须重新冻结。

### 8.3 检查与测试入口

| 范围 | 命令（仓库根，本机 shell 前缀按项目要求） |
|---|---|
| Python 桌面引擎定向测试 | `PYTHONPATH=src python -m pytest tests/test_desktop_engine.py -q` |
| 前端类型/Svelte 诊断 | 在 `apps/desktop` 执行 `npm run check` |
| 前端 Controller 契约测试 | 在 `apps/desktop` 执行 `npm run test`（vitest） |
| 前端生产构建 | 在 `apps/desktop` 执行 `npm run build` |
| Rust 编译检查 | `cargo check --tests`（在 `apps/desktop/src-tauri`） |
| Windows 真实引擎 smoke | [scripts/smoke_desktop_engine.py](../scripts/smoke_desktop_engine.py) 用真实 7z 对合成样本跑端到端 |

本文只做静态核对，**没有运行上述任何测试**；实际通过/失败记录见 [desktop-status.md](desktop-status.md) 第 5 节。

## 9. 新增业务如何扩展

### 9.1 加一个 IPC 方法（最常见的改动）

按依赖方向一次改五处，缺一处就会在运行时断链：

1. **Python 白名单**：[facade.py:20-22](../src/reorder_engine/application/facade.py) 的 `METHODS` 加方法名。
2. **Python 分派**：[facade.py:46-133](../src/reorder_engine/application/facade.py) 的 `dispatch` 里加分支。若需要新参数，在 [application/models.py](../src/reorder_engine/application/models.py) 加一个 `Contract` 子类并用 `model_validate` 校验。
3. **Rust 白名单**：[engine_bridge.rs:17-21](../apps/desktop/src-tauri/src/engine_bridge.rs) 的 `allowed_method` 加同一名字。这是**独立**的防线，不会读 Python 的白名单。
4. **TS 方法联合类型**：[contracts.ts:29-31](../apps/desktop/src/lib/contracts.ts) 的 `Method` 加名字，必要时加对应的 TS interface。
5. **Controller 与界面**：在 [desktop-controller.ts](../apps/desktop/src/lib/desktop-controller.ts) 加方法，在 [App.svelte](../apps/desktop/src/App.svelte) 绑按钮。

约束别忘了：响应整体仍受 1 MiB 帧限制；列表类返回要分页或截断（参考 `jobs.list` 只回前 5 个包、`_job_view` 清空 `results` 的做法，[facade.py:135-142](../src/reorder_engine/application/facade.py)）。

### 9.2 加一个设置项

在 [models.py](../src/reorder_engine/application/models.py) 的 `ProcessingOptions` 加带 `Field(...)` 边界的字段（见 [models.py:20-29](../src/reorder_engine/application/models.py)），在 [contracts.ts](../apps/desktop/src/lib/contracts.ts) 的 `ProcessingOptions` 加镜像字段，在 [App.svelte:107-113](../apps/desktop/src/App.svelte) 的设置面板加控件。默认值改了会自动改变 `settings.revision()`，从而让旧计划在开始时报 `SETTINGS_CHANGED` 要求重扫。

### 9.3 加一个解压工具

参照 `unrar`/`bandizip` 的可选挂载方式：在 [settings_repository.py:54-58](../src/reorder_engine/infrastructure/settings_repository.py) 的 `names` 映射加候选可执行名，在 [processing.py:69-74](../src/reorder_engine/application/processing.py) 的 delegate 列表加一个 `ExtractorStrategy` 实现（接口见 [interfaces/extracting.py](../src/reorder_engine/interfaces/extracting.py)），必要时在 [models.py:32-36](../src/reorder_engine/application/models.py) 的 `ToolPaths` 加路径字段。新增工具会自动被 `GuardedExtractor` 包一层安全预检（[archive_safety.py:138-165](../src/reorder_engine/infrastructure/archive_safety.py)）。

### 9.4 改 DTO 或状态枚举

必须成对改 [models.py](../src/reorder_engine/application/models.py) 与 [contracts.ts](../apps/desktop/src/lib/contracts.ts)，并同步更新 [tests/test_desktop_engine.py](../tests/test_desktop_engine.py) 与 [desktop-controller.test.ts](../apps/desktop/src/lib/desktop-controller.test.ts)。再次强调：**没有 DTO 代码生成**。

## 10. 容易踩的坑与不变量

- **不要在 stdout 打印。** 协议独占 stdout；调试输出会被当成坏帧（[desktop_engine.py:21](../src/reorder_engine/desktop_engine.py)）。
- **不要一次性返回大结果。** 超过 1 MiB 会被替换成 `RESPONSE_TOO_LARGE`（[json_rpc.py:54-57](../src/reorder_engine/infrastructure/json_rpc.py)）。
- **长任务不要靠单次请求阻塞。** Rust 请求 60 秒超时（[engine_bridge.rs:133](../apps/desktop/src-tauri/src/engine_bridge.rs)）；`jobs.start` 立即返回，进度靠 `jobs.get`。
- **取消不是立刻停。** 它在安全边界生效，删除源文件那一段不响应取消（[file_transaction.py:178-186](../src/reorder_engine/infrastructure/file_transaction.py)）。
- **从不覆盖已有文件。** 发布与归档都用独占路径，同名改投 `_duplicates`（[file_transaction.py:88-98](../src/reorder_engine/infrastructure/file_transaction.py)）。
- **两个白名单、两份 DTO、两套状态枚举**都要同步维护。
- **没有自动 reconcile、没有自动重跑、没有 DTO 自动生成。** 恢复只是标记 `needs_review` 等人工核对（[job_repository.py:150-164](../src/reorder_engine/infrastructure/job_repository.py)）。
- **密码不落明文。** `SecretStore` 只接受系统凭据后端，否则退回会话内存（[secret_store.py:20-37](../src/reorder_engine/infrastructure/secret_store.py)）；日志经 `redact`（[secret_store.py:56-59](../src/reorder_engine/infrastructure/secret_store.py)）。

## 附录 A：源文件定位索引

引擎（Python）：

- 入口：[src/reorder_engine/desktop_engine.py](../src/reorder_engine/desktop_engine.py)
- 应用层：[application/facade.py](../src/reorder_engine/application/facade.py)、[planning.py](../src/reorder_engine/application/planning.py)、[jobs.py](../src/reorder_engine/application/jobs.py)、[processing.py](../src/reorder_engine/application/processing.py)、[models.py](../src/reorder_engine/application/models.py)、[errors.py](../src/reorder_engine/application/errors.py)
- 基础设施：[infrastructure/json_rpc.py](../src/reorder_engine/infrastructure/json_rpc.py)、[job_repository.py](../src/reorder_engine/infrastructure/job_repository.py)、[file_transaction.py](../src/reorder_engine/infrastructure/file_transaction.py)、[archive_safety.py](../src/reorder_engine/infrastructure/archive_safety.py)、[command_runner.py](../src/reorder_engine/infrastructure/command_runner.py)、[process_control.py](../src/reorder_engine/infrastructure/process_control.py)、[secret_store.py](../src/reorder_engine/infrastructure/secret_store.py)、[settings_repository.py](../src/reorder_engine/infrastructure/settings_repository.py)、[desktop_paths.py](../src/reorder_engine/infrastructure/desktop_paths.py)、[engine_lock.py](../src/reorder_engine/infrastructure/engine_lock.py)
- 既有管线：[services/beta_pipeline.py](../src/reorder_engine/services/beta_pipeline.py)
- 测试：[tests/test_desktop_engine.py](../tests/test_desktop_engine.py)

前端与宿主：

- 前端：[apps/desktop/src/App.svelte](../apps/desktop/src/App.svelte)、[main.ts](../apps/desktop/src/main.ts)、[lib/desktop-controller.ts](../apps/desktop/src/lib/desktop-controller.ts)、[lib/engine-client.ts](../apps/desktop/src/lib/engine-client.ts)、[lib/contracts.ts](../apps/desktop/src/lib/contracts.ts)、[lib/desktop-controller.test.ts](../apps/desktop/src/lib/desktop-controller.test.ts)
- 宿主：[apps/desktop/src-tauri/src/lib.rs](../apps/desktop/src-tauri/src/lib.rs)、[engine_bridge.rs](../apps/desktop/src-tauri/src/engine_bridge.rs)、[main.rs](../apps/desktop/src-tauri/src/main.rs)、[tauri.conf.json](../apps/desktop/src-tauri/tauri.conf.json)、[capabilities/main.json](../apps/desktop/src-tauri/capabilities/main.json)

构建与文档：

- 脚本：[scripts/build_desktop_windows.ps1](../scripts/build_desktop_windows.ps1)、[stage_desktop_engine.py](../scripts/stage_desktop_engine.py)、[desktop_engine_entry.py](../scripts/desktop_engine_entry.py)、[smoke_desktop_engine.py](../scripts/smoke_desktop_engine.py)
- 文档：[desktop-design.md](desktop-design.md)、[desktop-status.md](desktop-status.md)、[product_plan.md](product_plan.md)、[diagrams/](../docs/diagrams/)

## 12. 0.3.0 新对象与界面状态

- [BuiltinDefaults](../src/reorder_engine/infrastructure/builtin_defaults.py)：只读公开库对象，加载 `app_root/defaults/manifest.json`，校验路径、文件大小、hash与条数。缺清单的隔离测试可用空库；存在但损坏明确失败。
- `EngineFacade` 与 `PackageProcessor` 共用 catalog；私人 `SecretStore` 独立，私人优先、有序去重组合，日志对两类密码脱敏。DTO只传版本、条数和开关。
- `ProcessingOptions.use_builtin_passwords=true`；`clean_builtin_keywords=false`。关键词只作用于成品顶层名，保留扩展名与内部结构，再经 `FileTransaction` 安全发布。
- [appearance.ts](../apps/desktop/src/lib/appearance.ts)：前端本地外观对象，校验 PNG/JPEG/WebP、2 MiB、data URL、解码尺寸与存储回读。没有引擎或 SQLite 依赖；类似 Java 的独立值对象加存储适配函数。
- 名称 Hoshiribbon 用于窗口和发行；内部 `reorder_engine`、`io.reorder.desktop` 保留，数据位置保持。
