from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DesktopPaths:
    """Resolve user state separately from immutable installation resources."""

    app_root: Path
    data_root: Path

    @classmethod
    def discover(cls, *, app_root: Path | None = None, data_root: Path | None = None) -> "DesktopPaths":
        installed = app_root or (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3])
        if data_root is None:
            if sys.platform == "win32":
                base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
            elif sys.platform == "darwin":
                base = Path.home() / "Library/Application Support"
            else:
                base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
            data_root = base / "ReOrder"
        return cls(installed.resolve(), data_root.resolve())

    @property
    def work_root(self) -> Path:
        return self.data_root / "work"

    @property
    def logs_root(self) -> Path:
        return self.data_root / "logs"

    def initialize(self) -> None:
        for directory in (self.data_root, self.work_root, self.logs_root):
            directory.mkdir(parents=True, exist_ok=True)
