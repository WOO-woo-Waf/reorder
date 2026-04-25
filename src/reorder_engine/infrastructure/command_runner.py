from __future__ import annotations

import locale
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    aborted: bool = False


class ExternalCommandRunner:
    def __init__(
        self,
        *,
        stream: bool = False,
        encoding: str | None = None,
        line_sink: Callable[[str], None] | None = None,
        abort_on_line: Callable[[str], bool] | None = None,
    ):
        self._stream = stream
        # Windows 上外部工具常用本地 ANSI 代码页输出（例如 cp936）。
        # 注意：当启用 PYTHONUTF8=1 时，getpreferredencoding(False) 往往会变成 utf-8，
        # 会导致外部工具的中文输出在日志里出现乱码；这里优先使用 locale.getencoding()。
        self._encoding = encoding or self._default_external_tool_encoding()
        self._line_sink = line_sink
        self._abort_on_line = abort_on_line

    def _default_external_tool_encoding(self) -> str:
        if sys.platform.startswith("win"):
            # Python 3.11+ 提供 locale.getencoding()，返回系统 ANSI 代码页（不受 UTF-8 mode 影响）
            getenc = getattr(locale, "getencoding", None)
            if callable(getenc):
                enc = getenc()
                if enc:
                    return str(enc)
            # 兜底：mbcs = Windows ANSI code page
            return "mbcs"

        return locale.getpreferredencoding(False) or "utf-8"

    def run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        timeout_sec: int | None = None,
        stream: bool | None = None,
    ) -> CommandResult:
        use_stream = self._stream if stream is None else bool(stream)
        if not use_stream:
            proc = subprocess.run(
                args,
                cwd=str(cwd) if cwd else None,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding=self._encoding,
                errors="replace",
                timeout=timeout_sec,
                shell=False,
            )
            ok = proc.returncode == 0
            if self._line_sink:
                if proc.stdout:
                    for line in proc.stdout.splitlines():
                        self._line_sink(line)
                if proc.stderr:
                    for line in proc.stderr.splitlines():
                        self._line_sink(line)
            return CommandResult(ok=ok, exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

        # 实时模式：把工具输出直接打印到当前终端，并同时收集一份文本用于上层记录。
        proc = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding=self._encoding,
            errors="replace",
            shell=False,
        )

        collected: list[str] = []
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                stripped = line.rstrip("\n")
                if self._line_sink:
                    # line 自带 \n
                    self._line_sink(stripped)
                else:
                    print(line, end="")
                collected.append(line)
                if self._abort_on_line and self._abort_on_line(stripped):
                    proc.kill()
                    proc.stdout.close()
                    proc.wait()
                    out = "".join(collected)
                    return CommandResult(ok=False, exit_code=130, stdout=out, stderr="aborted by output guard", aborted=True)
            proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            out = "".join(collected)
            return CommandResult(ok=False, exit_code=124, stdout=out, stderr="timeout")

        out = "".join(collected)
        ok = proc.returncode == 0
        return CommandResult(ok=ok, exit_code=int(proc.returncode or 0), stdout=out, stderr="")
