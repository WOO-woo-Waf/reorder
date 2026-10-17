from __future__ import annotations

import shutil
import os
import stat
from pathlib import Path
from typing import Callable

from reorder_engine.infrastructure.command_runner import ExternalCommandRunner


# Lines that mean the running extraction command cannot make progress: a strong
# password rejection or a hard format/tool mismatch. Only these quicken the
# process tree; soft warnings and per-member CRC lines are not included.
_EXTRACT_ABORT_MARKERS = (
    "wrong password",
    "password is incorrect",
    "incorrect password",
    "invalid password",
    "illegal password",
    "not a rar archive",
    "not rar archive",
    "is not rar",
    "not a rar",
    "can not open the file as archive",
    "cannot open the file as archive",
    "is not archive",
    "not archive",
    "unsupported archive type",
    "不是 rar",
    "不是rar",
    "不是压缩文件",
    "不是有效的压缩文件",
    "无法作为压缩文件打开",
    "密码错误",
    "密码不正确",
    "口令错误",
    "非法密码",
)
_PASSWORD_ABORT_MARKERS = (
    "wrong password",
    "password is incorrect",
    "incorrect password",
    "invalid password",
    "illegal password",
    "密码错误",
    "密码不正确",
    "口令错误",
    "非法密码",
)


def contains_nonempty_regular_file(root: Path) -> bool:
    """Return True when ``root`` holds at least one non-empty regular file.

    Symlinks and special files are never followed or counted here; the workspace
    guard remains responsible for rejecting them. A missing/unreadable tree
    yields False rather than raising.
    """

    directory = Path(root)
    if not directory.is_dir():
        return False
    for current, _dirs, files in os.walk(directory, followlinks=False):
        base = Path(current)
        for name in files:
            child = base / name
            try:
                metadata = child.lstat()
            except OSError:
                continue
            mode = metadata.st_mode
            if stat.S_ISLNK(mode):
                continue
            if getattr(metadata, "st_reparse_tag", 0) in (0xA0000003, 0xA000000C):
                continue
            if stat.S_ISREG(mode) and metadata.st_size > 0:
                return True
    return False


def _extract_abort_guard(output_dir: Path) -> Callable[[str], bool]:
    """Abort a single extraction run on a strong failure line.

    Only fires while this run's output directory still has no non-empty regular
    file, so an already-recovered result is never discarded mid-run.
    """

    def _abort(line: str) -> bool:
        lowered = line.lower()
        if not any(marker in lowered for marker in _EXTRACT_ABORT_MARKERS):
            return False
        # A clear password rejection is final for this process even if it has
        # already emitted damaged bytes. Those bytes stay isolated to this try.
        if any(marker in lowered for marker in _PASSWORD_ABORT_MARKERS):
            return True
        return not contains_nonempty_regular_file(output_dir)

    return _abort


def _resolve_tool(exe: str | None, candidates: list[str]) -> str | None:
    if exe:
        return exe
    # 优先项目内 tools/
    tools_dir = Path(__file__).resolve().parents[3] / "tools"
    for name in candidates:
        p = tools_dir / name
        if p.exists():
            return str(p)
        matches = sorted(tools_dir.rglob(name), key=lambda x: (len(str(x)), str(x).lower())) if tools_dir.exists() else []
        if matches:
            return str(matches[0])
    # 再从 PATH 查找
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    return None


class SevenZipCli:
    def __init__(self, runner: ExternalCommandRunner, exe: str | None = None):
        self._runner = runner
        self._exe = _resolve_tool(exe, ["7z.exe", "7za.exe", "7zz", "7z", "7za"])

    def is_available(self) -> bool:
        return bool(self._exe)

    def extract(self, archive: Path, output_dir: Path, *, password: str | None = None):
        # 7z x -y -sccUTF-8 -o<dir> -p[PASSWORD] <archive>. The console charset is
        # pinned to UTF-8 to match the production runner. With no password to try we
        # pass the defined literal ``-p-``: 7-Zip reads it as the password "-" (unlike
        # UnRAR's ``-p-`` = no-password), so the switch always carries a value and 7-Zip
        # does not prompt for input. Omitting -p entirely can prompt and return 255
        # on stdin EOF, blocking later password attempts. Bare -p is also noninteractive.
        # Unencrypted archives
        # ignore the switch; an encrypted archive yields a clear "Wrong password" and
        # the caller keeps polling the real password list.
        args = [self._exe, "x", "-y", "-sccUTF-8", f"-o{str(output_dir)}"]
        args.append(f"-p{password}" if password else "-p-")
        args.append(str(archive))
        return self._runner.run(args, abort_on_line=_extract_abort_guard(output_dir))


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
        return self._runner.run(args, abort_on_line=_extract_abort_guard(output_dir))


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
        out_dir = str(output_dir) + os.sep

        # rar/unrar: x -y -kb -p<password>|-p- <archive> <outdir>\\
        # ``-kb`` keeps broken/partial files when a tail error would otherwise
        # delete already-recovered output.
        args = [self._exe, "x", "-y", "-kb"]
        if password is None:
            args.append("-p-")
        else:
            args.append(f"-p{password}")
        args.append(str(archive))
        args.append(out_dir)
        return self._runner.run(args, abort_on_line=_extract_abort_guard(output_dir))
