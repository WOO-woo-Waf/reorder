from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

# 路径任一分量命中则整文件跳过（防止动到源码树、Git、虚拟环境等）
_MANDATORY_EXCLUDE_DIR_PARTS: frozenset[str] = frozenset(
    {
        ".git",
        "src",
        ".venv",
        "venv",
        ".cursor",
        ".idea",
        "node_modules",
    }
)


@dataclass(frozen=True)
class FlattenMoveRecord:
    src: Path
    dst: Path


def detect_reorder_engine_repo_root() -> Path | None:
    """若本文件位于「含 pyproject.toml 的 reorder_engine 源码仓库」内，则返回仓库根目录。"""
    p = Path(__file__).resolve()
    try:
        project = p.parents[3]
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
    """若应跳过展平则返回人类可读原因，否则返回 None。"""
    root = folder.resolve()
    if allowed_roots:
        allowed_resolved: list[Path] = []
        for raw in allowed_roots:
            s = (raw or "").strip()
            if not s:
                continue
            allowed_resolved.append(Path(s).expanduser().resolve())
        if not allowed_resolved:
            return "已配置 flatten.allowed_roots 但无有效路径，已跳过展平"
        if not any(root == ar or root.is_relative_to(ar) for ar in allowed_resolved):
            return "目标目录不在 flatten.allowed_roots 白名单内，已跳过展平（避免误伤项目/系统目录）"
        return None

    checkout = detect_reorder_engine_repo_root()
    if checkout is not None and not allow_inside_project_repo:
        if root == checkout or root.is_relative_to(checkout):
            return (
                "检测到目标目录位于 reorder_engine 源码仓库内，已跳过展平；"
                "请在下载目录运行，或在 config.json 设置 beta.flatten.allowed_roots，"
                "或启用 beta.flatten.allow_inside_project_repo / 命令行 --allow-flatten-in-project"
            )
    return None


class FolderFlattener:
    """把 root 下所有子文件夹中的文件移动到 root（不递归处理文件夹内容，直接展平）。

    - 用于 beta 模式：先把所有文件放在同一层，便于统一尝试解压。
    - 不删除空目录（避免误删用户目录结构）；你可后续加清理阶段。
    """

    def flatten(self, root: Path, *, dry_run: bool = False, exclude_dirs: set[str] | None = None) -> list[FlattenMoveRecord]:
        user_exclude = exclude_dirs or {"success", "failed", "tools", "__pycache__"}
        exclude_dirs = set(user_exclude) | _MANDATORY_EXCLUDE_DIR_PARTS
        moves: list[FlattenMoveRecord] = []

        if not root.exists():
            return moves

        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.parent == root:
                continue
            if any(part in exclude_dirs for part in p.parts):
                continue

            dst = root / p.name
            dst = self._dedupe(dst)
            moves.append(FlattenMoveRecord(src=p, dst=dst))
            if dry_run:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dst))

        return moves

    def _dedupe(self, dst: Path) -> Path:
        if not dst.exists():
            return dst
        stem = dst.stem
        suf = dst.suffix
        for i in range(1, 1000):
            cand = dst.with_name(f"{stem} ({i}){suf}")
            if not cand.exists():
                return cand
        return dst
