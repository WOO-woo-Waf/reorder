from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path

from reorder_engine.application.models import DesktopSettings
from reorder_engine.infrastructure.desktop_paths import DesktopPaths


class SettingsRepository:
    def __init__(self, paths: DesktopPaths):
        self.paths = paths
        self._file = paths.data_root / "settings.json"
        self._lock = threading.RLock()
        self._settings = DesktopSettings()
        if self._file.exists():
            self._settings = DesktopSettings.model_validate_json(self._file.read_bytes())

    def get(self) -> DesktopSettings:
        with self._lock:
            return self._settings.model_copy(deep=True)

    def revision(self) -> str:
        with self._lock:
            return hashlib.sha256(self._settings.model_dump_json().encode()).hexdigest()

    def update(self, settings: DesktopSettings) -> DesktopSettings:
        with self._lock:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=self._file.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(settings.model_dump_json(indent=2))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, self._file)
            finally:
                Path(name).unlink(missing_ok=True)
            self._settings = settings.model_copy(deep=True)
            return self.get()

    def resolve_tool(self, name: str) -> str | None:
        configured = getattr(self.get().tools, name)
        if configured:
            candidate = Path(configured)
            if candidate.is_absolute() and candidate.is_file():
                return str(candidate.resolve())
            return None
        names = {
            "seven_zip": ("7z.exe", "7za.exe", "7zz", "7z", "7za"),
            "unrar": ("unrar.exe", "rar.exe", "unrar", "rar"),
            "bandizip": ("bz.exe", "bandizip.exe", "bz"),
        }[name]
        if os.name != "nt":
            names = tuple(value for value in names if not value.endswith(".exe"))
        tools = self.paths.app_root / "tools"
        if tools.is_dir():
            for filename in names:
                candidates = sorted(tools.rglob(filename), key=lambda p: (len(p.parts), str(p)))
                if candidates:
                    return str(candidates[0].resolve())
        for filename in names:
            found = shutil.which(filename)
            if found:
                return str(Path(found).resolve())
        return None
