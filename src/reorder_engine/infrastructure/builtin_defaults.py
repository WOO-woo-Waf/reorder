from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from reorder_engine.application.errors import EngineError

# 内置词库是安装目录内的只读资源：不做网络访问，不查 PATH，不读用户目录。
DEFAULT_VERSION = "2026.10.08"
DEFAULT_SCHEMA_VERSION = 1
DEFAULT_DIR_NAME = "defaults"
MANIFEST_NAME = "manifest.json"
INVALID_CODE = "BUILTIN_DEFAULTS_INVALID"
# 只接受 manifest 声明的这两个固定相对文件名。
ALLOWED_PATHS = {"passwords": "builtin-passwords.txt", "keywords": "builtin-keywords.txt"}


@dataclass(frozen=True)
class BuiltinEntry:
    id: str
    path: str
    sha256: str
    size: int
    count: int


@dataclass(frozen=True)
class BuiltinCatalog:
    version: str
    schema_version: int
    files: tuple[BuiltinEntry, ...]


def parse_passwords(text: str) -> tuple[str, ...]:
    """忽略空行与 ``#`` 注释、去空白，按出现顺序去重。"""
    seen: set[str] = set()
    values: list[str] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw in seen:
            continue
        seen.add(raw)
        values.append(raw)
    return tuple(values)


def parse_keywords(text: str) -> tuple[str, ...]:
    """忽略空行与 ``#`` 注释、去空白，长词优先（减少局部覆盖）。"""
    values: list[str] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        values.append(raw)
    values.sort(key=len, reverse=True)
    return tuple(values)


def _is_hex_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_link(path: Path) -> bool:
    """True for a symlink, or a Windows junction when ``Path.is_junction`` exists."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is None:
        return False
    try:
        return bool(is_junction())
    except OSError:
        return True


def validate_manifest(raw: object) -> BuiltinCatalog:
    """Strictly validate the manifest shape; a bad catalog raises EngineError."""
    if not isinstance(raw, dict):
        raise EngineError(INVALID_CODE, "内置词库清单结构非法。")
    version, schema_version, files = raw.get("version"), raw.get("schema_version"), raw.get("files")
    if not isinstance(version, str) or version != DEFAULT_VERSION:
        raise EngineError(INVALID_CODE, "内置词库版本不受支持。")
    if isinstance(schema_version, bool) or schema_version != DEFAULT_SCHEMA_VERSION:
        raise EngineError(INVALID_CODE, "内置词库清单版本不受支持。")
    if not isinstance(files, list) or len(files) != len(ALLOWED_PATHS):
        raise EngineError(INVALID_CODE, "内置词库文件清单非法。")
    entries: list[BuiltinEntry] = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise EngineError(INVALID_CODE, "内置词库条目非法。")
        entry_id = item.get("id")
        # The id may be any JSON value; a non-string (for example a list) must fail
        # as EngineError, never as a bare TypeError from an unhashable lookup.
        if not isinstance(entry_id, str) or entry_id not in ALLOWED_PATHS or entry_id in seen:
            raise EngineError(INVALID_CODE, "内置词库条目标识非法。")
        seen.add(entry_id)
        if item.get("path") != ALLOWED_PATHS[entry_id]:
            raise EngineError(INVALID_CODE, "内置词库条目路径非法。")
        digest, size, count = item.get("sha256"), item.get("size"), item.get("count")
        if not _is_hex_digest(digest):
            raise EngineError(INVALID_CODE, "内置词库校验值非法。")
        if not _is_count(size):
            raise EngineError(INVALID_CODE, "内置词库文件大小非法。")
        if not _is_count(count):
            raise EngineError(INVALID_CODE, "内置词库条目数量非法。")
        entries.append(BuiltinEntry(id=entry_id, path=ALLOWED_PATHS[entry_id],
                                    sha256=digest.lower(), size=size, count=count))
    if seen != set(ALLOWED_PATHS):
        raise EngineError(INVALID_CODE, "内置词库缺少必要文件。")
    return BuiltinCatalog(version=version, schema_version=schema_version, files=tuple(entries))


class BuiltinDefaults:
    """读取安装目录 ``defaults/manifest.json`` 声明的两个内置词库文件。

    - 没有 manifest（开发环境或既有隔离测试）时返回空对象，不报错。
    - manifest 存在但结构非法、被篡改或缺少数据文件时抛出明确 EngineError。
    - 构造函数可注入 fake catalog 便于测试；Facade/Processor 共享同一实例。
    - 只加载 ``defaults`` 下的普通相对文件；校验边界、符号链接/junction、sha256、大小。
    - 解析结果长度必须等于 manifest ``count``（密码按去重后，关键词按解析后）。
    """

    def __init__(self, app_root: Path | str, *, catalog: BuiltinCatalog | None = None):
        self._app_root = Path(app_root)
        self._injected = catalog
        self._loaded = False
        self._version: str | None = None
        self._passwords: tuple[str, ...] = ()
        self._keywords: tuple[str, ...] = ()

    @property
    def defaults_dir(self) -> Path:
        return self._app_root / DEFAULT_DIR_NAME

    @property
    def manifest_path(self) -> Path:
        return self.defaults_dir / MANIFEST_NAME

    @property
    def version(self) -> str | None:
        self.load()
        return self._version

    @property
    def passwords(self) -> tuple[str, ...]:
        self.load()
        return self._passwords

    @property
    def keywords(self) -> tuple[str, ...]:
        self.load()
        return self._keywords

    @property
    def password_count(self) -> int:
        return len(self.passwords)

    @property
    def keyword_count(self) -> int:
        return len(self.keywords)

    def load(self) -> "BuiltinDefaults":
        """Validate and parse once; a failed load stays failed on the next call."""
        if self._loaded:
            return self
        catalog = self._injected if self._injected is not None else self._read_manifest()
        if catalog is not None:
            self._passwords, self._keywords, self._version = self._extract(catalog)
        self._loaded = True
        return self

    def _read_manifest(self) -> BuiltinCatalog | None:
        # defaults 目录及其到安装根的祖先、以及 manifest 目标都不能是链接，
        # 防止通过符号链接/junction 读到安装目录之外的内容。
        self._assert_no_links(self.manifest_path)
        manifest = self.manifest_path
        if not manifest.exists():
            return None
        if not manifest.is_file():
            raise EngineError(INVALID_CODE, "内置词库清单缺失或不是普通文件。")
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise EngineError(INVALID_CODE, "内置词库清单无法解析。") from exc
        return validate_manifest(raw)

    def _assert_no_links(self, start: Path) -> None:
        current = start
        while True:
            if _is_link(current):
                raise EngineError(INVALID_CODE, "内置词库路径不能是符号链接或其他链接。")
            if current == self._app_root or current.parent == current:
                break
            current = current.parent

    def _extract(self, catalog: BuiltinCatalog) -> tuple[tuple[str, ...], tuple[str, ...], str]:
        passwords: tuple[str, ...] = ()
        keywords: tuple[str, ...] = ()
        for entry in catalog.files:
            text = self._read_entry_text(entry)
            values = parse_passwords(text) if entry.id == "passwords" else parse_keywords(text)
            if len(values) != entry.count:
                raise EngineError(INVALID_CODE, "内置词库条目数量与清单不一致。")
            if entry.id == "passwords":
                passwords = values
            else:
                keywords = values
        return passwords, keywords, catalog.version

    def _read_entry_text(self, entry: BuiltinEntry) -> str:
        base = self.defaults_dir
        target = base / entry.path
        if target.is_symlink():
            raise EngineError(INVALID_CODE, "内置词库文件不能是符号链接。")
        try:
            target.resolve().relative_to(base.resolve())
        except (OSError, ValueError) as exc:
            raise EngineError(INVALID_CODE, "内置词库文件越出安装目录。") from exc
        if not target.is_file():
            raise EngineError(INVALID_CODE, "内置词库文件缺失。")
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise EngineError(INVALID_CODE, "内置词库文件无法读取。") from exc
        if len(data) != entry.size:
            raise EngineError(INVALID_CODE, "内置词库文件大小与清单不一致。")
        if hashlib.sha256(data).hexdigest() != entry.sha256:
            raise EngineError(INVALID_CODE, "内置词库文件校验失败，可能已被篡改。")
        try:
            return data.decode("utf-8-sig")
        except UnicodeError as exc:
            raise EngineError(INVALID_CODE, "内置词库文件编码非法。") from exc
