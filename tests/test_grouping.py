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


if __name__ == "__main__":
    unittest.main()
