# 桌面软件实施状态与新聊天交接

更新时间：2026-10-08（Asia/Shanghai）。Windows x64 手动桌面首版已具备 EXE、NSIS、便携 ZIP、OO 图与制作方指南，本轮更新星绫解封 0.3.0。真实文件、体验及干净机器安装仍待用户人工验收，整体产品目标尚未验收完成。

方案：[product_plan.md](product_plan.md)。正式设计：[desktop-design.md](desktop-design.md)。

## 0.0 2026-10-08 星绫解封 0.3.0（当前切片）

本节优先于下方 0.2.0 和迁移记录。用户再次授权实现、Windows构建、commit和重试push；真实文件、GUI与安装体验继续由用户人工验收。内部 `reorder_engine` 与 `io.reorder.desktop` 保留，旧便携目录及 `data/` 保留。

### 已批准与实现

- 显示名 **星绫解封 · Hoshiribbon**，介绍“把压缩的次元，一层层展开”；日系紫粉界面、新图标、默认原创成年少女星夜背景。
- 可选择本地 PNG/JPEG/WebP（2 MiB），调节遮罩、模糊、无背景与恢复默认；外观仅在 WebView 本地存储。
- Git公开词库作为固定数据资源：115密码、8关键词；私人密码优先、去重后追加默认密码。内置密码默认开启，私人库清空不删除内置库，DTO只含条数/开关，日志对两库脱敏。
- **用户明确选择关键词清理默认关闭**。开启后只清理成品顶层名，保留扩展名、包内层级、原件及归档名；非法/空名保留，同名安全分配。
- Bandizip CLI 7.40.0.1：所有者声明已取得官方书面分发许可，本轮据此固定捆绑 `bz.exe`、两个 Ark DLL、公开 EULA/Ark/LGPL文本与授权依据说明；没有审阅许可函原文。GUI/更新器/宿主私人配置排除。
- 7-Zip 25.01 / UnRAR 7.13 保持；所有解压路径先经7-Zip安全预检，不能因 Bandizip 支持 ALZ/EGG 就宣称项目端到端可用。见 [格式指南](desktop-format-support.md) 与 [视觉说明](desktop-artwork.md)。
- 交接界面风险已修：未提交密码文本统一清理；取消按 accepted 显示提示；运行时拖入、忙时拒绝操作有反馈。

### 本轮证据与交付入口

新任务使用独立 V1 正文及显式 DeepSeek/high，未恢复旧子线程。证据在 ignored `artifacts/desktop/hoshiribbon-20261008/`。未受影响的历史核心检查复用；外观审查修复后已定向重查，新增检查只覆盖变动风险；当前已完成以下定向检查（不相加声称全套）：

| 范围 | 命令/方式 | 结果与证据 |
| --- | --- | --- |
| 外观导入/存储边界 | `npx --no-install vitest run src/lib/appearance.test.ts` | exit 0，23 passed；`main/appearance-final-test.log`（加入头部尺寸/像素上限，原20项证据留作历史） |
| 内置库与成品改名 | `PYTHONPATH=src python -m pytest tests/test_builtin_desktop_defaults.py -q` | exit 0，34 passed；`defaults/pytest-builtin-defaults.txt` |
| 既有设置/脱敏 | `pytest tests/test_desktop_engine.py::test_rpc_invalid_params_and_no_password_persistence -q` | exit 0，1 passed；`defaults/pytest-existing-settings-info.txt` |
| 工具/词库暂存与发行命名 | `pytest tests/test_desktop_tool_staging.py tests/test_desktop_default_staging.py -q` | exit 0，97 passed；`packaging/packaging-slice-evidence.json` |
| 取消/忙时/启动恢复 | `npx --no-install vitest run src/lib/desktop-controller.test.ts` | exit 0，7 passed；`main/controller-test.log`。首次失败为测试期望仍写0.2.0，修正fixture后通过，初始日志保留 |
| 前端类型/产物 | `npm run check`、`npm run build` | 均exit 0；0 errors/0 warnings；`main/frontend-final-results.json` |
| Windows 引擎重新冻结 | Windows venv Python执行 `scripts/stage_desktop_engine.py` | exit 0；含工具与defaults严格校验；`main/engine-stage-result.json` 与 `main/freeze.log` |

Windows最终release/NSIS/ZIP已构建成功（exit 0，release约1m33s），已嵌入外观审查修复后的最终前端；最终包完整性由 `main/windows-build-final-result.json` 与 `package-validation/final-result.json` 记录证明。本表不代替实际内容或GUI验收。

最终资源已通过有限包校验：`rtk proxy bash run_final_check.sh final` exit 0，102项结构断言与11项NSIS断言无错误；ZIP CRC和773个清单文件的大小/hash一致，NSIS/便携 engine 157、docs 22、licenses 591逐文件差异为0。只解包安装器，未执行安装/GUI/内容。完整记录 `package-validation/final-result.json`，复用冻结help证据。

本轮最终便携目录为 `artifacts/desktop/Hoshiribbon-0.3.0-windows-x64-r2z6_rpx/`；同名无后缀目录为构建中间树，最终验收请使用此目录或下面的ZIP。安装包与ZIP在 `artifacts/desktop/`：

- ZIP SHA-256：`5d81f79753519329c39d1542e2231ad721ec80053ff2c499300de020596d1e0e`。
- NSIS SHA-256：`b09c0f1a98514492a4878feaf1f04abd972bd4ec912ec253a77a5da16097ab03`。

发行包内实施状态为封包时快照；本仓库本节另补录最终检查结果，其他操作与格式指南一致。

Windows发行名称：`Hoshiribbon.exe`、`Hoshiribbon_0.3.0_x64-setup.exe`、`Hoshiribbon-0.3.0-windows-x64.zip`；实际路径和hash以最终打包记录为准。源码/资源变动后重新冻结引擎、构建前端与Rust宿主，不以旧内容smoke代替新版本业务验收。

独立UI审查的尺寸上限、保存归一化与存储提示问题均已修复并复核闭环；引擎独立审查无阻断。证据见 `ui-review/findings-ui-review.md`、`engine-review/review-disposition.json`。公开库含10条不足4字符的密码，脱敏有意覆盖完整命中片段，可能使相应日志片段隐藏；不改变成品与原件内容。损坏内置资源会报错，需恢复安装资源；不静默降级。当前冻结Python3.13.5的junction检查可用，较低Python开发环境未验证。

### Git 与下一步

功能提交 `60e13228f52e38a7cc6264ba3006d67e51b9f5fd` 已推送到 `origin/main`，远程提交号独立核对一致；上一轮固定UnRAR提交也一并送达。WSL初次TLS握手失败，Windows Git缺登录凭据，随后WSL保留代理/证书校验并使用TLS1.2成功；记录在 `main/push-wsl-retry-result.json`、`main/remote-main-check.json`。源码、公开背景与锁定Bandizip输入随Git同步；WindowsEXE/NSIS/ZIP位于上述本地交付入口。

下一步为用户按副本真实文件、界面、安装与历史验收清单核对；收到明确反馈后再实施相应修复，不重复本轮有效机器检查。整体产品仍未人工验收完成。

### 人工验收仍未完成

按 [用户指南](desktop-user-guide.md) 新增清单核对：默认背景/本地换图/重启保存/缩放/窄窗，内置与私人库分别启停，关键词开关的成品改名，真实 ZIP/AES ZIP/7z/RAR/分卷/错密码/嵌套，以及安装卸载与已有历史迁移。浏览器、监听与Agent继续留后续。安装包未签名，离线干净机器仍需要系统WebView2；工程检查不等于这些验收已通过。

## 0. 本轮继续状态（优先于下方迁移快照）

用户已授权继续已批准方案、构建并按目标 commit / push。下方第 1–8 节保留迁移前事实，其中“未提交”“尚未构建”“仅交接”均为当时状态；最新结果以本节为准。

### 实现与设计

- main 原有修改完整保留，续作快照在 `artifacts/handoff/continuation-20261007/`；按目标提交，没有 reset 或回滚原工作。
- Tauri 2 + Svelte 5 + TypeScript + Vite、Rust 宿主、Python 引擎、SQLite 已落地；手动批处理、只读计划、取消、失败重试、历史、密码/工具设置、登记结果打开已实现。
- 已完成 3 个独立 DeepSeek/high 审查及对应修复：设置代理复制、窗口关闭权限、初始化恢复、完整历史、忙时取消竞态；宿主失效连接重建、有界写队列与超时；日志故障隔离、中断包状态、工作区登记、复制清理和原位归档重试。
- 主线程进一步修正动作恢复按 `(job_id, package_id)` 定位，避免旧任务记录污染同包 ID 的新重试；工具返回信息保留脱敏尾部错误。
- 制作方指南：[代码阅读](desktop-code-guide.md)、[语言对照](desktop-language-guide.md)；使用与验收：[用户指南](desktop-user-guide.md)。
- OO 架构、模块、类、时序、任务和包状态共 6 张图已对齐源码并逐图视觉核查；图源/SVG 在 `docs/diagrams/`，渲染入口 `scripts/render_desktop_diagrams.py`。
- Windows Rust 可达图 254 个 registry 包均有许可文本；另 208 个锁条目不在该图。Python 19 项、7-Zip 及主要 npm 文本随包；2 个 npm 传递依赖仍只有 MIT 元数据，来源快照与限制见 [第三方说明](desktop-third-party.md)。
- 新聊天 DeepSeek/high 正文链路已验证：A final `CHAIN-REORDER-20261007-A:49`，B final `CHAIN-REORDER-20261007-B:64`；元数据两轮均为 `deepseek-v4.1-flash/high`。静态 runtime 检查退出 0、compatible=true；没有恢复迁移前旧子线程。

### 机器检查（复用最终状态有效证据）

命令均带 `rtk`；日志/产物在 ignored 的 `artifacts/desktop/`，未随 Git 推送。下表列出范围与实际退出结果，不能解释成全库测试或真实业务验收。

| 范围 | 命令 / 方式 | 结果与证据 |
|---|---|---|
| 前端最终状态 | `npm run check`、`npm run test`、`npm run build`，目录 `apps/desktop` | 各退出 0；0 errors / warnings、6 tests；`frontend-final/{check,test,build}.log` |
| 宿主协议、健康状态与队列 | Windows `cargo test --lib --jobs 2`，默认资源配置 | 退出 0，8 passed；`rust-tests-final.log` |
| Python 协议与生命周期修复 | `PYTHONPATH=src /usr/local/bin/python -m pytest tests/test_desktop_review_regressions.py tests/test_desktop_engine.py -q` | 退出 0，29 passed；`python-review-final.log`。随后恢复键修改仅重查下一行范围，复用其他未受影响证据 |
| 最终恢复键回归 | 同一 Python/环境，`pytest tests/test_desktop_review_regressions.py -k 'recover or terminal' -q` | 退出 0，4 passed / 8 deselected；本回合 session 44366 最终输出、回归测试源码 |
| 最终冻结引擎 | Windows build venv 运行 `stage_desktop_engine.py` 后运行 `smoke_desktop_engine.py --engine …reorder-engine.exe --evidence …native-engine-final.json` | 退出 0；4 组 / 8 原件成员，ZIP/AES ZIP/PDF+ZIP 恢复/7z 分卷、原件 bytes/hash、危险路径与密码脱敏；`engine-build/freeze.log`、`native-engine-final.json` |
| 最终 Windows release | Tauri build，`--features custom-protocol`、Cargo jobs=2，复用有效前端 dist 和冻结引擎 | 编译成功，2m54s；`windows-build-final.log`。同次 NSIS 下载中断不能当作打包成功，成功 bundling 另见下一行 |
| NSIS 安装包 | Tauri `bundle --features custom-protocol --bundles nsis --no-binary-patching` | 退出 0；`windows-bundle-final.log`。官方 NSIS/辅助 DLL 下载按上游 hash 校验后进入本项目 `target/.tauri` 缓存，`nsis-tool-evidence.json` |
| 原生桌面启动与关闭 | Windows PowerShell `smoke_desktop_window.ps1 -Exe …ReOrder.exe -Evidence …desktop-window-smoke.json` | 退出 0；窗口“归序 · ReOrder”响应、便携数据库、1 个自有引擎、空闲关闭 exit 0、引擎已退出；`desktop-window-smoke.json` |
| 首次便携资源完整性 | ZIP 全部条目的 manifest/size/SHA-256/CRC、敏感文件名排除 | 退出 0，751 文件；`package-check-initial.json`。最终文档/许可资源将更新，运行时字节保持同一版本 |

原生窗口检查没有操作真实文件、拖放、设置或密码；原生引擎 smoke 没有模拟强制崩溃。Rust 测试和 Python 恢复模拟不能替代 Windows 真实强杀后的体验。

### Windows 0.2.0 交付入口

| 产物 | 仓库内路径 | 使用方式 |
|---|---|---|
| 新便携目录 | `artifacts/desktop/ReOrder-preview-0.2.0/` | 保留整个目录；`ReOrder.exe` 用用户数据目录，`Start-Portable.cmd` 用旁边 `data/` 与会话密码 |
| 安装包 | `artifacts/desktop/ReOrder_0.2.0_x64-setup.exe` | 当前用户 NSIS 安装；未签名，缺 WebView2 时需要联网或预装 |
| ZIP | `artifacts/desktop/ReOrder-0.2.0-windows-x64.zip` | 解压到新目录；包含完整 Python 引擎、工具、指南和许可 |
| 完整性 | `artifacts/desktop/SHA256SUMS.txt`、目录内 `package-manifest.json` | 前者校验发行文件，后者给出全部文件大小与 SHA-256 |

- 打包检查已补齐：必须携带解压器/DLL、许可、Apate 与冻结运行时核心文件；NSIS/ZIP 共用 `stage_desktop_resources.py` 的八份文档、十二个图输出和许可 manifest。替换的旧资源保存在 `resource-backups/`，已有便携运行目录和 `data/` 保留。
- 隔离打包检查 17/17、py_compile、PowerShell 语法检查退出 0；`package-gates.log`。主线程追加祖先链接越界和大小写敏感文件名两项，退出 0；`resource-boundary-final.json`。Windows 实际资源暂存另见 `resource-stage-final.log`、`engine-stage-validate-final.log`。
- 最终 NSIS/ZIP 的资源、逐文件哈希/CRC、禁止文件名、发行文件 SHA-256 与运行时一致性记录：`package-check-final.json`。原生窗口 smoke 使用前一次便携目录；最终新目录的 EXE/engine 字节与已通过的窗口和冻结引擎记录一致，只更新交付资源。
- 文档仅做本地链接、代码围栏与规则一致性检查，退出 0；`artifacts/handoff/continuation-20261007/doc-check-final.json`。没有为文档重跑应用测试。
- Git 已推送：`8c335e4`（OO 图与指南）、`036abcd`（桌面应用与安全引擎桥接）。交付脚本、许可和本状态属于最后一个目标提交；其 commit ID/推送结果见本回合交付回复或 `git log -1 --oneline`。产物和 runtime 不进入 Git，没有创建 GitHub Release。
- 构建曾遇 NSIS 下载截断与 ZIP 早于 1980 的文件时间戳；已采用官方 hash 校验的项目缓存和 ZIP 时间戳截断。时间戳处理不改变内容字节。最终默认从零安装依赖的一键流程未整体重跑；本轮复用有效输入并实际执行各构建/打包阶段。


### 2026-10-08 前置验证（代码与测试）

本次按用户要求检查代码、测试断言和已有机器证据，基线为已推送的 `1be3ae0dfa40a54e4f54a85d12354c4e5964b8bf`。开始时工作区干净；核心代码与测试相对 `036abcd` 无差异。未运行桌面程序、冻结引擎、7-Zip 或真实内容管线，未改产品源码、未重新打包。

两个驻留且已核实为 DeepSeek/high 的子智能体完成接口审查与测试覆盖核对，没有恢复旧子线程。主线程复核具体调用链及探针源码、结果，不重跑同状态的前端/Rust/打包检查。静态范围内，三层 14 个方法的白名单、参数/返回 DTO、终态/重试集合、窗口 capability 和结果登记调用链一致，未发现阻断项。

| 功能 | 实现与检查依据 | 本轮结论及边界 |
|---|---|---|
| 扫描、启动、原件保全 | Python 生命周期断言验证归档原件 bytes、输出不覆盖、输入身份变化拒绝、同 key 复用任务 | 复用已有定向证据；解压器桩与合成样本不能代表真实格式效果 |
| 取消、重试、历史 | Python 取消响应/源保留/原位重试；前端 6 项含忙时取消、恢复初始化与全量历史快照 | 复用通过证据；真实工具中途取消和原生窗口行为仍待人工验收 |
| 恢复、日志写入隔离 | 多卷未决动作、终态包恢复、跨任务恢复键、日志异常隔离回归 | `python-review-final.log` 的 29 passed 属恢复键最后修正前；修正后的 4 passed / 8 deselected 沿用 session 44366 记录，无独立持久日志，不能相加为 33 项或声称当前整套重跑通过 |
| 日志读取、结果登记 | 本轮独立探针：未知任务/缺日志、分页游标/截断/脱敏、登记路径存在性与 prepared 排除、needs_review 动作 | 4/4 组断言通过，退出 0；只检查读取端点与临时合成记录，未打开系统目录或处理内容；未加入正式回归套件 |
| 设置、密码参数与忙保护 | 本轮纯 mock 探针：3 条写入口 BUSY 拒绝且无副作用、非法 DTO、空列表清空、导入路径/文件类型/512 KiB 边界/编码异常 | 15/15 分支通过，退出 0；未构造真实引擎、未访问文件/OS 凭据、未建线程/SQLite；不能证明真实设置写入与凭据持久化 |
| 宿主协议、打包 | Rust 8 项协议/队列/健康状态测试；资源边界、ZIP/NSIS 哈希与运行时身份记录 | 复用有效证据；没有新增安装、启动、强杀恢复或干净机器检查 |

本轮读取探针命令：`rtk env PYTHONPATH=src /usr/local/bin/python /tmp/reorder-preflight-test-20261008/probe_read_endpoints.py`，退出 0。源码、结果和静态审查收存于 ignored 的 `artifacts/desktop/preflight-20261008/`（`probe_read_endpoints.py`、`probe_results.txt`、`static-review.md`、`baseline.json`），不随 Git 分发。

参数探针命令：`rtk env PYTHONPATH=src /usr/local/bin/python /tmp/reorder-preflight-test-20261008/probe_param_guards.py`，最终退出 0；同一证据目录保存 `probe_param_guards.py`、`probe_param_results.txt`。首次执行退出 1，原因是探针接受分支漏调用 dispatch；修正探针后通过，产品代码未改。两个探针均未加入正式测试套件，后续修改这些接口时仍需补回归保护。

发现三组低风险界面问题，已核对源码，当前产物中仍保留：

| 问题 | 触发与影响 | 人工核对 |
|---|---|---|
| 取消提示可能失真 | `desktop-controller.ts` 未检查 `accepted:false`；任务在两次轮询间结束后点取消，仍显示已请求取消 | 以任务终态为准，核对结束边界的状态与提示是否一致 |
| 被拒绝的界面操作缺反馈 | 运行中拖入文件会被 running 拦住；busy 时点击日志或错误刷新会被 busy 拦住 | 核对用户能否理解操作未执行，忙结束后日志/刷新能否正常工作 |
| 未提交密码文本关闭后残留 | `App.svelte` 仅关闭按钮清空文本；ESC/遮罩关闭或仅保存设置后，再打开可能仍看到粘贴内容 | 分别验证关闭方式与重开；当前应使用关闭按钮清除尚未提交文本 |

这轮完成前置工程检查，不等于业务验收通过。原生 `invoke` 参数绑定、WebView UUID、拖放/对话框、OS 凭据、真实强杀恢复、实际文件效果与安装卸载仍交由用户验收；读取端点探针也未覆盖 Rust 系统打开操作。

### 2026-10-08 Windows 固定解压依赖

用户要求最终 Windows 包固定携带可用解压器，本轮沿用前置验证边界：检查代码、纯 fake 测试、CLI 帮助与发行资源，不启动 GUI、不处理用户文件或内容样本。基线 `b9f275e`；main 原有工作保留。

- 随包固定 **7-Zip 25.01 + UnRAR 7.13.0 x64**。UnRAR 使用宿主 `C:\Program Files\WinRAR\UnRAR.exe` 的有效签名原始 EXE；Git 中保存三个公开文件组成的 [vendor ZIP](../tools/desktop-vendor/unrar-7.13-windows-x64.zip)，[工具锁](../scripts/desktop-tools.lock.json) 固定包和逐文件 SHA-256。缺输入、哈希不符或缺许可直接失败，不用 PATH 或上游最新 beta 顶替。
- UnRAR 自身许可明确允许放进其他软件包。随包保留 `UnRAR-License.txt` 和对应 WinRAR 7.13 的公开 `WinRAR-License.txt`；不包含 WinRAR GUI、`Rar.exe`、注册信息或用户配置。
- 宿主找到 Bandizip `bz.exe` 7.40.0.1，签名和帮助检查正常；官方 EULA 2.3/2.4 要求书面分发许可，目前未取得，故不随包。已安装的 `bz.exe` 仍可在设置里指定，默认 7-Zip/UnRAR 不依赖它。宿主 `tar.exe` 为 bsdtar/libarchive 3.8.8，仅作为候选记录，未复制系统 EXE 或新增适配器。具体来源见 [第三方说明](desktop-third-party.md#8-unrar-固定依赖与未随包工具)。
- 新增备用工具后，纯 fake 复现发现 7-Zip 的密码错误被最后 UnRAR 的格式不兼容消息覆盖。`ExtractionService` 现在仅在最终为明确格式不兼容时保留此前密码失败；成功回退、全工具×密码矩阵、缺卷早停保持，最后磁盘/写入错误仍保留。
- 已重新冻结 Windows Python 引擎，`--help` 退出 0，验证加载且未构造任务/SQLite。Rust 宿主、前端和 7-Zip 字节未改，复用已有对应检查。旧引擎内容 smoke 的结果不能当作本次新冻结引擎的业务验收。
- 两轮新建且驻留的 DeepSeek/high 完成锁→暂存→许可→打包边界审查及错误保留修复审查，无阻断；复用本聊天有效 V1 正文与运行元数据，没有恢复迁移前旧子线程。临时解包目录已移到 engine 同级，避免硬杀残留进入资源映射。

证据根：ignored 的 `artifacts/desktop/fixed-tools-20261008/`。检查均使用 `rtk`，本轮没有全库测试、浏览器检查或真实内容管线运行。

| 范围 | 命令 / 方式 | 结果与证据 |
|---|---|---|
| 锁、离线输入与暂存 | `PYTHONPATH=src /root/miniforge3/bin/python -m pytest tests/test_desktop_tool_staging.py -q` | worker 57 passed，退出 0；`staging-tests-rev2.json`。后续临时目录小修只重查 `-k 'stage_from_cache_then_validate or stage_repairs_tampered_target_and_backs_up_old'`，2 passed / 55 deselected、退出 0，`temp-stage-check.json`；属于原套子集，不累加 |
| 密码失败保留与回退 | 同环境 `pytest tests/test_fixed_tool_fallback.py -q`；`pytest tests/test_beta_cli_and_extracting.py -k extraction_service -q` | 8 passed；4 passed / 3 deselected，各退出 0；`fallback-tests.log`。全部 fake；原红复现 `fallback-diagnostic-red.log` 退出 1 |
| Windows 工具发现 | 隔离临时设置目录，默认解析 7-Zip/UnRAR，校验锁哈希；UnRAR `-?` | 退出 0；`windows-tool-probe.json`、`host-cli-help.json`。只帮助，不解压；没有保存设置、凭据或建立引擎/SQLite |
| Windows 最终冻结 | build venv `scripts/stage_desktop_engine.py`；暂存引擎 `--help` | 各退出 0；`freeze-stage.log`、`artifacts/desktop/engine-build/freeze.log`、`frozen-engine-help.log` |
| 许可与构建脚本 | Windows build venv `scripts/collect_desktop_licenses.py`；PowerShell Parser 读取 PS1 | 各退出 0；641 entries（366 text / 67 metadata / 208 unresolved），UnRAR 两份 text；`collect-licenses-final.log`、`powershell-parse.log` |

发行入口沿用 `artifacts/desktop/ReOrder_0.2.0_x64-setup.exe`、`ReOrder-0.2.0-windows-x64.zip` 与 `SHA256SUMS.txt`。新便携目录由打包脚本选择，不覆盖旧目录或 `data/`；实际路径、NSIS/ZIP 最终检查结果及发行哈希记录分别保存在 `package-result.json`、`windows-bundle.log`、`package-check-final.json`。本段已通过检查只覆盖上表；最终发行产物结果以这些记录与交付回复为准。

UnRAR 中文帮助编码未统一成 UTF-8；没有通过增加 `-scfc` 解决此问题。真实 RAR/密码/分卷兼容性、安装卸载、干净 Windows 与界面体验仍待用户人工验收，新增工具不能当作这些验收已通过。

### 人工验收与明确限制

按 [用户指南第 12 节](desktop-user-guide.md#12-真实文件人工验收清单) 使用副本样本，逐项核对操作步骤和预期结果：

1. 原生对话框/拖放、初始化异常后的刷新、设置保存、默认用户数据与便携数据。
2. 代表性真实格式、密码、成品与原件归档 bytes/hash、结果打开与错误分类。
3. 处理中取消、失败重试、活动任务关闭与中断后的人工核对；不自动重做成功包。
4. 干净 Windows 机器安装/卸载、WebView2 和路径兼容性。

未签名；没有正式 GitHub Release、根项目 LICENSE 决定、完整法律审计、性能 benchmark 或 macOS/Linux 发布。引擎在窗口初始化查询时启动，当前没有自动空闲卸载。工具密码 argv 可被同用户进程读取；Job Object 不可用时 taskkill 后代回收有边界；大量文件遍历会延迟取消；系统凭据真实读写尚未验收。工作目录不是 OS 沙箱。

浏览器控制、监听、托盘与 Agent 保持后续范围。没有运行浏览器 E2E、真实模型调用或全库测试。

## 1. 项目、Git 与迁移边界

- Windows 绝对路径：`D:\buff\reorder`。
- WSL 绝对路径：`/mnt/d/buff/reorder`。
- 当前分支：`main`。
- HEAD：`07b561fe5088cf15828f4c499a913472207f7405`，`fix: complete source archive lifecycle routing`。
- 本次核查时 `origin/main` 与 HEAD 指向同一提交；未重新 fetch，不能据此断言远端后来没有变动。
- 桌面方案、设计、全部新桌面代码和新测试均在工作区，**尚未提交、尚未推送**。
- 已保留所有修改；没有回滚他人工作、删除文件、提交、推送或部署。
- 用户原有 config、密码库与既有 CLI 继续保留；不要读取或回传 `resources/passwords.txt`、用户 `config.json` 的敏感内容、`docs/todo.md`（其中含密码）。
- 交接时已停止本轮启动的 Windows release 编译进程树；保留编译缓存和日志。没有确认中的文件写入子智能体。

Git 核查快照位于 ignored 文件：

- `artifacts/handoff/git-status.txt`
- `artifacts/handoff/untracked-files.txt`
- `artifacts/handoff/tracked-diff-stat.txt`

这些快照在本交接文档更新前保存，所以文档本身的最后更新时间不在快照内。`git diff --stat` 只统计已跟踪文件，**不含**所有新建应用文件；必须同时看 untracked 清单。

## 2. 当前完整目标

按 `docs/product_plan.md` 实施跨平台桌面软件首版，Windows 首交付。完成正式的面向对象模块/接口/类设计及架构、类、时序、生命周期图，然后实现简单手动批处理版本与可发布产物，修复范围内缺陷并完成风险匹配验证，交付面向熟悉 Python/Java/C++ 的制作方的详细代码阅读与新语言入门指南。目录监听、浏览器控制与 Agent 留后续。用户补充要求：用好 Git，及时 commit，及时推送远程。

当前最新指令：**本聊天只交接，不自行提交、推送、部署、删除文件，不再扩展或开启实现。** 新聊天收到继续授权后再推进上述目标；不要把目标写成已经完成。

### 已批准需求与实施授权

1. 跨平台软件，当前优先在 Windows 做完整可用版本，保留 Linux/macOS 的架构扩展能力。
2. 独立桌面 EXE，可接受内置 WebView；系统文件对话框和拖放输入。
3. 技术栈已确认：Tauri 2 + Svelte 5 + TypeScript + Vite；Rust 宿主 + Python 业务引擎 + SQLite。
4. 首版保留现在脚本核心能力：分卷分组、伪后缀/Apate/合并文件恢复、密码解压、可选嵌套解压、成品发布、原包归档、失败分类、取消、重试、历史。
5. 操作小而美，以手动批处理先落地；浏览器版、监听、AI Agent/API 工具接入为未来阶段。
6. 先正式设计对象、模块边界、接口、类图与流程，再实施。用户已批准方案并授权本阶段从设计自动推进到实现和验证，迁移请求暂时停止本聊天的推进。
7. 代码按面向对象组合组织。文档面对有深厚编程基础、熟悉 Python/Java/OO/C++ 指针的制作方，解释 TS/Svelte/Rust 项目用法与跨进程约束。
8. 不把 Python 全部重写成 Rust/C++/Java：首版保留已实现功能，Rust 负责桌面和进程边界，原生解压工具负责主要计算。性能判断需基于证据，不能只凭语言比较。
9. 界面可参考开源项目并复用成熟组件，尽量美观、功能完整。已采用 Bits UI 的 Dialog 与 `@lucide/svelte` 图标，参考 shadcn-svelte 的简洁组件风格；没有直接搬整个开源应用。
10. 原用户已批准提交 `resources/restoreAB.exe` 与 pyzipper 依赖（上一阶段处理）。新桌面发行包暂未携带 restoreAB/Bandizip/UnRAR 商业或许可不明的工具；原生恢复逻辑已复用，7-Zip 带 License.txt。

### 约束与尚未决定事项

- 真实用户文件尚未提供；模拟样本检查与业务/体验验收分别报告，不承诺“没有各种 bug”。
- 默认 1–3 项风险匹配检查；跨层文件完整性、取消和协议风险已按约定做定向检查，不开启全库测试/全库 lint/真实模型调用。
- 首版单任务执行，UI 适度轮询，工作线程与协议线程独立；暂没有推送事件、目录监听或浏览器服务。
- 后续 Rust 重构具体算法的范围与性能目标尚未决定，需要真实 profiling 后决策。
- macOS/Linux 发布矩阵、应用签名/Windows 代码签名、最终发布渠道、更新机制、完整第三方许可汇总尚未完成/决定。
- 用户希望积极使用子智能体：默认 DeepSeek/high，独立上下文，独占文件，不静默换付费模型、不恢复旧子线程、不继续递归派生。

## 3. 已完成、部分完成、未开始

### 已完成（限定下面的范围）

- `product_plan.md`：产品范围、技术选型、两种控制形态、跨平台和性能取舍、分阶段路线。
- `desktop-design.md`：OO 职责、资源归属、DTO、JSON-RPC 方法表、文件生命周期、安全边界、实现与验证契约。
- 架构/类/时序/状态图源与 SVG 已生成。架构图经过 renderer/check 检查；此前发现数据库横线穿标签，已改绿色矩形并重新渲染。新版 PNG 已生成，最后一版 PNG 尚未重新视觉检查。
- Python 应用层与基础设施代码已写入：严格 DTO、扫描/源身份复核、独立用户设置、密码后端、SQLite 状态/动作日志、文件复制校验/发布/归档、单工作线程、取消/重试、门面与 JSON-RPC 入口。
- 原管线增加结构化 `BetaPackageResult`，保持旧计数接口兼容；桌面不靠解析日志判断状态。
- 外部命令执行器加入有界输出、独立 deadline、取消、工具输出守卫、Unix 进程组、Windows Job Object 与 taskkill 兜底。
- 前端与宿主首版源码：EngineClient/TauriEngineClient/DesktopController，手动流程、输入/结果目录选择、拖放、计划预览、状态表、设置、密码导入、取消、重试、历史、日志、只打开登记结果目录。
- Windows Python 引擎已冻结/暂存，并用真实 7-Zip 对合成 ZIP、AES ZIP、PDF+ZIP 合并恢复、7z 分卷执行了成功路径与原件 hash 检查；恶意路径和密码脱敏检查通过。
- Python 30 项定向检查（另有 12 subtests）、前端 2 项 Controller 测试、前端类型检查/构建、Rust `cargo check --tests` 通过。详细边界见第 5 节。

### 部分完成

- 可发布桌面产物：冻结引擎存在；Tauri 完整 release/安装包/便携 ZIP **尚未构建完成**。
- UI：源码与静态构建完成，尚未打开实际桌面窗口操作、截图或做人工体验验收。
- 图：架构、类、时序、状态 SVG 存在；模块图仅有 `.mmd`，图内容尚未逐项对齐最终实际方法名。
- 安全：预检/输出检查/限制已实现并有部分检查；不是 OS 强沙箱。恶意第三方工具漏洞、同用户路径竞态、所有链接/归档类型与强制结束进程树尚未完整验证。
- 制作方解释：正式设计与产品方案存在；详细代码阅读、新语言指南、用户操作/验收指南尚未编写。
- 打包脚本：源码存在，用户完整“一键构建”流程尚未从头执行；发布包许可证清单尚未汇总。

### 未开始

- `docs/desktop-code-guide.md`
- `docs/desktop-language-guide.md`
- `docs/desktop-user-guide.md`
- 跨平台 CI / 发布矩阵、发行包签名与正式 GitHub release。
- 浏览器控制版、监听目录、LLM/Agent 接入（明确为后续愿景）。

## 4. 修改文件与关键实现入口

### 已跟踪文件修改（全部未提交）

- `.gitignore`：忽略 runtime/artifacts/node_modules/target、前端 dist、引擎暂存/gen 等。
- `README.md`、`ARCHITECTURE.md`、`REQUIREMENTS.md`：上一阶段已批准方案的入口/边界。
- `pyproject.toml`：新增 desktop extras：pydantic、keyring；已有 pyzipper 依赖。
- `src/reorder_engine/infrastructure/command_runner.py`：有界执行、超时和取消。
- `src/reorder_engine/infrastructure/tools.py`：7zz 检索、UnRAR 输出分隔符跨平台。
- `src/reorder_engine/services/beta_pipeline.py`：新增包级结构化结果，原件/副本路由复用已有逻辑。

### 新增文件（全部未提交）

**设计与图**：

- `docs/product_plan.md`、`docs/desktop-design.md`、本文件 `docs/desktop-status.md`
- `docs/diagrams/architecture.json`、`architecture.svg`
- `docs/diagrams/modules.mmd`
- `docs/diagrams/classes.mmd`、`classes.svg`
- `docs/diagrams/sequence.mmd`、`sequence.svg`
- `docs/diagrams/states.mmd`、`states.svg`

**Python**：

- `application/__init__.py`、`errors.py`、`models.py`、`planning.py`、`processing.py`、`jobs.py`、`facade.py`（位于 `src/reorder_engine/`）
- `src/reorder_engine/desktop_engine.py`
- `infrastructure/desktop_paths.py`、`settings_repository.py`、`secret_store.py`、`job_repository.py`、`file_transaction.py`、`process_control.py`、`archive_safety.py`、`engine_lock.py`、`json_rpc.py`（同 package）
- `tests/test_desktop_engine.py`

**桌面前端/宿主**（位于 `apps/desktop/`）：

- `package.json`、`package-lock.json`、`index.html`、`tsconfig.json`、`vite.config.ts`、`svelte.config.js`
- `src/main.ts`、`src/App.svelte`、`src/styles.css`
- `src/lib/contracts.ts`、`engine-client.ts`、`desktop-controller.ts`、`desktop-controller.test.ts`
- `src-tauri/Cargo.toml`、`Cargo.lock`、`build.rs`、`tauri.conf.json`
- `src-tauri/capabilities/main.json`
- `src-tauri/src/main.rs`、`lib.rs`、`engine_bridge.rs`
- `src-tauri/icons/icon.svg`、`icon.png`、`icon.ico`

**构建与检查脚本**：

- `scripts/build_desktop_windows.ps1`
- `scripts/desktop_engine_entry.py`
- `scripts/requirements-desktop-build.txt`、`requirements-desktop-windows.lock.txt`
- `scripts/stage_desktop_engine.py`
- `scripts/smoke_desktop_engine.py`

### 对象与执行链入口

`App.svelte` → `DesktopController` → `TauriEngineClient` → Rust `engine_request` → `EngineBridge` → Python `JsonRpcServer` → `EngineFacade` → `PlanService` / `JobRunner` → `PackageProcessor` → 既有 `BetaFolderPipeline` → `FileTransaction` → `JobRepository`。

- DTO：`application/models.py`。
- 协议：UTF-8 JSON-RPC 2.0 JSON Lines，最大帧 1 MiB。Rust/Python 都有业务方法白名单。stdout 只用于协议。
- 原件：处理前复核 → 复制至工作区 → 恢复/解压工作副本 → 发布成品 → 全卷复制校验 → 删除源并归档真实原件。
- 归档/发布异常：记录动作、保留已生成结果和剩余原件，标 `needs_review`；禁止盲删或自动全任务重跑。
- 失败包的工作副本原件不会再被当成成品发布，避免原件重复归档。
- 密码：仅允许 Windows/macOS/SecretService/libsecret OS backend；Chainer/明文后端不自动接受，不可用则 session。`--session-secrets` 供隔离 smoke/隐私模式使用。
- 重试：成功/需要检查包不能直接重做；失败且已路由原件从登记目的路径读入，保留旧任务关联。
- 日志：脱敏，单 job 滚动约 2 MiB；状态事件和返回 DTO 有限长/分页。完整结果目录由 `results.get` 登记。

## 5. 检查记录与证据（不要为交接重跑）

### 通过

| 范围 | 实际命令/方式 | 结果 | 证据 |
|---|---|---|---|
| Python 桌面完整性/协议/取消 + 原 CLI 定向回归 | `rtk env PYTHONPATH=src /usr/local/bin/python -m pytest tests/test_desktop_engine.py tests/test_beta_lifecycle.py tests/test_beta_cli_and_extracting.py -q`，仓库根执行 | 退出 0；30 passed，12 subtests passed，最后一次约 3.21s | 工具 session 51474 最终输出；摘要保存于 `artifacts/handoff/check-results.md`；测试源码 |
| 前端类型与 Svelte 诊断 | `rtk proxy npm run check`，`apps/desktop` | 退出 0；0 errors，0 warnings | session 75747；摘要同上 |
| Controller 交互契约 | `rtk proxy npm run test`，`apps/desktop` | 退出 0；2 tests passed | session 64646；摘要同上 |
| 前端生产构建 | `rtk proxy npm run build`，`apps/desktop` | 退出 0；JS 138.86 kB / gzip 46.29 kB，CSS 7.23 kB | session 7631；`apps/desktop/dist/`（ignored） |
| Rust 桥接/测试源码编译 | `rtk proxy /mnt/c/Windows/System32/cmd.exe /c 'D:\buff\reorder\runtime\check-desktop.cmd'`；脚本内 `cargo check --tests --jobs 2` | 退出 0；4m58s；**只编译检查，没有执行 Rust 单元测试** | `artifacts/desktop/rust-check.log`；session 4569 |
| Windows 冻结引擎 | `rtk proxy runtime/desktop-build-venv/Scripts/python.exe 'D:\buff\reorder\scripts\stage_desktop_engine.py'` | 退出 0；引擎暂存完成 | `artifacts/desktop/engine-build/freeze.log`、`apps/desktop/src-tauri/resources/engine/` |
| Windows 真实 7z / 合成样本 | `rtk proxy runtime/desktop-build-venv/Scripts/python.exe 'D:\buff\reorder\scripts\smoke_desktop_engine.py' --engine 'D:\buff\reorder\apps\desktop\src-tauri\resources\engine\reorder-engine.exe' --evidence 'D:\buff\reorder\artifacts\desktop\native-engine-smoke.json'` | 退出 0；4 组/8 原件成员；ZIP、AES ZIP、PDF+ZIP 恢复、7z 分卷、成品与归档 bytes/hash、恶意路径、日志密码脱敏通过 | `artifacts/desktop/native-engine-smoke.json`；session 21506 |
| 架构 SVG | `rtk proxy python …/fireworks.py render architecture …`、`check docs/diagrams/architecture.svg` | 退出 0；文字完整，geometry/collisions/composition/markers/XML 均 OK | `artifacts/diagrams/architecture.render.json`、SVG |
| 架构 PNG | `rtk proxy python …/fireworks.py export-png docs/diagrams/architecture.svg artifacts/diagrams/architecture.png --width 1920` | 退出 0；1920×1658，CairoSVG | `artifacts/diagrams/architecture.png`；最后版未再视觉复核 |
| UML SVG 生成 | Windows Node 调 Mermaid CLI，分别 classes / sequence / states | 退出 0 | `docs/diagrams/{classes,sequence,states}.svg`；sessions 72226/22659/69107；未逐图视觉验收 |
| DeepSeek 静态配置 | `rtk proxy python3 /mnt/c/Users/98289/.codex/hooks/check_deepseek_runtime.py` | 退出 0；compatible=true；受控目录 v8、父/子标称 V1、high、上限 12 | 工具输出；静态配置不能证明当前聊天消息链路可用 |

说明：`native-engine-smoke.json` 的 scope 字符串包含 `interrupted recovery`，**该 smoke 脚本并没有模拟引擎异常退出**，不能把该标签当作通过证据。中断恢复目前只有 Python 定向模拟，不是 Windows 进程强杀验收。

### 已发现并处理的检查问题

- 初次 pytest 未设 PYTHONPATH，收集失败；改用上表命令后通过。不是源码导入回归。
- 初次 Svelte 变量 `state` 与 `$state` rune 冲突；已改为 `view`。原自制 modal 的可访问性警告随 Bits UI Dialog 替换后消失。
- 新执行器没有保留 password abort 原始行，旧定向回归失败；已保留有界错误行后通过。
- 模拟失败测试意外找到 WSL 继承的 Windows Bandizip；Unix 自动检索已过滤 `.exe`，测试明确隔离可选工具后通过。
- 工作副本失败原件曾被重复发布；已修正仅 succeeded/partial 发布工作输出。
- 冻结日志初次警告 Conda 的 SQLite/FFI/压缩/XML DLL 未找到；stage 脚本已显式收集必要 DLL，最终冻结与 native smoke 通过。
- npm 一次下载 `@lucide/svelte` 遇 ECONNRESET；设置本次 `npm_config_proxy`/`npm_config_https_proxy` 后成功。未关闭 TLS、未改全局代理。
- 曾临时装 deprecated `lucide-svelte`，已卸载并使用 `@lucide/svelte`。

### 未完成 / 中止 / 未验证

- `cargo build --release --jobs 2` 被本次迁移请求主动停止；session 99939，`artifacts/desktop/rust-release.log`。终止后 wrapper 最终退出 1；这是主动停止造成的退出，不是成功构建，也没有认定源码编译错误。
- 启动上述编译的 wrapper cargo PID 33032，实际 cargo PID 33436；按精确路径与命令核对后执行 `taskkill.exe /PID 33032 /T /F`，退出 0，编译进程树已停止。没有删除缓存。
- Tauri 实际窗口、拖放/对话框、设置保存、关闭时取消、打开结果目录、安装/卸载、WebView2、干净 Windows 机器尚未验证。
- Rust 单元测试尚未实际运行，Windows 子进程强制退出后的重开协调、所有工具/格式/极大包/长期批处理、性能/内存 benchmark 尚未验证。
- macOS/Linux build、浏览器控制、AI、真实用户样本尚未验证/实现。
- 独立代码审查没有产生有效结果（第 6 节）。

## 6. 子智能体与任务正文故障

用户要求多用子智能体。再次检查配置后派发了一个只读任务；当前聊天仍丢失正文，不能凭 spawn 成功宣称工作完成。

| 编号/用途 | 返回的 ID / canonical path | 成果 | 最后确认状态 |
|---|---|---|---|
| `R-DESKTOP-20261007`，桌面文件生命周期/取消/协议安全只读审查，DeepSeek/high，fork_turns=none | `/root/desktop_review` | 没有审查产物、没有扫描/写入；final 为 `TASK_INPUT_MISSING` | 2026-10-07 list_agents 明确 completed；子只读角色与边界，无需收尾文件 |
| 上一阶段 source lifecycle tests（本阶段没有重启） | `/root/source_lifecycle_tests` | 无有效产物，final `TASK_INPUT_MISSING` | 当前 list_agents 明确 completed |
| 上一阶段 restoreAB 实施任务（本阶段没有重启） | `/root/implement_restore_ab` | 概要记录无有效子成果；当前工作不依赖它 | 初始环境曾列出，当前 list_agents 不再列出；最后运行状态未知，未通信/恢复/重试 |
| 上一阶段 restore 代码扫描（本阶段没有重启） | `/root/scan_restore_code` | 概要记录无有效子成果；当前工作不依赖它 | 同上：状态未知，未恢复/反复通信 |

- 当前 list_agents 只包含 root、completed desktop_review、completed source_lifecycle_tests；没有确认的活跃写文件子任务。
- 不要在旧聊天调用 resume 或反复 send/followup 来修这个故障，不另起 CLI 子模型绕过宿主限制，不静默换付费模型。
- 新聊天使用已配置的 V1 明文路径，在读取实际 tool schema 和 runtime 检查后，先用一个无副作用任务确认实际收到编号/正文，再派发有独占范围的 DeepSeek/high 子任务。
- 如果新聊天仍是 TASK_INPUT_MISSING，报告阻碍并停止该委派链；静态 compatible=true 仅表示磁盘配置。

## 7. 当前阻碍、具体风险与下一步

### 当前阻碍/风险

1. 整体桌面交付未完成：没有完成的 Tauri release EXE、安装包或便携 ZIP。
2. 当前会话 DeepSeek 消息链路不工作，独立审查未完成；新聊天需要验证明文链路。
3. UI 尚未实际运行：特别检查 Svelte `$state` 代理是否可被 `structuredClone(draft)` 直接复制。`App.svelte` 保存设置目前这样调用，可能抛 DataCloneError；可考虑先 `$state.snapshot(draft)`。这是待核查风险，本交接未继续修代码。
4. 文档图中的一些方法是设计命名，尚未对齐实际代码：例如 `FileTransaction.publish_tree/reconcile` 当前实际为 `publish`/`publish_children`/`route_sources`，协调恢复目前由 repository 标记后人工核对；不要声称已实现自动 reconcile。状态图里的 cancelling 应区分 job 与 package（package DTO 没有 cancelling）。
5. 执行器/文件事务的高风险路径仍需要有效只读审查；Windows Job Object 不支持时退回 taskkill /T，不能把当前 smoke 当作全部进程树边界通过。
6. 输出限额/空间检查为应用层防护；复制校验增加磁盘 I/O，未测真实性能。Windows/MSVC 构建缓存很大，继续保持 jobs=2。
7. CLI 与 desktop 入口配置分离；不要把用户密码库打进发行包。默认 keyring OS 凭据保存未做真实凭据读写验收；smoke 强制 session 模式未触碰真实密码。
8. staging 冻结后再次改 Python 代码必须重新 stage 才能测试最终包。同状态已有通过检查不要重复跑；按改动影响定向补验。
9. Windows 构建与 Linux npm 依赖有平台差异：当前 node_modules 在 WSL npm 安装；Windows完整脚本包含 npm ci，会重装本项目依赖目录。不要直接假设 WSL 的 esbuild/CLI 二进制可在 Windows运行。
10. `build_desktop_windows.ps1` 会复制尚不存在的 `desktop-user-guide.md`，需要先写该文档再进行完整产物打包。

### 新聊天应按顺序继续

1. 读本文件、`product_plan.md`、`desktop-design.md` 与适用 AGENTS/RTK/NETWORK；核对 Git 状态，保留现有未提交修改，不重新实施已经存在的模块。
2. 按当前工具 schema 验证新聊天的 DeepSeek/high 正文链路，再拆分独立任务：引擎只读审查、Rust/IPC审查、图/文档对齐、制作方指南、UI验收可并行；所有写者范围不重叠。
3. 核查并修复 UI 运行风险（优先代理复制/设置保存）；有效审查指出具体问题再定向修复，引擎无需推倒重写。
4. 对齐正式类图/状态图与实际接口。补写 `desktop-code-guide.md`、`desktop-language-guide.md`、`desktop-user-guide.md`；面向有编程基础的制作方，按一个按钮追到对象/文件/协议，解释 Rust所有权、TS类型、Svelte响应式与Python对照。
5. 使用已存在 Rust/MSVC/Node/Python工具链继续 release 构建。保留 `runtime/release-desktop.cmd` 与 `rust-release.log` 缓存/证据，先完成 EXE，再实际运行窗口检查关键操作。
6. 根据实际修复重新 stage 引擎并复用/定向补充检查。当前 native smoke 已通过，同状态不重跑；Rust unit test实际未跑，可在最终构建稳定后安排有限范围。
7. 完成便携目录/ZIP和NSIS安装包，汇总第三方许可（Bits UI、Lucide、7-Zip、Python依赖等）、提供清晰运行和人工验收步骤。不要把商业工具或用户秘密带入包。
8. 最终核对 diff、需求覆盖、检查记录、实际产物，再按原目标及时分阶段 commit/push；本交接回合禁止提交/推送，新聊天收到继续后沿用用户的实施与Git交付授权。
9. 留真实特殊格式/体验给用户人工验收，明确实现状态、机器检查、未验证范围。只有完整目标与要求产物/指南完成后才标记目标 complete。

## 8. 环境、启动与构建入口

### 已准备环境（不需重复安装）

- WSL：`/usr/local/bin/python` 有 pydantic 2.13.4、pyzipper、pytest、CairoSVG、Pillow。`python`/`python3` 路径可能指向其他环境，验证命令用上表显式路径。
- Windows base Python：`D:\anaconda\python.exe`，3.13.5。
- 独立 Windows build venv：`D:\buff\reorder\runtime\desktop-build-venv\Scripts\python.exe`；PyInstaller 6.22.3、pydantic 2.13.5、keyring 25.7.0、pyzipper 0.3.6。锁定清单见 `scripts/requirements-desktop-windows.lock.txt`。
- Windows Node：`C:\Program Files\nodejs\node.exe`，v24.10.0；WSL npm 已安装前端依赖和 lock。
- Windows Rust：本项目 isolated `runtime\toolchains\cargo\bin\cargo.exe`，stable rustc 1.99.0。没有修改用户全局 PATH。
- `CARGO_HOME=D:\buff\reorder\runtime\toolchains\cargo`，`RUSTUP_HOME=D:\buff\reorder\runtime\toolchains\rustup`。
- MSVC：`C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvarsall.bat`，x64；14.29.30133。SDK 10.0.19041.0。
- 7-Zip源工具：`D:\buff\reorder\tools\7zip\Files\7-Zip\7z.exe`，带 dll 与 License.txt。
- 冻结引擎暂存：`D:\buff\reorder\apps\desktop\src-tauri\resources\engine\reorder-engine.exe`，`_internal`、`tools/7zip`、`tools/apate.py`；完整目录必须保留。
- 图工具：`runtime/diagram-tools/node_modules/@mermaid-js/mermaid-cli/src/cli.js`；Windows Chrome headless配置在 `runtime/diagram-tools/puppeteer.json`。
- Fireworks：`/mnt/c/Users/98289/.codex/skills/fireworks-tech-graph/scripts/fireworks.py`。使用现成 renderer，不重造图工具。

### 网络与执行规则

每条 shell 命令以 `rtk` 开头，不适合过滤用 `rtk proxy`。联网命令本次显式使用：

```
http_proxy=https_proxy=HTTP_PROXY=HTTPS_PROXY=http://127.0.0.1:7897
no_proxy=NO_PROXY=localhost,127.0.0.1,::1,*.local
```

npm 必要时额外设置本次 `npm_config_proxy`、`npm_config_https_proxy` 同一URL。不要改全局代理或关闭TLS。WSL直接调用 Windows exe时依赖对应Windows路径参数；嵌套 PowerShell→native进程输出曾出现 interop故障，已验证直接 Windows Python/Node 或 cmd wrapper 可用。

### 继续后可用的入口（此回合没有重新启动）

- Python开发引擎：`rtk env PYTHONPATH=src /usr/local/bin/python -m reorder_engine.desktop_engine --data-root /tmp/reorder-dev-state --session-secrets`。它读取 stdin 的 JSON-RPC行，stdout只输出协议。
- 前端静态开发：`apps/desktop` 下 `rtk proxy npm run dev`；普通浏览器只做预览，不能处理本地文件，TauriEngineClient会提示桌面环境。
- 实际桌面开发：Windows设置 `REORDER_PYTHON` 为上面的 venv Python，在已初始化 MSVC/Rust环境的 `apps/desktop` 下运行 `npm run tauri -- dev`。需要Windows平台的 npm依赖，不保证当前WSL node_modules 可用。
- 本项目的 Windows编译 wrapper：`runtime/check-desktop.cmd`（已成功）、`runtime/release-desktop.cmd`（此次编译主动中止）。内部代理/CARGO/RUSTUP/PATH均是本项目路径，没有凭据。
- 完整构建脚本：`scripts/build_desktop_windows.ps1 -Python 'D:\anaconda\python.exe'`，可用 `-PortableOnly`；先完成用户指南与UI风险处理再运行。此脚本尚未完整验证。

## 9. 后续聊天启动参考

> 继续 D:\buff\reorder（WSL /mnt/d/buff/reorder）的桌面首版验收收尾。先读取 docs/desktop-status.md 第 0 节最新状态、docs/product_plan.md 和 docs/desktop-design.md，保留 main 全部本地修改。Windows 0.2.0 预览 EXE/NSIS/便携 ZIP、OO 图、代码和语言指南已交付；先根据我提供的真实文件与体验反馈定位下一切片，不重做已完成模块或同状态有效检查。技术栈与手动批处理范围保持已批准方案，浏览器/监听/Agent留后续。多用 DeepSeek/high、独立上下文和不重叠文件范围；新聊天先验证 V1 正文链路，不恢复旧子线程。真实文件与体验由我人工验收；未明确反馈项时先核对最新交付状态。仍遵守 RTK/现有代理，不读取/外发用户密码，不将私人配置打包。需要实施时复用本轮授权边界，按目标及时 commit 并推送；未完成的人工验收不能写成通过。
