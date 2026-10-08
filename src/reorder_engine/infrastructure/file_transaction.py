from __future__ import annotations

import errno
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


def _is_link(path: Path) -> bool:
    """True for a symlink, or a Windows junction when ``Path.is_junction`` exists."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is None:
        return False
    try:
        return bool(is_junction())
    except OSError:
        return True


def _device_of(path: Path) -> int | None:
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


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


def move_exclusive(source: Path, target: Path) -> None:
    """Rename a regular file into place without copying and without overwriting.

    The work folder and the output folder share one volume, so a hard link followed
    by removing the source renames the file atomically and refuses to replace an
    existing target. Only on filesystems without hard links does the platform fall
    back to a safe copy-then-install; source bytes are never re-hashed on the fast
    path.
    """
    install_exclusive(source, target)


def link_exclusive(source: Path, target: Path) -> bool:
    """Create one exclusive hard link without touching the source.

    Returns ``True`` when the link was created. Returns ``False`` when the volume has
    no hard links (FAT/exFAT and some network shares); the caller then falls back to
    an exclusive rename. An existing target always raises and is never replaced.
    """
    try:
        os.link(source, target)
        return True
    except FileExistsError:
        raise
    except OSError:
        if target.exists() or target.is_symlink():
            raise
        return False


def _copy_tree(source: Path, destination: Path) -> None:
    destination.mkdir()
    for directory, dirs, files in os.walk(source, followlinks=False):
        base = Path(directory)
        relative = base.relative_to(source)
        for name in dirs:
            child = base / name
            if child.is_symlink() or _is_link(child):
                raise EngineError("UNSAFE_OUTPUT", "解压结果包含链接目录。")
            (destination / relative / name).mkdir(parents=True, exist_ok=True)
        for name in files:
            copy_verified(base / name, destination / relative / name)


def _move_tree(source: Path, target: Path) -> None:
    """Move a directory within one volume; fall back to a verified copy on EXDEV."""
    try:
        os.rename(source, target)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    # Cross-device is not expected because work lives under the output root; keep a
    # verified copy so a nonstandard mount never loses data.
    temporary = target.parent / (".reorder-" + str(uuid4()) + ".partial")
    try:
        _copy_tree(source, temporary)
        if target.exists() or target.is_symlink():
            raise EngineError("OUTPUT_CONFLICT", "发布时目标出现冲突，未覆盖。")
        os.rename(temporary, target)
        shutil.rmtree(source, ignore_errors=True)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


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

    def _check_inside_output(self, target: Path) -> Path:
        """Validate a destination path's ancestry without creating anything yet.

        ``reject_links`` walks the target and every ancestor (including above the
        output root), so a junction or symlink introduced after the plan was created
        cannot redirect creation outside the chosen folder.
        """
        reject_links(target)
        absolute = target.absolute()
        try:
            absolute.relative_to(self.output_root)
        except ValueError as exc:
            raise EngineError("INVALID_OUTPUT", "文件目标越过授权输出目录。") from exc
        current = absolute.parent
        while True:
            if current.is_symlink() or _is_link(current):
                raise EngineError("UNSAFE_OUTPUT", "输出路径包含符号链接。")
            if current == self.output_root or current.parent == current:
                break
            current = current.parent
        if self.output_root.is_symlink() or _is_link(self.output_root):
            raise EngineError("UNSAFE_OUTPUT", "输出根目录不能是符号链接。")
        return absolute

    def _safe_parent(self, target: Path) -> None:
        """Validate ancestry, then create only the target's parent directory."""
        absolute = self._check_inside_output(target)
        absolute.parent.mkdir(parents=True, exist_ok=True)

    def _safe_directory(self, directory: Path) -> Path:
        """Validate and create one destination directory before any file is written."""
        absolute = self._check_inside_output(directory)
        absolute.mkdir(parents=True, exist_ok=True)
        return absolute

    def _unique_target(self, target: Path) -> Path:
        self._safe_parent(target)
        if not target.exists() and not target.is_symlink():
            return target
        return self._duplicate_target(target)

    def _duplicate_target(self, target: Path) -> Path:
        """A non-existing path under ``_duplicates``; never overwrites a file."""
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
        """Publish one workspace entry into the output without overwriting old files.

        Directories are merged into an existing same-named directory recursively;
        file collisions are diverted to ``_duplicates``. Work and output share one
        volume, so publishing is an exclusive move and no bytes are copied or
        re-hashed.
        """
        if source.is_symlink() or _is_link(source):
            raise EngineError("UNSAFE_OUTPUT", "解压结果包含符号链接。")
        if source.is_dir():
            return self._publish_directory(source, target)
        return self._publish_file(source, self._unique_target(target))

    def _publish_file(self, source: Path, target: Path) -> str:
        action = self._record(source, target, kind="publish")
        try:
            move_exclusive(source, target)
            self.repository.action_phase(action, "published")
            self.repository.action_phase(action, "committed")
            return str(target)
        except Exception:
            # An absent destination is known not to have been published.
            if not target.exists() and not target.is_symlink():
                self.repository.action_phase(action, "abandoned")
            raise

    def _publish_directory(self, source: Path, target: Path) -> str:
        self._safe_parent(target)
        if _is_link(target):
            raise EngineError("UNSAFE_OUTPUT", "输出路径包含符号链接。")
        if not target.exists():
            return self._publish_new_directory(source, target)
        if not target.is_dir():
            # The name is taken by a plain file: keep the whole folder as a duplicate.
            return self._publish_new_directory(source, self._duplicate_target(target))
        return self._merge_directory(source, target)

    def _publish_new_directory(self, source: Path, target: Path) -> str:
        self._safe_parent(target)
        action = self._record(source, target, kind="publish")
        try:
            if target.exists() or target.is_symlink() or _is_link(target):
                raise EngineError("OUTPUT_CONFLICT", "发布时目标出现冲突，未覆盖。")
            _move_tree(source, target)
            self.repository.action_phase(action, "published")
            self.repository.action_phase(action, "committed")
            return str(target)
        except Exception:
            if not target.exists() and not target.is_symlink():
                self.repository.action_phase(action, "abandoned")
            raise

    def _merge_directory(self, source: Path, target: Path) -> str:
        action = self._record(source, target, kind="publish")
        # Reuse the existing directory and merge its children with exclusive moves.
        # A failure mid-merge leaves the action open so recovery flags the package
        # for review instead of claiming a clean publish.
        self._merge_children(source, target)
        self.repository.action_phase(action, "published")
        self.repository.action_phase(action, "committed")
        return str(target)

    def _merge_children(self, source: Path, target: Path) -> None:
        for entry in sorted(source.iterdir(), key=lambda item: item.name):
            if entry.is_symlink() or _is_link(entry):
                raise EngineError("UNSAFE_OUTPUT", "解压结果包含链接。")
            if entry.is_dir():
                child = target / entry.name
                if _is_link(child):
                    raise EngineError("UNSAFE_OUTPUT", "输出路径包含符号链接。")
                if child.exists() and not child.is_dir():
                    self._publish_new_directory(entry, self._duplicate_target(child))
                    continue
                # Validate and reuse or create the nested directory; never follow a
                # link and never overwrite an existing directory's contents.
                self._safe_directory(child)
                self._merge_children(entry, child)
            else:
                self._publish_file(entry, self._unique_target(target / entry.name))

    def publish_children(self, source: Path, destination: Path) -> list[str]:
        if not source.exists():
            return []
        results = []
        for child in sorted(source.iterdir(), key=lambda p: p.name):
            results.append(self.publish(child, destination / child.name))
        return results

    def route_sources(self, package: PlannedPackage, destination: Path) -> list[str]:
        """Route every verified original into its destination, then remove the source.

        Every member identity is checked before any original is touched. When the
        originals already sit on the output volume they are hard-linked into place
        first and only then have their original names removed, so no bytes or hashes
        are recomputed. Members on another volume are copied and verified first, then
        the original is removed. Both paths stage every target before any source
        removal so a volume set is never left half-archived. A member that already
        sits at its intended destination (for example a retry whose failed original
        was routed there earlier) is registered in place and never shuffled into
        ``_duplicates``.
        """
        for snapshot in package.members:
            validate_source(snapshot)
        # Validate the destination ancestry (links/containment) before creating it.
        destination = self._safe_directory(destination)
        output_device = _device_of(destination)
        same_volume = (output_device is not None
                       and all(snapshot.device == output_device for snapshot in package.members))
        if same_volume:
            return self._route_sources_in_place(package, destination)
        return self._route_sources_copied(package, destination)

    def _route_sources_in_place(self, package: PlannedPackage, destination: Path) -> list[str]:
        # Phase 1: create every target as an exclusive hard link (no source removal)
        # and record it. A later allocation error leaves all originals intact and any
        # created links reusable.
        results: list[str] = []
        linked: list[tuple[SourceSnapshot, Path, str]] = []
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
            try:
                if link_exclusive(source, target):
                    linked.append((snapshot, target, action))
                else:
                    # No hard links on this volume: the fastest safe move is an
                    # exclusive rename, which may journal a partial move for review.
                    _install_without_hardlinks(source, target)
                    self.repository.action_phase(action, "copied")
                    self.repository.action_phase(action, "source_removed")
                    self.repository.action_phase(action, "committed")
                results.append(str(target))
            except Exception:
                if not target.exists() and not target.is_symlink():
                    self.repository.action_phase(action, "abandoned")
                raise
        # Phase 2: re-validate every snapshot, then remove the original names.
        for snapshot, _target, _action in linked:
            validate_source(snapshot)
        for snapshot, _target, action in linked:
            self.repository.action_phase(action, "copied")
            Path(snapshot.path).unlink()
            self.repository.action_phase(action, "source_removed")
            self.repository.action_phase(action, "committed")
        return results

    def _route_sources_copied(self, package: PlannedPackage, destination: Path) -> list[str]:
        prepared: list[tuple[SourceSnapshot, Path, str]] = []
        results: list[str] = []
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
