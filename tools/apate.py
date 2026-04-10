"""
Apate 伪装文件就地还原：对标上游 rippod/apate 的 Program.Reveal（常规分支）。

上游仓库: https://github.com/rippod/apate
参考实现: apate/Program.cs -> Reveal
详细文档: 同目录 APATE.md
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path


def apate_official_reveal(file_path: str | os.PathLike[str], *, quiet: bool = False) -> bool:
    """
    与 Apate 源码 Program.Reveal 等价的 Python 实现（面具长度正常时）。

    就地改写文件：去掉尾部面具元数据并写回原始文件头。请先备份。
    """
    path = Path(file_path)
    if not path.is_file():
        if not quiet:
            print("文件路径不存在或不是文件")
        return False

    try:
        with open(path, "rb+") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()

            if file_size < 4:
                return False

            f.seek(-4, os.SEEK_END)
            indicator_bytes = f.read(4)
            mask_head_length = struct.unpack("<I", indicator_bytes)[0]

            if not quiet:
                print(f"[*] 检测到面具头长度: {mask_head_length} 字节")

            backup_pos = file_size - 4 - mask_head_length
            if backup_pos < 0:
                if not quiet:
                    print("[-] 文件结构损坏，无法还原")
                return False

            f.seek(backup_pos)
            original_head_reversed = f.read(mask_head_length)
            original_head = original_head_reversed[::-1]

            f.truncate(backup_pos)
            f.seek(0)
            f.write(original_head)

        if not quiet:
            print("[+] 还原成功！文件已恢复原始结构。")
        return True
    except OSError as e:
        if not quiet:
            print(f"[-] 还原过程中发生错误: {e}")
        return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Apate 伪装文件还原（对标 github.com/rippod/apate Program.Reveal）。默认原地修改，请先备份。"
    )
    p.add_argument("file", type=Path, help="被伪装过的文件路径")
    p.add_argument("-q", "--quiet", action="store_true", help="仅通过退出码表示结果")
    args = p.parse_args(argv)
    ok = apate_official_reveal(args.file, quiet=args.quiet)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
