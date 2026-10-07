from __future__ import annotations

import os
from pathlib import Path
from reorder_engine.application.errors import EngineError


class EngineLock:
    """Process lock released by the OS even if the engine crashes."""

    def __init__(self, path: Path):
        self._file = path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._file.seek(0)
                if not self._file.read(1):
                    self._file.write(b"0")
                    self._file.flush()
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file.close()
            raise EngineError("ENGINE_IN_USE", "另一实例正在使用此任务数据目录。") from exc

    def close(self) -> None:
        self._file.close()
