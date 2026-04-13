from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reorder_engine.services.flattening import FolderFlattener, deepest_wrapper_dir


class FlatteningTests(unittest.TestCase):
    def test_flatten_moves_conflicts_into_duplicates_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            (root / "b").mkdir()
            (root / "a" / "same.001").write_text("a", encoding="utf-8")
            (root / "b" / "same.001").write_text("b", encoding="utf-8")

            FolderFlattener().flatten(root)

            root_value = (root / "same.001").read_text(encoding="utf-8")
            duplicate_values = [path.read_text(encoding="utf-8") for path in (root / "_duplicates").rglob("same.001")]
            self.assertIn(root_value, {"a", "b"})
            self.assertEqual(sorted(duplicate_values + [root_value]), ["a", "b"])
            self.assertFalse((root / "a").exists())
            self.assertFalse((root / "b").exists())

    def test_deepest_wrapper_dir_returns_leaf_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            leaf = root / "A" / "B" / "C"
            leaf.mkdir(parents=True)
            (leaf / "payload.txt").write_text("ok", encoding="utf-8")

            self.assertEqual(deepest_wrapper_dir(root / "A"), leaf)


if __name__ == "__main__":
    unittest.main()
