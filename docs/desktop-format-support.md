# 星绫解封 · Hoshiribbon 桌面版：压缩包格式与解压说明

本说明面向拿到星绫解封（Hoshiribbon）Windows 桌面版的用户和验收人：它讲清楚软件能处理哪些压缩包、每种情况会走哪条路、成品和原件大致会落到哪里，以及哪些属于“尽力识别”而不保证成功。文中把“能稳定覆盖的格式”“靠识别与还原兜底的伪装/嵌套”“注定会失败或转入人工处理的情况”分开写。

更新：2026-10-18。2026-10-18 源码 partial 规则见第 11 节，旧 BAT/Beta 兼容切片见第 12 节。此次仅更新源码与说明，未更新已发布的 0.3.3 ZIP；真实用户 `393.7z.001/.002` 尚待人工验收。

相关文档：安装、界面操作与人工验收清单见 [desktop-user-guide.md](desktop-user-guide.md)；随包组件与许可见 [desktop-third-party.md](desktop-third-party.md)；工具来源与退出语义见 [desktop-tool-research.md](desktop-tool-research.md)；伪装机制背景见 [apate_multi_layer_mechanism.md](apate_multi_layer_mechanism.md)。

## 0. 先记住这几条

- 处理顺序是：**分组 → 识别 →（必要时）原地还原伪装 → 解压 →（可选）继续解开内层 → 发布成品、整理原件**。0.3.3 直接读选中原包，优先原地后缀调整，失败且没有可用结果才做原地 Apate / 嵌入封面 / restoreAB 还原；成功后同盘移动原包归档，**跨盘原包保留当前位置并登记结果**。详见第 11 节。
- 每个文件组只归入**六类核心处理方式**之一（直接解压、后缀改名、Apate 还原、嵌入封面剥离、restoreAB、强制 Apate 兜底）；嵌套层数与分卷数量不改变方式分类，详见第 11.1 节。
- 主要处理路径覆盖 **ZIP / 加密 ZIP、7z（含分卷）、RAR / RAR5（含分卷）**。tar/gz 等依赖7-Zip的格式能力与项目识别路径，实际效果仍待验收，不能据此声称稳定覆盖。
- **伪装后缀、合并进图片/视频的归档、Apate 伪装、多层嵌套**都有对应的识别与还原路径，但属于“尽力命中”，不覆盖所有变体。
- **任何格式都不承诺 100% 成功**。只有完整成功才进入完成状态；错误密码、非零退出或 CRC/Data Error 即使有非空输出也属于失败尝试。
- **全部适用尝试完成后仍有非空输出**：状态为“未完成，待检查”，输出放在 `incomplete/outputs/<包名>/`；原包所有分卷按原 basename 放在 `incomplete/archives/` 根目录，同名冲突沿用去重处理，不创建包级 archives 目录。详见第 11.4 节。
- Bandizip 是**可选备用工具**；它支持的专有格式（如 ALZ/EGG）**不代表本项目端到端可用**，原因见第 4 节。

## 1. 解压工具：必需、备用、可选

| 工具 | 角色 | 本项目中的位置 |
| --- | --- | --- |
| 7-Zip **26.04**（由 25.01 受控升级） | **必需**。所有候选解压前的安全检查都用它做预检；ZIP 之外的格式基本靠它解压 | 随包内置（`engine\tools\7zip`；版本/哈希锁在 `scripts/desktop-sevenzip.lock.json`） |
| UnRAR **7.13** | **备用**。处理 RAR / RAR5（含分卷）时为 RAR 专用工具 | 随包内置（固定版本、含许可） |
| Bandizip `bz` **7.40.0.1** | **可选**。可作额外兜底解压工具 | 固定随包内置 |

关于 Bandizip 的分发许可：**依据用户 2026-10-08 声明，已取得 Bandisoft 的书面分发许可**。本说明只记录这一声明，未查阅、也未核验许可函原文；不要求上传复核。

0.3.3 起 7-Zip 由 25.01 **受控固定升级到 26.04**（25.01 的 MSI 与解包目录作为历史保留，不就地覆盖），UnRAR 7.13.0 与 Bandizip 7.40.0.1 保持固定。升级理由、退出码语义与版本取舍见 [工具研究](desktop-tool-research.md)；升级的功能必要性尚未在本项目语料上证实，不宣称升级带来功能改进。

0.3.2 已重新冻结 Windows 引擎，并在实际 EXE 协议入口通过 5 组合成样本；证据见 `artifacts/desktop/hoshiribbon-0.3.2/native-engine.json`。该检查覆盖普通/加密 ZIP、封面恢复、7z 分卷与 Apate 分卷，没有把 RAR 或专有格式的静态支持写成实测通过。0.3.3 的工具与停止策略变更见第 11 节，最终冻结 EXE 证据由主线程记录在实施状态。

如果 7-Zip 缺失，软件会直接提示“没有找到 7-Zip，请在设置中选择 7z/7zz”，不会在没有 7-Zip 的情况下硬跑其它工具。

## 2. 处理该怎么走（用户视角）

1. **分组**：把一批文件按“分卷集合”归类，并挑出入口卷（例如 `.7z.001`、`.part01.rar`、`.r00` 的同伴 `.rar`）。
2. **识别**：看文件头签名（ZIP/7z/RAR4/RAR5/gz）和文件名，判断“这大概是什么、该用哪个工具”。
3. **还原伪装**：命中伪装规则时先还原再解压，例如去掉多加的后缀、剥离前置的图片/视频头、还原 Apate 伪装。失败尝试会回滚；成功可能保留修复内容与新后缀。桌面 restoreAB 走原地修复（同一路径，不生成派生副本，见第 11.3 节）；2026-10-14 起根 BAT/Beta 入口也使用原地接口（见第 12 节）。发生中断时按动作记录人工核对，不保证自动还原字节。
4. **解压**：隔离每次尝试的输出，遍历适用的配置密码、可用工具与直接解压 → 原地后缀调整 → 必要伪装还原路径；只有完整成功才可提前结束。真实格式不匹配可切换工具并跳过该工具的密码轮询。缺卷、权限、空间不足、取消、超时与安全失败直接停止，不盲目重试。
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
| `final\` | 完整成功后解出的成品（保留内容原名时用内容自带的最外层名字，否则用包名） |
| `success\archives\` | 成功处理的**原件**（整组一起移动） |
| `incomplete\outputs\<包名>\` | 全部适用尝试结束仍无完整成功时保留的非空部分内容；状态“未完成，待检查” |
| `incomplete\archives\` | 上述 partial 对应的原包全卷，使用原 basename；同名冲突沿用去重处理，不再套包名子目录 |
| `error_files\<分类>\` | 没有非空输出的失败原件，分类如 `password_error`、`unknown_type`、`extract_failed` |
| `deferred_volumes\<组>\` | 缺分卷等待补卷的原件 |
| `intermediate\` | 本次运行的临时工作区（恢复派生产物、解压中间结果与工具临时数据）；开启“保留临时工作区”才留在盘上 |

桌面 0.3.1 起上述目录都位于你选择的**工作文件夹**下（`final\`、`success\archives\`、`error_files\`、`deferred_volumes\` 直接在其根，临时区在 `intermediate\workspaces\<作业>\<文件组>\`，占用时改用唯一同级目录）；解压工具的 TEMP/TMP/TMPDIR 与当前目录也在该临时区内。软件复用已存在的同名目录，但**从不覆盖已有文件**，冲突项安全改名或进入 `_duplicates`，也不会清理你原有的内容。旧数据目录 `work\` 只作只读遗留恢复。

处理前会提示：原件按文件组归档，不会预先展平输入；**扫描只读；开始后必要的还原与后缀调整直接作用于选中原包**。

## 7. 不覆盖 / 不保证

- 不保证 ZIP / 7z / RAR 之外的每种变体都命中；tar/gz 系主要依赖 7-Zip。
- 不保证所有伪装与嵌套都能识别；改名或还原都是可回滚的试探。
- 缺分卷会进入 `deferred_volumes/`；密码失败或损坏在全部适用尝试失败后仍有非空内容时进入 `incomplete/` 待检查，没有文件时进入 `error_files/`。
- 明确不宣称 ALZ/EGG 等 Bandizip 专有格式端到端可用（见第 4 节）。
- 真实文件效果、界面体验、安装卸载仍属于人工验收范围。

## 8. 证据、证据类型与未验证项

本说明依据源码对照与检查证据。0.3.1 已对授权的 BG57 两卷副本做 Windows 原生工具检查，原件不变；其他真实格式效果与 GUI 仍待人工验收。可核对的位置（仓库 `/mnt/d/buff/reorder`）：

| 主题 | 位置 |
| --- | --- |
| 工具矩阵、失败分类、缺卷停止 | `src/reorder_engine/services/extracting.py`（class `ExtractionService`；`_PASSWORD_FAILURE_MARKERS`、`_FORMAT_MISMATCH_MARKERS`、`_CORRUPTION_MARKERS`、`_STOP_ALL_MARKERS`、`_STOP_ALL_EXIT_CODES`；`_failure_disposition`；`extract_one`；`_partial_result_for_output`） |
| 7-Zip 预检、超限、链接、失败关闭 | `src/reorder_engine/infrastructure/archive_safety.py`（`validate_member`；`WorkspaceGuard`；`ArchiveSafetyInspector`；`_inspect_cli` 用 `7z l -slt`；`GuardedExtractor`） |
| 分组与入口卷选择 | `src/reorder_engine/services/grouping.py`（`DefaultVolumeGroupingStrategy._group_key`；`_pick_entry`） |
| 合并文件嵌入归档（restoreAB 类） | `src/reorder_engine/services/restore_ab.py`（容器签名常量；`RestoreABRestorer`；`_cover_suffixes`；`_build_zip_plan`；`_apply_in_place`） |
| 签名识别、嵌入扫描、Apate、多层候选 | `src/reorder_engine/services/restoring.py`（`ArchiveSignatureInspector`；`probe_embedded_archive`；`probe_apate`；`preferred_tool_for_suffix`；`ApateRestorer`；`EmbeddedArchiveRestorer`；`SuffixVariantBuilder`；`build_post_extract_candidates`） |
| 深度解压、失败分类、缺卷、成品判定、六类方式 | `src/reorder_engine/services/beta_pipeline.py`（`_entry_candidates`；`_extract_first_success`；`_extract_volume_set_body`；`_normalize_*_volume_set`；`_restore_after_failure`；`_probe_method`；`_restore_method`；`_force_apate_attempt_if_useful`；`_continue_after_extract`；`_nested_candidates`；`_failure_category`；`_is_missing_volume`；`_result_partial`） |
| 抽取命令与快速失败 | `src/reorder_engine/infrastructure/tools.py`（`SevenZipCli`/`UnrarCli`/`BandizipCli.extract`；`contains_nonempty_regular_file`；`_extract_abort_guard`）与 `src/reorder_engine/infrastructure/command_runner.py`（`ExternalCommandRunner.run` 的 `abort_on_line`） |
| 原包归档与跨盘保留 | `src/reorder_engine/infrastructure/file_transaction.py`（`FileTransaction.route_sources` 的 `copy_across_volumes`）与 `src/reorder_engine/application/processing.py`（`PackageProcessor` 跨盘保留提示） |
| 输入过滤、直接源提示 | `src/reorder_engine/application/planning.py`（`EXCLUDED_EXTENSIONS`、`EXCLUDED_NAMES`、`PlanService.create` 的 warnings） |
| 内置词库读取与校验 | `src/reorder_engine/infrastructure/builtin_defaults.py`（清单与只读校验；`password_count`；`keyword_count`） |
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

## 9. 桌面端保留的旧引擎行为对照（0.3.2）

桌面端**包裹现有引擎**，不缩减旧管线能力；下表把用户在意的行为逐条列清楚。证据类型沿用第 3 节的 `源 / 史 / 模 / 验`，并新增 `策`：已批准设计，本轮实现方向，机器证据由主线程汇总，本文不预判通过。

| 主题 | 旧引擎（portable 基线）行为 | 桌面 0.3.2 | 证据 |
| --- | --- | --- | --- |
| 分卷分组 | 按分卷集合分组并挑入口卷（`.7z.001`、`.partNN.rar`、`.r00`、`.rar`） | 复用同一分组策略；补齐 `.zip.001.mp4` 一类逐卷 Apate 还原与完整卷组预检 | 源 + 模 + BG57 隔离实测 |
| 原始名 / 内容名 | `preserve_payload_names` 开：成品用最深单层包装目录名；关：用压缩包基础名 | 界面“保留内容原名”开关，语义相同 | 源 |
| 单包装成判定 | 目录内 ≥10 个子目录或 ≥80 个文件即判为成品，停止继续深挖 | 同上阈值，不再往下拆 | 源 |
| 嵌套候选 | 唯一非媒体文件是归档（或 ≥max(识别下限, 单文件阈值)）；出现成品媒体即停止；多个归档取体积最大的前 5 个；否则取 ≥单文件阈值的最大一个 | 同上 | 源 |
| 深挖停止 | 默认最多 4 层、识别下限 100 MB、单文件阈值 200 MB | 同上默认值；桌面**默认开启**深度解压（旧 CLI 默认关闭），用户可关 | 源 |
| 伪装与合并恢复 | 后缀候选、restoreAB 合并、Apate（默认 3 轮）、前后缀嵌入、尾部 `*sc` | 全部保留 | 源 |
| 输出目录 | `final` / `success/archives` / `error_files` / `deferred_volumes` / `intermediate` | 目录语义不变，整体落在所选**工作文件夹**下 | 源 + 策 |
| 失败归类 | 缺卷 → `deferred`；旧实现曾把非空残留快速停为 `partial`；错误分类 `password_error` / `unknown_type` / `missing_volume` / `extract_failed` | 2026-10-18 起，只有完整成功才算完成；所有适用尝试失败但有非空输出 → `partial` 并移入 `incomplete/`；无输出失败仍进 `error_files/` | 源 + 策 |
| 输入是否展平 | 旧 Beta 可配置处理前展平，BAT 预设不同 | 保持不展平；原件按文件组归档 | 源 + 策 |
| 发布 / 归档的 I/O | 在单一处理目录内用 `shutil.move` 改名/移动 | 直接读原包，必要时原地修复；成品与处理后的原包用同盘**独占链接/改名**落位（`os.link` 优先，Windows 退化为独占改名）；**跨盘原包保留当前位置并登记结果**，不做整包复制 | 源 + 定向文件事务检查（大盘性能未测） |
| 可调项范围 | 旧 CLI 有更多自定义项 | 桌面只暴露其中一部分（深挖、层数、识别下限、单文件阈值、保留原名、保留工作区等），**不宣称所有 CLI 开关都已暴露** | 源 |

说明：

- 上表按当前源码与 portable 基线对照得出，属于**静态核对**；桌面相对旧管线的增量是结果上报、源路由修正与安全预检；确认的 Apate/嵌入伪装在多卷路径中优先交给对应还原器，避免合并文件扫描器抢先误判，旧文件夹策略、命名保留与嵌套整理都保留。工作文件夹布局与独占移动已在当前工作区实现（`infrastructure/workspace.py`、`infrastructure/file_transaction.py`），有限机器检查见第 8 节，Windows 冻结引擎检查以实施状态记录为准。
- 桌面发布与原件整理优先用同盘独占链接/改名落位而不是重拷贝；**跨盘原包保留当前位置并登记结果**，不做整包复制（跨盘时成品仍在工作文件夹，原包不再搬走）。这不等于“绝对没有额外 I/O”，实际字节与 I/O 次数未在真实大盘上测量。
- 当前多卷扩展覆盖已确认的逐卷单层 Apate 伪装；更复杂的多重伪装或混合嵌入卷组仍需单独样本验收。
- 0.3.1 已验证 BG57 这对样本；其他真实文件命中率、性能数字与界面体验仍待人工验收；本文不把静态或合成结论当作业务验收。

## 10. 磁盘占用与跨盘行为（0.3.3）

开始时不复制整包，也不对同盘归档做整文件复制/哈希。普通原包归档字节不变；Apate/嵌入修复及后缀归一化可能改变归档内容/名称，这是旧版语义。桌面 restoreAB 走原地修复，**不生成恢复整包副本**；多层解压和部分失败输出仍占空间。**跨盘原包保留当前位置并登记结果，不做整包复制**；同盘归档用独占链接/改名移动。Windows 无硬链接的磁盘可独占改名；少数 POSIX 文件系统的安全落位仍可能回退复制，若原地改名身份改变则停下核对。只有本次任务独占目录可清理，已有内容和未选文件不动。

检查范围与最终 EXE 证据见实施状态；0.3.1 的 BG57 隔离副本结果是历史证据，不表示新直接模式已对真实原件验收。0.3.2 当时“跨盘归档校验复制”的说法由本节与第 11.5 节取代。

## 11. 0.3.3 磁盘读写优先、停止策略与工具升级（当前切片）

本节记录 0.3.3 已实现的改动与范围边界。0.3.3 Windows 便携预发布已发布；真实文件效果、GUI 与大盘 I/O 仍待用户人工验收。本节只给类/函数名与合成检查入口，不引用会随改动漂移的源码行号。

### 11.1 六类核心处理方式

每个文件组只归入以下**六类核心方式**之一；嵌套层数、分卷数量只是同一方式内的迭代或分流，**不**与方式数量累加。方法标记来自 `beta_pipeline.py` 的 `_probe_method`、`_restore_method` 与 `_force_apate_attempt_if_useful`：

| # | 方式（方法标记） | 触发与行为 |
| --- | --- | --- |
| 1 | 直接解压（`direct`） | 文件头即归档签名或名字可靠时，直接读取选中原包解压；普通包字节不变。 |
| 2 | 后缀改名（`direct+rename:<规则>`） | 签名或名字提示需要归一化后缀时，原地改名后再解压；失败即回滚原名。 |
| 3 | Apate 还原（`apate-reveal`） | 尾部 4 字节长度标记命中时，按 `tools/apate.py` 原地还原真实头（默认 3 轮）。 |
| 4 | 嵌入封面剥离（`embedded-archive-strip-prefix`） | 前缀是图片/视频、正文是归档时，原地剥掉前缀（ZIP 另修偏移）。 |
| 5 | restoreAB（`restore-ab-in-place`） | 合并文件检索未命中封面/Apate 时的最终兜底；桌面按原地语义修复**同一路径**，不生成恢复整包副本。 |
| 6 | 强制 Apate 兜底（`apate-force-reveal`） | 名字（one/two/three 等）或后缀提示疑似 Apate 时，强制多轮还原后再解压。 |

旧版 tag 没有 restoreAB 原地修复，核心方式为 **5 类**；0.3.3 起为 **6 类**。类型为 APATE / EMBEDDED 的候选优先交给对应原地策略，避免合并文件扫描器在缺 EOCD 的分卷组上抢先误判（`RestorationService.restore_with_rollbacks` 的 `preferred_kind`）。

### 11.2 后缀规则与分卷归一化的确切数量

- **后缀改名规则 7 条**（`SuffixVariantBuilder` 默认规则元组，本次静态枚举确认）：`trim-sc-to-zip`、`trim-embedded-archive-suffix`、`signature-rename`、`media-to-zip`、`no-suffix-to-zip`、`jpg-exe-archive-variants`、`unknown-archive-variants`。0.3.2 基线为 5 条；本轮新增 `media-to-zip` 与 `no-suffix-to-zip`。
- **分卷归一化 4 条**（`BetaFolderPipeline._extract_volume_set_body` 调用，各自失败即回滚）：`disguised-split-suffix`、`sfx-volume`、`numbered-tail-volume`、`middle-numbered-volume`。
- 静态证据位置：`services/restoring.py` 的 `SuffixVariantBuilder.__init__` 与各 `RenameVariantRule.matches/plan`；`services/beta_pipeline.py` 的 `_normalize_disguised_split_suffix_volume_set`、`_normalize_sfx_volume_set`、`_normalize_numbered_tail_volume_set`、`_normalize_middle_numbered_volume_set`。
- 这些数字只描述**代码里注册的规则条数**，不等于“所有后缀变体都能命中”；规则是试探性的，失败会回滚。

### 11.3 restoreAB 原地语义

- 桌面 `RestoreABRestorer(in_place=True)`（见 `application/processing.py` 的 `PackageProcessor` 装配）：单句柄 `r+b`，先只读规划与校验，再前移 + `truncate` + 回写 ZIP 偏移；**不创建派生副本**，`restore_with_rollbacks` 原地模式返回同一路径、回滚列表为空（不伪造可恢复已丢前缀）。方法标记 `restore-ab-in-place`。
- ZIP64 或布局异常在修改前抛 `RestoreABError`，绝不误截断；`lstat` 只处理常规文件，不跟随符号链接。
- CLI（`beta.py`）默认仍用旧派生接口 `RestoreABRestorer()`（`in_place=False`，切片到工作区新文件），桌面磁盘优先。CLI 兼容性与桌面原地语义是两条路径，不互相代表。

### 11.4 完整成功、partial 输出与停止策略（2026-10-18）

旧版 0.3.3 曾把“非零退出但已有非空文件”立即显示为“完成，有警告”并停止重试。该历史规则已被以下当前源码契约取代；已发布 ZIP 未更新：

- 只有完整成功才报告完成。错误密码、非零退出或 CRC/Data Error 即使已有非空输出，也代表该次尝试失败。
- 隔离每次尝试输出，失败内容不能污染之后的完整成功。遍历适用的所有配置密码、可用工具和恢复路径，顺序为直接解压 → 原地后缀调整 → 必要的伪装还原；完整成功后才提前结束。真实格式不匹配可换工具并跳过该工具的密码尝试。
- 缺卷、权限、磁盘空间不足、取消、超时和安全失败是硬停止条件，不盲目重试。CRC/Data Error 也不能降格成密码错误或完整成功。
- 不为每次尝试保留结果；当前服务只保留一份候选/最新非空 partial，不承诺选择“最好”的结果。不做整包备份或恢复副本，结果只在同一工作区内移动。
- 全部适用尝试失败仍有非空文件时，状态为 `partial`，界面显示“未完成，待检查”；输出放到工作根 `incomplete/outputs/<包名>/`，完整原包分卷按原 basename 放到 `incomplete/archives/` 根目录，同名冲突沿用去重处理，不增加包级 archives 目录。不进入 `final/` 或 `success/archives/`。跨盘原件留在原位置并登记。
- 没有非空文件的失败仍进入 `error_files/`；缺卷留在 `deferred_volumes/`。内层 partial 仍是未完成，不能让外层状态变成成功。未知普通文件沿用旧停止策略。
- 真实用户 `393.7z.001/.002` 尚未执行人工验收；不要把历史合成检查写成这项验收通过。

### 11.5 原包归档：同盘移动、跨盘保留

- 同盘：`FileTransaction.route_sources` 走原地独占落位（`os.link` 优先，Windows 退化为独占改名）后删源。
- 跨盘：`PackageProcessor` 传 `copy_across_volumes=False`，`route_sources` 不改动字节、**保留原包当前位置**并把已核验的当前路径登记为结果，日志追加“已保留原包当前位置，未复制归档”。因此跨盘不再需要为“整包归档”额外复制一份原包。
- 该行为取代第 9 节历史行与 0.3.2 的“跨盘归档必须复制校验”表述；桌面不再要求跨盘整包复制，也不要求 restoreAB 派生副本。

### 11.6 工具版本与升级

- 7-Zip 由 25.01 **受控固定升级到 26.04**（`scripts/desktop-sevenzip.lock.json` 固定版本与哈希，25.01 历史保留）；UnRAR 7.13.0、Bandizip 7.40.0.1 保持固定。bsdtar 3.8.8 经实测对核心格式无实质补充，**本版不捆绑**。
- ALZ/EGG **不宣称支持**：尚无安全预检的真实 fixture，预检门槛仍在 7-Zip；不启用 `bz t`（存在挂起风险）。详见 [工具研究](desktop-tool-research.md)。

### 11.7 0.3.3 未验证项

- 7-Zip 26.04 冻结引擎在真实文件上的行为；Windows 上 7-Zip/UnRAR/Bandizip 的退出码、`-kb`、`-sccUTF-8` 仅经合成 fake 工具与真实 Python 子进程验证快速结束，未对随包二进制逐条复现。
- 跨盘保留、restoreAB 原地修复在真实大盘与真实原件上的效果；本次只做合成样本与定向源路径/动作检查。
- 六类方式的真实命中率、`incomplete/` 结果在真实损坏包上的表现、以及 GUI 文案，均属人工验收。

## 12. 旧 BAT / Beta 入口与桌面策略的关系（2026-10-14）

根目录 `extract_here.bat`、`beta_here.bat`、`run_in_this_folder.bat`、basic/smart 和工具准备脚本继续可用。异机拉取、一次性 Python 环境和仓库外入口部署见 [README](../README.md#在另一台-windows-机器运行旧-bat)。完整仓库放到含空格、中文的新路径时使用本份源码；只复制一个 BAT 则需指定程序仓库或部署绑定入口。

- **处理优先级共享**：直接读原包 → 原地后缀尝试 → 识别到的必要伪装还原；六类核心方式、七条后缀规则、四类分卷归一化沿用第 11 节。工具默认顺序为 7-Zip / UnRAR / Bandizip，格式偏好和 CLI 配置可调整工具先后。识别为 ZIP/RAR/7z 不等于执行 restoreAB。
- **原地接口共享**：Beta 也使用原地 restoreAB；普通包不改字节，必要的还原可能不可逆，不产生整包恢复副本。
- **停止策略共享**：只有完整成功才完成；错误密码、非零退出与 CRC/Data Error 即使已有非空输出也不能算成功。遍历适用密码、工具和恢复路径后仍有非空文件时，设为“未完成，待检查”并保留在 `incomplete/`；无文件失败进 `error_files/`。格式不匹配允许更换工具并跳过该工具密码；缺卷、权限、空间不足、取消、超时和安全失败硬停止。内层 partial 不能让外层成功。未知普通文件沿用旧停止策略。
- **原名与目录规则保留**：内容原名、单层包装压缩、多文件夹成品停止、嵌套阈值与分卷分流仍由共享管线完成。CLI 输入预展平由配置决定；使用 `--no-flatten` 可保留输入子目录，桌面继续不预展平。
- **预设真正生效**：basic 每层选择最大候选，最多一个；smart 按规则顺序选择，受 `deep-max-candidates` 约束。basic 为 2000 MB / 深度 4；smart 默认 128 / 128 / 512 MB、深度 6、候选 3。大小阈值并非所有已识别压缩包的硬跳过线。
- **工作路径**：BAT 输入目录就是 CLI 工作文件夹，复用 `final/`、`success/archives/`、`error_files/`、`deferred_volumes/`、`intermediate/`；本次占用的工作子目录冲突时隔离到新目录，已有文件不覆盖。工具临时目录在 `intermediate/tool_tmp/`。程序依赖仍在仓库 `.venv` 与 `runtime/cli-tools/`，不会把用户原包复制到那里。
- **公开词库**：CLI 默认读取仓库 `resources/passwords.txt` / `keywords.txt` 或个人配置的路径，可直接维护，清空不重新播种。密码日志是原文。CLI txt 解析仍沿用空行和 `#` 注释规则；桌面每行按字面保存，两者差异在 README 说明。
- **动态 DLL 清理**：默认关闭，仅明确开启时在发布前移除本次完整解出的匹配普通 DLL；已有文件、外部链接和 partial 内容保留。旧参数曾只接受而无实际效果，本轮接入不作为历史已经实测的证据。

BAT/Beta 保留旧直接工具入口，不使用 Tauri 的任务数据库，也没有桌面 `GuardedExtractor` 的统一安全预检与输出配额封装。因此上文桌面的预检限制和 EXE 检查记录不直接等于 BAT 的验证结果。本轮仅做独立的合成包与路径、配置、退出码检查，范围与证据见 [实施状态](desktop-status.md)。真实损坏文件、RAR、专有格式及大盘性能仍需你验收。
