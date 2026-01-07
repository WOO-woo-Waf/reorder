from __future__ import annotations

from pathlib import Path

from reorder_engine.interfaces.decrypting import DecryptorStrategy


class PassthroughDecryptor(DecryptorStrategy):
    def can_handle(self, path: Path) -> bool:
        return True

    def prepare(self, path: Path) -> list[Path]:
        return [path]


class DecryptionService:
    def __init__(self, decryptors: list[DecryptorStrategy]):
        self._decryptors = decryptors

    def prepare(self, path: Path, *, dry_run: bool = False) -> list[Path]:
        # dry_run 下也保持行为一致（不写文件即可）
        for d in self._decryptors:
            if d.can_handle(path):
                return d.prepare(path)
        return [path]
