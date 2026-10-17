from __future__ import annotations

import shutil
import re
import fnmatch
import uuid
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Callable

from reorder_engine.domain.models import ArchiveKind, ArchiveProbe, ExtractionRequest, ExtractionResult, VolumeSet
from reorder_engine.application.errors import EngineError, ProcessingCancelled
from reorder_engine.interfaces.grouping import VolumeGroupingStrategy
from reorder_engine.services.archive_naming import split_archive_name
from reorder_engine.services.cleaning import SafeRenamer
from reorder_engine.services.config import BetaDeepExtractConfig
from reorder_engine.services.decrypting import DecryptionService
from reorder_engine.services.extracting import ExtractionService
from reorder_engine.services.flattening import deepest_wrapper_dir
from reorder_engine.services.rename_session import RenameSession
from reorder_engine.infrastructure.tools import contains_nonempty_regular_file
from reorder_engine.services.restoring import RestorationService
from reorder_engine.services.restore_ab import RestoreABError


@dataclass(frozen=True)
class BetaPackageResult:
    entry: Path
    state: str
    category: str | None = None
    message: str = ""


@dataclass(frozen=True)
class BetaRunResult:
    ok_count: int
    fail_count: int
    total: int
    packages: tuple[BetaPackageResult, ...] = ()


@dataclass(frozen=True)
class CandidateAttempt:
    path: Path
    rollbacks: tuple = ()
    method: str = "direct"
    source: Path | None = None


@dataclass
class _VolumeRevealSession:
    """In-place disguise reveals applied to a whole volume set before extraction."""

    records: tuple
    service: object

    def rollback_best_effort(self, *, dry_run: bool) -> bool:
        if dry_run or not self.records:
            return True
        try:
            self.service.rollback_apate(list(self.records), dry_run=dry_run)
        except Exception:
            return False
        return True


PARTIAL_WARNING = "未完成，已保留部分内容，请检查工具错误。"


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
        cleanup_dynamic_libs: bool = False,
        dynamic_lib_patterns: tuple[str, ...] = (),
        volume_sets: tuple[VolumeSet, ...] | None = None,
        manage_originals: bool = True,
        renamer: SafeRenamer | None = None,
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
        self._cleanup_dynamic_libs = bool(cleanup_dynamic_libs)
        self._dynamic_lib_patterns = tuple(pattern for pattern in dynamic_lib_patterns if pattern)
        self._produced_files: set[Path] = set()
        self._renamer = renamer or SafeRenamer()
        self._volume_sets = volume_sets
        self._manage_originals = manage_originals

    def run(self, *, dry_run: bool = False) -> BetaRunResult:
        success_dir = self._folder / "success"
        archives_dir = success_dir / "archives"
        intermediate_dir = self._folder / "intermediate"
        final_root = self._folder / "final"
        error_dir = self._folder / "error_files"
        deferred_volume_dir = self._folder / "deferred_volumes"
        incomplete_dir = self._folder / "incomplete"
        incomplete_outputs_dir = incomplete_dir / "outputs"
        incomplete_archives_dir = incomplete_dir / "archives"
        result_dirs = (success_dir, archives_dir, intermediate_dir, final_root, error_dir, deferred_volume_dir, incomplete_dir, incomplete_outputs_dir, incomplete_archives_dir)
        if any(not path.resolve().is_relative_to(self._folder.resolve()) for path in result_dirs):
            raise EngineError("UNSAFE_WORKSPACE", "结果目录指向工作文件夹外部，请调整目录后重试。")
        created_result_dirs = [path for path in result_dirs if not path.exists()]
        if not dry_run:
            archives_dir.mkdir(parents=True, exist_ok=True)
            intermediate_dir.mkdir(parents=True, exist_ok=True)
            final_root.mkdir(parents=True, exist_ok=True)
            error_dir.mkdir(parents=True, exist_ok=True)
            deferred_volume_dir.mkdir(parents=True, exist_ok=True)
            incomplete_outputs_dir.mkdir(parents=True, exist_ok=True)
            incomplete_archives_dir.mkdir(parents=True, exist_ok=True)

        if self._volume_sets is None:
            all_files = [p for p in self._folder.glob("*") if p.is_file()]
            all_files = [p for p in all_files if p.name not in self._exclude_names and p.suffix.lower() not in self._exclude_exts]
            volume_sets = self._grouper.group(all_files)
        else:
            # The desktop selects explicit original groups. The folder is only
            # the output workspace; no full archive staging copy is required.
            volume_sets = self._volume_sets
            all_files = [p for group in volume_sets for p in group.members]
        self._emit(f"SCAN: folder={self._folder} files={len(all_files)} groups={len(volume_sets)} dry_run={dry_run}")

        ok = 0
        fail = 0
        outcomes: list[BetaPackageResult] = []
        owned_package_roots: list[Path] = []
        for volume_set in volume_sets:
            if self._is_in_result_dirs(volume_set, {success_dir, intermediate_dir, final_root, error_dir, deferred_volume_dir, incomplete_dir}):
                continue

            package_name = self._package_name(volume_set.entry.name)
            package_root = self._allocate_package_workspace(intermediate_dir, package_name, dry_run=dry_run)
            if not dry_run:
                owned_package_roots.append(package_root)
            candidates = self._entry_candidates(volume_set, package_root / "variants" / "L1", dry_run=dry_run)
            result, layer1_dir = self._extract_first_success(
                volume_set,
                candidates,
                package_root / "L1",
                dry_run=dry_run,
                preferred_password=None,
            )
            source_volume_set = result.source_volume_set or result.volume_set

            if self._result_partial(result) and layer1_dir is not None and self._has_nonempty_file(layer1_dir):
                moved = self._move_partial_outputs_to_incomplete(
                    layer1_dir, incomplete_outputs_dir, package_name=package_name, dry_run=dry_run
                )
                self._move_original_members(source_volume_set, incomplete_archives_dir, package_name=package_name, dry_run=dry_run)
                fail += 1
                outcomes.append(
                    BetaPackageResult(
                        volume_set.entry,
                        "partial",
                        None,
                        self._partial_result_message(result.message),
                    )
                )
                if result.message:
                    self._emit(f"EXTRACT[PARTIAL]: entry={volume_set.entry.name} {self._summarize_message(result.message)}")
                if moved is not None:
                    self._emit(f"INCOMPLETE-DIR: {moved}")
                continue

            if self._result_complete(result) and layer1_dir is not None:
                deep_ok, final_dir, message = self._continue_after_extract(
                    package_name=package_name,
                    package_root=package_root,
                    start_dir=layer1_dir,
                    final_root=final_root,
                    dry_run=dry_run,
                    preferred_password=result.password,
                )
                if deep_ok:
                    inner_partial = bool(message and message.startswith(PARTIAL_WARNING)) and final_dir is not None
                    deferred_only = "deferred-volume" in (message or "")
                    if inner_partial:
                        moved = self._move_partial_outputs_to_incomplete(
                            final_dir, incomplete_outputs_dir, package_name=package_name, dry_run=dry_run
                        )
                        fail += 1
                        outcomes.append(BetaPackageResult(volume_set.entry, "partial", None, message or ""))
                        self._move_original_members(source_volume_set, incomplete_archives_dir, package_name=package_name, dry_run=dry_run)
                        self._emit(f"EXTRACT[PARTIAL]: entry={volume_set.entry.name} {message}")
                        if moved is not None:
                            self._emit(f"INCOMPLETE-DIR: {moved}")
                    elif deferred_only:
                        fail += 1
                        outcomes.append(BetaPackageResult(volume_set.entry, "partial", "deferred_volume", message or ""))
                        self._move_original_members(source_volume_set, incomplete_archives_dir, package_name=package_name, dry_run=dry_run)
                    else:
                        ok += 1
                        outcomes.append(BetaPackageResult(volume_set.entry, "succeeded", message=message or ""))
                        self._move_original_members(source_volume_set, archives_dir, package_name=package_name, dry_run=dry_run)
                    if message:
                        self._emit(f"FINAL: {message}")
                    if final_dir is not None:
                        self._emit(f"FINAL-DIR: {final_dir}")
                    continue

            if not result.ok and layer1_dir is not None and self._has_nonempty_file(layer1_dir):
                moved_dir = self._move_partial_outputs_to_incomplete(
                    layer1_dir, incomplete_outputs_dir, package_name=package_name, dry_run=dry_run
                )
                self._move_original_members(source_volume_set, incomplete_archives_dir, package_name=package_name, dry_run=dry_run)
                if result.message:
                    self._emit(f"EXTRACT[PARTIAL]: entry={volume_set.entry.name} {self._summarize_message(result.message)}")
                if moved_dir is not None:
                    self._emit(f"INCOMPLETE-DIR: {moved_dir}")
                fail += 1
                outcomes.append(
                    BetaPackageResult(
                        volume_set.entry,
                        "partial",
                        None,
                        self._partial_result_message(result.message),
                    )
                )
                continue

            if self._is_missing_volume(result.message):
                moved = self._defer_missing_volume_set(source_volume_set, dry_run=dry_run)
                if moved:
                    ok += 1
                    outcomes.append(BetaPackageResult(volume_set.entry, "deferred", "missing_volume", result.message or ""))
                    self._emit(
                        f"MISSING-VOLUME: deferred entry={volume_set.entry.name} files={len(moved)}"
                    )
                    continue
                self._emit(f"MISSING-VOLUME: keep-in-place entry={volume_set.entry.name}")
                outcomes.append(BetaPackageResult(volume_set.entry, "deferred", "missing_volume", result.message or ""))
                continue

            fail += 1
            fail_category = self._failure_category(result, source_volume_set.entry)
            outcomes.append(BetaPackageResult(volume_set.entry, "failed", fail_category, result.message or ""))
            self._move_original_members(source_volume_set, error_dir / fail_category, package_name=package_name, dry_run=dry_run)
            if result.message:
                self._emit(f"ERROR-FILE[{fail_category}]: {self._summarize_message(result.message)}")

        if not dry_run:
            for package_root in owned_package_roots:
                self._remove_empty_dirs(package_root)
            # Reuse existing result roots without cleaning any of their contents,
            # including empty user directories. Only remove roots created by us.
            for path in reversed(created_result_dirs):
                try:
                    path.rmdir()
                except OSError:
                    pass
        return BetaRunResult(ok_count=ok, fail_count=fail, total=ok + fail, packages=tuple(outcomes))

    def _allocate_package_workspace(self, intermediate_dir: Path, package_name: str, *, dry_run: bool) -> Path:
        """Reserve a fresh per-package workspace without reusing prior attempts."""
        candidate = intermediate_dir / package_name
        while True:
            if dry_run:
                if not candidate.exists() and not candidate.is_symlink():
                    return candidate
            else:
                try:
                    candidate.mkdir()
                    return candidate
                except FileExistsError:
                    pass
            candidate = intermediate_dir / f"{package_name}__run_{uuid.uuid4().hex}"

    def _entry_candidates(self, vs: VolumeSet, workspace: Path, *, dry_run: bool) -> list[CandidateAttempt]:
        if len(vs.members) > 1:
            return [CandidateAttempt(vs.entry)]
        out: list[CandidateAttempt] = []
        seen: set[Path] = set()
        prepared = self._decrypt.prepare(vs.entry, workspace=workspace, dry_run=dry_run)
        for item in prepared:
            probe = self._restore.identify(item)
            self._emit(f"IDENTIFY: file={item.name} kind={probe.kind.value} suffix={probe.archive_suffix or '-'} reason={probe.reason or '-'}")
            for attempt in self._candidate_chain(item, probe=probe, workspace=workspace, dry_run=dry_run):
                candidate = attempt.path
                if candidate in seen:
                    continue
                seen.add(candidate)
                self._emit(
                    f"CANDIDATE: source={item.name} entry={candidate.name} method={attempt.method}"
                )
                out.append(attempt)
        return out or [CandidateAttempt(vs.entry)]

    def _extract_first_success(
        self,
        vs: VolumeSet,
        candidates: list[CandidateAttempt],
        layer_root: Path,
        *,
        dry_run: bool,
        preferred_password: str | None = None,
    ) -> tuple[ExtractionResult, Path | None]:
        if len(vs.members) > 1:
            result, out_dir = self._extract_volume_set_first_success(
                vs, layer_root, dry_run=dry_run, preferred_password=preferred_password
            )
            # Successful/partial normalized volumes retain their new names;
            # empty failures have already rolled those names back.
            source_vs = result.volume_set if (result.ok or self._result_partial(result) or out_dir is not None) else vs
            return replace(result, source_volume_set=source_vs), out_dir

        last = ExtractionResult(volume_set=vs, ok=False, tool="none", message="No candidate executed")
        partial_holder = layer_root / "_partial_candidate"
        best_partial: ExtractionResult | None = None
        for index, attempt in enumerate(candidates, start=1):
            candidate = attempt.path
            request_vs = VolumeSet(entry=candidate, members=(candidate,), group_key=vs.group_key)
            res, out_dir = self._extract_with_variant_attempts(
                request_vs,
                layer_root / f"attempt_{index}",
                dry_run=dry_run,
                preferred_password=preferred_password,
                method=attempt.method,
            )
            # A separate preparation/restoration copy does not replace the
            # original. In-place suffix renames, however, do replace its path.
            source_vs = vs if candidate != vs.entry else res.volume_set
            res = replace(res, source_volume_set=source_vs)
            last = res
            if self._result_complete(res):
                self._discard_partial_output(partial_holder)
                return res, out_dir
            if self._result_partial(res) and out_dir is not None and self._has_nonempty_file(out_dir):
                best_partial = res
                self._retain_partial_output(out_dir, partial_holder)
            elif self._result_partial(res) and self._has_nonempty_file(partial_holder):
                best_partial = res
            elif out_dir is not None and self._has_nonempty_file(out_dir):
                self._discard_partial_output(partial_holder)
                return res, out_dir
            self._rollback_attempt(attempt, dry_run=dry_run)
            if self._result_stop_all(res):
                # A serious no-output failure already classified as stop_all
                # (missing volume / permission / disk-write / space /
                # exit 7/8/124/255) cannot be fixed by a forced Apate
                # rewrite or a later candidate; escalating would edit the
                # selected original for nothing.
                if best_partial is not None:
                    return replace(best_partial, source_volume_set=source_vs), partial_holder
                return res, None

        force_attempt = self._force_apate_attempt_if_useful(vs.entry, dry_run=dry_run)
        if force_attempt is not None:
            request_vs = VolumeSet(entry=force_attempt.path, members=(force_attempt.path,), group_key=vs.group_key)
            res, out_dir = self._extract_with_variant_attempts(
                request_vs,
                layer_root / f"attempt_{len(candidates) + 1}_force_apate",
                dry_run=dry_run,
                preferred_password=preferred_password,
                method=force_attempt.method,
            )
            source_vs = vs if force_attempt.path != vs.entry else res.volume_set
            res = replace(res, source_volume_set=source_vs)
            last = res
            if self._result_complete(res):
                self._discard_partial_output(partial_holder)
                return res, out_dir
            if self._result_partial(res) and out_dir is not None and self._has_nonempty_file(out_dir):
                best_partial = res
                self._retain_partial_output(out_dir, partial_holder)
            elif out_dir is not None and self._has_nonempty_file(out_dir):
                self._discard_partial_output(partial_holder)
                return res, out_dir
            self._rollback_attempt(force_attempt, dry_run=dry_run)
        if best_partial is not None:
            return replace(best_partial, source_volume_set=vs), partial_holder
        return replace(last, source_volume_set=vs), None

    def _extract_volume_set_first_success(
        self,
        vs: VolumeSet,
        layer_root: Path,
        *,
        dry_run: bool,
        preferred_password: str | None = None,
    ) -> tuple[ExtractionResult, Path | None]:
        # Read and in-place suffix normalization first; only a failure without
        # usable output escalates to in-place disguise restoration of the set.
        result, out_dir = self._extract_volume_set_body(
            vs,
            layer_root,
            dry_run=dry_run,
            preferred_password=preferred_password,
        )
        if self._result_complete(result):
            return result, out_dir
        if out_dir is not None and self._has_nonempty_file(out_dir) and not self._result_partial(result):
            return result, out_dir
        if self._result_stop_all(result):
            return result, out_dir if out_dir is not None and self._has_nonempty_file(out_dir) else None

        reveal = self._reveal_disguised_volume_members(vs, dry_run=dry_run)
        if reveal is None:
            if self._result_partial(result):
                return result, out_dir
            return result, None
        reveal_result, reveal_out_dir = self._extract_volume_set_body(
            vs,
            layer_root / "attempt_2_revealed",
            dry_run=dry_run,
            preferred_password=preferred_password,
        )
        if self._result_complete(reveal_result):
            self._discard_partial_output(out_dir)
            return reveal_result, reveal_out_dir
        if self._result_partial(reveal_result) and reveal_out_dir is not None and self._has_nonempty_file(reveal_out_dir):
            self._discard_partial_output(out_dir)
            return reveal_result, reveal_out_dir
        if out_dir is not None and self._has_nonempty_file(out_dir):
            return result, out_dir
        if not reveal.rollback_best_effort(dry_run=dry_run):
            self._emit(f"VOLUME-REVEAL-ROLLBACK-FAILED: entry={vs.entry.name}")
            raise RuntimeError("分卷伪装回滚失败，未继续归档，请检查工作文件。")
        self._emit(f"VOLUME-REVEAL-ROLLBACK: entry={vs.entry.name}")
        if self._result_partial(result):
            return result, out_dir
        return reveal_result, None

    def _reveal_disguised_volume_members(
        self,
        vs: VolumeSet,
        *,
        dry_run: bool,
    ) -> "_VolumeRevealSession | None":
        """Reveal Apate/embedded disguise on every volume before extraction.

        A multi-volume set is extracted as a group, so the per-member disguise
        restoration the single-candidate chain already performs must be applied
        to all members first. Only the first volume carries an archive head, so a
        confirmed disguise on the entry is extended to sibling volumes that carry
        a valid Apate indicator of their own. Reveals are in place and rolled
        back when the whole set fails.
        """

        records: list[object] = []
        entry = vs.entry
        confirmed = False
        if entry.exists():
            probe = self._restore.identify(entry)
            if probe.kind in (ArchiveKind.APATE, ArchiveKind.EMBEDDED):
                _restored, rollbacks = self._restore.restore_with_rollbacks(
                    entry,
                    workspace=entry.parent,
                    dry_run=dry_run,
                    preferred_kind=probe.kind,
                )
                if rollbacks:
                    records.extend(rollbacks)
                    # Only an Apate disguise is expected to repeat on the later
                    # volumes; an embedded cover is confined to the entry.
                    confirmed = probe.kind is ArchiveKind.APATE
                    self._emit(f"VOLUME-REVEAL: entry={entry.name} kind={probe.kind.value}")
        if confirmed:
            for member in vs.members:
                if member == entry or not member.exists():
                    continue
                forced = self._force_reveal_volume_member(member, dry_run=dry_run)
                if forced is not None:
                    records.append(forced)
                    self._emit(f"VOLUME-REVEAL: entry={member.name} kind=apate-sibling")
        if not records:
            return None
        return _VolumeRevealSession(tuple(records), self._restore)

    def _force_reveal_volume_member(self, member: Path, *, dry_run: bool) -> object | None:
        fn = getattr(self._restore, "force_apate_restore_with_rollbacks", None)
        if not callable(fn):
            return None
        restored, rollbacks = fn(member, dry_run=dry_run)
        if restored is None or not rollbacks:
            return None
        return rollbacks[0]

    def _extract_volume_set_body(
        self,
        vs: VolumeSet,
        layer_root: Path,
        *,
        dry_run: bool,
        preferred_password: str | None = None,
    ) -> tuple[ExtractionResult, Path | None]:
        partial_holder = layer_root / "_partial_candidate"
        best_partial: ExtractionResult | None = None
        disguised_split_normalized = self._normalize_disguised_split_suffix_volume_set(vs, dry_run=dry_run)
        if disguised_split_normalized is not None:
            normalized_vs, session = disguised_split_normalized
            self._emit(
                f"VOLUME-RENAME-TRY: {vs.entry.name} -> {normalized_vs.entry.name} rule=disguised-split-suffix"
            )
            result, normalized_out_dir = self._extract_with_variant_attempts(
                normalized_vs,
                layer_root / "attempt_1_disguised_split_renamed",
                dry_run=dry_run,
                preferred_password=preferred_password,
            )
            if self._result_complete(result):
                self._discard_partial_output(partial_holder)
                return result, normalized_out_dir
            if normalized_out_dir is not None and self._has_nonempty_file(normalized_out_dir):
                best_partial = result
                self._retain_partial_output(normalized_out_dir, partial_holder)

            session.rollback_best_effort(dry_run=dry_run)
            self._emit(
                f"VOLUME-RENAME-ROLLBACK: {normalized_vs.entry.name} -> {vs.entry.name} rule=disguised-split-suffix"
            )
            if self._result_stop_all(result):
                if best_partial is not None:
                    return replace(result, volume_set=vs, partial_output=True), partial_holder
                return replace(result, volume_set=vs), None

        sfx_normalized = self._normalize_sfx_volume_set(vs, dry_run=dry_run)
        if sfx_normalized is not None:
            normalized_vs, session = sfx_normalized
            self._emit(
                f"VOLUME-RENAME-TRY: {vs.entry.name} -> {normalized_vs.entry.name} rule=sfx-volume"
            )
            result, normalized_out_dir = self._extract_with_variant_attempts(
                normalized_vs,
                layer_root / "attempt_1_sfx_renamed",
                dry_run=dry_run,
                preferred_password=preferred_password,
            )
            if self._result_complete(result):
                self._discard_partial_output(partial_holder)
                return result, normalized_out_dir
            if normalized_out_dir is not None and self._has_nonempty_file(normalized_out_dir):
                best_partial = result
                self._retain_partial_output(normalized_out_dir, partial_holder)

            session.rollback_best_effort(dry_run=dry_run)
            self._emit(
                f"VOLUME-RENAME-ROLLBACK: {normalized_vs.entry.name} -> {vs.entry.name} rule=sfx-volume"
            )
            if self._result_stop_all(result):
                if best_partial is not None:
                    return replace(result, volume_set=vs, partial_output=True), partial_holder
                return replace(result, volume_set=vs), None

        numbered_tail_normalized = self._normalize_numbered_tail_volume_set(vs, dry_run=dry_run)
        if numbered_tail_normalized is not None:
            normalized_vs, session = numbered_tail_normalized
            self._emit(
                f"VOLUME-RENAME-TRY: {vs.entry.name} -> {normalized_vs.entry.name} rule=numbered-tail-volume"
            )
            result, normalized_out_dir = self._extract_with_variant_attempts(
                normalized_vs,
                layer_root / "attempt_1_numbered_tail_renamed",
                dry_run=dry_run,
                preferred_password=preferred_password,
            )
            if self._result_complete(result):
                self._discard_partial_output(partial_holder)
                return result, normalized_out_dir
            if normalized_out_dir is not None and self._has_nonempty_file(normalized_out_dir):
                best_partial = result
                self._retain_partial_output(normalized_out_dir, partial_holder)

            session.rollback_best_effort(dry_run=dry_run)
            self._emit(
                f"VOLUME-RENAME-ROLLBACK: {normalized_vs.entry.name} -> {vs.entry.name} rule=numbered-tail-volume"
            )
            if self._result_stop_all(result):
                if best_partial is not None:
                    return replace(result, volume_set=vs, partial_output=True), partial_holder
                return replace(result, volume_set=vs), None

        last, out_dir = self._extract_with_variant_attempts(
            vs,
            layer_root / "attempt_1",
            dry_run=dry_run,
            preferred_password=preferred_password,
        )
        if self._result_complete(last):
            self._discard_partial_output(partial_holder)
            return last, out_dir
        if self._result_partial(last) and out_dir is not None and self._has_nonempty_file(out_dir):
            best_partial = last
            self._retain_partial_output(out_dir, partial_holder)
        elif out_dir is not None and self._has_nonempty_file(out_dir):
            if best_partial is not None:
                best_partial = last
                self._retain_partial_output(out_dir, partial_holder)
                return replace(last, volume_set=vs, partial_output=True), partial_holder
            return last, out_dir
        if self._result_stop_all(last):
            if best_partial is not None:
                return replace(last, volume_set=vs, partial_output=True), partial_holder
            return last, None

        normalized = self._normalize_middle_numbered_volume_set(vs, dry_run=dry_run)
        if normalized is None:
            if best_partial is not None:
                return replace(best_partial, volume_set=vs), partial_holder
            return last, None

        normalized_vs, session = normalized
        self._emit(
            f"VOLUME-RENAME-TRY: {vs.entry.name} -> {normalized_vs.entry.name} rule=middle-numbered-volume"
        )
        result, normalized_out_dir = self._extract_with_variant_attempts(
            normalized_vs,
            layer_root / "attempt_2_volume_renamed",
            dry_run=dry_run,
            preferred_password=preferred_password,
        )
        if self._result_complete(result):
            self._discard_partial_output(partial_holder)
            return result, normalized_out_dir
        if normalized_out_dir is not None and self._has_nonempty_file(normalized_out_dir):
            best_partial = result
            self._retain_partial_output(normalized_out_dir, partial_holder)

        session.rollback_best_effort(dry_run=dry_run)
        self._emit(
            f"VOLUME-RENAME-ROLLBACK: {normalized_vs.entry.name} -> {vs.entry.name} rule=middle-numbered-volume"
        )
        if self._result_stop_all(result):
            if best_partial is not None:
                return replace(result, volume_set=vs, partial_output=True), partial_holder
            return replace(result, volume_set=vs), None
        if best_partial is not None:
            return replace(best_partial, volume_set=vs), partial_holder
        return replace(result, volume_set=vs), None

    def _continue_after_extract(
        self,
        *,
        package_name: str,
        package_root: Path,
        start_dir: Path,
        final_root: Path,
        dry_run: bool,
        preferred_password: str | None = None,
    ) -> tuple[bool, Path | None, str | None]:
        if not self._deep.enabled:
            final_dir = self._promote_final_dir(
                start_dir, final_root, package_name=package_name, dry_run=dry_run,
                cleanup_dynamic_libs=True,
            )
            return True, final_dir, "depth=1"

        max_depth = max(1, int(self._deep.max_depth))
        min_archive_bytes = max(self._archive_min_mb, int(self._deep.min_archive_mb)) * 1024 * 1024
        final_single_bytes = max(1, int(self._deep.final_single_mb)) * 1024 * 1024
        current_dir = start_dir
        current_password = preferred_password
        partial_holder = package_root / "_partial_candidate"
        best_partial: ExtractionResult | None = None
        latest_failure_message: str | None = None
        for depth in range(1, max_depth + 1):
            reason = self._is_final_output(current_dir)
            if reason is not None:
                final_dir = self._promote_final_dir(
                    current_dir, final_root, package_name=package_name, dry_run=dry_run,
                    cleanup_dynamic_libs=True,
                )
                return True, final_dir, f"depth={depth} reason={reason}"

            candidates = self._nested_candidates(
                current_dir,
                package_root / "variants" / f"L{depth + 1}",
                min_archive_bytes=min_archive_bytes,
                final_single_bytes=final_single_bytes,
                dry_run=dry_run,
            )
            if not candidates:
                if self._has_nonempty_file(current_dir):
                    final_dir = self._promote_final_dir(
                        current_dir, final_root, package_name=package_name, dry_run=dry_run,
                        cleanup_dynamic_libs=True,
                    )
                    return True, final_dir, f"depth={depth} reason=no-candidates"
                return False, None, f"depth={depth} no candidates"

            extracted = False
            deferred = False
            for index, attempt in enumerate(candidates, start=1):
                candidate = attempt.path
                if self._is_deferred_volume_fragment(candidate):
                    target = self._defer_volume_fragment(candidate, dry_run=dry_run)
                    self._rollback_attempt(attempt, dry_run=dry_run)
                    if target is not None:
                        self._emit(f"DEFER-VOLUME: {candidate.name} -> {target}")
                    deferred = True
                    continue

                request_vs = VolumeSet(entry=candidate, members=(candidate,), group_key=f"{package_name}:{depth}:{index}")
                target_dir_root = package_root / f"L{depth + 1}" / self._safe_name(candidate.name)
                res, target_dir = self._extract_with_variant_attempts(
                    request_vs,
                    target_dir_root,
                    dry_run=dry_run,
                    prefix="DEEP-EXTRACT",
                    preferred_password=current_password,
                    method=attempt.method,
                )
                if self._result_complete(res):
                    self._discard_partial_output(partial_holder)
                    best_partial = None
                    latest_failure_message = None
                    if res.password:
                        current_password = res.password
                    current_dir = target_dir if target_dir is not None else target_dir_root
                    extracted = True
                    break
                partial_dir = target_dir if target_dir is not None else target_dir_root
                has_output = self._has_nonempty_file(partial_dir)
                if self._result_partial(res) or (not res.ok and has_output):
                    if has_output:
                        self._retain_partial_output(partial_dir, partial_holder)
                        best_partial = res
                    if res.message:
                        latest_failure_message = res.message
                    if self._result_stop_all(res):
                        break
                    continue
                if res.message:
                    latest_failure_message = res.message
                self._rollback_attempt(attempt, dry_run=dry_run)
                if self._result_stop_all(res):
                    break
            if not extracted:
                if best_partial is not None:
                    message = PARTIAL_WARNING
                    if latest_failure_message:
                        message = f"{PARTIAL_WARNING} 工具错误：{latest_failure_message}"
                    return True, partial_holder, message
                if deferred and not self._has_nonempty_file(current_dir):
                    self._remove_empty_dirs(current_dir)
                    return True, None, f"depth={depth} reason=deferred-volume-fragments"
                if self._has_nonempty_file(current_dir):
                    final_dir = self._promote_final_dir(
                        current_dir, final_root, package_name=package_name, dry_run=dry_run,
                        cleanup_dynamic_libs=True,
                    )
                    return True, final_dir, f"depth={depth} reason=candidates-failed"
                return False, None, f"depth={depth} all candidates failed"

        if best_partial is not None:
            message = PARTIAL_WARNING
            if latest_failure_message:
                message = f"{PARTIAL_WARNING} 工具错误：{latest_failure_message}"
            return True, partial_holder, message
        return False, None, f"exceeded max_depth={max_depth}"

    def _nested_candidates(
        self,
        folder: Path,
        workspace: Path,
        *,
        min_archive_bytes: int,
        final_single_bytes: int,
        dry_run: bool,
    ) -> list[CandidateAttempt]:
        base_candidates = self._restore.build_post_extract_candidates(
            folder,
            workspace=workspace,
            min_archive_bytes=min_archive_bytes,
            final_single_bytes=final_single_bytes,
            dry_run=dry_run,
        )
        limit = max(1, int(getattr(self._deep, "max_candidates", 5)))
        mode = str(getattr(self._deep, "mode", "smart")).lower()
        if mode == "basic":
            base_candidates.sort(key=lambda candidate: self._safe_file_size(candidate), reverse=True)
            base_candidates = base_candidates[:1]
        else:
            base_candidates = base_candidates[:limit]
        out: list[CandidateAttempt] = []
        seen: set[Path] = set()
        for candidate in base_candidates:
            probe = self._restore.identify(candidate)
            self._emit(f"IDENTIFY: nested={candidate.name} kind={probe.kind.value} suffix={probe.archive_suffix or '-'} reason={probe.reason or '-'}")
            child_workspace = workspace / self._safe_name(candidate.name)
            for attempt in self._candidate_chain(candidate, probe=probe, workspace=child_workspace, dry_run=dry_run):
                if attempt.path in seen:
                    continue
                seen.add(attempt.path)
                self._emit(
                    f"CANDIDATE: source={candidate.name} entry={attempt.path.name} method={attempt.method}"
                )
                out.append(attempt)
        return out

    @staticmethod
    def _safe_file_size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _extract_with_variant_attempts(
        self,
        volume_set: VolumeSet,
        base_output_dir: Path,
        *,
        dry_run: bool,
        prefix: str = "EXTRACT",
        preferred_password: str | None = None,
        method: str = "direct",
    ) -> tuple[ExtractionResult, Path | None]:
        partial_holder = base_output_dir.parent / "_partial_candidate"
        best_partial: ExtractionResult | None = None
        probe = self._restore.identify(volume_set.entry)
        attempt_vs = volume_set
        direct_result, direct_dir = self._run_extract_attempt(
            attempt_vs,
            probe=probe,
            output_dir=base_output_dir,
            dry_run=dry_run,
            prefix=prefix,
            preferred_password=preferred_password,
            method=method,
        )
        if self._result_complete(direct_result) or len(volume_set.members) > 1:
            return direct_result, direct_dir
        if self._result_partial(direct_result) and direct_dir is not None and self._has_nonempty_file(direct_dir):
            best_partial = direct_result
            self._retain_partial_output(direct_dir, partial_holder)
        elif direct_dir is not None and self._has_nonempty_file(direct_dir):
            # Preserve the established stop behavior for unclassified output
            # from ordinary/unknown candidates.
            return direct_result, direct_dir
        if self._result_stop_all(direct_result):
            # A serious no-output failure the extraction service already classified
            # as stop_all (missing volume / permission / disk-write / space /
            # exit 7/8/124/255) cannot be fixed by a rename or a disguise restore;
            # escalating would edit the selected original for nothing.
            return replace(direct_result, volume_set=volume_set), partial_holder if best_partial is not None else None

        last = direct_result
        variant_plans = self._restore.variant_plans(volume_set.entry)
        for variant_index, plan in enumerate(variant_plans, start=1):
            if plan.target.exists() and plan.target != volume_set.entry:
                self._emit(f"RENAME-SKIP: target exists {plan.target.name} rule={plan.rule_name}")
                continue
            session = RenameSession.create(self._renamer)
            renamed_entry = session.rename(volume_set.entry, plan.target, dry_run=dry_run)
            renamed_vs = VolumeSet(entry=renamed_entry, members=(renamed_entry,), group_key=volume_set.group_key)
            variant_probe = self._restore.identify(renamed_entry)
            if plan.preferred_tool:
                variant_probe = replace(variant_probe, preferred_tool=plan.preferred_tool)
            self._emit(f"RENAME-TRY: {volume_set.entry.name} -> {renamed_entry.name} rule={plan.rule_name}")
            result, out_dir = self._run_extract_attempt(
                renamed_vs,
                probe=variant_probe,
                output_dir=base_output_dir.parent / f"{base_output_dir.name}_variant_{variant_index}",
                dry_run=dry_run,
                prefix=prefix,
                preferred_password=preferred_password,
                method=f"{method}+rename:{plan.rule_name}",
            )
            last = result
            if self._result_complete(result):
                self._discard_partial_output(partial_holder)
                return result, out_dir
            if self._result_partial(result) and out_dir is not None and self._has_nonempty_file(out_dir):
                best_partial = result
                self._retain_partial_output(out_dir, partial_holder)
                session.rollback_best_effort(dry_run=dry_run)
                self._emit(f"RENAME-ROLLBACK: {renamed_entry.name} -> {volume_set.entry.name} rule={plan.rule_name}")
                if self._result_stop_all(result):
                    return replace(result, volume_set=volume_set), partial_holder
                continue
            if out_dir is not None and self._has_nonempty_file(out_dir):
                if best_partial is not None:
                    session.rollback_best_effort(dry_run=dry_run)
                    return replace(best_partial, volume_set=volume_set), partial_holder
                self._discard_partial_output(partial_holder)
                return result, out_dir
            session.rollback_best_effort(dry_run=dry_run)
            self._emit(f"RENAME-ROLLBACK: {renamed_entry.name} -> {volume_set.entry.name} rule={plan.rule_name}")
            if self._result_stop_all(result):
                if best_partial is not None:
                    return replace(result, volume_set=volume_set, partial_output=True), partial_holder
                return replace(result, volume_set=volume_set), None

        # In-place suffix attempts were rolled back to the input volume set. Only
        # now escalate to disguise restoration, which may edit the source in place.
        restored = self._restore_after_failure(
            volume_set,
            base_output_dir,
            probe=probe,
            dry_run=dry_run,
            prefix=prefix,
            preferred_password=preferred_password,
            method=method,
        )
        if restored is not None:
            result, out_dir = restored
            if self._result_complete(result):
                self._discard_partial_output(partial_holder)
                return replace(result, volume_set=volume_set), out_dir
            if self._result_stop_all(result) and best_partial is not None:
                return replace(result, volume_set=volume_set, partial_output=True), partial_holder
            if self._result_partial(result) and out_dir is not None and self._has_nonempty_file(out_dir):
                best_partial = result
                self._retain_partial_output(out_dir, partial_holder)
                return replace(result, volume_set=volume_set), partial_holder
            if self._result_partial(result) and self._has_nonempty_file(partial_holder):
                return replace(result, volume_set=volume_set), partial_holder
            elif out_dir is not None and self._has_nonempty_file(out_dir):
                if best_partial is not None:
                    return replace(best_partial, volume_set=volume_set), partial_holder
                self._discard_partial_output(partial_holder)
                return replace(result, volume_set=volume_set), out_dir
            elif best_partial is not None:
                return replace(best_partial, volume_set=volume_set), partial_holder
            return replace(result, volume_set=volume_set), None
        if best_partial is not None:
            return replace(best_partial, volume_set=volume_set), partial_holder
        return replace(last, volume_set=volume_set), None

    def _restore_after_failure(
        self,
        volume_set: VolumeSet,
        base_output_dir: Path,
        *,
        probe: ArchiveProbe,
        dry_run: bool,
        prefix: str,
        preferred_password: str | None,
        method: str,
    ) -> tuple[ExtractionResult, Path | None] | None:
        """Apply disguise restoration only after a read/suffix pass produced nothing.

        A confirmed Apate/embedded disguise is passed as preferred_kind so the
        polyglot (RestoreAB) scanner cannot preempt it on a single file; RestoreAB
        remains the final fallback for otherwise UNKNOWN covers. Any in-place edit
        is rolled back when restoration still yields no usable output.
        """
        if probe.kind == ArchiveKind.ARCHIVE:
            return None
        self._emit(f"RESTORE-TRY: entry={volume_set.entry.name} kind={probe.kind.value}")
        before = self._source_signature(volume_set.entry)
        try:
            restored, rollbacks = self._restore.restore_with_rollbacks(
                volume_set.entry,
                workspace=base_output_dir,
                dry_run=dry_run,
                preferred_kind=probe.kind,
            )
        except (EngineError, ProcessingCancelled):
            raise
        except RestoreABError as exc:
            # The in-place planner rejected this layout (for example ZIP64). That is
            # an ordinary single-package failure, not an internal processing error;
            # surface the real reason instead of needs_review / PROCESSING_FAILED.
            self._emit(
                f"RESTORE-ERROR: entry={volume_set.entry.name} kind={probe.kind.value} error={exc}"
            )
            return ExtractionResult(volume_set=volume_set, ok=False, tool="restore", message=str(exc)), None
        restored_paths = list(restored or [])
        in_place = bool(restored_paths) and all(candidate == volume_set.entry for candidate in restored_paths)
        changed = in_place and self._source_signature(volume_set.entry) != before
        name_getter = getattr(self._restore, "last_restorer_name", None)
        restorer_name = name_getter() if callable(name_getter) else None
        method_getter = getattr(self._restore, "last_method", None)
        restored_marker = method_getter() if callable(method_getter) else None
        if in_place and not changed:
            # A no-op restore (for example an unregistered suffix rule) is not a
            # usable result and must not be labeled as a restoration method.
            return None
        restore_method = self._restore_method(
            probe,
            tuple(rollbacks),
            restorer_name=restorer_name,
            in_place=changed,
            last_method=restored_marker,
        )
        last_result: ExtractionResult | None = None
        retained_result: ExtractionResult | None = None
        retained_partial: Path | None = None
        for index, restored_path in enumerate(restored_paths or [volume_set.entry], start=1):
            restore_vs = VolumeSet(entry=restored_path, members=(restored_path,), group_key=volume_set.group_key)
            restore_probe = self._restore.identify(restored_path)
            result, out_dir = self._run_extract_attempt(
                restore_vs,
                probe=restore_probe,
                output_dir=base_output_dir.parent / f"{base_output_dir.name}_restore_{index}",
                dry_run=dry_run,
                prefix=prefix,
                preferred_password=preferred_password,
                method=restore_method,
            )
            last_result = result
            if self._result_complete(result):
                self._discard_partial_output(retained_partial)
                return result, out_dir
            if self._result_partial(result) and out_dir is not None and self._has_nonempty_file(out_dir):
                retained_partial = base_output_dir.parent / "_partial_candidate"
                self._retain_partial_output(out_dir, retained_partial)
                retained_result = result
            elif out_dir is not None and self._has_nonempty_file(out_dir):
                self._discard_partial_output(retained_partial)
                return result, out_dir
        if rollbacks:
            self._restore.rollback_apate(list(rollbacks), dry_run=dry_run)
            self._emit(f"RESTORE-ROLLBACK: entry={volume_set.entry.name} method={restore_method}")
        # Report the restore attempt's error so the failure category stays
        # actionable even though the derive produced no usable output.
        if last_result is not None:
            return (retained_result or last_result), retained_partial
        return None

    def _run_extract_attempt(
        self,
        volume_set: VolumeSet,
        *,
        probe: ArchiveProbe,
        output_dir: Path,
        dry_run: bool,
        prefix: str,
        preferred_password: str | None = None,
        method: str = "direct",
    ) -> tuple[ExtractionResult, Path | None]:
        preferred = preferred_password if preferred_password else "<none>"
        self._emit(
            f"{prefix}-TRY: entry={volume_set.entry.name} method={method} "
            f"kind={probe.kind.value} suffix={probe.archive_suffix or '-'} "
            f"preferred_password={preferred} output={output_dir}"
        )
        req = ExtractionRequest(
            volume_set=volume_set,
            output_dir=output_dir,
            passwords=self._passwords,
            preferred_password=preferred_password,
            method=method,
        )
        track_produced_files = self._cleanup_dynamic_libs and bool(self._dynamic_lib_patterns) and not dry_run
        existing_files: set[Path] = set()
        if track_produced_files and output_dir.is_dir():
            existing_files = {path.absolute() for path in output_dir.rglob("*") if path.is_file() and not path.is_symlink()}
        try:
            result = self._extractor.extract_one(req, preference="auto", probe=probe, dry_run=dry_run)
        except (EngineError, ProcessingCancelled):
            # Cancellation and engine error codes (UNSAFE_ARCHIVE / OUTPUT_LIMIT /
            # DISK_FULL / TOOL_TIMEOUT / TOOL_START_FAILED / ...) must abort the job
            # with their real code. Downgrading them to a per-attempt failure would
            # let rename/restore run on and mis-report the outcome as partial.
            raise
        except Exception as exc:  # a crashed tool/attempt is a failure, not a package abort
            self._emit(
                f"{prefix}-ERROR: entry={volume_set.entry.name} method={method} "
                f"error={type(exc).__name__}: {exc}"
            )
            return ExtractionResult(volume_set=volume_set, ok=False, tool="error", message=str(exc)), None
        if track_produced_files and output_dir.is_dir():
            self._produced_files.update(
                path.absolute()
                for path in output_dir.rglob("*")
                if path.is_file() and not path.is_symlink() and path.absolute() not in existing_files
            )
        self._emit_extract_result(prefix, volume_set.entry.name, result, output_dir=output_dir, method=method)
        if result.ok or self._result_partial(result) or self._has_nonempty_file(output_dir):
            return result, output_dir
        return result, None

    def _candidate_chain(self, path: Path, *, probe: ArchiveProbe, workspace: Path, dry_run: bool) -> list[CandidateAttempt]:
        # Candidates are read-only: yield only the original path for a direct
        # read plus in-place suffix attempts. Disguise restoration is deferred
        # until those fail without usable output, so candidate preparation never
        # materializes a recovery copy or a fresh backup of the source.
        _ = (workspace, probe)
        # The first attempt is always a plain read of the original path; no
        # disguise has been restored yet, so it must not claim a "reveal" method.
        # A real method label is applied only by _restore_after_failure.
        return [CandidateAttempt(path, method="direct", source=path)]

    def _rollback_attempt(self, attempt: CandidateAttempt, *, dry_run: bool) -> None:
        if not attempt.rollbacks:
            return
        self._restore.rollback_apate(list(attempt.rollbacks), dry_run=dry_run)
        self._emit(f"RESTORE-ROLLBACK: entry={attempt.path.name} method={attempt.method}")

    def _restore_method(
        self,
        probe: ArchiveProbe,
        rollbacks: tuple,
        *,
        restorer_name: str | None = None,
        in_place: bool = False,
        last_method: str | None = None,
    ) -> str:
        # Prefer the marker the active restorer reported for an actual in-place
        # edit; a marker left over from an earlier call must not relabel a copy
        # or an untouched UNKNOWN file as a successful restore.
        if last_method and in_place:
            return last_method
        names = [getattr(record, "rule_name", "") for record in rollbacks]
        names = [name for name in names if name]
        if names:
            return "+".join(dict.fromkeys(names))
        if probe.kind == ArchiveKind.APATE:
            return "apate-reveal"
        if probe.kind == ArchiveKind.EMBEDDED:
            return "embedded-archive-strip-prefix"
        # Only an actual in-place RestoreAB edit earns this label; an UNKNOWN
        # file must not be reported as restoreAB just because it fell back here.
        if in_place and restorer_name == "RestoreABRestorer":
            return "restore-ab-in-place"
        return "direct"

    def _force_apate_attempt_if_useful(self, path: Path, *, dry_run: bool) -> CandidateAttempt | None:
        probe = self._restore.identify(path)
        if probe.kind != ArchiveKind.UNKNOWN:
            return None
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mkv", ".avi", ".mov", ".exe"}:
            return None
        fn = getattr(self._restore, "force_apate_restore_with_rollbacks", None)
        if not callable(fn):
            return None
        rounds = self._force_apate_rounds_from_name(path.name)
        restored = path
        all_rollbacks = []
        for _ in range(rounds):
            restored, rollbacks = fn(restored, dry_run=dry_run)
            if restored is None:
                break
            all_rollbacks.extend(rollbacks)
        if restored is None:
            return None
        self._emit(f"APATE-FORCE-TRY: {path.name}")
        return CandidateAttempt(restored, tuple(all_rollbacks), method="apate-force-reveal", source=path)

    def _force_apate_rounds_from_name(self, name: str) -> int:
        low = name.lower()
        markers = (
            ("three", 3),
            ("triple", 3),
            ("two", 2),
            ("double", 2),
            ("one", 1),
            ("single", 1),
        )
        for marker, rounds in markers:
            if re.search(rf"(^|[^a-z0-9]){marker}([^a-z0-9]|$)", low):
                return rounds
        return 1

    def _normalize_disguised_split_suffix_volume_set(
        self,
        vs: VolumeSet,
        *,
        dry_run: bool,
    ) -> tuple[VolumeSet, RenameSession] | None:
        if len(vs.members) != 2:
            return None

        bare: Path | None = None
        match: re.Match[str] | None = None
        for member in vs.members:
            member_match = re.match(r"^(?P<base>.+)\.(?P<ext>7z|zip|rar)$", member.name, flags=re.IGNORECASE)
            if member_match is not None:
                bare = member
                match = member_match
                break

        if bare is None or match is None:
            # Variant seen in the wild: the first volume is disguised as a
            # media file, while the next volume keeps its .002 suffix, e.g.
            # name.zip.jpg + name.zip.002.
            numbered = next(
                (
                    (member, member_match)
                    for member in vs.members
                    for member_match in [
                        re.match(
                            r"^(?P<base>.+)\.(?P<ext>7z|zip|rar)\.(?P<idx>\d{3})$",
                            member.name,
                            flags=re.IGNORECASE,
                        )
                    ]
                    if member_match is not None and member_match.group("idx") == "002"
                ),
                None,
            )
            if numbered is None:
                return None

            numbered_path, numbered_match = numbered
            disguised_pattern = re.compile(
                rf"^{re.escape(numbered_match.group('base'))}\.{numbered_match.group('ext')}\.(zip|jpg|jpeg|png|webp|mp4|mkv|avi|mov|exe)$",
                flags=re.IGNORECASE,
            )
            disguised = next(
                (member for member in vs.members
                 if member != numbered_path and member.parent == numbered_path.parent
                 and disguised_pattern.match(member.name)),
                None,
            )
            if disguised is None:
                return None

            target = numbered_path.with_name(
                f"{numbered_match.group('base')}.{numbered_match.group('ext')}.001"
            )
            if target.exists() and target not in vs.members:
                self._emit(f"VOLUME-RENAME-SKIP: target exists {target.name}")
                return None

            session = RenameSession.create(self._renamer)
            try:
                renamed_disguised = session.rename(disguised, target, dry_run=dry_run)
            except OSError:
                session.rollback_best_effort(dry_run=dry_run)
                raise
            renamed_members = tuple(
                renamed_disguised if member == disguised else member for member in vs.members
            )
            return (
                VolumeSet(
                    entry=renamed_disguised,
                    members=renamed_members,
                    group_key=vs.group_key,
                ),
                session,
            )

        disguised_pattern = re.compile(
            rf"^{re.escape(bare.name)}\.(zip|jpg|jpeg|png|webp|mp4|mkv|avi|mov|exe)$",
            flags=re.IGNORECASE,
        )
        disguised = next((member for member in vs.members if member != bare and disguised_pattern.match(member.name)), None)
        if disguised is None:
            return None

        ext = match.group("ext")
        plans = [
            (bare, bare.with_name(f"{bare.name}.001")),
            (disguised, bare.with_name(f"{match.group('base')}.{ext}.002")),
        ]
        targets = [target for _src, target in plans]
        if len(set(targets)) != len(targets):
            return None
        for target in targets:
            if target.exists() and target not in vs.members:
                self._emit(f"VOLUME-RENAME-SKIP: target exists {target.name}")
                return None

        session = RenameSession.create(self._renamer)
        renamed_members: list[Path] = []
        entry: Path | None = None
        try:
            for src, dst in plans:
                renamed = session.rename(src, dst, dry_run=dry_run)
                renamed_members.append(renamed)
                if src == bare:
                    entry = renamed
        except OSError:
            session.rollback_best_effort(dry_run=dry_run)
            raise

        return (
            VolumeSet(
                entry=entry or renamed_members[0],
                members=tuple(renamed_members),
                group_key=vs.group_key,
            ),
            session,
        )

    def _normalize_numbered_tail_volume_set(
        self,
        vs: VolumeSet,
        *,
        dry_run: bool,
    ) -> tuple[VolumeSet, RenameSession] | None:
        if len(vs.members) < 2:
            return None

        plans: list[tuple[Path, Path]] = []
        changed = False
        for member in vs.members:
            target = self._numbered_tail_volume_target(member)
            if target is None:
                return None
            if target != member:
                changed = True
            if target.exists() and target not in vs.members:
                self._emit(f"VOLUME-RENAME-SKIP: target exists {target.name}")
                return None
            plans.append((member, target))

        if not changed:
            return None

        targets = [target for _src, target in plans]
        if len(set(targets)) != len(targets):
            return None

        session = RenameSession.create(self._renamer)
        renamed_members: list[Path] = []
        entry: Path | None = None
        try:
            for src, dst in plans:
                renamed = session.rename(src, dst, dry_run=dry_run)
                renamed_members.append(renamed)
                if src == vs.entry:
                    entry = renamed
        except OSError:
            session.rollback_best_effort(dry_run=dry_run)
            raise

        return (
            VolumeSet(
                entry=entry or renamed_members[0],
                members=tuple(renamed_members),
                group_key=vs.group_key,
            ),
            session,
        )

    def _numbered_tail_volume_target(self, path: Path) -> Path | None:
        if re.match(r"^.+\.\d{3}$", path.name, flags=re.IGNORECASE):
            return path
        match = re.match(r"^(?P<base>.+)\.(?P<idx>\d{3})\.[^.]+$", path.name, flags=re.IGNORECASE)
        if match is None:
            return None
        return path.with_name(f"{match.group('base')}.{match.group('idx')}")

    def _normalize_middle_numbered_volume_set(
        self,
        vs: VolumeSet,
        *,
        dry_run: bool,
    ) -> tuple[VolumeSet, RenameSession] | None:
        plans: list[tuple[Path, Path]] = []
        for member in vs.members:
            target = self._middle_numbered_volume_target(member)
            if target is None:
                return None
            if target.exists() and target not in vs.members:
                self._emit(f"VOLUME-RENAME-SKIP: target exists {target.name}")
                return None
            plans.append((member, target))

        if not plans or all(src == dst for src, dst in plans):
            return None

        targets = [target for _src, target in plans]
        if len(set(targets)) != len(targets):
            return None

        session = RenameSession.create(self._renamer)
        renamed_members: list[Path] = []
        entry: Path | None = None
        try:
            for src, dst in plans:
                renamed = session.rename(src, dst, dry_run=dry_run)
                renamed_members.append(renamed)
                if src == vs.entry:
                    entry = renamed
        except OSError:
            session.rollback_best_effort(dry_run=dry_run)
            raise

        return (
            VolumeSet(
                entry=entry or renamed_members[0],
                members=tuple(renamed_members),
                group_key=vs.group_key,
            ),
            session,
        )

    def _middle_numbered_volume_target(self, path: Path) -> Path | None:
        match = re.match(r"^(?P<base>.+)\.(?P<idx>\d{3})\.(?P<ext>7z|zip|rar)$", path.name, flags=re.IGNORECASE)
        if match is None:
            return None
        return path.with_name(f"{match.group('base')}.{match.group('ext')}.{match.group('idx')}")

    def _normalize_sfx_volume_set(
        self,
        vs: VolumeSet,
        *,
        dry_run: bool,
    ) -> tuple[VolumeSet, RenameSession] | None:
        sfx_members = [member for member in vs.members if re.search(r"\.(7z|zip|rar)\.exe$", member.name, flags=re.IGNORECASE)]
        if len(sfx_members) != 1 or len(vs.members) < 2:
            return None

        sfx = sfx_members[0]
        match = re.match(r"^(?P<base>.+)\.(?P<ext>7z|zip|rar)\.exe$", sfx.name, flags=re.IGNORECASE)
        if match is None:
            return None
        target = sfx.with_name(f"{match.group('base')}.{match.group('ext')}.001")
        if target.exists() and target not in vs.members:
            self._emit(f"VOLUME-RENAME-SKIP: target exists {target.name}")
            return None

        session = RenameSession.create(self._renamer)
        renamed_sfx = session.rename(sfx, target, dry_run=dry_run)
        renamed_members = tuple(renamed_sfx if member == sfx else member for member in vs.members)
        return (
            VolumeSet(
                entry=renamed_sfx if vs.entry == sfx else vs.entry,
                members=renamed_members,
                group_key=vs.group_key,
            ),
            session,
        )

    def _deferred_volume_group_name(self, path: Path) -> str | None:
        name = path.name
        match = re.match(r"^(?P<base>.+)\.(?P<ext>7z|zip|rar)\.(?P<idx>\d{3})$", name, flags=re.IGNORECASE)
        if match is not None:
            return f"{match.group('base')}.{match.group('ext')}"

        match = re.match(r"^(?P<base>.+)\.(?P<idx>\d{3})\.(?P<ext>7z|zip|rar)$", name, flags=re.IGNORECASE)
        if match is not None:
            return f"{match.group('base')}.{match.group('ext')}"

        match = re.match(r"^(?P<base>.+)\.part(?P<idx>\d{1,3})\.(?P<ext>rar|zip|7z)$", name, flags=re.IGNORECASE)
        if match is not None:
            return f"{match.group('base')}.{match.group('ext')}"

        match = re.match(r"^(?P<base>.+)\.(?P<ext>[rz])(?P<idx>\d{2})$", name, flags=re.IGNORECASE)
        if match is not None:
            archive_ext = "rar" if match.group("ext").lower() == "r" else "zip"
            return f"{match.group('base')}.{archive_ext}"

        match = re.match(r"^(?P<base>.+)\.(?P<idx>\d{3})$", name, flags=re.IGNORECASE)
        if match is not None:
            return match.group("base")

        return None

    def _is_deferred_volume_fragment(self, path: Path) -> bool:
        return self._deferred_volume_group_name(path) is not None

    def _defer_volume_fragment(self, path: Path, *, dry_run: bool) -> Path | None:
        group_name = self._deferred_volume_group_name(path)
        if group_name is None:
            return None

        dest_dir = self._folder / "deferred_volumes" / self._safe_name(group_name)
        target = dest_dir / path.name
        if target.exists():
            target = self._duplicate_target(dest_dir, target_name=path.name, package_name=self._safe_name(group_name))
        if dry_run:
            self._emit(f"MOVE(dry): {path} -> {target}")
            return target
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        self._remove_empty_dirs(path.parent)
        return target

    def _defer_missing_volume_set(self, vs: VolumeSet, *, dry_run: bool) -> list[Path]:
        if not self._manage_originals:
            return [member for member in vs.members if member.exists()]
        moved: list[Path] = []
        for member in list(vs.members):
            if not member.exists():
                continue
            target = self._defer_volume_fragment(member, dry_run=dry_run)
            if target is not None:
                moved.append(target)
        return moved

    def _promote_final_dir(
        self,
        current_dir: Path,
        final_root: Path,
        *,
        package_name: str,
        dry_run: bool,
        cleanup_dynamic_libs: bool = False,
    ) -> Path:
        leaf = deepest_wrapper_dir(current_dir) if self._path_compress else current_dir
        target_name = leaf.name if self._preserve_payload_names and leaf != current_dir else package_name
        target = final_root / target_name
        if target.exists() or target.is_symlink():
            target = self._duplicate_target(final_root, target_name=target_name, package_name=package_name)
        if dry_run:
            return target
        if cleanup_dynamic_libs and self._cleanup_dynamic_libs and self._dynamic_lib_patterns:
            self._cleanup_dynamic_libraries(leaf)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = leaf if leaf.exists() else current_dir
        shutil.move(str(source), str(target))
        self._remove_empty_dirs(current_dir)
        return target

    def _cleanup_dynamic_libraries(self, root: Path) -> None:
        """Remove only matching regular DLLs created by this pipeline run."""
        if not root.exists() or not root.is_dir() or root.is_symlink():
            return
        try:
            folder_root = self._folder.resolve()
            owned_root = (self._folder / "intermediate").resolve()
            owned_root.relative_to(folder_root)
            resolved_root = root.resolve()
            resolved_root.relative_to(owned_root)
        except (OSError, ValueError):
            return
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".dll":
                continue
            if path.absolute() not in self._produced_files:
                continue
            if not any(fnmatch.fnmatchcase(path.name.casefold(), pattern.casefold()) for pattern in self._dynamic_lib_patterns):
                continue
            try:
                resolved_path = path.resolve(strict=True)
                resolved_path.relative_to(owned_root)
                path.unlink()
                self._produced_files.discard(path.absolute())
                self._emit(f"CLEANUP-DYNAMIC-LIB: {path.name}")
            except (OSError, ValueError) as exc:
                self._emit(f"CLEANUP-DYNAMIC-LIB-SKIP: {path.name} error={type(exc).__name__}")

    def _duplicate_target(self, root: Path, *, target_name: str, package_name: str) -> Path:
        duplicate_root = root / self._duplicates_dir_name / package_name
        dst = duplicate_root / target_name
        if not dst.exists() and not dst.is_symlink():
            return dst
        for index in range(1, 1000):
            candidate_root = duplicate_root / f"copy_{index}"
            dst = candidate_root / target_name
            if not dst.exists() and not dst.is_symlink():
                return dst
        raise EngineError("OUTPUT_CONFLICT", "重复成品目录已占满，请整理重复项后重试。")

    def _move_original_members(self, vs: VolumeSet, dest_dir: Path, *, package_name: str, dry_run: bool) -> None:
        if not self._manage_originals:
            return
        for member in vs.members:
            if not member.exists():
                continue
            dst = dest_dir / member.name
            if dst.exists() or dst.is_symlink():
                dst = self._duplicate_target(dest_dir, target_name=member.name, package_name=package_name)
            if dry_run:
                self._emit(f"MOVE(dry): {member} -> {dst}")
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(member), str(dst))
            self._emit(f"MOVE: {member.name} -> {dst}")

    def _move_partial_outputs_to_error(
        self,
        source_dir: Path,
        dest_dir: Path,
        *,
        package_name: str,
        dry_run: bool,
    ) -> Path | None:
        if not self._has_any_file(source_dir):
            return None

        files = [path for path in source_dir.iterdir() if path.is_file()]
        dirs = [path for path in source_dir.iterdir() if path.is_dir()]
        if len(files) == 1 and not dirs:
            target = dest_dir / files[0].name
            if target.exists():
                target = self._duplicate_target(dest_dir, target_name=files[0].name, package_name=package_name)
            if dry_run:
                self._emit(f"MOVE(dry): {files[0]} -> {target}")
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(files[0]), str(target))
            self._remove_empty_dirs(source_dir)
            self._emit(f"MOVE-PARTIAL: {files[0].name} -> {target}")
            return target

        target_dir = dest_dir / package_name
        if target_dir.exists():
            target_dir = self._duplicate_target(dest_dir, target_name=package_name, package_name=package_name)
        if dry_run:
            self._emit(f"MOVE(dry): {source_dir} -> {target_dir}")
            return target_dir
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_dir), str(target_dir))
        self._emit(f"MOVE-PARTIAL-DIR: {source_dir} -> {target_dir}")
        return target_dir

    def _move_partial_outputs_to_incomplete(
        self,
        source_dir: Path,
        dest_root: Path,
        *,
        package_name: str,
        dry_run: bool,
    ) -> Path | None:
        if not self._has_nonempty_file(source_dir):
            return None
        target = dest_root / package_name
        while target.exists() or target.is_symlink():
            target = dest_root / f"{package_name}__run_{uuid.uuid4().hex}"
        if dry_run:
            self._emit(f"MOVE(dry): {source_dir} -> {target}")
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_dir), str(target))
        self._emit(f"MOVE-INCOMPLETE: {source_dir} -> {target}")
        return target

    def _retain_partial_output(self, source_dir: Path, holder: Path) -> None:
        """Keep one failed output while later extraction modes are tried."""
        if source_dir == holder:
            return
        self._discard_partial_output(holder)
        if source_dir.exists():
            holder.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_dir), str(holder))

    def _discard_partial_output(self, path: Path | None) -> None:
        if path is None or not path.exists():
            return
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()

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

    def _failure_category(self, result: ExtractionResult, entry: Path) -> str:
        message = (result.message or "").lower()
        if self._looks_like_password_error(message):
            return "password_error"
        probe = self._restore.identify(entry)
        if probe.kind == ArchiveKind.UNKNOWN or self._looks_like_unknown_type_error(message):
            return "unknown_type"
        if self._is_missing_volume(result.message):
            return "missing_volume"
        return "extract_failed"

    def _looks_like_password_error(self, message: str) -> bool:
        return any(
            marker in message
            for marker in (
                "wrong password",
                "error: wrong password",
                "wrong password?",
                "password is incorrect",
                "incorrect password",
                "invalid password",
                "illegal password",
                "encrypted",
                "can not open encrypted archive",
                "cannot open encrypted archive",
                "data error in encrypted",
                "crc failed in encrypted",
                "data error",
                "crc failed",
                "密码错误",
                "密码不正确",
                "口令错误",
                "非法密码",
            )
        )

    def _looks_like_unknown_type_error(self, message: str) -> bool:
        return any(
            marker in message
            for marker in (
                "can not open the file as archive",
                "cannot open the file as archive",
                "is not archive",
                "not archive",
                "unsupported archive type",
            )
        )

    def _emit_extract_result(
        self,
        prefix: str,
        entry_name: str,
        result: ExtractionResult,
        *,
        output_dir: Path,
        method: str,
    ) -> None:
        status = "PARTIAL" if self._result_partial(result) else ("OK" if self._result_complete(result) else "FAIL")
        if self._log_passwords:
            password = result.password if result.password else "<none>"
            pw = f" password={password}"
        else:
            pw = ""
        self._emit(
            f"{prefix}[{status}] entry={entry_name} method={method} "
            f"tool={result.tool}{pw} output={output_dir}"
        )
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
        return None

    def _has_any_file(self, root: Path) -> bool:
        try:
            return any(path.is_file() for path in root.rglob("*"))
        except OSError:
            return False

    def _has_nonempty_file(self, root: Path) -> bool:
        # A zero-byte output is not a usable result and must not stop retries.
        # Delegates to the shared guard so symlinks/special files are never counted.
        return contains_nonempty_regular_file(root)

    @staticmethod
    def _result_partial(result: ExtractionResult) -> bool:
        # ExtractionResult.partial_output is added by a parallel worker; tolerate
        # its absence so this module works before and after that change.
        return bool(getattr(result, "partial_output", False))

    @classmethod
    def _result_complete(cls, result: ExtractionResult) -> bool:
        return bool(result.ok and result.exit_code in (0, None) and not cls._result_partial(result))

    @staticmethod
    def _partial_result_message(message: str | None) -> str:
        if not message:
            return PARTIAL_WARNING
        if message.startswith("未完成"):
            return message
        return f"未完成：{message}"

    @staticmethod
    def _result_stop_all(result: ExtractionResult) -> bool:
        # Consult the extraction service's own disposition. A serious no-output
        # failure (missing volume / permission / disk-write / space / exit
        # 7/8/124/255) must not trigger an in-place rename or disguise restore.
        disposition = ExtractionService.classify_failure(
            result.message, exit_code=result.exit_code
        )
        return disposition == "stop_all"

    @staticmethod
    def _source_signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime_ns)

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
