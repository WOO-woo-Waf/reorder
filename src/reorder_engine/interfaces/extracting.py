from __future__ import annotations

from abc import ABC, abstractmethod

from reorder_engine.domain.models import ExtractionRequest, ExtractionResult


class ExtractorStrategy(ABC):
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def extract(self, request: ExtractionRequest, *, dry_run: bool = False) -> ExtractionResult:
        ...
