from __future__ import annotations

from pathlib import Path

from reorder_engine.interfaces.decrypting import DecryptorStrategy


class PassthroughDecryptor(DecryptorStrategy):
    def can_handle(self, path: Path) -> bool:
        return True

    def prepare(self, path: Path, *, workspace: Path | None = None, dry_run: bool = False) -> list[Path]:
        _ = (workspace, dry_run)
        return [path]


class DecryptionService:
    def __init__(self, decryptors: list[DecryptorStrategy]):
        self._decryptors = decryptors

    def prepare(self, path: Path, *, workspace: Path | None = None, dry_run: bool = False) -> list[Path]:
        outputs: list[Path] = []
        for decryptor in self._decryptors:
            if decryptor.can_handle(path):
                outputs.extend(decryptor.prepare(path, workspace=workspace, dry_run=dry_run))
        return outputs or [path]
