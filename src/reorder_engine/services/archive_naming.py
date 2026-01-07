from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ArchiveNameParts:
    """把“可清洗的 base”与“技术尾巴(分卷/格式)”拆开，避免清洗破坏分卷模式。"""

    base: str
    mid: str  # 例如 .part01 或 .7z 或 .zip
    end: str  # 最终后缀，例如 .rar / .001 / .z01 / .r00

    def rebuild(self) -> str:
        return f"{self.base}{self.mid}{self.end}"


def split_archive_name(filename: str) -> ArchiveNameParts:
    """从文件名拆分出可清洗 base，并保留分卷相关片段。

    示例：
    - name.part01.rar  -> base=name, mid=.part01, end=.rar
    - name.7z.001      -> base=name, mid=.7z, end=.001
    - name.zip.001     -> base=name, mid=.zip, end=.001
    - name.r00         -> base=name, mid=, end=.r00
    - name.z01         -> base=name, mid=, end=.z01
    - name.rar         -> base=name, mid=, end=.rar
    """

    # split archives: .7z.001 / .zip.001
    m = re.match(r"^(?P<base>.+)\.(?P<mid>7z|zip)\.(?P<idx>\d{3})$", filename, flags=re.IGNORECASE)
    if m:
        return ArchiveNameParts(
            base=m.group("base"),
            mid=f".{m.group('mid')}",
            end=f".{m.group('idx')}",
        )

    # part volumes: .part01.rar / .part1.rar
    m = re.match(
        r"^(?P<base>.+)\.part(?P<idx>\d{1,3})\.(?P<ext>rar|zip|7z)$",
        filename,
        flags=re.IGNORECASE,
    )
    if m:
        idx = m.group("idx")
        return ArchiveNameParts(base=m.group("base"), mid=f".part{idx}", end=f".{m.group('ext')}")

    # default: split at last dot
    dot = filename.rfind(".")
    if dot <= 0:
        return ArchiveNameParts(base=filename, mid="", end="")

    base = filename[:dot]
    end = filename[dot:]
    return ArchiveNameParts(base=base, mid="", end=end)
