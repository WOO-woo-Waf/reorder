from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class DecryptorStrategy(ABC):
    @abstractmethod
    def can_handle(self, path: Path) -> bool:
        ...

    @abstractmethod
    def prepare(self, path: Path, *, workspace: Path | None = None, dry_run: bool = False) -> list[Path]:
        ...


class RestorerStrategy(ABC):
    @abstractmethod
    def can_handle(self, path: Path) -> bool:
        ...

    @abstractmethod
    def restore(self, path: Path, *, workspace: Path | None = None, dry_run: bool = False) -> list[Path]:
        ...
