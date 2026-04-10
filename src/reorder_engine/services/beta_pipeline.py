from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from reorder_engine.domain.models import ArchiveKind, ArchiveProbe, ExtractionRequest, ExtractionResult, VolumeSet
from reorder_engine.interfaces.grouping import VolumeGroupingStrategy
from reorder_engine.services.archive_naming import split_archive_name
from reorder_engine.services.config import BetaDeepExtractConfig
from reorder_engine.services.decrypting import DecryptionService
from reorder_engine.services.extracting import ExtractionService
from reorder_engine.services.flattening import deepest_wrapper_dir
from reorder_engine.services.restoring import RestorationService


@dataclass(frozen=True)
class BetaRunResult:
    ok_count: int
    fail_count: int
    total: int


class BetaFolderPipeline:
    def __init__(
        self,
        *,
        folder: Path,
        decrypt_service: DecryptionService,
        restore_service: RestorationService,
        grouper: VolumeGroupingStrategy,
        extractor: ExtractionService,
        passwords: tuple[str, ...],
        exclude_names: set[str] | None = None,
        exclude_exts: set[str] | None = None,
        log_passwords: bool = False,
        deep_extract: BetaDeepExtractConfig | None = None,
        emit: Callable[[str], None] | None = None,
        duplicates_dir_name: str = "_duplicates",
        preserve_payload_names: bool = True,
        path_compress: bool = True,
        archive_min_mb: int = 100,
    ):
        self._folder = folder
        self._decrypt = decrypt_service
        self._restore = restore_service
        self._grouper = grouper
        self._extractor = extractor
        self._passwords = passwords
        self._exclude_names = exclude_names or {"config.json"}
        self._exclude_exts = {e.lower() for e in (exclude_exts or {".bat", ".cmd", ".ps1", ".py", ".json", ".md", ".log"})}
        self._log_passwords = bool(log_passwords)
        self._deep = deep_extract or BetaDeepExtractConfig(enabled=False, max_depth=4, min_archive_mb=100, final_single_mb=200)
        self._emit = emit or (lambda s: print(s))
        self._duplicates_dir_name = duplicates_dir_name or "_duplicates"
        self._preserve_payload_names = bool(preserve_payload_names)
        self._path_compress = bool(path_compress)
        self._archive_min_mb = max(1, int(archive_min_mb))

    def run(self, *, dry_run: bool = False) -> BetaRunResult:
        success_dir = self._folder / "success"
        archives_dir = success_dir / "archives"
        intermediate_dir = self._folder / "intermediate"
        final_root = self._folder / "final"
        failed_dir = self._folder / "failed"
        if not dry_run:
            archives_dir.mkdir(parents=True, exist_ok=True)
            intermediate_dir.mkdir(parents=True, exist_ok=True)
            final_root.mkdir(parents=True, exist_ok=True)
            failed_dir.mkdir(parents=True, exist_ok=True)

        all_files = [p for p in self._folder.glob("*") if p.is_file()]
        all_files = [p for p in all_files if p.name not in self._exclude_names and p.suffix.lower() not in self._exclude_exts]
        volume_sets = self._grouper.group(all_files)
        self._emit(f"SCAN: folder={self._folder} files={len(all_files)} groups={len(volume_sets)} dry_run={dry_run}")

        ok = 0
        fail = 0
        for volume_set in volume_sets:
            if self._is_in_result_dirs(volume_set, {success_dir, intermediate_dir, final_root, failed_dir}):
                continue

            package_name = self._package_name(volume_set.entry.name)
            package_root = intermediate_dir / package_name
            copied_vs = self._copy_volume_set_to_workspace(volume_set, package_root / "input", dry_run=dry_run)
            candidates = self._entry_candidates(copied_vs, package_root / "variants" / "L1", dry_run=dry_run)
            result, layer1_dir = self._extract_first_success(copied_vs, candidates, package_root / "L1", dry_run=dry_run)

            if result.ok and layer1_dir is not None:
                deep_ok, final_dir, message = self._continue_after_extract(
                    package_name=package_name,
                    package_root=package_root,
                    start_dir=layer1_dir,
                    final_root=final_root,
                    dry_run=dry_run,
                )
                if deep_ok:
                    ok += 1
                    self._move_original_members(volume_set, archives_dir, package_name=package_name, dry_run=dry_run)
                    if message:
                        self._emit(f"FINAL: {message}")
                    if final_dir is not None:
                        self._emit(f"FINAL-DIR: {final_dir}")
                    continue

            if self._is_missing_volume(result.message):
                self._emit(f"MISSING-VOLUME: keep-in-place entry={volume_set.entry.name}")
                continue

            fail += 1
            self._move_original_members(volume_set, failed_dir, package_name=package_name, dry_run=dry_run)
            if result.message:
                self._emit(f"FAIL: {self._summarize_message(result.message)}")

        if not dry_run:
            self._remove_empty_dirs(intermediate_dir)
            self._remove_empty_dirs(final_root)
            self._remove_empty_dirs(failed_dir)
            self._remove_empty_dirs(success_dir)
        return BetaRunResult(ok_count=ok, fail_count=fail, total=ok + fail)

    def _entry_candidates(self, vs: VolumeSet, workspace: Path, *, dry_run: bool) -> list[Path]:
        if len(vs.members) > 1:
            return [vs.entry]
        out: list[Path] = []
        seen: set[Path] = set()
        prepared = self._decrypt.prepare(vs.entry, workspace=workspace, dry_run=dry_run)
        for item in prepared:
            probe = self._restore.identify(item)
            self._emit(f"IDENTIFY: file={item.name} kind={probe.kind.value} suffix={probe.archive_suffix or '-'} reason={probe.reason or '-'}")
            for candidate in self._candidate_chain(item, probe=probe, workspace=workspace, dry_run=dry_run):
                if candidate in seen:
                    continue
                seen.add(candidate)
                out.append(candidate)
        return out or [vs.entry]

    def _extract_first_success(
        self,
        vs: VolumeSet,
        candidates: list[Path],
        layer_root: Path,
        *,
        dry_run: bool,
    ) -> tuple[ExtractionResult, Path | None]:
        last = ExtractionResult(volume_set=vs, ok=False, tool="none", message="No candidate executed")
        for index, candidate in enumerate(candidates, start=1):
            attempt_dir = layer_root / f"attempt_{index}"
            probe = self._restore.identify(candidate)
            request_vs = vs if len(vs.members) > 1 else VolumeSet(entry=candidate, members=(candidate,), group_key=vs.group_key)
            req = ExtractionRequest(volume_set=request_vs, output_dir=attempt_dir, passwords=self._passwords)
            res = self._extractor.extract_one(req, preference="auto", probe=probe, dry_run=dry_run)
            last = res
            self._emit_extract_result("EXTRACT", request_vs.entry.name, res)
            if res.ok:
                return res, attempt_dir
        return last, None

    def _continue_after_extract(
        self,
        *,
        package_name: str,
        package_root: Path,
        start_dir: Path,
        final_root: Path,
        dry_run: bool,
    ) -> tuple[bool, Path | None, str | None]:
        if not self._deep.enabled:
            final_dir = self._promote_final_dir(start_dir, final_root, package_name=package_name, dry_run=dry_run)
            return True, final_dir, "depth=1"

        max_depth = max(1, int(self._deep.max_depth))
        min_archive_bytes = max(self._archive_min_mb, int(self._deep.min_archive_mb)) * 1024 * 1024
        final_single_bytes = max(1, int(self._deep.final_single_mb)) * 1024 * 1024
        current_dir = start_dir
        for depth in range(1, max_depth + 1):
            reason = self._is_final_output(current_dir)
            if reason is not None:
                final_dir = self._promote_final_dir(current_dir, final_root, package_name=package_name, dry_run=dry_run)
                return True, final_dir, f"depth={depth} reason={reason}"

            candidates = self._nested_candidates(
                current_dir,
                package_root / "variants" / f"L{depth + 1}",
                min_archive_bytes=min_archive_bytes,
                final_single_bytes=final_single_bytes,
                dry_run=dry_run,
            )
            if not candidates:
                if self._has_any_file(current_dir):
                    final_dir = self._promote_final_dir(current_dir, final_root, package_name=package_name, dry_run=dry_run)
                    return True, final_dir, f"depth={depth} reason=no-candidates"
                return False, None, f"depth={depth} no candidates"

            extracted = False
            for index, candidate in enumerate(candidates, start=1):
                target_dir = package_root / f"L{depth + 1}" / self._safe_name(candidate.name)
                probe = self._restore.identify(candidate)
                req = ExtractionRequest(
                    volume_set=VolumeSet(entry=candidate, members=(candidate,), group_key=f"{package_name}:{depth}:{index}"),
                    output_dir=target_dir,
                    passwords=self._passwords,
                )
                res = self._extractor.extract_one(req, preference="auto", probe=probe, dry_run=dry_run)
                self._emit_extract_result("DEEP-EXTRACT", candidate.name, res)
                if res.ok:
                    current_dir = target_dir
                    extracted = True
                    break
            if not extracted:
                if self._has_any_file(current_dir):
                    final_dir = self._promote_final_dir(current_dir, final_root, package_name=package_name, dry_run=dry_run)
                    return True, final_dir, f"depth={depth} reason=candidates-failed"
                return False, None, f"depth={depth} all candidates failed"

        return False, None, f"exceeded max_depth={max_depth}"

    def _nested_candidates(
        self,
        folder: Path,
        workspace: Path,
        *,
        min_archive_bytes: int,
        final_single_bytes: int,
        dry_run: bool,
    ) -> list[Path]:
        base_candidates = self._restore.build_post_extract_candidates(
            folder,
            workspace=workspace,
            min_archive_bytes=min_archive_bytes,
            final_single_bytes=final_single_bytes,
            dry_run=dry_run,
        )
        out: list[Path] = []
        seen: set[Path] = set()
        for candidate in base_candidates:
            probe = self._restore.identify(candidate)
            self._emit(f"IDENTIFY: nested={candidate.name} kind={probe.kind.value} suffix={probe.archive_suffix or '-'} reason={probe.reason or '-'}")
            child_workspace = workspace / self._safe_name(candidate.name)
            for derived in self._candidate_chain(candidate, probe=probe, workspace=child_workspace, dry_run=dry_run):
                if derived in seen:
                    continue
                seen.add(derived)
                out.append(derived)
        return out

    def _candidate_chain(self, path: Path, *, probe: ArchiveProbe, workspace: Path, dry_run: bool) -> list[Path]:
        if probe.kind == ArchiveKind.ARCHIVE:
            return [path]
        if probe.kind == ArchiveKind.APATE:
            restored = self._restore.restore(path, workspace=workspace, dry_run=dry_run)
            return restored or [path]
        restored = self._restore.restore(path, workspace=workspace, dry_run=dry_run)
        return restored or [path]

    def _copy_volume_set_to_workspace(self, vs: VolumeSet, workspace: Path, *, dry_run: bool) -> VolumeSet:
        _ = (workspace, dry_run)
        return vs

    def _promote_final_dir(self, current_dir: Path, final_root: Path, *, package_name: str, dry_run: bool) -> Path:
        leaf = deepest_wrapper_dir(current_dir) if self._path_compress else current_dir
        target_name = leaf.name if self._preserve_payload_names and leaf != current_dir else package_name
        target = final_root / target_name
        if target.exists():
            target = self._duplicate_target(final_root, target_name=target_name, package_name=package_name)
        if dry_run:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        source = leaf if leaf.exists() else current_dir
        shutil.move(str(source), str(target))
        self._remove_empty_dirs(current_dir)
        return target

    def _duplicate_target(self, root: Path, *, target_name: str, package_name: str) -> Path:
        duplicate_root = root / self._duplicates_dir_name / package_name
        dst = duplicate_root / target_name
        if not dst.exists():
            return dst
        for index in range(1, 1000):
            candidate_root = duplicate_root / f"copy_{index}"
            dst = candidate_root / target_name
            if not dst.exists():
                return dst
        return duplicate_root / target_name

    def _move_original_members(self, vs: VolumeSet, dest_dir: Path, *, package_name: str, dry_run: bool) -> None:
        for member in vs.members:
            if not member.exists():
                continue
            dst = dest_dir / member.name
            if dst.exists():
                dst = self._duplicate_target(dest_dir, target_name=member.name, package_name=package_name)
            if dry_run:
                self._emit(f"MOVE(dry): {member} -> {dst}")
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(member), str(dst))
            self._emit(f"MOVE: {member.name} -> {dst}")

    def _is_in_result_dirs(self, vs: VolumeSet, dirs: set[Path]) -> bool:
        for member in vs.members:
            for directory in dirs:
                if directory in member.parents:
                    return True
        return False

    def _is_missing_volume(self, message: str | None) -> bool:
        if not message:
            return False
        text = message.lower()
        return "missing volume" in text or "unavailable data" in text

    def _emit_extract_result(self, prefix: str, entry_name: str, result: ExtractionResult) -> None:
        status = "OK" if result.ok else "FAIL"
        pw = f" password={result.password}" if result.ok and self._log_passwords and result.password else ""
        self._emit(f"{prefix}[{status}] entry={entry_name} tool={result.tool}{pw}")
        if result.message:
            self._emit(f"  msg: {self._summarize_message(result.message)}")

    def _is_final_output(self, folder: Path) -> str | None:
        if not folder.exists() or not folder.is_dir():
            return None
        files = [p for p in folder.rglob("*") if p.is_file()]
        dirs = [p for p in folder.rglob("*") if p.is_dir()]
        if not files and not dirs:
            return None
        if len(dirs) >= 10:
            return "many-folders"
        if len(files) >= 80:
            return "many-files"
        video_exts = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"}
        image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
        strong_exts = {".iso", ".exe", ".apk", ".obb", ".pak"}
        video_count = sum(1 for file in files if file.suffix.lower() in video_exts)
        image_count = sum(1 for file in files if file.suffix.lower() in image_exts)
        strong_count = sum(1 for file in files if file.suffix.lower() in strong_exts)
        if video_count >= 1:
            return "has-video"
        if strong_count >= 1:
            return "has-binary"
        if image_count >= 20:
            return "many-images"
        return None

    def _has_any_file(self, root: Path) -> bool:
        try:
            return any(path.is_file() for path in root.rglob("*"))
        except OSError:
            return False

    def _remove_empty_dirs(self, root: Path) -> None:
        if not root.exists() or not root.is_dir():
            return
        dirs = sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
        for directory in dirs:
            try:
                if not any(directory.iterdir()):
                    directory.rmdir()
            except OSError:
                continue
        try:
            if root.exists() and root.is_dir() and not any(root.iterdir()):
                root.rmdir()
        except OSError:
            pass

    def _safe_name(self, name: str) -> str:
        return (name.strip().replace("/", "_").replace("\\", "_")) or "_item"

    def _summarize_message(self, message: str) -> str:
        lines = message.splitlines()
        if len(lines) <= 18:
            return message.strip()
        return "\n".join(lines[:18]).strip() + "\n... (truncated)"

    def _package_name(self, entry_name: str) -> str:
        parts = split_archive_name(entry_name)
        return (parts.base.strip() or Path(entry_name).stem).replace("/", "_").replace("\\", "_")
