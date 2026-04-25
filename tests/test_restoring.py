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

    def test_inspector_rejects_apate_layout_when_restored_head_is_not_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "video.mp4"
            source.write_bytes(self._make_disguised(b"NOPEnot an archive", b"\x00\x00\x00\x00"))

            probe = ArchiveSignatureInspector().probe_path(source)

            self.assertEqual(probe.kind, ArchiveKind.UNKNOWN)

    def test_inspector_detects_valid_media_without_overriding_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "payload.mp4"
            video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
            fake_video_archive = root / "payload2.mp4"
            fake_video_archive.write_bytes(b"PK\x03\x04fake zip")
            inspector = ArchiveSignatureInspector()

            self.assertEqual(inspector.detect_media_suffix(video), ".mp4")
            self.assertTrue(inspector.is_valid_final_media(video))
            self.assertFalse(inspector.is_valid_final_media(fake_video_archive))
            self.assertEqual(inspector.probe_path(fake_video_archive).kind, ArchiveKind.ARCHIVE)

    def test_nested_candidate_skips_single_valid_media_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "payload.mp4"
            payload.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 1024)
            service = RestorationService([SuffixVariantBuilder(ArchiveSignatureInspector())])

            candidates = service.build_post_extract_candidates(
                root,
                workspace=root / "variants",
                min_archive_bytes=1,
                final_single_bytes=1,
            )

            self.assertEqual(candidates, [])

    def test_nested_candidate_keeps_large_unknown_media_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "payload.mp4"
            payload.write_bytes(b"not a normal media header")
            service = RestorationService([SuffixVariantBuilder(ArchiveSignatureInspector())])

            candidates = service.build_post_extract_candidates(
                root,
                workspace=root / "variants",
                min_archive_bytes=1,
                final_single_bytes=1,
            )

            self.assertEqual(candidates, [payload])

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

    def test_jpg_and_exe_variants_try_rar_before_zip_and_7z(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "PC11025.jpg"
            source.write_bytes(b"not directly identifiable")
            service = RestorationService([SuffixVariantBuilder(ArchiveSignatureInspector())])
            plans = service.variant_plans(source)

            self.assertEqual([plan.target.suffix for plan in plans[:3]], [".rar", ".zip", ".7z"])

    def test_apate_restore_is_in_place_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            original = b"PK\x03\x04hello"
            disguised = self._make_disguised(original, b"\x00\x00\x00\x00")
            source = root / "FolderThree.mp4"
            source.write_bytes(disguised)
            inspector = ArchiveSignatureInspector()
            service = RestorationService([ApateRestorer(inspector, rounds=3)], inspector=inspector)

            restored, rollbacks = service.restore_with_rollbacks(source, workspace=workspace)

            self.assertEqual(len(restored), 1)
            self.assertEqual(restored[0], source)
            self.assertEqual(source.read_bytes(), original)

            service.rollback_apate(rollbacks)

            self.assertEqual(source.read_bytes(), disguised)

    def test_force_apate_restore_allows_non_archive_restored_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = b"NOPEhello"
            disguised = self._make_disguised(original, b"\x00\x00\x00\x00")
            source = root / "damaged.jpg"
            source.write_bytes(disguised)
            service = RestorationService([SuffixVariantBuilder(ArchiveSignatureInspector())])

            restored, rollbacks = service.force_apate_restore_with_rollbacks(source)

            self.assertEqual(restored, source)
            self.assertEqual(source.read_bytes(), original)
            service.rollback_apate(rollbacks)
            self.assertEqual(source.read_bytes(), disguised)

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
