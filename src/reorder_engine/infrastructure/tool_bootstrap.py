from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from reorder_engine.infrastructure.sevenzip_bootstrap import SevenZipBootstrapper
from reorder_engine.services.config import AppConfig, ConfigManager


@dataclass(frozen=True)
class ToolEnsureResult:
    name: str
    ok: bool
    required: bool
    exe: Path | None
    message: str


@dataclass(frozen=True)
class ToolPrepareSummary:
    results: tuple[ToolEnsureResult, ...]

    @property
    def required_ok(self) -> bool:
        return all(result.ok for result in self.results if result.required)

    def get(self, name: str) -> ToolEnsureResult | None:
        key = name.lower()
        for result in self.results:
            if result.name.lower() == key:
                return result
        return None


class ExtractToolBootstrapper:
    """Prepare Windows archive extractor executables and persist their paths."""

    def ensure_all(self, cfg: AppConfig, cfg_mgr: ConfigManager) -> ToolPrepareSummary:
        seven = SevenZipBootstrapper().ensure(cfg, cfg_mgr)
        refreshed = cfg_mgr.to_app_config()

        results = [
            ToolEnsureResult("7z", seven.ok, True, seven.exe, seven.message),
            self._ensure_optional(
                "unrar",
                cfg=refreshed,
                cfg_mgr=cfg_mgr,
                configured=refreshed.tools.unrar.exe,
                setter=cfg_mgr.set_unrar_exe,
                candidates=("Rar.exe", "UnRAR.exe", "rar.exe", "unrar.exe"),
                package_names=("winrar-cli.zip", "rar-cli.zip", "rar.zip", "unrar.zip"),
                package_dir_name="rar",
                known_roots=(
                    Path(r"D:\RAR"),
                    Path(r"C:\Program Files\WinRAR"),
                    Path(r"C:\Program Files (x86)\WinRAR"),
                ),
            ),
            self._ensure_optional(
                "bandizip",
                cfg=refreshed,
                cfg_mgr=cfg_mgr,
                configured=refreshed.tools.bandizip.exe,
                setter=cfg_mgr.set_bandizip_exe,
                candidates=("bz.exe", "Bandizip.exe", "bandizip.exe", "bz"),
                package_names=("bandizip-cli.zip", "bandizip.zip", "bz.zip"),
                package_dir_name="bandizip",
                known_roots=(
                    Path(r"D:\bandzip"),
                    Path(r"D:\Bandizip"),
                    Path(r"C:\Program Files\Bandizip"),
                    Path(r"C:\Program Files (x86)\Bandizip"),
                ),
            ),
        ]
        return ToolPrepareSummary(tuple(results))

    def _ensure_optional(
        self,
        name: str,
        *,
        cfg: AppConfig,
        cfg_mgr: ConfigManager,
        configured: Path | None,
        setter: Callable[[Path], None],
        candidates: tuple[str, ...],
        package_names: tuple[str, ...],
        package_dir_name: str,
        known_roots: tuple[Path, ...],
    ) -> ToolEnsureResult:
        if configured and configured.exists():
            return ToolEnsureResult(name, True, False, configured, f"{name} found from config")

        found = self._find_project_tool(cfg.root_dir, candidates)
        if found:
            setter(found)
            cfg_mgr.save()
            return ToolEnsureResult(name, True, False, found, f"{name} found from tools/")

        extracted = self._extract_first_package(cfg.root_dir, package_names, package_dir_name)
        if extracted:
            found = self._find_in_root(extracted, candidates)
            if found:
                setter(found)
                cfg_mgr.save()
                return ToolEnsureResult(name, True, False, found, f"{name} extracted from tools/_packages")

        for root in known_roots:
            found = self._find_in_root(root, candidates)
            if found:
                setter(found)
                cfg_mgr.save()
                return ToolEnsureResult(name, True, False, found, f"{name} found from known install path")

        for candidate in candidates:
            found_raw = shutil.which(candidate)
            if found_raw:
                found = Path(found_raw)
                setter(found)
                cfg_mgr.save()
                return ToolEnsureResult(name, True, False, found, f"{name} found from PATH")

        return ToolEnsureResult(name, False, False, None, f"{name} not found; optional extractor left unconfigured")

    def _find_project_tool(self, root_dir: Path, candidates: tuple[str, ...]) -> Path | None:
        return self._find_in_root(root_dir / "tools", candidates)

    def _find_in_root(self, root: Path, candidates: tuple[str, ...]) -> Path | None:
        if not root.exists():
            return None
        for candidate in candidates:
            direct = root / candidate
            if direct.exists():
                return direct
            matches = sorted(root.rglob(candidate), key=lambda p: (self._tool_score(p), len(str(p)), str(p).lower()))
            if matches:
                return matches[0]
        return None

    def _tool_score(self, path: Path) -> int:
        lowered = path.name.lower()
        if lowered in {"7z.exe", "bz.exe", "rar.exe", "unrar.exe"}:
            return 0
        return 1

    def _extract_first_package(self, root_dir: Path, package_names: tuple[str, ...], package_dir_name: str) -> Path | None:
        package_root = root_dir / "tools" / "_packages"
        if not package_root.exists():
            return None
        for package_name in package_names:
            archive = package_root / package_name
            if not archive.exists():
                continue
            dest = root_dir / "tools" / package_dir_name
            self._safe_extract_zip(archive, dest)
            return dest
        return None

    def _safe_extract_zip(self, archive: Path, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        dest_root = dest.resolve()
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                target = (dest / member.filename).resolve()
                if not target.is_relative_to(dest_root):
                    raise RuntimeError(f"Refusing unsafe zip member: {member.filename}")
            zf.extractall(dest)
