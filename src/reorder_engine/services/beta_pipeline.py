from __future__ import annotations

import re
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from reorder_engine.domain.models import ExtractionRequest, ExtractionResult, VolumeSet
from reorder_engine.interfaces.grouping import VolumeGroupingStrategy
from reorder_engine.services.archive_naming import split_archive_name
from reorder_engine.services.cleaning import FilenameCleaningService, SafeRenamer
from reorder_engine.services.decrypting import DecryptionService
from reorder_engine.services.extracting import ExtractionService
from reorder_engine.services.restoring import RestorationService
from reorder_engine.services.rename_session import RenameSession
from reorder_engine.services.config import BetaDeepExtractConfig


@dataclass(frozen=True)
class BetaRunResult:
    ok_count: int
    fail_count: int
    total: int


class BetaFolderPipeline:
    """Beta 目标：

    - 处理一个目录（通常是 bat 所在目录）
    - 尽量把目录内文件“变成可解压的样子”（清洗命名 + 必要时猜后缀）
    - 解压输出到 success/
    - 失败时把“处理过的文件（压缩包/分卷）”移动到 failed/
    - 成功时把原压缩包移动到 success/archives/
    """

    def __init__(
        self,
        *,
        folder: Path,
        cleaning_service: FilenameCleaningService,
        renamer: SafeRenamer,
        decrypt_service: DecryptionService,
        restore_service: RestorationService,
        grouper: VolumeGroupingStrategy,
        extractor: ExtractionService,
        passwords: tuple[str, ...],
        exclude_names: set[str] | None = None,
        exclude_exts: set[str] | None = None,
        guess_suffixes: list[str] | None = None,
        log_passwords: bool = False,
        deep_extract: BetaDeepExtractConfig | None = None,
        emit: Callable[[str], None] | None = None,
    ):
        self._folder = folder
        self._cleaning = cleaning_service
        self._renamer = renamer
        self._decrypt = decrypt_service
        self._restore = restore_service
        self._grouper = grouper
        self._extractor = extractor
        self._passwords = passwords
        self._exclude_names = exclude_names or {"config.json"}
        self._exclude_exts = {e.lower() for e in (exclude_exts or {".bat", ".cmd", ".ps1", ".py", ".json", ".md"})}
        self._guess_suffixes = guess_suffixes or [".7z", ".zip", ".rar"]
        self._log_passwords = bool(log_passwords)
        self._deep = deep_extract or BetaDeepExtractConfig(enabled=False, max_depth=4, min_archive_mb=100, final_single_mb=200)
        self._emit = emit or (lambda s: print(s))

    def run(self, *, dry_run: bool = False) -> BetaRunResult:
        # Layout:
        # - success/archives/ : store original archives for successful packages
        # - intermediate/<package>/L1.. : store intermediate extraction products (layered)
        # - final/<package>/ : store final artifacts (videos/images/many files)
        success_dir = self._folder / "success"
        archives_dir = success_dir / "archives"

        intermediate_dir = self._folder / "intermediate"
        final_root = self._folder / "final"
        tmp_root = intermediate_dir / "_tmp"
        failed_dir = self._folder / "failed"
        if not dry_run:
            success_dir.mkdir(exist_ok=True)
            archives_dir.mkdir(parents=True, exist_ok=True)
            intermediate_dir.mkdir(exist_ok=True)
            final_root.mkdir(exist_ok=True)
            tmp_root.mkdir(parents=True, exist_ok=True)
            failed_dir.mkdir(exist_ok=True)

        # Beta 默认“目录内文件都当成可解压候选”，通过配置排除列表避免把控制文件移动走
        all_files = [p for p in self._folder.glob("*") if p.is_file()]
        all_files = [
            p
            for p in all_files
            if p.name not in self._exclude_names and p.suffix.lower() not in self._exclude_exts
        ]

        # 先对“全部文件”做分卷分组（未识别的也会按 single 分组）
        volume_sets = self._grouper.group(all_files)

        self._emit(f"SCAN: folder={self._folder} files={len(all_files)} groups={len(volume_sets)} dry_run={dry_run}")

        ok = 0
        fail = 0
        for vs in volume_sets:
            # 忽略 success/failed 中的文件（如果用户反复跑）
            if self._is_in_result_dirs(vs, {success_dir, intermediate_dir, final_root, failed_dir}):
                continue

            session = RenameSession.create(self._renamer)
            renamed_vs = self._clean_rename_volume_set(vs, session=session, dry_run=dry_run)

            # 预处理：解密准备/还原（默认直通）
            prepared_members: list[Path] = []
            pre_ok = True
            try:
                for p in renamed_vs.members:
                    for pp in self._decrypt.prepare(p, dry_run=dry_run):
                        prepared_members.extend(self._restore.restore(pp, dry_run=dry_run))
            except Exception:  # noqa: BLE001
                # 暂不处理解密/还原：如果你后续接入真实策略，异常就直接按失败处理
                pre_ok = False

            # 重建 VolumeSet：entry 尽量指向还原后的同名文件（若还原返回多个，取第一个）
            entry = prepared_members[0] if prepared_members else renamed_vs.entry
            prepared_vs = VolumeSet(entry=entry, members=tuple(prepared_members or renamed_vs.members), group_key=renamed_vs.group_key)

            res, layer1_dir = (
                self._try_extract_with_suffix_guesses(prepared_vs, intermediate_dir=intermediate_dir, dry_run=dry_run)
                if pre_ok
                else (ExtractionResult(volume_set=prepared_vs, ok=False, tool="pre", message="preprocess failed"), None)
            )

            deep_ok = True
            deep_message: str | None = None
            final_dir: Path | None = None
            if res.ok and layer1_dir is not None and self._deep.enabled:
                deep_ok, final_dir, deep_message = self._deep_extract(
                    package_root=layer1_dir.parent,
                    start_dir=layer1_dir,
                    failed_dir=failed_dir,
                    final_root=final_root,
                    dry_run=dry_run,
                )

            if res.ok and (not self._deep.enabled or deep_ok):
                ok += 1
                # 成功：把原压缩包/分卷移动到 success/archives；最终产物在 final/（若 deep enabled）
                # 注意：单文件场景可能发生“猜后缀改名”（例如 *.7z删除 -> *.7z），
                # 此时 renamed_vs 仍指向旧路径。应以 res.volume_set 为准归档。
                self._move_members(res.volume_set, archives_dir, dry_run=dry_run)
                if deep_message:
                    self._emit(f"DEEP[OK]: {deep_message}")
                if final_dir is not None:
                    self._emit(f"FINAL: {final_dir}")
            else:
                fail += 1
                if res.ok and self._deep.enabled and not deep_ok and deep_message:
                    self._emit(f"DEEP[FAIL]: {deep_message}")

                # 缺分卷：回滚改名但不移动文件（方便补齐分卷后重试）
                if self._is_missing_volume(res.message):
                    self._emit(f"MISSING-VOLUME: keep-in-place entry={prepared_vs.entry.name}")
                    session.rollback_best_effort(dry_run=dry_run)
                    continue

                # 其它失败：尽量把文件名回滚成原样，再分流到 failed
                session.rollback_best_effort(dry_run=dry_run)
                rolled_vs = self._refresh_members_after_rollback(vs, renamed_vs)
                self._move_members(rolled_vs, failed_dir, dry_run=dry_run)

        # Post-process final outputs: flatten redundant wrapper directories.
        # Example: final/<pkg>/<only-child>/...  ->  final/<only-child>/...
        self._flatten_final_root(final_root, dry_run=dry_run)

        # Cleanup: remove empty directories left by intermediate/final moves.
        if not dry_run:
            self._remove_empty_dirs(intermediate_dir)
            self._remove_empty_dirs(final_root)
            self._remove_empty_dirs(failed_dir)
            self._remove_empty_dirs(success_dir)

        return BetaRunResult(ok_count=ok, fail_count=fail, total=ok + fail)

    def _flatten_final_root(self, final_root: Path, *, dry_run: bool) -> None:
        if not final_root.exists() or not final_root.is_dir():
            return

        junk_files = {"desktop.ini", "thumbs.db"}

        # Only flatten when a directory is a pure wrapper: no files, and exactly
        # one child directory (possibly repeated). This avoids destroying meaningful
        # multi-folder structures.
        for wrapper in list(final_root.iterdir()):
            if not wrapper.is_dir():
                continue

            leaf = wrapper
            while True:
                try:
                    entries = list(leaf.iterdir())
                except Exception:  # noqa: BLE001
                    break
                dirs = [p for p in entries if p.is_dir()]
                files = [p for p in entries if p.is_file() and p.name.lower() not in junk_files]
                if files or len(dirs) != 1:
                    break
                leaf = dirs[0]

            if leaf == wrapper:
                continue

            target = self._dedupe_dir(final_root / leaf.name)
            if dry_run:
                self._emit(f"FLATTEN(dry): {leaf} -> {target}")
                continue

            try:
                final_root.mkdir(parents=True, exist_ok=True)
                self._move_path(leaf, target)
                self._emit(f"FLATTEN: {leaf.name} -> {target}")
            except Exception as e:  # noqa: BLE001
                self._emit(f"FLATTEN[SKIP]: move failed src={leaf} dst={target} err={type(e).__name__}: {e}")
                continue

            # Remove now-empty wrapper directories.
            try:
                # Best-effort remove known junk files so wrapper can be removed.
                for junk in junk_files:
                    jp = wrapper / junk
                    if jp.exists() and jp.is_file():
                        try:
                            jp.unlink()
                        except Exception:  # noqa: BLE001
                            pass
                self._remove_empty_dirs(wrapper)
                if wrapper.exists() and not any(wrapper.iterdir()):
                    wrapper.rmdir()
            except Exception:  # noqa: BLE001
                pass

    def _as_extended_windows_path(self, path: Path) -> str:
        # Windows long-path support: prefix with \\?\ for absolute paths.
        # This helps operations like shutil.move when paths exceed MAX_PATH.
        p = str(path.resolve())
        if p.startswith("\\\\?\\"):
            return p
        if p.startswith("\\\\"):
            # UNC path: \\server\share\... -> \\?\UNC\server\share\...
            return "\\\\?\\UNC\\" + p.lstrip("\\")
        return "\\\\?\\" + p

    def _move_path(self, src: Path, dst: Path) -> None:
        # Try normal move first; on Windows retry with extended-length paths.
        try:
            shutil.move(str(src), str(dst))
            return
        except Exception:
            if os.name != "nt":
                raise
        shutil.move(self._as_extended_windows_path(src), self._as_extended_windows_path(dst))

    def _is_in_result_dirs(self, vs: VolumeSet, dirs: set[Path]) -> bool:
        for p in vs.members:
            for d in dirs:
                if d in p.parents:
                    return True
        return False

    def _clean_rename_volume_set(self, vs: VolumeSet, *, session: RenameSession, dry_run: bool) -> VolumeSet:
        # 对每个成员：只清洗 base，保留分卷/格式尾巴；避免破坏分卷模式
        renamed: list[Path] = []
        for p in vs.members:
            parts = split_archive_name(p.name)
            cleaned_base = self._cleaning.clean_stem(parts.base)
            dst = p.with_name(f"{cleaned_base}{parts.mid}{parts.end}")
            renamed.append(session.rename(p, dst, dry_run=dry_run))

        # 更新 entry 指向同名匹配的文件
        new_entry = next((p for p in renamed if p.name.lower() == vs.entry.name.lower()), renamed[0])
        return VolumeSet(entry=new_entry, members=tuple(renamed), group_key=vs.group_key)

    def _refresh_members_after_rollback(self, original: VolumeSet, renamed: VolumeSet) -> VolumeSet:
        # 回滚后成员路径可能变化：尽量优先 original 成员名存在则用 original，否则用 renamed
        members: list[Path] = []
        for before in original.members:
            if before.exists():
                members.append(before)
                continue
            # fallback：按名字在同目录找（例如 dedupe 时）
            cand = before.parent / before.name
            if cand.exists():
                members.append(cand)

        if not members:
            members = list(renamed.members)

        entry = members[0]
        for p in members:
            if p.name.lower() == original.entry.name.lower() and p.exists():
                entry = p
                break
        return VolumeSet(entry=entry, members=tuple(members), group_key=original.group_key)

    def _try_extract_with_suffix_guesses(self, vs: VolumeSet, *, intermediate_dir: Path, dry_run: bool) -> tuple[ExtractionResult, Path | None]:
        # 多分卷：只尝试一次（不猜后缀）
        if len(vs.members) > 1:
            return self._extract_one(vs, intermediate_dir=intermediate_dir, dry_run=dry_run)

        # 单文件：如果已经像压缩包（.7z/.zip/.rar/.001/.7z.001 等），就直接尝试
        entry = vs.entry
        if self._looks_like_archive(entry.name):
            return self._extract_one(vs, intermediate_dir=intermediate_dir, dry_run=dry_run)

        # 否则：按配置顺序尝试“重命名成可解压后缀”再解压（例如先 .7z，再 .zip，再 .rar...）
        original = entry
        guesses = self._guess_suffixes
        last: tuple[ExtractionResult, Path | None] | None = None
        for suf in guesses:
            trial = self._with_full_suffix(original, suf)
            if trial == original:
                continue
            # 如果目标名已存在，跳过该猜测，避免误改名
            if trial.exists():
                continue

            # 这里不记录到主 session（主 session 只负责“清洗改名”），猜测失败会立即改回
            rec = self._renamer.rename_file(original, trial, dry_run=dry_run)
            trial_path = rec.after if rec else original
            trial_vs = VolumeSet(entry=trial_path, members=(trial_path,), group_key=vs.group_key)
            last = self._extract_one(trial_vs, intermediate_dir=intermediate_dir, dry_run=dry_run)
            if last[0].ok:
                return last
            # 失败则尝试下一后缀：把文件名改回 original（便于下一次 rename）
            if rec is not None:
                self._renamer.rename_file(trial_path, original, dry_run=dry_run)

        return last or self._extract_one(vs, intermediate_dir=intermediate_dir, dry_run=dry_run)

    def _extract_one(self, vs: VolumeSet, *, intermediate_dir: Path, dry_run: bool) -> tuple[ExtractionResult, Path | None]:
        """Extract into intermediate/<package>/L1, using a temp directory to avoid junk on failure."""

        package_root = self._dedupe_dir(intermediate_dir / self._success_subdir_name(vs.entry.name))
        out_dir = package_root / "L1"

        tmp_dir = intermediate_dir / "_tmp" / f"{package_root.name}__L1__work"
        tmp_dir = self._dedupe_dir(tmp_dir)

        req = ExtractionRequest(volume_set=vs, output_dir=tmp_dir, passwords=self._passwords)
        res = self._extractor.extract_one(req, preference="auto", dry_run=dry_run)

        status = "OK" if res.ok else "FAIL"
        tool = res.tool or "?"
        pw_part = ""
        if res.ok and self._log_passwords and res.password:
            pw_part = f" password={res.password}"
        self._emit(f"EXTRACT[{status}] entry={vs.entry.name} tool={tool}{pw_part}")
        if res.message:
            self._emit(f"  msg: {self._summarize_message(res.message)}")

        if dry_run:
            return res, out_dir if res.ok else None

        if res.ok:
            out_dir.mkdir(parents=True, exist_ok=True)
            if tmp_dir.exists():
                for p in tmp_dir.iterdir():
                    dst = out_dir / p.name
                    if dst.exists():
                        dst = self._dedupe(dst)
                    shutil.move(str(p), str(dst))
            # Cleanup tmp
            shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            # Failed extraction: cleanup temp output to avoid leaving junk in success
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return res, (out_dir if res.ok else None)

    def _deep_extract(
        self,
        *,
        package_root: Path,
        start_dir: Path,
        failed_dir: Path,
        final_root: Path,
        dry_run: bool,
    ) -> tuple[bool, Path | None, str | None]:
        """Iteratively extract nested archives until final criteria is met or max depth reached."""

        max_depth = max(1, int(self._deep.max_depth))
        min_archive_bytes = max(0, int(self._deep.min_archive_mb)) * 1024 * 1024
        final_single_bytes = max(0, int(self._deep.final_single_mb)) * 1024 * 1024

        current_dir = start_dir
        for depth in range(1, max_depth + 1):
            # Per-layer preprocess: clean extracted names again before deciding final/candidates.
            # This is important for nested archives with junk suffixes like "*.7z删除".
            self._preprocess_extracted_folder(current_dir, dry_run=dry_run)

            final_reason = self._is_final_output(current_dir, final_single_bytes=final_single_bytes)
            if final_reason is not None:
                # Move current_dir to final/<package>/
                final_pkg_dir = self._dedupe_dir(final_root / package_root.name)
                if dry_run:
                    return True, final_pkg_dir, f"depth={depth} reason={final_reason} (dry-run)"
                final_pkg_dir.parent.mkdir(parents=True, exist_ok=True)
                if final_pkg_dir.exists():
                    final_pkg_dir = self._dedupe_dir(final_pkg_dir)
                shutil.move(str(current_dir), str(final_pkg_dir))
                return True, final_pkg_dir, f"depth={depth} reason={final_reason}"

            # Not final: pick next archive candidates
            candidates = self._find_archive_candidates(current_dir, min_archive_bytes=min_archive_bytes, final_single_bytes=final_single_bytes)
            if not candidates:
                # If we can't go deeper but we already have extracted content,
                # treat it as final instead of marking the whole package failed.
                if self._has_any_file(current_dir):
                    final_pkg_dir = self._dedupe_dir(final_root / package_root.name)
                    if dry_run:
                        return True, final_pkg_dir, f"depth={depth} reason=no-candidates"
                    final_pkg_dir.parent.mkdir(parents=True, exist_ok=True)
                    if final_pkg_dir.exists():
                        final_pkg_dir = self._dedupe_dir(final_pkg_dir)
                    shutil.move(str(current_dir), str(final_pkg_dir))
                    return True, final_pkg_dir, f"depth={depth} reason=no-candidates"
                return False, None, f"depth={depth} no candidates under {current_dir.name}"

            extracted_any = False
            for idx, cand in enumerate(candidates, start=1):
                next_layer_root = package_root / f"L{depth + 1}"
                target = next_layer_root / self._safe_name(cand.stem)
                target = self._dedupe_dir(target)
                res = self._extract_nested_file(cand, output_dir=target, dry_run=dry_run)
                if res.ok:
                    extracted_any = True
                    current_dir = target
                    break

                # Move failed nested archive into failed/<package>/nested/
                nested_failed = failed_dir / package_root.name / "nested"
                if dry_run:
                    self._emit(f"NESTED-FAIL(dry): {cand} -> {nested_failed / cand.name}")
                else:
                    nested_failed.mkdir(parents=True, exist_ok=True)
                    dst = self._dedupe(nested_failed / cand.name)
                    try:
                        shutil.move(str(cand), str(dst))
                    except Exception:  # noqa: BLE001
                        # If move fails (locked etc.), just leave it; still continue.
                        pass
                self._emit(f"DEEP: candidate failed ({idx}/{len(candidates)}) file={cand.name}")

            if not extracted_any:
                # Similar fallback: if we have non-empty content already, don't
                # mark as failure just because nested candidates couldn't be extracted.
                if self._has_any_file(current_dir):
                    final_pkg_dir = self._dedupe_dir(final_root / package_root.name)
                    if dry_run:
                        return True, final_pkg_dir, f"depth={depth} reason=candidates-failed-nonempty"
                    final_pkg_dir.parent.mkdir(parents=True, exist_ok=True)
                    if final_pkg_dir.exists():
                        final_pkg_dir = self._dedupe_dir(final_pkg_dir)
                    shutil.move(str(current_dir), str(final_pkg_dir))
                    return True, final_pkg_dir, f"depth={depth} reason=candidates-failed-nonempty"
                return False, None, f"depth={depth} all candidates failed"

        return False, None, f"exceeded max_depth={max_depth}"

    def _has_any_file(self, root: Path) -> bool:
        try:
            for p in root.rglob("*"):
                if p.is_file():
                    return True
        except Exception:  # noqa: BLE001
            return False
        return False

    def _preprocess_extracted_folder(self, folder: Path, *, dry_run: bool) -> None:
        if not folder.exists() or not folder.is_dir():
            return

        # Rename deepest first to keep paths stable.
        items: list[Path] = [p for p in folder.rglob("*")]
        items.sort(key=lambda p: len(p.parts), reverse=True)

        # 1) Normalize "archive-ish" names (e.g. *.7z删除 -> *.7z)
        for p in items:
            if not p.is_file():
                continue
            norm = self._normalize_archive_filename(p)
            if norm is not None and norm != p:
                if dry_run:
                    self._emit(f"PRE: NORM(dry) {p.name} -> {norm.name}")
                else:
                    try:
                        p.rename(norm)
                    except Exception:  # noqa: BLE001
                        pass

        # Refresh snapshot after possible renames
        items = [p for p in folder.rglob("*")]
        items.sort(key=lambda p: len(p.parts), reverse=True)

        # 2) Clean filenames + directory names
        for p in items:
            if p.is_file():
                parts = split_archive_name(p.name)
                cleaned_base = self._cleaning.clean_stem(parts.base)
                dst = p.with_name(f"{cleaned_base}{parts.mid}{parts.end}")
                if dst != p:
                    try:
                        self._renamer.rename_file(p, dst, dry_run=dry_run)
                    except Exception:  # noqa: BLE001
                        pass
            elif p.is_dir():
                cleaned = self._cleaning.clean_stem(p.name)
                if cleaned and cleaned != p.name:
                    dst = p.with_name(cleaned)
                    if dst.exists():
                        dst = self._dedupe_dir(dst)
                    if dry_run:
                        self._emit(f"PRE: DIR(dry) {p} -> {dst}")
                    else:
                        try:
                            p.rename(dst)
                        except Exception:  # noqa: BLE001
                            pass

    def _extract_nested_file(self, archive_file: Path, *, output_dir: Path, dry_run: bool) -> ExtractionResult:
        archive_file = self._normalize_archive_filename(archive_file) or archive_file

        # If still not obviously an archive, try suffix guesses (common for misnamed files).
        if not self._looks_like_archive(archive_file.name) and not self._contains_archive_hint(archive_file.name):
            original = archive_file
            for suf in self._guess_suffixes:
                trial = self._with_full_suffix(original, suf)
                if trial == original or trial.exists():
                    continue
                try:
                    rec = self._renamer.rename_file(original, trial, dry_run=dry_run)
                except Exception:  # noqa: BLE001
                    rec = None
                trial_path = rec.after if rec else original
                vs_trial = VolumeSet(entry=trial_path, members=(trial_path,), group_key=trial_path.name)
                tmp_dir = output_dir.parent.parent / "_tmp" / f"{output_dir.parent.name}__{output_dir.name}__work"
                tmp_dir = self._dedupe_dir(tmp_dir)
                req = ExtractionRequest(volume_set=vs_trial, output_dir=tmp_dir, passwords=self._passwords)
                res = self._extractor.extract_one(req, preference="auto", dry_run=dry_run)
                if res.ok:
                    if dry_run:
                        return res

                    # Materialize output and return immediately (avoid double extraction).
                    output_dir.mkdir(parents=True, exist_ok=True)
                    if tmp_dir.exists():
                        for p in tmp_dir.iterdir():
                            dst = output_dir / p.name
                            if dst.exists():
                                dst = self._dedupe(dst)
                            shutil.move(str(p), str(dst))
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    return res

                # cleanup junk
                if not dry_run:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                # revert
                if rec is not None:
                    try:
                        self._renamer.rename_file(trial_path, original, dry_run=dry_run)
                    except Exception:  # noqa: BLE001
                        pass

        vs = VolumeSet(entry=archive_file, members=(archive_file,), group_key=archive_file.name)
        tmp_dir = output_dir.parent.parent / "_tmp" / f"{output_dir.parent.name}__{output_dir.name}__work"
        tmp_dir = self._dedupe_dir(tmp_dir)

        req = ExtractionRequest(volume_set=vs, output_dir=tmp_dir, passwords=self._passwords)
        res = self._extractor.extract_one(req, preference="auto", dry_run=dry_run)

        status = "OK" if res.ok else "FAIL"
        tool = res.tool or "?"
        pw_part = ""
        if res.ok and self._log_passwords and res.password:
            pw_part = f" password={res.password}"
        self._emit(f"DEEP-EXTRACT[{status}] file={archive_file.name} tool={tool}{pw_part}")
        if res.message:
            self._emit(f"  msg: {self._summarize_message(res.message)}")

        if dry_run:
            return res

        if res.ok:
            output_dir.mkdir(parents=True, exist_ok=True)
            if tmp_dir.exists():
                for p in tmp_dir.iterdir():
                    dst = output_dir / p.name
                    if dst.exists():
                        dst = self._dedupe(dst)
                    shutil.move(str(p), str(dst))
            shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return res

    def _is_final_output(self, folder: Path, *, final_single_bytes: int) -> str | None:
        if not folder.exists() or not folder.is_dir():
            return None

        # NOTE: must scan recursively.
        # Common case: L1 contains a single folder, and the real payload
        # (images/videos/etc.) is under that folder.

        direct_files: list[Path] = []
        direct_dirs = 0
        for p in folder.iterdir():
            if p.is_dir():
                direct_dirs += 1
            elif p.is_file():
                direct_files.append(p)

        all_files: list[Path] = [p for p in folder.rglob("*") if p.is_file()]
        all_dirs = sum(1 for p in folder.rglob("*") if p.is_dir())

        # Final criteria should be conservative: default to keep extracting.
        # Only stop when we have strong evidence this is the payload.
        many_files_threshold = 80
        many_dirs_threshold = 10
        many_entries_threshold = 120

        if all_dirs >= many_dirs_threshold:
            return "many-folders"
        if len(all_files) >= many_files_threshold:
            return "many-files"
        if (all_dirs + len(all_files)) >= many_entries_threshold:
            return "many-entries"

        video_exts = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"}
        image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
        strong_payload_exts = {".iso", ".exe", ".apk", ".obb", ".pak"}

        video_count = 0
        image_count = 0
        strong_count = 0
        for f in all_files:
            suf = f.suffix.lower()
            if suf in video_exts:
                video_count += 1
            elif suf in image_exts:
                image_count += 1
            elif suf in strong_payload_exts:
                strong_count += 1

        if video_count >= 1:
            return "has-video"
        if strong_count >= 1:
            return "has-binary"
        if image_count >= 20:
            return "many-images"

        # Single big file is NOT a final signal by default.
        # It might be a misnamed archive (e.g. '*.7z删除'). Let candidate selection handle it.
        _ = (direct_dirs, direct_files, final_single_bytes)
        return None

    def _find_archive_candidates(self, folder: Path, *, min_archive_bytes: int, final_single_bytes: int) -> list[Path]:
        # Search recursively (nested archives may be inside folders)
        files: list[Path] = [p for p in folder.rglob("*") if p.is_file()]
        if not files:
            return []

        def size_of(p: Path) -> int:
            try:
                return p.stat().st_size
            except OSError:
                return 0

        # If folder contains a single file: treat big unknown as candidate too (or misnamed archive)
        direct_files = [p for p in folder.iterdir() if p.is_file()]
        direct_dirs = [p for p in folder.iterdir() if p.is_dir()]
        if len(direct_files) == 1 and not direct_dirs:
            only = direct_files[0]
            s = size_of(only)
            if self._looks_like_archive(only.name) or self._contains_archive_hint(only.name) or s >= min_archive_bytes:
                return [only]

        # Primary: archive-like (including misnamed) and size >= min_archive_bytes
        archive_like = [p for p in files if self._looks_like_archive(p.name) or self._contains_archive_hint(p.name)]
        big_archives = [p for p in archive_like if size_of(p) >= min_archive_bytes]
        big_archives.sort(key=size_of, reverse=True)
        if big_archives:
            return big_archives[:5]

        # Fallback: any archive-like (pick largest)
        archive_like.sort(key=size_of, reverse=True)
        if archive_like:
            return archive_like[:3]

        # Last resort: any large file that *might* be an archive (>= final_single_bytes)
        big_files = [p for p in files if size_of(p) >= final_single_bytes]
        big_files.sort(key=size_of, reverse=True)
        return big_files[:1]

    def _contains_archive_hint(self, name: str) -> bool:
        low = name.lower()
        hints = [
            ".7z.001",
            ".zip.001",
            ".tar.gz",
            ".7z",
            ".zip",
            ".rar",
            ".tar",
            ".tgz",
            ".gz",
            ".bz2",
            ".xz",
        ]
        return any(h in low for h in hints)

    def _normalize_archive_filename(self, path: Path) -> Path | None:
        """Normalize misnamed archive file like '*.7z删除' -> '*.7z' when safe."""

        name = path.name
        low = name.lower()
        # Prefer longer patterns first
        patterns = [
            ".tar.gz",
            ".7z.001",
            ".zip.001",
            ".7z",
            ".zip",
            ".rar",
            ".tar",
            ".tgz",
            ".gz",
            ".bz2",
            ".xz",
        ]
        for pat in patterns:
            idx = low.rfind(pat)
            if idx < 0:
                continue
            end = idx + len(pat)
            if end == len(name):
                return None
            new_name = name[:end]
            if new_name == name:
                return None
            dst = path.with_name(new_name)
            if dst.exists():
                dst = self._dedupe(dst)
            return dst
        return None

    def _remove_empty_dirs(self, root: Path) -> None:
        if not root.exists() or not root.is_dir():
            return
        # bottom-up remove
        dirs = [p for p in root.rglob("*") if p.is_dir()]
        dirs.sort(key=lambda p: len(p.parts), reverse=True)
        for d in dirs:
            try:
                if not any(d.iterdir()):
                    d.rmdir()
            except Exception:  # noqa: BLE001
                pass

    def _safe_name(self, name: str) -> str:
        # keep it simple for Windows paths
        s = name.strip().replace("/", "_").replace("\\", "_")
        return s or "_item"

    def _summarize_message(self, message: str) -> str:
        """Keep main log readable; full output is in tool log."""

        lines = message.splitlines()
        max_lines = 18
        if len(lines) <= max_lines:
            return message.strip()
        head = "\n".join(lines[:max_lines]).strip()
        return f"{head}\n... (truncated)"

    def _move_members(self, vs: VolumeSet, dest_dir: Path, *, dry_run: bool) -> None:
        for p in vs.members:
            if not p.exists():
                continue
            dst = dest_dir / p.name
            if dry_run:
                self._emit(f"MOVE(dry): {p.name} -> {dst}")
                continue
            dst = self._dedupe(dst)
            shutil.move(str(p), str(dst))
            self._emit(f"MOVE: {p.name} -> {dst}")

    def _is_missing_volume(self, message: str | None) -> bool:
        if not message:
            return False
        m = message.lower()
        # 7z: "Missing volume : xxx.z01" / "Unavailable data"
        if "missing volume" in m or "unavailable data" in m:
            return True
        return False

    def _dedupe(self, dst: Path) -> Path:
        if not dst.exists():
            return dst
        stem = dst.stem
        suf = dst.suffix
        for i in range(1, 1000):
            cand = dst.with_name(f"{stem} ({i}){suf}")
            if not cand.exists():
                return cand
        return dst

    def _dedupe_dir(self, dst: Path) -> Path:
        if not dst.exists():
            return dst
        for i in range(1, 1000):
            cand = dst.with_name(f"{dst.name} ({i})")
            if not cand.exists():
                return cand
        return dst

    def _success_subdir_name(self, entry_name: str) -> str:
        parts = split_archive_name(entry_name)
        name = parts.base.strip() or Path(entry_name).stem
        # avoid reserved / empty names
        name = name.replace("/", "_").replace("\\", "_").strip()
        return name or "_archive"

    def _looks_like_archive(self, name: str) -> bool:
        low = name.lower()
        if low.endswith((".7z", ".zip", ".rar", ".tar", ".gz", ".bz2", ".xz", ".tgz")):
            return True
        if low.endswith(".tar.gz"):
            return True
        if low.endswith((".7z.001", ".zip.001")):
            return True
        if low.endswith((".001", ".002", ".003")):
            return True
        if ".part" in low and low.endswith((".rar", ".zip", ".7z")):
            return True
        if re.match(r"^.+\.(r|z)\d{2}$", low):
            return True
        return False

    def _with_full_suffix(self, path: Path, new_suffix: str) -> Path:
        """把文件名的“所有现有后缀”整体替换为 new_suffix（new_suffix 可为 .tar.gz）。"""

        ns = new_suffix if new_suffix.startswith(".") else f".{new_suffix}"
        suffixes = path.suffixes
        if suffixes:
            total = "".join(suffixes)
            base = path.name[: -len(total)]
        else:
            base = path.name
        return path.with_name(base + ns)
