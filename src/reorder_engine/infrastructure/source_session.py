from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, TypeVar

from reorder_engine.application.errors import EngineError
from reorder_engine.application.models import PlannedPackage, SourceSnapshot
from reorder_engine.application.planning import source_snapshot, validate_source
from reorder_engine.domain.models import RenameRecord, VolumeSet
from reorder_engine.infrastructure.file_transaction import move_exclusive
from reorder_engine.infrastructure.job_repository import JobRepository
from reorder_engine.services.cleaning import SafeRenamer

T = TypeVar("T")


class DirectSourceSession:
    """Track the selected originals while the legacy pipeline works in place.

    No archive bytes are copied here. Only this session's restores and renames
    may update a planned identity. Applied edits stay pending until the original
    group is routed, so interruption cannot silently retry a changed source.
    """

    def __init__(self, package: PlannedPackage, repository: JobRepository, *, job_id: str):
        self.package, self.repository, self.job_id = package, repository, job_id
        self._current = {member.path: member for member in package.members}
        self._pending: list[str] = []
        for member in package.members:
            validate_source(member)

    @staticmethod
    def _key(path: Path | str) -> str:
        return os.path.normcase(str(Path(path).absolute()))

    def _owner(self, path: Path) -> str | None:
        key = self._key(path)
        return next((owner for owner, item in self._current.items()
                     if self._key(item.path) == key), None)

    def volume_set(self) -> VolumeSet:
        return VolumeSet(entry=Path(self._current[self.package.entry].path),
                         members=tuple(Path(item.path) for item in self._current.values()),
                         group_key=self.package.group_key)

    def _record(self, before: SourceSnapshot, target: Path, kind: str) -> str:
        return self.repository.record_action({
            "job_id": self.job_id, "package_id": self.package.package_id,
            "kind": kind, "source": before.path, "destination": str(target),
            "snapshot": before.model_dump()})

    def change(self, path: Path, operation: Callable[[], T], *, dry_run: bool = False) -> T:
        owner = self._owner(path)
        if owner is None or dry_run:
            return operation()  # derived/nested files belong to the owned workspace
        before = self._current[owner]
        validate_source(before)
        action = self._record(before, path, "source_edit")
        try:
            result = operation()
            after = source_snapshot(path)
            if (after.device, after.inode) != (before.device, before.inode):
                raise EngineError("INPUT_CHANGED", "原地还原期间输入被替换，请检查文件。")
        except BaseException:
            # A no-op probe that failed before touching the source need not block
            # retry. A partially written original deliberately remains pending.
            try:
                unchanged = source_snapshot(path) == before
            except (OSError, EngineError):
                unchanged = False
            if unchanged:
                self.repository.action_phase(action, "abandoned")
            raise
        self._current[owner] = after
        if after == before:
            self.repository.action_phase(action, "abandoned")
        else:
            self.repository.action_phase(action, "applied")
            self._pending.append(action)
        return result

    def rename(self, source: Path, target: Path, *, dry_run: bool = False) -> RenameRecord | None:
        if source == target:
            return None
        owner = self._owner(source)
        if owner is None:
            return SafeRenamer().rename_file(source, target, dry_run=dry_run)
        if dry_run:
            return RenameRecord(source, target)
        before = self._current[owner]
        validate_source(before)
        action = self._record(before, target, "source_rename")
        try:
            # Targets share the source parent; exclusive rename never replaces
            # an unrelated archive that appeared after variant planning.
            move_exclusive(source, target)
            after = source_snapshot(target)
            if (after.device, after.inode, after.size, after.mtime_ns) != (
                    before.device, before.inode, before.size, before.mtime_ns):
                raise EngineError("INPUT_CHANGED", "原地改名期间输入发生变化，请检查文件。")
        except BaseException:
            try:
                unchanged = source_snapshot(source) == before
            except (OSError, EngineError):
                unchanged = False
            if unchanged:
                self.repository.action_phase(action, "abandoned")
            raise
        self._current[owner] = after
        self.repository.action_phase(action, "applied")
        self._pending.append(action)
        return RenameRecord(source, target)

    def routing_package(self) -> PlannedPackage:
        for item in self._current.values():
            validate_source(item)
        return self.package.model_copy(update={
            "entry": self._current[self.package.entry].path,
            "members": list(self._current.values())})

    def complete(self) -> None:
        """Called only after all current originals have finished routing."""
        for action in self._pending:
            self.repository.action_phase(action, "committed")
        self._pending.clear()


class SourceRenamer(SafeRenamer):
    def __init__(self, session: DirectSourceSession):
        self.session = session

    def rename_file(self, src: Path, dst: Path, *, dry_run: bool = False):
        return self.session.rename(src, dst, dry_run=dry_run)


class SourceRestoration:
    """Keep all existing restoration strategies behind a journaled adapter."""

    def __init__(self, delegate, session: DirectSourceSession, workspace: Path):
        self.delegate, self.session = delegate, session
        self.workspace = workspace

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def restore_with_rollbacks(self, path, *, dry_run=False, **kwargs):
        # The legacy multi-volume reveal passes entry.parent. Keep any derived
        # restoration files in the selected work folder even for direct sources.
        target = kwargs.get("workspace")
        if target is None or not Path(target).is_relative_to(self.workspace):
            kwargs["workspace"] = self.workspace / "restore"
        return self.session.change(path,
            lambda: self.delegate.restore_with_rollbacks(path, dry_run=dry_run, **kwargs),
            dry_run=dry_run)

    def force_apate_restore_with_rollbacks(self, path, *, dry_run=False, **kwargs):
        return self.session.change(path,
            lambda: self.delegate.force_apate_restore_with_rollbacks(path, dry_run=dry_run, **kwargs),
            dry_run=dry_run)

    def rollback_apate(self, records, *, dry_run=False):
        for record in reversed(records):
            self.session.change(record.path,
                lambda record=record: self.delegate.rollback_apate([record], dry_run=dry_run),
                dry_run=dry_run)
