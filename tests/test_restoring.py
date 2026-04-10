from __future__ import annotations

import tempfile
import unittest
import struct
from pathlib import Path

from reorder_engine.domain.models import ArchiveKind
from reorder_engine.services.restoring import ArchiveSignatureInspector, RepeatedApateRestorer, RestorationService, SuffixVariantBuilder


class RestoringTests(unittest.TestCase):
    def _make_disguised(self, original: bytes, mask_head: bytes) -> bytes:
        head_length = len(mask_head)
        return mask_head + original[head_length:] + original[:head_length][::-1] + struct.pack("<I", head_length)

    def test_inspector_prefers_direct_archive_identification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "pkg.mp4"
            source.write_bytes(b"PK\x03\x04fake zip")

            probe = ArchiveSignatureInspector().probe_path(source)

            self.assertEqual(probe.kind, ArchiveKind.ARCHIVE)
            self.assertEqual(probe.archive_suffix, ".zip")

    def test_inspector_does_not_mark_plain_media_as_apate_without_valid_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "video.mp4"
            source.write_bytes(b"\x00" * 128)

            probe = ArchiveSignatureInspector().probe_path(source)

            self.assertEqual(probe.kind, ArchiveKind.UNKNOWN)

    def test_suffix_variant_builder_plans_suffix_changes_without_copying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "005-04.喜怒不形于色的人_1.mp4"
            source.write_bytes(b"not really a zip")
            service = RestorationService([SuffixVariantBuilder(ArchiveSignatureInspector())])
            plans = service.variant_plans(source)

            target_names = {plan.target.name for plan in plans}
            self.assertIn("005-04.喜怒不形于色的人_1.zip", target_names)
            self.assertFalse((root / "005-04.喜怒不形于色的人_1.zip").exists())

    def test_repeated_apate_restorer_matches_three_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "FolderThree.mp4"
            source.write_bytes(self._make_disguised(b"PK\x03\x04hello", b"\x00\x00\x00\x00"))
            inspector = ArchiveSignatureInspector()
            restorer = RepeatedApateRestorer(inspector, rounds=3)
            self.assertTrue(restorer.can_handle(source))


if __name__ == "__main__":
    unittest.main()
