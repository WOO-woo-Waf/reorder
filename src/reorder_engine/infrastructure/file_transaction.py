from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from uuid import uuid4

from reorder_engine.application.errors import EngineError, ProcessingCancelled
from reorder_engine.application.models import PlannedPackage, SourceSnapshot
from reorder_engine.application.planning import source_snapshot, validate_source, reject_links
from reorder_engine.infrastructure.job_repository import JobRepository

CHUNK_BYTES = 4 * 1024 * 1024


def install_exclusive(temporary: Path, target: Path) -> None:
    """Install a verified file at ``target`` without ever overwriting an existing file.

    A hard link is tried first because it is atomic and refuses to replace a file.
    FAT/exFAT and some network volumes have no hard links; on those Windows falls back
    to a same-volume rename that also refuses to replace an existing target, so a
    failure never exposes a truncated file at the final path.
    """
    try:
        os.link(temporary, target)
    except FileExistsError:
        raise
    except OSError:
        if target.exists() or target.is_symlink():
            raise
        _install_without_hardlinks(temporary, target)
        return
    temporary.unlink(missing_ok=True)


def _install_without_hardlinks(temporary: Path, target: Path) -> None:
    if os.name == "nt":
        # os.rename on Windows maps to a move that raises FileExistsError when the
        # target exists, so the destination is never replaced and no truncated final
        # file is exposed if the process dies mid-copy.
        os.rename(temporary, target)
        return
    # POSIX has no exclusive rename; an exclusive create still never overwrites, and
    # copy_verified removes only the incomplete target this call created.
    copy_verified(temporary, target)
    temporary.unlink(missing_ok=True)


def check_cancel(cancel_event) -> None:
    if cancel_event.is_set():
        raise ProcessingCancelled()


def copy_verified(source: Path, destination: Path, *, cancel_event=None) -> None:
    """Copy into a private destination, flush, and verify bytes before publishing.

    On failure only the destination file this call newly created is removed. An
    existing destination is never touched (exclusive create fails first) and the
    source is never modified.
    """
    if source.is_symlink() or not source.is_file():
        raise EngineError("UNSAFE_FILE", "不能复制符号链接或非普通文件。")
    before = source_snapshot(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    digest = hashlib.sha256()
    try:
        with source.open("rb") as src, destination.open("xb") as dst:
            created = True
            while chunk := src.read(CHUNK_BYTES):
                if cancel_event is not None:
                    check_cancel(cancel_event)
                digest.update(chunk)
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        validate_source(before)
        actual = hashlib.sha256()
        with destination.open("rb") as dst:
            while chunk := dst.read(CHUNK_BYTES):
                if cancel_event is not None:
                    check_cancel(cancel_event)
                actual.update(chunk)
        if actual.digest() != digest.digest():
            raise EngineError("COPY_MISMATCH", "文件复制校验失败，原件未删除。")
        shutil.copystat(source, destination, follow_symlinks=False)
    except BaseException:
        if created:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise


class FileTransaction:
    """Centralize journaled publishing and source routing; never trust work-copy names."""

    def __init__(self, repository: JobRepository, *, job_id: str, package_id: str, output_root: Path):
        self.repository = repository
        self.job_id = job_id
        self.package_id = package_id
        reject_links(output_root)
        self.output_root = output_root.resolve()

    def _safe_parent(self, target: Path) -> None:
        reject_links(target)
        target = target.absolute()
        try:
            target.relative_to(self.output_root)
        except ValueError as exc:
            raise EngineError("INVALID_OUTPUT", "文件目标越过授权输出目录。") from exc
        current = target.parent
        while True:
            if current.is_symlink():
                raise EngineError("UNSAFE_OUTPUT", "输出路径包含符号链接。")
            if current == self.output_root or current.parent == current:
                break
            current = current.parent
        if self.output_root.is_symlink():
            raise EngineError("UNSAFE_OUTPUT", "输出根目录不能是符号链接。")
        target.parent.mkdir(parents=True, exist_ok=True)

    def _unique_target(self, target: Path) -> Path:
        self._safe_parent(target)
        if not target.exists() and not target.is_symlink():
            return target
        duplicate = target.parent / "_duplicates" / self.job_id / self.package_id
        for index in range(10000):
            candidate = duplicate / (str(index) if index else "first") / target.name
            self._safe_parent(candidate)
            if not candidate.exists() and not candidate.is_symlink():
                return candidate
        raise EngineError("OUTPUT_CONFLICT", "同名输出过多，未覆盖现有文件。")

    def _record(self, source: Path, target: Path, *, kind: str, snapshot: SourceSnapshot | None = None) -> str:
        return self.repository.record_action({
            "job_id": self.job_id, "package_id": self.package_id, "kind": kind,
            "source": str(source), "destination": str(target),
            "snapshot": snapshot.model_dump() if snapshot else None,
        })

    def publish(self, source: Path, target: Path) -> str:
        target = self._unique_target(target)
        action = self._record(source, target, kind="publish")
        temporary = target.parent / (".reorder-" + str(uuid4()) + ".partial")
        try:
            if source.is_symlink():
                raise EngineError("UNSAFE_OUTPUT", "解压结果包含符号链接。")
            if source.is_dir():
                temporary.mkdir()
                for directory, dirs, files in os.walk(source, followlinks=False):
                    base = Path(directory)
                    relative = base.relative_to(source)
                    for name in dirs:
                        child = base / name
                        if child.is_symlink():
                            raise EngineError("UNSAFE_OUTPUT", "解压结果包含链接目录。")
                        (temporary / relative / name).mkdir(parents=True, exist_ok=True)
                    for name in files:
                        copy_verified(base / name, temporary / relative / name)
            else:
                copy_verified(source, temporary)
            # Hard-link creation is exclusive; it never replaces an existing file.
            if temporary.is_file():
                install_exclusive(temporary, target)
            else:
                if target.exists():
                    raise EngineError("OUTPUT_CONFLICT", "发布时目标出现冲突，未覆盖。")
                os.rename(temporary, target)
            self.repository.action_phase(action, "published")
            self.repository.action_phase(action, "committed")
            return str(target)
        except Exception:
            # An absent destination is known not to have been published.
            if not target.exists() and not target.is_symlink():
                self.repository.action_phase(action, "abandoned")
            if temporary.exists():
                if temporary.is_dir():
                    shutil.rmtree(temporary)
                else:
                    temporary.unlink()
            raise

    def publish_children(self, source: Path, destination: Path) -> list[str]:
        if not source.exists():
            return []
        results = []
        for child in sorted(source.iterdir(), key=lambda p: p.name):
            results.append(self.publish(child, destination / child.name))
        return results

    def route_sources(self, package: PlannedPackage, destination: Path) -> list[str]:
        """Copy and verify every member before removing any source member.

        A member that already sits at its intended destination (for example a retry
        whose failed original was routed there earlier) is registered in place and is
        neither copied nor unlinked, so retries never shuffle it into ``_duplicates``.
        """
        prepared: list[tuple[SourceSnapshot, Path, str]] = []
        results: list[str] = []
        for snapshot in package.members:
            validate_source(snapshot)
        for snapshot in package.members:
            source = Path(snapshot.path)
            desired = destination / source.name
            if self._is_same_file(source, desired):
                action = self._record(source, desired, kind="route_source", snapshot=snapshot)
                self.repository.action_phase(action, "copied")
                self.repository.action_phase(action, "committed")
                results.append(str(desired))
                continue
            target = self._unique_target(desired)
            action = self._record(source, target, kind="route_source", snapshot=snapshot)
            temporary = target.parent / (".reorder-" + str(uuid4()) + ".partial")
            try:
                copy_verified(source, temporary)
                install_exclusive(temporary, target)
                self.repository.action_phase(action, "copied")
                prepared.append((snapshot, target, action))
                results.append(str(target))
            except Exception:
                if not target.exists():
                    self.repository.action_phase(action, "abandoned")
                raise
            finally:
                temporary.unlink(missing_ok=True)
        # At this point copies for all members exist; cancellation is deferred
        # until this commit section ends to avoid half-archived volume sets.
        for snapshot, _target, _action in prepared:
            validate_source(snapshot)
        for snapshot, _target, action in prepared:
            Path(snapshot.path).unlink()
            self.repository.action_phase(action, "source_removed")
            self.repository.action_phase(action, "committed")
        return results

    @staticmethod
    def _is_same_file(source: Path, target: Path) -> bool:
        """True when the source already resolves to the intended destination path."""
        try:
            return source.exists() and target.exists() and os.path.samefile(source, target)
        except OSError:
            return False
