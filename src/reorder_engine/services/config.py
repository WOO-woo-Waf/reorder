from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PathsConfig:
    keywords: Path
    passwords: Path


@dataclass(frozen=True)
class SevenZipDownloadConfig:
    source: str
    prefer: str
    timeout_sec: int


@dataclass(frozen=True)
class SevenZipConfig:
    exe: Path | None
    auto_download: bool
    download: SevenZipDownloadConfig
    install_dir: Path


@dataclass(frozen=True)
class OptionalExeToolConfig:
    exe: Path | None


@dataclass(frozen=True)
class ToolsConfig:
    seven_zip: SevenZipConfig
    unrar: OptionalExeToolConfig
    bandizip: OptionalExeToolConfig


@dataclass(frozen=True)
class BetaFlattenConfig:
    enabled: bool
    exclude_dirs: tuple[str, ...]
    # 非空时：仅当目标目录落在下列路径之一之下时才允许展平（推荐用于固定「下载收件箱」目录）
    allowed_roots: tuple[str, ...]
    # 未配置白名单时：若目标在 reorder_engine 源码仓库内则默认禁止展平
    allow_inside_project_repo: bool


@dataclass(frozen=True)
class BetaExcludeConfig:
    names: tuple[str, ...]
    exts: tuple[str, ...]


@dataclass(frozen=True)
class BetaDeepExtractConfig:
    enabled: bool
    max_depth: int
    min_archive_mb: int
    final_single_mb: int


@dataclass(frozen=True)
class BetaRulesConfig:
    max_restore_rounds: int


@dataclass(frozen=True)
class BetaConfig:
    flatten: BetaFlattenConfig
    exclude: BetaExcludeConfig
    guess_suffixes: tuple[str, ...]
    log_passwords: bool = False
    extractor_order: tuple[str, ...] = ("7z", "unrar", "bandizip")
    preserve_payload_names: bool = True
    duplicates_dir_name: str = "_duplicates"
    path_compress: bool = True
    rules: BetaRulesConfig = BetaRulesConfig(max_restore_rounds=3)
    deep_extract: BetaDeepExtractConfig = BetaDeepExtractConfig(
        enabled=False,
        max_depth=4,
        min_archive_mb=100,
        final_single_mb=200,
    )


@dataclass(frozen=True)
class AppConfig:
    version: int
    config_file: Path
    root_dir: Path
    paths: PathsConfig
    tools: ToolsConfig
    beta: BetaConfig


class ConfigManager:
    """读取/写回 config.json，并将相对路径解析为相对项目根目录。"""

    def __init__(self, config_file: Path, *, root_dir: Path):
        self._config_file = config_file
        self._root_dir = root_dir
        self._data: dict[str, Any] = {}

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    @property
    def config_file(self) -> Path:
        return self._config_file

    def load_or_create_default(self) -> dict[str, Any]:
        if self._config_file.exists():
            self._data = json.loads(self._config_file.read_text(encoding="utf-8"))
            return self._data
        self._config_file.parent.mkdir(parents=True, exist_ok=True)

        keywords_default = "resources/keywords.txt" if (self._root_dir / "resources" / "keywords.txt").exists() else "keywords.txt"
        passwords_default = "resources/passwords.txt" if (self._root_dir / "resources" / "passwords.txt").exists() else "passwords.txt"

        default = {
            "version": 1,
            "beta": {
                "flatten": {
                    "enabled": True,
                    "exclude_dirs": [
                        "success",
                        "extracted",
                        "intermediate",
                        "final",
                        "failed",
                        "error_files",
                        "deferred_volumes",
                        "archives_success",
                        "resources",
                        "tests",
                        "tools",
                        "__pycache__",
                    ],
                    "allowed_roots": [],
                    "allow_inside_project_repo": False,
                },
                "exclude": {"names": ["config.json"], "exts": [".bat", ".cmd", ".ps1", ".py", ".json", ".md", ".log"]},
                "guess_suffixes": [".7z", ".zip", ".rar", ".tar", ".tgz", ".tar.gz", ".gz", ".bz2", ".xz"],
                "log_passwords": False,
                "extractor_order": ["7z", "unrar", "bandizip"],
                "preserve_payload_names": True,
                "duplicates_dir_name": "_duplicates",
                "path_compress": True,
                "rules": {"max_restore_rounds": 3},
                "deep_extract": {"enabled": False, "max_depth": 4, "min_archive_mb": 100, "final_single_mb": 200},
            },
            "paths": {
                "keywords": keywords_default,
                "passwords": passwords_default,
            },
            "tools": {
                "seven_zip": {
                    "exe": None,
                    "auto_download": True,
                    "download": {"source": "7-zip.org", "prefer": "msi-x64", "timeout_sec": 60},
                    "install_dir": "tools/7zip",
                },
                "unrar": {"exe": None},
                "bandizip": {"exe": None},
            },
        }

        # 尽量给 Windows 用户“开箱即用”的默认值（仅在文件真实存在时才写入）。
        # 注意：仍然允许用户在 config.json 里覆盖。
        self._data = default

        bz = Path(r"D:\\bandzip\\bz.exe")
        if bz.exists():
            self.set_bandizip_exe(bz)

        rar_dir = Path(r"D:\\RAR")
        for candidate in ("Rar.exe", "UnRAR.exe"):
            p = rar_dir / candidate
            if p.exists():
                self.set_unrar_exe(p)
                break
        self.save()
        return self._data

    def save(self) -> None:
        self._config_file.write_text(json.dumps(self._data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def set_seven_zip_exe(self, exe: Path) -> None:
        tools = self._data.setdefault("tools", {})
        seven_zip = tools.setdefault("seven_zip", {})
        # 写相对路径，便于随项目移动
        seven_zip["exe"] = str(exe.relative_to(self._root_dir)) if exe.is_relative_to(self._root_dir) else str(exe)

    def set_unrar_exe(self, exe: Path) -> None:
        tools = self._data.setdefault("tools", {})
        unrar = tools.setdefault("unrar", {})
        unrar["exe"] = str(exe.relative_to(self._root_dir)) if exe.is_relative_to(self._root_dir) else str(exe)

    def set_bandizip_exe(self, exe: Path) -> None:
        tools = self._data.setdefault("tools", {})
        bandizip = tools.setdefault("bandizip", {})
        bandizip["exe"] = str(exe.relative_to(self._root_dir)) if exe.is_relative_to(self._root_dir) else str(exe)

    def to_app_config(self) -> AppConfig:
        data = self._data
        version = int(data.get("version", 1))

        paths = data.get("paths", {})
        keywords = self._resolve_path(paths.get("keywords", "resources/keywords.txt"))
        passwords = self._resolve_path(paths.get("passwords", "resources/passwords.txt"))

        tools = data.get("tools", {})
        sz = tools.get("seven_zip", {})
        exe_raw = sz.get("exe")
        exe = self._resolve_path(exe_raw) if exe_raw else None
        auto_download = bool(sz.get("auto_download", True))

        dl = sz.get("download", {})
        download = SevenZipDownloadConfig(
            source=str(dl.get("source", "7-zip.org")),
            prefer=str(dl.get("prefer", "msi-x64")),
            timeout_sec=int(dl.get("timeout_sec", 60)),
        )
        install_dir = self._resolve_path(sz.get("install_dir", "tools/7zip"))

        unrar_raw = (tools.get("unrar", {}) or {}).get("exe")
        bandizip_raw = (tools.get("bandizip", {}) or {}).get("exe")
        unrar_exe = self._resolve_path(unrar_raw) if unrar_raw else None
        bandizip_exe = self._resolve_path(bandizip_raw) if bandizip_raw else None

        beta = data.get("beta", {})
        flatten = beta.get("flatten", {})
        exclude = beta.get("exclude", {})
        guess_suffixes = beta.get("guess_suffixes", [".7z", ".zip", ".rar"])
        log_passwords = bool(beta.get("log_passwords", False))
        extractor_order = beta.get("extractor_order", ["7z", "unrar", "bandizip"])
        preserve_payload_names = bool(beta.get("preserve_payload_names", True))
        duplicates_dir_name = str(beta.get("duplicates_dir_name", "_duplicates") or "_duplicates")
        path_compress = bool(beta.get("path_compress", True))
        rules_raw = beta.get("rules", {}) or {}
        deep = beta.get("deep_extract", {}) or {}
        deep_cfg = BetaDeepExtractConfig(
            enabled=bool(deep.get("enabled", False)),
            max_depth=int(deep.get("max_depth", 4)),
            min_archive_mb=int(deep.get("min_archive_mb", 100)),
            final_single_mb=int(deep.get("final_single_mb", 200)),
        )
        allowed_raw = flatten.get("allowed_roots", [])
        allowed_roots = tuple(str(x) for x in allowed_raw) if isinstance(allowed_raw, list) else ()
        beta_cfg = BetaConfig(
            flatten=BetaFlattenConfig(
                enabled=bool(flatten.get("enabled", True)),
                exclude_dirs=tuple(
                    flatten.get(
                        "exclude_dirs",
                        [
                            "success",
                            "extracted",
                            "intermediate",
                            "final",
                            "failed",
                            "error_files",
                            "deferred_volumes",
                            "archives_success",
                            "resources",
                            "tests",
                            "tools",
                            "__pycache__",
                        ],
                    )
                )
                if isinstance(flatten.get("exclude_dirs", None), list)
                else (
                    "success",
                    "extracted",
                    "intermediate",
                    "final",
                    "failed",
                    "error_files",
                    "deferred_volumes",
                    "archives_success",
                    "resources",
                    "tests",
                    "tools",
                    "__pycache__",
                ),
                allowed_roots=allowed_roots,
                allow_inside_project_repo=bool(flatten.get("allow_inside_project_repo", False)),
            ),
            exclude=BetaExcludeConfig(
                names=tuple(exclude.get("names", ["config.json"])) if isinstance(exclude.get("names", None), list) else ("config.json",),
                exts=tuple(exclude.get("exts", [".bat", ".cmd", ".ps1", ".py", ".json", ".md", ".log"]))
                if isinstance(exclude.get("exts", None), list)
                else (".bat", ".cmd", ".ps1", ".py", ".json", ".md", ".log"),
            ),
            guess_suffixes=tuple(guess_suffixes) if isinstance(guess_suffixes, list) else (".7z", ".zip", ".rar"),
            log_passwords=log_passwords,
            extractor_order=tuple(str(x) for x in extractor_order) if isinstance(extractor_order, list) else ("7z", "unrar", "bandizip"),
            preserve_payload_names=preserve_payload_names,
            duplicates_dir_name=duplicates_dir_name,
            path_compress=path_compress,
            rules=BetaRulesConfig(max_restore_rounds=int(rules_raw.get("max_restore_rounds", 3))),
            deep_extract=deep_cfg,
        )

        return AppConfig(
            version=version,
            config_file=self._config_file,
            root_dir=self._root_dir,
            paths=PathsConfig(keywords=keywords, passwords=passwords),
            tools=ToolsConfig(
                seven_zip=SevenZipConfig(
                    exe=exe,
                    auto_download=auto_download,
                    download=download,
                    install_dir=install_dir,
                ),
                unrar=OptionalExeToolConfig(exe=unrar_exe),
                bandizip=OptionalExeToolConfig(exe=bandizip_exe),
            ),
            beta=beta_cfg,
        )

    def _resolve_path(self, value: Any) -> Path:
        if value is None:
            return self._root_dir
        p = Path(str(value))
        if not p.is_absolute():
            p = self._root_dir / p
        return p
