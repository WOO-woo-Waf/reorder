from __future__ import annotations

import io
import shutil
import struct
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path

from reorder_engine.application.errors import EngineError
from reorder_engine.domain.models import ExtractionResult
from reorder_engine.infrastructure.archive_safety import (
    ArchiveSafetyInspector,
    GuardedExtractor,
    WorkspaceGuard,
)
from reorder_engine.services.beta_pipeline import BetaFolderPipeline
from reorder_engine.services.cleaning import DefaultGroupingNormalizer
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

# Synthetic fixtures live under the ignored, task-owned evidence directory only.
FORMATS_ROOT = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "desktop"
    / "hoshiribbon-0.3.1"
    / "formats"
)

PAYLOAD = b"disguised-volume-payload"
# A short media-looking cover; only the length matters for the Apate layout.
MASK = b"\x00\x00\x00\x18ftypmp42mp41" + b"\x00" * 52


def zip_bytes(member: str = "hello.txt", payload: bytes = PAYLOAD) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, payload)
    return buffer.getvalue()


def apate_disguise(original: bytes, mask: bytes = MASK) -> bytes:
    """Reproduce the Apate layout: mask + body + reversed real head + length."""

    length = len(mask)
    return mask + original[length:] + original[:length][::-1] + struct.pack("<I", length)


class _IsolatedCase:
    """Own an isolated fixture directory for one test and remove it afterwards."""

    def __init__(self, name: str):
        self.dir = FORMATS_ROOT / name

    def __enter__(self) -> Path:
        if self.dir.exists():
            shutil.rmtree(self.dir)
        self.dir.mkdir(parents=True)
        return self.dir

    def __exit__(self, *_exc: object) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


class _SplitZipExtractor:
    """Simulated volume-joining extractor (NOT a real 7-Zip run)."""

    def __init__(self, *, succeed: bool = True):
        self.succeed = succeed
        self.entries: list[Path] = []
        self.members: list[tuple[Path, ...]] = []
        self.verified: list[str] = []

    def extract_one(self, request, *, preference="auto", probe=None, dry_run=False):
        _ = (preference, probe, dry_run)
        self.entries.append(request.volume_set.entry)
        self.members.append(tuple(request.volume_set.members))
        if not self.succeed:
            return ExtractionResult(request.volume_set, ok=False, tool="synthetic", message="wrong password")
        data = b"".join(
            path.read_bytes() for path in sorted(request.volume_set.members, key=lambda p: p.name)
        )
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            self.verified.extend(names)
            request.output_dir.mkdir(parents=True, exist_ok=True)
            for name in names:
                (request.output_dir / name).write_bytes(archive.read(name))
        return ExtractionResult(request.volume_set, ok=True, tool="synthetic")


def _pipeline(root: Path, extractor: object) -> BetaFolderPipeline:
    inspector = ArchiveSignatureInspector()
    return BetaFolderPipeline(
        folder=root,
        decrypt_service=DecryptionService([PassthroughDecryptor()]),
        restore_service=RestorationService(
            [RestoreABRestorer(), ApateRestorer(inspector, rounds=3), EmbeddedArchiveRestorer(inspector), SuffixVariantBuilder(inspector)],
            inspector=inspector,
        ),
        grouper=DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()),
        extractor=extractor,
        passwords=(),
        emit=lambda _message: None,
    )


class WorkspaceScanRaceTests(unittest.TestCase):
    def _scan_during_removal(self, root, limit):
        vanished = root / "attempt-output.txt"
        vanished.write_bytes(b"temporary")
        live = root / "live.txt"
        live.write_bytes(b"live!")
        original_lstat = Path.lstat
        def disappearing(path, *args, **kwargs):
            if path == vanished:
                path.unlink()
                raise FileNotFoundError("tool removed temporary output")
            return original_lstat(path, *args, **kwargs)
        with patch.object(Path, "lstat", new=disappearing):
            WorkspaceGuard(root, byte_limit=limit).check(force=True)

    def test_scan_tolerates_tool_removing_failed_attempt_outputs(self):
        with _IsolatedCase("guard-live-removal") as root:
            self._scan_during_removal(root, 100)
            self.assertFalse((root / "attempt-output.txt").exists())

    def test_removal_tolerance_does_not_disable_output_limit(self):
        with _IsolatedCase("guard-live-removal-quota") as root:
            with self.assertRaises(EngineError) as ctx:
                self._scan_during_removal(root, 4)
            self.assertEqual(ctx.exception.code, "OUTPUT_LIMIT")

    def test_live_scan_still_rejects_links(self):
        with _IsolatedCase("guard-live-link") as root:
            target = root / "target.txt"
            target.write_bytes(b"keep")
            (root / "link.txt").symlink_to(target)
            with self.assertRaises(EngineError) as ctx:
                WorkspaceGuard(root, byte_limit=100).check(force=True)
            self.assertEqual(ctx.exception.code, "UNSAFE_ARCHIVE")
            self.assertEqual(target.read_bytes(), b"keep")


class DisguisedVolumeGroupingTests(unittest.TestCase):
    def test_hidden_suffix_volumes_group_to_the_first_part(self) -> None:
        with _IsolatedCase("grouping-hidden-suffix") as root:
            first = root / "BG57.zip.001.mp4"
            second = root / "BG57.zip.002.mp4"
            first.write_bytes(b"x")
            second.write_bytes(b"x")

            groups = DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()).group([first, second])

            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].entry.name, "BG57.zip.001.mp4")
            self.assertEqual({member.name for member in groups[0].members}, {first.name, second.name})


class DisguisedVolumePipelineTests(unittest.TestCase):
    def test_production_restoreab_order_does_not_shadow_confirmed_apate(self) -> None:
        with _IsolatedCase("pipeline-production-restorer-order") as root:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as handle:
                handle.writestr("first.txt", b"a" * 1000)
                handle.writestr("second.txt", b"b" * 1000)
            data = buffer.getvalue()
            midpoint = len(data) // 2
            first = root / "X.zip.001.mp4"
            second = root / "X.zip.002.mp4"
            first.write_bytes(apate_disguise(data[:midpoint]))
            second.write_bytes(apate_disguise(data[midpoint:]))
            # This is the overlap the earlier one-member fixture missed.
            self.assertTrue(RestoreABRestorer().can_handle(first))
            extractor = _SplitZipExtractor()
            result = _pipeline(root, extractor).run()
            self.assertEqual((result.ok_count, result.fail_count), (1, 0))
            self.assertEqual(extractor.verified, ["first.txt", "second.txt"])

    def test_failed_reveal_rollback_is_reported_and_not_logged_as_success(self) -> None:
        with _IsolatedCase("pipeline-rollback-error") as root:
            data = zip_bytes()
            midpoint = len(data) // 2
            (root / "X.zip.001.mp4").write_bytes(apate_disguise(data[:midpoint]))
            (root / "X.zip.002.mp4").write_bytes(apate_disguise(data[midpoint:]))
            pipeline = _pipeline(root, _SplitZipExtractor(succeed=False))
            trace = []
            pipeline._emit = trace.append
            def fail_rollback(*_args, **_kwargs):
                raise OSError("synthetic rollback failure")
            pipeline._restore.rollback_apate = fail_rollback
            with self.assertRaisesRegex(RuntimeError, "回滚失败"):
                pipeline.run()
            self.assertTrue(any(line.startswith("VOLUME-REVEAL-ROLLBACK-FAILED:") for line in trace))
            self.assertFalse(any(line.startswith("VOLUME-REVEAL-ROLLBACK:") for line in trace))

    def test_pipeline_reveals_every_volume_then_extracts(self) -> None:
        with _IsolatedCase("pipeline-hidden-suffix") as root:
            data = zip_bytes()
            midpoint = len(data) // 2
            disguised = (
                apate_disguise(data[:midpoint]),
                apate_disguise(data[midpoint:]),
            )
            first = root / "BG57.zip.001.mp4"
            second = root / "BG57.zip.002.mp4"
            first.write_bytes(disguised[0])
            second.write_bytes(disguised[1])

            extractor = _SplitZipExtractor()
            result = _pipeline(root, extractor).run()

            self.assertEqual((result.ok_count, result.fail_count), (1, 0))
            # The extractor must see the normalized first part plus its sibling.
            self.assertEqual([path.name for path in extractor.entries], ["BG57.zip.001"])
            self.assertEqual(
                [member.name for member in extractor.members[0]],
                ["BG57.zip.001", "BG57.zip.002"],
            )
            self.assertEqual(extractor.verified, ["hello.txt"])
            final = list((root / "final").rglob("hello.txt"))
            self.assertEqual(len(final), 1)
            self.assertEqual(final[0].read_bytes(), PAYLOAD)
            # Originals are archived with their revealed, renamed bytes.
            archived = {path.name: path.read_bytes() for path in (root / "success/archives").iterdir()}
            self.assertEqual(archived["BG57.zip.001"], data[:midpoint])
            self.assertEqual(archived["BG57.zip.002"], data[midpoint:])

    def test_reveal_rolls_back_to_the_disguised_bytes_on_failure(self) -> None:
        with _IsolatedCase("pipeline-hidden-suffix-rollback") as root:
            data = zip_bytes()
            midpoint = len(data) // 2
            disguised = (
                apate_disguise(data[:midpoint]),
                apate_disguise(data[midpoint:]),
            )
            first = root / "BG57.zip.001.mp4"
            second = root / "BG57.zip.002.mp4"
            first.write_bytes(disguised[0])
            second.write_bytes(disguised[1])

            result = _pipeline(root, _SplitZipExtractor(succeed=False)).run()

            self.assertEqual((result.ok_count, result.fail_count), (0, 1))
            routed = {path.name: path.read_bytes() for path in (root / "error_files").rglob("BG57.zip.*")}
            self.assertEqual(routed["BG57.zip.001.mp4"], disguised[0])
            self.assertEqual(routed["BG57.zip.002.mp4"], disguised[1])


class _FakeResult:
    def __init__(self, exit_code: int, stdout: str = ""):
        self.exit_code = exit_code
        self.stdout = stdout


class _FakeListingRunner:
    """Stand-in for ExternalCommandRunner that emits a fixed 7z -slt listing."""

    def __init__(self, lines: list[str], *, exit_code: int = 0):
        self.lines = list(lines)
        self.exit_code = exit_code
        self.calls = 0

    def run(self, args, *, cwd=None, output_sink=None):
        self.calls += 1
        if output_sink is not None:
            for line in self.lines:
                output_sink(line)
        return _FakeResult(self.exit_code, "\n".join(self.lines))


_SPLIT_LISTING = [
    "Path = folder",
    "Folder = +",
    "Size = 0",
    "",
    "Path = folder/hello.txt",
    "Folder = -",
    "Size = 3",
    "",
]


class GuardedVolumeInspectionTests(unittest.TestCase):
    @staticmethod
    def _split_part(root: Path, name: str = "X.zip.001") -> Path:
        # First split volume: a leading local header but no central directory.
        path = root / name
        path.write_bytes(b"PK\x03\x04" + b"\x00" * 20)
        return path

    def test_split_first_part_is_checked_as_the_complete_set(self) -> None:
        with _IsolatedCase("guard-split-set") as root:
            entry = self._split_part(root)
            sibling = root / "X.zip.002"
            sibling.write_bytes(b"\x00" * 8)
            runner = _FakeListingRunner(_SPLIT_LISTING)
            inspector = ArchiveSafetyInspector("7z", runner, 1 << 30)

            inspector.inspect(entry, None, siblings=(sibling,))
            self.assertEqual(runner.calls, 1)

    def test_eocdless_zip_path_falls_back_to_the_tool_only_with_siblings(self) -> None:
        with _IsolatedCase("guard-eocd-fallback") as root:
            entry = self._split_part(root)
            sibling = root / "X.zip.002"
            sibling.write_bytes(b"\x00" * 8)
            runner = _FakeListingRunner(_SPLIT_LISTING)
            inspector = ArchiveSafetyInspector("7z", runner, 1 << 30)

            # A multipart entry is trusted only after the tool lists the whole set.
            inspector._inspect_zip(entry, None, fallback_to_cli=True)
            self.assertEqual(runner.calls, 1)
            with self.assertRaises(EngineError) as ctx:
                inspector._inspect_zip(entry, None, fallback_to_cli=False)
            self.assertEqual(ctx.exception.code, "ARCHIVE_UNREADABLE")

    def test_complete_zip_with_a_sibling_lists_the_complete_set(self) -> None:
        with _IsolatedCase("guard-complete-zip") as root:
            entry = root / "X.zip"
            entry.write_bytes(zip_bytes())
            sibling = root / "X.z01"
            sibling.write_bytes(b"\x00" * 8)
            runner = _FakeListingRunner(_SPLIT_LISTING)
            inspector = ArchiveSafetyInspector("7z", runner, 1 << 30)

            inspector.inspect(entry, None, siblings=(sibling,))
            self.assertEqual(runner.calls, 1, "every declared volume set uses a tool listing")

    def test_unreadable_set_is_rejected(self) -> None:
        with _IsolatedCase("guard-split-unreadable") as root:
            entry = self._split_part(root)
            sibling = root / "X.zip.002"
            sibling.write_bytes(b"\x00" * 8)
            runner = _FakeListingRunner([], exit_code=2)
            inspector = ArchiveSafetyInspector("7z", runner, 1 << 30)

            with self.assertRaises(EngineError) as ctx:
                inspector.inspect(entry, None, siblings=(sibling,))

            self.assertEqual(ctx.exception.code, "ARCHIVE_UNREADABLE")

    def test_unsafe_member_from_the_complete_set_is_rejected(self) -> None:
        with _IsolatedCase("guard-split-unsafe-member") as root:
            entry = root / "X.zip.001"
            entry.write_bytes(b"PK\x03\x04" + b"\x00" * 20)
            sibling = root / "X.zip.002"
            sibling.write_bytes(b"\x00" * 8)
            runner = _FakeListingRunner(
                ["Path = ../escape.txt", "Size = 3", ""]
            )
            inspector = ArchiveSafetyInspector("7z", runner, 1 << 30)

            with self.assertRaises(EngineError) as ctx:
                inspector.inspect(entry, None, siblings=(sibling,))

            self.assertEqual(ctx.exception.code, "UNSAFE_ARCHIVE")

    def test_only_successful_inspections_are_cached_and_siblings_invalidate(self) -> None:
        with _IsolatedCase("guard-cache") as root:
            entry = root / "X.zip.001"
            entry.write_bytes(b"PK\x03\x04" + b"\x00" * 20)
            sibling = root / "X.zip.002"
            sibling.write_bytes(b"\x00" * 8)
            runner = _FakeListingRunner(_SPLIT_LISTING)
            inspector = ArchiveSafetyInspector("7z", runner, 1 << 30)

            inspector.inspect(entry, None, siblings=(sibling,))
            inspector.inspect(entry, None, siblings=(sibling,))
            self.assertEqual(runner.calls, 1, "unchanged successful set must be reused")

            sibling.write_bytes(b"\x00" * 16)  # sibling identity changed
            inspector.inspect(entry, None, siblings=(sibling,))
            self.assertEqual(runner.calls, 2, "a changed sibling must re-inspect")

    def test_failed_inspections_are_not_cached(self) -> None:
        with _IsolatedCase("guard-cache-failure") as root:
            entry = root / "X.zip.001"
            entry.write_bytes(b"PK\x03\x04" + b"\x00" * 20)
            sibling = root / "X.zip.002"
            sibling.write_bytes(b"\x00" * 8)
            runner = _FakeListingRunner([], exit_code=2)
            inspector = ArchiveSafetyInspector("7z", runner, 1 << 30)

            for _ in range(2):
                with self.assertRaises(EngineError):
                    inspector.inspect(entry, None, siblings=(sibling,))

            self.assertEqual(runner.calls, 2, "unreadable candidates are never cached")

    def test_guarded_extractor_passes_siblings_from_the_volume_set(self) -> None:
        with _IsolatedCase("guard-extractor-siblings") as root:
            entry = root / "X.zip.001"
            entry.write_bytes(b"PK\x03\x04" + b"\x00" * 20)
            sibling = root / "X.zip.002"
            sibling.write_bytes(b"\x00" * 8)
            runner = _FakeListingRunner(_SPLIT_LISTING)
            inspector = ArchiveSafetyInspector("7z", runner, 1 << 30)
            guard = WorkspaceGuard(root, byte_limit=1 << 30)

            seen: list[str] = []

            class _Delegate:
                def name(self) -> str:
                    return "7z"

                def is_available(self) -> bool:
                    return True

                def extract_with_password(self, request, password, *, dry_run=False):
                    seen.append(request.volume_set.entry.name)
                    return ExtractionResult(request.volume_set, ok=True, tool="7z", exit_code=0)

            request = type(
                "Request",
                (),
                {"volume_set": type("VS", (), {"entry": entry, "members": (entry, sibling)})()},
            )()
            extractor = GuardedExtractor(_Delegate(), inspector, guard)

            result = extractor.extract_with_password(request, None)

            self.assertTrue(result.ok)
            self.assertEqual(seen, ["X.zip.001"])
            self.assertEqual(runner.calls, 1)


class RestoreABTrailingDataTests(unittest.TestCase):
    def test_trailing_bytes_after_eocd_do_not_block_recovery(self) -> None:
        with _IsolatedCase("restoreab-trailing-data") as root:
            archive = root / "payload.zip"
            archive.write_bytes(zip_bytes())
            source = root / "cover.jpg"
            source.write_bytes(b"\xff\xd8\xff\xe0cover" + archive.read_bytes() + b"TRAILING-GARBAGE" * 8)

            restored = RestoreABRestorer().restore(source, workspace=root / "workspace")

            with zipfile.ZipFile(restored[0]) as handle:
                self.assertEqual(handle.read("hello.txt"), PAYLOAD)


if __name__ == "__main__":
    unittest.main()
