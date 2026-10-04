from __future__ import annotations

import unittest

from reorder_engine.services.archive_naming import split_archive_name


class ArchiveNamingTests(unittest.TestCase):
    def test_disguised_and_numbered_zip_volumes_keep_the_same_base(self) -> None:
        disguised = split_archive_name("小初.zip.jpg")
        numbered = split_archive_name("小初.zip.002")

        self.assertEqual((disguised.base, disguised.mid, disguised.end), ("小初", ".zip", ".jpg"))
        self.assertEqual((numbered.base, numbered.mid, numbered.end), ("小初", ".zip", ".002"))
        self.assertEqual(disguised.rebuild(), "小初.zip.jpg")
        self.assertEqual(numbered.rebuild(), "小初.zip.002")

    def test_regular_zip_name_is_unchanged(self) -> None:
        parts = split_archive_name("报告.zip")

        self.assertEqual((parts.base, parts.mid, parts.end), ("报告", "", ".zip"))


    def test_disguised_first_volume_keeps_base_for_other_media_tails(self) -> None:
        for tail in ("png", "mp4"):
            parts = split_archive_name(f"小初.zip.{tail}")

            self.assertEqual((parts.base, parts.mid, parts.end), ("小初", ".zip", f".{tail}"))
            self.assertEqual(parts.rebuild(), f"小初.zip.{tail}")

    def test_plain_media_name_is_not_treated_as_disguised_volume(self) -> None:
        parts = split_archive_name("photo.jpg")

        self.assertEqual((parts.base, parts.mid, parts.end), ("photo", "", ".jpg"))


if __name__ == "__main__":
    unittest.main()
