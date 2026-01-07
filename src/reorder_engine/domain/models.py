from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class ToolConfig:
    seven_zip_path: str | None = None
    bandizip_path: str | None = None


@dataclass(frozen=True)
class KeywordLibrary:
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class PasswordLibrary:
    passwords: tuple[str, ...]


@dataclass(frozen=True)
class FileCandidate:
    path: Path


@dataclass(frozen=True)
class VolumeSet:
    """一组分卷或单卷的集合。

    entry 是用于触发解压的入口文件（例如 .7z.001 / .part01.rar / .zip）。
    """

    entry: Path
    members: tuple[Path, ...]
    group_key: str

    def all_paths(self) -> Sequence[Path]:
        return self.members


@dataclass(frozen=True)
class RenameRecord:
    before: Path
    after: Path


@dataclass(frozen=True)
class ExtractionRequest:
    volume_set: VolumeSet
    output_dir: Path
    passwords: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractionResult:
    volume_set: VolumeSet
    ok: bool
    tool: str
    exit_code: int | None = None
    message: str | None = None
    password: str | None = None


@dataclass(frozen=True)
class PipelineOptions:
    input_dir: Path
    output_dir: Path
    keyword_file: Path
    tool_preference: str  # auto|7z|bandizip
    dry_run: bool = False
    recursive: bool = True
