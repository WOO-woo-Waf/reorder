from __future__ import annotations

from pathlib import Path

from reorder_engine.domain.models import KeywordLibrary


class KeywordRepository:
    def load(self, path: Path) -> KeywordLibrary:
        keywords: list[str] = []
        if not path.exists():
            return KeywordLibrary(keywords=tuple())
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            keywords.append(raw)
        # 长词优先，减少局部覆盖
        keywords.sort(key=len, reverse=True)
        return KeywordLibrary(keywords=tuple(keywords))
