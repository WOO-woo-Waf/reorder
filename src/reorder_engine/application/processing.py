from __future__ import annotations

import shutil
import threading
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from reorder_engine.application.errors import EngineError
from reorder_engine.application.models import PlannedPackage, ProcessingOptions
from reorder_engine.application.planning import validate_source
from reorder_engine.domain.models import KeywordLibrary
from reorder_engine.infrastructure.archive_safety import ArchiveSafetyInspector, GuardedExtractor, WorkspaceGuard
from reorder_engine.infrastructure.builtin_defaults import BuiltinDefaults
from reorder_engine.infrastructure.command_runner import ExternalCommandRunner
from reorder_engine.infrastructure.desktop_paths import DesktopPaths
from reorder_engine.infrastructure.file_transaction import FileTransaction, check_cancel, copy_verified
from reorder_engine.infrastructure.job_repository import JobRepository
from reorder_engine.infrastructure.secret_store import SecretStore
from reorder_engine.infrastructure.settings_repository import SettingsRepository
from reorder_engine.services.beta_pipeline import BetaFolderPipeline
from reorder_engine.services.cleaning import CleaningContext, DefaultGroupingNormalizer, KeywordStripCleaner
from reorder_engine.services.config import BetaDeepExtractConfig
from reorder_engine.services.decrypting import DecryptionService, PassthroughDecryptor
from reorder_engine.services.extracting import SevenZipExtractor, UnrarExtractor, BandizipExtractor, ExtractionService
from reorder_engine.services.grouping import DefaultVolumeGroupingStrategy
from reorder_engine.services.restore_ab import RestoreABRestorer
from reorder_engine.services.restoring import ArchiveSignatureInspector, RestorationService, ApateRestorer, EmbeddedArchiveRestorer, SuffixVariantBuilder


@dataclass(frozen=True)
class PackageOutcome:
    state: str
    message: str
    results: list[str]
    error_code: str | None = None


_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)})
_INVALID_NAME_CHARS = frozenset('<>:"/\\|?*')


def _strip_keywords(stem: str, keywords: tuple[str, ...]) -> str:
    cleaner = KeywordStripCleaner(CleaningContext(KeywordLibrary(keywords=tuple(keywords))))
    return cleaner.clean_stem(stem)


def _normalize_output_stem(stem: str) -> str:
    return re.sub(r"\s+", " ", stem).strip()


def _valid_output_name(name: str, stem: str) -> bool:
    if not name or name in {".", ".."}:
        return False
    if any(ch in _INVALID_NAME_CHARS or ord(ch) < 32 for ch in name):
        return False
    if name != name.rstrip(" .") or stem != stem.rstrip(" ."):
        return False
    return stem.upper() not in _WINDOWS_RESERVED_NAMES


def _allocate_output_name(taken: set[str], base: str, suffix: str) -> str | None:
    candidate = base + suffix
    if candidate.casefold() not in taken:
        return candidate
    for index in range(1, 10000):
        candidate = f"{base} ({index}){suffix}"
        if candidate.casefold() not in taken:
            return candidate
    return None


def clean_output_entry_names(root: Path, keywords: tuple[str, ...], *,
                             redact: Callable[[str], str] = lambda text: text,
                             log: Callable[[str], None] = lambda text: None) -> list[tuple[str, str]]:
    """清理发布目录顶层的成品条目名：只改 stem、保留扩展名、不递归、不覆盖。

    空名、非法名与 Windows 保留名保留原名并给出脱敏提示；同名按大小写不敏感
    去重，已存在的条目优先保留原名。只处理传入目录的顶层条目（本包只对
    ``workspace/final`` 调用），不触碰源文件、归档或工作副本原件，也不改动内部层级。
    """
    if not root.is_dir():
        return []
    entries = sorted(root.iterdir(), key=lambda item: item.name)
    taken = {item.name.casefold() for item in entries}
    renamed: list[tuple[str, str]] = []
    for entry in entries:
        original = entry.name
        is_dir = entry.is_dir() and not entry.is_symlink()
        stem, suffix = (original, "") if is_dir else (entry.stem, entry.suffix)
        stripped = _strip_keywords(stem, keywords)
        if stripped == stem:
            continue  # no keyword matched: unrelated names stay untouched
        cleaned = _normalize_output_stem(stripped)
        if not cleaned:
            log(redact(f"保留原名（清理后为空名）: {original}"))
            continue
        if not _valid_output_name(cleaned + suffix, cleaned):
            log(redact(f"保留原名（清理后名称非法）: {original}"))
            continue
        allocated = _allocate_output_name(taken, cleaned, suffix)
        if allocated is None:
            log(redact(f"保留原名（同名条目过多）: {original}"))
            continue
        try:
            entry.rename(root / allocated)
        except OSError:
            log(redact(f"保留原名（改名失败）: {original}"))
            continue
        taken.add(allocated.casefold())
        renamed.append((original, allocated))
        log(redact(f"清理顶层条目: {original} -> {allocated}"))
    return renamed


class PackageProcessor:
    """Adapt the existing pipeline on copies; publish and archive real sources centrally."""

    def __init__(self, paths: DesktopPaths, settings: SettingsRepository,
                 repository: JobRepository, secrets: SecretStore,
                 *, builtin: BuiltinDefaults | None = None):
        self.paths, self.settings = paths, settings
        self.repository, self.secrets = repository, secrets
        self.builtin = builtin if builtin is not None else BuiltinDefaults(paths.app_root)

    def runtime_passwords(self, options: ProcessingOptions) -> tuple[str, ...]:
        """私有用户库优先，其后按内置开关拼接默认库并保持有序去重。"""
        private = self.secrets.load()
        if not options.use_builtin_passwords:
            return private
        return tuple(dict.fromkeys((*private, *self.builtin.passwords)))

    def apply_builtin_keyword_cleaning(self, workspace: Path, options: ProcessingOptions,
                                       state: str, *, log: Callable[[str], None]) -> None:
        """仅显式开启且结果为成功/部分时，清理 final 顶层成品条目名；不碰 error_files。"""
        if not options.clean_builtin_keywords or state not in {"succeeded", "partial"}:
            return
        clean_output_entry_names(workspace / "final", self.builtin.keywords,
                                 redact=self.secrets.redact, log=log)

    def process(self, package: PlannedPackage, options: ProcessingOptions, *,
                job_id: str, output_root: Path, cancel: threading.Event,
                progress: Callable[[str], None], log: Callable[[str], None]) -> PackageOutcome:
        check_cancel(cancel)
        seven_zip = self.settings.resolve_tool("seven_zip")
        if not seven_zip:
            raise EngineError("TOOL_MISSING", "没有找到 7-Zip，请在设置中选择 7z/7zz。")
        workspace = self.paths.work_root / job_id / package.package_id
        workspace.mkdir(parents=True, exist_ok=False)
        output_root.mkdir(parents=True, exist_ok=True)
        source_bytes = sum(member.size for member in package.members)
        if (shutil.disk_usage(workspace).free < source_bytes * 2 + 64 * 1024 * 1024 or
                shutil.disk_usage(output_root).free < source_bytes + 64 * 1024 * 1024):
            raise EngineError("DISK_FULL", "准备副本和归档需要的可用空间不足。")
        try:
            progress("preparing")
            for member in package.members:
                validate_source(member)
                copy_verified(Path(member.path), workspace / Path(member.path).name, cancel_event=cancel)
            quota = options.max_output_gb * 1024 ** 3
            guard = WorkspaceGuard(workspace, byte_limit=source_bytes * 4 + quota)
            runner = ExternalCommandRunner(encoding="utf-8", cancel_event=cancel,
                guard=guard.check, timeout_sec=options.tool_timeout_sec, line_sink=log)
            safety = ArchiveSafetyInspector(seven_zip, runner, quota)
            delegates = [SevenZipExtractor(runner, exe=seven_zip)]
            for key, cls in (("unrar", UnrarExtractor), ("bandizip", BandizipExtractor)):
                tool = self.settings.resolve_tool(key)
                if tool:
                    delegates.append(cls(runner, exe=tool))
            extractor = ExtractionService([GuardedExtractor(d, safety, guard) for d in delegates])
            inspector = ArchiveSignatureInspector()
            restore = RestorationService([RestoreABRestorer(), ApateRestorer(inspector, rounds=3),
                EmbeddedArchiveRestorer(inspector), SuffixVariantBuilder(inspector)], inspector=inspector)

            def emit(message: str) -> None:
                check_cancel(cancel)
                guard.check()
                log(message)

            pipeline = BetaFolderPipeline(folder=workspace,
                decrypt_service=DecryptionService([PassthroughDecryptor()]),
                restore_service=restore,
                grouper=DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()),
                extractor=extractor, passwords=self.runtime_passwords(options), emit=emit,
                deep_extract=BetaDeepExtractConfig(options.deep_extract, options.max_depth,
                    options.min_archive_mb, options.final_single_mb),
                preserve_payload_names=options.preserve_payload_names,
                archive_min_mb=options.min_archive_mb)
            progress("extracting")
            result = pipeline.run()
            check_cancel(cancel)
            guard.check(force=True)
            if len(result.packages) != 1:
                raise EngineError("GROUP_MISMATCH", "副本分组与处理计划不同，原件保留。")
            outcome = result.packages[0]
            transaction = FileTransaction(self.repository, job_id=job_id,
                package_id=package.package_id, output_root=output_root)
            self.apply_builtin_keyword_cleaning(workspace, options, outcome.state, log=log)
            progress("publishing")
            # No cancellation inside this file transaction: finish to a known boundary.
            results: list[str] = []
            for name in (("final", "error_files", "deferred_volumes") if outcome.state in {"succeeded", "partial"} else ()):
                results.extend(transaction.publish_children(workspace / name, output_root / name))
            progress("archiving")
            if outcome.state in {"succeeded", "partial"}:
                destination = output_root / "success" / "archives"
            elif outcome.state == "deferred":
                destination = output_root / "deferred_volumes" / package.package_id
            else:
                destination = output_root / "error_files" / (outcome.category or "unknown")
            results.extend(transaction.route_sources(package, destination))
            # Tool failures usually finish with the actionable error line.
            return PackageOutcome(outcome.state, self.secrets.redact(outcome.message)[-2000:], results)
        finally:
            # Journaled actions may need these files for manual recovery.
            incomplete = any(a["job_id"] == job_id and a["package_id"] == package.package_id
                             for a in self.repository.incomplete_actions())
            if not options.keep_workspace and not incomplete:
                shutil.rmtree(workspace, ignore_errors=True)
