from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
from pathlib import Path, PureWindowsPath

from reorder_engine.application.errors import EngineError
from reorder_engine.application.planning import reject_links
from reorder_engine.infrastructure.file_transaction import move_exclusive


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is None:
        return False
    try:
        return bool(is_junction())
    except OSError:
        return True


def _ensure_real_directories(directory: Path) -> None:
    """Create a directory chain without following preexisting links or files."""

    absolute = directory.absolute()
    reject_links(absolute)
    chain: list[Path] = []
    current = absolute
    while not current.exists():
        chain.append(current)
        if current.parent == current:
            break
        current = current.parent
    for component in reversed(chain):
        reject_links(component)
        try:
            component.mkdir()
        except FileExistsError:
            pass
        reject_links(component)
        try:
            metadata = component.lstat()
        except OSError as exc:
            raise EngineError("UNSAFE_OUTPUT", "无法安全创建解压输出目录。") from exc
        if not stat.S_ISDIR(metadata.st_mode):
            raise EngineError("UNSAFE_OUTPUT", "解压输出路径包含非目录组件。")
    reject_links(absolute)
    if absolute.exists() and not stat.S_ISDIR(absolute.lstat().st_mode):
        raise EngineError("UNSAFE_OUTPUT", "解压输出路径不是目录。")


def _validated_entries(source: Path) -> list[tuple[Path, bool]]:
    """Collect regular directories and files; reject links and special entries."""

    if _is_link(source):
        raise EngineError("UNSAFE_OUTPUT", "解压结果根目录包含链接。")
    try:
        source_mode = source.lstat().st_mode
    except OSError as exc:
        raise EngineError("UNSAFE_OUTPUT", "无法读取解压结果根目录。") from exc
    if not stat.S_ISDIR(source_mode):
        raise EngineError("UNSAFE_OUTPUT", "解压结果根目录不是普通目录。")

    entries: list[tuple[Path, bool]] = []
    for current, directories, files in os.walk(source, followlinks=False):
        base = Path(current)
        for name in directories:
            entry = base / name
            if _is_link(entry):
                raise EngineError("UNSAFE_OUTPUT", "解压结果包含链接目录。")
            try:
                metadata = entry.lstat()
            except OSError as exc:
                raise EngineError("UNSAFE_OUTPUT", "无法读取解压结果目录。") from exc
            if not stat.S_ISDIR(metadata.st_mode):
                raise EngineError("UNSAFE_OUTPUT", "解压结果包含特殊目录项。")
            entries.append((entry, True))
        for name in files:
            entry = base / name
            if _is_link(entry):
                raise EngineError("UNSAFE_OUTPUT", "解压结果包含链接文件。")
            try:
                metadata = entry.lstat()
            except OSError as exc:
                raise EngineError("UNSAFE_OUTPUT", "无法读取解压结果文件。") from exc
            if not stat.S_ISREG(metadata.st_mode):
                raise EngineError("UNSAFE_OUTPUT", "解压结果包含特殊文件。")
            entries.append((entry, False))
    return sorted(entries, key=lambda item: (len(item[0].relative_to(source).parts), item[0].as_posix()))


class ExtractionOutputScope:
    """Isolate one extraction matrix's output from existing user files.

    Staging lives beside the requested output directory so files can be moved
    onto the same filesystem. At most one failed attempt is retained as a
    candidate while the next attempt runs.
    """

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self._native_windows = os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", str(output_dir)) is not None
        if self._native_windows:
            drive = str(output_dir)[0].lower()
            windows_tail = str(output_dir)[3:].replace("\\", "/")
            self._filesystem_output_dir = Path(f"/mnt/{drive}") / windows_tail
        else:
            self._filesystem_output_dir = self.output_dir
        _ensure_real_directories(self._filesystem_output_dir.parent)
        self._root = Path(tempfile.mkdtemp(prefix=".reorder-extract-", dir=self._filesystem_output_dir.parent))
        self._tool_root = (
            PureWindowsPath(str(output_dir)).parent / self._root.name
            if self._native_windows
            else self._root
        )
        self._attempt: Path | None = None
        self._tool_attempt: Path | None = None
        self._candidate: Path | None = None

    def new_attempt(self) -> Path:
        self._discard_attempt()
        self._attempt = Path(tempfile.mkdtemp(prefix="attempt-", dir=self._root))
        self._tool_attempt = self._tool_root / self._attempt.name
        return Path(str(self._tool_attempt))

    @property
    def attempt_filesystem_dir(self) -> Path | None:
        return self._attempt

    def keep_as_candidate(self) -> None:
        if self._candidate is not None:
            shutil.rmtree(self._candidate, ignore_errors=True)
        self._candidate = self._attempt
        self._attempt = None
        self._tool_attempt = None

    def discard_attempt(self) -> None:
        self._discard_attempt()

    def discard_candidate(self) -> None:
        if self._candidate is not None:
            shutil.rmtree(self._candidate, ignore_errors=True)
            self._candidate = None

    def promote_attempt(self) -> None:
        if self._attempt is None:
            return
        self._promote(self._attempt)
        self._attempt = None

    def promote_candidate(self) -> None:
        if self._candidate is None:
            return
        self._promote(self._candidate)
        self._candidate = None

    def cleanup(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)
        self._attempt = None
        self._tool_attempt = None
        self._candidate = None

    def _discard_attempt(self) -> None:
        if self._attempt is not None:
            shutil.rmtree(self._attempt, ignore_errors=True)
            self._attempt = None
            self._tool_attempt = None

    def _promote(self, source: Path) -> None:
        entries = _validated_entries(source)
        _ensure_real_directories(self._filesystem_output_dir)
        directory_map: dict[Path, Path] = {Path("."): self._filesystem_output_dir}
        directories = [(path, is_directory) for path, is_directory in entries if is_directory]
        files = [(path, is_directory) for path, is_directory in entries if not is_directory]

        for path, _is_directory_entry in directories:
            relative = path.relative_to(source)
            parent = directory_map[relative.parent]
            target = self._ensure_destination_directory(parent / relative.name)
            directory_map[relative] = target

        for path, _is_directory_entry in files:
            relative = path.relative_to(source)
            parent = directory_map[relative.parent]
            base_target = parent / relative.name
            target = self._available_destination(base_target)
            while True:
                reject_links(target)
                if target.exists() or _is_link(target):
                    target = self._available_destination(base_target)
                    continue
                try:
                    move_exclusive(path, target)
                    break
                except FileExistsError:
                    target = self._available_destination(base_target)

    def _ensure_destination_directory(self, destination: Path) -> Path:
        base_target = destination
        target = base_target
        while True:
            reject_links(target)
            try:
                metadata = target.lstat()
            except FileNotFoundError:
                try:
                    target.mkdir()
                except FileExistsError:
                    continue
                reject_links(target)
                if not stat.S_ISDIR(target.lstat().st_mode):
                    raise EngineError("UNSAFE_OUTPUT", "解压目录目标不是普通目录。")
                return target
            except OSError as exc:
                raise EngineError("UNSAFE_OUTPUT", "无法检查解压目录目标。") from exc

            if _is_link(target):
                raise EngineError("UNSAFE_OUTPUT", "解压输出目录包含链接或连接点。")
            if stat.S_ISDIR(metadata.st_mode):
                return target
            if not stat.S_ISREG(metadata.st_mode):
                raise EngineError("UNSAFE_OUTPUT", "解压目录目标包含特殊文件。")
            target = self._available_destination(base_target)

    @staticmethod
    def _available_destination(destination: Path) -> Path:
        if not destination.exists() and not _is_link(destination):
            return destination
        stem, suffix = destination.stem, destination.suffix
        index = 1
        while True:
            candidate = destination.with_name(f"{stem}.recovered-{index}{suffix}")
            if not candidate.exists() and not _is_link(candidate):
                return candidate
            index += 1
