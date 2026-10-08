# Windows 桌面固定工具输入

`unrar-7.13-windows-x64.zip` 是构建输入，随 Git 保存。安装包和便携包中的真实工具放在 `engine/tools/unrar/`，用户无需另装 WinRAR。

来源为 Windows 宿主 `C:\Program Files\WinRAR\UnRAR.exe`：7.13.0、AMD64、win.rar GmbH 有效 Authenticode 签名。EXE 字节没有修改；不包含商业 `Rar.exe`、GUI、注册信息或用户配置。ZIP 另外保留安装目录公开 `License.txt`（命名为 `WinRAR-License.txt`）及 RARLAB 官方独立 UnRAR 包的公开免费工具许可（`UnRAR-License.txt`）。后者明确允许把 UnRAR 随其他软件包分发，前者保留 UnRAR 组件的单独分发例外。不得用于重造专有 RAR 压缩算法。

- 上游入口：https://www.rarlab.com/rar_add.htm
- 免费工具许可来源：https://www.rarlab.com/rar/unrarw64.exe
- ZIP SHA-256：`8d609ae919a1cf1d29ae3d5072f58b030d69a234f9f56de8730924c93a014a1d`
- EXE SHA-256：`8bc2ed3a734be9e90c63f3acf6adcf6e1f1e64ddbc6643ce2190d0401d98efb5`

锁定信息与逐文件哈希见 [desktop-tools.lock.json](../../scripts/desktop-tools.lock.json)。该条目的 URL 用于来源追溯；构建从本目录的已锁 ZIP 取输入，不从最新下载 URL 猜版本。当前上游独立 addon 为 beta，未用它的 EXE 替换这个稳定输入。

Bandizip 不在该目录。官方 EULA 2.3/2.4 要求书面分发许可，当前没有这项授权；本机独立安装的 `bz.exe` 仍可在设置里选择使用。详见 [第三方说明](../../docs/desktop-third-party.md)。
