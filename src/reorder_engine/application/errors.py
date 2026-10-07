from __future__ import annotations


class EngineError(RuntimeError):
    """A stable business error safe to show at the protocol boundary."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ProcessingCancelled(EngineError):
    def __init__(self):
        super().__init__("CANCELLED", "任务已取消，原件保持原位置。")
