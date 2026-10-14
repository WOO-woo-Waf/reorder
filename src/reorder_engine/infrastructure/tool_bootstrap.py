from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from reorder_engine.infrastructure.repo_tooling import is_managed_cli_tool_path, stage_locked_cli_tools
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

        fixed_lock = refreshed.root_dir / "scripts" / "desktop-tools.lock.json"
        fixed_paths: dict[str, Path] = {}
        fixed_error: str | None = None
        optional_configs = {
            "unrar": refreshed.tools.unrar.exe,
            "bandizip": refreshed.tools.bandizip.exe,
        }
        if fixed_lock.is_file() and any(
            not (path and path.is_file()) or is_managed_cli_tool_path(refreshed.root_dir, name, path)
            for name, path in optional_configs.items()
        ):
            try:
                stage_dir = refreshed.root_dir / "runtime" / "cli-tools"
                stage_locked_cli_tools(
                    refreshed.root_dir,
                    stage_dir,
                    seven.exe if seven.ok else None,
                )
                fixed_paths = {
                    "unrar": stage_dir / "tools" / "unrar" / "UnRAR.exe",
                    "bandizip": stage_dir / "tools" / "bandizip" / "bz.exe",
                }
            except Exception as exc:  # noqa: BLE001 - return the locked bundle failure to the CLI
                fixed_error = str(exc)

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
                required=fixed_lock.is_file(),
                configured_managed=is_managed_cli_tool_path(refreshed.root_dir, "unrar", refreshed.tools.unrar.exe),
                fixed_path=fixed_paths.get("unrar"),
                fixed_error=fixed_error,
                fixed_lock_present=fixed_lock.is_file(),
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
                required=fixed_lock.is_file(),
                configured_managed=is_managed_cli_tool_path(refreshed.root_dir, "bandizip", refreshed.tools.bandizip.exe),
                fixed_path=fixed_paths.get("bandizip"),
                fixed_error=fixed_error,
                fixed_lock_present=fixed_lock.is_file(),
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
        required: bool,
        configured_managed: bool,
        fixed_path: Path | None,
        fixed_error: str | None,
        fixed_lock_present: bool,
        known_roots: tuple[Path, ...],
    ) -> ToolEnsureResult:
        if configured and configured.is_file() and not configured_managed:
            return ToolEnsureResult(name, True, required, configured, f"{name} found from config")

        # The tracked lock has precedence over old repository tool trees and host installs.
        if fixed_lock_present:
            if fixed_error:
                return ToolEnsureResult(name, False, required, None,
                                         f"locked {name} bundle is invalid: {fixed_error}")
            if fixed_path and fixed_path.is_file():
                setter(fixed_path)
                cfg_mgr.save()
                version = "7.13" if name == "unrar" else "7.40.0.1"
                return ToolEnsureResult(name, True, required, fixed_path,
                                        f"{name} found from Git-pinned {version} bundle")
            return ToolEnsureResult(name, False, required, None,
                                    f"locked {name} bundle is missing its executable under runtime/cli-tools")

        found = self._find_project_tool(cfg.root_dir, candidates)
        if found:
            setter(found)
            cfg_mgr.save()
            return ToolEnsureResult(name, True, required, found, f"{name} found from tools/")

        extracted = self._extract_first_package(cfg.root_dir, package_names, package_dir_name)
        if extracted:
            found = self._find_in_root(extracted, candidates)
            if found:
                setter(found)
                cfg_mgr.save()
                return ToolEnsureResult(name, True, required, found, f"{name} extracted from tools/_packages")

        for root in known_roots:
            found = self._find_in_root(root, candidates)
            if found:
                setter(found)
                cfg_mgr.save()
                return ToolEnsureResult(name, True, required, found, f"{name} found from known install path")

        for candidate in candidates:
            found_raw = shutil.which(candidate)
            if found_raw:
                found = Path(found_raw)
                setter(found)
                cfg_mgr.save()
                return ToolEnsureResult(name, True, required, found, f"{name} found from PATH")

        return ToolEnsureResult(name, False, required, None, f"{name} not found; optional extractor left unconfigured")

    def _find_project_tool(self, root_dir: Path, candidates: tuple[str, ...]) -> Path | None:
        return self._find_in_root(root_dir / "tools", candidates)

    def _find_in_root(self, root: Path, candidates: tuple[str, ...]) -> Path | None:
        if not root.exists():
            return None
        for candidate in candidates:
            direct = root / candidate
            if direct.is_file():
                return direct
            matches = sorted(
                (path for path in root.rglob(candidate) if path.is_file()),
                key=lambda p: (self._tool_score(p), len(str(p)), str(p).lower()),
            )
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
