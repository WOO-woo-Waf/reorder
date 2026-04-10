"""
Non-destructive Apate reveal helper.

Reference project:
https://github.com/rippod/apate
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
from pathlib import Path


def _default_output_path(path: Path) -> Path:
    if path.suffix:
        return path.with_name(f"{path.stem}.revealed{path.suffix}")
    return path.with_name(f"{path.name}.revealed")


def _read_revealed_bytes(src: Path) -> tuple[bytes, int] | None:
    with open(src, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        file_size = handle.tell()
        if file_size < 4:
            return None

        handle.seek(-4, os.SEEK_END)
        indicator_bytes = handle.read(4)
        mask_head_length = struct.unpack("<I", indicator_bytes)[0]
        backup_pos = file_size - 4 - mask_head_length
        if backup_pos < 0:
            return None

        handle.seek(backup_pos)
        original_head_reversed = handle.read(mask_head_length)
        original_head = original_head_reversed[::-1]

        handle.seek(0)
        body = handle.read(backup_pos)
        return original_head + body[mask_head_length:], mask_head_length


def apate_official_reveal(
    file_path: str | os.PathLike[str],
    *,
    output_path: str | os.PathLike[str] | None = None,
    quiet: bool = False,
    in_place: bool = False,
) -> bool:
    src = Path(file_path)
    if not src.is_file():
        if not quiet:
            print("File does not exist or is not a file.")
        return False

    try:
        parsed = _read_revealed_bytes(src)
        if parsed is None:
            if not quiet:
                print("Invalid Apate structure.")
            return False

        revealed_bytes, mask_head_length = parsed
        if not quiet:
            print(f"[*] Mask head length: {mask_head_length} bytes")

        if in_place:
            dst = src
        else:
            dst = Path(output_path) if output_path else _default_output_path(src)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst != src:
                shutil.copy2(src, dst)

        with open(dst, "rb+") as handle:
            handle.seek(0)
            handle.write(revealed_bytes)
            handle.truncate(len(revealed_bytes))

        if not quiet:
            print(f"[+] Reveal succeeded: {dst}")
        return True
    except OSError as exc:
        if not quiet:
            print(f"[-] Reveal failed: {exc}")
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reveal Apate-disguised files. Defaults to writing a sibling output file."
    )
    parser.add_argument("file", type=Path, help="Path to the disguised file.")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Optional output path.")
    parser.add_argument("--in-place", action="store_true", help="Modify the input file directly.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Only signal success via exit code.")
    args = parser.parse_args(argv)

    ok = apate_official_reveal(
        args.file,
        output_path=args.output,
        quiet=args.quiet,
        in_place=bool(args.in_place),
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
