# 星绫解封 · Hoshiribbon 桌面版：解压工具来源与取舍（HOSHI-033）

本说明汇总 0.3.3（磁盘读写优先）期间为“工具退出语义、7-Zip 版本、ALZ/EGG 覆盖”三处缺口做的公开来源取证与有界合成实验，并说明本项目据此采纳的做法。它是给验收人和制作方看的**精简结论**，原始长证据留在同仓库的以下两份文件里，本文只做压缩与链接，不重复粘贴日志：

- `artifacts/desktop/hoshiribbon-0.3.3/research/HOSHI-033-TOOLS-tooling-research.md`（公开来源调研，含来源链接与证据分级）
- `artifacts/desktop/hoshiribbon-0.3.3/research/HOSHI-033-TOOLS-EXPERIMENT.md`（合成样本真实工具实验，含退出码矩阵）

证据分级沿用调研文：**源**＝官方文档或源码；**史**＝历史机器证据；**推**＝推断；**未**＝未联网核实或未见官方文档。本文结论区分“已实测”“官方记载”“推断”，不把公开资料当作随包 Windows 二进制的实测通过。

相关文档：格式与处理方式见 [desktop-format-support.md](desktop-format-support.md)；随包组件与许可见 [desktop-third-party.md](desktop-third-party.md)。

## 1. 随包工具与版本

| 工具 | 0.3.3 版本 | 角色 | 来源 |
| --- | --- | --- | --- |
| 7-Zip | 26.04（x64，2026-10-05） | 必需：所有候选解压前的安全预检，ZIP 之外的格式主要靠它解压 | `源`（官方 release + `history.txt`）|
| UnRAR | 7.13.0 | 备用：处理 RAR / RAR5（含分卷） | `源/史`（`rar.txt` + 本仓库锁）|
| Bandizip CLI `bz` | 7.40.0.1 | 可选：额外兜底解压工具 | `源`（Bandisoft 命令行页）|

7-Zip 由随包 **25.01 受控升级到 26.04**：版本、URL、逐文件 SHA-256 固定在 `scripts/desktop-sevenzip.lock.json`，随包固定目录为 `tools/7zip/26.04/`（`7z.exe`、`7z.dll`、`License.txt`、`readme.txt`）；25.01 的 MSI 与解包目录作为历史保留，不就地覆盖。升级理由见第 3 节（25.01 之后多项安全修复），但升级的**功能必要性未在本项目语料上被证实**——合成样本上 25.01 与 26.04 行为一致。UnRAR 7.13.0 与 Bandizip 7.40.0.1 同样打包为固定依赖。

## 2. 三工具的退出码语义（关键差异）

- **7-Zip**：只有 6 个已定义码（`0` 成功，`1` 警告，`2` 致命，`7` 命令行，`8` 内存，`255` 用户中断）；数据/CRC 失败落入 `2`。`源`（上游 `ExitCode.h`）合成实验证实：正常/正确密码/尾部多出数据 `x` 退出 `0`（尾部告警也仍是 `0`），CRC 失败与错误密码退出 `2`，无密码的加密 ZIP 因交互式提示在 stdin=EOF 下退出 `255`。`史`
- **UnRAR**：14 个已定义码，能区分 `3` 校验错误、`11` 密码错误、`13` 坏档；只有加 `-kb` 才保留部分解出的字节（不加则删除）。`源`（WinRAR `rar.txt`）
- **Bandizip CLI**：**未找到**官方退出码文档。合成实验观测到 `17`＝未知档、`2`＝完整性/密码错误、`13`＝参数错误，但 `bz t` 在 AES 错误/缺密码时会**挂起**（60s 超时后被杀，退出 `124`）。`史/未`

据此，本项目失败分类只**以 stderr 文本标记为主**，退出码仅用于少数硬停（命令行/内存/超时/用户中断）。不依赖 Bandizip 的退出码类型。

## 3. 7-Zip 升级 / 旧版退化的已证实案例

- 25.01 之后的安全修复（`history.txt`）：`26.00` 改进 ZIP/CPIO/RAR/UFD/QCOW/Compound 并修 TAR 稀疏文件；`26.01` 新增 `-spo` 并修多个 CVE；`26.02` 修 `CVE-2026-14266`（XZ 解压堆溢出 RCE）；`26.03` 修 `CVE-2026-58052`（MOTW）；`26.04` 修若干 bug/漏洞。`源`
- 已证实的缺陷案例**限 Linux 打包**：Debian #1118733（`7zip-rar/25.01+ds-3`）对一个有效 RAR 的 `7z t` 报错，修复为“缺失的 CRC 表构造器”。**不能**据此断言随包 Windows 官方 25.01 有同样问题（那是 RAR 编解码被拆成 non-free 插件的 `+ds` 重打包）。`源`
- 社区性能退化报告（未获开发者确认）：SourceForge 7-Zip #2588，v25.01 首次运行创建 ZIP 的回归，属压缩路径、非解压正确性。`源`

结论：升级以**安全修复**为主要理由；是否升级的功能必要性在真实语料上未证实，因此按项目验收流程做受控升级评估，不在合成样本上宣称升级带来功能改进。

## 4. libarchive / bsdtar 与其他开源工具对 ALZ/EGG 的补充

- 7-Zip 官方格式清单**无 ALZ/EGG**；libarchive/bsdtar 格式清单同样**无 ALZ/EGG**，其余格式与 7-Zip 重叠。`源`
- 合成实验：Windows `tar.exe`（bsdtar 3.8.8）对 CRC 坏的 ZIP，`-tf` 仍返回 `0`，`-xf` 返回 `1` 但**仍写出损坏文件**。注意列表命令本身**不验证 CRC**：7-Zip `l` 与 bsdtar `-tf` 都不会因 CRC 坏而报错，因此不能靠“只列”判断文件完好。`史`
- ALZ/EGG 的“只列不写盘”候选：随包内只有 Bandizip `bz l`；外部为 The Unarchiver `lsar`（ALZ，非 EGG）、`blurfx/unegg -l`（ALZ+EGG）等。`源/推`

## 5. 0.3.3 采纳的做法

1. **7-Zip 受控固定升级到 26.04**，25.01 历史保留（第 1 节）。
2. **bsdtar 3.8.8 本版不捆绑**：对核心格式无实质补充（合成样本未显示超出 7-Zip 的可用能力）。
3. **ALZ/EGG 不宣称支持**：没有可安全预检的真实 fixture，且预检门槛在 7-Zip，7-Zip 列不出即判不可读；Bandizip 帮不了预检这一步。
4. **不启用 `bz t`**：存在挂起风险，不适合无监督自动化。
5. **失败分类以文本标记为主，退出码只做硬停**（第 2 节）。
6. RAR 抽取加 `-kb` 保留半成品；7-Zip 抽取钉 `-sccUTF-8`，无密码时用 `-p-`（字面 `-`）表示空密码，避免 7-Zip 进入交互式密码提示；只有完全不带 `-p` 时才会交互（见格式指南的停止策略）。

## 6. 未验证项

- 未在随包 Windows 二进制上复现 Debian #1118733 那类 RAR 缺陷；未在真实语料上比较 25.01 与 26.04。
- Bandizip CLI 退出码无官方文档；`bz t` 挂起的确切触发条件与真实交互会话下是否返回未知。
- 未用真实 ALZ/EGG fixture 验证 Bandizip `l/x` 或外部 `unar/lsar`、`unegg` 的列出输出能否被现有成员校验复用（需先取得 fixture）。
- 以上均为公开来源事实、标注推断或合成样本实验，**不能**替代真实文件与 GUI 的人工验收。

## 7. 来源清单

- 7-Zip 上游退出码：`CPP/7zip/UI/Common/ExitCode.h`（android.googlesource.com 镜像）
- 7-Zip 主页与版本历史：https://www.7-zip.org/ 、https://www.7-zip.org/history.txt
- 7-Zip x 命令手册：https://7zip.bugaco.com/7zip/MANUAL/cmdline/commands/extract_full.htm
- WinRAR/UnRAR `rar.txt`（退出码、`-kb`）：https://sources.debian.org/src/rar/2%3A7.11-1/rar.txt
- Bandizip 命令行参数：https://www.bandisoft.com/bandizip/help/parameter/
- libarchive 格式：https://github.com/libarchive/libarchive/wiki/LibarchiveFormats
- The Unarchiver 命令行：https://theunarchiver.com/command-line
- Debian #1118733：https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1118733
- SourceForge 7-Zip #2588：https://sourceforge.net/p/sevenzip/bugs/2588/
- blurfx/unegg：https://github.com/blurfx/unegg

版本号随时间变化（本次最新 7-Zip 26.04，2026-10-05）；引用的行号类信息一律以类/函数名或命令行为准，不引会随改动漂移的源码行号。
