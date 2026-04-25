from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reorder_engine.services.beta_pipeline import BetaFolderPipeline


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


if __name__ == "__main__":
    unittest.main()
