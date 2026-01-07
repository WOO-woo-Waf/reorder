from __future__ import annotations

from pathlib import Path

from reorder_engine.domain.models import PasswordLibrary


class PasswordRepository:
    def load(self, path: Path) -> PasswordLibrary:
        passwords: list[str] = []
        if not path.exists():
            return PasswordLibrary(passwords=tuple())

        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            raw = line.strip()
            if not raw:
                continue
            if raw.startswith("#"):
                continue
            passwords.append(raw)

        # 去重但保持顺序
        seen: set[str] = set()
        deduped: list[str] = []
        for p in passwords:
            if p in seen:
                continue
            seen.add(p)
            deduped.append(p)

        return PasswordLibrary(passwords=tuple(deduped))
