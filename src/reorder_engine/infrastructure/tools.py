from __future__ import annotations

import shutil
from pathlib import Path

from reorder_engine.infrastructure.command_runner import ExternalCommandRunner


def _resolve_tool(exe: str | None, candidates: list[str]) -> str | None:
    if exe:
        return exe
    # 优先项目内 tools/
    tools_dir = Path(__file__).resolve().parents[3] / "tools"
    for name in candidates:
        p = tools_dir / name
        if p.exists():
            return str(p)
    # 再从 PATH 查找
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    return None


class SevenZipCli:
    def __init__(self, runner: ExternalCommandRunner, exe: str | None = None):
        self._runner = runner
        self._exe = _resolve_tool(exe, ["7z.exe", "7za.exe", "7z", "7za"])

    def is_available(self) -> bool:
        return bool(self._exe)

    def extract(self, archive: Path, output_dir: Path, *, password: str | None = None):
        # 7z x -y -o<dir> [-pPASSWORD] <archive>
        args = [self._exe, "x", "-y", f"-o{str(output_dir)}"]
        if password:
            args.append(f"-p{password}")
        args.append(str(archive))
        return self._runner.run(args)


class BandizipCli:
    def __init__(self, runner: ExternalCommandRunner, exe: str | None = None):
        self._runner = runner
        # Bandizip 命令行在不同版本可能不同；这里先用常见的 bandizip.exe 作为探测点
        self._exe = _resolve_tool(exe, ["bandizip.exe", "bandizip", "bz.exe", "bz"])

    def is_available(self) -> bool:
        return bool(self._exe)

    def extract(self, archive: Path, output_dir: Path, *, password: str | None = None):
        # bz 用法：bz x [switches] <archive> ...
        # 常用开关：-y (assume yes), -o:{dir} (输出目录), -p:{password}
        args = [self._exe, "x", "-y", f"-o:{str(output_dir)}"]
        if password:
            args.append(f"-p:{password}")
        args.append(str(archive))
        return self._runner.run(args)


class UnrarCli:
    """WinRAR/UnRAR 系命令行封装。

    说明：WinRAR 是闭源商业软件；但通常可在本机安装后使用其 CLI。
    这里优先查找 unrar.exe/rar.exe/winrar.exe。
    """

    def __init__(self, runner: ExternalCommandRunner, exe: str | None = None):
        self._runner = runner
        # 仅使用纯 CLI：rar.exe/unrar.exe。WinRAR.exe 是 GUI 程序，可能弹窗并导致脚本“卡住”。
        self._exe = _resolve_tool(exe, ["rar.exe", "unrar.exe", "rar", "unrar"])

    def is_available(self) -> bool:
        return bool(self._exe)

    def extract(self, archive: Path, output_dir: Path, *, password: str | None = None):
        out_dir = str(output_dir) + "\\"

        # rar/unrar: x -y -p<password>|-p- <archive> <outdir>\\
        args = [self._exe, "x", "-y"]
        if password is None:
            args.append("-p-")
        else:
            args.append(f"-p{password}")
        args.append(str(archive))
        args.append(out_dir)
        return self._runner.run(args)
