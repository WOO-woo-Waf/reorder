# 桌面首版第三方组件与许可清单

日期：2026-10-08（Asia/Shanghai）。适用范围：Windows x64 桌面首版发行包所携带的组件。
本文只整理第三方组件、许可与证据位置；**不决定本项目根 `LICENSE`**，最终打包集成由主线程完成。
相关文档：[实施状态](desktop-status.md)、[工程设计](desktop-design.md)。

## 1. 目的与边界

- 目标：为已将/将随桌面包分发的组件建立可复现的许可证据，覆盖前端 npm 锁、Rust Cargo 锁、冻结 Python 引擎依赖，以及随包 7-Zip / UnRAR 的发布许可。
- 证据来源：**本地已安装依赖**的包内许可文件与元数据（`node_modules`、Cargo registry checkout、冻结 venv `site-packages`、`tools/7zip`），以及第 5.1 节固定来源的上游补充文本。脚本默认离线，只有 `--fetch-upstream` 才下载补充文本。
- 不读取用户秘密：脚本不接触 `resources/passwords.txt`、用户 `config.json`、`.env` 或任何凭据。
- 不编辑冻结引擎暂存目录：脚本只写 `artifacts/desktop/license-evidence/`（被 `.gitignore` 忽略），不修改 `apps/desktop/src-tauri/resources/engine/`。
- **明确不随包分发**：Bandizip（官方 EULA 要求书面分发许可，当前未取得）、`restoreAB.exe`（历史用户工具）。见第 8 节。UnRAR 自身许可明确允许随其他软件分发，本次纳入固定依赖。
- 冻结引擎 `build-info.json` 的 `excluded` 保留 `passwords.txt`、`config.json`、`restoreAB.exe`、`Bandizip`；`bundled_tools` 及 `tools/fixed-tools.json` 记录固定 UnRAR 身份。

## 2. 收集方法与复现

收集脚本：[`scripts/collect_desktop_licenses.py`](../scripts/collect_desktop_licenses.py)（纯标准库，Python ≥3.11，只读输入）。

```bash
# WSL
rtk proxy python3 scripts/collect_desktop_licenses.py
# 需要重新下载上游补充许可文本时（联网，走既定代理）
rtk proxy python3 scripts/collect_desktop_licenses.py --fetch-upstream
# Windows（PowerShell，仓库根）
# rtk proxy py -3 scripts/collect_desktop_licenses.py
```

可选的路径覆盖参数：`--repo`、`--evidence`、`--cargo-registry-src`、`--site-packages`、`--python-lock`、`--seven-zip`、`--upstream-dir`、`--windows-deps`、`--windows-scope`。
`--fetch-upstream` 才会联网；默认离线，直接复用 `artifacts/desktop/license-evidence/upstream/` 内已保存的上游许可文本。
脚本内置一张"crate → 上游仓库/ref/commit/文件 URL"映射，用于补全发布包未携带许可文本的 crate（见第 5.1 节）；缺失的文本如实标为 `metadata` 或 `unresolved`。
脚本对每个条目给出三种状态之一：

| 状态 | 含义 |
|---|---|
| `text` | 已复制到许可/声明文本文件到证据目录 |
| `metadata` | 只有声明的许可标识，本地无文本文件 |
| `unresolved` | 既无许可标识，也无文本文件（通常为本地无源码） |

输出：

- `artifacts/desktop/license-evidence/manifest.json`：逐条清单（生态、名称、版本、许可、作用域、是否已安装、收集到的文本文件、状态）。
- `artifacts/desktop/license-evidence/SUMMARY.md`：分生态汇总与未完全解析条目。
- `artifacts/desktop/license-evidence/{npm,cargo,python,7zip,unrar}/…`：完整许可文本副本。
- `artifacts/desktop/license-evidence/upstream/…` + `upstream/SOURCES.json`：上游补充许可文本及其来源/commit/sha256。

## 3. 2026-10-07 收集快照与 10-08 工具增补

在 2026-10-07 的 Windows 构建工作区（`Cargo registry` 位于 `runtime/toolchains/cargo/registry`，Python 位于 `runtime/desktop-build-venv`）执行脚本，退出码 0。
主线程另以 `cargo metadata --filter-platform x86_64-pc-windows-msvc --locked --offline --format-version 1`（退出 0）生成 Windows 可达依赖图 `artifacts/desktop/windows-dependencies.json`；脚本据此把 Cargo 条目分为"Windows 图内 254"与"不在 Windows 图内的 208"两类（`manifest.json` 的 `cargo_scope`）：

| 生态 | 条目数 | `text` | `metadata` | `unresolved` |
|---|---|---|---|---|
| npm（`package-lock.json`，lockfileVersion 3） | 158 | 91 | 67 | 0 |
| Cargo — Windows x64 可达图（254） | 254 | 254 | 0 | 0 |
| Cargo — 不在 Windows 图内（锁内其余 registry 包） | 208 | 0 | 0 | 208 |
| Python（`requirements-desktop-windows.lock.txt`） | 19 | 19 | 0 | 0 |
| 7-Zip | 1 | 1 | 0 | 0 |
| 合计 | 640 | 365 | 67 | 208 |

2026-10-08 新增 UnRAR 一项（`text`），原依赖范围不变；最新实际清单以 `manifest.json` 为准。完整上游/宿主取证在 `artifacts/desktop/fixed-tools-20261008/`，锁定构建输入及其许可字节随 Git 保存在 `tools/desktop-vendor/`。

Windows 可达图内 254 个 crate **全部取得许可文本**（含 7 个通过上游补充，见第 5.1 节）。`unresolved` 208 项全部来自"不在 Windows 图内"的 Cargo 锁条目（本地无源码、非本次分发路径），见第 5、9 节。

## 4. 前端 npm 依赖（Bits UI / Lucide / Svelte / Tauri JS）

来源：`apps/desktop/package-lock.json` + `apps/desktop/node_modules/*`。锁中共 158 个包条目（36 个生产依赖，其余为开发/平台可选依赖）。11 个直接依赖全部收集到许可文本：

| 包 | 锁定版本 | 许可 | 文本 |
|---|---|---|---|
| `bits-ui`（Dialog 等组件） | 2.19.5 | MIT | ✅ |
| `@lucide/svelte`（图标） | 1.52.0 | ISC | ✅ |
| `svelte` | 5.57.2 | MIT | ✅ |
| `@tauri-apps/api` | 2.12.1 | Apache-2.0 OR MIT | ✅（含两份） |
| `@tauri-apps/plugin-dialog` | 2.8.1 | MIT OR Apache-2.0 | ✅ |
| `@tauri-apps/cli`（开发） | 2.12.1 | Apache-2.0 OR MIT | ✅ |
| `@sveltejs/vite-plugin-svelte`（开发） | 6.2.4 | MIT | ✅ |
| `svelte-check`（开发） | 4.7.6 | MIT | ✅ |
| `typescript`（开发） | 5.9.3 | Apache-2.0 | ✅ |
| `vite`（开发） | 7.3.7 | MIT | ✅ |
| `vitest`（开发） | 3.2.7 | MIT | ✅ |

打包后实际随应用分发的运行期依赖共 36 个（含 `@floating-ui/*`、`@internationalized/date`、`runed`、`svelte-toolbelt`、`esrap` 等传递依赖），许可为 MIT / Apache-2.0 / ISC / 0BSD，且基本都有许可文本；未完全解析的运行期依赖见下。

未完全解析（2 项，均为运行期传递依赖）：

- `is-reference` 3.0.3 — `license: MIT`（`metadata`，包内无 `LICENSE` 文件）。
- `locate-character` 3.0.0 — `license: MIT`（`metadata`，包内无 `LICENSE` 文件）。

两者许可标识来自各自 `package.json`，文本在证据目录中缺失；需要时从上游 npm 元数据补全。

## 5. Rust crates（Tauri / 桥接宿主）

来源：`apps/desktop/src-tauri/Cargo.lock`（version 4）+ 本地 Cargo registry checkout
`runtime/toolchains/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/`。锁内 registry 包共 462 个（另含本地工作区 crate，已排除）。

直接依赖的许可解析结果：

| crate | 版本 | 许可 | 文本 |
|---|---|---|---|
| `tauri` | 2.12.1 | Apache-2.0 OR MIT | ✅ |
| `tauri-build` | 2.7.1 | Apache-2.0 OR MIT | ✅ |
| `tauri-plugin-dialog` | 2.8.1 | Apache-2.0 OR MIT | ✅ |
| `tauri-plugin-opener` | 2.7.0 | Apache-2.0 OR MIT | ✅ |
| `tauri-plugin-single-instance` | 2.5.2 | Apache-2.0 OR MIT | ✅ |
| `serde` | 1.0.229 | MIT OR Apache-2.0 | ✅ |
| `serde_json` | 1.0.151 | MIT OR Apache-2.0 | ✅ |

宿主核心传递依赖同样已取证：`tauri-runtime`、`tauri-utils`、`tauri-macros`、`tauri-codegen`、`wry` 0.57.0、`tao` 0.37.1（Apache-2.0）等。

汇总：**Windows x64 可达图 254 个 crate 已全部取得许可文本**（其中 247 个来自本地 registry 包内文本，7 个来自上游补充，见第 5.1 节）。另有 208 个 `Cargo.lock` 条目不在 Windows 依赖图内、本地 registry 无源码，单独列为未取证（见第 9 节）。

### 5.1 上游补充许可文本（原缺文本的 7 个 crate）

以下 crate 的发布包内不含许可文件，只带 `Cargo.toml` 许可标识。脚本依据本地 crate 元数据的 `repository`/`version`，从上游仓库固定 ref（tag 或 commit）取回真实文本（均含实际版权行，非占位模板），保存到 `artifacts/desktop/license-evidence/upstream/<crate>-<version>/`，来源与 sha256 记录在 `upstream/SOURCES.json`。

| crate | 版本 | 许可 | 上游仓库 | ref | commit | 文件 |
|---|---|---|---|---|---|---|
| `alloc-stdlib` | 0.3.0 | BSD-3-Clause | dropbox/rust-alloc-no-stdlib | tag `0.3.0` | `0a81fd69…` | `LICENSE` |
| `defmt-parser` | 1.0.0 | MIT OR Apache-2.0 | knurling-rs/defmt | tag `defmt-v1.0.0` | `48c82e1c…` | `LICENSE-MIT`、`LICENSE-APACHE` |
| `selectors` | 0.38.0 | MPL-2.0 | servo/stylo | commit（默认分支头） | `0cb50925…` | `README.md`（MPL 声明）、`MPL-2.0.txt`（官方全文） |
| `tauri-plugin` | 2.7.1 | Apache-2.0 OR MIT | tauri-apps/tauri | tag `tauri-plugin-v2.7.1` | `30da1fd6…` | `LICENSE-APACHE-2.0`、`LICENSE-MIT` |
| `webview2-com` | 0.39.1 | MIT | wravery/webview2-rs | commit（默认分支头） | `edc2caf8…` | `LICENSE` |
| `webview2-com-macros` | 0.8.1 | MIT | wravery/webview2-rs | commit（默认分支头） | `edc2caf8…` | `LICENSE` |
| `webview2-com-sys` | 0.39.1 | MIT | wravery/webview2-rs | commit（默认分支头） | `edc2caf8…` | `LICENSE` |

完整 URL（`raw.githubusercontent.com`，按上述 commit 固定）见 `upstream/SOURCES.json` 的 `files[].url`。要点：

- `defmt-parser` 无 `defmt-parser-v1.0.0` tag，使用同一 1.0.0 发布的 workspace tag `defmt-v1.0.0`。
- `selectors`/stylo 仓库**不提供 LICENSE 文件**；许可依据来自 crate `Cargo.toml` 的 `license = "MPL-2.0"` 与仓库 README 第 105 行"Stylo is licensed under MPL 2.0"，`MPL-2.0.txt` 为 Mozilla 官方规范全文。
- `webview2-com` 系列仓库无发布 tag，使用默认分支头 commit。

## 6. Python 冻结引擎依赖

来源：`scripts/requirements-desktop-windows.lock.txt`（19 个固定版本）+ 冻结 venv
`runtime/desktop-build-venv/Lib/site-packages/*.dist-info`。19 个发行包全部收集到许可文本：

| 发行包 | 版本 | 许可 |
|---|---|---|
| `pydantic` / `pydantic_core` | 2.13.5 / 2.46.5 | MIT |
| `keyring` | 25.7.0 | MIT |
| `pyzipper` | 0.3.6 | MIT |
| `pyinstaller` | 6.22.3 | GPL-2.0-or-later **含 Bootloader 例外**（见下） |
| `pyinstaller-hooks-contrib` | 2026.8 | Apache-2.0 / GPL-2.0 |
| `pycryptodomex` | 3.24.0 | BSD / Public Domain |
| `packaging` | 26.3 | Apache-2.0 OR BSD-2-Clause |
| `setuptools` | 84.0.0 | MIT |
| `altgraph`、`pefile`、`pywin32-ctypes` | — | MIT / MIT / BSD-3-Clause |
| `jaraco.*`、`more-itertools` | — | MIT |
| `annotated-types`、`typing-inspection`、`typing_extensions` | — | MIT / MIT / PSF-2.0 |

说明：`pip`（25.1.1）存在于 venv 但不在锁定文件内，属构建工具，不计入分发清单。`setuptools` 记录在锁定文件中，需按实际是否被 PyInstaller 收集核对。

## 7. PyInstaller 例外与 7-Zip 发布许可

### 7.1 PyInstaller Bootloader 例外

`pyinstaller` 6.22.3 元数据许可：`GPLv2-or-later with a special exception which allows to use PyInstaller to build and distribute non-free programs (including commercial ones)`。其 `COPYING.txt`（已收集）写明 Bootloader 例外：

> Bootloader Exception — In addition to the permissions in the GNU General Public License, the authors give you unlimited permission to link or embed compiled bootloader and related files into combinations with other programs, and to distribute those combinations without any restriction coming from the use of those files.

即：用 PyInstaller 构建并分发**非自由/商业**程序是允许的；该例外只针对 bootloader 与相关文件的链接与分发，GPL 的其他约束（例如修改这些文件本身）仍然适用。**结论：本项目以 PyInstaller 冻结引擎不触发 GPL 传染限制，但须随包保留 PyInstaller 许可与例外说明。**

### 7.2 7-Zip 发布许可

随包工具：`tools/7zip/Files/7-Zip/`（`7z.exe`、`7z.dll` 等），来源 7-Zip **25.01** x64，`readme.txt`、`License.txt` 已收集。

- `7z.dll` 主许可 GNU **LGPL**；其中部分代码为 LGPL with unRAR restriction、BSD 3-clause、BSD 2-clause。
- 其余文件：GNU **LGPL**。
- 二进制再分发要求：**必须复现 `License.txt` 中相关许可信息**。

unRAR 限制原文（`License.txt` 第 131–146 行）：

> The unRAR sources cannot be used to re-create the RAR compression algorithm, which is proprietary. Distribution of modified unRAR sources in separate form or as a part of other software is permitted, provided that it is clearly stated in the documentation and source comments that the code may not be used to develop a RAR (WinRAR) compatible archiver.

含义：7-Zip 自带 RAR **解压**能力合法，但不得用来开发 RAR 兼容的**压缩**器。本项目只用其解压。随包必须附带 7-Zip `License.txt`；`scripts/stage_desktop_engine.py` 已强制校验 `License.txt` 存在，缺失即报错。

## 8. UnRAR 固定依赖与未随包工具

UnRAR **7.13.0 x64** 从 Windows 宿主已签名的稳定 CLI 获取，EXE 未修改。Git 输入为 [unrar-7.13-windows-x64.zip](../tools/desktop-vendor/unrar-7.13-windows-x64.zip)，版本、来源、包及逐文件 SHA-256 由 [desktop-tools.lock.json](../scripts/desktop-tools.lock.json) 固定；stage 对缺失/哈希不符报错，不用 PATH 或最新 beta 替代。工具落在 `engine/tools/unrar/`，与两个公开许可文本一起进入 NSIS / ZIP。

RARLAB 独立 UnRAR `license.txt` 原文：

> The UnRAR utility may be freely distributed. It is allowed to distribute UnRAR inside of other software packages.

安装版公开 EULA 同时保留 “with the exception of the UnRAR components” 的单独分发例外。随包携带 `UnRAR-License.txt` 和 `WinRAR-License.txt`；本项目仅解压，不用于重造专有 RAR 压缩算法。官方独立 addon 当前为 7.30 beta 1，本次仅用其公开免费工具许可，不用其 beta EXE。

Bandizip 官方 [EULA](https://www.bandisoft.com/bandizip/eula/eula.en.pdf) 2.3/2.4 明确要求书面许可后才能复制/分发产品。已核对英文原文；11.4 约定冲突时以韩文版为准。宿主 `bz.exe` 7.40.0.1 的签名及帮助命令正常，但标准使用许可不授予捆绑分发权，未取得独立 SDK/CLI 授权或 6.29 的不同许可，故当前不打入包。本机独立安装的 `bz.exe` 仍可在设置里指定。

| 组件 | 原因 | 证据 |
|---|---|---|
| Bandizip | EULA 2.3/2.4 要求书面分发许可，当前未取得 | `scripts/desktop-tools.lock.json` 的 `not_bundled`、官方 EULA；`build-info.json.excluded` |
| `restoreAB.exe` | 历史用户工具，不在桌面发行包 | 同上 |

原生恢复逻辑复用项目实现；默认解压由随包开源 7-Zip 执行，UnRAR 作为 RAR 备用。不自动安装或复制宿主 Bandizip。

## 9. 未确认 / 待核实部分

以下条目**尚未确认**，交由主线程在打包或后续阶段核对，不得当作已验证：

1. **Cargo 锁中 208 个 crate 不在 Windows 依赖图内**：主线程的 `cargo metadata --filter-platform x86_64-pc-windows-msvc` 得到 254 个可达 registry 包；`Cargo.lock` 其余 208 个 registry 条目不属于当前 Windows 图（多为 Linux/macOS/Android/WASM 平台条件依赖与未启用特性）。这些条目本地无源码、未逐条取证，**不随 Windows 首版分发**；`manifest.json` 已用 `cargo_scope.not_in_windows_graph` 与其分开记录。若将来加入对应平台目标，需重新收集。
2. **上游补充文本的版本对齐**：`defmt-parser 1.0.0` 用 workspace tag `defmt-v1.0.0`（无该 crate 的独立 tag），`selectors`/`webview2-com*` 用默认分支头 commit（前者仓库无 LICENSE 文件、后者无发布 tag）。这些是可复现的固定 ref，但未必逐字节等于发布该版本当时的快照；文本为项目级稳定许可，风险低。
3. **`selectors` 许可文本来源**：上游仓库不提供 LICENSE 文件，`MPL-2.0.txt` 取自 Mozilla 官方规范全文，"MPL-2.0" 声明依据 crate `Cargo.toml` 与仓库 README。注意 `selectors` 为 **MPL-2.0**，若随包分发须保留其许可与来源说明。
4. **npm `is-reference` / `locate-character`**：仅 `metadata`，无文本文件。
5. **冻结引擎实际收集集合未逐条核对**：本清单按锁定文件与 venv 元数据整理，未逐一比对本机 PyInstaller 产物实际包含的 Python 模块；`setuptools`、`pip` 等是否随包需按最终产物确认。
6. **`tools/apate.py`（Apate 伪装还原）**：随引擎暂存一并复制，属第三方算法来源（https://github.com/rippod/apate）的 Python 复刻；本地文件与 `APATE.md` 均未声明许可，**上游许可待确认**，本清单未纳入 ECMA 生态自动收集。
7. **应用自身许可与 WebView2 运行期**：项目根 `LICENSE`（尚未建立）与 Windows WebView2 Runtime 的分发/许可约定不在本文范围，由主线程决定。

## 10. 主线程集成职责（交接）

- 将本项目根 `LICENSE` / `NOTICE` 与你选定的许可汇总合并进发行包；本脚本不生成、不替代根许可。
- 依据 `manifest.json` + `SUMMARY.md` 决定哪些许可文本随安装包分发，并把完整文本从 `artifacts/desktop/license-evidence/` 打入最终产物（该目录被忽略，不随 Git）。
- 处理第 9 节的未确认项；`unresolved` / `metadata` 条目在上游补全前不得在发行文档中标注为"已验证"。
- 将锁定 UnRAR 与许可打入发行包；未取得书面许可前不把 Bandizip 打入发行包，继续排除 `restoreAB`。

## 11. 证据入口

| 内容 | 路径 |
|---|---|
| 收集脚本 | `scripts/collect_desktop_licenses.py` |
| 逐条清单 | `artifacts/desktop/license-evidence/manifest.json` |
| 分生态汇总 | `artifacts/desktop/license-evidence/SUMMARY.md` |
| npm 文本 | `artifacts/desktop/license-evidence/npm/<pkg>@<ver>/` |
| Cargo 文本 | `artifacts/desktop/license-evidence/cargo/<crate>-<ver>/` |
| Python 文本 | `artifacts/desktop/license-evidence/python/<dist>-<ver>/` |
| 7-Zip 文本 | `artifacts/desktop/license-evidence/7zip/License.txt` |
| UnRAR 固定输入 | `scripts/desktop-tools.lock.json`、`tools/desktop-vendor/unrar-7.13-windows-x64.zip` |
| UnRAR 许可 | `artifacts/desktop/license-evidence/unrar/{UnRAR-License.txt,WinRAR-License.txt}` |
| Windows 工具/条款取证 | `artifacts/desktop/fixed-tools-20261008/` |
| 上游补充文本 | `artifacts/desktop/license-evidence/upstream/<crate>-<ver>/` |
| 上游来源与校验 | `artifacts/desktop/license-evidence/upstream/SOURCES.json`（repository/ref/commit/url/sha256） |
| Windows 可达图（主线程） | `artifacts/desktop/windows-dependencies.json` |
| 范围摘要（主线程） | `artifacts/desktop/windows-license-scope.json` |
