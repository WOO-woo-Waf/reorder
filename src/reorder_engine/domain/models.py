from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
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


class CollisionPolicy(str, Enum):
    DUPLICATES_DIR = "_duplicates"


class ArchiveKind(str, Enum):
    UNKNOWN = "unknown"
    ARCHIVE = "archive"
    APATE = "apate"
    VARIANT = "variant"


@dataclass(frozen=True)
class ArchiveProbe:
    path: Path
    kind: ArchiveKind
    archive_suffix: str | None = None
    embedded_archive_name: str | None = None
    preferred_tool: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class VariantArtifact:
    source: Path
    path: Path
    rule_name: str
    suffix_changed: bool = False
    keep: bool = True


@dataclass(frozen=True)
class TraceStep:
    stage: str
    detail: str
    path: Path | None = None


@dataclass
class ProcessTrace:
    steps: list[TraceStep] = field(default_factory=list)

    def add(self, stage: str, detail: str, path: Path | None = None) -> None:
        self.steps.append(TraceStep(stage=stage, detail=detail, path=path))


@dataclass
class WorkItem:
    original_path: Path
    current_path: Path
    workspace: Path
    group_key: str
    trace: ProcessTrace = field(default_factory=ProcessTrace)

    def record(self, stage: str, detail: str, path: Path | None = None) -> None:
        self.trace.add(stage=stage, detail=detail, path=path)
