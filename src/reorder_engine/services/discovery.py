from __future__ import annotations

import re
from pathlib import Path


_ARCHIVE_SUFFIXES = {
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
}


class ArchiveDiscoveryService:
    def discover(self, root: Path, *, recursive: bool = True) -> list[Path]:
        if not root.exists():
            return []

        paths: list[Path] = []
        it = root.rglob("*") if recursive else root.glob("*")
        for p in it:
            if not p.is_file():
                continue
            if self._is_candidate(p):
                paths.append(p)
        return paths

    def _is_candidate(self, p: Path) -> bool:
        # 额外考虑分卷：.001/.z01/.r00/.7z.001 等
        suf = p.suffix.lower()
        if suf in _ARCHIVE_SUFFIXES:
            return True
        if re.fullmatch(r"\.\d{3}", suf):
            return True
        if p.name.lower().endswith((".7z.001", ".zip.001")):
            return True
        if suf.startswith(".r") and len(suf) == 4 and suf[2:].isdigit():
            return True
        if suf.startswith(".z") and len(suf) == 4 and suf[2:].isdigit():
            return True
        # partXX.
        name = p.name.lower()
        if ".part" in name and (name.endswith(".rar") or name.endswith(".zip") or name.endswith(".7z")):
            return True
        return False
