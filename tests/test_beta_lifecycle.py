from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from reorder_engine.domain.models import ExtractionResult
from reorder_engine.services.beta_pipeline import BetaFolderPipeline
from reorder_engine.services.cleaning import DefaultGroupingNormalizer
from reorder_engine.services.decrypting import DecryptionService, PassthroughDecryptor
from reorder_engine.services.grouping import DefaultVolumeGroupingStrategy
from reorder_engine.services.restore_ab import RestoreABRestorer
from reorder_engine.services.restoring import (
    ArchiveSignatureInspector,
    PassthroughRestorer,
    RestorationService,
    SuffixVariantBuilder,
)


PAYLOAD = b"synthetic lifecycle payload"


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("payload.txt", PAYLOAD)
    return buffer.getvalue()


class _SyntheticExtractor:
    """Use real ZIP bytes while simulating external-tool outcomes."""

    def __init__(self, outcome: str, *, required_suffix: str | None = None):
        self.outcome = outcome
        self.required_suffix = required_suffix
        self.entries: list[Path] = []

    def extract_one(self, request, *, preference="auto", probe=None, dry_run=False):
        entry = request.volume_set.entry
        self.entries.append(entry)
        if dry_run:
            return ExtractionResult(request.volume_set, ok=True, tool="synthetic", message="dry-run")
        if self.required_suffix and not entry.name.endswith(self.required_suffix):
            return ExtractionResult(request.volume_set, ok=False, tool="synthetic", message="suffix rejected")
        if self.outcome == "failure":
            return ExtractionResult(request.volume_set, ok=False, tool="synthetic", message="wrong password")
        data = b"".join(path.read_bytes() for path in sorted(request.volume_set.members, key=lambda p: p.name))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(request.output_dir)
        return ExtractionResult(
            request.volume_set,
            ok=self.outcome == "success",
            tool="synthetic",
            message=None if self.outcome == "success" else "wrong password: simulated partial output",
        )


class BetaLifecycleTests(unittest.TestCase):
    def _pipeline(self, root: Path, extractor: _SyntheticExtractor) -> BetaFolderPipeline:
        inspector = ArchiveSignatureInspector()
        return BetaFolderPipeline(
            folder=root,
            decrypt_service=DecryptionService([PassthroughDecryptor()]),
            restore_service=RestorationService(
                [RestoreABRestorer(), SuffixVariantBuilder(inspector), PassthroughRestorer()],
                inspector=inspector,
            ),
            grouper=DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()),
            extractor=extractor,
            passwords=(),
            emit=lambda _message: None,
        )

    def _assert_routed(
        self,
        root: Path,
        result,
        *,
        outcome: str,
        expected_sources: dict[str, bytes],
    ) -> None:
        self.assertEqual(result.total, 1)
        self.assertEqual((result.ok_count, result.fail_count), (0, 1) if outcome == "failure" else (1, 0))
        self.assertEqual([p.name for p in root.iterdir() if p.is_file()], [], "originals left in input folder")
        destination = root / ("error_files/password_error" if outcome == "failure" else "success/archives")
        routed = {p.name: p.read_bytes() for p in destination.rglob("*") if p.is_file()}
        self.assertEqual(routed, expected_sources, "archive routing must retain the original bytes")
        if outcome == "success":
            payloads = list((root / "final").rglob("payload.txt"))
            self.assertEqual(len(payloads), 1)
            self.assertEqual(payloads[0].read_bytes(), PAYLOAD)
            self.assertFalse(any(p.is_file() for p in (root / "error_files").rglob("*")))
        elif outcome == "partial":
            payloads = list((root / "error_files/password_error").rglob("payload.txt"))
            self.assertEqual(len(payloads), 1)
            self.assertEqual(payloads[0].read_bytes(), PAYLOAD)
            self.assertFalse(any(p.is_file() for p in (root / "final").rglob("*")))
        else:
            self.assertFalse(list(root.rglob("payload.txt")))

    def test_restore_ab_copy_routes_original_for_each_outcome(self) -> None:
        for outcome in ("success", "partial", "failure"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                original = root / "merged.pdf"
                archive_data = _zip_bytes()
                data = b"%PDF-1.7\nsynthetic cover\n%%EOF\n" + archive_data
                original.write_bytes(data)
                extractor = _SyntheticExtractor(outcome)
                pipeline = self._pipeline(root, extractor)

                result = pipeline.run()

                self._assert_routed(root, result, outcome=outcome, expected_sources={original.name: data})
                self.assertTrue(extractor.entries)
                self.assertTrue(all(entry.parent.name == "restore_ab" for entry in extractor.entries))
                copies = list((root / "intermediate").rglob("*_restoreAB.zip"))
                self.assertEqual(len(copies), 1)
                self.assertEqual(copies[0].read_bytes(), archive_data)
                self.assertEqual(pipeline.run().total, 0, "a routed original must not be processed again")

    def test_suffix_rename_routes_live_path_or_rolled_back_original(self) -> None:
        for outcome in ("success", "partial", "failure"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                original = root / "disguised.bin"
                data = _zip_bytes()
                original.write_bytes(data)
                extractor = _SyntheticExtractor(outcome, required_suffix=".zip")

                result = self._pipeline(root, extractor).run()

                routed_name = original.name if outcome == "failure" else "disguised.zip"
                self._assert_routed(root, result, outcome=outcome, expected_sources={routed_name: data})
                self.assertEqual([p.name for p in extractor.entries], ["disguised.bin", "disguised.zip"])

    def test_normalized_volumes_route_all_members_for_each_outcome(self) -> None:
        for outcome in ("success", "partial", "failure"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                data = _zip_bytes()
                midpoint = len(data) // 2
                first = root / "split.zip.jpg"
                second = root / "split.zip.002"
                first.write_bytes(data[:midpoint])
                second.write_bytes(data[midpoint:])
                extractor = _SyntheticExtractor(outcome, required_suffix=".001")

                result = self._pipeline(root, extractor).run()

                first_name = first.name if outcome == "failure" else "split.zip.001"
                self._assert_routed(
                    root, result, outcome=outcome,
                    expected_sources={first_name: data[:midpoint], second.name: data[midpoint:]},
                )
                self.assertEqual([p.name for p in extractor.entries], ["split.zip.001"])

    def test_direct_volumes_keep_partial_output_for_routing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = _zip_bytes()
            midpoint = len(data) // 2
            sources = {"split.zip.001": data[:midpoint], "split.zip.002": data[midpoint:]}
            for name, content in sources.items():
                (root / name).write_bytes(content)

            result = self._pipeline(root, _SyntheticExtractor("partial")).run()

            self._assert_routed(root, result, outcome="partial", expected_sources=sources)

    def test_dry_run_keeps_sources_and_creates_no_outputs(self) -> None:
        for kind in ("restore_ab", "suffix", "volumes"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                data = _zip_bytes()
                if kind == "restore_ab":
                    sources = {"merged.pdf": b"%PDF-1.7\ncover\n" + data}
                elif kind == "suffix":
                    sources = {"disguised.bin": data}
                else:
                    sources = {"split.zip.jpg": data[:len(data) // 2], "split.zip.002": data[len(data) // 2:]}
                for name, content in sources.items():
                    (root / name).write_bytes(content)

                result = self._pipeline(root, _SyntheticExtractor("success")).run(dry_run=True)

                self.assertEqual(result.total, 1)
                self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, sources)


if __name__ == "__main__":
    unittest.main()
