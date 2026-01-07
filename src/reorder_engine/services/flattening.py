from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FlattenMoveRecord:
    src: Path
    dst: Path


class FolderFlattener:
    """把 root 下所有子文件夹中的文件移动到 root（不递归处理文件夹内容，直接展平）。

    - 用于 beta 模式：先把所有文件放在同一层，便于统一尝试解压。
    - 不删除空目录（避免误删用户目录结构）；你可后续加清理阶段。
    """

    def flatten(self, root: Path, *, dry_run: bool = False, exclude_dirs: set[str] | None = None) -> list[FlattenMoveRecord]:
        exclude_dirs = exclude_dirs or {"success", "failed", "tools", "__pycache__"}
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
