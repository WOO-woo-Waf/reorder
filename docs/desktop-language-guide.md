# 桌面版语言指南：TypeScript · Svelte 5 · Rust 速通

> 面向读者：长期用 Python / Java，并熟悉 C++ 指针、栈与堆、生命周期概念的工程师。
> 目标：读完能直接读懂本仓库（Windows `D:\buff\reorder`，WSL `/mnt/d/buff/reorder`）里前端与桌面宿主的关键代码，而不是把它当成要从头学的两门语言。
> 依据：本文所有片段都取自本仓库实际源码并标注 `文件:行号`，没有虚构示例；实现若变化，以源码为准。
> 配套文档：[desktop-design.md](desktop-design.md)（职责、类图、协议）、[desktop-status.md](desktop-status.md)（已完成范围与检查证据）。

## 0. 先建立地图：这套软件由三个"世界"组成

看懂语法之前，先记住运行时被拆成三层，它们之间只能通过"序列化数据"通信：

| 层 | 语言 / 运行时 | 进程 | 职责 |
|---|---|---|---|
| 界面层 | TypeScript + Svelte 5，跑在 WebView 里 | 与宿主同进程内的 WebView 线程 | 展示状态、收集用户操作、发请求 |
| 宿主层 | Rust（Tauri 2） | 独立进程 | 管窗口、管对话框、启动并监管 Python 引擎、转发请求 |
| 引擎层 | Python（Pydantic + SQLite） | Python 子进程 | 真正的扫描、分组、解压、发布、归档 |

关键约束（贯穿全文，尤其见第 3.11 节）：**不同进程之间只能传序列化后的数据，不能传内存指针、对象引用或 Rust 借用**。C++ 里"传个指针过去"在本项目里没有对应写法，取而代之的是 JSON。

典型调用链（一个"扫描"按钮）：

```
App.svelte  →  DesktopController  →  EngineClient.request()
  →  Rust `engine_request` 命令  →  EngineBridge（写 stdin / 读 stdout）
  →  Python JsonRpcServer  →  EngineFacade.dispatch()  →  Pydantic 校验
```

下面分别讲你会在这些文件里遇到的语法点。

---

## 1. TypeScript：给 JavaScript 加一层"编译期"类型

TypeScript（TS）本质是 JavaScript 加上**只在编译期存在**的类型标注。类型检查在打包时完成，产物仍是普通 JS，运行时看不到类型。这一点和 Java/C++ 完全不同，是本项目多处设计的根因。

### 1.1 interface：结构类型，不是"必须实现的契约类"

`apps/desktop/src/lib/contracts.ts:4` 定义了设置的形状：

```ts
export interface ProcessingOptions {
  deep_extract: boolean; max_depth: number; min_archive_mb: number; final_single_mb: number;
  preserve_payload_names: boolean; recursive: boolean; tool_timeout_sec: number;
  max_output_gb: number; keep_workspace: boolean;
}
```

对 Java/C++ 读者的关键差异：

* TS 的 `interface` 是**结构类型（structural typing）**：只要一个对象的字段"长得够像"，就自动满足这个接口，不需要 `implements` 声明，也没有虚表。Java 的接口是"名义类型"，必须显式 `implements`。
* 因此 `settings.update` 请求里前端可以直接传一个匿名对象（`desktop-controller.ts:141` 附近的 `{ ...settings }`），Python 端照收——正确性靠字段形状匹配，而不是类继承关系。
* `number` 在 TS 里统一表示整数和浮点（都是 JS 的 64 位双精度），**没有** `i32/f64` 之分；需要明确整数范围时只能靠运行时校验（见 1.5）。

### 1.2 字面量联合类型：比枚举轻的"状态集合"

`contracts.ts:1` 用一长串字符串字面量描述包状态：

```ts
export type PackageState = 'queued' | 'preparing' | 'extracting' | 'publishing' | 'archiving' |
  'succeeded' | 'partial' | 'failed' | 'deferred' | 'cancelled' | 'interrupted' | 'needs_review';
```

`A | B | C` 叫**联合类型（union）**，意思是"取值只能是这几个之一"。它等价于一个受限枚举，但：

* 编译后完全消失，运行时就是一个普通字符串；`'succeeded'` 和 Java `enum.SUCCEEDED` 不同，不需要查表。
* 类型收窄（narrowing）：`if (x === 'failed')` 之后，TS 会把 `x` 的类型收窄到只剩 `'failed'`，和 C++ `switch` 的分支推导类似，但发生在编译期。
* 真正的"成员判断"要在运行时自己做，所以 `contracts.ts:32`、`contracts.ts:34` 用 `Set` 把"终态""可重试态"落成真实数据结构：

```ts
export const terminalJobs = new Set<JobState>(['succeeded', 'partial', 'failed', 'cancelled', 'interrupted', 'needs_review']);
export const retryable = new Set<PackageState>(['failed', 'deferred', 'cancelled', 'interrupted']);
```

`App.svelte:21` 用它判断任务是否还在跑：`const running = $derived(!!view.job && !terminalJobs.has(view.job.state));`。**类型只帮编译期，运行时要用的集合必须自己建**，这是本项目反复出现的模式。

### 1.3 泛型：`request<T>` 是怎么做到"返回类型可指定"的

`apps/desktop/src/lib/engine-client.ts:5`：

```ts
export interface EngineClient {
  request<T>(method: Method, params?: Record<string, unknown>): Promise<T>;
  openResult(jobId: string, path?: string): Promise<void>;
}
```

* `request<T>` 的 `<T>` 是**类型参数**，和 Java 泛型、C++ 模板同源：调用方写 `client.request<SystemInfo>('system.info')`（`desktop-controller.ts:44`），编译器就把返回的 `Promise` 元素类型认定为 `SystemInfo`，从而让后面的 `info.platform` 有类型。
* `Record<string, unknown>` 等价于"字符串键、任意值"的字典，类似 `Map<String, Object>` 或 `std::unordered_map<std::string, ...>`。
* **重要限制**：TS 泛型同样是编译期擦除的。`request<T>` 里的 `T` 在运行时完全不存在，它**不会**在运行时检查返回数据。真正的运行时校验发生在 Python 端（见 1.5）。
* `implements` 在 `engine-client.ts:9` 出现：`class TauriEngineClient implements EngineClient`。这里是类实现接口，TS 会检查方法签名是否齐全，但同样只在编译期。

### 1.4 Promise：等价于"一次性 Future"

TS/JS 的异步模型建立在 `Promise<T>` 之上，可以理解成"将来会给出 `T` 或失败的一次性盒子"。

* `Promise<T>` ≈ Java `CompletableFuture<T>` ≈ C++ `std::future<T>`（但可链式 `.then`）。
* `async`/`await` 是语法糖：`async function f(): Promise<T>` 内部可以用 `await` 把 `Promise` 拆开。`desktop-controller.ts:33` 的 `operation(action: () => Promise<void>)` 就是把一段异步操作包起来统一处理 `busy/error`。
* 与 Java 的差别：JS 是**单线程事件循环**，`await` 不会阻塞线程，只是挂起当前函数等结果。真正吃 CPU 或阻塞的工作必须挪到别的线程——这正是 Rust 端用 `spawn_blocking` 的原因（见 3.11 对 `lib.rs:20` 的说明）。
* `desktop-controller.ts:44` 的 `Promise.all([...])` 等价于 Java `CompletableFuture.allOf`：并发发起两个请求，一起等待。
* 错误处理用 `try/catch`（`desktop-controller.ts:35`），没有 Java 的受检异常概念，所有抛出的东西都能被 `catch`。

### 1.5 类型擦除 vs 运行时边界：项目的真实"闸门"在 Python

把上面几点合起来看，就是前端类型系统的核心事实：

> TS 的 `interface`、`type`、联合、泛型在编译产物里**全部消失**；`client.request<JobSnapshot>('jobs.get', ...)` 里的 `JobSnapshot` 只是一句"相信我"的注解。

因此本项目的正确性闸门不在前端，而在**跨进程的 Python 端**。你会在 `src/reorder_engine/application/models.py` 看到同一份数据模型的**运行时**版本：

```python
class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

class ProcessingOptions(Contract):
    deep_extract: bool = True
    max_depth: int = Field(default=4, ge=1, le=20)
    ...
    max_output_gb: int = Field(default=64, ge=1, le=1048576)
```

对照点：

* 这里的 `Contract` 用 `strict=True` 且 `extra="forbid"`，等于要求"字段齐全、类型精确、不许多余字段"，这与 TS 结构类型"多几个字段也 OK"的宽松恰好相反——**运行时比编译期更严格**。
* `Field(ge=1, le=20)` 是 Pydantic 的运行时区间校验，对应 TS 里根本没有的整数范围检查。
* `PackageState = Literal[...]`（`models.py:6`）是上一节联合类型的运行时镜像：TS 用 `'queued' | ...` 在编译期约束，Python 用 `Literal` 在运行时拒绝非法值。

请求进入 Python 时的入口在 `src/reorder_engine/infrastructure/json_rpc.py`，它把 Pydantic 的校验失败映射成协议错误：

```python
except ValidationError:
    return self.error(request_id, -32602, "INVALID_PARAMS", "参数类型或范围不正确。")
```

真正的分发在 `src/reorder_engine/application/facade.py`，每个方法都把原始 `params` 交给对应模型做运行时校验，例如：

```python
if method == "jobs.start":
    request = StartRequest.model_validate(params)
    return self.runner.submit(...).model_dump(mode="json")
```

**读代码时的结论**：TS 类型帮你写代码时少犯错；Python 模型决定线上是否接受一份数据。二者必须手工保持一致，改一处要同步改另一处。

### 1.6 小练习与常见错误（TypeScript）

练习：

1. 在 `contracts.ts` 里给 `JobSnapshot` 新增一个可选字段 `note?: string`，说明为什么加 `?` 后旧数据仍能通过类型检查。
2. 给定 `state: JobState`，用 `terminalJobs` 判断是否需要继续轮询；再解释为什么编译器不会替你完成这个运行时判断。

常见错误：

* **以为接口会在运行时生效**。删掉 `desktop-controller.ts:44` 的 `<SystemInfo>` 注解，代码照样跑；类型错了也只是编辑器报错，不会在运行时抛异常。
* **把联合类型当运行时枚举**。联合类型编译后消失，`if (state === PackageState.Succeeded)` 这种 Java 写法在 TS 里要改成字符串比较或 `Set`。
* **把泛型 `T` 当运行时类型参数用**，`request<T>` 拿不到 `T` 的构造函数，无法做 `T` 级别的运行时判断。

---

## 2. Svelte 5：声明式 UI + 一个"代理式"响应系统

Svelte 是一个编译式前端框架：`.svelte` 文件在构建时被编译成普通 JS，运行时不带庞大的虚拟 DOM 框架。Svelte 5 引入了以 `$` 开头的 **rune（符文）** 语法。本项目界面集中在 `apps/desktop/src/App.svelte`。

### 2.1 runes 总览

在 `App.svelte` 顶部能看到本项目的用法：

* `$state(...)`：声明**可变响应式状态**（`App.svelte:15-20`）。
* `$derived(...)`：声明**由其他状态算出的只读值**（`App.svelte:21-24`）。
* `$effect(...)`：声明**副作用**，在依赖变化时重新执行。**本项目当前没有使用 `$effect`**，等价职责由 `onMount` + 订阅回调承担（见 2.4）。
* `$state.snapshot(...)`：取响应式代理的**普通快照**（见 2.6）。

### 2.2 `$state` 与"代理"：为什么它像会自动刷新的变量

```ts
let view = $state<DesktopState>({ ready: false, busy: false, ..., inputs: [], ... });
let settingsOpen = $state(false);
let draft = $state<DesktopSettings | null>(null);
```

要点：

* `let view = $state({...})` 里的 `view` **不是**普通对象，而是一个被 Svelte 包了 **Proxy** 的响应式对象。你写 `view.inputs = [...]` 时，Svelte 在拦截 setter，自动记录"依赖 view.inputs 的界面需要重绘"。
* 对 C++/Java 读者的类比：这类似一个"带观察者的对象"，每次字段写入都可能触发回调；更像语言级的 `PropertyChanged` 通知，而不是普通结构体赋值。
* `$state(false)`、`$state('')` 这种原始值在组件/函数边界被包装成响应式变量；写 `settingsOpen = true` 就会让依赖它的 `{#if}` 区块更新。
* 代理是**深层的**：嵌套的 `view.job.packages[i].state` 变化也会被追踪。这带来了 2.6 的注意事项。

### 2.3 `$derived`：只读的"派生列"

```ts
const running = $derived(!!view.job && !terminalJobs.has(view.job.state));
const done = $derived(view.job?.packages.filter(p => terminalPackages.has(p.state)).length ?? 0);
const retryCount = $derived(view.job?.packages.filter(p => retryable.has(p.state)).length ?? 0);
```

* `$derived(expr)` 会**惰性重算**：只有当它内部的依赖（`view.job` 等）变化、且有人读取该值时，才重新求值。
* 它是**只读**的，不能 `done = 5`，因为值由公式决定。这和 Java 里"不提供 setter 的计算属性"一致。
* `?.`（可选链）和 `??`（空值合并）是常见 TS/JS 操作符：`view.job?.packages` 在 `view.job` 为空时安全返回 `undefined`，`?? 0` 再把 `undefined` 兜底成 `0`。等价于 Java `Optional` 链式取值再 `orElse(0)`，但写法更紧凑。

### 2.4 `onMount` 与 `$effect`：本项目为什么没用 `$effect`

`App.svelte` 里生命周期用 `onMount`：

```ts
onMount(() => {
  const unsubscribe = controller.subscribe(value => { view = value; });
  let stopped = false;
  const cleanups: (() => void)[] = [];
  void controller.initialize();
  if (isTauri()) {
    void getCurrentWebview().onDragDropEvent(...).then(cleanup => { if (stopped) cleanup(); else cleanups.push(cleanup); });
    ...
  }
  return () => { stopped = true; unsubscribe(); controller.dispose(); cleanups.forEach(fn => fn()); };
});
```

* `onMount(cb)` 在组件挂载后执行一次 `cb`。如果 `cb` **返回一个函数**，Svelte 会在组件卸载时调用它——这是资源清理（退订、关监听、销毁控制器）的标准位置，等价于 React 的 `useEffect` 返回清理函数，或 C++ 里"析构时释放"。
* `void fn()` 的 `void` 只是显式表示"我知道这是 Promise，但这里故意不 await"，压掉 lint 提醒，不是异步黑魔法。
* **`$effect` 是什么**：它会在其读取到的 `$state` 变化后重新运行回调，常用于"派生之外的副作用"（写 DOM、打日志、同步外部系统）。本项目因为已有 `DesktopController.subscribe` + 每秒 `setInterval` 轮询这套显式推送机制，**没有**再引入 `$effect`，避免两条响应路径互相打架。
* 读代码时不要去找 `$effect` 的用法；界面刷新靠的是 `view = value` 这次赋值触发代理更新。

### 2.5 由 controller 推送到界面的数据流

`App.svelte` 不直接调用引擎，而是订阅控制器：

```ts
const controller = new DesktopController(new TauriEngineClient());
...
const unsubscribe = controller.subscribe(value => { view = value; });
```

`desktop-controller.ts:25` 的订阅契约是"订阅时立刻推一次当前状态，返回一个退订函数"：

```ts
subscribe(listener: (state: DesktopState) => void): () => void {
  this.listeners.add(listener); listener(this.state);
  return () => { this.listeners.delete(listener); };
}
```

控制器内部每次改状态都走 `update`（`desktop-controller.ts:29`），用展开运算符合成一个**新对象**再广播：

```ts
private update(values: Partial<DesktopState>): void {
  this.state = { ...this.state, ...values };
  for (const listener of this.listeners) listener(this.state);
}
```

`...this.state` 是对象展开（浅拷贝），`Partial<DesktopState>` 表示"每个字段都可选"。对 Java 读者的类比：这是手工实现的不可变快照 + 观察者，只是用展开运算符代替 `new State(...)`。

### 2.6 代理与 `$state.snapshot`：本项目最容易踩的坑

`$state` 返回的是代理对象。当你把代理交给**期待普通对象**的 API 时，可能出错——典型场景是 `structuredClone` 和跨进程序列化。

本项目在编辑与保存设置时正是这样处理的：

```ts
function editSettings() {
  if (!view.settings) return;
  draft = structuredClone($state.snapshot(view.settings.settings)); settingsOpen = true;
}
...
async () => { if (draft) await controller.saveSettings($state.snapshot(draft)); ... }
```

* `$state.snapshot(x)` 返回一个**去代理的普通副本**：深层代理会被剥掉，得到可以安全 `structuredClone`、可 `JSON.stringify`、可跨进程传输的纯数据。
* 如果直接把代理传给 `structuredClone`，某些环境会抛 `DataCloneError`（代理不可被结构化克隆）；即使不抛，跨进程 `postMessage`/`invoke` 也可能失败。`$state.snapshot` 就是官方给出的避坑手段。
* 经验规则：**任何"要离开 Svelte 响应式世界"的动作（克隆、持久化、发 IPC），先 `$state.snapshot`。**

### 2.7 BitsUI：把无障碍对话框当"组件族"用

设置面板用了 BitsUI 的 Dialog（`App.svelte` 底部）：

```svelte
<Dialog.Root bind:open={settingsOpen}>
{#if draft}<Dialog.Portal><Dialog.Overlay class="modal-backdrop" />
  <Dialog.Content class="settings-dialog">
    ...
    <Dialog.Title class="settings-title">处理设置</Dialog.Title>
    <Dialog.Close ...><X size={17} /></Dialog.Close>
  ...
</Dialog.Content></Dialog.Portal>{/if}
</Dialog.Root>
```

* BitsUI 是一组无样式、可访问性（a11y）就绪的组件，类似 shadcn-svelte 的底座思路：只给行为，样式自己写。
* `Dialog.Root` / `Dialog.Portal` / `Dialog.Overlay` / `Dialog.Content` 是**同一组件的不同部位**（compound component，组合式组件），类比 Java 里"一个控件由多个子部件构成"，但用命名空间点号暴露。
* `bind:open={settingsOpen}` 是 Svelte 的**双向绑定**：对话框内部把 open 状态写回 `settingsOpen`，组件里也能读它。等价于 JavaFX 的 `bindBidirectional(property)`，但固定在某个 `$state` 变量上。
* `{#if draft}` 表示"只有在 draft 存在时才渲染 Portal"，避免空数据分支。
* `bind:checked`、`bind:value`（设置表单里大量出现）同理：表单控件与 `$state` 双向同步。注意 `bind:value={draft.options.max_depth}` 会把输入值写回代理对象，因此保存时要 `$state.snapshot(draft)`。

### 2.8 小练习与常见错误（Svelte 5）

练习：

1. 把 `running` 改写成普通函数并说明为什么它会失去"状态变化自动重算"的能力。
2. 解释 `App.svelte:52` 的 `onMount` 返回的清理函数里，为什么既要 `unsubscribe()` 又要 `controller.dispose()`。

常见错误：

* **把 `$state` 对象当普通对象传递**。`structuredClone(view.settings.settings)` 可能报 `DataCloneError`，正确写法是 `structuredClone($state.snapshot(...))`。
* **以为 `$derived` 是变量赋值**。它是表达式，不能手动写。
* **忘了清理订阅**。不返回清理函数会导致组件卸载后控制器仍向已销毁的界面推送，表现为内存泄漏或对旧 DOM 的操作。
* **想当然使用 `$effect`**。本项目用显式订阅 + 轮询，若再加入 `$effect` 容易造成重复请求或循环更新。

---

## 3. Rust：没有 GC，用所有权在编译期管内存

Rust 的宿主代码在 `apps/desktop/src-tauri/src/`。核心文件是 `engine_bridge.rs`（进程与 IPC）与 `lib.rs`（Tauri 命令与状态）。对 C++ 读者，Rust 的目标是"像 C++ 一样无运行时开销，但由编译器而不是人保证内存安全"。

### 3.1 `struct` 与 `impl`：数据与行为分离

```rust
pub struct EngineBridge {
    sender: Mutex<Option<mpsc::SyncSender<Vec<u8>>>>,
    child: Arc<Mutex<Option<Child>>>,
    pending: Pending,
    sequence: AtomicU64,
    health: Arc<Health>,
}

impl EngineBridge {
    pub fn start(app: &AppHandle, portable: bool) -> Result<Self, String> { ... }
    pub fn request(&self, method: &str, params: Value) -> Reply { ... }
    pub fn stop(&self) { ... }
}
```

* `struct` 只放数据，方法放在 `impl` 块里。没有构造函数关键字，约定用关联函数 `EngineBridge::start(...)` 充当"工厂/构造器"（`engine_bridge.rs:31`）。
* 字段默认**私有**，写了 `pub` 才对外可见。这里结构体本身 `pub`，字段不写 `pub` 就仅本模块可见。
* Rust **没有继承**。复用靠组合与 trait（见 3.2），`EngineBridge` 直接"持有"子进程句柄而不是"是一个"进程管理器，和 [desktop-design.md](desktop-design.md) 里"组合优先"的原则一致。
* `&self` 表示以不可变借用调用（内部仍可通过 Mutex 管理可变状态，见 3.6）；`start(app: &AppHandle, portable: bool)` 里的 `&AppHandle` 同样是借用，不夺走所有权。

### 3.2 `trait`：接口 + 编译期多态

本项目里 trait 用得克制，但你要认识它：

* `impl Drop for EngineBridge`（`engine_bridge.rs:160`）就是"为某类型实现某 trait"。`Drop` 是标准库 trait，语义见 3.10。
* trait ≈ Java `interface` / C++ 抽象基类，但默认是**静态分发**：编译时就确定调用哪个实现，没有虚表开销；需要运行期多态时才用 `dyn Trait`（本项目未用）。
* 与 Java 的差别：trait 可以给已有类型补实现（不受类定义处限制），也可以提供默认方法；在 Java 里需要接口 + 默认方法才能做到。

### 3.3 `Option<T>`：把"可能没有"编进类型

```rust
pub struct EngineBridge {
    sender: Mutex<Option<mpsc::SyncSender<Vec<u8>>>>,
    child: Arc<Mutex<Option<Child>>>,
    ...
}
```

* `Option<T>` 是 `Some(T)` 或 `None`。项目里 `sender`/`child` 用它表示发送队列或引擎句柄已被释放；应用层 `EngineState.engine` 的 `None` 表示尚未持有桥接实例。
* 对比：Java 常用 `null`、C++ 常用空指针。Rust 的普通引用不能为 null，安全接口通常用 `Option` 表示缺失；原始指针仍可以为空，解引用涉及 unsafe。
* `is_some_and(...)`（`lib.rs:31`）是 `Option` 的常用组合子：`Some` 时对内部值求条件，类似 Java `Optional.filter(...).isPresent()`。
* `unwrap_or_else(...)`、`and_then(...)`、`map(...)` 都是同族方法，把"取值 + 兜底"写成链式，避免层层 `if let`。

### 3.4 `Result<T, E>` 与 `?` 运算符：没有异常的错误处理

```rust
type Reply = Result<Value, String>;

pub fn start(app: &AppHandle, portable: bool) -> Result<Self, String> {
    let resource_dir = app.path().resource_dir().map_err(|_| "无法定位软件资源目录")?;
    ...
}
```

* `Result<T, E>` 是 `Ok(T)` 或 `Err(E)`。Rust **没有 try/catch**，可恢复错误都用返回值表达，调用方必须显式处理（要么 `?`，要么 `match`，要么 `unwrap`）。
* `?` 运算符：如果值是 `Err`，立即把它作为当前函数的返回值向上抛；如果是 `Ok`，取出内部值继续。等价于 Java 里"这段出错就 `throw` 给上层"，但类型是显式的、在签名里可见。
* `map_err(|_| "…")?` 先把底层错误映射成人类可读的字符串，再用 `?` 向上抛。`|_| ...` 是闭包（匿名函数），`_` 表示忽略入参——类似 Java lambda `e -> "…"`，但 `_` 明确丢弃。
* 项目里 `Reply = Result<Value, String>`（`engine_bridge.rs:14`）对外统一用字符串错误，最终经 Tauri 命令回到前端 `catch`（对照 1.4）。

### 3.5 所有权与 `move`：编译期的"谁负责释放"

线程里能直接看到所有权移动：

```rust
let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
let replies = pending.clone();
thread::spawn(move || {
    let mut reader = BufReader::new(output);
    loop { ... }
});
```

* Rust 每个值有唯一所有者；赋值或传参会**移动（move）**所有权，原变量此后不可用。这与 C++ `std::move` + `unique_ptr` 的直觉接近，但 Rust 是默认行为且由编译器强制。
* 闭包前的 `move` 关键字表示：闭包**按所有权捕获**外部变量（`output` 的 `ChildStdout` 被移进新线程）。不用 `move` 的话，闭包借用栈上的变量，而线程可能比栈帧活得久，编译器会拒绝——这正是 C++ 里"把局部变量地址传给线程"的悬垂指针风险，Rust 在编译期就挡住。
* `replies = pending.clone()`：因为后面主线程还要用 `pending`，这里克隆一份引用计数交给线程（见 3.7），而不是移动走。对 C++ 读者：在 `Arc` 上 `clone` 约等于 `shared_ptr` 拷贝，增加引用计数。

### 3.6 `&` 与 `&mut`：借用，不夺所有权

```rust
pub fn allowed_method(method: &str) -> bool { ... }
pub fn request(&self, method: &str, params: Value) -> Reply { ... }
```

* `&T` 是**不可变借用**（只读）：可以同时存在多个，但不能在借用期间修改被借对象。
* `&mut T` 是**可变借用**（可写）：同一时刻只能有一个，且不能与任何不可变借用共存。
* 这套规则（别名与可变二选一）在编译期消除数据竞争，是 Rust 不用 GC 还能并发安全的根基。C++ 里"传引用还是传值"要自己权衡，Rust 用 `&` / `&mut` / 值三种形态把它变成类型系统的一部分。
* `&str` 是"字符串切片借用"，等价于"只读的字符视图"；`params: Value` 不写 `&` 就表示**按值传入**（这里 `serde_json::Value` 被移动进函数）。

### 3.7 `Arc`：可跨线程共享的引用计数

```rust
type Pending = Arc<Mutex<HashMap<u64, mpsc::Sender<Reply>>>>;
...
pub struct EngineState {
    engine: Mutex<Option<Arc<EngineBridge>>>,
    portable: bool,
}
```

* `Arc<T>` = 原子引用计数智能指针，可安全地在多线程间共享所有权（单线程版是 `Rc<T>`）。类比 C++ `std::shared_ptr`，但引用计数是原子的且由类型系统保证线程安全。
* 为什么用 `Arc<EngineBridge>`：读取线程、Tauri 命令线程、退出事件都可能访问同一个桥接对象，谁都可能"最后离开"，所以用引用计数而不是唯一所有权。

### 3.8 `Mutex`：共享可变状态的进出证

```rust
sender: Mutex<Option<mpsc::SyncSender<Vec<u8>>>>,
pending: Pending,
...
self.pending.lock().map_err(|_| "引擎请求锁异常")?.insert(id, sender);
```

* `Mutex<T>` 保证同一时刻只有一个线程能拿到内部数据的可变访问。`lock()` 返回 `Result`（锁可能被"中毒"），所以后面接 `map_err(...)?`。
* 对比 C++ `std::mutex` + `std::lock_guard`：Rust 的 `Mutex` **把数据和锁绑在一起**，锁存在期间才能访问数据，不会出现"忘了加锁就访问"。锁守卫（guard）离开作用域自动释放，见 3.10。
* `Arc<Mutex<...>>` 是"多线程共享 + 内部可变"的经典组合；本项目用它维护"请求 id → 等待回复的通道"这张表。

### 3.9 `mpsc` 通道：线程间的消息管道

```rust
let (sender, receiver) = mpsc::channel();
self.pending.lock()...?.insert(id, sender);   // 把 sender 存进待回复表
...
let result = receiver.recv_timeout(Duration::from_secs(60));
```

* `mpsc` = multiple producer, single consumer 通道。这里主线程发请求后，把 `Sender` 存进表里，然后 `recv_timeout` 等回复；读取线程收到对应 id 的响应时，通过同一个 `Sender` 把结果送回。
* 类比：Java `BlockingQueue` + `Future`，或 Go channel。区别是 Rust 通道随所有权一起被移动，谁持有 `Sender` 谁才能发消息。
* `recv_timeout` 避免无限等待——超时返回 `Err`，代码再翻译成给用户的"引擎响应超时"提示（`engine_bridge.rs:135` 附近）。这与 [desktop-design.md](desktop-design.md) 里"有界等待、不无限阻塞"的约定一致。

### 3.10 `Drop` 与 RAII：析构即清理

```rust
impl Drop for EngineBridge { fn drop(&mut self) { self.stop(); } }
```

* `Drop` 类似 C++ 析构函数。Rust 与 C++ 的正常作用域退出、异常展开都会释放相应资源；abort、强杀、故意遗忘对象等路径不保证执行析构。Java `AutoCloseable` 需要 try-with-resources 等机制，不能把它等同于垃圾回收。
* `EngineBridge` 正常被丢弃时会调用 `stop()`：关闭发送队列，让 writer 释放 stdin，再等待引擎安全收尾，必要时结束子进程。这种资源归属与释放方式沿用了 RAII。
* 锁守卫（`MutexGuard`）、文件句柄、`ChildStdin` 都靠 `Drop` 自动收尾；不需要手动 free/close。
* 额外入口在 `lib.rs:54`：应用退出（`RunEvent::Exit`）时主动取走并 `stop()` 引擎，作为对 `Drop` 之外的一次显式兜底。

### 3.11 跨进程边界：不能传指针，只能传值

这是全文最重要的一条。`EngineBridge`（Rust）与 `EngineFacade`（Python）跑在不同进程里，[desktop-design.md](desktop-design.md) 明确写着"跨进程只能传序列化 DTO，不能共享 Python 对象指针或 Rust 引用"。代码里的体现：

* Rust 侧只把 `serde_json::Value` 编码成一行 JSON 写进子进程 stdin：`serde_json::to_vec(&json!({...}))`（`engine_bridge.rs:116` 附近）。
* 读取线程反过来把 stdout 的一行反序列化成 `Value`，再提取 `result`/`error`（`engine_bridge.rs:78` 附近）。
* 前端 → Rust 的 Tauri 命令 `engine_request` 同样只收 `method: String` 与 `params: Value`（`lib.rs:18`）；真正阻塞的收发被 `spawn_blocking` 挪出 WebView 事件循环（`lib.rs:20`）。

为什么不能传指针：两个进程有各自独立的虚拟地址空间，A 进程内部的地址在 B 进程里没有意义；而且 Rust 的借用/所有权、Python 的对象生命周期都无法跨越进程边界被对方理解。JSON 值（`Value`）是"可序列化的自有数据"，不含对方内存地址，因此是唯一安全的通用载体。

对照：C++ 里可以把指针丢进共享内存并用偏移寻址，但那是需要专门设计的颠覆性方案；本项目选择"复制序列化数据"，代价是拷贝，收益是简单和安全。

### 3.12 小练习与常见错误（Rust）

练习：

1. 解释 `thread::spawn(move || { ... })` 如果去掉 `move` 会报什么错，并对应到 C++ 里的哪种 bug。
2. 把 `impl Drop for EngineBridge` 删掉后，说明"引擎进程可能在应用退出后仍存活"的原因，以及 `lib.rs:54` 的退出口为什么能补上。

常见错误：

* **把 `&mut` 和多个 `&` 混用**。Rust 会拒绝"同时持有可变借用和不可变借用"，这是编译期防数据竞争，不是麻烦。
* **忘了 `?` 就 `unwrap`**。生产路径上 `unwrap()`/`expect()` 会在错误时 panic；可恢复错误应由 `?` 向上传递成给用户的提示。
* **以为 `clone()` 是深拷贝**。在 `Arc` 上 `clone` 只加引用计数；在 `Value`/`Vec` 上才是真正的数据拷贝。
* **把 `Mutex` 当可选**。Rust 的 `Mutex<T>` 把锁和数据绑定，必须 `lock()` 才能碰数据，这是设计特性不是限制。
* **想跨进程"传引用"**。做不到，必须序列化成值（见 3.11）。

---

## 4. 三种语言对照速查表

| 概念 | Python | Java | C++ | TypeScript | Rust |
|---|---|---|---|---|---|
| 类型存在期 | 运行时有类型（Pydantic 强校验） | 运行时有类型 | 编译期 + 部分运行时 | 仅编译期擦除 | 仅编译期 |
| "枚举 / 状态集合" | `Literal` / `Enum` | `enum` | `enum class` | 字符串字面量联合 | `enum`（更强） |
| 可空 | `None` / `Optional` | `null` | 空指针 | `undefined` / `null` / `x?` | `Option<T>`（无 null） |
| 错误 | 异常 | 异常 | 返回码 / 异常 | `throw` + `Promise` reject | `Result<T,E>` + `?` |
| 资源释放 | `with` / GC | GC / try-with-resources | 析构 / RAII | GC（`onMount` 清理） | `Drop` + RAII（默认） |
| 并发共享 | GIL / 锁 | 锁 | 锁 + 手动 | 单线程事件循环 | `Arc<Mutex<T>>` |
| 进程间通信 | 管道 / JSON | 管道 / JSON | 共享内存 / 管道 | 前端经 IPC | JSON over stdin/stdout |
| 内存地址传递 | 不可 | 不可 | 可以（危险） | 不可 | 进程内借用，跨进程不可 |

---

## 5. 最小开发命令（本项目实际入口）

以下命令取自 [desktop-status.md](desktop-status.md) 第 8 节记录的环境与入口，供你本地自查。**本指南未执行这些命令**（任务范围限定只写文档），实际可用性以该文档与源码为准；Windows 完整构建请遵循文档中的 MSVC/Rust/Node 前置条件。

### 前端（目录 `apps/desktop`）

| 目的 | 命令 | 说明 |
|---|---|---|
| 类型检查 | `npm run check` | 跑 `svelte-check`，纯编译期，不改文件 |
| 单元测试 | `npm run test` | `vitest run`，覆盖 `DesktopController` 的两个用例 |
| 静态构建 | `npm run build` | `vite build`，产出前端静态资源 |
| 浏览器预览 | `npm run dev` | `vite --host 127.0.0.1`；普通浏览器没有本地文件能力，`TauriEngineClient` 会提示 |

### Python 引擎（仓库根）

```bash
# 开发态直接以 JSON-RPC 方式启动引擎（读 stdin、写 stdout）
PYTHONPATH=src python -m reorder_engine.desktop_engine --data-root /tmp/reorder-dev-state --session-secrets
```

### Rust 宿主（目录 `apps/desktop/src-tauri`）

| 目的 | 命令 |
|---|---|
| 检查编译（含测试目标） | `cargo check --tests` |
| 构建发布 | `cargo build --release --features custom-protocol --jobs 2`（Windows 需先初始化 MSVC；最终包优先用 `scripts/build_desktop_windows.ps1`） |

### 实际桌面开发运行

Windows 上把 `REORDER_PYTHON` 指向构建用的 venv Python，在已初始化 MSVC/Rust 环境的 `apps/desktop` 下执行 `npm run tauri -- dev`。普通浏览器预览无法处理本地文件。

> 平台提醒：本项目前端依赖是在 WSL 里用 npm 安装的；Windows 原生构建会重装依赖目录，不要把 WSL 的 esbuild/CLI 二进制直接搬到 Windows 用。

---

## 6. 收尾：三句话总结

1. **TypeScript 只保护编译期**。interface、联合、泛型、`Promise<T>` 都会被擦除，真正拒绝非法数据的是 Python 端的 Pydantic 模型（`models.py` 的 `strict=True` + `extra="forbid"` + `Field` 区间）。
2. **Svelte 5 的 `$state` 是深层代理**。读值很方便，但任何"离开响应式世界"的动作（克隆、IPC、持久化）都要先 `$state.snapshot`；本项目用 `onMount` 订阅 + 清理，而不是 `$effect`。
3. **Rust 用所有权替代 GC**。`Option` / `Result` / `?` / `&` / `&mut` / `Arc` / `Mutex` / `Drop` 共同保证"无悬垂、无数据竞争、自动释放"；跨进程只有一条路——把数据序列化成 JSON，指针和引用都不能穿越进程边界。

如需沿一个按钮追到具体类与文件，见 [desktop-code-guide.md](desktop-code-guide.md)；操作与验收步骤见 [desktop-user-guide.md](desktop-user-guide.md)，实际交付进度见 [desktop-status.md](desktop-status.md)。
