from __future__ import annotations

from abc import ABC, abstractmethod


class PipelineStage(ABC):
    """阶段3/未来扩展点：允许在解压前后插入自定义处理。

    v0.1 只定义接口，不绑定具体业务。
    """

    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def run(self, context: object) -> object:
        ...
