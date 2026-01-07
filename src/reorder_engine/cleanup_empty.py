from __future__ import annotations

import argparse
from pathlib import Path


def remove_empty_dirs(root: Path, *, keep_root: bool = True, dry_run: bool = False) -> int:
    if not root.exists() or not root.is_dir():
        return 0

    removed = 0
    # Bottom-up traversal
    dirs = [p for p in root.rglob("*") if p.is_dir()]
    dirs.sort(key=lambda p: len(p.parts), reverse=True)

    for d in dirs:
        try:
            if any(d.iterdir()):
                continue
            if dry_run:
                print(f"RMDIR(dry): {d}")
            else:
                d.rmdir()
            removed += 1
        except Exception:
            continue

    if not keep_root:
        try:
            if not any(root.iterdir()):
                if dry_run:
                    print(f"RMDIR(dry): {root}")
                else:
                    root.rmdir()
                removed += 1
        except Exception:
            pass

    return removed


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reorder_engine.cleanup_empty",
        description="删除目录树中的空文件夹（仅删除空目录，不删除任何文件）",
    )
    p.add_argument("--folder", required=True, help="要清理的目录")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不实际删除")
    p.add_argument("--remove-root", action="store_true", help="如果根目录也为空，也删除根目录")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.folder)
    removed = remove_empty_dirs(root, keep_root=not bool(args.remove_root), dry_run=bool(args.dry_run))
    print(f"DONE: removed_empty_dirs={removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
