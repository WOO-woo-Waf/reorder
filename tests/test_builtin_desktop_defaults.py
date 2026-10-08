"""内置 Git 词库接入：catalog 校验、开关独立、脱敏与顶层改名。

全部使用临时合成的 fake 词条，不读取真实词库、不启动内容管线、不建 SQLite、
不跑 GUI。仅覆盖本包新增契约。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import pytest

from reorder_engine.application.errors import EngineError
from reorder_engine.application.facade import EngineFacade
from reorder_engine.application.models import ProcessingOptions
from reorder_engine.application.processing import PackageProcessor, clean_output_entry_names
from reorder_engine.infrastructure.builtin_defaults import (ALLOWED_PATHS, BuiltinCatalog,
    BuiltinDefaults, BuiltinEntry, parse_keywords, parse_passwords)
from reorder_engine.infrastructure.desktop_paths import DesktopPaths
from reorder_engine.infrastructure.secret_store import SecretStore

VERSION = "2026.10.08"


class SessionSecrets(SecretStore):
    """Session-only store; never touch the OS credential backend during tests."""

    def __init__(self):
        self._lock = threading.RLock()
        self._passwords = ()
        self.mode = "session"
        self._backend = None


def _entry(entry_id: str, data: bytes, count: int) -> dict:
    return {"id": entry_id, "path": ALLOWED_PATHS[entry_id],
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "count": count}


def make_defaults(app_root: Path, password_text: str, keyword_text: str, *,
                  encoding: str = "utf-8", version: str = VERSION) -> Path:
    """Stage a synthetic defaults catalog; no real content involved."""
    folder = app_root / "defaults"
    folder.mkdir(parents=True, exist_ok=True)
    password_bytes = password_text.encode(encoding)
    keyword_bytes = keyword_text.encode(encoding)
    (folder / ALLOWED_PATHS["passwords"]).write_bytes(password_bytes)
    (folder / ALLOWED_PATHS["keywords"]).write_bytes(keyword_bytes)
    manifest = {"version": version, "schema_version": 1, "files": [
        _entry("passwords", password_bytes, len(parse_passwords(password_text))),
        _entry("keywords", keyword_bytes, len(parse_keywords(keyword_text)))]}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def write_entry(root: Path, name: str, *, is_dir: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    if is_dir:
        target.mkdir()
    else:
        target.write_text("payload", encoding="utf-8")
    return target


def make_processor(builtin: BuiltinDefaults, secrets: SecretStore) -> PackageProcessor:
    # paths/settings/repository stay unused by the two focused methods under test.
    return PackageProcessor(None, None, None, secrets, builtin=builtin)


# --- catalog: missing / valid / invalid / tampered ---------------------------

def test_missing_manifest_yields_empty_defaults(tmp_path):
    builtin = BuiltinDefaults(tmp_path / "app")
    assert builtin.passwords == () and builtin.keywords == ()
    assert builtin.password_count == 0 and builtin.keyword_count == 0
    assert builtin.version is None


def test_valid_catalog_parses_comments_bom_strip_and_ordering(tmp_path):
    app = tmp_path / "app"
    passwords = "# users\n\nalpha\nbeta\n  gamma  \nalpha\n# tail\ndelta\n"
    keywords = "\n# kw\nlong-keyword\nmid-word\nab\nz\n"
    make_defaults(app, passwords, keywords, encoding="utf-8-sig")
    builtin = BuiltinDefaults(app)
    assert builtin.version == VERSION
    assert builtin.passwords == ("alpha", "beta", "gamma", "delta")  # strip + ordered dedupe
    assert builtin.keywords == ("long-keyword", "mid-word", "ab", "z")  # longest first
    assert builtin.password_count == 4 and builtin.keyword_count == 4


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(version="1999.01.01"),
    lambda m: m.update(schema_version=2),
    lambda m: m.update(files=m["files"][:1]),
    lambda m: m["files"][0].update(id="other"),
    lambda m: m["files"][0].update(id=["passwords"]),
    lambda m: m["files"][0].update(path="../builtin-passwords.txt"),
    lambda m: m["files"][0].update(sha256="not-a-digest"),
    lambda m: m["files"][0].update(size=-1),
    lambda m: m["files"][0].update(count="115"),
    lambda m: m.pop("files"),
])
def test_invalid_catalog_raises(tmp_path, mutate):
    app = tmp_path / "app"
    folder = make_defaults(app, "p1\n", "k1\n")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    mutate(manifest)
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(EngineError):
        BuiltinDefaults(app).load()


def test_non_object_manifest_raises(tmp_path):
    app = tmp_path / "app"
    folder = make_defaults(app, "p1\n", "k1\n")
    (folder / "manifest.json").write_text("[]", encoding="utf-8")
    with pytest.raises(EngineError):
        BuiltinDefaults(app).load()


def test_missing_data_file_raises(tmp_path):
    app = tmp_path / "app"
    folder = make_defaults(app, "p1\n", "k1\n")
    (folder / ALLOWED_PATHS["keywords"]).unlink()
    with pytest.raises(EngineError):
        BuiltinDefaults(app).load()


def test_tampered_content_raises(tmp_path):
    app = tmp_path / "app"
    folder = make_defaults(app, "p1\np2\n", "k1\n")
    (folder / ALLOWED_PATHS["passwords"]).write_bytes(b"tampered\n")
    with pytest.raises(EngineError):
        BuiltinDefaults(app).load()


def test_size_mismatch_raises(tmp_path):
    app = tmp_path / "app"
    folder = make_defaults(app, "p1\n", "k1\n")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][0]["size"] = 999999
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(EngineError):
        BuiltinDefaults(app).load()


@pytest.mark.parametrize("index,password_text,keyword_text,bad_count", [
    (0, "p1\np2\n", "k1\n", 3),   # passwords dedupe leaves 2
    (1, "p1\n", "k1\nk2\n", 5),   # keywords parse leaves 2
])
def test_count_mismatch_raises(tmp_path, index, password_text, keyword_text, bad_count):
    app = tmp_path / "app"
    folder = make_defaults(app, password_text, keyword_text)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][index]["count"] = bad_count
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(EngineError):
        BuiltinDefaults(app).load()


def test_count_matching_parse_is_accepted(tmp_path):
    app = tmp_path / "app"
    make_defaults(app, "p1\np2\np1\n", "kw\n")  # dedupe leaves 2 passwords
    builtin = BuiltinDefaults(app)
    assert builtin.passwords == ("p1", "p2") and builtin.keyword_count == 1


def test_symlinked_data_file_rejected(tmp_path):
    app = tmp_path / "app"
    folder = make_defaults(app, "p1\n", "k1\n")
    target = folder / ALLOWED_PATHS["keywords"]
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside\n")
    target.unlink()
    try:
        os.symlink(outside, target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(EngineError):
        BuiltinDefaults(app).load()


def test_symlinked_defaults_root_rejected(tmp_path):
    real = tmp_path / "real"
    make_defaults(real, "p1\n", "k1\n")  # creates real/defaults/{manifest,data}
    app = tmp_path / "app"
    app.mkdir()
    try:
        os.symlink(real / "defaults", app / "defaults")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(EngineError):
        BuiltinDefaults(app).load()


def test_injected_catalog_needs_no_manifest(tmp_path):
    app = tmp_path / "app"
    folder = app / "defaults"
    folder.mkdir(parents=True)
    password_bytes = "p1\np2\n".encode()
    keyword_bytes = "kw-long\nkw\n".encode()
    (folder / ALLOWED_PATHS["passwords"]).write_bytes(password_bytes)
    (folder / ALLOWED_PATHS["keywords"]).write_bytes(keyword_bytes)
    assert not (folder / "manifest.json").exists()
    catalog = BuiltinCatalog(version=VERSION, schema_version=1, files=(
        BuiltinEntry("passwords", ALLOWED_PATHS["passwords"],
                     hashlib.sha256(password_bytes).hexdigest(), len(password_bytes), 2),
        BuiltinEntry("keywords", ALLOWED_PATHS["keywords"],
                     hashlib.sha256(keyword_bytes).hexdigest(), len(keyword_bytes), 2)))
    builtin = BuiltinDefaults(app, catalog=catalog)
    assert builtin.passwords == ("p1", "p2")
    assert builtin.keywords == ("kw-long", "kw")
    assert builtin.version == VERSION


# --- switches and private/builtin independence --------------------------------

def test_replace_private_keeps_builtin_and_switch_independent(tmp_path):
    app = tmp_path / "app"
    make_defaults(app, "builtin-a\nbuiltin-b\n", "kw\n")
    builtin = BuiltinDefaults(app)
    secrets = SessionSecrets()
    processor = make_processor(builtin, secrets)

    secrets.replace([])
    assert secrets.load() == ()
    assert processor.runtime_passwords(ProcessingOptions()) == ("builtin-a", "builtin-b")
    assert processor.runtime_passwords(ProcessingOptions(use_builtin_passwords=False)) == ()

    secrets.replace(["builtin-b", "user-only"])
    assert secrets.load() == ("builtin-b", "user-only")
    assert processor.runtime_passwords(ProcessingOptions()) == ("builtin-b", "user-only", "builtin-a")
    assert processor.runtime_passwords(ProcessingOptions(use_builtin_passwords=False)) == ("builtin-b", "user-only")


def test_redact_covers_private_and_builtin_even_when_disabled(tmp_path):
    app = tmp_path / "app"
    make_defaults(app, "builtin-secret\n", "kw-secret\n")
    builtin = BuiltinDefaults(app)
    secrets = SessionSecrets()
    secrets.set_extra_secrets(builtin.passwords)
    secrets.replace(["user-secret"])

    text = secrets.redact("a builtin-secret b user-secret c kw-secret")
    assert "builtin-secret" not in text and "user-secret" not in text
    assert "kw-secret" in text  # keywords are not secrets and stay visible
    assert secrets.redact("x builtin-secret y") == "x [密码已隐藏] y"


# --- top-level output name cleaning ------------------------------------------

def test_clean_keeps_extension_and_skips_internal_levels(tmp_path):
    folder = tmp_path / "final"
    write_entry(folder, "Sample Keyword Clip.7z")
    inner = write_entry(folder, "Keyword Folder", is_dir=True)
    write_entry(inner, "Keyword inside.txt")

    renamed = clean_output_entry_names(folder, ("Keyword",))
    assert renamed == [("Keyword Folder", "Folder"), ("Sample Keyword Clip.7z", "Sample Clip.7z")]
    assert (folder / "Sample Clip.7z").exists()
    assert (folder / "Folder").is_dir()
    assert (folder / "Folder" / "Keyword inside.txt").exists()  # internal level untouched


def test_clean_dedupes_case_insensitive_conflicts(tmp_path):
    folder = tmp_path / "final"
    write_entry(folder, "One.txt")
    write_entry(folder, "kw one.txt")

    clean_output_entry_names(folder, ("kw",))
    assert (folder / "One.txt").exists()  # existing entry keeps its name
    assert (folder / "one (1).txt").exists()  # case collision deduped, nothing overwritten
    assert sorted(p.name for p in folder.iterdir()) == ["One.txt", "one (1).txt"]


@pytest.mark.parametrize("name,keywords", [
    ("kw.txt", ("kw",)),           # empty stem after cleaning
    ("kw CON.txt", ("kw",)),       # Windows reserved name
    ("kw bad:name.txt", ("kw",)),  # invalid character
])
def test_clean_keeps_unsafe_names_with_notice(tmp_path, name, keywords):
    folder = tmp_path / "final"
    write_entry(folder, name)
    logs: list[str] = []
    renamed = clean_output_entry_names(folder, keywords, log=logs.append)
    assert renamed == []
    assert (folder / name).exists()
    assert logs  # original kept and a notice emitted


def test_clean_notice_is_redacted(tmp_path):
    folder = tmp_path / "final"
    write_entry(folder, "secretvalue.txt")
    secrets = SessionSecrets()
    secrets.set_extra_secrets(("secretvalue",))
    logs: list[str] = []
    clean_output_entry_names(folder, ("secretvalue",), redact=secrets.redact, log=logs.append)
    assert (folder / "secretvalue.txt").exists()
    assert logs and all("secretvalue" not in line for line in logs)


def test_clean_leaves_names_without_keywords_untouched(tmp_path):
    folder = tmp_path / "final"
    write_entry(folder, "Double  Space.txt")  # no keyword match, even with odd spacing
    renamed = clean_output_entry_names(folder, ("kw",))
    assert renamed == []
    assert (folder / "Double  Space.txt").exists()


def test_apply_builtin_keyword_cleaning_respects_switch_and_state(tmp_path):
    app = tmp_path / "app"
    make_defaults(app, "p1\n", "kw\n")
    builtin = BuiltinDefaults(app)
    processor = make_processor(builtin, SessionSecrets())
    workspace = tmp_path / "ws"
    write_entry(workspace / "final", "kw item.txt")
    write_entry(workspace / "error_files", "kw error.txt")

    processor.apply_builtin_keyword_cleaning(workspace, ProcessingOptions(), "succeeded", log=lambda _: None)
    assert (workspace / "final" / "kw item.txt").exists()  # default off: untouched

    processor.apply_builtin_keyword_cleaning(
        workspace, ProcessingOptions(clean_builtin_keywords=True), "failed", log=lambda _: None)
    assert (workspace / "final" / "kw item.txt").exists()  # not success/partial: untouched

    processor.apply_builtin_keyword_cleaning(
        workspace, ProcessingOptions(clean_builtin_keywords=True), "succeeded", log=lambda _: None)
    assert (workspace / "final" / "item.txt").exists()
    assert not (workspace / "final" / "kw item.txt").exists()
    assert (workspace / "error_files" / "kw error.txt").exists()  # error_files never renamed


# --- facade settings_info defaults field -------------------------------------

class _FakeRepository:
    def __init__(self, *_args, **_kwargs): pass
    def close(self): pass


class _FakeRunner:
    def __init__(self, *_args, **_kwargs): self.busy = False
    def close(self): pass


@pytest.fixture
def isolated_facade(monkeypatch):
    monkeypatch.setattr("reorder_engine.application.facade.JobRepository", _FakeRepository)
    monkeypatch.setattr("reorder_engine.application.facade.JobRunner", _FakeRunner)


def test_facade_settings_info_defaults_and_shared_object(tmp_path, isolated_facade):
    app = tmp_path / "app"
    make_defaults(app, "b1\nb2\n", "k1\nk2\nk3\n")
    secrets = SessionSecrets()
    builtin = BuiltinDefaults(app)
    facade = EngineFacade(DesktopPaths(app, tmp_path / "data"), secrets=secrets, defaults=builtin)
    try:
        info = facade.settings_info()
        assert info["defaults"] == {"password_count": 2, "keyword_count": 3,
            "passwords_enabled": True, "keyword_cleaning_enabled": False, "version": VERSION}
        assert info["passwords"]["count"] == 0  # private library count stays independent
        assert facade.defaults is builtin and facade.processor.builtin is builtin
        assert secrets.redact("x b1 y") == "x [密码已隐藏] y"  # builtin values are redacted
        secrets.replace(["u1"])
        info = facade.settings_info()
        assert info["passwords"]["count"] == 1 and info["defaults"]["password_count"] == 2
    finally:
        facade.close()


def test_facade_settings_info_missing_manifest_and_tamper(tmp_path, isolated_facade):
    app = tmp_path / "app"
    app.mkdir()
    facade = EngineFacade(DesktopPaths(app, tmp_path / "data"), secrets=SessionSecrets())
    try:
        info = facade.settings_info()
        assert info["defaults"]["version"] == "" and info["defaults"]["password_count"] == 0
        assert info["defaults"]["keyword_count"] == 0
    finally:
        facade.close()

    folder = make_defaults(app, "p1\n", "k1\n")
    (folder / ALLOWED_PATHS["passwords"]).write_bytes(b"changed\n")
    facade = EngineFacade(DesktopPaths(app, tmp_path / "data2"), secrets=SessionSecrets())
    try:
        with pytest.raises(EngineError):
            facade.settings_info()
    finally:
        facade.close()
