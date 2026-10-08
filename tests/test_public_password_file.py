"""公开明文密码库（PWD-031）：PasswordFile 文件语义、facade DTO/导入、设置持久化。

全部使用临时目录里合成的词库清单与合成值；不读取真实 resources/passwords.txt、
不导出任何真实词条、不启动工具管线、不建真实任务。仅验证本包新增契约。
"""
from __future__ import annotations

import hashlib
import json
import os
import types
from pathlib import Path

import pytest

from reorder_engine.application.errors import EngineError
from reorder_engine.application.facade import EngineFacade
from reorder_engine.application.models import ProcessingOptions
from reorder_engine.infrastructure import secret_store
from reorder_engine.infrastructure.builtin_defaults import (ALLOWED_PATHS, BuiltinDefaults,
    parse_keywords, parse_passwords)
from reorder_engine.infrastructure.desktop_paths import DesktopPaths
from reorder_engine.infrastructure.secret_store import PasswordFile, parse_password_text

VERSION = "2026.10.08"
MAX_BYTES = PasswordFile.MAX_BYTES


# --- 合成词库与最小运行时隔离（不建真实任务/SQLite/工作线程） -------------------

def stage_catalog(app_root: Path, password_text: str, keyword_text: str) -> BuiltinDefaults:
    """写一份合成 defaults 清单；只含合成词条，不接触真实词库。"""
    folder = app_root / "defaults"
    folder.mkdir(parents=True, exist_ok=True)
    pw = password_text.encode("utf-8")
    kw = keyword_text.encode("utf-8")
    (folder / ALLOWED_PATHS["passwords"]).write_bytes(pw)
    (folder / ALLOWED_PATHS["keywords"]).write_bytes(kw)
    manifest = {"version": VERSION, "schema_version": 1, "files": [
        {"id": "passwords", "path": ALLOWED_PATHS["passwords"],
         "sha256": hashlib.sha256(pw).hexdigest(), "size": len(pw),
         "count": len(parse_passwords(password_text))},
        {"id": "keywords", "path": ALLOWED_PATHS["keywords"],
         "sha256": hashlib.sha256(kw).hexdigest(), "size": len(kw),
         "count": len(parse_keywords(keyword_text))}]}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return BuiltinDefaults(app_root)


class _FakeRepository:
    def __init__(self, *_args, **_kwargs): pass
    def close(self): pass


class _FakeRunner:
    def __init__(self, *_args, **_kwargs): self.busy = False
    def close(self): pass


@pytest.fixture
def isolate_runtime(monkeypatch):
    """Facade 只做设置/密码分发：替换任务仓库与工作线程，不落 SQLite、不启线程。"""
    monkeypatch.setattr("reorder_engine.application.facade.JobRepository", _FakeRepository)
    monkeypatch.setattr("reorder_engine.application.facade.JobRunner", _FakeRunner)


def make_facade(tmp_path: Path, *, passwords: str = "seed-a\nseed-b\nseed-c\n",
                keywords: str = "kw\n", name: str = "app") -> EngineFacade:
    app = tmp_path / name
    builtin = stage_catalog(app, passwords, keywords)
    return EngineFacade(DesktopPaths(app, tmp_path / "data"), defaults=builtin)


def password_file(tmp_path: Path, name: str = "app") -> Path:
    return tmp_path / "data" / "passwords.txt"


# --- 仅 CRLF/LF 是分隔符：Unicode/空白/#/重复全部按字面保留 -------------------

def test_parse_password_text_only_crlf_lf_separators():
    assert parse_password_text("a\r\nb\nc") == ("a", "b", "c")
    assert parse_password_text("") == ()
    # 首尾空白、# 前缀、重复行都按字面保留；只跳过空行。
    assert parse_password_text(" spaced \n#hash\n\ndup\ndup\n") == (" spaced ", "#hash", "dup", "dup")
    # U+2028 不是记录分隔符，与其它 Unicode 一样按字面保留在一个词条里。
    assert parse_password_text("l1\u2028l2") == ("l1\u2028l2",)


# --- 首次播种一次；外部编辑、删除与清空都跨重启保留 ---------------------------

def test_seed_once_then_external_edit_survives_restart(tmp_path, isolate_runtime):
    app = tmp_path / "app"
    builtin = stage_catalog(app, "seed-a\nseed-b\nseed-c\n", "kw\n")
    paths = DesktopPaths(app, tmp_path / "data")
    target = tmp_path / "data" / "passwords.txt"

    facade = EngineFacade(paths, defaults=builtin)
    try:
        assert target.is_file()
        assert target.read_text(encoding="utf-8") == "seed-a\nseed-b\nseed-c\n"
        assert facade.secrets.load() == ("seed-a", "seed-b", "seed-c")
        assert facade.secrets.info()["storage"] == "plaintext"
        assert facade.secrets.info()["path"] == str(target)
        # 外部编辑在下次 load 时生效。
        target.write_text("external only\n", encoding="utf-8")
        assert facade.secrets.load() == ("external only",)
    finally:
        facade.close()

    # 重启不重新播种：既有文件（含外部编辑）是唯一权威。
    again = EngineFacade(paths, defaults=builtin)
    try:
        assert again.secrets.load() == ("external only",)
        assert target.read_text(encoding="utf-8") == "external only\n"
    finally:
        again.close()


def test_deleted_and_empty_library_are_not_reseeded(tmp_path, isolate_runtime):
    app = tmp_path / "app"
    builtin = stage_catalog(app, "keep\ndrop\n", "kw\n")
    paths = DesktopPaths(app, tmp_path / "data")
    target = tmp_path / "data" / "passwords.txt"

    EngineFacade(paths, defaults=builtin).close()  # 首次播种 keep/drop
    target.write_text("drop\n", encoding="utf-8")  # 用户删除了 keep
    facade = EngineFacade(paths, defaults=builtin)
    try:
        assert facade.secrets.load() == ("drop",)  # 被删除的默认项不会在运行时补回
    finally:
        facade.close()

    target.write_text("", encoding="utf-8")  # 主动清空：空库是合法状态
    empty = EngineFacade(paths, defaults=builtin)
    try:
        assert empty.secrets.load() == ()
    finally:
        empty.close()
    again = EngineFacade(paths, defaults=builtin)
    try:
        assert again.secrets.load() == ()  # 空库跨重启保持为空
    finally:
        again.close()


def test_load_accepts_bom_and_crlf_on_disk(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        password_file(tmp_path).write_bytes("\ufefffirst\r\nsecond\r\n".encode("utf-8"))
        assert facade.secrets.load() == ("first", "second")
    finally:
        facade.close()


# --- replace 覆盖整个文件，字面值往返（含 U+2028） -----------------------------

def test_replace_writes_entire_file_with_literal_values(tmp_path, isolate_runtime):
    values = ["  padded  ", "#hash", "dup", "dup", "пароль", "αβγ", "x\u2028y"]
    facade = make_facade(tmp_path)
    try:
        result = facade.secrets.replace(values)
        assert result["count"] == len(values)
        assert result["values"] == values
        target = password_file(tmp_path)
        assert target.read_text(encoding="utf-8") == "\n".join(values) + "\n"
        assert facade.secrets.load() == tuple(values)
    finally:
        facade.close()

    # 重启后仍是同一字面列表（无脱敏、无加密、无去重、无裁剪）。
    again = make_facade(tmp_path)
    try:
        assert again.secrets.load() == ("  padded  ", "#hash", "dup", "dup", "пароль", "αβγ", "x\u2028y")
    finally:
        again.close()


def test_replace_skips_only_empty_lines(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        result = facade.secrets.replace(["", "a", "", " b ", ""])
        assert result["values"] == ["a", " b "]
        assert password_file(tmp_path).read_text(encoding="utf-8") == "a\n b \n"
        facade.secrets.replace([])
        assert password_file(tmp_path).read_text(encoding="utf-8") == ""  # 清空写空文件
        assert facade.secrets.load() == ()
    finally:
        facade.close()


def test_replace_rejects_invalid_values_and_preserves_original(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        facade.secrets.replace(["keep"])
        before = password_file(tmp_path).read_bytes()
        for bad in (["x" * 4097], ["x"] * 10001, ["line\nbreak"], ["with\x00nul"]):
            with pytest.raises(EngineError) as exc:
                facade.secrets.replace(bad)
            assert exc.value.code == "INVALID_PASSWORDS"
        assert password_file(tmp_path).read_bytes() == before
        assert facade.secrets.load() == ("keep",)
    finally:
        facade.close()


def test_replace_oversize_is_rejected_and_preserves_original(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        facade.secrets.replace(["keep"])
        before = password_file(tmp_path).read_bytes()
        big = ["x" * 4096] * ((MAX_BYTES // 4096) + 1)  # > 512 KiB
        with pytest.raises(EngineError) as exc:
            facade.secrets.replace(big)
        assert exc.value.code == "INVALID_PASSWORD_FILE"
        assert password_file(tmp_path).read_bytes() == before
    finally:
        facade.close()


# --- JSON 预算边界：转义膨胀不得撑破 1 MiB 桥接帧，原文件保持完整 ------------

def test_replace_accepts_near_file_limit_plain_text(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        values = ["a" * 4090] * 127  # 约 507 KiB：文件与 JSON 都仍在预算内
        result = facade.secrets.replace(values)
        assert result["count"] == 127
        assert facade.secrets.load() == tuple(values)
    finally:
        facade.close()


def test_replace_rejects_json_escape_expansion_and_preserves_original(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        facade.secrets.replace(["keep"])
        before = password_file(tmp_path).read_bytes()
        # 34 行各 4096 个控制字符：文件仅约 139 KiB，但 JSON 转义后超过 800 KiB。
        expanded = ["\x01" * 4096] * 34
        assert len("\n".join(expanded).encode("utf-8")) < MAX_BYTES
        with pytest.raises(EngineError) as exc:
            facade.secrets.replace(expanded)
        assert exc.value.code == "INVALID_PASSWORDS"
        assert password_file(tmp_path).read_bytes() == before  # 非法保存不改原文件
        assert facade.secrets.load() == ("keep",)
    finally:
        facade.close()


# --- 读取失败边界（编码 / 大小）不改变文件 ------------------------------------

def test_load_rejects_invalid_encoding(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        password_file(tmp_path).write_bytes(b"\xff\xfe\x00\x01")
        with pytest.raises(EngineError) as exc:
            facade.secrets.load()
        assert exc.value.code == "PASSWORD_FILE_FAILED"
    finally:
        facade.close()


def test_load_rejects_oversize_file(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        password_file(tmp_path).write_bytes(b"a" * (MAX_BYTES + 1))
        with pytest.raises(EngineError) as exc:
            facade.secrets.load()
        assert exc.value.code == "INVALID_PASSWORD_FILE"
    finally:
        facade.close()


def test_load_rejects_json_escape_expansion_over_budget(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        target = password_file(tmp_path)
        target.write_bytes(("\n".join(["\x01" * 4096] * 34) + "\n").encode("utf-8"))
        assert target.stat().st_size < MAX_BYTES  # 文件合法大小，但 JSON 转义超出预算
        with pytest.raises(EngineError) as exc:
            facade.secrets.load()
        assert exc.value.code == "INVALID_PASSWORDS"
    finally:
        facade.close()


# --- 写失败 / 符号链接 / 非普通文件边界（POSIX 足够） -------------------------

def test_write_failure_preserves_original_and_leaves_no_temp(tmp_path, isolate_runtime, monkeypatch):
    facade = make_facade(tmp_path)
    try:
        facade.secrets.replace(["original"])
        target = password_file(tmp_path)
        before = target.read_bytes()
        real_os = secret_store.os
        monkeypatch.setattr(secret_store, "os", types.SimpleNamespace(
            fdopen=real_os.fdopen, fsync=real_os.fsync,
            replace=lambda *a, **k: (_ for _ in ()).throw(OSError("模拟写盘失败"))))
        with pytest.raises(EngineError) as exc:
            facade.secrets.replace(["replacement"])
        assert exc.value.code == "PASSWORD_FILE_FAILED"
        assert target.read_bytes() == before  # 原文件未被替换
        assert facade.secrets.load() == ("original",)
        leftovers = [p.name for p in target.parent.iterdir() if p.name.startswith(".passwords-")]
        assert leftovers == []  # 临时文件已清理
    finally:
        facade.close()


def test_existing_symlink_is_rejected(tmp_path, isolate_runtime):
    app = tmp_path / "app"
    builtin = stage_catalog(app, "d1\n", "kw\n")
    data = tmp_path / "data"
    data.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        os.symlink(outside, data / "passwords.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(EngineError) as exc:
        EngineFacade(DesktopPaths(app, data), defaults=builtin)
    assert exc.value.code == "INVALID_PASSWORD_FILE"


def test_directory_in_place_of_password_file_is_rejected(tmp_path, isolate_runtime):
    app = tmp_path / "app"
    builtin = stage_catalog(app, "d1\n", "kw\n")
    data = tmp_path / "data"
    (data / "passwords.txt").mkdir(parents=True)
    with pytest.raises(EngineError) as exc:
        EngineFacade(DesktopPaths(app, data), defaults=builtin)
    assert exc.value.code == "INVALID_PASSWORD_FILE"


def test_later_symlink_swap_is_rejected_on_load_and_replace(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        facade.secrets.replace(["a"])
        target = password_file(tmp_path)
        outside = tmp_path / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        target.unlink()
        try:
            os.symlink(outside, target)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable")
        with pytest.raises(EngineError) as exc:
            facade.secrets.load()
        assert exc.value.code == "INVALID_PASSWORD_FILE"
        with pytest.raises(EngineError) as exc:
            facade.secrets.replace(["b"])
        assert exc.value.code == "INVALID_PASSWORD_FILE"
        assert outside.read_text(encoding="utf-8") == "outside\n"  # 链外目标未被改写
    finally:
        facade.close()


# --- facade 导入：追加；DTO 明文；日志不脱敏 ----------------------------------

def test_import_appends_and_keeps_duplicates(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        facade.dispatch("passwords.replace", {"passwords": ["base"]})
        source = tmp_path / "import.txt"
        source.write_bytes("\ufeffone\r\ntwo\n#tag\n".encode("utf-8"))
        result = facade.dispatch("passwords.import", {"path": str(source)})
        assert result["values"] == ["base", "one", "two", "#tag"]
        # 再次导入按字面追加重复项。
        result = facade.dispatch("passwords.import", {"path": str(source)})
        assert result["values"] == ["base", "one", "two", "#tag", "one", "two", "#tag"]
        assert facade.secrets.load() == tuple(result["values"])
    finally:
        facade.close()


def test_failed_import_leaves_library_unchanged(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        facade.dispatch("passwords.replace", {"passwords": ["keep"]})
        bad = tmp_path / "bad.bin"
        bad.write_bytes(b"\xff\xfe\x00")
        with pytest.raises(EngineError) as exc:
            facade.dispatch("passwords.import", {"path": str(bad)})
        assert exc.value.code == "INVALID_PASSWORD_FILE"
        assert facade.secrets.load() == ("keep",)
    finally:
        facade.close()


def test_import_rejects_relative_missing_and_oversize_paths(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        facade.dispatch("passwords.replace", {"passwords": ["keep"]})
        folder = tmp_path / "folder"
        folder.mkdir()
        oversize = tmp_path / "big.txt"
        oversize.write_bytes(b"a" * (MAX_BYTES + 1))
        for path in ("relative.txt", str(tmp_path / "missing.txt"), str(folder), str(oversize)):
            with pytest.raises(EngineError) as exc:
                facade.dispatch("passwords.import", {"path": path})
            assert exc.value.code == "INVALID_PASSWORD_FILE"
        assert facade.secrets.load() == ("keep",)
    finally:
        facade.close()


def test_dto_exposes_plain_values_and_redact_is_identity(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        facade.dispatch("passwords.replace", {"passwords": ["alpha", "#beta"]})
        expected = {"count": 2, "storage": "plaintext",
                    "path": str(password_file(tmp_path)), "values": ["alpha", "#beta"]}
        for method in ("settings.get", "system.info"):
            info = facade.dispatch(method, {})
            assert info["passwords"] == expected
            assert "work_root" in info["settings"]
        # 公开明文库不再对日志/消息做任何脱敏。
        assert facade.secrets.redact("password=alpha") == "password=alpha"
        assert facade.secrets.redact("#beta") == "#beta"
    finally:
        facade.close()


# --- 设置持久化：顶层 work_root 可空、options/tools 跨重启保留 ----------------

def test_settings_work_root_nullable_and_options_tools_survive_restart(tmp_path, isolate_runtime):
    app = tmp_path / "app"
    builtin = stage_catalog(app, "d1\n", "kw\n")
    paths = DesktopPaths(app, tmp_path / "data")

    facade = EngineFacade(paths, defaults=builtin)
    try:
        assert facade.settings.get().work_root is None  # 默认可空
        options = {**ProcessingOptions().model_dump(), "keep_workspace": True,
                   "use_builtin_passwords": False}
        updated = facade.dispatch("settings.update", {
            "work_root": str(tmp_path / "custom-out"),
            "options": options,
            "tools": {"seven_zip": str(tmp_path / "7z.exe"), "unrar": None, "bandizip": None}})
        assert updated["settings"]["work_root"] == str(tmp_path / "custom-out")
        assert updated["settings"]["options"]["keep_workspace"] is True
        assert updated["settings"]["options"]["use_builtin_passwords"] is False
    finally:
        facade.close()

    again = EngineFacade(paths, defaults=builtin)
    try:
        stored = again.settings.get()
        assert stored.work_root == str(tmp_path / "custom-out")
        assert stored.options.keep_workspace is True
        assert stored.options.use_builtin_passwords is False  # 旧字段保留兼容
        assert stored.tools.seven_zip == str(tmp_path / "7z.exe")
        again.dispatch("settings.update", {**stored.model_dump(), "work_root": None})
        assert again.settings.get().work_root is None  # 可空值可写回持久化
    finally:
        again.close()

    third = EngineFacade(paths, defaults=builtin)
    try:
        final = third.settings.get()
        assert final.work_root is None
        assert final.options.keep_workspace is True
        assert final.options.use_builtin_passwords is False
        assert final.tools.seven_zip == str(tmp_path / "7z.exe")
    finally:
        third.close()


# --- 坏文件不阻断 settings.get/system.info；显式 replace 才能修复 --------------

@pytest.mark.parametrize("payload,code", [
    (b"\xff\xfe\x00\x01", "PASSWORD_FILE_FAILED"),            # 非法 UTF-8
    (b"a" * (MAX_BYTES + 1), "INVALID_PASSWORD_FILE"),         # 超过 512 KiB
    (("\n".join(["\x01" * 4096] * 34) + "\n").encode("utf-8"), "INVALID_PASSWORDS"),  # 转义膨胀超预算
])
def test_broken_file_keeps_settings_available_and_replace_repairs(
        tmp_path, isolate_runtime, payload, code):
    facade = make_facade(tmp_path)
    try:
        target = password_file(tmp_path)
        target.write_bytes(payload)
        before = target.read_bytes()
        for method in ("settings.get", "system.info"):
            info = facade.dispatch(method, {})
            passwords = info["passwords"]
            assert passwords["error_code"] == code
            assert passwords["count"] == 0 and passwords["values"] == []
            assert passwords["storage"] == "plaintext" and passwords["path"] == str(target)
            assert passwords["error"]  # 有可读错误文案
            assert info["settings"]["work_root"] is None  # 设置主体仍可用
        assert target.read_bytes() == before  # 读取坏文件不修改原文件
        repaired = facade.dispatch("passwords.replace", {"passwords": ["fixed"]})
        assert repaired["values"] == ["fixed"] and "error_code" not in repaired
        assert target.read_text(encoding="utf-8") == "fixed\n"
        assert facade.dispatch("system.info", {})["passwords"]["values"] == ["fixed"]
    finally:
        facade.close()


def test_runtime_deleted_file_is_recovered_by_replace(tmp_path, isolate_runtime):
    facade = make_facade(tmp_path)
    try:
        facade.dispatch("passwords.replace", {"passwords": ["first"]})
        target = password_file(tmp_path)
        target.unlink()
        info = facade.dispatch("settings.get", {})["passwords"]
        assert info["error_code"] == "PASSWORD_FILE_FAILED" and info["values"] == []
        assert info["path"] == str(target)
        recovered = facade.dispatch("passwords.replace", {"passwords": ["second"]})
        assert recovered["values"] == ["second"] and "error_code" not in recovered
        assert target.read_text(encoding="utf-8") == "second\n"
        assert facade.dispatch("system.info", {})["passwords"]["values"] == ["second"]
    finally:
        facade.close()


def test_invalid_initial_does_not_create_password_file(tmp_path):
    target = tmp_path / "data" / "passwords.txt"
    with pytest.raises(EngineError) as exc:
        PasswordFile(target, initial=("x" * 4097,))  # 单条 > 4096
    assert exc.value.code == "INVALID_PASSWORDS"
    assert not target.exists()
    with pytest.raises(EngineError) as exc:
        PasswordFile(target, initial=tuple("y" * 4096 for _ in range(130)))  # 拼接 > 512 KiB
    assert exc.value.code == "INVALID_PASSWORD_FILE"
    assert not target.exists()
    with pytest.raises(EngineError) as exc:
        PasswordFile(target, initial=("bad\ud800",))  # 非法代理字符无法编码
    assert exc.value.code == "INVALID_PASSWORDS"
    assert not target.exists()
