# 星绫解封 · Hoshiribbon 桌面版：压缩包格式与解压说明

本说明面向拿到星绫解封（Hoshiribbon）Windows 桌面版的用户和验收人：它讲清楚软件能处理哪些压缩包、每种情况会走哪条路、成品和原件大致会落到哪里，以及哪些属于“尽力识别”而不保证成功。文中把“能稳定覆盖的格式”“靠识别与还原兜底的伪装/嵌套”“注定会失败或转入人工处理的情况”分开写。

相关文档：安装、界面操作与人工验收清单见 [desktop-user-guide.md](desktop-user-guide.md)；随包组件与许可见 [desktop-third-party.md](desktop-third-party.md)；伪装机制背景见 [apate_multi_layer_mechanism.md](apate_multi_layer_mechanism.md)。

## 0. 先记住这几条

- 处理顺序是：**分组 → 识别 → 还原伪装 → 解压 →（可选）继续解开内层 → 发布成品、归档原件**。原件在发布阶段才移动，处理过程中不会改写输入。
- 主要处理路径覆盖 **ZIP / 加密 ZIP、7z（含分卷）、RAR / RAR5（含分卷）**。tar/gz 等依赖7-Zip的格式能力与项目识别路径，实际效果仍待验收，不能据此声称稳定覆盖。
- **伪装后缀、合并进图片/视频的归档、Apate 伪装、多层嵌套**都有对应的识别与还原路径，但属于“尽力命中”，不覆盖所有变体。
- **任何格式都不承诺 100% 成功**。缺分卷、密码不在密码集、文件损坏、专有格式都会失败或转入对应分类目录。
- Bandizip 是**可选备用工具**；它支持的专有格式（如 ALZ/EGG）**不代表本项目端到端可用**，原因见第 4 节。

## 1. 解压工具：必需、备用、可选

| 工具 | 角色 | 本项目中的位置 |
| --- | --- | --- |
| 7-Zip **25.01** | **必需**。所有候选解压前的安全检查都用它做预检；ZIP 之外的格式基本靠它解压 | 随包内置（`engine\tools\7zip`） |
| UnRAR **7.13** | **备用**。处理 RAR / RAR5（含分卷）时质量最好 | 随包内置（固定版本、含许可） |
| Bandizip `bz` **7.40.0.1** | **可选**。可作额外兜底解压工具 | 固定随包内置 |

关于 Bandizip 的分发许可：**依据用户 2026-10-08 声明，已取得 Bandisoft 的书面分发许可**。本说明只记录这一声明，未查阅、也未核验许可函原文；不要求上传复核。

关于内置版本与旧检查的关系：本文的 7-Zip 25.01 / UnRAR 7.13 / Bandizip 7.40.0.1 是**本轮固定随包版本**。当前冻结引擎（`artifacts/desktop/native-engine-final.json` 等）是在旧工具集下产出的合成检查，**新引擎已重新冻结；本轮只做代码、合成fake与资源检查，旧 smoke 不等于新验收**。

如果 7-Zip 缺失，软件会直接提示“没有找到 7-Zip，请在设置中选择 7z/7zz”，不会在没有 7-Zip 的情况下硬跑其它工具。

## 2. 处理该怎么走（用户视角）

1. **分组**：把一批文件按“分卷集合”归类，并挑出入口卷（例如 `.7z.001`、`.part01.rar`、`.r00` 的同伴 `.rar`）。
2. **识别**：看文件头签名（ZIP/7z/RAR4/RAR5/gz）和文件名，判断“这大概是什么、该用哪个工具”。
3. **还原伪装**：命中伪装规则时先还原再解压，例如去掉多加的后缀、剥离前置的图片/视频头、还原 Apate 伪装。还原都是可回滚的：解压失败会把改名/改动还原回去。
4. **解压**：按“工具 × 密码”矩阵依次尝试，直到成功；缺分卷会直接停下并转入等待补卷。
5. **继续解开内层（深度解压，默认开）**：解出的内容里若还有压缩包，会继续往下解，默认最多 4 层。
6. **发布 + 归档**：把成品放到 `final\`，原件移动到 `success\archives\`；失败/缺卷/损坏按分类移动（见第 6 节）。桌面 0.3.1 起这些目录都在你选择的**工作文件夹**下。

## 3. 格式 × 处理路径 × 预期 × 限制 × 证据

“证据类型”一列含义：

- **源**＝由当前源码路径支持（本次静态阅读得出）。
- **史**＝有历史合成机器证据（`artifacts/desktop` 下 2026-10-07/08 的合成样本检查），**不是**本次新跑。
- **模**＝本次未运行，仅按源码静态推演（只到“写法支持”的程度）。
- **验**＝需要真实文件 / GUI 的人工验收，**尚未做**。

| 格式 / 情形 | 处理路径 | 成品与原件的预期 | 限制与注意 | 证据类型 |
| --- | --- | --- | --- | --- |
| **普通 ZIP** | 识别签名 → 预检（优先用 Python 直接读目录）→ 7-Zip 解压 | 成品到 `final\`；原件进 `success\archives\` | 声明成员过多、声明解压过大、目录记录不完整会被拦下 | 源、史（合成 ZIP）、验 |
| **加密 ZIP（含 AES）** | 用明文密码文件里的条目轮询，再交给 7-Zip 解压 | 解开后同上；密码匹配则成功 | 密码不在密码文件 → 归 `error_files\password_error\`；ZIP64 大目录转 7-Zip 预检 | 源、史（AES ZIP）、验 |
| **7z / 7z 分卷（`.7z.001/.002`）** | 分卷归一组、入口取 `.7z.001` → 7-Zip 解压 | 成品到 `final\`；整组原件进归档 | 分卷必须齐全；缺卷转等待补卷 | 源、史（7z 分卷）、验 |
| **RAR / RAR5（`.partNN.rar`、`.r00`、`.rar`）** | 分组含老式 `r00` 与新式 `part`；优先用 UnRAR，失败再试 7-Zip | 同上 | 缺分卷或密码错误分别转“无卷”/`password_error`；个别变体名字要试探性改名后再试 | 源、模、验 |
| **tar / gz / tar.gz / tgz / bz2 / xz** | 只有 7-Zip 能预检与解压；按名字与签名识别 | 解开后成品到 `final\` | 这些格式没有独立的“签名级”识别分支，主要靠 7-Zip 预检能否列目录 | 源、模、验 |
| **伪装后缀（如 `.zip.jpg`、`.jpg` 实为压缩包）** | 命中改名候选（改为 `.zip`/`.7z`/`.rar`）后再解压；成对出现时识别“首个卷被伪装” | 解压成功则成品到 `final\`，原件整组归档 | 属于试探性改名；改名会回滚，不命中就退回原始状态 | 源、史（restoreAB cover 场景相关）、验 |
| **合并文件嵌入归档（前缀图片/视频 + 尾部归档）** | 扫描封面头之后的归档签名并剥离前缀；ZIP 还会修复偏移得到独立包 | 还原出的归档继续解压到 `final\` | 只扫描一定范围内的前缀；过大前缀不处理；属于尽力恢复 | 源、史（restoreAB cover）、验 |
| **Apate 伪装（尾部 4 字节长度标记）** | 读尾部标记还原真实头（可多轮）；名字带 `two/three` 等提示会多试几轮 | 还原后再解压到 `final\` | 只处理规则内的常规分支；伪装被识别错时不会强行改文件 | 源、模、验 |
| **多层嵌套（内层还有压缩包）** | 深度解压按层继续解开，默认最多 4 层 | 最终成品到 `final\`；原件整组归档 | 低于“嵌套识别下限”的文件不深挖；达到“成品阈值/多文件夹”即判定为成品停下 | 源、史、验 |
| **缺分卷** | 解压报“缺卷/数据不可用”时立即停止 | 该组转为“等待补卷”，原件进 `deferred_volumes\<组>\` | 缺卷不靠换工具或换密码补齐 | 源、验 |
| **错密码 / 未收录密码** | 全矩阵尝试后仍失败 | 该组归 `error_files\password_error\`，原件一并移动 | 某些工具把“格式不符”说成“打不开”，此时会优先保留更可读的密码错误信息 | 源、史（fallback-tests.log 覆盖分类）、验 |
| **文件损坏 / 无法读取** | 预检列不出成员 → 判定不可读，不进入解压 | 归 `error_files\` 相应分类 | 宁可不解压，也不产出半成品；无法用它做密码探测 | 源、验 |
| **不安全路径（越界/设备名/链接/超大）** | 预检即拒绝：越界路径、路径段含冒号、设备名、符号链接/硬链接、声明过大一律停止 | 该候选不解压；不产生越界文件 | 失败即“关闭失败”（fail closed），不做宽松放行 | 源、史（unsafe member）、验 |
| **SFX（`.exe` 自解压）/ 无后缀 / 数字分卷尾部混入伪尾缀** | 按名字与签名构造候选，必要时改名后解压 | 成功则成品到 `final\` | 属于兜底规则；不保证每个变体都命中 | 源、模、验 |
| **Bandizip 专有格式（ALZ/EGG 等）** | 预检仍走 7-Zip；7-Zip 列不出目录时该候选直接判不可读 | **不会端到端解出** | 见第 4 节：预检门槛在 7-Zip，Bandizip 帮不了这一步 | 源、模、验 |

## 4. 为什么“7-Zip 预检”是一条硬门槛

每个候选在真正解压前都要先过安全检查（`GuardedExtractor`）。这一步**只调用 7-Zip**（`7z l -slt`）来列出成员、检查路径与大小；ZIP 则优先用 Python 直接读中央目录，遇到 ZIP64 再退回 7-Zip。

后果很直接：**如果 7-Zip 无法列出某个归档，该候选就会被判为“不可读”并跳过解压**，即使你另外选了 Bandizip 也不会走到它那一步。因此：

- Bandizip 的帮助里写它支持 ALZ、EGG 等格式，**只说明 Bandizip 自己能解**，不代表本项目会端到端解开这些格式。
- 本项目里 Bandizip 的角色是“当 7-Zip 能预检、但解压不稳时的备用解压工具”，而不是“绕过 7-Zip 预检的专用格式入口”。

所以本说明**不宣称 ALZ/EGG 等专有格式可用**；这类文件更可能落到失败/未知类型分类，需要你在同类工具里单独处理。

## 5. 公开密码文件与关键词词库

- 随包词库本次统计为 **115 条公开密码、8 条关键词**（此处不展示词条原文）。
- **公开密码**只由一个明文文件承载：首次运行在数据目录生成 `passwords.txt` 并播种那 115 条，之后完全由你维护。它不加密、不走系统凭据后端，**日志与错误信息也不再掩码**——请按公开数据对待，不要把私人密码写进去。
- 每行一个条目、UTF-8，空行忽略；重复、空格和 `#` 都按字面内容保留。界面“导入密码文件”是追加，“保存密码列表/清空”才整体替换；外部直接改文件在下次使用或重开设置时生效；清空并重启不会补回默认。0.3.0 的“使用内置密码库”开关已取消。
- “**清理内置关键词**”默认**关闭**。关键词清理只涉及成品顶层命名这一层，**不改动原件，也不改变包内层级**。
- 逐条行为对照与证据状态见第 9 节。

## 6. 原件与成品的去向

处理完成后，输出根目录下会出现这些分目录：

| 目录 | 内容 |
| --- | --- |
| `final\` | 解出的成品（保留内容原名时用内容自带的最外层名字，否则用包名） |
| `success\archives\` | 成功处理的**原件**（整组一起移动） |
| `error_files\<分类>\` | 失败原件与（若有）部分解出的内容，分类如 `password_error`、`unknown_type`、`extract_failed`、`missing_volume` |
| `deferred_volumes\<组>\` | 缺分卷等待补卷的原件 |
| `intermediate\` | 本次运行的临时工作区（副本、恢复与解压中间结果）；开启“保留临时工作区”才留在盘上 |

桌面 0.3.1 起上述目录都位于你选择的**工作文件夹**下（`final\`、`success\archives\`、`error_files\`、`deferred_volumes\` 直接在其根，临时区在 `intermediate\workspaces\<作业>\<文件组>\`，占用时改用唯一同级目录）；解压工具的 TEMP/TMP/TMPDIR 与当前目录也在该临时区内。软件复用已存在的同名目录，但**从不覆盖已有文件**，冲突项安全改名或进入 `_duplicates`，也不会清理你原有的内容。旧数据目录 `work\` 只作只读遗留恢复。

处理前会提示：原件按文件组归档，不会预先展平输入；**处理前不会改写原件**。

## 7. 不覆盖 / 不保证

- 不保证 ZIP / 7z / RAR 之外的每种变体都命中；tar/gz 系主要依赖 7-Zip。
- 不保证所有伪装与嵌套都能识别；改名或还原都是可回滚的试探。
- 不处理缺分卷、密码未收录、文件损坏这几类“客观上无法解开”的情况。
- 明确不宣称 ALZ/EGG 等 Bandizip 专有格式端到端可用（见第 4 节）。
- 真实文件效果、界面体验、安装卸载仍属于人工验收范围。

## 8. 证据、证据类型与未验证项

本说明依据源码对照与检查证据。0.3.1 已对授权的 BG57 两卷副本做 Windows 原生工具检查，原件不变；其他真实格式效果与 GUI 仍待人工验收。可核对的位置（仓库 `/mnt/d/buff/reorder`）：

| 主题 | 位置 |
| --- | --- |
| 工具矩阵、失败分类、缺卷停止 | `src/reorder_engine/services/extracting.py`（class `ExtractionService` L132；密码标记 L136；格式不符标记 L151；`_failure_disposition` L192；缺卷停止 L207；`extract_one` L215） |
| 7-Zip 预检、超限、链接、失败关闭 | `src/reorder_engine/infrastructure/archive_safety.py`（`validate_member` L20；`WorkspaceGuard` L29；`ArchiveSafetyInspector` L55/L63；`_inspect_cli` 用 `7z l -slt` L99/L129；不可读 L135；`GuardedExtractor` L138/L153/L158） |
| 分组与入口卷选择 | `src/reorder_engine/services/grouping.py`（`_group_key` L31；分卷规则 L53/L65/L70/L76/L82；`_pick_entry` L112） |
| 合并文件嵌入归档（restoreAB 类） | `src/reorder_engine/services/restore_ab.py`（签名 L19–L23；`RestoreABRestorer` L192；封面后缀 L195） |
| 签名识别、嵌入扫描、Apate、多层候选 | `src/reorder_engine/services/restoring.py`（签名 L67；后缀 L74；媒体后缀 L87；嵌入后缀 L119；`probe_embedded_archive` L204；`probe_apate` L333；`preferred_tool_for_suffix` L374；`ApateRestorer` L849；`EmbeddedArchiveRestorer` L930；`SuffixVariantBuilder` L1017；`build_post_extract_candidates` L1163） |
| 深度解压、失败分类、缺卷、成品判定 | `src/reorder_engine/services/beta_pipeline.py`（`_continue_after_extract` L365；`_nested_candidates` L443；缺卷识别 L1026；`_failure_category` L1032；成品判定 L1102） |
| 输入过滤、原件不改写 | `src/reorder_engine/application/planning.py`（排除后缀 L16；排除名单 L17；原件不改写提示 L105） |
| 内置词库读取与校验 | `src/reorder_engine/infrastructure/builtin_defaults.py`（清单与只读校验 L108 起；`password_count` L149；`keyword_count` L153） |
| 处理开关默认值 | `src/reorder_engine/application/models.py`（`deep_extract` 默认真、`preserve_payload_names` 默认真、`clean_builtin_keywords` 默认假、`work_root` 新增；`use_builtin_passwords` 仅作旧配置兼容） |
| 公开密码文件 | `src/reorder_engine/infrastructure/secret_store.py`（`PasswordFile`）、`src/reorder_engine/application/facade.py`（`passwords.replace/import`、`settings_info`） |
| 工作文件夹与设置记忆 | `src/reorder_engine/application/models.py`（`DesktopSettings.work_root`）、`apps/desktop/src/lib/contracts.ts`、`apps/desktop/src/App.svelte`、`apps/desktop/src/lib/desktop-controller.ts` |
| 发布与归档的文件动作 | `src/reorder_engine/infrastructure/file_transaction.py`（`install_exclusive`、`publish`、`route_sources`） |

已有机器证据（**历史合成，非本次**）：

- `artifacts/desktop/native-engine-final.json`：合成 ZIP、AES ZIP、restoreAB 封面、7z 分卷、不安全成员、密码脱敏；平台 Windows；`not_tested` 含“原生强制中断”“真实用户文件”。
- `artifacts/desktop/native-engine-smoke.json`：同范围合成检查（含中断恢复）。
- `artifacts/desktop/fixed-tools-20261008/fallback-tests.log`：`ExtractionService` 失败分类的固定工具回退测试（8 passed）；该日志所属引擎为旧工具集。

0.3.1 当前证据（`artifacts/desktop/hoshiribbon-0.3.1/`，本地 ignored）：

- `formats/real-bg57-evidence/SUMMARY.md`：Windows Python + 7-Zip 25.01，对 BG57 两个 Apate 伪装分卷的隔离副本解压成功（exit 0，231 个成品文件），两个原件 SHA-256 前后相同。该早期 harness 曾缺少生产还原器顺序，后已补齐；最终以实际冻结 EXE 检查为准。
- `tests/test_desktop_disguised_volumes.py`：17 个合成用例覆盖卷组预检、逐卷还原、失败回滚、越界成员拒绝与成功缓存失效、工具清理临时输出的扫描竞态，以及保留容量/链接拒绝。
- `workspace/ws-031-work-evidence.json`：14 个新工作文件夹用例及 30 个既有回归通过，覆盖目录复用、冲突保护、同盘不调用校验复制、源删除阶段和恢复路径。
- 密码 27 个定向用例、前端 16 个控制器用例与类型/构建均通过，见实施状态。
- `main/bg57-native-final.json`：最终 Windows 冻结 EXE 的实际 stdio 任务入口处理 BG57 隔离副本成功，231 个成品文件；2 个归档副本的原始字节 hash 与原件相同，原件未变（exit 0）。
- `main/native-engine-delivery-final.json`：重新冻结的 Windows EXE 通过普通 ZIP、AES ZIP、嵌入封面、7z 分卷和 Apate 伪装 ZIP 分卷五组小型合成输入，源哈希/已有目录内容保持与重启记忆通过，越界成员被拒绝（exit 0）。

未验证项（留给人工验收）：

- BG57 之外的真实压缩包、真实密码、真实分卷端到端效果；
- 伪装 / Apate / 合并嵌入 / 多层嵌套在真实样本上的命中率；
- **重新冻结后的新引擎**（7-Zip 25.01 + UnRAR 7.13 + Bandizip 7.40.0.1）的真实行为；
- Bandizip 专有格式是否被 7-Zip 预检放行（设计上不会，需实测确认）；
- 真实文件效果与界面体验的最终判定（属业务验收）。

## 9. 桌面端保留的旧引擎行为对照（0.3.1）

桌面端**包裹现有引擎**，不缩减旧管线能力；下表把用户在意的行为逐条列清楚。证据类型沿用第 3 节的 `源 / 史 / 模 / 验`，并新增 `策`：已批准设计，本轮实现方向，机器证据由主线程汇总，本文不预判通过。

| 主题 | 旧引擎（portable 基线）行为 | 桌面 0.3.1 | 证据 |
| --- | --- | --- | --- |
| 分卷分组 | 按分卷集合分组并挑入口卷（`.7z.001`、`.partNN.rar`、`.r00`、`.rar`） | 复用同一分组策略；补齐 `.zip.001.mp4` 一类逐卷 Apate 还原与完整卷组预检 | 源 + 模 + BG57 隔离实测 |
| 原始名 / 内容名 | `preserve_payload_names` 开：成品用最深单层包装目录名；关：用压缩包基础名 | 界面“保留内容原名”开关，语义相同 | 源 |
| 单包装成判定 | 目录内 ≥10 个子目录或 ≥80 个文件即判为成品，停止继续深挖 | 同上阈值，不再往下拆 | 源 |
| 嵌套候选 | 唯一非媒体文件是归档（或 ≥max(识别下限, 单文件阈值)）；出现成品媒体即停止；多个归档取体积最大的前 5 个；否则取 ≥单文件阈值的最大一个 | 同上 | 源 |
| 深挖停止 | 默认最多 4 层、识别下限 100 MB、单文件阈值 200 MB | 同上默认值；桌面**默认开启**深度解压（旧 CLI 默认关闭），用户可关 | 源 |
| 伪装与合并恢复 | 后缀候选、restoreAB 合并、Apate（默认 3 轮）、前后缀嵌入、尾部 `*sc` | 全部保留 | 源 |
| 输出目录 | `final` / `success/archives` / `error_files` / `deferred_volumes` / `intermediate` | 目录语义不变，整体落在所选**工作文件夹**下 | 源 + 策 |
| 失败归类 | 缺卷 → `deferred`；有残留且失败 → `partial`；错误分类 `password_error` / `unknown_type` / `missing_volume` / `extract_failed` | 同上 | 源 |
| 输入是否展平 | 桌面处理前不展平原件（已批准边界） | 保持不展平；原件按文件组归档 | 源 + 策 |
| 发布 / 归档的 I/O | 在单一处理目录内用 `shutil.move` 改名/移动 | 先在工作文件夹内备好暂存副本，再用同卷**独占链接/改名**落位（`os.link` 优先，Windows 退化为独占改名）；跨卷或不可用时回退到校验复制 | 源 + 定向文件事务检查（大盘性能未测） |
| 可调项范围 | 旧 CLI 有更多自定义项 | 桌面只暴露其中一部分（深挖、层数、识别下限、单文件阈值、保留原名、保留工作区等），**不宣称所有 CLI 开关都已暴露** | 源 |

说明：

- 上表按当前源码与 portable 基线对照得出，属于**静态核对**；桌面相对旧管线的增量是结果上报、源路由修正与安全预检；确认的 Apate/嵌入伪装在多卷路径中优先交给对应还原器，避免合并文件扫描器抢先误判，旧文件夹策略、命名保留与嵌套整理都保留。工作文件夹布局与独占移动已在当前工作区实现（`infrastructure/workspace.py`、`infrastructure/file_transaction.py`），有限机器检查见第 8 节，Windows 冻结引擎检查以实施状态记录为准。
- 桌面发布与原件归档优先用同卷独占链接/改名落位而不是重拷贝，跨卷或无法独占时回退到校验复制；这不等于“绝对没有额外 I/O”，是否需要复制/校验取决于卷与文件系统，实际 I/O 次数未在真实大盘上测量。
- 当前多卷扩展覆盖已确认的逐卷单层 Apate 伪装；更复杂的多重伪装或混合嵌入卷组仍需单独样本验收。
- 0.3.1 已验证 BG57 这对样本；其他真实文件命中率、性能数字与界面体验仍待人工验收；本文不把静态或合成结论当作业务验收。
