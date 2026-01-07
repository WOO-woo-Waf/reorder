from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class DecryptorStrategy(ABC):
    @abstractmethod
    def can_handle(self, path: Path) -> bool:
        ...

    @abstractmethod
    def prepare(self, path: Path) -> list[Path]:
        """对文件进行解密/准备，返回需要进入后续流程的文件列表。

        v0.1 默认返回 [path]。
        """


class RestorerStrategy(ABC):
    """“还原/修复/格式还原”阶段：某些文件被改后缀、轻度加密或需要专用工具还原后才能解压。

    设计目标：把外部还原软件调用封装为可插拔策略。
    """

    @abstractmethod
    def can_handle(self, path: Path) -> bool:
        ...

    @abstractmethod
    def restore(self, path: Path) -> list[Path]:
        """还原后返回需要进入后续解压阶段的文件列表。"""
