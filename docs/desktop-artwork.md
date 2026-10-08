# 星绫解封 · 视觉素材与背景设置

## 默认插画

默认背景位于 [hoshiribbon-default.png](../apps/desktop/public/backgrounds/hoshiribbon-default.png)，随前端构建和 Windows 发行包提供。它是 2026-10-08 使用 Codex 内置 imagegen 生成的原创成年二次元角色插画；没有引用外部角色图片，也没有下载 Pixabay 素材。原生成文件保留，项目文件未裁切或改色。

- SHA-256：`2798dd29e96e5bffe6cb91bfa6326dd8ea86061418ca0614c257896888bd69be`。
- 模式：内置工具，生成新图，非透明背景。
- 画面：银紫长发、丝带、星夜、温和紫粉；人物位于右侧，左侧留出界面空间。
- 应用图标是仓库内可编辑的 [SVG](../apps/desktop/src-tauri/icons/icon.svg)，另生成 PNG 与 ICO 供 Tauri 使用。

生成提示词（原文）：

```text
Use case: stylized-concept
Asset type: original wallpaper illustration for an anime community desktop archive organizer named Hoshiribbon, actual bitmap artwork not a UI mockup.
Primary request: a beautiful original adult anime woman in her twenties, long silvery lavender hair with small ribbon ornaments, fully clothed in a graceful modest lavender and cream dress, gentle confident smile, soft starry night and drifting ribbon shapes.
Composition: wide horizontal 16:9 illustration, character on the right third, calm spacious softly detailed left side for readable desktop interface overlays, no panels or controls.
Style: polished Japanese anime illustration, delicate clean linework, refined shading, charming eyes, subtle luminous stars.
Color palette: muted lavender, dusty rose, pale cream and midnight violet, soft ambient glow.
Constraints: original character; clearly adult; fully clothed; no text, no logo, no watermark, no signature, no interface.
```

本页记录生成来源，不声称外部作者授予了素材许可，也不替代项目根许可证决定。

## 自定义背景

设置中的外观区域可以选择本地 PNG、JPEG 或 WebP，文件不超过 2 MiB，边长不超过 8192、总像素不超过 16 Mi，先检查容器头尺寸再解码。读取后检查类型、图像解码和尺寸，再保存到当前 WebView 的本地外观设置。背景不发送给引擎、不写入任务数据库，也不需要新增宿主文件权限。

支持调节遮罩和模糊、关闭背景和恢复默认。浏览器存储满额或被禁用时显示错误并保留原外观；重启持久化需人工核对。个人图片不进入 Git 或公共发行包。更换背景不会改动输入文件、密码或处理设置。

## 人工核对

1. 启动后确认默认少女背景、任务表文字和状态可读。
2. 选择符合限制的图片，调节遮罩、模糊，重启确认外观保存。
3. 关闭背景、恢复默认；选择超限或伪装类型的文件时应提示错误并保留原外观。
4. 在最小窗口和 Windows 缩放下确认设置仍能滚动，所有按钮可见。
