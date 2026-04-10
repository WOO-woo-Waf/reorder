from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reorder_engine.services.restoring import ArchiveSignatureInspector, RepeatedApateRestorer, RestorationService, SuffixVariantBuilder


class RestoringTests(unittest.TestCase):
    def test_suffix_variant_builder_changes_only_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "005-04.喜怒不形于色的人_1.mp4"
            source.write_bytes(b"not really a zip")
            workspace = root / "variants"

            service = RestorationService([SuffixVariantBuilder(ArchiveSignatureInspector())])
            candidates = service.restore(source, workspace=workspace)

            names = {candidate.name for candidate in candidates}
            self.assertIn("005-04.喜怒不形于色的人_1.mp4", names)
            self.assertIn("005-04.喜怒不形于色的人_1.zip", names)

    def test_repeated_apate_restorer_matches_three_name(self) -> None:
        inspector = ArchiveSignatureInspector()
        restorer = RepeatedApateRestorer(inspector, rounds=3)
        self.assertTrue(restorer.can_handle(Path("FolderThree.mp4")))


if __name__ == "__main__":
    unittest.main()
