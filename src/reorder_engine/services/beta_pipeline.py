from __future__ import annotations

import shutil
import re
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Callable

from reorder_engine.domain.models import ArchiveKind, ArchiveProbe, ExtractionRequest, ExtractionResult, VolumeSet
from reorder_engine.interfaces.grouping import VolumeGroupingStrategy
from reorder_engine.services.archive_naming import split_archive_name
from reorder_engine.services.cleaning import SafeRenamer
from reorder_engine.services.config import BetaDeepExtractConfig
from reorder_engine.services.decrypting import DecryptionService
from reorder_engine.services.extracting import ExtractionService
from reorder_engine.services.flattening import deepest_wrapper_dir
from reorder_engine.services.rename_session import RenameSession
from reorder_engine.services.restoring import RestorationService


@dataclass(frozen=True)
class BetaRunResult:
    ok_count: int
    fail_count: int
    total: int


@dataclass(frozen=True)
class CandidateAttempt:
    path: Path
    rollbacks: tuple = ()


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
        self._renamer = SafeRenamer()

    def run(self, *, dry_run: bool = False) -> BetaRunResult:
        success_dir = self._folder / "success"
        archives_dir = success_dir / "archives"
        intermediate_dir = self._folder / "intermediate"
        final_root = self._folder / "final"
        error_dir = self._folder / "error_files"
        deferred_volume_dir = self._folder / "deferred_volumes"
        if not dry_run:
            archives_dir.mkdir(parents=True, exist_ok=True)
            intermediate_dir.mkdir(parents=True, exist_ok=True)
            final_root.mkdir(parents=True, exist_ok=True)
            error_dir.mkdir(parents=True, exist_ok=True)
            deferred_volume_dir.mkdir(parents=True, exist_ok=True)

        all_files = [p for p in self._folder.glob("*") if p.is_file()]
        all_files = [p for p in all_files if p.name not in self._exclude_names and p.suffix.lower() not in self._exclude_exts]
        volume_sets = self._grouper.group(all_files)
        self._emit(f"SCAN: folder={self._folder} files={len(all_files)} groups={len(volume_sets)} dry_run={dry_run}")

        ok = 0
        fail = 0
        for volume_set in volume_sets:
            if self._is_in_result_dirs(volume_set, {success_dir, intermediate_dir, final_root, error_dir, deferred_volume_dir}):
                continue

            package_name = self._package_name(volume_set.entry.name)
            package_root = intermediate_dir / package_name
            candidates = self._entry_candidates(volume_set, package_root / "variants" / "L1", dry_run=dry_run)
            result, layer1_dir = self._extract_first_success(
                volume_set,
                candidates,
                package_root / "L1",
                dry_run=dry_run,
                preferred_password=None,
            )

            if result.ok and layer1_dir is not None:
                deep_ok, final_dir, message = self._continue_after_extract(
                    package_name=package_name,
                    package_root=package_root,
                    start_dir=layer1_dir,
                    final_root=final_root,
                    dry_run=dry_run,
                    preferred_password=result.password,
                )
                if deep_ok:
                    ok += 1
                    self._move_original_members(result.volume_set, archives_dir, package_name=package_name, dry_run=dry_run)
                    if message:
                        self._emit(f"FINAL: {message}")
                    if final_dir is not None:
                        self._emit(f"FINAL-DIR: {final_dir}")
                    continue

            if not result.ok and layer1_dir is not None and self._has_any_file(layer1_dir):
                fail_category = self._failure_category(result, layer1_dir)
                moved_dir = self._move_partial_outputs_to_error(
                    layer1_dir,
                    error_dir / fail_category,
                    package_name=package_name,
                    dry_run=dry_run,
                )
                self._move_original_members(result.volume_set, archives_dir, package_name=package_name, dry_run=dry_run)
                if result.message:
                    self._emit(f"ERROR-PARTIAL[{fail_category}]: {self._summarize_message(result.message)}")
                if moved_dir is not None:
                    self._emit(f"ERROR-PARTIAL-DIR: {moved_dir}")
                ok += 1
                continue

            if self._is_missing_volume(result.message):
                self._emit(f"MISSING-VOLUME: keep-in-place entry={volume_set.entry.name}")
                continue

            fail += 1
            fail_category = self._failure_category(result, volume_set.entry)
            self._move_original_members(volume_set, error_dir / fail_category, package_name=package_name, dry_run=dry_run)
            if result.message:
                self._emit(f"ERROR-FILE[{fail_category}]: {self._summarize_message(result.message)}")

        if not dry_run:
            self._remove_empty_dirs(intermediate_dir)
            self._remove_empty_dirs(final_root)
            self._remove_empty_dirs(error_dir)
            self._remove_empty_dirs(success_dir)
            self._remove_empty_dirs(deferred_volume_dir)
        return BetaRunResult(ok_count=ok, fail_count=fail, total=ok + fail)

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
            return self._extract_volume_set_first_success(vs, layer_root, dry_run=dry_run, preferred_password=preferred_password)

        last = ExtractionResult(volume_set=vs, ok=False, tool="none", message="No candidate executed")
        for index, attempt in enumerate(candidates, start=1):
            candidate = attempt.path
            request_vs = VolumeSet(entry=candidate, members=(candidate,), group_key=vs.group_key)
            res, out_dir = self._extract_with_variant_attempts(
                request_vs,
                layer_root / f"attempt_{index}",
                dry_run=dry_run,
                preferred_password=preferred_password,
            )
            last = res
            if res.ok:
                return res, out_dir
            self._rollback_attempt(attempt, dry_run=dry_run)

        force_attempt = self._force_apate_attempt_if_useful(vs.entry, dry_run=dry_run)
        if force_attempt is not None:
            request_vs = VolumeSet(entry=force_attempt.path, members=(force_attempt.path,), group_key=vs.group_key)
            res, out_dir = self._extract_with_variant_attempts(
                request_vs,
                layer_root / f"attempt_{len(candidates) + 1}_force_apate",
                dry_run=dry_run,
                preferred_password=preferred_password,
            )
            last = res
            if res.ok:
                return res, out_dir
            self._rollback_attempt(force_attempt, dry_run=dry_run)
        return last, None

    def _extract_volume_set_first_success(
        self,
        vs: VolumeSet,
        layer_root: Path,
        *,
        dry_run: bool,
        preferred_password: str | None = None,
    ) -> tuple[ExtractionResult, Path | None]:
        last, out_dir = self._extract_with_variant_attempts(
            vs,
            layer_root / "attempt_1",
            dry_run=dry_run,
            preferred_password=preferred_password,
        )
        if last.ok:
            return last, out_dir

        normalized = self._normalize_middle_numbered_volume_set(vs, dry_run=dry_run)
        if normalized is None:
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
        if result.ok or (normalized_out_dir is not None and self._has_any_file(normalized_out_dir)):
            return result, normalized_out_dir

        session.rollback_best_effort(dry_run=dry_run)
        self._emit(
            f"VOLUME-RENAME-ROLLBACK: {normalized_vs.entry.name} -> {vs.entry.name} rule=middle-numbered-volume"
        )
        return result, None

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
            final_dir = self._promote_final_dir(start_dir, final_root, package_name=package_name, dry_run=dry_run)
            return True, final_dir, "depth=1"

        max_depth = max(1, int(self._deep.max_depth))
        min_archive_bytes = max(self._archive_min_mb, int(self._deep.min_archive_mb)) * 1024 * 1024
        final_single_bytes = max(1, int(self._deep.final_single_mb)) * 1024 * 1024
        current_dir = start_dir
        current_password = preferred_password
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
                )
                if res.ok:
                    if res.password:
                        current_password = res.password
                    current_dir = target_dir if target_dir is not None else target_dir_root
                    extracted = True
                    break
                self._rollback_attempt(attempt, dry_run=dry_run)
            if not extracted:
                if deferred and not self._has_any_file(current_dir):
                    self._remove_empty_dirs(current_dir)
                    return True, None, f"depth={depth} reason=deferred-volume-fragments"
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
    ) -> list[CandidateAttempt]:
        base_candidates = self._restore.build_post_extract_candidates(
            folder,
            workspace=workspace,
            min_archive_bytes=min_archive_bytes,
            final_single_bytes=final_single_bytes,
            dry_run=dry_run,
        )
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
                out.append(attempt)
        return out

    def _extract_with_variant_attempts(
        self,
        volume_set: VolumeSet,
        base_output_dir: Path,
        *,
        dry_run: bool,
        prefix: str = "EXTRACT",
        preferred_password: str | None = None,
    ) -> tuple[ExtractionResult, Path | None]:
        probe = self._restore.identify(volume_set.entry)
        attempt_vs = volume_set
        direct_result, direct_dir = self._run_extract_attempt(
            attempt_vs,
            probe=probe,
            output_dir=base_output_dir,
            dry_run=dry_run,
            prefix=prefix,
            preferred_password=preferred_password,
        )
        if direct_result.ok or len(volume_set.members) > 1:
            return direct_result, direct_dir

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
            )
            last = result
            if result.ok:
                return result, out_dir
            session.rollback_best_effort(dry_run=dry_run)
            self._emit(f"RENAME-ROLLBACK: {renamed_entry.name} -> {volume_set.entry.name} rule={plan.rule_name}")

        return last, None

    def _run_extract_attempt(
        self,
        volume_set: VolumeSet,
        *,
        probe: ArchiveProbe,
        output_dir: Path,
        dry_run: bool,
        prefix: str,
        preferred_password: str | None = None,
    ) -> tuple[ExtractionResult, Path | None]:
        req = ExtractionRequest(
            volume_set=volume_set,
            output_dir=output_dir,
            passwords=self._passwords,
            preferred_password=preferred_password,
        )
        result = self._extractor.extract_one(req, preference="auto", probe=probe, dry_run=dry_run)
        self._emit_extract_result(prefix, volume_set.entry.name, result)
        if result.ok or self._has_any_file(output_dir):
            return result, output_dir
        return result, None

    def _candidate_chain(self, path: Path, *, probe: ArchiveProbe, workspace: Path, dry_run: bool) -> list[CandidateAttempt]:
        if probe.kind == ArchiveKind.ARCHIVE:
            return [CandidateAttempt(path)]
        if probe.kind == ArchiveKind.APATE:
            restored, rollbacks = self._restore.restore_with_rollbacks(path, workspace=workspace, dry_run=dry_run)
            return [CandidateAttempt(candidate, tuple(rollbacks)) for candidate in (restored or [path])]
        restored, rollbacks = self._restore.restore_with_rollbacks(path, workspace=workspace, dry_run=dry_run)
        return [CandidateAttempt(candidate, tuple(rollbacks)) for candidate in (restored or [path])]

    def _rollback_attempt(self, attempt: CandidateAttempt, *, dry_run: bool) -> None:
        if not attempt.rollbacks:
            return
        self._restore.rollback_apate(list(attempt.rollbacks), dry_run=dry_run)
        self._emit(f"APATE-ROLLBACK: {attempt.path.name}")

    def _force_apate_attempt_if_useful(self, path: Path, *, dry_run: bool) -> CandidateAttempt | None:
        probe = self._restore.identify(path)
        if probe.kind != ArchiveKind.UNKNOWN:
            return None
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mkv", ".avi", ".mov", ".exe"}:
            return None
        fn = getattr(self._restore, "force_apate_restore_with_rollbacks", None)
        if not callable(fn):
            return None
        restored, rollbacks = fn(path, dry_run=dry_run)
        if restored is None:
            return None
        self._emit(f"APATE-FORCE-TRY: {path.name}")
        return CandidateAttempt(restored, tuple(rollbacks))

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

    def _deferred_volume_group_name(self, path: Path) -> str | None:
        name = path.name
        match = re.match(r"^(?P<base>.+)\.(?P<ext>7z|zip)\.(?P<idx>\d{3})$", name, flags=re.IGNORECASE)
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
