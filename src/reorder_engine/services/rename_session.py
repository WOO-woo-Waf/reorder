from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reorder_engine.domain.models import RenameRecord
from reorder_engine.services.cleaning import SafeRenamer


@dataclass
class RenameSession:
    """记录一次处理过程中的重命名，便于失败时回滚。"""

    renamer: SafeRenamer
    records: list[RenameRecord]

    @classmethod
    def create(cls, renamer: SafeRenamer) -> "RenameSession":
        return cls(renamer=renamer, records=[])

    def rename(self, src: Path, dst: Path, *, dry_run: bool = False) -> Path:
        rec = self.renamer.rename_file(src, dst, dry_run=dry_run)
        if rec is not None:
            self.records.append(rec)
            return rec.after
        return src

    def rollback_best_effort(self, *, dry_run: bool = False) -> None:
        # 逆序回滚，尽量恢复到 before；如果 before 已存在则跳过
        for rec in reversed(self.records):
            if not rec.after.exists():
                continue
            if rec.before.exists():
                continue
            self.renamer.rename_file(rec.after, rec.before, dry_run=dry_run)
