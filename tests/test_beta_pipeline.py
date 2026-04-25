from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reorder_engine.domain.models import ArchiveKind, ArchiveProbe, VolumeSet
from reorder_engine.domain.models import ExtractionResult
from reorder_engine.services.beta_pipeline import BetaFolderPipeline, CandidateAttempt
from reorder_engine.services.config import BetaDeepExtractConfig


class BetaPipelineTests(unittest.TestCase):
    def _make_pipeline(self, root: Path) -> BetaFolderPipeline:
        return BetaFolderPipeline(
            folder=root,
            decrypt_service=object(),
            restore_service=object(),
            grouper=object(),
            extractor=object(),
            passwords=(),
            emit=lambda _message: None,
        )

    def test_is_final_output_does_not_stop_on_single_video_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "payload.mp4").write_bytes(b"\x00\x00\x00\x20ftypisom")

            result = self._make_pipeline(root)._is_final_output(root)

            self.assertIsNone(result)

    def test_is_final_output_keeps_structural_many_files_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(80):
                (root / f"file_{index}.bin").write_bytes(b"x")

            result = self._make_pipeline(root)._is_final_output(root)

            self.assertEqual(result, "many-files")

    def test_failure_category_splits_password_from_unknown_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unknown = root / "payload.bin"
            unknown.write_bytes(b"plain")
            pipeline = self._make_pipeline(root)
            pipeline._restore = type(
                "Restore",
                (),
                {"identify": lambda _self, path: ArchiveProbe(path=path, kind=ArchiveKind.UNKNOWN)},
            )()

            password_result = type("Result", (), {"message": "Wrong password"})()
            unknown_result = type("Result", (), {"message": "Can not open the file as archive"})()

            self.assertEqual(pipeline._failure_category(password_result, unknown), "password_error")
            self.assertEqual(pipeline._failure_category(unknown_result, unknown), "unknown_type")

    def test_password_category_matches_tool_log_wrong_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "payload.zip"
            archive.write_bytes(b"plain")
            pipeline = self._make_pipeline(root)

            result = type("Result", (), {"message": "ERROR: Wrong password : file.jpg"})()

            self.assertEqual(pipeline._failure_category(result, archive), "password_error")

    def test_force_apate_attempt_is_limited_to_unknown_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "payload.jpg"
            media.write_bytes(b"plain")
            pipeline = self._make_pipeline(root)
            pipeline._restore = type(
                "Restore",
                (),
                {
                    "identify": lambda _self, path: ArchiveProbe(path=path, kind=ArchiveKind.UNKNOWN),
                    "force_apate_restore_with_rollbacks": lambda _self, path, dry_run=False: (path, ["rollback"]),
                },
            )()

            attempt = pipeline._force_apate_attempt_if_useful(media, dry_run=False)

            self.assertIsNotNone(attempt)
            self.assertEqual(attempt.path, media)
            self.assertEqual(attempt.rollbacks, ("rollback",))

    def test_package_name_ignores_middle_numbered_volume_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            self.assertEqual(self._make_pipeline(root)._package_name("3616S.001.7z"), "3616S")

    def test_middle_numbered_volume_set_normalizes_to_001_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "3616S.001.7z"
            second = root / "3616S.002.7z"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            pipeline = self._make_pipeline(root)

            normalized = pipeline._normalize_middle_numbered_volume_set(
                VolumeSet(entry=first, members=(first, second), group_key="split-midnum:3616s.7z"),
                dry_run=False,
            )

            self.assertIsNotNone(normalized)
            normalized_vs, session = normalized
            self.assertEqual(normalized_vs.entry.name, "3616S.7z.001")
            self.assertEqual({path.name for path in normalized_vs.members}, {"3616S.7z.001", "3616S.7z.002"})
            self.assertFalse(first.exists())

            session.rollback_best_effort()

            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_partial_single_output_moves_to_error_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "intermediate" / "pkg" / "L1" / "attempt"
            source_dir.mkdir(parents=True)
            payload = source_dir / "NO-3616.7zz"
            payload.write_bytes(b"inner")
            pipeline = self._make_pipeline(root)

            moved = pipeline._move_partial_outputs_to_error(
                source_dir,
                root / "error_files" / "password_error",
                package_name="3616S",
                dry_run=False,
            )

            self.assertEqual(moved, root / "error_files" / "password_error" / "NO-3616.7zz")
            self.assertTrue(moved.exists())
            self.assertFalse(payload.exists())

    def test_run_extract_attempt_passes_preferred_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "payload.7z"
            archive.write_bytes(b"7z")
            vs = VolumeSet(entry=archive, members=(archive,), group_key="g")

            class _Extractor:
                seen = None

                def extract_one(self, request, *, preference="auto", probe=None, dry_run=False):
                    self.seen = request.preferred_password
                    return ExtractionResult(volume_set=request.volume_set, ok=False, tool="fake")

            extractor = _Extractor()
            pipeline = self._make_pipeline(root)
            pipeline._extractor = extractor

            pipeline._run_extract_attempt(
                vs,
                probe=ArchiveProbe(path=archive, kind=ArchiveKind.ARCHIVE),
                output_dir=root / "out",
                dry_run=False,
                prefix="TEST",
                preferred_password="secret",
            )

            self.assertEqual(extractor.seen, "secret")

    def test_defer_volume_fragment_moves_to_group_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fragment = root / "payload.zip.001"
            fragment.write_bytes(b"PK\x03\x04")
            pipeline = self._make_pipeline(root)

            moved = pipeline._defer_volume_fragment(fragment, dry_run=False)

            self.assertEqual(moved, root / "deferred_volumes" / "payload.zip" / "payload.zip.001")
            self.assertTrue(moved.exists())
            self.assertFalse(fragment.exists())

    def test_continue_after_extract_defers_nested_volume_fragment_without_extracting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            start_dir = root / "intermediate" / "pkg" / "L1"
            start_dir.mkdir(parents=True)
            fragment = start_dir / "payload.zip.001"
            fragment.write_bytes(b"PK\x03\x04")
            pipeline = self._make_pipeline(root)
            pipeline._deep = BetaDeepExtractConfig(enabled=True, max_depth=2, min_archive_mb=1, final_single_mb=1)
            pipeline._nested_candidates = lambda *args, **kwargs: [CandidateAttempt(fragment)]

            class _Extractor:
                def extract_one(self, *args, **kwargs):
                    raise AssertionError("deferred split volumes should not be extracted in this pass")

            pipeline._extractor = _Extractor()

            ok, final_dir, message = pipeline._continue_after_extract(
                package_name="pkg",
                package_root=root / "intermediate" / "pkg",
                start_dir=start_dir,
                final_root=root / "final",
                dry_run=False,
            )

            self.assertTrue(ok)
            self.assertIsNone(final_dir)
            self.assertIn("deferred-volume-fragments", message or "")
            self.assertTrue((root / "deferred_volumes" / "payload.zip" / "payload.zip.001").exists())


if __name__ == "__main__":
    unittest.main()
