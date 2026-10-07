from __future__ import annotations

import os
import re
import shutil
import stat
import struct
import time
import zipfile
from pathlib import Path, PurePosixPath

from reorder_engine.application.errors import EngineError
from reorder_engine.domain.models import ExtractionResult
from reorder_engine.infrastructure.command_runner import ExternalCommandRunner
from reorder_engine.interfaces.extracting import ExtractorStrategy

DEVICE = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.I)


def validate_member(name: str) -> None:
    value = name.replace("\\", "/")
    parts = PurePosixPath(value).parts
    if (not value or len(value) > 4096 or value.startswith("/") or
            any(ord(c) < 32 for c in value) or
            any(p in {"..", "."} or ":" in p or DEVICE.match(p) or p.endswith((" ", ".")) for p in parts)):
        raise EngineError("UNSAFE_ARCHIVE", "归档包含越界路径、设备名或危险文件名，已停止处理。")


class WorkspaceGuard:
    def __init__(self, workspace: Path, *, byte_limit: int):
        self.workspace = workspace
        self.byte_limit = byte_limit
        self._last = 0.0

    def check(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last < 1:
            return
        self._last = now
        total = 0
        for directory, dirs, files in os.walk(self.workspace, followlinks=False):
            base = Path(directory)
            for name in dirs + files:
                child = base / name
                if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
                    raise EngineError("UNSAFE_ARCHIVE", "解压结果包含链接，已停止处理。")
            for name in files:
                total += (base / name).stat().st_size
                if total > self.byte_limit:
                    raise EngineError("OUTPUT_LIMIT", "工作区达到空间上限，原件保留。")
        if shutil.disk_usage(self.workspace).free < 64 * 1024 * 1024:
            raise EngineError("DISK_FULL", "工作区剩余空间不足，已停止处理。")


class ArchiveSafetyInspector:
    """Fail closed per candidate; normalized candidates are checked again."""

    def __init__(self, seven_zip: str, runner: ExternalCommandRunner, max_bytes: int):
        self.seven_zip = seven_zip
        self.runner = runner
        self.max_bytes = max_bytes

    def inspect(self, archive: Path, password: str | None) -> None:
        if zipfile.is_zipfile(archive):
            # ZIP central directories are bounded before zipfile builds its list.
            if archive.stat().st_size > self.max_bytes:
                raise EngineError("OUTPUT_LIMIT", "归档超过当前空间限制。")
            with archive.open("rb") as stream:
                size = archive.stat().st_size
                stream.seek(max(0, size - 65557))
                tail = stream.read(65557)
            position = tail.rfind(b"PK\x05\x06")
            if position < 0 or position + 22 > len(tail):
                raise EngineError("ARCHIVE_UNREADABLE", "ZIP 目录记录不完整。")
            header = struct.unpack_from("<4s4H2LH", tail, position)
            if header[4] == 0xffff or header[5] == 0xffffffff:
                # Avoid allocating an unbounded ZIP64 directory in Python.
                self._inspect_cli(archive, password)
                return
            if header[4] > 100000 or header[5] > 64 * 1024 * 1024:
                raise EngineError("OUTPUT_LIMIT", "ZIP 成员目录过大。")
            with zipfile.ZipFile(archive) as handle:
                infos = handle.infolist()
                if len(infos) > 100000:
                    raise EngineError("OUTPUT_LIMIT", "归档成员过多。")
                total = 0
                for info in infos:
                    validate_member(info.filename)
                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode) or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                        raise EngineError("UNSAFE_ARCHIVE", "归档包含链接或特殊文件。")
                    total += info.file_size
                    if total > self.max_bytes:
                        raise EngineError("OUTPUT_LIMIT", "归档声明的解压大小超过空间限制。")
            return

        self._inspect_cli(archive, password)

    def _inspect_cli(self, archive: Path, password: str | None) -> None:
        record: dict[str, str] = {}
        count = 0
        total = 0

        def finish() -> None:
            nonlocal count, total
            if "Path" in record:
                count += 1
                validate_member(record["Path"])
                if any(k.lower() in {"symbolic link", "hard link", "reparse point"} for k in record):
                    raise EngineError("UNSAFE_ARCHIVE", "归档包含链接，已拒绝解压。")
                if "l" in record.get("Attributes", "").split() or "lrwx" in record.get("Mode", ""):
                    raise EngineError("UNSAFE_ARCHIVE", "归档包含链接。")
                size = record.get("Size", "0")
                if size.isdigit():
                    total += int(size)
                if count > 100000 or total > self.max_bytes:
                    raise EngineError("OUTPUT_LIMIT", "归档声明的输出超过限制。")
            record.clear()

        def consume(line: str) -> None:
            if not line.strip():
                finish()
            elif " = " in line:
                key, value = line.split(" = ", 1)
                if key in record:
                    raise EngineError("UNSAFE_ARCHIVE", "归档成员信息存在歧义。")
                record[key] = value

        args = [self.seven_zip, "l", "-slt", "-ba", "-sccUTF-8", "-p" + (password or ""), str(archive.resolve())]
        result = self.runner.run(args, output_sink=consume)
        finish()
        if result.exit_code == 124:
            raise EngineError("TOOL_TIMEOUT", "归档预检超时。")
        if result.exit_code not in (0, 1) or count == 0:
            raise EngineError("ARCHIVE_UNREADABLE", result.stdout[-2000:] or "无法列出归档成员，候选未解压。")


class GuardedExtractor(ExtractorStrategy):
    def __init__(self, delegate: ExtractorStrategy, safety: ArchiveSafetyInspector, guard: WorkspaceGuard):
        self.delegate = delegate
        self.safety = safety
        self.guard = guard

    def name(self) -> str:
        return self.delegate.name()

    def is_available(self) -> bool:
        return self.delegate.is_available()

    def extract(self, request, *, dry_run=False):
        return self.extract_with_password(request, None, dry_run=dry_run)

    def extract_with_password(self, request, password, *, dry_run=False):
        if not dry_run:
            try:
                self.safety.inspect(request.volume_set.entry, password)
            except EngineError as exc:
                if exc.code != "ARCHIVE_UNREADABLE":
                    raise
                return ExtractionResult(volume_set=request.volume_set, ok=False, tool=self.name(), message=str(exc))
        result = self.delegate.extract_with_password(request, password, dry_run=dry_run)
        self.guard.check(force=True)
        if result.exit_code == 124:
            raise EngineError("TOOL_TIMEOUT", "解压工具超时，原件保留。")
        return result
