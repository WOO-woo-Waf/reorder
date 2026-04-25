from __future__ import annotations

import importlib.util
import re
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from reorder_engine.domain.models import ArchiveKind, ArchiveProbe, RenameVariantPlan, VariantArtifact
from reorder_engine.interfaces.decrypting import RestorerStrategy


@lru_cache(maxsize=1)
def _load_apate_reveal():
    script = Path(__file__).resolve().parents[3] / "tools" / "apate.py"
    spec = importlib.util.spec_from_file_location("reorder_engine_tools_apate", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Apate script: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "apate_official_reveal")


@lru_cache(maxsize=1)
def _load_apate_probe():
    script = Path(__file__).resolve().parents[3] / "tools" / "apate.py"
    spec = importlib.util.spec_from_file_location("reorder_engine_tools_apate", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Apate script: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "probe_apate_file")


@lru_cache(maxsize=1)
def _load_apate_redisguise():
    script = Path(__file__).resolve().parents[3] / "tools" / "apate.py"
    spec = importlib.util.spec_from_file_location("reorder_engine_tools_apate", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Apate script: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "apate_official_redisguise_in_place")


@dataclass(frozen=True)
class ApateRollbackRecord:
    path: Path
    mask_head: bytes
    rule_name: str


class ArchiveSignatureInspector:
    _archive_signatures: tuple[tuple[bytes, str], ...] = (
        (b"PK\x03\x04", ".zip"),
        (b"7z\xbc\xaf'\x1c", ".7z"),
        (b"Rar!\x1a\x07\x00", ".rar"),
        (b"Rar!\x1a\x07\x01\x00", ".rar"),
        (b"\x1f\x8b\x08", ".gz"),
    )
    _archive_suffixes: tuple[str, ...] = (
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".tgz",
        ".tar.gz",
        ".7z.001",
        ".zip.001",
    )
    _media_suffixes: frozenset[str] = frozenset(
        {
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
    _rar_suffixes: tuple[str, ...] = (".rar",)
    _zip_suffixes: tuple[str, ...] = (".zip", ".zip.001", ".z01")
    _seven_zip_suffixes: tuple[str, ...] = (".7z", ".7z.001")

    def read_header(self, path: Path, size: int = 8) -> bytes:
        try:
            with open(path, "rb") as handle:
                return handle.read(size)
        except OSError:
            return b""

    def detect_archive_suffix(self, path: Path) -> str | None:
        header = self.read_header(path)
        for signature, suffix in self._archive_signatures:
            if header.startswith(signature):
                return suffix
        return None

    def detect_media_suffix(self, path: Path) -> str | None:
        header = self.read_header(path, size=16)
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if suffix == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if suffix == ".gif" and (header.startswith(b"GIF87a") or header.startswith(b"GIF89a")):
            return ".gif"
        if suffix == ".bmp" and header.startswith(b"BM"):
            return ".bmp"
        if suffix == ".webp" and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return ".webp"
        if suffix in {".mp4", ".mov", ".m4a"} and len(header) >= 12 and header[4:8] == b"ftyp":
            return suffix
        if suffix in {".mkv", ".webm"} and header.startswith(b"\x1a\x45\xdf\xa3"):
            return suffix
        if suffix == ".avi" and header.startswith(b"RIFF") and header[8:12] == b"AVI ":
            return ".avi"
        if suffix == ".wav" and header.startswith(b"RIFF") and header[8:12] == b"WAVE":
            return ".wav"
        if suffix == ".mp3" and (header.startswith(b"ID3") or header.startswith((b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"))):
            return ".mp3"
        if suffix == ".flac" and header.startswith(b"fLaC"):
            return ".flac"
        if suffix == ".ogg" and header.startswith(b"OggS"):
            return ".ogg"
        if suffix == ".flv" and header.startswith(b"FLV"):
            return ".flv"
        return None

    def is_valid_final_media(self, path: Path) -> bool:
        if path.suffix.lower() not in self._media_suffixes:
            return False
        if self.detect_archive_suffix(path) is not None:
            return False
        if self.probe_apate(path) is not None:
            return False
        return self.detect_media_suffix(path) is not None

    def looks_like_archive_name(self, name: str) -> bool:
        low = name.lower()
        if low.endswith(self._archive_suffixes):
            return True
        if re.search(r"\.part\d{1,3}\.(rar|zip|7z)$", low):
            return True
        if re.search(r"\.[rz]\d{2}$", low):
            return True
        if re.search(r"\.\d{3}$", low):
            return True
        return False

    def looks_like_archive(self, path: Path) -> bool:
        return self.looks_like_archive_name(path.name) or self.detect_archive_suffix(path) is not None

    def probe_apate(self, path: Path) -> ArchiveProbe | None:
        probe = _load_apate_probe()(path)
        if not probe.ok:
            return None

        archive_suffix = None
        preferred_tool = None
        for signature, suffix in self._archive_signatures:
            if probe.original_head.startswith(signature):
                archive_suffix = suffix
                preferred_tool = self.preferred_tool_for_suffix(suffix)
                break
        if archive_suffix is None:
            return None

        return ArchiveProbe(
            path=path,
            kind=ArchiveKind.APATE,
            archive_suffix=archive_suffix,
            preferred_tool=preferred_tool,
            reason=probe.reason,
        )

    def trim_embedded_archive_name(self, name: str) -> str | None:
        patterns = (
            r"\.tar\.gz",
            r"\.7z\.001",
            r"\.zip\.001",
            r"\.part\d{1,3}\.(rar|zip|7z)",
            r"\.rar",
            r"\.zip",
            r"\.7z",
            r"\.tar",
            r"\.tgz",
            r"\.gz",
            r"\.bz2",
            r"\.xz",
        )
        low = name.lower()
        for pattern in patterns:
            match = re.search(pattern, low)
            if match and match.end() < len(name):
                return name[: match.end()]
        return None

    def preferred_tool_for_suffix(self, suffix: str | None) -> str | None:
        if not suffix:
            return None
        low = suffix.lower()
        if low.endswith(self._rar_suffixes):
            return "unrar"
        if low.endswith(self._seven_zip_suffixes):
            return "7z"
        if low.endswith(self._zip_suffixes):
            return "7z"
        return None

    def probe_path(self, path: Path) -> ArchiveProbe:
        archive_suffix = self.detect_archive_suffix(path)
        if self.looks_like_archive_name(path.name) or archive_suffix is not None:
            suffix = archive_suffix or self._archive_suffix_from_name(path.name)
            return ArchiveProbe(
                path=path,
                kind=ArchiveKind.ARCHIVE,
                archive_suffix=suffix,
                preferred_tool=self.preferred_tool_for_suffix(suffix),
                reason="name-or-signature",
            )

        apate_probe = self.probe_apate(path)
        if apate_probe is not None:
            return apate_probe

        trimmed = self.trim_embedded_archive_name(path.name)
        if trimmed is not None:
            suffix = self._archive_suffix_from_name(trimmed)
            return ArchiveProbe(
                path=path,
                kind=ArchiveKind.VARIANT,
                archive_suffix=suffix,
                embedded_archive_name=trimmed,
                preferred_tool=self.preferred_tool_for_suffix(suffix),
                reason="embedded-archive-name",
            )

        return ArchiveProbe(path=path, kind=ArchiveKind.UNKNOWN, reason="no-match")

    def _archive_suffix_from_name(self, name: str) -> str | None:
        low = name.lower()
        ordered = sorted(self._archive_suffixes, key=len, reverse=True)
        for suffix in ordered:
            if low.endswith(suffix):
                return suffix
        if re.search(r"\.part\d{1,3}\.rar$", low):
            return ".rar"
        if re.search(r"\.part\d{1,3}\.zip$", low):
            return ".zip"
        if re.search(r"\.part\d{1,3}\.7z$", low):
            return ".7z"
        if re.search(r"\.r\d{2}$", low):
            return ".rar"
        if re.search(r"\.z\d{2}$", low):
            return ".zip"
        if re.search(r"\.\d{3}$", low):
            return ".zip"
        return None


class RestoreRule(ABC):
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def matches(self, path: Path, inspector: ArchiveSignatureInspector) -> bool:
        ...

    @abstractmethod
    def apply(
        self,
        path: Path,
        *,
        workspace: Path,
        inspector: ArchiveSignatureInspector,
        dry_run: bool,
    ) -> list[VariantArtifact]:
        ...


class RenameVariantRule(ABC):
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def matches(self, path: Path, inspector: ArchiveSignatureInspector) -> bool:
        ...

    @abstractmethod
    def plan(
        self,
        path: Path,
        *,
        inspector: ArchiveSignatureInspector,
    ) -> list[RenameVariantPlan]:
        ...


class PostExtractRule(ABC):
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def collect(
        self,
        folder: Path,
        *,
        workspace: Path,
        inspector: ArchiveSignatureInspector,
        min_archive_bytes: int,
        final_single_bytes: int,
        dry_run: bool,
    ) -> list[Path]:
        ...


class _ApateRestoreRule(RestoreRule):
    def __init__(self, *, rounds: int):
        self._rounds = max(1, rounds)

    def name(self) -> str:
        return "apate-reveal"

    def matches(self, path: Path, inspector: ArchiveSignatureInspector) -> bool:
        return inspector.probe_apate(path) is not None

    def apply(
        self,
        path: Path,
        *,
        workspace: Path,
        inspector: ArchiveSignatureInspector,
        dry_run: bool,
    ) -> list[VariantArtifact]:
        reveal = _load_apate_reveal()
        outputs: list[VariantArtifact] = []
        current = path
        for index in range(1, self._rounds + 1):
            if inspector.probe_apate(current) is None:
                break
            ok = True
            if not dry_run:
                ok = reveal(current, quiet=True, in_place=True)
            if not ok:
                return outputs
            artifact = VariantArtifact(
                source=path,
                path=current,
                rule_name=self.name(),
                suffix_changed=False,
                keep=True,
            )
            outputs.append(artifact)
            current = artifact.path
        return outputs

    def capture_rollback(self, path: Path, inspector: ArchiveSignatureInspector) -> ApateRollbackRecord | None:
        if inspector.probe_apate(path) is None:
            return None
        probe = _load_apate_probe()(path)
        if not probe.ok or not probe.mask_head:
            return None
        return ApateRollbackRecord(path=path, mask_head=probe.mask_head, rule_name=self.name())


class _UnknownArchiveVariantsRule(RenameVariantRule):
    def name(self) -> str:
        return "unknown-archive-variants"

    def matches(self, path: Path, inspector: ArchiveSignatureInspector) -> bool:
        probe = inspector.probe_path(path)
        return probe.kind == ArchiveKind.UNKNOWN

    def plan(
        self,
        path: Path,
        *,
        inspector: ArchiveSignatureInspector,
    ) -> list[RenameVariantPlan]:
        _ = inspector
        return [
            RenameVariantPlan(
                source=path,
                target=path.with_name(f"{path.stem}.7z" if path.suffix else f"{path.name}.7z"),
                rule_name=self.name(),
                preferred_tool="bandizip",
            ),
            RenameVariantPlan(
                source=path,
                target=path.with_name(f"{path.stem}.zip" if path.suffix else f"{path.name}.zip"),
                rule_name=self.name(),
            ),
            RenameVariantPlan(
                source=path,
                target=path.with_name(f"{path.stem}.rar" if path.suffix else f"{path.name}.rar"),
                rule_name=self.name(),
            ),
        ]


class _TrimEmbeddedArchiveSuffixRule(RenameVariantRule):
    def name(self) -> str:
        return "trim-embedded-archive-suffix"

    def matches(self, path: Path, inspector: ArchiveSignatureInspector) -> bool:
        return inspector.trim_embedded_archive_name(path.name) is not None

    def plan(
        self,
        path: Path,
        *,
        inspector: ArchiveSignatureInspector,
    ) -> list[RenameVariantPlan]:
        trimmed = inspector.trim_embedded_archive_name(path.name)
        if trimmed is None:
            return []
        return [
            RenameVariantPlan(
                source=path,
                target=path.with_name(trimmed),
                rule_name=self.name(),
            )
        ]


class _SignatureRenameRule(RenameVariantRule):
    def name(self) -> str:
        return "signature-rename"

    def matches(self, path: Path, inspector: ArchiveSignatureInspector) -> bool:
        detected = inspector.detect_archive_suffix(path)
        return detected is not None and not path.name.lower().endswith(detected)

    def plan(
        self,
        path: Path,
        *,
        inspector: ArchiveSignatureInspector,
    ) -> list[RenameVariantPlan]:
        detected = inspector.detect_archive_suffix(path)
        if detected is None:
            return []
        target_name = f"{path.name}{detected}" if not path.suffix else f"{path.stem}{detected}"
        return [
            RenameVariantPlan(
                source=path,
                target=path.with_name(target_name),
                rule_name=self.name(),
            )
        ]


class _MediaZipRule(RenameVariantRule):
    _suffixes = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"}

    def name(self) -> str:
        return "media-to-zip"

    def matches(self, path: Path, inspector: ArchiveSignatureInspector) -> bool:
        probe = inspector.probe_path(path)
        return probe.kind == ArchiveKind.UNKNOWN and path.suffix.lower() in self._suffixes

    def plan(
        self,
        path: Path,
        *,
        inspector: ArchiveSignatureInspector,
    ) -> list[RenameVariantPlan]:
        _ = inspector
        return [
            RenameVariantPlan(
                source=path,
                target=path.with_name(f"{path.stem}.zip"),
                rule_name=self.name(),
            )
        ]


class _Media7zBandizipRule(RenameVariantRule):
    _suffixes = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"}

    def name(self) -> str:
        return "media-to-7z-bandizip"

    def matches(self, path: Path, inspector: ArchiveSignatureInspector) -> bool:
        probe = inspector.probe_path(path)
        return probe.kind == ArchiveKind.UNKNOWN and path.suffix.lower() in self._suffixes

    def plan(
        self,
        path: Path,
        *,
        inspector: ArchiveSignatureInspector,
    ) -> list[RenameVariantPlan]:
        _ = inspector
        return [
            RenameVariantPlan(
                source=path,
                target=path.with_name(f"{path.stem}.7z"),
                rule_name=self.name(),
                preferred_tool="bandizip",
            )
        ]


class _NoSuffixZipRule(RenameVariantRule):
    def name(self) -> str:
        return "no-suffix-to-zip"

    def matches(self, path: Path, inspector: ArchiveSignatureInspector) -> bool:
        return inspector.probe_path(path).kind == ArchiveKind.UNKNOWN and not path.suffix

    def plan(
        self,
        path: Path,
        *,
        inspector: ArchiveSignatureInspector,
    ) -> list[RenameVariantPlan]:
        _ = inspector
        return [
            RenameVariantPlan(
                source=path,
                target=path.with_name(f"{path.name}.zip"),
                rule_name=self.name(),
            )
        ]


class _JpgExeArchiveRule(RenameVariantRule):
    _allowed = {".jpg", ".jpeg", ".exe"}

    def name(self) -> str:
        return "jpg-exe-archive-variants"

    def matches(self, path: Path, inspector: ArchiveSignatureInspector) -> bool:
        probe = inspector.probe_path(path)
        return probe.kind == ArchiveKind.UNKNOWN and path.suffix.lower() in self._allowed

    def plan(
        self,
        path: Path,
        *,
        inspector: ArchiveSignatureInspector,
    ) -> list[RenameVariantPlan]:
        _ = inspector
        out: list[RenameVariantPlan] = []
        for suffix in (".rar", ".zip", ".7z"):
            out.append(
                RenameVariantPlan(
                    source=path,
                    target=path.with_name(f"{path.stem}{suffix}"),
                    rule_name=self.name(),
                )
            )
        return out


class _TrimScToZipRule(RenameVariantRule):
    def name(self) -> str:
        return "trim-sc-to-zip"

    def matches(self, path: Path, inspector: ArchiveSignatureInspector) -> bool:
        _ = inspector
        return path.is_file() and path.name.lower().endswith("sc")

    def plan(
        self,
        path: Path,
        *,
        inspector: ArchiveSignatureInspector,
    ) -> list[RenameVariantPlan]:
        _ = inspector
        trimmed = path.name[:-2].rstrip()
        if not trimmed:
            return []
        return [RenameVariantPlan(source=path, target=path.with_name(f"{trimmed}.zip"), rule_name=self.name())]


class _TrailingScZipRule(PostExtractRule):
    def name(self) -> str:
        return "trim-sc-zip"

    def collect(
        self,
        folder: Path,
        *,
        workspace: Path,
        inspector: ArchiveSignatureInspector,
        min_archive_bytes: int,
        final_single_bytes: int,
        dry_run: bool,
    ) -> list[Path]:
        _ = (inspector, min_archive_bytes, final_single_bytes)
        out: list[Path] = []
        for file in folder.rglob("*"):
            if not file.is_file():
                continue
            base_name = file.name
            if not base_name.lower().endswith("sc"):
                continue
            out.append(file)
        return out


class _NestedArchivePostRule(PostExtractRule):
    def name(self) -> str:
        return "nested-archives"

    def collect(
        self,
        folder: Path,
        *,
        workspace: Path,
        inspector: ArchiveSignatureInspector,
        min_archive_bytes: int,
        final_single_bytes: int,
        dry_run: bool,
    ) -> list[Path]:
        _ = (workspace, dry_run)
        files = [p for p in folder.rglob("*") if p.is_file()]
        if not files:
            return []

        def size_of(path: Path) -> int:
            try:
                return path.stat().st_size
            except OSError:
                return 0

        direct_files = [p for p in folder.iterdir() if p.is_file()]
        direct_dirs = [p for p in folder.iterdir() if p.is_dir()]
        if len(direct_files) == 1 and not direct_dirs:
            only = direct_files[0]
            if inspector.is_valid_final_media(only):
                return []
            if inspector.looks_like_archive(only) or size_of(only) >= max(min_archive_bytes, final_single_bytes):
                return [only]

        archive_like = [p for p in files if inspector.looks_like_archive(p)]
        archive_like.sort(key=size_of, reverse=True)
        if archive_like:
            return archive_like[:5]

        big_files = [p for p in files if size_of(p) >= final_single_bytes and not inspector.is_valid_final_media(p)]
        big_files.sort(key=size_of, reverse=True)
        return big_files[:1]


class ApateRestorer(RestorerStrategy):
    def __init__(self, inspector: ArchiveSignatureInspector, *, rounds: int = 3):
        self._inspector = inspector
        self._rules: tuple[RestoreRule, ...] = (_ApateRestoreRule(rounds=rounds),)

    def can_handle(self, path: Path) -> bool:
        return any(rule.matches(path, self._inspector) for rule in self._rules)

    def restore(self, path: Path, *, workspace: Path | None = None, dry_run: bool = False) -> list[Path]:
        if workspace is None:
            return [path]
        out: list[Path] = []
        for rule in self._rules:
            if rule.matches(path, self._inspector):
                out.extend(artifact.path for artifact in rule.apply(path, workspace=workspace, inspector=self._inspector, dry_run=dry_run))
        return out

    def restore_with_rollbacks(
        self,
        path: Path,
        *,
        workspace: Path | None = None,
        dry_run: bool = False,
    ) -> tuple[list[Path], list[ApateRollbackRecord]]:
        if workspace is None:
            return [path], []
        out: list[Path] = []
        rollbacks: list[ApateRollbackRecord] = []
        for rule in self._rules:
            if not rule.matches(path, self._inspector):
                continue
            reveal = _load_apate_reveal()
            current = path
            for _ in range(rule._rounds):
                rollback = rule.capture_rollback(current, self._inspector)
                if rollback is None:
                    break
                ok = True
                if not dry_run:
                    ok = reveal(current, quiet=True, in_place=True)
                if not ok:
                    break
                if not dry_run:
                    rollbacks.append(rollback)
                out.append(current)
        return out, rollbacks


class RepeatedApateRestorer(RestorerStrategy):
    def __init__(self, inspector: ArchiveSignatureInspector, *, rounds: int = 3):
        self._inspector = inspector
        self._rules: tuple[RestoreRule, ...] = (
            _ApateRestoreRule(rounds=max(1, rounds)),
        )

    def can_handle(self, path: Path) -> bool:
        return any(rule.matches(path, self._inspector) for rule in self._rules)

    def restore(self, path: Path, *, workspace: Path | None = None, dry_run: bool = False) -> list[Path]:
        if workspace is None:
            return [path]
        out: list[Path] = []
        for rule in self._rules:
            if rule.matches(path, self._inspector):
                out.extend(artifact.path for artifact in rule.apply(path, workspace=workspace, inspector=self._inspector, dry_run=dry_run))
        return out

    def restore_with_rollbacks(
        self,
        path: Path,
        *,
        workspace: Path | None = None,
        dry_run: bool = False,
    ) -> tuple[list[Path], list[ApateRollbackRecord]]:
        return ApateRestorer(self._inspector, rounds=self._rules[0]._rounds).restore_with_rollbacks(
            path,
            workspace=workspace,
            dry_run=dry_run,
        )


class SuffixVariantBuilder(RestorerStrategy):
    def __init__(self, inspector: ArchiveSignatureInspector, rules: list[RenameVariantRule] | None = None):
        self._inspector = inspector
        self._rules = tuple(
            rules
            or [
                _TrimScToZipRule(),
                _TrimEmbeddedArchiveSuffixRule(),
                _SignatureRenameRule(),
                _JpgExeArchiveRule(),
                _UnknownArchiveVariantsRule(),
            ]
        )

    def can_handle(self, path: Path) -> bool:
        return any(rule.matches(path, self._inspector) for rule in self._rules)

    def restore(self, path: Path, *, workspace: Path | None = None, dry_run: bool = False) -> list[Path]:
        _ = (workspace, dry_run)
        return [path]

    def plan_variants(self, path: Path) -> list[RenameVariantPlan]:
        out: list[RenameVariantPlan] = []
        seen: set[Path] = set()
        for rule in self._rules:
            if not rule.matches(path, self._inspector):
                continue
            for plan in rule.plan(path, inspector=self._inspector):
                if plan.target == path or plan.target in seen:
                    continue
                seen.add(plan.target)
                out.append(plan)
        return out


class PassthroughRestorer(RestorerStrategy):
    def can_handle(self, path: Path) -> bool:
        return True

    def restore(self, path: Path, *, workspace: Path | None = None, dry_run: bool = False) -> list[Path]:
        _ = (workspace, dry_run)
        return [path]


class RestorationService:
    def __init__(
        self,
        restorers: list[RestorerStrategy],
        *,
        post_rules: list[PostExtractRule] | None = None,
        inspector: ArchiveSignatureInspector | None = None,
    ):
        self._restorers = restorers
        self._post_rules = tuple(post_rules or [_TrailingScZipRule(), _NestedArchivePostRule()])
        self._inspector = inspector or ArchiveSignatureInspector()

    def restore(self, path: Path, *, workspace: Path | None = None, dry_run: bool = False) -> list[Path]:
        for restorer in self._restorers:
            if not restorer.can_handle(path):
                continue
            restored = restorer.restore(path, workspace=workspace, dry_run=dry_run)
            if not restored:
                return [path]
            out: list[Path] = []
            seen: set[Path] = set()
            for candidate in restored:
                if candidate in seen:
                    continue
                seen.add(candidate)
                out.append(candidate)
            return out or [path]
        return [path]

    def restore_with_rollbacks(
        self,
        path: Path,
        *,
        workspace: Path | None = None,
        dry_run: bool = False,
    ) -> tuple[list[Path], list[ApateRollbackRecord]]:
        for restorer in self._restorers:
            if not restorer.can_handle(path):
                continue
            fn = getattr(restorer, "restore_with_rollbacks", None)
            if callable(fn):
                restored, rollbacks = fn(path, workspace=workspace, dry_run=dry_run)
            else:
                restored = restorer.restore(path, workspace=workspace, dry_run=dry_run)
                rollbacks = []
            if not restored:
                return [path], rollbacks
            out: list[Path] = []
            seen: set[Path] = set()
            for candidate in restored:
                if candidate in seen:
                    continue
                seen.add(candidate)
                out.append(candidate)
            return (out or [path]), rollbacks
        return [path], []

    def rollback_apate(self, records: list[ApateRollbackRecord], *, dry_run: bool = False) -> None:
        if dry_run:
            return
        redisguise = _load_apate_redisguise()
        for record in reversed(records):
            redisguise(record.path, mask_head=record.mask_head, quiet=True)

    def force_apate_restore_with_rollbacks(
        self,
        path: Path,
        *,
        dry_run: bool = False,
    ) -> tuple[Path | None, list[ApateRollbackRecord]]:
        probe = _load_apate_probe()(path)
        if not probe.ok or not probe.mask_head:
            return None, []
        rollback = ApateRollbackRecord(path=path, mask_head=probe.mask_head, rule_name="apate-force-reveal")
        if dry_run:
            return path, []
        reveal = _load_apate_reveal()
        ok = reveal(path, quiet=True, in_place=True)
        if not ok:
            return None, []
        return path, [rollback]

    def identify(self, path: Path) -> ArchiveProbe:
        return self._inspector.probe_path(path)

    def variant_plans(self, path: Path) -> list[RenameVariantPlan]:
        out: list[RenameVariantPlan] = []
        seen: set[Path] = set()
        for restorer in self._restorers:
            plan_fn = getattr(restorer, "plan_variants", None)
            if not callable(plan_fn):
                continue
            for plan in plan_fn(path):
                if plan.target in seen:
                    continue
                seen.add(plan.target)
                out.append(plan)
        return out

    def build_post_extract_candidates(
        self,
        folder: Path,
        *,
        workspace: Path,
        min_archive_bytes: int,
        final_single_bytes: int,
        dry_run: bool = False,
    ) -> list[Path]:
        seen: set[Path] = set()
        out: list[Path] = []
        for rule in self._post_rules:
            for candidate in rule.collect(
                folder,
                workspace=workspace,
                inspector=self._inspector,
                min_archive_bytes=min_archive_bytes,
                final_single_bytes=final_single_bytes,
                dry_run=dry_run,
            ):
                if candidate in seen:
                    continue
                seen.add(candidate)
                out.append(candidate)
        return out
