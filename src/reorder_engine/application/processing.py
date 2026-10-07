from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from reorder_engine.application.errors import EngineError
from reorder_engine.application.models import PlannedPackage, ProcessingOptions
from reorder_engine.application.planning import validate_source
from reorder_engine.infrastructure.archive_safety import ArchiveSafetyInspector, GuardedExtractor, WorkspaceGuard
from reorder_engine.infrastructure.command_runner import ExternalCommandRunner
from reorder_engine.infrastructure.desktop_paths import DesktopPaths
from reorder_engine.infrastructure.file_transaction import FileTransaction, check_cancel, copy_verified
from reorder_engine.infrastructure.job_repository import JobRepository
from reorder_engine.infrastructure.secret_store import SecretStore
from reorder_engine.infrastructure.settings_repository import SettingsRepository
from reorder_engine.services.beta_pipeline import BetaFolderPipeline
from reorder_engine.services.cleaning import DefaultGroupingNormalizer
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


class PackageProcessor:
    """Adapt the existing pipeline on copies; publish and archive real sources centrally."""

    def __init__(self, paths: DesktopPaths, settings: SettingsRepository,
                 repository: JobRepository, secrets: SecretStore):
        self.paths, self.settings = paths, settings
        self.repository, self.secrets = repository, secrets

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
                extractor=extractor, passwords=self.secrets.load(), emit=emit,
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
