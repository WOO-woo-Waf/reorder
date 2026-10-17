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
        members = (request.volume_set.entry,) + tuple(
            path for path in request.volume_set.members if path != request.volume_set.entry
        )
        data = b"".join(path.read_bytes() for path in members)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(request.output_dir)
        return ExtractionResult(
            request.volume_set,
            ok=self.outcome == "success",
            tool="synthetic",
            message=None if self.outcome == "success" else "wrong password: simulated partial output",
            partial_output=self.outcome == "partial",
        )


class _SequenceExtractor:
    def __init__(self, outcomes: tuple[str, ...]):
        self.outcomes = outcomes
        self.entries: list[Path] = []

    def extract_one(self, request, *, preference="auto", probe=None, dry_run=False):
        self.entries.append(request.volume_set.entry)
        outcome = self.outcomes[min(len(self.entries) - 1, len(self.outcomes) - 1)]
        if outcome == "failure":
            return ExtractionResult(request.volume_set, ok=False, tool="synthetic", message="wrong password")
        members = (request.volume_set.entry,) + tuple(
            path for path in request.volume_set.members if path != request.volume_set.entry
        )
        data = b"".join(path.read_bytes() for path in members)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(request.output_dir)
        return ExtractionResult(
            request.volume_set,
            ok=outcome == "success",
            tool="synthetic",
            message=None if outcome == "success" else "wrong password: simulated partial output",
            partial_output=outcome == "partial",
        )


class _UnmarkedOutputExtractor:
    def extract_one(self, request, *, preference="auto", probe=None, dry_run=False):
        request.output_dir.mkdir(parents=True, exist_ok=True)
        (request.output_dir / "partial.txt").write_bytes(b"unmarked output")
        return ExtractionResult(request.volume_set, ok=False, tool="synthetic", message="tool exited with warning")


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
        self.assertEqual((result.ok_count, result.fail_count), (1, 0) if outcome == "success" else (0, 1))
        self.assertEqual([p.name for p in root.iterdir() if p.is_file()], [], "originals left in input folder")
        destination = root / ("error_files" if outcome == "failure" else ("success/archives" if outcome == "success" else "incomplete/archives"))
        routed = {p.name: p.read_bytes() for p in destination.rglob("*") if p.is_file()}
        self.assertEqual(routed, expected_sources, "archive routing must retain the original bytes")
        if outcome == "success":
            payloads = list((root / "final").rglob("payload.txt"))
            self.assertEqual(len(payloads), 1)
            self.assertEqual(payloads[0].read_bytes(), PAYLOAD)
            self.assertFalse(any(p.is_file() for p in (root / "error_files").rglob("*")))
        elif outcome == "partial":
            payloads = list((root / "incomplete/outputs").rglob("payload.txt"))
            self.assertEqual(len(payloads), 1)
            self.assertEqual(payloads[0].read_bytes(), PAYLOAD)
            self.assertFalse(any(p.is_file() for p in (root / "final").rglob("*")))
            self.assertFalse(any(p.is_file() for p in (root / "success/archives").rglob("*")))
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
                # 0.3.3 reads the original and in-place suffix variants first; a
                # restoreAB copy is derived only when those leave no usable output.
                copies = list((root / "intermediate").rglob("*_restoreAB.zip"))
                if outcome in ("failure", "partial"):
                    self.assertTrue(
                        any(entry.parent.name == "restore_ab" for entry in extractor.entries)
                    )
                    self.assertEqual(len(copies), 1)
                    self.assertEqual(copies[0].read_bytes(), archive_data)
                else:
                    self.assertEqual(copies, [], "a directly readable polyglot needs no restoreAB copy")
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

                routed_name = original.name if outcome != "success" else "disguised.zip"
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

                first_name = first.name if outcome != "success" else "split.zip.001"
                self._assert_routed(
                    root, result, outcome=outcome,
                    expected_sources={first_name: data[:midpoint], second.name: data[midpoint:]},
                )
                expected_entries = ["split.zip.001"] if outcome == "success" else ["split.zip.001", "split.zip.jpg"]
                self.assertEqual([p.name for p in extractor.entries], expected_entries)

    def test_direct_volumes_keep_partial_output_for_routing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = _zip_bytes()
            midpoint = len(data) // 2
            sources = {"split.zip.001": data[:midpoint], "split.zip.002": data[midpoint:]}
            for name, content in sources.items():
                (root / name).write_bytes(content)
            existing = root / "incomplete" / "outputs" / "split" / "sentinel.txt"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"keep-existing")

            result = self._pipeline(root, _SyntheticExtractor("partial")).run()

            self._assert_routed(root, result, outcome="partial", expected_sources=sources)
            self.assertEqual(existing.read_bytes(), b"keep-existing")
            self.assertEqual(len(list((root / "incomplete/outputs").glob("split__run_*"))), 1)

    def test_multi_volume_partial_normalization_rolls_back_then_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = _zip_bytes()
            midpoint = len(data) // 2
            sources = {"split.zip.jpg": data[:midpoint], "split.zip.002": data[midpoint:]}
            for name, content in sources.items():
                (root / name).write_bytes(content)
            extractor = _SequenceExtractor(("partial", "success"))

            result = self._pipeline(root, extractor).run()

            self.assertEqual((result.ok_count, result.fail_count), (1, 0))
            self.assertEqual([path.name for path in extractor.entries], ["split.zip.001", "split.zip.jpg"])
            self.assertEqual({p.name: p.read_bytes() for p in (root / "success/archives").rglob("*") if p.is_file()}, sources)
            self.assertEqual([p.read_bytes() for p in (root / "final").rglob("payload.txt")], [PAYLOAD])
            self.assertFalse(any(p.is_file() for p in (root / "incomplete").rglob("*")))
            self.assertFalse(
                any(
                    path.is_file()
                    for holder in (root / "intermediate").rglob("_partial_candidate")
                    for path in holder.rglob("*")
                )
            )

    def test_nonempty_failure_without_partial_flag_is_not_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "payload.zip").write_bytes(_zip_bytes())

            result = self._pipeline(root, _UnmarkedOutputExtractor()).run()

            self.assertEqual((result.ok_count, result.fail_count), (0, 1))
            self.assertEqual(result.packages[0].state, "partial")
            self.assertEqual(
                [path.read_bytes() for path in (root / "incomplete/outputs").rglob("partial.txt")],
                [b"unmarked output"],
            )
            self.assertTrue((root / "incomplete/archives" / "payload.zip").exists())
            self.assertFalse(any(path.is_file() for path in (root / "success/archives").rglob("*")))

    def test_normalized_nonempty_unmarked_failure_routes_without_intermediate_holder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = _zip_bytes()
            midpoint = len(data) // 2
            sources = {"split.zip.jpg": data[:midpoint], "split.zip.002": data[midpoint:]}
            for name, content in sources.items():
                (root / name).write_bytes(content)

            result = self._pipeline(root, _UnmarkedOutputExtractor()).run()

            self.assertEqual((result.ok_count, result.fail_count), (0, 1))
            self.assertEqual(result.packages[0].state, "partial")
            self.assertEqual(
                [path.read_bytes() for path in (root / "incomplete/outputs").rglob("partial.txt")],
                [b"unmarked output"],
            )
            self.assertEqual(
                {path.name: path.read_bytes() for path in (root / "incomplete/archives").rglob("*") if path.is_file()},
                sources,
            )
            self.assertFalse(
                any(
                    path.is_file()
                    for holder in (root / "intermediate").rglob("_partial_candidate")
                    for path in holder.rglob("*")
                )
            )

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
