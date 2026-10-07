from __future__ import annotations

import codecs
import locale
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from reorder_engine.application.errors import ProcessingCancelled, EngineError
from reorder_engine.infrastructure.process_control import spawn_options, terminate_process_tree, ProcessScope


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    aborted: bool = False


class ExternalCommandRunner:
    """Drain output independently of the deadline; retain only a bounded tail."""

    def __init__(self, *, stream: bool = False, encoding: str | None = None,
                 line_sink: Callable[[str], None] | None = None,
                 abort_on_line: Callable[[str], bool] | None = None,
                 cancel_event: threading.Event | None = None,
                 guard: Callable[[], None] | None = None,
                 timeout_sec: int | None = None, max_output_chars: int = 256 * 1024):
        self._stream = stream
        self._encoding = encoding or self._default_external_tool_encoding()
        self._line_sink = line_sink
        self._abort_on_line = abort_on_line
        self._cancel = cancel_event
        self._guard = guard
        self._timeout = timeout_sec
        self._max_output = max_output_chars

    def _default_external_tool_encoding(self) -> str:
        if sys.platform.startswith("win"):
            getenc = getattr(locale, "getencoding", None)
            return getenc() if callable(getenc) else "mbcs"
        return locale.getpreferredencoding(False) or "utf-8"

    def run(self, args: list[str], *, cwd: Path | None = None,
            timeout_sec: int | None = None, stream: bool | None = None,
            output_sink: Callable[[str], None] | None = None) -> CommandResult:
        use_stream = self._stream if stream is None else stream
        timeout = self._timeout if timeout_sec is None else timeout_sec
        if self._cancel and self._cancel.is_set():
            raise ProcessingCancelled()
        try:
            process = subprocess.Popen(args, cwd=str(cwd) if cwd else None,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                shell=False, **spawn_options())
        except OSError as exc:
            raise EngineError("TOOL_START_FAILED", "无法启动解压工具，请检查工具设置。") from exc
        scope = ProcessScope(process)
        chunks: queue.Queue[bytes | None] = queue.Queue(maxsize=32)
        stop = threading.Event()

        def read_output() -> None:
            try:
                assert process.stdout is not None
                while not stop.is_set():
                    chunk = process.stdout.read1(8192)
                    if not chunk:
                        break
                    while not stop.is_set():
                        try:
                            chunks.put(chunk, timeout=0.1)
                            break
                        except queue.Full:
                            continue
            finally:
                while not stop.is_set():
                    try:
                        chunks.put(None, timeout=0.1)
                        break
                    except queue.Full:
                        continue

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        decoder = codecs.getincrementaldecoder(self._encoding)(errors="replace")
        tail = ""
        pending_line = ""
        deadline = time.monotonic() + timeout if timeout else None
        reason = ""
        eof = False
        try:
            while not eof:
                if self._cancel and self._cancel.is_set():
                    raise ProcessingCancelled()
                if self._guard:
                    self._guard()
                if deadline and time.monotonic() >= deadline:
                    reason = "timeout"
                    terminate_process_tree(process)
                    break
                try:
                    chunk = chunks.get(timeout=0.1)
                except queue.Empty:
                    continue
                eof = chunk is None
                value = decoder.decode(chunk or b"", final=eof)
                tail = (tail + value)[-self._max_output:]
                pending_line += value
                while "\n" in pending_line or len(pending_line) > 8192 or (eof and pending_line):
                    if "\n" in pending_line and pending_line.index("\n") < 8192:
                        line, pending_line = pending_line.split("\n", 1)
                    else:
                        line, pending_line = pending_line[:8192], pending_line[8192:]
                    line = line.rstrip("\r")
                    if output_sink:
                        output_sink(line)
                    if self._line_sink:
                        self._line_sink(line)
                    elif use_stream:
                        print(line)
                    if self._abort_on_line and self._abort_on_line(line):
                        reason = "aborted by output guard: " + line
                        terminate_process_tree(process)
                        eof = True
                        break
            # stdout may close before the process exits; keep deadline/cancel checks alive.
            while process.poll() is None:
                if self._cancel and self._cancel.is_set():
                    raise ProcessingCancelled()
                if self._guard:
                    self._guard()
                if deadline and time.monotonic() >= deadline:
                    reason = "timeout"
                    terminate_process_tree(process)
                    break
                time.sleep(0.05)
            process.wait(timeout=5)
        except BaseException:
            terminate_process_tree(process)
            raise
        finally:
            stop.set()
            scope.close()
            reader.join(timeout=1)
            if process.stdout and not reader.is_alive():
                process.stdout.close()
        code = 124 if reason == "timeout" else (130 if reason else int(process.returncode or 0))
        return CommandResult(code == 0, code, tail, reason, bool(reason and reason != "timeout"))
