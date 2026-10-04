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

    def test_discovery_accepts_numbered_part_with_extra_tail_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            part = root / "p.001.pdf"
            part.write_text("x", encoding="utf-8")

            discovered = ArchiveDiscoveryService().discover(root, recursive=False)

            self.assertIn(part, discovered)

    def test_discovery_accepts_disguised_first_zip_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "小初.zip.jpg"
            second = root / "小初.zip.002"
            first.write_bytes(b"first")
            second.write_bytes(b"second")

            discovered = ArchiveDiscoveryService().discover(root, recursive=False)

            self.assertEqual({path.name for path in discovered}, {first.name, second.name})

    def test_discovery_ignores_plain_media_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photo = root / "photo.jpg"
            photo.write_bytes(b"\xff\xd8\xff")

            discovered = ArchiveDiscoveryService().discover(root, recursive=False)

            self.assertNotIn(photo, discovered)


if __name__ == "__main__":
    unittest.main()
