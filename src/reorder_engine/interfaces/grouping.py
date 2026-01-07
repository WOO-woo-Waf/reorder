from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from reorder_engine.domain.models import VolumeSet


class VolumeGroupingStrategy(ABC):
    @abstractmethod
    def group(self, paths: list[Path]) -> list[VolumeSet]:
        ...
