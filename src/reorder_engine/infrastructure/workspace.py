from __future__ import annotations

import uuid
from pathlib import Path

from reorder_engine.application.errors import EngineError
from reorder_engine.application.planning import reject_links

# Bulk work lives under the user-chosen output folder, never under app data.
WORKSPACES_PARENT = ("intermediate", "workspaces")


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


def workspaces_root(output_root: Path) -> Path:
    """The fixed parent of every run workspace: ``<output>/intermediate/workspaces``."""
    return Path(output_root).joinpath(*WORKSPACES_PARENT)


def planned_run_workspace(output_root: Path, job_id: str, package_id: str) -> Path:
    """Deterministic primary location ``<output>/intermediate/workspaces/<job>/<package>``.

    Recovery can derive this from the stored plan when the journal entry is missing;
    the actual run may sit in a unique sibling when this path was already occupied.
    """
    return workspaces_root(output_root) / job_id / package_id


def guard_workspace_path(output_root: Path, target: Path) -> None:
    """Reject links/junctions and escapes between ``output_root`` and ``target``.

    Only ``output_root`` and its descendants are inspected here; the full ancestry
    above the chosen folder is validated separately with ``reject_links`` before any
    directory is created. Every existing component from the root down must be a real
    directory so a symlink or junction cannot redirect bulk work outside the chosen
    folder before any source is copied.
    """
    root = Path(output_root).absolute()
    current = Path(target).absolute()
    try:
        current.relative_to(root)
    except ValueError as exc:
        raise EngineError("INVALID_OUTPUT", "工作目录越过授权输出目录。") from exc
    chain: list[Path] = []
    while True:
        chain.append(current)
        if current == root or current.parent == current:
            break
        current = current.parent
    for part in reversed(chain):  # from the output root down to the target
        if _is_link(part):
            raise EngineError("UNSAFE_OUTPUT", "工作目录路径包含符号链接或连接点。")
        if part.exists() and not part.is_dir():
            raise EngineError("UNSAFE_OUTPUT", "工作目录路径被非目录占用。")


def allocate_run_workspace(output_root: Path, job_id: str, package_id: str) -> Path:
    """Create an owned run directory under ``<output>/intermediate/workspaces``.

    Parent directories (``intermediate``/``workspaces``/``<job_id>``) are reused when
    present; any preexisting content is preserved and never cleared or overwritten.
    The leaf run directory is created exclusively so cleanup can delete only data this
    run created; when the fixed ``<package_id>`` location is already occupied a unique
    sibling is used instead of touching the existing directory.
    """
    root = Path(output_root)
    # Validate the full chosen-root ancestry (including ancestors above the output
    # folder) before creating anything, so a link changed after scanning cannot
    # create directories outside the selected folder.
    reject_links(root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EngineError("WORKSPACE_FAILED", "无法创建工作目录，请检查输出目录权限。") from exc
    guard_workspace_path(root, root)
    parent = workspaces_root(root) / job_id
    # Validate the chain before creating anything so an existing symlink/junction
    # component cannot redirect the creation into the link target.
    guard_workspace_path(root, parent)
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EngineError("WORKSPACE_FAILED", "无法创建工作目录，请检查输出目录权限。") from exc
    guard_workspace_path(root, parent)
    base = package_id or "package"
    for index in range(10000):
        candidate = parent / (base if index == 0 else f"{base}-{uuid.uuid4().hex[:12]}")
        try:
            candidate.mkdir()  # exclusive create: this directory belongs to the run
        except FileExistsError:
            continue
        except OSError as exc:
            raise EngineError("WORKSPACE_FAILED", "无法创建工作目录，请检查输出目录权限。") from exc
        guard_workspace_path(root, candidate)
        return candidate
    raise EngineError("OUTPUT_CONFLICT", "同名工作目录过多，未删除任何既有内容。")
