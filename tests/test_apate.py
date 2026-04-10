from __future__ import annotations

import struct
import tempfile
import unittest
import importlib.util
from pathlib import Path


def _load_apate():
    script = Path(__file__).resolve().parents[1] / "tools" / "apate.py"
    spec = importlib.util.spec_from_file_location("test_apate_module", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.apate_official_reveal


def _make_disguised(original: bytes, mask_head: bytes) -> bytes:
    head_length = len(mask_head)
    return mask_head + original[head_length:] + original[:head_length][::-1] + struct.pack("<I", head_length)


class ApateTests(unittest.TestCase):
    def test_reveal_writes_to_new_file_and_keeps_source(self) -> None:
        apate_official_reveal = _load_apate()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = b"PK\x03\x04hello world from zip"
            disguised = _make_disguised(original, b"\x00\x00\x00\x00")
            src = root / "sample.mp4"
            out = root / "sample.revealed"
            src.write_bytes(disguised)

            ok = apate_official_reveal(src, output_path=out, quiet=True)

            self.assertTrue(ok)
            self.assertEqual(src.read_bytes(), disguised)
            self.assertEqual(out.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
