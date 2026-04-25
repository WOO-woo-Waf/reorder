from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reorder_engine.services.cleaning import DefaultGroupingNormalizer
from reorder_engine.services.grouping import DefaultVolumeGroupingStrategy


class GroupingTests(unittest.TestCase):
    def test_grouping_handles_numeric_and_part_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a001 = root / "pkg.001"
            a002 = root / "pkg.002"
            part1 = root / "name.part1.rar"
            part2 = root / "name.part2.rar"
            for path in (a001, a002, part1, part2):
                path.write_text("x", encoding="utf-8")

            groups = DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()).group([a001, a002, part1, part2])

            self.assertEqual(len(groups), 2)
            entries = {group.entry.name for group in groups}
            self.assertIn("pkg.001", entries)
            self.assertIn("name.part1.rar", entries)

    def test_grouping_merges_legacy_rar_and_zip_volume_sets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rar = root / "movie.rar"
            r00 = root / "movie.r00"
            r01 = root / "movie.r01"
            zip_main = root / "album.zip"
            z01 = root / "album.z01"
            z02 = root / "album.z02"
            for path in (rar, r00, r01, zip_main, z01, z02):
                path.write_text("x", encoding="utf-8")

            groups = DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()).group([rar, r00, r01, zip_main, z01, z02])

            self.assertEqual(len(groups), 2)
            by_entry = {group.entry.name: {member.name for member in group.members} for group in groups}
            self.assertEqual(by_entry["movie.rar"], {"movie.rar", "movie.r00", "movie.r01"})
            self.assertEqual(by_entry["album.zip"], {"album.zip", "album.z01", "album.z02"})

    def test_grouping_handles_middle_numbered_7z_volumes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "3616S.001.7z"
            second = root / "3616S.002.7z"
            for path in (first, second):
                path.write_text("x", encoding="utf-8")

            groups = DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()).group([first, second])

            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].entry.name, "3616S.001.7z")
            self.assertEqual({member.name for member in groups[0].members}, {"3616S.001.7z", "3616S.002.7z"})

    def test_grouping_merges_sfx_exe_with_matching_split_volumes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sfx = root / "A1651.7z.exe"
            second = root / "A1651.7z.002"
            for path in (sfx, second):
                path.write_text("x", encoding="utf-8")

            groups = DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()).group([sfx, second])

            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].entry.name, "A1651.7z.exe")
            self.assertEqual({member.name for member in groups[0].members}, {"A1651.7z.exe", "A1651.7z.002"})


if __name__ == "__main__":
    unittest.main()
