from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from reorder_engine.application.errors import EngineError
from reorder_engine.application.models import PlanRequest, PlannedPackage, ProcessingPlan, SourceSnapshot
from reorder_engine.infrastructure.desktop_paths import DesktopPaths
from reorder_engine.infrastructure.job_repository import utc_now
from reorder_engine.infrastructure.settings_repository import SettingsRepository
from reorder_engine.services.cleaning import DefaultGroupingNormalizer
from reorder_engine.services.grouping import DefaultVolumeGroupingStrategy

RESULT_DIRS = frozenset({"success", "final", "failed", "error_files", "deferred_volumes", "intermediate", "_duplicates"})
EXCLUDED_EXTENSIONS = frozenset({".bat", ".cmd", ".ps1", ".py", ".json", ".md", ".log", ".dll"})
EXCLUDED_NAMES = frozenset({"passwords.txt", "keywords.txt", "target_folder.txt"})


def reject_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise EngineError("UNSAFE_PATH", "输入和输出路径不能包含符号链接或连接点。")


def source_snapshot(path: Path) -> SourceSnapshot:
    reject_links(path)
    if path.is_symlink() or not path.is_file():
        raise EngineError("INVALID_INPUT", "输入必须是普通文件，不支持符号链接。")
    stat = path.stat()
    return SourceSnapshot(path=str(path.resolve()), size=stat.st_size, mtime_ns=stat.st_mtime_ns, device=stat.st_dev, inode=stat.st_ino)


def validate_source(snapshot: SourceSnapshot) -> None:
    try:
        current = source_snapshot(Path(snapshot.path))
    except OSError as exc:
        raise EngineError("INPUT_CHANGED", "输入文件已不可用，请重新扫描。") from exc
    if current != snapshot:
        raise EngineError("INPUT_CHANGED", "扫描后输入文件发生变化，请重新扫描。")


def within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class PlanService:
    MAX_FILES = 10000
    MAX_PACKAGES = 1000

    def __init__(self, paths: DesktopPaths, settings: SettingsRepository):
        self.paths = paths
        self.settings = settings
        self.grouper = DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer())

    def create(self, request: PlanRequest) -> ProcessingPlan:
        output = Path(request.output_root).expanduser()
        if not output.is_absolute():
            raise EngineError("INVALID_OUTPUT", "输出目录必须是绝对路径。")
        reject_links(output)
        output = output.resolve()
        if within(output, self.paths.app_root) or within(output, self.paths.data_root):
            raise EngineError("INVALID_OUTPUT", "输出不能位于软件安装目录或内部数据目录。")
        if output.exists() and not output.is_dir():
            raise EngineError("INVALID_OUTPUT", "输出位置不是目录。")
        inputs: dict[str, Path] = {}
        warnings: list[str] = []
        for raw in request.input_paths:
            path = Path(raw).expanduser()
            if not path.is_absolute() or path.is_symlink() or not path.exists():
                raise EngineError("INVALID_INPUT", "输入位置不存在或不是有效的绝对路径。")
            reject_links(path)
            path = path.resolve()
            if within(path, self.paths.app_root) or within(path, self.paths.data_root):
                raise EngineError("INVALID_INPUT", "不能处理软件安装目录或内部数据。")
            if path.is_dir():
                self._scan(path, inputs, recursive=request.options.recursive)
            elif path.is_file():
                inputs[os.path.normcase(str(path))] = path
        files = [p for p in inputs.values() if p.name.lower() not in EXCLUDED_NAMES and p.suffix.lower() not in EXCLUDED_EXTENSIONS]
        if not files:
            raise EngineError("NO_INPUT", "没有找到可处理的文件。")
        if len(files) > self.MAX_FILES:
            raise EngineError("BATCH_TOO_LARGE", "单批文件超过 10000，请分批处理。")
        # Keep same-name archives from different directories in separate groups.
        parents: dict[Path, list[Path]] = {}
        for file in files:
            parents.setdefault(file.parent, []).append(file)
        packages: list[PlannedPackage] = []
        for parent, paths in sorted(parents.items(), key=lambda x: str(x[0])):
            for group in self.grouper.group(paths):
                packages.append(PlannedPackage(
                    package_id=str(uuid4()), name=group.entry.name,
                    group_key=group.group_key, entry=str(group.entry),
                    members=[source_snapshot(p) for p in group.members],
                ))
        if len(packages) > self.MAX_PACKAGES:
            raise EngineError("BATCH_TOO_LARGE", "单批文件组超过 1000，请分批处理。")
        if request.options.recursive:
            warnings.append("已扫描子目录；原件按文件组归档，不会预先展平输入。")
        warnings.append("处理完成后原包会移动到输出目录的归档或错误分类，处理前不会改写原件。")
        return ProcessingPlan(
            plan_id=str(uuid4()), created_at=utc_now(), output_root=str(output),
            options=request.options, settings_revision=self.settings.revision(),
            packages=packages, warnings=warnings,
        )

    def _scan(self, directory: Path, files: dict[str, Path], *, recursive: bool) -> None:
        if len(files) > self.MAX_FILES:
            raise EngineError("BATCH_TOO_LARGE", "文件过多，请分批处理。")
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                path = Path(entry.path)
                if hasattr(path, "is_junction") and path.is_junction():
                    continue
                if entry.is_file(follow_symlinks=False):
                    files[os.path.normcase(str(path.resolve()))] = path.resolve()
                    if len(files) > self.MAX_FILES:
                        raise EngineError("BATCH_TOO_LARGE", "文件过多，请分批处理。")
                elif recursive and entry.is_dir(follow_symlinks=False) and entry.name not in RESULT_DIRS:
                    if not within(path.resolve(), self.paths.data_root) and not within(path.resolve(), self.paths.app_root):
                        self._scan(path, files, recursive=True)

    def validate_sources(self, plan: ProcessingPlan) -> None:
        if plan.settings_revision != self.settings.revision():
            raise EngineError("SETTINGS_CHANGED", "扫描后设置已改变，请重新扫描。")
        for package in plan.packages:
            for member in package.members:
                validate_source(member)
