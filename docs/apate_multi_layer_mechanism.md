# APATE 多层伪装、还原与判定机制说明

本文解释 APATE 文件伪装的核心机制、官方还原流程、reorder-engine 当前的自动判定逻辑，以及 `1190-Three.mp4` 这类“既能播放又能还原成压缩包”的特殊文件为什么成立。

相关官方资料：

- APATE 官方仓库：https://github.com/rippod/apate
- 官方还原实现 `Program.Reveal`：https://github.com/rippod/apate/blob/main/apate/Program.cs
- 本项目 Python 复刻实现：[tools/apate.py](../tools/apate.py)
- 本项目 APATE 说明：[tools/APATE.md](../tools/APATE.md)

## 1. APATE 解决的是什么问题

很多资源为了绕过平台的文件类型检测，会把真实文件伪装成图片、视频或其他普通媒体文件。常见外观包括：

- `.jpg`
- `.png`
- `.webp`
- `.mp4`
- `.exe`

但它们内部可能实际是：

- ZIP 压缩包
- RAR 压缩包
- 7z 压缩包
- SFX 自解压 EXE
- 另一层 APATE 伪装文件

APATE 的重点不是加密，而是“格式伪装”。它改变文件头部，让文件表面看起来像某种媒体，同时在文件尾部保存恢复真实头部所需的信息。

## 2. APATE 和简单 copy /b 拼接的区别

有人会用这种方式把文件拼在一起：

```bat
copy /b cover.jpg+archive.zip disguised.jpg
```

这种方式的本质是：

1. 前面完整放一个图片。
2. 后面直接接一个压缩包。
3. 某些图片查看器会忽略尾部多余数据，所以它看起来仍然是图片。
4. 某些压缩工具能扫描尾部 ZIP 结构，所以也可能打开它。

APATE 不是这种简单拼接。APATE 更像“换头 + 尾部备份”：

1. 取真实文件开头的一段字节，叫做 `original_head`。
2. 用媒体文件开头的一段字节覆盖真实文件开头，这段叫做 `mask_head`。
3. 把 `original_head` 反转后写到文件尾部。
4. 最后 4 字节写入 `mask_head` 的长度，小端 uint32。

因此 APATE 文件尾部通常长这样：

```text
[...主体内容...][反转后的真实头 original_head[::-1]][4 字节 mask_head_length]
```

还原时不需要扫描整个文件，只需要看最后 4 字节。

## 3. 官方 APATE 的还原机制

官方 `Program.Reveal` 的核心流程可以理解成：

1. 读取文件最后 4 字节。
2. 把这 4 字节按小端整数解释为 `mask_head_length`。
3. 计算备份头位置：

```text
backup_pos = file_size - 4 - mask_head_length
```

4. 从 `backup_pos` 读取 `mask_head_length` 字节。
5. 把这段字节反转，得到真实原始头。
6. 把文件截断到 `backup_pos`，去掉尾部备份区和长度标记。
7. 把真实原始头写回文件开头。

伪代码：

```python
mask_head_length = read_uint32_le(file[-4:])
backup_pos = file_size - 4 - mask_head_length
original_head_reversed = read(file, backup_pos, mask_head_length)
original_head = reverse(original_head_reversed)
truncate(file, backup_pos)
write(file, offset=0, data=original_head)
```

注意：官方 APATE 一次 `Reveal` 只处理一层。它不会自动解压，也不会判断里面是不是 ZIP/RAR/7z。

## 4. 文件写入到底写了什么

以一个真实压缩包为例，假设原始 ZIP 文件开头是：

```text
50 4B 03 04 ...    # PK..，ZIP 文件头
```

APATE 用 MP4 做面具，MP4 头可能是：

```text
00 00 00 20 66 74 79 70 69 73 6F 6D ...
```

也就是：

```text
....ftypisom...
```

伪装前：

```text
[PK ZIP 真实头][ZIP 主体...]
```

伪装后：

```text
[MP4 面具头][ZIP 主体剩余部分...][反转后的 PK ZIP 真实头][面具头长度]
```

还原后：

```text
[PK ZIP 真实头][ZIP 主体...]
```

这里的“写入”主要有三类：

- 伪装时写入：把媒体面具头写到文件开头。
- 伪装时追加：把真实头反转后追加到文件尾部。
- 伪装时追加：把面具头长度作为最后 4 字节追加。

还原时也有三类写入：

- 读取尾部长度。
- 截断文件，去掉尾部备份。
- 把真实头写回文件开头。

## 5. 为什么 `1190-Three.mp4` 可以播放

这个文件的开头是标准 MP4：

```text
00 00 00 20 66 74 79 70 69 73 6F 6D
```

对应文本：

```text
....ftypisom
```

播放器看到这个头，会把它当成 MP4 容器。MP4 容器本身也常常能容忍文件尾部有额外数据，所以 APATE 写在尾部的备份区不一定影响播放。

这个样本外层是一个足够真实的 5 秒 MP4 面具。因此：

- 对播放器来说，它是一个能播放的短视频。
- 对 APATE 来说，它尾部仍然有可用于还原的结构。
- 对我们的解压器来说，第一眼看它不是压缩包，因为它确实是 MP4 头。

这就是它“既像视频，又能还原成压缩包”的原因。

## 6. 为什么它要处理三次

文件名里的 `Three` 是业务信号，表示它被套了三层 APATE。

可以把它想成这样：

```text
第 0 层：真实载荷，SFX/EXE 归档
第 1 层：用一个面具盖住真实载荷
第 2 层：再用一个面具盖住第 1 层
第 3 层：再用一个 MP4 面具盖住第 2 层
```

所以看到的最终文件是：

```text
1190-Three.mp4
```

连续还原结果：

```text
原始文件       -> MP4 头，可播放
Reveal 第 1 次 -> 仍然像 MP4 或中间层
Reveal 第 2 次 -> 仍然是中间层
Reveal 第 3 次 -> 露出 MZ 头
```

`MZ` 是 Windows 可执行文件头：

```text
4D 5A 90 00 ...
```

很多压缩包会做成 SFX 自解压 EXE。7-Zip 可以识别这类 EXE 内部的归档数据，所以第三次还原后，7-Zip 会进入解压流程，甚至可能开始询问密码。

## 7. 这个文件的实测特征

对 `1190-Three.mp4` 的实测观察：

```text
原始大小：56,628,777 字节
第 1 层 mask_head_length：689,483 字节
第 1 次 Reveal 后大小：55,939,290 字节
第 2 次 Reveal 后大小：55,249,803 字节
第 3 次 Reveal 后大小：54,560,316 字节
第 3 次 Reveal 后头部：MZ
```

每次少掉：

```text
689,483 + 4 = 689,487 字节
```

其中：

- `689,483` 是备份的面具头长度。
- `4` 是文件尾部保存长度用的 uint32。

这说明它不是随机损坏文件，而是非常规整的三层 APATE 结构。

## 8. 本项目当前的识别机制

本项目的识别不是单一规则，而是多个信号共同决定。你可以把它理解成“判定机制”或“投票机制”，但代码里不是数学投票，而是优先级判断。

主要信号包括：

- 文件名信号
- 扩展名信号
- 文件头签名
- APATE 尾部结构
- Reveal 后的真实头
- 解压工具反馈
- 文件是否属于多卷包

### 8.1 文件名信号

例如：

```text
1190-Three.mp4
FolderTwo.jpg
payloadDouble.png
sampleTriple.mp4
```

现在我们把这些名字里的层数词当成高优先级信号：

```text
One / Single   -> 还原 1 次
Two / Double   -> 还原 2 次
Three / Triple -> 还原 3 次
```

这里名字优先级高于探测结果。也就是说：

```text
Three.mp4
```

就明确还原 3 次，即使第 1 次还原后看起来仍然是 MP4，也不能提前停止。

### 8.2 文件头签名

文件头签名用于判断文件实际类型。

常见签名：

```text
ZIP:  50 4B 03 04
RAR:  52 61 72 21 1A 07
7z:   37 7A BC AF 27 1C
EXE:  4D 5A
MP4:  offset 4..7 是 66 74 79 70，也就是 ftyp
PNG:  89 50 4E 47 0D 0A 1A 0A
JPG:  FF D8 FF
```

如果文件开头直接是 ZIP/RAR/7z，本项目会优先当作压缩包处理。

如果文件开头是 MP4，但尾部存在 APATE 结构，就可能需要先还原。

### 8.3 APATE 尾部结构信号

APATE 探测会读取最后 4 字节，得到 `mask_head_length`。

如果这个长度合理，就继续计算：

```text
backup_pos = file_size - 4 - mask_head_length
```

然后读取备份头，反转，看看恢复出来的头是什么。

如果恢复出来的头是：

```text
PK / Rar! / 7z
```

就很明确：这是 APATE 伪装的压缩包。

如果恢复出来还是：

```text
ftypisom
```

那可能是：

1. 普通媒体文件尾部刚好像 APATE。
2. 多层 APATE 的中间层。
3. 文件确实是 APATE，但真实载荷不是压缩包。

因此没有名字标记时，不能无脑递归。

### 8.4 工具反馈信号

解压工具的返回信息也会参与分类：

- `Wrong password`
- `Can not open the file as archive`
- `Missing volume`
- `Unsupported archive type`

这些信息会把失败归类到：

- `password_error`
- `unknown_type`
- `missing_volume`
- `extract_failed`

但工具反馈通常是后置证据。前面 APATE 还原层数错了，工具只会告诉你“它不像压缩包”或“需要密码”。

## 9. 本项目和官方 APATE 的区别

### 9.1 官方 APATE

官方 APATE 只负责两件事：

- Disguise：伪装。
- Reveal：还原一层。

官方不负责：

- 自动识别 ZIP/RAR/7z。
- 自动解压。
- 自动尝试密码。
- 自动处理多层。
- 根据文件名 `Three` 决定跑三次。
- 把失败分类到 `unknown_type` 或 `password_error`。

### 9.2 本项目

本项目把 APATE 放进了自动解包流水线，所以多做了很多安全判断：

1. 判断文件是不是已经是压缩包。
2. 判断文件是不是 APATE。
3. 判断 APATE 还原后是不是压缩包。
4. 对可疑媒体文件做 force APATE。
5. 根据文件名层数标记重复还原。
6. 还原后交给 7-Zip、UnRAR、Bandizip 等工具。
7. 尝试密码列表。
8. 失败后分类移动到错误目录。
9. 如果 APATE 还原尝试失败，会回滚原文件。

因此本项目比官方 APATE 更像一个自动处理管线，而官方 APATE 是单一的伪装/还原工具。

## 10. 旧逻辑为什么处理不了这个样本

旧逻辑的问题是：

1. `1190-Three.mp4` 开头是标准 MP4。
2. 普通 APATE 探测 reveal 后看到的头仍然是 MP4。
3. 因为它没有马上露出 ZIP/RAR/7z，所以没有被识别为明确 APATE 压缩包。
4. force APATE 分支最多只做 1 次。
5. 第 1 次后仍然不是压缩包，于是交给解压器失败。

实际需要的是：

```text
根据 Three 明确执行 3 次 Reveal
```

第 3 次后才会出现：

```text
MZ
```

然后 7-Zip 才能把它当 SFX 归档处理。

## 11. 新逻辑应该怎么工作

推荐优先级：

```text
文件名明确层数 > 明确压缩包签名 > APATE 探测 > 扩展名猜测 > 工具反馈
```

具体规则：

1. 如果文件名包含 `Three` / `Triple`，强制 APATE Reveal 3 次。
2. 如果文件名包含 `Two` / `Double`，强制 APATE Reveal 2 次。
3. 如果文件名包含 `One` / `Single`，强制 APATE Reveal 1 次。
4. 没有层数标记时，不盲目多次还原。
5. 没有层数标记时，只在 APATE 结构明确、还原结果可信时进入下一步。
6. 还原后如果是 ZIP/RAR/7z/MZ，再交给解压器。
7. 每次 force APATE 都记录 rollback 信息，失败时恢复原文件。

这样可以避免把普通 MP4 错误破坏，同时支持 `1190-Three.mp4` 这种多层样本。

## 12. 举例：单层 APATE

原始文件：

```text
payload.zip
头部：PK 03 04
```

伪装后：

```text
payload.mp4
头部：ftypisom
尾部：反转后的 PK 头 + 长度
```

还原一次：

```text
payload.mp4
头部：PK 03 04
```

这时 7-Zip 可以直接解压。

## 13. 举例：三层 APATE

真实载荷：

```text
archive_sfx.exe
头部：MZ
```

第 1 次伪装：

```text
inner.mp4
头部：MP4
尾部：MZ 头的反转备份 + 长度
```

第 2 次伪装：

```text
middle.mp4
头部：MP4
尾部：inner.mp4 头的反转备份 + 长度
```

第 3 次伪装：

```text
1190-Three.mp4
头部：MP4
尾部：middle.mp4 头的反转备份 + 长度
```

还原流程：

```text
1190-Three.mp4
  Reveal 1 -> middle.mp4
  Reveal 2 -> inner.mp4
  Reveal 3 -> archive_sfx.exe
```

虽然文件扩展名可能仍然叫 `.mp4`，但头部已经是 `MZ`，解压器会按内容识别。

## 14. 为什么不对所有 MP4 都递归还原

普通 MP4 文件尾部可能存在各种元数据、填充、剪辑残留、下载器附加信息。最后 4 字节刚好能解释成一个“看似合理”的整数，并不代表它一定是 APATE。

如果对所有 MP4 都递归还原，风险是：

- 破坏正常视频。
- 把本来已经是最终媒体的文件误移动到错误目录。
- 产生错误的解压尝试。
- 增加大量无效处理时间。

所以本项目采用保守策略：

```text
有明确层数名字：按名字执行。
无明确层数名字：只在探测可信时执行。
```

## 15. 建议后的判断模型

可以把自动处理理解为下面这个流程：

```text
输入文件
  |
  |-- 文件名有 Three/Triple? -> force APATE 3 次
  |
  |-- 文件名有 Two/Double? -> force APATE 2 次
  |
  |-- 文件名有 One/Single? -> force APATE 1 次
  |
  |-- 文件头已经是 ZIP/RAR/7z/MZ? -> 直接解压
  |
  |-- APATE 探测 reveal 后是 ZIP/RAR/7z/MZ? -> APATE 还原后解压
  |
  |-- 扩展名像媒体但无法识别? -> 有限 force APATE 尝试
  |
  |-- 解压器反馈密码错误? -> password_error
  |
  |-- 解压器反馈不是归档? -> unknown_type
```

## 16. 对 `1190-Three.mp4` 的最终解释

这个文件是一个三层 APATE 伪装样本，最外层使用可播放 MP4 作为面具。

它的特殊性在于：

1. 最外层头部是真 MP4，所以播放器能播放。
2. MP4 容器能容忍尾部附加数据。
3. 尾部有 APATE 的恢复信息。
4. 文件名 `Three` 明确指示要还原 3 次。
5. 前两次还原不会露出压缩包，所以不能按“一次 reveal 后不是压缩包”判失败。
6. 第三次还原后露出 `MZ`，说明真实载荷是 SFX/EXE 类型归档。
7. 后续应交给 7-Zip 等工具，并进入密码尝试流程。

一句话总结：

```text
它是一个可以播放的 MP4 面具，里面按 APATE 规则套了三层，第三层下面才是真正的 SFX 压缩包。
```

