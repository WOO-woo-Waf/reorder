from __future__ import annotations

import io
import os
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from reorder_engine.services import restore_ab
from reorder_engine.services.restore_ab import RestoreABError, RestoreABRestorer


def zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def absolute_offset_zip(raw: bytes, prefix_len: int) -> bytes:
    """Rewrite a ZIP so its stored offsets already include the cover prefix."""

    data = bytearray(raw)
    eocd = data.rfind(b"PK\x05\x06")
    central_offset = struct.unpack_from("<I", data, eocd + 16)[0]
    central_size = struct.unpack_from("<I", data, eocd + 12)[0]
    struct.pack_into("<I", data, eocd + 16, prefix_len + central_offset)
    cursor = central_offset
    limit = central_offset + central_size
    while cursor < limit:
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", data, cursor + 28)
        local = struct.unpack_from("<I", data, cursor + 42)[0]
        struct.pack_into("<I", data, cursor + 42, prefix_len + local)
        cursor += 46 + name_len + extra_len + comment_len
    return bytes(data)


def apate_disguise(original: bytes, mask: bytes) -> bytes:
    """Reproduce the Apate layout: mask + body + reversed real head + length."""

    length = len(mask)
    return mask + original[length:] + original[:length][::-1] + struct.pack("<I", length)


class InPlaceFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def cover(self, name: str, prefix: bytes, payload: bytes) -> Path:
        path = self.root / name
        path.write_bytes(prefix + payload)
        return path

    def inode(self, path: Path) -> int:
        return os.stat(path).st_ino


class PlainSuffixTests(InPlaceFixture):
    def test_plain_suffix_disguise_needs_no_transform(self) -> None:
        path = self.root / "BG57.zip.jpg"
        path.write_bytes(zip_bytes({"a.txt": b"a", "b.txt": b"b"}))
        before = path.read_bytes()

        self.assertFalse(RestoreABRestorer().can_handle(path))
        restorer = RestoreABRestorer(in_place=True)
        self.assertFalse(restorer.can_handle(path))

        restored = restorer.restore(path, workspace=self.root / "workspace")
        self.assertEqual(restored, [path])
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.root / "workspace").exists())


class RelativeZipTests(InPlaceFixture):
    def test_in_place_strips_prefix_from_relative_multi_member_zip(self) -> None:
        payload = zip_bytes({"first.txt": b"first", "second.txt": b"second"})
        path = self.cover("cover.pdf", b"%PDF-1.7\ncover\n", payload)
        before_inode = self.inode(path)

        restorer = RestoreABRestorer(in_place=True)
        self.assertTrue(restorer.can_handle(path))
        with mock.patch.object(restore_ab, "_copy_range", side_effect=AssertionError("copy forbidden")):
            restored = restorer.restore(path, workspace=self.root / "workspace")

        self.assertEqual(restored, [path])
        data = path.read_bytes()
        self.assertTrue(data.startswith(b"PK\x03\x04"))
        self.assertEqual(self.inode(path), before_inode)
        self.assertEqual(restorer.last_method, "restore-ab-in-place")
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(sorted(archive.namelist()), ["first.txt", "second.txt"])
            self.assertEqual(archive.read("second.txt"), b"second")
        self.assertFalse((self.root / "workspace").exists())

    def test_detection_is_cached_between_can_handle_and_restore(self) -> None:
        payload = zip_bytes({"only.txt": b"payload"})
        path = self.cover("photo.jpg", b"\xff\xd8\xff\xe0cover", payload)

        original = restore_ab._find_signature
        calls: list[int] = []

        def counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        with mock.patch.object(restore_ab, "_find_signature", counting):
            restorer = RestoreABRestorer(in_place=True)
            self.assertTrue(restorer.can_handle(path))
            restorer.restore(path, workspace=self.root / "workspace")

        self.assertEqual(len(calls), 1)


class AbsoluteZipTests(InPlaceFixture):
    def test_in_place_repairs_absolute_offsets_multi_member(self) -> None:
        prefix = b"%PDF-1.7\nlegacy-cover\n"
        raw = zip_bytes({"one.txt": b"one", "two.txt": b"two"})
        source_bytes = absolute_offset_zip(raw, len(prefix))
        path = self.root / "legacy.pdf"
        path.write_bytes(prefix + source_bytes)

        RestoreABRestorer(in_place=True).restore(path, workspace=self.root / "workspace")

        with zipfile.ZipFile(path) as archive:
            self.assertEqual(sorted(archive.namelist()), ["one.txt", "two.txt"])
            self.assertEqual(archive.read("one.txt"), b"one")
            self.assertEqual(archive.read("two.txt"), b"two")


class ContainerShiftTests(InPlaceFixture):
    def test_in_place_rar_moves_signature_and_preserves_bytes_and_inode(self) -> None:
        payload = restore_ab.SIG_RAR4 + b"rar-body" * 4 + b"\x00\x00\x00\x00"
        path = self.cover("clip.mp4", b"COVER-VIDEO-HEADER", payload)
        before_inode = self.inode(path)

        restorer = RestoreABRestorer(in_place=True)
        self.assertTrue(restorer.can_handle(path))
        with mock.patch.object(restore_ab, "_copy_range", side_effect=AssertionError("copy forbidden")):
            restored = restorer.restore(path, workspace=self.root / "workspace")

        self.assertEqual(restored, [path])
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(self.inode(path), before_inode)

    def test_in_place_7z_moves_signature_and_preserves_bytes_and_inode(self) -> None:
        payload = restore_ab.SIG_7Z + b"7z-body" * 4 + b"\x00\x00\x00\x00"
        path = self.cover("clip.mkv", b"COVER-VIDEO-HEADER", payload)
        before_inode = self.inode(path)

        RestoreABRestorer(in_place=True).restore(path, workspace=self.root / "workspace")

        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(self.inode(path), before_inode)

    def test_legacy_copy_leaves_rar_source_bytes_and_inode_unchanged(self) -> None:
        payload = restore_ab.SIG_RAR4 + b"rar-body" * 4 + b"\x00\x00\x00\x00"
        path = self.cover("clip.mp4", b"COVER-VIDEO-HEADER", payload)
        before = path.read_bytes()
        before_inode = self.inode(path)

        restored = RestoreABRestorer().restore(path, workspace=self.root / "workspace")

        self.assertNotEqual(restored, [path])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.inode(path), before_inode)
        self.assertEqual(restored[0].read_bytes(), payload)


class SafetyTests(InPlaceFixture):
    def test_dry_run_leaves_the_source_intact(self) -> None:
        payload = zip_bytes({"hello.txt": b"hello"})
        path = self.cover("dry.pdf", b"%PDF-1.7\ncover\n", payload)
        before = path.read_bytes()

        restored = RestoreABRestorer(in_place=True).restore(
            path, workspace=self.root / "workspace", dry_run=True
        )

        self.assertEqual(restored, [path])
        self.assertEqual(path.read_bytes(), before)

    def test_metadata_anomaly_fails_before_modification(self) -> None:
        payload = bytearray(zip_bytes({"hello.txt": b"hello"}))
        eocd = payload.rfind(b"PK\x05\x06")
        central_offset = struct.unpack_from("<I", payload, eocd + 16)[0]
        payload[central_offset : central_offset + 4] = b"XXXX"  # corrupt CD magic
        path = self.cover("broken.pdf", b"%PDF-1.7\ncover\n", bytes(payload))
        before = path.read_bytes()

        with self.assertRaises(RestoreABError):
            RestoreABRestorer(in_place=True).restore(path, workspace=self.root / "workspace")

        self.assertEqual(path.read_bytes(), before)

    def test_zip64_sentinel_is_rejected_before_modification(self) -> None:
        payload = bytearray(zip_bytes({"hello.txt": b"hello"}))
        eocd = payload.rfind(b"PK\x05\x06")
        struct.pack_into("<I", payload, eocd + 16, 0xFFFFFFFF)  # ZIP64 marker
        path = self.cover("big.pdf", b"%PDF-1.7\ncover\n", bytes(payload))
        before = path.read_bytes()

        with self.assertRaises(RestoreABError):
            RestoreABRestorer(in_place=True).restore(path, workspace=self.root / "workspace")

        self.assertEqual(path.read_bytes(), before)

    def test_apate_disguise_is_skipped_in_place_but_matched_when_copying(self) -> None:
        mask = b"\x00\x00\x00\x18ftypmp42mp41" + b"\x00" * 52
        payload = zip_bytes({"first.txt": b"a" * 1000, "second.txt": b"b" * 1000})
        path = self.root / "X.zip.001.mp4"
        path.write_bytes(apate_disguise(payload, mask))
        before = path.read_bytes()

        self.assertTrue(RestoreABRestorer().can_handle(path))
        restorer = RestoreABRestorer(in_place=True)
        self.assertFalse(restorer.can_handle(path))

        restored = restorer.restore(path, workspace=self.root / "workspace")
        self.assertEqual(restored, [path])
        self.assertEqual(path.read_bytes(), before)

    def test_symlinked_cover_is_ignored(self) -> None:
        payload = zip_bytes({"hello.txt": b"hello"})
        real = self.root / "real.pdf"
        real.write_bytes(b"%PDF-1.7\ncover\n" + payload)
        link = self.root / "link.pdf"
        try:
            link.symlink_to(real)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")

        restorer = RestoreABRestorer(in_place=True)
        self.assertFalse(restorer.can_handle(link))
        self.assertEqual(restorer.restore(link, workspace=self.root / "workspace"), [link])


class PipelineExposureTests(InPlaceFixture):
    def test_restoration_service_labels_the_in_place_restorer(self) -> None:
        from reorder_engine.services.restoring import RestorationService

        payload = zip_bytes({"hello.txt": b"hello"})
        path = self.cover("label.pdf", b"%PDF-1.7\ncover\n", payload)

        service = RestorationService([RestoreABRestorer(in_place=True)])
        restored, rollbacks = service.restore_with_rollbacks(path, workspace=self.root / "workspace")

        self.assertEqual(restored, [path])
        self.assertEqual(rollbacks, [])
        # The pipeline maps this name plus an in-place edit to "restore-ab-in-place".
        self.assertEqual(service.last_restorer_name(), "RestoreABRestorer")
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.read("hello.txt"), b"hello")


class MetadataGuardTests(InPlaceFixture):
    PREFIX = b"%PDF-1.7\ncover\n"

    def two_member_payload(self) -> bytearray:
        return bytearray(zip_bytes({"a.txt": b"a", "b.txt": b"b"}))

    def test_zip_multi_disk_eocd_is_rejected_before_modification(self) -> None:
        payload = self.two_member_payload()
        eocd = payload.rfind(b"PK\x05\x06")
        struct.pack_into("<H", payload, eocd + 4, 1)  # "this disk" number
        path = self.cover("multi.pdf", self.PREFIX, bytes(payload))
        before = path.read_bytes()

        with self.assertRaises(RestoreABError):
            RestoreABRestorer(in_place=True).restore(path, workspace=self.root / "workspace")

        self.assertEqual(path.read_bytes(), before)

    def test_zip_entry_count_mismatch_is_rejected_before_modification(self) -> None:
        payload = self.two_member_payload()
        eocd = payload.rfind(b"PK\x05\x06")
        struct.pack_into("<H", payload, eocd + 10, 1)  # claims one member, has two
        path = self.cover("count.pdf", self.PREFIX, bytes(payload))
        before = path.read_bytes()

        with self.assertRaises(RestoreABError):
            RestoreABRestorer(in_place=True).restore(path, workspace=self.root / "workspace")

        self.assertEqual(path.read_bytes(), before)

    def test_zip_oversized_central_directory_is_rejected_before_modification(self) -> None:
        payload = self.two_member_payload()
        eocd = payload.rfind(b"PK\x05\x06")
        struct.pack_into("<I", payload, eocd + 12, 64 * 1024 * 1024 + 1)
        path = self.cover("huge.pdf", self.PREFIX, bytes(payload))
        before = path.read_bytes()

        with self.assertRaises(RestoreABError):
            RestoreABRestorer(in_place=True).restore(path, workspace=self.root / "workspace")

        self.assertEqual(path.read_bytes(), before)

    def test_zip_central_offset_inconsistent_with_physical_is_rejected(self) -> None:
        payload = self.two_member_payload()
        eocd = payload.rfind(b"PK\x05\x06")
        struct.pack_into("<I", payload, eocd + 16, 7)  # neither relative nor absolute
        path = self.cover("offset.pdf", self.PREFIX, bytes(payload))
        before = path.read_bytes()

        with self.assertRaises(RestoreABError):
            RestoreABRestorer(in_place=True).restore(path, workspace=self.root / "workspace")

        self.assertEqual(path.read_bytes(), before)

    def test_local_header_name_mismatch_is_rejected_before_modification(self) -> None:
        payload = bytearray(zip_bytes({"a.txt": b"a"}))
        eocd = payload.rfind(b"PK\x05\x06")
        central = struct.unpack_from("<I", payload, eocd + 16)[0]
        # Same length, different name: the local header no longer matches the
        # central directory entry, so the location is not trustworthy.
        payload[central + 46 : central + 51] = b"b.txt"
        path = self.cover("name.pdf", self.PREFIX, bytes(payload))
        before = path.read_bytes()

        with self.assertRaises(RestoreABError):
            RestoreABRestorer(in_place=True).restore(path, workspace=self.root / "workspace")

        self.assertEqual(path.read_bytes(), before)

    def test_short_zip_comment_is_not_mistaken_for_apate(self) -> None:
        payload = bytearray(zip_bytes({"hello.txt": b"hello"}))
        eocd = payload.rfind(b"PK\x05\x06")
        comment = b"XXXXXXXX" + b"\x08\x00\x00\x00"  # last four bytes read as length 8
        struct.pack_into("<H", payload, eocd + 20, len(comment))
        path = self.root / "comment.pdf"
        path.write_bytes(self.PREFIX + bytes(payload) + comment)

        restorer = RestoreABRestorer(in_place=True)
        self.assertTrue(restorer.can_handle(path))
        restorer.restore(path, workspace=self.root / "workspace")

        self.assertTrue(path.read_bytes().startswith(b"PK\x03\x04"))
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.read("hello.txt"), b"hello")


class CommentEocdTests(InPlaceFixture):
    PREFIX = b"%PDF-1.7\ncover\n"

    def commented_cover(self, name: str, comment: bytes) -> Path:
        payload = bytearray(zip_bytes({"hello.txt": b"hello"}))
        eocd = payload.rfind(b"PK\x05\x06")
        struct.pack_into("<H", payload, eocd + 20, len(comment))
        path = self.root / name
        path.write_bytes(self.PREFIX + bytes(payload) + comment)
        self.expected = bytes(payload) + comment
        return path

    @staticmethod
    def _real_eocd(data: bytes) -> int:
        """Locate the EOCD actually backed by a physical central directory."""

        limit = len(data)
        fallback: int | None = None
        while True:
            pos = data.rfind(b"PK\x05\x06", 0, limit)
            if pos < 0:
                break
            limit = pos
            if pos + 22 > len(data):
                continue
            central_size = struct.unpack_from("<I", data, pos + 12)[0]
            if central_size == 0:
                if fallback is None:
                    fallback = pos
                continue
            physical = pos - central_size
            if physical >= 0 and data[physical : physical + 4] == b"PK\x01\x02":
                return pos
        if fallback is None:
            raise AssertionError("no structurally valid EOCD in restored file")
        return fallback

    def restored_member(self, path: Path, name: str = "hello.txt") -> bytes:
        """Restore in place, then read a member without trusting ``zipfile``.

        ``zipfile`` locates the EOCD with a naive ``rfind`` and therefore
        cannot read a file whose comment embeds ``PK\x05\x06``.  The restored
        bytes are compared against the embedded payload, then the comment is
        stripped from a copy so the central directory and member data can be
        read with ``zipfile`` independently.
        """

        restorer = RestoreABRestorer(in_place=True)
        self.assertTrue(restorer.can_handle(path))
        restorer.restore(path, workspace=self.root / "workspace")
        data = path.read_bytes()
        self.assertTrue(data.startswith(b"PK\x03\x04"))
        self.assertEqual(data, self.expected)
        eocd = self._real_eocd(data)
        stripped = bytearray(data[: eocd + 22])
        struct.pack_into("<H", stripped, eocd + 20, 0)
        with zipfile.ZipFile(io.BytesIO(bytes(stripped))) as archive:
            return archive.read(name)

    def test_comment_containing_eocd_signature_is_still_recovered(self) -> None:
        # A legal ZIP comment may contain b"PK\x05\x06"; the last match is not
        # the real record, so the search must keep walking backwards.
        path = self.commented_cover("fake-eocd.pdf", b"natural comment PK\x05\x06 fake")

        self.assertEqual(self.restored_member(path), b"hello")

    def test_comment_with_pk_eocd_tail_is_still_recovered(self) -> None:
        path = self.commented_cover("trailing-signature.pdf", b"payload ends with PK\x05\x06")

        self.assertEqual(self.restored_member(path), b"hello")

    def test_comment_with_forged_full_eocd_is_not_accepted(self) -> None:
        # A fully formed 22-byte fake EOCD placed in the comment passes the
        # length checks but has no physical central directory ahead of it, so
        # it must be skipped in favour of the real record.
        fake = bytearray(b"PK\x05\x06" + b"\x00" * 16)
        fake[10:12] = b"\x00\x00"  # entries = 0
        fake[12:16] = (300).to_bytes(4, "little")  # central size points nowhere
        fake[16:20] = (999).to_bytes(4, "little")
        fake += b"\x00\x00"  # no comment on the fake
        path = self.commented_cover("forged.pdf", b"COMMENT " + bytes(fake))

        self.assertEqual(self.restored_member(path), b"hello")

    def test_comment_with_empty_directory_shaped_eocd_is_not_accepted(self) -> None:
        # A zero-filled EOCD shape (central_size == entries == 0) inside the
        # comment reaches EOF and has no central-directory anchor; it must not
        # shadow the real, anchored record.
        fake = b"PK\x05\x06" + b"\x00" * 18  # comment_len 0, ends at EOF
        path = self.commented_cover("empty-shaped.pdf", b"note " + fake)

        self.assertEqual(self.restored_member(path), b"hello")


if __name__ == "__main__":
    unittest.main()
