from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reorder_engine.services.discovery import ArchiveDiscoveryService


class DiscoveryTests(unittest.TestCase):
    def test_discovery_accepts_high_numbered_split_parts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = [
                root / "pack.004",
                root / "pack.127",
                root / "pack.7z.004",
                root / "pack.zip.010",
                root / "pack.r00",
                root / "pack.z01",
            ]
            for path in paths:
                path.write_text("x", encoding="utf-8")

            discovered = ArchiveDiscoveryService().discover(root, recursive=False)

            self.assertEqual({path.name for path in discovered}, {path.name for path in paths})


if __name__ == "__main__":
    unittest.main()
