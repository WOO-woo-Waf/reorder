from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from reorder_engine.domain.models import CollisionPolicy

_MANDATORY_EXCLUDE_DIR_PARTS: frozenset[str] = frozenset(
    {
        ".git",
        "src",
        ".venv",
        "venv",
        ".cursor",
        ".idea",
        "node_modules",
        CollisionPolicy.DUPLICATES_DIR.value,
    }
)


@dataclass(frozen=True)
class FlattenMoveRecord:
    src: Path
    dst: Path


def detect_reorder_engine_repo_root() -> Path | None:
    path = Path(__file__).resolve()
    try:
        project = path.parents[3]
    except IndexError:
        return None
    if (project / "pyproject.toml").exists() and (project / "src" / "reorder_engine").is_dir():
        return project
    return None


def flatten_safety_check(
    folder: Path,
    *,
    allowed_roots: tuple[str, ...],
    allow_inside_project_repo: bool,
) -> str | None:
    root = folder.resolve()
    if allowed_roots:
        allowed_resolved: list[Path] = []
        for raw in allowed_roots:
            value = (raw or "").strip()
            if value:
                allowed_resolved.append(Path(value).expanduser().resolve())
        if not allowed_resolved:
            return "flatten.allowed_roots is set but empty after resolution"
        if not any(root == allowed or root.is_relative_to(allowed) for allowed in allowed_resolved):
            return "target folder is outside flatten.allowed_roots"
        return None

    checkout = detect_reorder_engine_repo_root()
    if checkout is not None and not allow_inside_project_repo:
        if root == checkout or root.is_relative_to(checkout):
            return "target folder is inside the reorder_engine repository"
    return None


class FolderFlattener:
    def flatten(self, root: Path, *, dry_run: bool = False, exclude_dirs: set[str] | None = None) -> list[FlattenMoveRecord]:
        user_exclude = exclude_dirs or {"success", "failed", "error_files", "tools", "__pycache__", "intermediate", "final"}
        excluded = set(user_exclude) | set(_MANDATORY_EXCLUDE_DIR_PARTS)
        moves: list[FlattenMoveRecord] = []

        if not root.exists():
            return moves

        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.parent == root:
                continue
            if any(part in excluded for part in path.parts):
                continue

            dst = root / path.name
            if dst.exists():
                dst = self._duplicate_target(root, path)
            moves.append(FlattenMoveRecord(src=path, dst=dst))
            if dry_run:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(dst))

        if not dry_run:
            self._remove_empty_dirs(root, excluded=excluded)
        return moves

    def _duplicate_target(self, root: Path, src: Path) -> Path:
        duplicate_root = root / CollisionPolicy.DUPLICATES_DIR.value / src.parent.name
        dst = duplicate_root / src.name
        if not dst.exists():
            return dst
        for index in range(1, 1000):
            nested = duplicate_root / f"copy_{index}" / src.name
            if not nested.exists():
                return nested
        return duplicate_root / src.name

    def _remove_empty_dirs(self, root: Path, *, excluded: set[str]) -> None:
        dirs = [path for path in root.rglob("*") if path.is_dir()]
        for path in sorted(dirs, key=lambda item: len(item.parts), reverse=True):
            if path == root:
                continue
            if any(part in excluded for part in path.parts if part != root.name):
                continue
            try:
                next(path.iterdir())
            except StopIteration:
                path.rmdir()
            except OSError:
                continue


def deepest_wrapper_dir(root: Path) -> Path:
    junk_files = {"desktop.ini", "thumbs.db"}
    current = root
    while current.exists() and current.is_dir():
        entries = list(current.iterdir())
        dirs = [item for item in entries if item.is_dir()]
        files = [item for item in entries if item.is_file() and item.name.lower() not in junk_files]
        if files or len(dirs) != 1:
            break
        current = dirs[0]
    return current
