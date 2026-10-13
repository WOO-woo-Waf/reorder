from __future__ import annotations

"""Restore ``merge_ab`` style polyglot files.

``restoreAB.exe`` is a small Tk application which restores a normal archive
that has been appended to a PDF/image (or another media file).  The important
part for the batch pipeline is the archive recovery; the GUI has no useful
command line contract, so this module keeps the operation headless and leaves
password handling to the normal extractor/password matrix.

Two recovery modes are supported:

* ``in_place=False`` (default, legacy): slice the embedded archive into a new
  file under ``workspace``.  The selected original is never modified.
* ``in_place=True`` (desktop): repair the selected original through a single
  ``r+b`` handle.  The cover prefix is shifted off, the tail is truncated and
  the ZIP offsets are rewritten so the *same path* becomes a standalone
  archive.  No derived copy is produced and the caller accepts that the edit is
  not reversible.
"""

import os
import stat
import struct
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from reorder_engine.interfaces.decrypting import RestorerStrategy


SIG_ZIP_LOCAL = b"PK\x03\x04"
SIG_ZIP_CENTRAL = b"PK\x01\x02"
SIG_ZIP_EOCD = b"PK\x05\x06"
SIG_ZIP64_LOCATOR = b"PK\x06\x07"
SIG_RAR4 = b"Rar!\x1a\x07\x00"
SIG_RAR5 = b"Rar!\x1a\x07\x01\x00"
SIG_7Z = b"7z\xbc\xaf'\x1c"
_COPY_CHUNK = 4 * 1024 * 1024

# A file that already starts with one of these is the archive itself, not a
# cover, and must never be treated as a disguised polyglot.
_CONTAINER_SIGNATURES = (SIG_ZIP_LOCAL, SIG_RAR4, SIG_RAR5, SIG_7Z)
_EOCD_STRUCT_SIZE = 22
_CD_HEADER_SIZE = 46
_EOCD_TAIL_LIMIT = 1024 * 1024 + _EOCD_STRUCT_SIZE + 65535
_ZIP64_SENTINEL = 0xFFFFFFFF
_APATE_MAX_MASK = 64 * 1024 * 1024

# Mirror infrastructure/archive_safety.py: refuse oversized ZIP metadata
# before reading or rewriting anything.
_MAX_MEMBERS = 100000
_MAX_CENTRAL_DIR = 64 * 1024 * 1024

# Bound the per-instance detection cache; a changed identity invalidates it.
_CACHE_LIMIT = 16
IN_PLACE_METHOD = "restore-ab-in-place"


class RestoreABError(RuntimeError):
    """Raised when a polyglot archive cannot be recovered."""


@dataclass(frozen=True)
class _InPlacePlan:
    """Validated description of one in-place repair (applied atomically)."""

    start: int
    new_size: int
    patches: tuple[tuple[int, bytes], ...]


@dataclass(frozen=True)
class _EocdRecord:
    """Parsed ZIP32 end-of-central-directory record."""

    pos: int
    central_size: int
    central_offset: int
    entries: int
    entries_on_disk: int
    disk: int
    central_disk: int
    comment_len: int
    end: int


# ---------------------------------------------------------------------------
# Legacy copy helpers (kept for ``in_place=False`` and old callers).
# ---------------------------------------------------------------------------


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


def _read_eocd_record(handle, size: int) -> _EocdRecord | None:
    """Find the *real* ZIP32 EOCD, scanning the tail backwards (read-only).

    A ZIP comment may legally contain the ``PK\\x05\\x06`` bytes, so the last
    match is not necessarily the record.  Every candidate is checked for a
    physically consistent central directory before it is accepted; a
    fabricated record inside a comment is skipped and the search continues
    towards the real one.

    A record that anchors a non-empty central directory (or a ZIP64 locator)
    is preferred over a bare empty-directory record, so a comment ending in a
    zero-filled EOCD-shaped blob cannot shadow the real, anchored record.
    """

    if size <= 0:
        return None
    tail_len = min(size, _EOCD_TAIL_LIMIT)
    handle.seek(size - tail_len)
    tail = handle.read(tail_len)
    base = size - tail_len
    limit = len(tail)
    fallback: _EocdRecord | None = None
    while True:
        pos = tail.rfind(SIG_ZIP_EOCD, 0, limit)
        if pos < 0:
            return fallback
        limit = pos  # keep walking backwards past this candidate
        if pos + _EOCD_STRUCT_SIZE > len(tail):
            continue
        comment_len = struct.unpack_from("<H", tail, pos + 20)[0]
        end = base + pos + _EOCD_STRUCT_SIZE + comment_len
        if end > size:
            continue
        disk, central_disk, entries_on_disk, entries = struct.unpack_from("<4H", tail, pos + 4)
        central_size, central_offset = struct.unpack_from("<II", tail, pos + 12)
        if not _eocd_structure_ok(handle, base + pos, central_size, entries):
            continue
        record = _EocdRecord(
            pos=base + pos,
            central_size=central_size,
            central_offset=central_offset,
            entries=entries,
            entries_on_disk=entries_on_disk,
            disk=disk,
            central_disk=central_disk,
            comment_len=comment_len,
            end=end,
        )
        if central_size != 0:
            # Anchored to a real central directory or ZIP64 locator: a
            # fabricated record cannot produce that structure, so accept it.
            return record
        if fallback is None:
            # A bare empty-directory record: keep it only as a last resort in
            # case no anchored record exists in the tail.
            fallback = record


def _eocd_structure_ok(handle, eocd_pos: int, central_size: int, entries: int) -> bool:
    """Verify a candidate EOCD is backed by a physical central directory.

    The central directory sits immediately before the EOCD, so a real record
    must place a ``PK\\x01\\x02`` entry exactly ``central_size`` bytes ahead.
    Bytes that merely spell ``PK\\x05\\x06`` do not carry that structure.
    """

    if central_size == 0:
        # An empty directory carries no anchor; require the matching count.
        return entries == 0
    if central_size == _ZIP64_SENTINEL:
        # A real ZIP64 EOCD is announced by its locator immediately ahead.
        return eocd_pos - 20 >= 0 and _read_magic(handle, eocd_pos - 20) == SIG_ZIP64_LOCATOR
    physical = eocd_pos - central_size
    if physical < 0:
        return False
    return _read_magic(handle, physical) == SIG_ZIP_CENTRAL


def _read_eocd(handle, size: int) -> tuple[int, int, int, int] | None:
    """Return ``(position, central_size, central_offset, end)`` for legacy callers."""

    record = _read_eocd_record(handle, size)
    if record is None:
        return None
    return record.pos, record.central_size, record.central_offset, record.end


def _eocd(path: Path) -> tuple[int, int, int, int] | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    with path.open("rb") as handle:
        return _read_eocd(handle, size)


def _zip_start_candidates(path: Path, signature_offset: int, eocd: tuple[int, int, int, int]) -> list[int]:
    eocd_pos, central_size, central_offset, _ = eocd
    physical_cd = eocd_pos - central_size
    candidates = [signature_offset]
    # Standard concatenated ZIP: central-directory offsets are relative to the
    # physical start of the ZIP member.
    relative_start = physical_cd - central_offset
    if signature_offset <= relative_start:
        candidates.append(relative_start)
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
    """Create a standalone ZIP and repair the common absolute-offset layout."""

    eocd_pos, central_size, _central_offset, eocd_end = eocd
    central_start = eocd_pos - central_size
    _copy_range(path, signature_offset, eocd_end, output)
    with output.open("r+b") as handle:
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


# ---------------------------------------------------------------------------
# In-place planning helpers.
# ---------------------------------------------------------------------------


def _read_at(handle, offset: int, length: int) -> bytes:
    if offset < 0:
        return b""
    handle.seek(offset)
    return handle.read(length)


def _read_magic(handle, offset: int) -> bytes:
    return _read_at(handle, offset, 4)


def _resolve_local(
    handle,
    size: int,
    raw: int,
    start: int,
    name: bytes,
    central_physical: int,
    *,
    absolute_first: bool,
) -> int:
    """Return the *physical* offset of a member's local header.

    Older ``merge_ab`` builds stored absolute offsets; standard concatenated
    ZIPs store offsets relative to the ZIP member.  A candidate is accepted
    only when its local header sits before the central directory and repeats
    the central-directory member name; the EOCD layout decides the order when
    both readings happen to match, otherwise the single match is used.
    """

    order = (raw, raw + start) if absolute_first else (raw + start, raw)
    for candidate in dict.fromkeys(order):
        if _local_header_matches(
            handle, candidate, size, name=name, start=start, central_physical=central_physical
        ):
            return candidate
    raise RestoreABError("ZIP 成员 local header 未找到，已放弃原地修改")


def _local_header_matches(
    handle,
    candidate: int,
    size: int,
    *,
    name: bytes,
    start: int,
    central_physical: int,
) -> bool:
    """Confirm a local header sits before the directory and names its member."""

    if candidate < start or candidate + 30 > size or candidate + 30 > central_physical:
        return False
    header = _read_at(handle, candidate, 30)
    if len(header) != 30 or header[:4] != SIG_ZIP_LOCAL:
        return False
    local_name_len, local_extra_len = struct.unpack_from("<HH", header, 26)
    if local_name_len != len(name):
        return False
    if candidate + 30 + local_name_len + local_extra_len > central_physical:
        return False
    return _read_at(handle, candidate + 30, local_name_len) == name


def _build_zip_plan(handle, size: int, start: int, record: _EocdRecord) -> _InPlacePlan:
    """Validate the ZIP layout and describe the in-place repair."""

    eocd_pos, central_size, central_offset, end = record.pos, record.central_size, record.central_offset, record.end
    if record.disk != 0 or record.central_disk != 0 or record.entries_on_disk != record.entries:
        raise RestoreABError("ZIP 跨盘/分卷归档不支持原地修复")
    if central_offset == _ZIP64_SENTINEL or central_size == _ZIP64_SENTINEL or record.entries == 0xFFFF:
        raise RestoreABError("ZIP64 归档暂不支持原地修复")
    if eocd_pos - 20 >= 0 and _read_magic(handle, eocd_pos - 20) == SIG_ZIP64_LOCATOR:
        raise RestoreABError("ZIP64 归档暂不支持原地修复")
    if central_size > _MAX_CENTRAL_DIR:
        raise RestoreABError("ZIP 中央目录过大，已放弃原地修改")
    if record.entries > _MAX_MEMBERS:
        raise RestoreABError("ZIP 成员过多，已放弃原地修改")
    if start <= 0 or end - start <= 0:
        raise RestoreABError("ZIP 归档范围无效，已放弃原地修改")

    central_physical = eocd_pos - central_size
    if central_offset == central_physical:
        absolute_first = True
    elif central_offset == central_physical - start:
        absolute_first = False
    else:
        raise RestoreABError("ZIP 中央目录偏移与物理位置不一致，已放弃原地修改")

    patches: list[tuple[int, bytes]] = []
    count = 0
    if central_size:
        if central_physical <= start:
            raise RestoreABError("ZIP 中央目录位置无效，已放弃原地修改")
        cursor = central_physical
        limit = central_physical + central_size
        while cursor < limit:
            header = _read_at(handle, cursor, _CD_HEADER_SIZE)
            if len(header) != _CD_HEADER_SIZE or header[:4] != SIG_ZIP_CENTRAL:
                raise RestoreABError("ZIP 中央目录条目无效，已放弃原地修改")
            name_len, extra_len, comment_len = struct.unpack_from("<HHH", header, 28)
            name = _read_at(handle, cursor + _CD_HEADER_SIZE, name_len)
            if len(name) != name_len:
                raise RestoreABError("ZIP 中央目录条目无效，已放弃原地修改")
            raw = struct.unpack_from("<I", header, 42)[0]
            if raw == _ZIP64_SENTINEL:
                raise RestoreABError("ZIP64 归档暂不支持原地修复")
            local = _resolve_local(handle, size, raw, start, name, central_physical, absolute_first=absolute_first)
            patches.append((cursor + 42 - start, struct.pack("<I", local - start)))
            cursor += _CD_HEADER_SIZE + name_len + extra_len + comment_len
            count += 1
            if count > _MAX_MEMBERS:
                raise RestoreABError("ZIP 成员过多，已放弃原地修改")
        if cursor != limit:
            raise RestoreABError("ZIP 中央目录长度不一致，已放弃原地修改")
    if count != record.entries:
        raise RestoreABError("ZIP 中央目录数量不一致，已放弃原地修改")

    # The EOCD's central-directory offset must point inside the new file.
    patches.append((eocd_pos + 16 - start, struct.pack("<I", central_physical - start)))
    return _InPlacePlan(start, end - start, tuple(patches))


def _apply_in_place(handle, plan: _InPlacePlan) -> None:
    """Shift the tail forward by ``plan.start`` and rewrite the ZIP offsets."""

    remaining = plan.new_size
    read_pos = plan.start
    write_pos = 0
    while remaining > 0:
        handle.seek(read_pos)
        chunk = handle.read(min(_COPY_CHUNK, remaining))
        if not chunk:
            raise RestoreABError("原地前移时文件提前结束")
        handle.seek(write_pos)
        handle.write(chunk)
        read_pos += len(chunk)
        write_pos += len(chunk)
        remaining -= len(chunk)
    handle.truncate(plan.new_size)
    for offset, payload in plan.patches:
        handle.seek(offset)
        handle.write(payload)
    handle.flush()


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

    def __init__(self, *, in_place: bool = False) -> None:
        self._in_place = in_place
        self.last_method: str | None = None
        self._candidate_cache: OrderedDict[str, tuple[tuple[int, int, int, int, int], tuple[int, str] | None]] = (
            OrderedDict()
        )

    @property
    def in_place(self) -> bool:
        return self._in_place

    # -- detection --------------------------------------------------------

    def _is_cover(self, path: Path, *, head: bytes) -> bool:
        if path.suffix.lower() in self._cover_suffixes:
            return True
        return head.startswith(b"%PDF") or head.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"BM"))

    def _looks_like_apate(self, path: Path, *, size: int) -> bool:
        """Confirm an Apate disguise with bounded reads.

        Requires both the structural layout (mirroring ``tools/apate.py``) and
        a credible revealed head, i.e. the stored reversed original head must
        begin with a container signature (or ``MZ``).  This keeps a genuine
        Apate disguise excluded without mistaking a cover whose trailing bytes
        merely look like a small length (for example a short ZIP comment).
        """

        if size < 8:
            return False
        try:
            with path.open("rb") as handle:
                handle.seek(-4, os.SEEK_END)
                raw = handle.read(4)
                if len(raw) != 4:
                    return False
                mask_length = struct.unpack("<I", raw)[0]
                if mask_length <= 0 or mask_length > _APATE_MAX_MASK:
                    return False
                backup_pos = size - 4 - mask_length
                if backup_pos <= 0:
                    return False
                head_len = min(mask_length, 8)
                handle.seek(backup_pos + mask_length - head_len)
                reversed_tail = handle.read(head_len)
        except OSError:
            return False
        if len(reversed_tail) != head_len:
            return False
        revealed_head = reversed_tail[::-1]
        return revealed_head.startswith(_CONTAINER_SIGNATURES) or revealed_head.startswith(b"MZ")

    def _detect_candidate(self, path: Path) -> tuple[int, str] | None:
        try:
            with path.open("rb") as handle:
                head = handle.read(8)
        except OSError:
            return None
        if not head or head.startswith(_CONTAINER_SIGNATURES):
            # Already a real archive at offset 0 (for example an archive that
            # merely carries a media suffix); there is no cover to strip.
            return None
        if not self._is_cover(path, head=head):
            return None
        found = _find_signature(path, self._archive_signatures, start=1)
        if found is None:
            # Empty ZIP members have no local header; use their EOCD as probe.
            eocd = _eocd(path)
            if eocd is None:
                return None
            offset, signature = eocd[0], SIG_ZIP_EOCD
        else:
            offset, signature = found
        if offset < 1:
            return None
        if signature == SIG_7Z:
            return offset, ".7z"
        if signature in (SIG_RAR4, SIG_RAR5):
            return offset, ".rar"
        return offset, ".zip"

    def _candidate(self, path: Path) -> tuple[int, str] | None:
        """Detect the embedded member, cached against the file identity."""

        try:
            info = path.lstat()
        except OSError:
            return None
        if not stat.S_ISREG(info.st_mode):
            # Never follow symlinks or touch device/special files.
            return None
        identity = (info.st_mtime_ns, info.st_size, info.st_dev, info.st_ino, info.st_ctime_ns)
        key = str(path)
        cached = self._candidate_cache.get(key)
        if cached is not None and cached[0] == identity:
            self._candidate_cache.move_to_end(key)
            return cached[1]
        result = self._detect_candidate(path)
        self._candidate_cache[key] = (identity, result)
        self._candidate_cache.move_to_end(key)
        while len(self._candidate_cache) > _CACHE_LIMIT:
            self._candidate_cache.popitem(last=False)
        return result

    def _invalidate(self, path: Path) -> None:
        self._candidate_cache.pop(str(path), None)

    def _handle_candidate(self, path: Path) -> tuple[int, str] | None:
        candidate = self._candidate(path)
        if candidate is None:
            return None
        if self._in_place:
            try:
                size = path.lstat().st_size
            except OSError:
                return None
            if self._looks_like_apate(path, size=size):
                # A confirmed Apate disguise is restored by its own strategy;
                # truncating here would destroy it.  The pipeline also orders
                # Apate ahead of this scanner via ``preferred_kind``.
                return None
        return candidate

    def can_handle(self, path: Path) -> bool:
        return self._handle_candidate(path) is not None

    # -- public API -------------------------------------------------------

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
        candidate = self._handle_candidate(path)
        if candidate is None:
            return [path], []

        if self._in_place:
            self._restore_in_place(path, candidate, dry_run=dry_run)
            # The prefix is gone, so there is no honest rollback to advertise.
            return [path], []

        if workspace is None or dry_run:
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

    # -- in-place repair --------------------------------------------------

    def _restore_in_place(self, path: Path, candidate: tuple[int, str], *, dry_run: bool) -> None:
        offset, suffix = candidate
        # A dry run only validates the plan, so read-only access is enough; the
        # write handle is opened solely for an actual repair.
        with open(path, "rb" if dry_run else "r+b") as handle:
            size = os.fstat(handle.fileno()).st_size
            if suffix == ".zip":
                record = _read_eocd_record(handle, size)
                if record is None:
                    raise RestoreABError("ZIP EOCD 未找到，已放弃原地修改")
                plan = _build_zip_plan(handle, size, offset, record)
            else:
                if offset <= 0 or offset >= size:
                    raise RestoreABError("容器的签名偏移无效，已放弃原地修改")
                plan = _InPlacePlan(offset, size - offset, ())
            if dry_run:
                # Planning already validated the layout; leave the file intact.
                return
            _apply_in_place(handle, plan)
        self._invalidate(path)
        self.last_method = IN_PLACE_METHOD
