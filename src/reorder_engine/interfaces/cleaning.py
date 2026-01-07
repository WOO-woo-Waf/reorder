from __future__ import annotations

from abc import ABC, abstractmethod


class FilenameCleanerStrategy(ABC):
    @abstractmethod
    def clean_stem(self, stem: str) -> str:
        """清洗文件名 stem（不含扩展名）。"""


class FilenameNormalizer(ABC):
    @abstractmethod
    def normalize_for_grouping(self, stem: str) -> str:
        """用于分卷分组的归一化（更“粗粒度”）。"""
