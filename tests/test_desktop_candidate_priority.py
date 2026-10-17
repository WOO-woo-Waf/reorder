from __future__ import annotations

import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from reorder_engine.application.errors import EngineError, ProcessingCancelled
from reorder_engine.domain.models import ArchiveKind, ExtractionResult, RenameVariantPlan
from reorder_engine.services.beta_pipeline import CandidateAttempt, PARTIAL_WARNING, BetaFolderPipeline
from reorder_engine.services.cleaning import DefaultGroupingNormalizer
from reorder_engine.services.config import BetaDeepExtractConfig
from reorder_engine.services.decrypting import DecryptionService, PassthroughDecryptor
from reorder_engine.services.grouping import DefaultVolumeGroupingStrategy
from reorder_engine.services.restore_ab import RestoreABRestorer
from reorder_engine.services.restoring import (
    ApateRestorer,
    ArchiveSignatureInspector,
    EmbeddedArchiveRestorer,
    RestorationService,
    SuffixVariantBuilder,
)

MASK = bytes([0, 0, 0, 24]) + b"ftypmp42mp41" + bytes(52)


def zip_bytes(*members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, payload in (members or (("payload.txt", b"synthetic payload"),)):
            archive.writestr(name, payload)
    return buffer.getvalue()


def apate_disguise(original, mask=MASK):
    length = len(mask)
    return mask + original[length:] + original[:length][::-1] + struct.pack("<I", length)


def make_service():
    inspector = ArchiveSignatureInspector()
    return RestorationService(
        [
            RestoreABRestorer(),
            ApateRestorer(inspector, rounds=3),
            EmbeddedArchiveRestorer(inspector),
            SuffixVariantBuilder(inspector),
        ],
        inspector=inspector,
    )


class _RecordingRestore:
    """Delegate to a real RestorationService while recording restore calls."""

    def __init__(self, delegate):
        self._delegate = delegate
        self.restore_calls = []
        self.force_calls = []

    def identify(self, path):
        return self._delegate.identify(path)

    def variant_plans(self, path):
        return self._delegate.variant_plans(path)

    def build_post_extract_candidates(self, *args, **kwargs):
        return self._delegate.build_post_extract_candidates(*args, **kwargs)

    def last_restorer_name(self):
        getter = getattr(self._delegate, "last_restorer_name", None)
        return getter() if callable(getter) else None

    def restore_with_rollbacks(self, path, *, workspace=None, dry_run=False, preferred_kind=None):
        self.restore_calls.append((Path(path), preferred_kind))
        return self._delegate.restore_with_rollbacks(
            path, workspace=workspace, dry_run=dry_run, preferred_kind=preferred_kind
        )

    def force_apate_restore_with_rollbacks(self, path, *, dry_run=False):
        self.force_calls.append(Path(path))
        return self._delegate.force_apate_restore_with_rollbacks(path, dry_run=dry_run)

    def rollback_apate(self, records, *, dry_run=False):
        return self._delegate.rollback_apate(records, dry_run=dry_run)


class _ZipExtractor:
    """Join the current volume set and read it only when it is a real ZIP."""

    def __init__(self, *, partial=False, first_zero_byte=False):
        self.partial = partial
        self.first_zero_byte = first_zero_byte
        self.entries = []
        self.methods = []
        self.calls = 0

    def is_available(self):
        return True

    def extract_one(self, request, *, preference="auto", probe=None, dry_run=False):
        self.calls += 1
        self.entries.append(request.volume_set.entry)
        self.methods.append(request.method)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        if self.first_zero_byte and self.calls == 1:
            (request.output_dir / "empty.bin").write_bytes(b"")
            return ExtractionResult(request.volume_set, ok=False, tool="synthetic", message="zero-byte output")
        try:
            data = b"".join(path.read_bytes() for path in sorted(request.volume_set.members, key=lambda p: p.name))
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                items = {name: archive.read(name) for name in archive.namelist()}
        except Exception as exc:
            return ExtractionResult(request.volume_set, ok=False, tool="synthetic", message="not a zip: " + str(exc))
        for name, blob in items.items():
            (request.output_dir / name).write_bytes(blob)
        return ExtractionResult(request.volume_set, ok=True, tool="synthetic", partial_output=self.partial)


class _InnerRestorePartialExtractor:
    def extract_one(self, request, *, preference="auto", probe=None, dry_run=False):
        entry = request.volume_set.entry
        if entry.name == "outer.zip":
            data = entry.read_bytes()
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                archive.extractall(request.output_dir)
            return ExtractionResult(request.volume_set, ok=True, tool="synthetic")
        if entry.parent.name == "restore_ab":
            request.output_dir.mkdir(parents=True, exist_ok=True)
            (request.output_dir / "recovered.txt").write_bytes(b"partial recovered file")
            return ExtractionResult(
                request.volume_set,
                ok=False,
                tool="synthetic",
                message="wrong password with partial files",
                partial_output=True,
            )
        return ExtractionResult(request.volume_set, ok=False, tool="synthetic", message="not an archive")


class _PartialThenVariantHardStopExtractor:
    def __init__(self):
        self.calls = 0

    def extract_one(self, request, *, preference="auto", probe=None, dry_run=False):
        self.calls += 1
        if self.calls == 1:
            request.output_dir.mkdir(parents=True, exist_ok=True)
            (request.output_dir / "partial.txt").write_bytes(b"retained partial")
            return ExtractionResult(
                request.volume_set,
                ok=False,
                tool="synthetic",
                message="direct pass incomplete",
                partial_output=True,
            )
        return ExtractionResult(
            request.volume_set,
            ok=False,
            tool="synthetic",
            message="variant permission denied",
            exit_code=7,
        )


def _pipeline(root, extractor, restore=None, *, deep=None):
    return BetaFolderPipeline(
        folder=root,
        decrypt_service=DecryptionService([PassthroughDecryptor()]),
        restore_service=restore or make_service(),
        grouper=DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()),
        extractor=extractor,
        passwords=(),
        emit=lambda _message: None,
        deep_extract=deep,
    )


def _disguised_pair():
    data = zip_bytes(("a.txt", b"a" * 1000), ("b.txt", b"b" * 1000))
    midpoint = len(data) // 2
    return data, data[:midpoint], data[midpoint:]


class CandidatePriorityTests(unittest.TestCase):
    def test_direct_partial_then_variant_hard_error_routes_partial_and_keeps_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "pkg.zip.jpg"
            source.write_bytes(zip_bytes())
            restore = _RecordingRestore(make_service())
            restore.variant_plans = lambda path: [
                RenameVariantPlan(path, path.with_suffix(""), "synthetic-variant")
            ]
            extractor = _PartialThenVariantHardStopExtractor()

            result = _pipeline(root, extractor, restore=restore).run()

            self.assertEqual((result.ok_count, result.fail_count), (0, 1))
            self.assertEqual(result.packages[0].state, "partial")
            self.assertEqual(
                result.packages[0].message,
                "未完成：variant permission denied",
            )
            self.assertEqual(
                [path.read_bytes() for path in (root / "incomplete/outputs").rglob("partial.txt")],
                [b"retained partial"],
            )
            self.assertTrue((root / "incomplete/archives" / source.name).exists())
            self.assertFalse(any(path.is_file() for path in (root / "success/archives").rglob("*")))
            self.assertEqual(restore.restore_calls, [], "a hard variant error must not escalate to restore")
            self.assertEqual(extractor.calls, 2)

    def test_entry_candidates_yield_only_the_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "X.zip.jpg"
            source.write_bytes(apate_disguise(zip_bytes(("a.txt", b"a" * 1000), ("b.txt", b"b" * 1000))))
            restore = _RecordingRestore(make_service())
            pipeline = _pipeline(root, _ZipExtractor(), restore=restore)
            vs = DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()).group([source])[0]

            candidates = pipeline._entry_candidates(vs, root / "ws", dry_run=True)

            self.assertEqual([attempt.path for attempt in candidates], [source])
            self.assertEqual(restore.restore_calls, [], "candidate preparation must not call restore")

    def test_direct_read_precedes_in_place_restore_for_apate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "X.zip.jpg"
            source.write_bytes(apate_disguise(zip_bytes(("a.txt", b"a" * 1000), ("b.txt", b"b" * 1000))))
            restore = _RecordingRestore(make_service())
            extractor = _ZipExtractor()
            pipeline = _pipeline(root, extractor, restore=restore)

            result = pipeline.run()

            self.assertEqual(result.packages[0].state, "succeeded")
            self.assertEqual(extractor.entries[0], source, "the direct read targets the original path")
            self.assertEqual(len(restore.restore_calls), 1, "restore runs once, after the read failed")
            self.assertEqual(restore.restore_calls[0][1], ArchiveKind.APATE)

    def test_apate_is_preferred_over_restoreab_on_a_single_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "X.zip.jpg"
            source.write_bytes(apate_disguise(zip_bytes(("a.txt", b"a" * 1000), ("b.txt", b"b" * 1000))))
            self.assertTrue(RestoreABRestorer().can_handle(source), "polyglot scanner also claims this file")
            service = make_service()
            extractor = _ZipExtractor()
            pipeline = _pipeline(root, extractor, restore=service)

            result = pipeline.run()

            self.assertEqual(result.packages[0].state, "succeeded")
            self.assertEqual(service.last_restorer_name(), "ApateRestorer")
            self.assertTrue(list((root / "final").rglob("a.txt")))

    def test_restoreab_in_place_label_requires_an_actual_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unknown = root / "cover.jpg"
            unknown.write_bytes(bytes([255, 216, 255]) + b"cover" + bytes(32))
            pipeline = _pipeline(root, _ZipExtractor())
            probe = ArchiveSignatureInspector().probe_path(unknown)
            self.assertEqual(probe.kind, ArchiveKind.UNKNOWN)

            self.assertEqual(
                pipeline._restore_method(probe, (), restorer_name="RestoreABRestorer", in_place=True),
                "restore-ab-in-place",
            )
            self.assertEqual(
                pipeline._restore_method(probe, (), restorer_name="RestoreABRestorer", in_place=False),
                "direct",
            )
            self.assertEqual(
                pipeline._restore_method(probe, (), restorer_name="ApateRestorer", in_place=True),
                "direct",
            )
            # The RestoreAB restorer’s own in-place marker wins when present, but
            # only for an actual in-place edit.
            self.assertEqual(
                pipeline._restore_method(
                    probe, (), restorer_name=None, in_place=True, last_method="restore-ab-in-place"
                ),
                "restore-ab-in-place",
            )
            self.assertEqual(
                pipeline._restore_method(
                    probe, (), restorer_name=None, in_place=False, last_method="restore-ab-in-place"
                ),
                "direct",
            )

    def test_partial_output_exhausts_modes_and_routes_to_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "pkg.zip"
            source.write_bytes(zip_bytes(("inner.zip", zip_bytes(("inner.txt", b"inner")))))
            extractor = _ZipExtractor(partial=True)
            deep = BetaDeepExtractConfig(enabled=True, max_depth=4, min_archive_mb=1, final_single_mb=1)
            pipeline = _pipeline(root, extractor, deep=deep)

            result = pipeline.run()

            self.assertEqual(result.packages[0].state, "partial")
            self.assertEqual(result.ok_count, 0)
            self.assertEqual(result.fail_count, 1)
            self.assertGreaterEqual(len(extractor.entries), 1, "partial output should be retained after applicable modes")
            self.assertTrue(list((root / "incomplete/outputs").rglob("inner.zip")))
            self.assertFalse(list((root / "final").rglob("inner.zip")))

    def test_inner_restore_partial_is_routed_to_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inner_zip = zip_bytes(("payload.txt", b"nested"))
            inner_pdf = b"%PDF-1.7\ncover\n%%EOF\n" + inner_zip
            source = root / "outer.zip"
            source.write_bytes(zip_bytes(("merged.pdf", inner_pdf)))
            deep = BetaDeepExtractConfig(enabled=True, max_depth=3, min_archive_mb=1, final_single_mb=1)
            pipeline = _pipeline(root, _InnerRestorePartialExtractor(), deep=deep)

            def nested_candidate(folder, workspace, **kwargs):
                candidate = folder / "merged.pdf"
                return [CandidateAttempt(candidate, method="direct", source=candidate)]

            pipeline._nested_candidates = nested_candidate
            result = pipeline.run()

            self.assertEqual((result.ok_count, result.fail_count), (0, 1))
            self.assertEqual(result.packages[0].state, "partial")
            self.assertEqual(
                [path.read_bytes() for path in (root / "incomplete/outputs").rglob("recovered.txt")],
                [b"partial recovered file"],
            )
            self.assertEqual(
                [path.name for path in (root / "incomplete/archives").iterdir() if path.is_file()],
                ["outer.zip"],
            )

    def test_zero_byte_output_is_not_a_usable_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "X.zip.jpg"
            source.write_bytes(apate_disguise(zip_bytes(("a.txt", b"a" * 1000), ("b.txt", b"b" * 1000))))
            restore = _RecordingRestore(make_service())
            extractor = _ZipExtractor(first_zero_byte=True)
            pipeline = _pipeline(root, extractor, restore=restore)

            result = pipeline.run()

            self.assertEqual(result.packages[0].state, "succeeded")
            self.assertEqual(len(restore.restore_calls), 1, "zero-byte output must not stop the restore fallback")
            self.assertEqual(list((root / "final").rglob("empty.bin")), [])

    def test_multi_volume_reads_before_revealing_the_disguise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, first, second = _disguised_pair()
            (root / "read.zip.001").write_bytes(first)
            (root / "read.zip.002").write_bytes(second)
            restore = _RecordingRestore(make_service())
            pipeline = _pipeline(root, _ZipExtractor(), restore=restore)

            result = pipeline.run()

            self.assertEqual(result.packages[0].state, "succeeded")
            self.assertEqual(restore.restore_calls, [], "a readable set must not be revealed")

    def test_multi_volume_reveals_only_after_a_failed_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, first, second = _disguised_pair()
            (root / "X.zip.001.mp4").write_bytes(apate_disguise(first))
            (root / "X.zip.002.mp4").write_bytes(apate_disguise(second))
            restore = _RecordingRestore(make_service())
            extractor = _ZipExtractor()
            pipeline = _pipeline(root, extractor, restore=restore)

            result = pipeline.run()

            self.assertEqual(result.packages[0].state, "succeeded")
            self.assertEqual(len(restore.restore_calls), 1, "reveal happens once for the entry")
            self.assertEqual(restore.restore_calls[0][1], ArchiveKind.APATE, "entry reveal uses preferred_kind")
            self.assertGreaterEqual(len(extractor.entries), 2, "a read is attempted before the reveal")
            self.assertTrue(list((root / "final").rglob("a.txt")))

    def test_unknown_media_and_no_suffix_try_zip_before_7z(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "clip.mp4"
            media.write_bytes(b"not a real archive")
            nosuffix = root / "blob"
            nosuffix.write_bytes(b"not a real archive")
            jpg = root / "PC11025.jpg"
            jpg.write_bytes(b"not a real archive")
            builder = SuffixVariantBuilder(ArchiveSignatureInspector())

            self.assertEqual([p.target.suffix for p in builder.plan_variants(media)][:2], [".zip", ".7z"])
            self.assertEqual([p.target.suffix for p in builder.plan_variants(nosuffix)][:2], [".zip", ".7z"])
            self.assertEqual([p.target.suffix for p in builder.plan_variants(jpg)][:3], [".rar", ".zip", ".7z"])


class _RaisingExtractor:
    """Fail the first extraction with a typed exception, optionally after output."""

    def __init__(self, exc, *, write_output=False):
        self.exc = exc
        self.write_output = write_output
        self.entries = []

    def is_available(self):
        return True

    def extract_one(self, request, *, preference="auto", probe=None, dry_run=False):
        self.entries.append(request.volume_set.entry)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        if self.write_output:
            (request.output_dir / "junk.bin").write_bytes(b"partial bytes")
        raise self.exc


class ExtractionAbortPropagationTests(unittest.TestCase):
    """Cancellation and engine error codes must abort the job, not retry."""

    def _disguised_source(self, root):
        source = root / "X.zip.jpg"
        source.write_bytes(apate_disguise(zip_bytes(("a.txt", b"a" * 1000), ("b.txt", b"b" * 1000))))
        return source

    def test_cancellation_propagates_and_stops_restore_and_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._disguised_source(root)
            restore = _RecordingRestore(make_service())
            pipeline = _pipeline(root, _RaisingExtractor(ProcessingCancelled()), restore=restore)

            with self.assertRaises(ProcessingCancelled):
                pipeline.run()

            self.assertEqual(restore.restore_calls, [], "cancel must not trigger a restore")
            self.assertEqual(
                [p.name for p in root.iterdir() if p.is_file()], ["X.zip.jpg"],
                "cancel must not leave renamed/derived variants",
            )

    def test_engine_error_codes_propagate_unchanged(self):
        for code in ("UNSAFE_ARCHIVE", "OUTPUT_LIMIT", "DISK_FULL", "TOOL_TIMEOUT", "TOOL_START_FAILED"):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._disguised_source(root)
                restore = _RecordingRestore(make_service())
                error = EngineError(code, f"synthetic {code}")
                pipeline = _pipeline(root, _RaisingExtractor(error), restore=restore)

                with self.assertRaises(EngineError) as ctx:
                    pipeline.run()

                self.assertEqual(ctx.exception.code, code)
                self.assertEqual(restore.restore_calls, [], f"{code} must not trigger a restore")
                self.assertEqual(
                    [p.name for p in root.iterdir() if p.is_file()], ["X.zip.jpg"],
                    f"{code} must not leave renamed/derived variants",
                )

    def test_engine_error_with_output_is_not_downgraded_to_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._disguised_source(root)
            restore = _RecordingRestore(make_service())
            error = EngineError("UNSAFE_ARCHIVE", "unsafe member despite output")
            pipeline = _pipeline(root, _RaisingExtractor(error, write_output=True), restore=restore)

            with self.assertRaises(EngineError):
                pipeline.run()
            self.assertEqual(restore.restore_calls, [], "a typed abort must not be retried as a partial result")

    def test_first_candidate_attempt_is_labeled_direct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._disguised_source(root)
            pipeline = _pipeline(root, _ZipExtractor())

            vs = DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()).group([source])[0]
            candidates = pipeline._entry_candidates(vs, root / "ws", dry_run=True)

            self.assertEqual([attempt.method for attempt in candidates], ["direct"])


PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"coverbytes" * 4


def zip64_bytes():
    """A ZIP whose EOCD central offset is the ZIP64 sentinel (unrepairable in place)."""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("a.txt", b"a" * 1000)
    data = bytearray(buf.getvalue())
    eocd = data.rfind(b"PK\x05\x06")
    struct.pack_into("<I", data, eocd + 16, 0xFFFFFFFF)
    return bytes(data)


def make_inplace_service():
    inspector = ArchiveSignatureInspector()
    return RestorationService(
        [
            RestoreABRestorer(in_place=True),
            ApateRestorer(inspector, rounds=3),
            EmbeddedArchiveRestorer(inspector),
            SuffixVariantBuilder(inspector),
        ],
        inspector=inspector,
    )


class _FailingExtractor:
    """Return one no-output failure result; no tool or output is produced."""

    def __init__(self, message, *, exit_code=None):
        self.message = message
        self.exit_code = exit_code
        self.entries = []

    def is_available(self):
        return True

    def extract_one(self, request, *, preference="auto", probe=None, dry_run=False):
        request.output_dir.mkdir(parents=True, exist_ok=True)
        self.entries.append(request.volume_set.entry)
        return ExtractionResult(
            request.volume_set, ok=False, tool="synthetic",
            exit_code=self.exit_code, message=self.message,
        )


class _RaisingRestore(_RecordingRestore):
    def __init__(self, delegate, exc):
        super().__init__(delegate)
        self._exc = exc

    def restore_with_rollbacks(self, path, *, workspace=None, dry_run=False, preferred_kind=None):
        self.restore_calls.append((Path(path), preferred_kind))
        raise self._exc


class PipelineReviewFixTests(unittest.TestCase):
    """HOSHI-033-PIPELINE-REVIEW-FIX R1/R2."""

    def _cover(self, root, name="cover.png"):
        source = root / name
        source.write_bytes(PNG_HEAD + zip_bytes(("a.txt", b"a" * 1000)))
        return source

    def _routed_bytes(self, root, name):
        matches = [p for p in Path(root).rglob(name) if p.is_file()]
        self.assertTrue(matches, f"{name} not found after run")
        return matches[0].read_bytes()

    def test_serious_no_output_failures_do_not_restore_or_rename(self):
        cases = (
            ("permission-denied", {"message": "ERROR: Can not write to output. Access is denied."}),
            ("disk-full", {"message": "ERROR: No space left on device"}),
            ("missing-volume", {"message": "ERROR: Missing volume : cover.002"}),
            ("exit-7", {"message": "mysterious failure", "exit_code": 7}),
            ("exit-8", {"message": "mysterious failure", "exit_code": 8}),
            ("exit-124", {"message": "mysterious failure", "exit_code": 124}),
            ("exit-255", {"message": "mysterious failure", "exit_code": 255}),
        )
        for label, kwargs in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = self._cover(root)
                original = source.read_bytes()
                restore = _RecordingRestore(make_inplace_service())
                pipeline = _pipeline(root, _FailingExtractor(**kwargs), restore=restore)

                pipeline.run()

                self.assertEqual(restore.restore_calls, [], f"{label} must not restore")
                self.assertEqual(
                    self._routed_bytes(root, "cover.png"), original,
                    f"{label} must leave the selected original byte-identical",
                )

    def test_password_failure_still_escalates_to_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._cover(root)
            restore = _RecordingRestore(make_inplace_service())
            pipeline = _pipeline(root, _FailingExtractor("Wrong password"), restore=restore)

            pipeline.run()

            self.assertGreaterEqual(len(restore.restore_calls), 1, "content failures still escalate")

    def test_format_mismatch_still_escalates_to_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._cover(root)
            restore = _RecordingRestore(make_inplace_service())
            pipeline = _pipeline(root, _FailingExtractor("not a RAR archive"), restore=restore)

            pipeline.run()

            self.assertGreaterEqual(len(restore.restore_calls), 1)

    def test_restoreab_error_is_contained_as_package_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "cover.pdf"
            source.write_bytes(b"%PDF-1.7\ncover\n" + zip64_bytes())
            restore = _RecordingRestore(make_inplace_service())
            pipeline = _pipeline(root, _FailingExtractor("Wrong password"), restore=restore)

            result = pipeline.run()  # must not raise needs_review / PROCESSING_FAILED

            pkg = result.packages[0]
            self.assertEqual(pkg.state, "failed")
            self.assertIn("ZIP64", pkg.message)
            self.assertTrue(restore.restore_calls)

    def test_engine_error_from_restore_propagates(self):
        for exc in (EngineError("DISK_FULL", "no space"), ProcessingCancelled()):
            with self.subTest(exc=type(exc).__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._cover(root)
                restore = _RaisingRestore(make_inplace_service(), exc)
                pipeline = _pipeline(root, _FailingExtractor("Wrong password"), restore=restore)

                with self.assertRaises(type(exc)):
                    pipeline.run()


    def test_single_candidate_stop_all_skips_force_apate(self):
        """one/two/three force-restore hints must not fire on a stop_all failure."""

        cases = (
            ("one", "cover.one.png"),
            ("two", "cover.two.png"),
            ("three", "cover.three.png"),
        )
        for label, name in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / name
                source.write_bytes(PNG_HEAD + zip_bytes(("a.txt", b"a" * 1000)))
                original = source.read_bytes()
                restore = _RecordingRestore(make_inplace_service())
                extractor = _FailingExtractor("ERROR: Missing volume : cover.002")
                pipeline = _pipeline(root, extractor, restore=restore)

                pipeline.run()

                # The force-restore hint (rounds 1/2/3) must never be invoked,
                # no restore of any kind must run, only the original may remain,
                # and the extraction must not be retried on a forced candidate.
                self.assertEqual(restore.force_calls, [], f"{label} must not force-restore")
                self.assertEqual(restore.restore_calls, [], f"{label} must not restore")
                self.assertEqual(len(extractor.entries), 1, f"{label} must not re-extract a forced candidate")
                self.assertEqual(
                    self._routed_bytes(root, name), original,
                    f"{label} must leave the selected original byte-identical",
                )


if __name__ == "__main__":
    unittest.main()
