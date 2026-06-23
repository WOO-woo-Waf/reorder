from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from reorder_engine.domain.models import ArchiveKind
from reorder_engine.services.restoring import (
    ApateRestorer,
    ArchiveSignatureInspector,
    EmbeddedArchiveRestorer,
    RepeatedApateRestorer,
    RestorationService,
    SuffixVariantBuilder,
)


class RestoringTests(unittest.TestCase):
    def _make_disguised(self, original: bytes, mask_head: bytes) -> bytes:
        head_length = len(mask_head)
        return mask_head + original[head_length:] + original[:head_length][::-1] + struct.pack("<I", head_length)

    def _make_embedded_disguised(self, prefix: bytes, archive: bytes) -> tuple[bytes, bytes]:
        fake_original_head = b"not-an-archive".ljust(len(prefix), b"\x00")
        tail = fake_original_head[::-1] + struct.pack("<I", len(prefix))
        return prefix + archive + tail, archive + tail

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

    def test_nested_candidate_stops_on_archive_and_media_mix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "inner.7z"
            archive.write_bytes(b"7z\xbc\xaf'\x1c" + b"x")
            video = root / "payload.mp4"
            video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 1024)
            service = RestorationService([SuffixVariantBuilder(ArchiveSignatureInspector())])

            candidates = service.build_post_extract_candidates(
                root,
                workspace=root / "variants",
                min_archive_bytes=1,
                final_single_bytes=1,
            )

            self.assertEqual(candidates, [])

    def test_nested_candidate_stops_on_archive_and_unknown_file_mix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "inner.7z"
            archive.write_bytes(b"7z\xbc\xaf'\x1c" + b"x")
            payload = root / "payload.bin"
            payload.write_bytes(b"plain payload")
            service = RestorationService([SuffixVariantBuilder(ArchiveSignatureInspector())])

            candidates = service.build_post_extract_candidates(
                root,
                workspace=root / "variants",
                min_archive_bytes=1,
                final_single_bytes=1,
            )

            self.assertEqual(candidates, [])

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

    def test_numbered_tail_suffix_is_trimmed_as_variant_not_direct_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "p.001.pdf"
            source.write_bytes(b"plain")
            service = RestorationService([SuffixVariantBuilder(ArchiveSignatureInspector())])

            probe = service.identify(source)
            plans = service.variant_plans(source)

            self.assertEqual(probe.kind, ArchiveKind.VARIANT)
            self.assertEqual(probe.embedded_archive_name, "p.001")
            self.assertIn(root / "p.001", [plan.target for plan in plans])

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

    def test_embedded_archive_is_identified_after_media_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "payload.pdf"
            prefix = b"%PDF-1.7\nbody\n"
            archive = b"PK\x03\x04" + b"\x14\x00\x00\x00\x00\x00" + b"\x00" * 16 + b"\x07\x00\x00\x00" + b"001.jpg"
            source.write_bytes(self._make_embedded_disguised(prefix, archive)[0])

            probe = ArchiveSignatureInspector().probe_path(source)

            self.assertEqual(probe.kind, ArchiveKind.EMBEDDED)
            self.assertEqual(probe.archive_suffix, ".zip")
            self.assertEqual(probe.embedded_offset, len(prefix))

    def test_normal_pdf_is_not_scanned_for_embedded_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "normal.pdf"
            source.write_bytes(b"%PDF-1.7\nbody\n" + b"PK\x03\x04" + b"\x14\x00\x00\x00\x00\x00" + b"\x00" * 16 + b"\x07\x00\x00\x00" + b"001.jpg")

            probe = ArchiveSignatureInspector().probe_path(source)

            self.assertEqual(probe.kind, ArchiveKind.UNKNOWN)

    def test_embedded_archive_restore_strips_prefix_in_place_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = b"%PDF-1.7\nbody\n"
            archive = b"PK\x03\x04" + b"\x14\x00\x00\x00\x00\x00" + b"\x00" * 16 + b"\x07\x00\x00\x00" + b"001.jpg" + b"payload"
            source = root / "payload.pdf"
            disguised, restored_bytes = self._make_embedded_disguised(prefix, archive)
            source.write_bytes(disguised)
            inspector = ArchiveSignatureInspector()
            service = RestorationService([EmbeddedArchiveRestorer(inspector, chunk_size=8)], inspector=inspector)

            restored, rollbacks = service.restore_with_rollbacks(source, workspace=root)

            self.assertEqual(restored, [source])
            self.assertEqual(source.read_bytes(), restored_bytes)
            self.assertEqual(inspector.probe_path(source).kind, ArchiveKind.ARCHIVE)

            service.rollback_apate(rollbacks)

            self.assertEqual(source.read_bytes(), disguised)

    def test_embedded_archive_scan_matches_across_chunk_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = b"1234567"
            archive = b"PK\x03\x04" + b"\x14\x00\x00\x00\x00\x00" + b"\x00" * 16 + b"\x07\x00\x00\x00" + b"001.jpg"
            source = root / "payload.pdf"
            source.write_bytes(prefix + archive)

            probe = ArchiveSignatureInspector().probe_embedded_archive(source, chunk_size=8, force_scan=True)

            self.assertIsNotNone(probe)
            self.assertEqual(probe.embedded_offset, len(prefix))

    def test_archive_name_without_embedded_marker_does_not_trigger_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "payload.zip"
            source.write_bytes(b"%PDF-1.7\n" + b"PK\x03\x04" + b"\x14\x00\x00\x00\x00\x00" + b"\x00" * 16 + b"\x07\x00\x00\x00" + b"001.jpg")

            probe = ArchiveSignatureInspector().probe_path(source)

            self.assertEqual(probe.kind, ArchiveKind.ARCHIVE)
            self.assertEqual(probe.archive_suffix, ".zip")
            self.assertEqual(probe.reason, "name")

    def test_embedded_archive_marker_takes_precedence_over_archive_name_guess(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "payload.zip"
            prefix = b"%PDF-1.7\n"
            archive = b"PK\x03\x04" + b"\x14\x00\x00\x00\x00\x00" + b"\x00" * 16 + b"\x07\x00\x00\x00" + b"001.jpg"
            source.write_bytes(self._make_embedded_disguised(prefix, archive)[0])

            probe = ArchiveSignatureInspector().probe_path(source)

            self.assertEqual(probe.kind, ArchiveKind.EMBEDDED)
            self.assertEqual(probe.archive_suffix, ".zip")

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
