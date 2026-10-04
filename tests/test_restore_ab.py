from __future__ import annotations

import tempfile
import unittest
import zipfile
import struct
from pathlib import Path

import pyzipper

from reorder_engine.services.restore_ab import RestoreABRestorer


class RestoreABTests(unittest.TestCase):
    def test_restores_prefixed_standard_zip_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "payload.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("hello.txt", "hello")
            source = root / "cover.pdf"
            source.write_bytes(b"%PDF-1.7\ncover\n" + archive.read_bytes())

            restorer = RestoreABRestorer()
            self.assertTrue(restorer.can_handle(source))
            restored = restorer.restore(source, workspace=root / "workspace")

            self.assertEqual(len(restored), 1)
            self.assertEqual(source.read_bytes()[:5], b"%PDF-")
            with zipfile.ZipFile(restored[0]) as zf:
                self.assertEqual(zf.read("hello.txt"), b"hello")

    def test_repairs_absolute_offsets_used_by_older_merge_ab_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "payload.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("hello.txt", "hello")
            raw = bytearray(archive.read_bytes())
            eocd = raw.rfind(b"PK\x05\x06")
            central_offset = struct.unpack_from("<I", raw, eocd + 16)[0]
            central_size = struct.unpack_from("<I", raw, eocd + 12)[0]
            prefix = b"%PDF-1.7\nlegacy-cover\n"
            struct.pack_into("<I", raw, eocd + 16, len(prefix) + central_offset)
            struct.pack_into("<I", raw, central_offset + 42, len(prefix))
            source = root / "legacy.pdf"
            source.write_bytes(prefix + raw)

            restored = RestoreABRestorer().restore(source, workspace=root / "workspace")

            with zipfile.ZipFile(restored[0]) as zf:
                self.assertEqual(zf.read("hello.txt"), b"hello")

    def test_restores_aes_zip_and_leaves_password_for_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "payload.zip"
            with pyzipper.AESZipFile(
                archive,
                "w",
                compression=pyzipper.ZIP_DEFLATED,
                encryption=pyzipper.WZ_AES,
            ) as zf:
                zf.setpassword(b"secret")
                zf.writestr("secret.txt", b"encrypted payload")
            source = root / "photo.jpg"
            source.write_bytes(b"\xff\xd8\xff\xe0cover" + archive.read_bytes())

            restored = RestoreABRestorer().restore(source, workspace=root / "workspace")

            with pyzipper.AESZipFile(restored[0]) as zf:
                zf.setpassword(b"secret")
                self.assertEqual(zf.read("secret.txt"), b"encrypted payload")


if __name__ == "__main__":
    unittest.main()
