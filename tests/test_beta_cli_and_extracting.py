from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reorder_engine.beta import build_parser
from reorder_engine.domain.models import ArchiveKind, ArchiveProbe, ExtractionRequest, ExtractionResult, VolumeSet
from reorder_engine.interfaces.extracting import ExtractorStrategy
from reorder_engine.services.extracting import ExtractionService


class _FakeExtractor(ExtractorStrategy):
    def __init__(self, name: str, responses: list[ExtractionResult]):
        self._name = name
        self._responses = list(responses)
        self.calls: list[str | None] = []

    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return True

    def extract(self, request: ExtractionRequest, *, dry_run: bool = False) -> ExtractionResult:
        return self.extract_with_password(request, None, dry_run=dry_run)

    def extract_with_password(self, request: ExtractionRequest, password: str | None, *, dry_run: bool = False) -> ExtractionResult:
        _ = (request, dry_run)
        self.calls.append(password)
        return self._responses.pop(0)


class BetaCliAndExtractingTests(unittest.TestCase):
    def test_beta_parser_accepts_legacy_bat_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "--folder",
                "D:\\data",
                "--archive-mode",
                "wide",
                "--archive-min-mb",
                "2000",
                "--deep-extract",
                "--deep-mode",
                "smart",
                "--deep-max-depth",
                "6",
                "--deep-min-archive-mb",
                "128",
                "--deep-final-single-mb",
                "512",
                "--deep-max-candidates",
                "3",
                "--disable-bandizip",
                "--preserve-payload-names",
            ]
        )
        self.assertTrue(args.deep_extract)
        self.assertEqual(args.deep_max_depth, 6)
        self.assertTrue(args.disable_bandizip)

    def test_extraction_service_stops_on_missing_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "a.001"
            archive.write_text("x", encoding="utf-8")
            vs = VolumeSet(entry=archive, members=(archive,), group_key="g")
            req = ExtractionRequest(volume_set=vs, output_dir=root / "out", passwords=("pw1", "pw2"))
            fail = ExtractionResult(volume_set=vs, ok=False, tool="7z", message="Missing volume : a.002")
            first = _FakeExtractor("7z", [fail])
            second = _FakeExtractor("unrar", [fail])

            result = ExtractionService([first, second]).extract_one(req)

            self.assertFalse(result.ok)
            self.assertEqual(first.calls, [None])
            self.assertEqual(second.calls, [])

    def test_extraction_service_uses_probe_preferred_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "a.rar"
            archive.write_text("x", encoding="utf-8")
            vs = VolumeSet(entry=archive, members=(archive,), group_key="g")
            req = ExtractionRequest(volume_set=vs, output_dir=root / "out", passwords=())
            fail = ExtractionResult(volume_set=vs, ok=False, tool="7z", message="not rar archive")
            ok = ExtractionResult(volume_set=vs, ok=True, tool="unrar", message="ok")
            first = _FakeExtractor("7z", [fail])
            second = _FakeExtractor("unrar", [ok])
            probe = ArchiveProbe(path=archive, kind=ArchiveKind.ARCHIVE, archive_suffix=".rar", preferred_tool="unrar", reason="test")

            result = ExtractionService([first, second]).extract_one(req, probe=probe)

            self.assertTrue(result.ok)
            self.assertEqual(first.calls, [])
            self.assertEqual(second.calls, [None])


if __name__ == "__main__":
    unittest.main()
