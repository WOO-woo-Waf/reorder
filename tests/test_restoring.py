from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from reorder_engine.domain.models import ArchiveKind
from reorder_engine.services.restoring import (
    ApateRestorer,
    ArchiveSignatureInspector,
    RepeatedApateRestorer,
    RestorationService,
    SuffixVariantBuilder,
)


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
            source = root / "lesson_1.mp4"
            source.write_bytes(b"not really a zip")
            service = RestorationService([SuffixVariantBuilder(ArchiveSignatureInspector())])
            plans = service.variant_plans(source)

            target_names = {plan.target.name for plan in plans}
            self.assertIn("lesson_1.zip", target_names)
            self.assertIn("lesson_1.7z", target_names)
            self.assertFalse((root / "lesson_1.zip").exists())
            self.assertFalse((root / "lesson_1.7z").exists())

            seven_zip_plan = next(plan for plan in plans if plan.target.suffix == ".7z")
            self.assertEqual(seven_zip_plan.preferred_tool, "bandizip")

    def test_repeated_apate_restorer_matches_three_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "FolderThree.mp4"
            source.write_bytes(self._make_disguised(b"PK\x03\x04hello", b"\x00\x00\x00\x00"))
            inspector = ArchiveSignatureInspector()
            restorer = RepeatedApateRestorer(inspector, rounds=3)
            self.assertTrue(restorer.can_handle(source))

    def test_restoration_service_picks_single_apate_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "FolderThree.mp4"
            source.write_bytes(self._make_disguised(b"PK\x03\x04hello", b"\x00\x00\x00\x00"))
            inspector = ArchiveSignatureInspector()
            service = RestorationService(
                [
                    RepeatedApateRestorer(inspector, rounds=3),
                    ApateRestorer(inspector),
                    SuffixVariantBuilder(inspector),
                ],
                inspector=inspector,
            )

            restored = service.restore(source, workspace=root, dry_run=True)

            self.assertEqual(restored, [source])


if __name__ == "__main__":
    unittest.main()
