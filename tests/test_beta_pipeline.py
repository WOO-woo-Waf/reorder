from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from reorder_engine.domain.models import ArchiveKind, ArchiveProbe, VolumeSet
from reorder_engine.domain.models import ExtractionResult
from reorder_engine.services.beta_pipeline import BetaFolderPipeline, CandidateAttempt
from reorder_engine.services.config import BetaDeepExtractConfig


def _write_synthetic_split_zip(
    root: Path,
    first_name: str,
    second_name: str,
    *,
    member: str = "hello.txt",
    payload: bytes = b"reorder-synthetic-payload",
) -> tuple[bytes, Path, Path]:
    """Build a real ZIP in memory and split its bytes across two named files."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, payload)
    data = buffer.getvalue()
    midpoint = len(data) // 2
    first = root / first_name
    second = root / second_name
    first.write_bytes(data[:midpoint])
    second.write_bytes(data[midpoint:])
    return data, first, second


class _SyntheticZipRecombiningExtractor:
    """Simulated split-volume extractor, NOT a real 7-Zip/Bandizip run.

    It only concatenates the numbered volumes handed to it and opens the bytes
    with ``zipfile`` so the pipeline routing can be checked end to end.
    """

    def __init__(self, *, succeed: bool) -> None:
        self.succeed = succeed
        self.seen_entries: list[str] = []
        self.verified_members: list[str] = []

    def extract_one(self, request, *, preference="auto", probe=None, dry_run=False):
        _ = (preference, probe, dry_run)
        entry = request.volume_set.entry
        self.seen_entries.append(entry.name)
        if not self.succeed:
            return ExtractionResult(
                volume_set=request.volume_set,
                ok=False,
                tool="synthetic-zip",
                message="simulated extraction failure",
            )
        data = b"".join(path.read_bytes() for path in sorted(request.volume_set.members, key=lambda p: p.name))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            self.verified_members.extend(names)
            request.output_dir.mkdir(parents=True, exist_ok=True)
            for name in names:
                (request.output_dir / name).write_bytes(archive.read(name))
        return ExtractionResult(volume_set=request.volume_set, ok=True, tool="synthetic-zip")


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

    def test_force_apate_attempt_repeats_for_three_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "1190-Three.mp4"
            media.write_bytes(b"plain")
            calls: list[Path] = []

            def force(_self, path: Path, dry_run: bool = False):
                _ = dry_run
                calls.append(path)
                return path, [f"rollback-{len(calls)}"]

            pipeline = self._make_pipeline(root)
            pipeline._restore = type(
                "Restore",
                (),
                {
                    "identify": lambda _self, path: ArchiveProbe(path=path, kind=ArchiveKind.UNKNOWN),
                    "force_apate_restore_with_rollbacks": force,
                },
            )()

            attempt = pipeline._force_apate_attempt_if_useful(media, dry_run=False)

            self.assertIsNotNone(attempt)
            self.assertEqual(len(calls), 3)
            self.assertEqual(attempt.path, media)
            self.assertEqual(attempt.rollbacks, ("rollback-1", "rollback-2", "rollback-3"))

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

    def test_sfx_volume_set_normalizes_exe_to_001_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sfx = root / "A1651.7z.exe"
            second = root / "A1651.7z.002"
            sfx.write_bytes(b"one")
            second.write_bytes(b"two")
            pipeline = self._make_pipeline(root)

            normalized = pipeline._normalize_sfx_volume_set(
                VolumeSet(entry=sfx, members=(sfx, second), group_key="split:a1651.7z"),
                dry_run=False,
            )

            self.assertIsNotNone(normalized)
            normalized_vs, session = normalized
            self.assertEqual(normalized_vs.entry.name, "A1651.7z.001")
            self.assertEqual({path.name for path in normalized_vs.members}, {"A1651.7z.001", "A1651.7z.002"})
            self.assertFalse(sfx.exists())

            session.rollback_best_effort()

            self.assertTrue(sfx.exists())
            self.assertTrue(second.exists())

    def test_disguised_split_suffix_normalizes_to_numbered_7z_volumes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "evil.7z"
            second = root / "evil.7z.zip"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            pipeline = self._make_pipeline(root)

            normalized = pipeline._normalize_disguised_split_suffix_volume_set(
                VolumeSet(entry=first, members=(first, second), group_key="split:evil.7z"),
                dry_run=False,
            )

            self.assertIsNotNone(normalized)
            normalized_vs, session = normalized
            self.assertEqual(normalized_vs.entry.name, "evil.7z.001")
            self.assertEqual({path.name for path in normalized_vs.members}, {"evil.7z.001", "evil.7z.002"})
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())

            session.rollback_best_effort()

            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_disguised_first_zip_volume_is_normalized_with_numbered_second_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "小初.zip.jpg"
            second = root / "小初.zip.002"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            pipeline = self._make_pipeline(root)

            normalized = pipeline._normalize_disguised_split_suffix_volume_set(
                VolumeSet(entry=first, members=(first, second), group_key="split:小初.zip"),
                dry_run=False,
            )

            self.assertIsNotNone(normalized)
            normalized_vs, session = normalized
            self.assertEqual(normalized_vs.entry.name, "小初.zip.001")
            self.assertEqual(
                {path.name for path in normalized_vs.members},
                {"小初.zip.001", "小初.zip.002"},
            )
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())

            session.rollback_best_effort()

            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_disguised_first_volume_without_numbered_sibling_is_not_renamed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "小初.zip.jpg"
            first.write_bytes(b"one")
            pipeline = self._make_pipeline(root)

            normalized = pipeline._normalize_disguised_split_suffix_volume_set(
                VolumeSet(entry=first, members=(first,), group_key="split:小初.zip"),
                dry_run=False,
            )

            self.assertIsNone(normalized)
            self.assertTrue(first.exists())

    def test_numbered_tail_volume_set_trims_disguised_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "p.001.pdf"
            second = root / "p.002"
            fourth = root / "p.004"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            fourth.write_bytes(b"four")
            pipeline = self._make_pipeline(root)

            normalized = pipeline._normalize_numbered_tail_volume_set(
                VolumeSet(entry=first, members=(first, second, fourth), group_key="num:p"),
                dry_run=False,
            )

            self.assertIsNotNone(normalized)
            normalized_vs, session = normalized
            self.assertEqual(normalized_vs.entry.name, "p.001")
            self.assertEqual({path.name for path in normalized_vs.members}, {"p.001", "p.002", "p.004"})
            self.assertFalse(first.exists())

            session.rollback_best_effort()

            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertTrue(fourth.exists())

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

    def test_run_extract_attempt_logs_method_password_and_output_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "payload.7z"
            archive.write_bytes(b"7z")
            vs = VolumeSet(entry=archive, members=(archive,), group_key="g")
            messages: list[str] = []

            class _Extractor:
                def extract_one(self, request, *, preference="auto", probe=None, dry_run=False):
                    return ExtractionResult(volume_set=request.volume_set, ok=True, tool="fake", password="pw")

            pipeline = self._make_pipeline(root)
            pipeline._emit = messages.append
            pipeline._extractor = _Extractor()
            pipeline._log_passwords = True

            pipeline._run_extract_attempt(
                vs,
                probe=ArchiveProbe(path=archive, kind=ArchiveKind.ARCHIVE, archive_suffix=".7z"),
                output_dir=root / "out",
                dry_run=False,
                prefix="TEST",
                preferred_password="secret",
                method="embedded-archive-strip-prefix",
            )

            joined = "\n".join(messages)
            self.assertIn("TEST-TRY: entry=payload.7z method=embedded-archive-strip-prefix", joined)
            self.assertIn("preferred_password=secret", joined)
            self.assertIn("TEST[OK] entry=payload.7z method=embedded-archive-strip-prefix tool=fake password=pw", joined)
            self.assertIn(str(root / "out"), joined)

    def test_unknown_candidate_without_restore_logs_as_direct_method(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "payload.bin"
            payload.write_bytes(b"plain")
            pipeline = self._make_pipeline(root)
            pipeline._restore = type(
                "Restore",
                (),
                {"restore_with_rollbacks": lambda _self, path, workspace=None, dry_run=False: ([path], [])},
            )()

            attempts = pipeline._candidate_chain(
                payload,
                probe=ArchiveProbe(path=payload, kind=ArchiveKind.UNKNOWN),
                workspace=root / "workspace",
                dry_run=False,
            )

            self.assertEqual([attempt.method for attempt in attempts], ["direct"])

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

    def test_missing_top_level_split_zip_fragments_are_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "payload.z01"
            second = root / "payload.z02"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            pipeline = self._make_pipeline(root)

            moved = pipeline._defer_missing_volume_set(
                VolumeSet(entry=first, members=(first, second), group_key="split:payload.zip"),
                dry_run=False,
            )

            self.assertEqual(
                {path.relative_to(root).as_posix() for path in moved},
                {
                    "deferred_volumes/payload.zip/payload.z01",
                    "deferred_volumes/payload.zip/payload.z02",
                },
            )
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())

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


    def test_disguised_first_volume_skips_when_numbered_target_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "小初.zip.jpg"
            second = root / "小初.zip.002"
            existing = root / "小初.zip.001"
            first.write_bytes(b"disguised-first")
            second.write_bytes(b"second-part")
            existing.write_bytes(b"unrelated-existing-001")
            pipeline = self._make_pipeline(root)

            normalized = pipeline._normalize_disguised_split_suffix_volume_set(
                VolumeSet(entry=first, members=(first, second), group_key="split:小初.zip"),
                dry_run=False,
            )

            self.assertIsNone(normalized)
            self.assertEqual(first.read_bytes(), b"disguised-first")
            self.assertEqual(second.read_bytes(), b"second-part")
            self.assertEqual(existing.read_bytes(), b"unrelated-existing-001")

    def test_disguised_first_volume_dry_run_reports_target_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "小初.zip.jpg"
            second = root / "小初.zip.002"
            first.write_bytes(b"disguised-first")
            second.write_bytes(b"second-part")
            pipeline = self._make_pipeline(root)

            normalized = pipeline._normalize_disguised_split_suffix_volume_set(
                VolumeSet(entry=first, members=(first, second), group_key="split:小初.zip"),
                dry_run=True,
            )

            self.assertIsNotNone(normalized)
            normalized_vs, _session = normalized
            self.assertEqual(normalized_vs.entry.name, "小初.zip.001")
            self.assertEqual({path.name for path in normalized_vs.members}, {"小初.zip.001", "小初.zip.002"})
            # dry-run only reports the intended rename; the disk stays untouched.
            self.assertTrue(first.exists())
            self.assertEqual(first.read_bytes(), b"disguised-first")
            self.assertEqual(second.read_bytes(), b"second-part")
            self.assertFalse((root / "小初.zip.001").exists())

    def test_extract_volume_set_first_success_recombines_disguised_zip_via_001(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, first, second = _write_synthetic_split_zip(root, "小初.zip.jpg", "小初.zip.002")
            pipeline = self._make_pipeline(root)
            pipeline._restore = type(
                "Restore",
                (),
                {"identify": lambda _self, path: ArchiveProbe(path=path, kind=ArchiveKind.ARCHIVE)},
            )()
            extractor = _SyntheticZipRecombiningExtractor(succeed=True)
            pipeline._extractor = extractor

            result, out_dir = pipeline._extract_volume_set_first_success(
                VolumeSet(entry=first, members=(first, second), group_key="split:小初.zip"),
                root / "L1",
                dry_run=False,
            )

            self.assertTrue(result.ok)
            self.assertEqual(extractor.seen_entries, ["小初.zip.001"])
            self.assertIn("hello.txt", extractor.verified_members)
            renamed = root / "小初.zip.001"
            self.assertTrue(renamed.exists())
            self.assertFalse(first.exists())
            self.assertEqual(renamed.read_bytes() + second.read_bytes(), data)
            self.assertIsNotNone(out_dir)
            self.assertEqual((out_dir / "hello.txt").read_bytes(), b"reorder-synthetic-payload")

    def test_extract_volume_set_first_success_rolls_back_disguised_rename_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, first, second = _write_synthetic_split_zip(root, "小初.zip.jpg", "小初.zip.002")
            first_bytes = first.read_bytes()
            second_bytes = second.read_bytes()
            pipeline = self._make_pipeline(root)
            pipeline._restore = type(
                "Restore",
                (),
                {"identify": lambda _self, path: ArchiveProbe(path=path, kind=ArchiveKind.ARCHIVE)},
            )()
            extractor = _SyntheticZipRecombiningExtractor(succeed=False)
            pipeline._extractor = extractor

            result, out_dir = pipeline._extract_volume_set_first_success(
                VolumeSet(entry=first, members=(first, second), group_key="split:小初.zip"),
                root / "L1",
                dry_run=False,
            )

            self.assertFalse(result.ok)
            self.assertIsNone(out_dir)
            self.assertEqual(extractor.seen_entries, ["小初.zip.001"])
            self.assertFalse((root / "小初.zip.001").exists())
            self.assertTrue(first.exists())
            self.assertEqual(first.read_bytes(), first_bytes)
            self.assertEqual(second.read_bytes(), second_bytes)
            self.assertEqual(first.read_bytes() + second.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
