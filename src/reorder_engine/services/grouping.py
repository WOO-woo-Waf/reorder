from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from reorder_engine.domain.models import VolumeSet
from reorder_engine.interfaces.cleaning import FilenameNormalizer
from reorder_engine.interfaces.grouping import VolumeGroupingStrategy


class DefaultVolumeGroupingStrategy(VolumeGroupingStrategy):
    """把候选文件分组成分卷集合，并尽量确定入口文件。"""

    def __init__(self, normalizer: FilenameNormalizer):
        self._normalizer = normalizer

    def group(self, paths: list[Path]) -> list[VolumeSet]:
        buckets: dict[str, list[Path]] = defaultdict(list)
        for p in paths:
            key = self._group_key(p)
            buckets[key].append(p)

        out: list[VolumeSet] = []
        for key, members in buckets.items():
            members_sorted = sorted(members, key=lambda x: x.name.lower())
            entry = self._pick_entry(members_sorted)
            out.append(VolumeSet(entry=entry, members=tuple(members_sorted), group_key=key))
        return sorted(out, key=lambda vs: vs.entry.name.lower())

    def _group_key(self, p: Path) -> str:
        name = p.name

        # 1) 处理 .7z.001 / .zip.001
        m = re.match(r"^(?P<base>.+)\.(7z|zip)\.(?P<idx>\d{3})$", name, flags=re.IGNORECASE)
        if m:
            base = m.group("base")
            base_norm = self._normalizer.normalize_for_grouping(base)
            return f"split:{base_norm}.{m.group(2).lower()}"

        # 2) 处理 name.part01.rar / name.part1.rar
        m = re.match(r"^(?P<base>.+)\.part(?P<idx>\d{1,3})\.(?P<ext>rar|zip|7z)$", name, flags=re.IGNORECASE)
        if m:
            base_norm = self._normalizer.normalize_for_grouping(m.group("base"))
            return f"part:{base_norm}.{m.group('ext').lower()}"

        # 3) 处理 rar 的 r00/r01...
        if re.match(r"^.+\.r\d{2}$", name, flags=re.IGNORECASE):
            base = name[:-4]
            base_norm = self._normalizer.normalize_for_grouping(base)
            return f"rxx:{base_norm}.rar"

        # 4) 处理 zip 的 z01/z02...
        if re.match(r"^.+\.z\d{2}$", name, flags=re.IGNORECASE):
            base = name[:-4]
            base_norm = self._normalizer.normalize_for_grouping(base)
            return f"zxx:{base_norm}.zip"

        # 5) 处理 .001/.002...（不带显式 .7z/.zip）
        if re.match(r"^.+\.\d{3}$", name, flags=re.IGNORECASE):
            base = name[:-4]
            base_norm = self._normalizer.normalize_for_grouping(base)
            return f"num:{base_norm}"

        # 6) 主文件也要与老式分卷共用同一个分组键
        suffix = p.suffix.lower()
        if suffix == ".rar":
            stem_norm = self._normalizer.normalize_for_grouping(p.stem)
            return f"rxx:{stem_norm}.rar"
        if suffix == ".zip":
            stem_norm = self._normalizer.normalize_for_grouping(p.stem)
            return f"zxx:{stem_norm}.zip"

        # 7) 单卷：按 stem+suffix
        stem_norm = self._normalizer.normalize_for_grouping(p.stem)
        return f"single:{stem_norm}{suffix}"

    def _pick_entry(self, members: list[Path]) -> Path:
        # 优先入口：
        # - .7z.001/.zip.001/.001
        # - .part01.*
        # - .rar（如果有 r00/r01）
        # - .zip（如果有 z01/z02）
        for p in members:
            low = p.name.lower()
            if low.endswith(".7z.001") or low.endswith(".zip.001"):
                return p
        for p in members:
            if re.search(r"\.part0*1\.(rar|zip|7z)$", p.name, flags=re.IGNORECASE):
                return p
        for p in members:
            if p.suffix.lower() == ".001":
                return p
        for p in members:
            if p.suffix.lower() == ".rar":
                return p
        for p in members:
            if p.suffix.lower() == ".zip":
                return p
        return members[0]
