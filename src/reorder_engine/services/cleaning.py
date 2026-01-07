from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from reorder_engine.domain.models import KeywordLibrary, RenameRecord
from reorder_engine.interfaces.cleaning import FilenameCleanerStrategy, FilenameNormalizer


@dataclass(frozen=True)
class CleaningContext:
    keywords: KeywordLibrary


class KeywordStripCleaner(FilenameCleanerStrategy):
    def __init__(self, ctx: CleaningContext):
        self._ctx = ctx

    def clean_stem(self, stem: str) -> str:
        out = stem
        for kw in self._ctx.keywords.keywords:
            out = out.replace(kw, " ")
        return out


class BasicPunctuationCleaner(FilenameCleanerStrategy):
    _fullwidth_map = str.maketrans(
        {
            "（": "(",
            "）": ")",
            "【": "[",
            "】": "]",
            "，": ",",
            "。": ".",
            "　": " ",
        }
    )

    def clean_stem(self, stem: str) -> str:
        out = stem.translate(self._fullwidth_map)
        # 常见分隔符统一为空格
        out = re.sub(r"[._]+", " ", out)
        out = re.sub(r"\s+", " ", out).strip()
        return out


class TailIndexCleaner(FilenameCleanerStrategy):
    """去掉末尾常见的“(1)”这种拷贝序号，但不影响分卷 part 号。"""

    def clean_stem(self, stem: str) -> str:
        return re.sub(r"\s*\((\d{1,3})\)\s*$", "", stem).strip()


class DefaultGroupingNormalizer(FilenameNormalizer):
    """用于分卷分组的更粗归一化：忽略大小写、空白、括号序号等细微差异。"""

    def normalize_for_grouping(self, stem: str) -> str:
        out = stem.lower()
        out = re.sub(r"\s+", " ", out).strip()
        out = re.sub(r"\s*\((\d{1,3})\)\s*$", "", out).strip()
        return out


class FilenameCleaningService:
    def __init__(
        self,
        cleaners: list[FilenameCleanerStrategy],
    ):
        self._cleaners = cleaners

    def clean_stem(self, stem: str) -> str:
        out = stem
        for cleaner in self._cleaners:
            out = cleaner.clean_stem(out)
        # 最终再压缩空白
        out = re.sub(r"\s+", " ", out).strip()
        return out


class SafeRenamer:
    def rename_file(self, src: Path, dst: Path, *, dry_run: bool = False) -> RenameRecord | None:
        if src == dst:
            return None
        if dst.exists():
            dst = self._dedupe_path(dst)
        if not dry_run:
            src.rename(dst)
        return RenameRecord(before=src, after=dst)

    def _dedupe_path(self, dst: Path) -> Path:
        parent = dst.parent
        stem = dst.stem
        suffix = dst.suffix
        for i in range(1, 1000):
            cand = parent / f"{stem} ({i}){suffix}"
            if not cand.exists():
                return cand
        raise RuntimeError(f"Too many name collisions for: {dst}")
