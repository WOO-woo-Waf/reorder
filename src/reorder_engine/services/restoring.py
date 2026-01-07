from __future__ import annotations

from pathlib import Path

from reorder_engine.interfaces.decrypting import RestorerStrategy


class PassthroughRestorer(RestorerStrategy):
    def can_handle(self, path: Path) -> bool:
        return True

    def restore(self, path: Path) -> list[Path]:
        return [path]


class RestorationService:
    def __init__(self, restorers: list[RestorerStrategy]):
        self._restorers = restorers

    def restore(self, path: Path, *, dry_run: bool = False) -> list[Path]:
        # dry_run 下同样不写文件即可
        for r in self._restorers:
            if r.can_handle(path):
                return r.restore(path)
        return [path]
