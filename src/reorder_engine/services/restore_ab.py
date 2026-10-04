from __future__ import annotations

"""Restore ``merge_ab`` style polyglot files.

``restoreAB.exe`` is a small Tk application which restores a normal archive
that has been appended to a PDF/image (or another media file).  The important
part for the batch pipeline is the archive recovery; the GUI has no useful
command line contract, so this module keeps the operation headless and leaves
password handling to the normal extractor/password matrix.
"""

import os
import struct
from pathlib import Path

from reorder_engine.interfaces.decrypting import RestorerStrategy


SIG_ZIP_LOCAL = b"PK\x03\x04"
SIG_ZIP_EOCD = b"PK\x05\x06"
SIG_RAR4 = b"Rar!\x1a\x07\x00"
SIG_RAR5 = b"Rar!\x1a\x07\x01\x00"
SIG_7Z = b"7z\xbc\xaf'\x1c"
_COPY_CHUNK = 4 * 1024 * 1024


class RestoreABError(RuntimeError):
    """Raised when a polyglot archive cannot be recovered."""


def _copy_range(source: Path, start: int, end: int, target: Path) -> None:
    if start < 0 or end <= start:
        raise RestoreABError(f"invalid archive range: {start}:{end}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, target.open("wb") as dst:
        src.seek(start)
        remaining = end - start
        while remaining:
            chunk = src.read(min(_COPY_CHUNK, remaining))
            if not chunk:
                raise RestoreABError("archive ended before the recovered range")
            dst.write(chunk)
            remaining -= len(chunk)


def _find_signature(path: Path, signatures: tuple[bytes, ...], *, start: int = 1) -> tuple[int, bytes] | None:
    """Find the first container signature after the cover file header."""

    overlap = max(len(sig) for sig in signatures) - 1
    position = 0
    carry = b""
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_COPY_CHUNK)
            if not chunk:
                return None
            window = carry + chunk
            window_start = position - len(carry)
            candidates: list[tuple[int, bytes]] = []
            for sig in signatures:
                offset = window.find(sig)
                while offset >= 0:
                    absolute = window_start + offset
                    if absolute >= start:
                        candidates.append((absolute, sig))
                    offset = window.find(sig, offset + 1)
            if candidates:
                return min(candidates, key=lambda item: item[0])
            position += len(chunk)
            carry = window[-overlap:] if overlap else b""


def _eocd(path: Path) -> tuple[int, int, int, int] | None:
    """Return ``(position, central_size, central_offset, end)`` for ZIP32."""

    size = path.stat().st_size
    tail_len = min(size, 1024 * 1024 + 22 + 65535)
    with path.open("rb") as handle:
        handle.seek(size - tail_len)
        tail = handle.read(tail_len)
    pos = tail.rfind(SIG_ZIP_EOCD)
    if pos < 0 or pos + 22 > len(tail):
        return None
    comment_len = struct.unpack_from("<H", tail, pos + 20)[0]
    end = size - tail_len + pos + 22 + comment_len
    if end > size:
        return None
    central_size, central_offset = struct.unpack_from("<II", tail, pos + 12)
    return size - tail_len + pos, central_size, central_offset, end


def _zip_start_candidates(path: Path, signature_offset: int, eocd: tuple[int, int, int, int]) -> list[int]:
    eocd_pos, central_size, central_offset, _ = eocd
    physical_cd = eocd_pos - central_size
    candidates = [signature_offset]
    # Standard concatenated ZIP: central-directory offsets are relative to the
    # physical start of the ZIP member.
    relative_start = physical_cd - central_offset
    if signature_offset <= relative_start:
        candidates.append(relative_start)
    # Some merge_ab versions stored absolute offsets.  The physical signature
    # is still the safest start for producing a standalone archive.
    return list(dict.fromkeys(candidates))


def _zip_is_readable(path: Path) -> bool:
    """Validate the central directory without needing the archive password."""

    try:
        with path.open("rb") as handle:
            import zipfile

            with zipfile.ZipFile(handle) as archive:
                archive.namelist()
        return True
    except Exception:
        try:
            import pyzipper

            with pyzipper.AESZipFile(path) as archive:
                archive.namelist()
            return True
        except Exception:
            return False


def _patch_zip_offsets(path: Path, *, signature_offset: int, eocd: tuple[int, int, int, int], output: Path) -> None:
    """Create a standalone ZIP and repair the common absolute-offset layout.

    The normal path is a direct physical slice.  This fallback handles the
    older merge_ab layout where the central directory and local-header offsets
    were written against the original polyglot file.
    """

    eocd_pos, central_size, _central_offset, eocd_end = eocd
    central_start = eocd_pos - central_size
    _copy_range(path, signature_offset, eocd_end, output)
    with output.open("r+b") as handle:
        # EOCD's central-directory offset must point inside the new file.
        handle.seek((eocd_pos - signature_offset) + 16)
        handle.write(struct.pack("<I", central_start - signature_offset))

        handle.seek(central_start - signature_offset)
        end = central_start + central_size
        while handle.tell() < end:
            entry_pos = handle.tell()
            header = handle.read(46)
            if len(header) < 46 or header[:4] != b"PK\x01\x02":
                raise RestoreABError("invalid ZIP central-directory entry")
            name_len, extra_len, comment_len = struct.unpack_from("<HHH", header, 28)
            raw_offset = struct.unpack_from("<I", header, 42)[0]
            name = handle.read(name_len)
            extra = handle.read(extra_len)
            handle.seek(comment_len, os.SEEK_CUR)
            if raw_offset != 0xFFFFFFFF:
                # Prefer the layout whose local header has the expected magic.
                with path.open("rb") as src:
                    src.seek(raw_offset)
                    local = src.read(4)
                    if local != SIG_ZIP_LOCAL:
                        src.seek(raw_offset + signature_offset)
                        local = src.read(4)
                        if local == SIG_ZIP_LOCAL:
                            raw_offset += signature_offset
                    if local != SIG_ZIP_LOCAL:
                        raise RestoreABError(f"ZIP local header not found for {name!r}")
                local_offset = raw_offset - signature_offset
                handle.seek(entry_pos + 42)
                handle.write(struct.pack("<I", local_offset))
                handle.seek(entry_pos + 46 + name_len + extra_len + comment_len)


def _recover_zip(path: Path, signature_offset: int, output: Path) -> None:
    eocd = _eocd(path)
    if eocd is None:
        raise RestoreABError("ZIP EOCD not found")
    for start in _zip_start_candidates(path, signature_offset, eocd):
        candidate = output if start == signature_offset else output.with_suffix(".candidate.zip")
        try:
            _copy_range(path, start, eocd[3], candidate)
            if _zip_is_readable(candidate):
                if candidate != output:
                    candidate.replace(output)
                return
        except Exception:
            candidate.unlink(missing_ok=True)
    _patch_zip_offsets(path, signature_offset=signature_offset, eocd=eocd, output=output)
    if not _zip_is_readable(output):
        raise RestoreABError("recovered ZIP is not readable")


class RestoreABRestorer(RestorerStrategy):
    """Recover the archive member embedded by ``merge_ab``/``restoreAB``."""

    _cover_suffixes = frozenset(
        {
            ".pdf",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".webp",
            ".mp4",
            ".mkv",
            ".avi",
            ".mov",
            ".wmv",
            ".flv",
            ".webm",
            ".mp3",
            ".flac",
            ".ogg",
            ".wav",
            ".m4a",
            ".exe",
        }
    )
    _archive_signatures = (SIG_ZIP_LOCAL, SIG_RAR4, SIG_RAR5, SIG_7Z)

    def _is_cover(self, path: Path) -> bool:
        if path.suffix.lower() in self._cover_suffixes:
            return True
        try:
            with path.open("rb") as handle:
                head = handle.read(16)
        except OSError:
            return False
        return head.startswith(b"%PDF") or head.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"BM"))

    def _candidate(self, path: Path) -> tuple[int, str] | None:
        if not path.is_file() or not self._is_cover(path):
            return None
        found = _find_signature(path, self._archive_signatures)
        if found is None:
            # Empty ZIPs have no local header; use their EOCD as the probe.
            eocd = _eocd(path)
            if eocd is None:
                return None
            offset, signature = eocd[0], SIG_ZIP_EOCD
        else:
            offset, signature = found
        if signature == SIG_7Z:
            return offset, ".7z"
        if signature in (SIG_RAR4, SIG_RAR5):
            return offset, ".rar"
        return offset, ".zip"

    def can_handle(self, path: Path) -> bool:
        return self._candidate(path) is not None

    def restore(self, path: Path, *, workspace: Path | None = None, dry_run: bool = False) -> list[Path]:
        restored, _ = self.restore_with_rollbacks(path, workspace=workspace, dry_run=dry_run)
        return restored

    def restore_with_rollbacks(
        self,
        path: Path,
        *,
        workspace: Path | None = None,
        dry_run: bool = False,
    ) -> tuple[list[Path], list[object]]:
        candidate = self._candidate(path)
        if candidate is None or workspace is None or dry_run:
            return [path], []
        offset, suffix = candidate
        output_dir = workspace / "restore_ab"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{path.stem}_restoreAB{suffix}"
        if suffix == ".zip":
            _recover_zip(path, offset, output)
        else:
            _copy_range(path, offset, path.stat().st_size, output)
        return [output], []
