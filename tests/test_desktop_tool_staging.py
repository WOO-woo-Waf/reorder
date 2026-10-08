"""Contract tests for scripts/stage_desktop_tools.py (synthetic text packages only).

UnRAR is the only mandatory locked tool; ``bandizip`` is allowed but optional and
only ever exercised here as a synthetic future entry. The real repo lock and its
vendored UnRAR snapshot are checked read-only at the end of this file.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import stage_desktop_engine  # noqa: E402  (sibling build script)
import stage_desktop_tools as sdt  # noqa: E402


# Official provenance pages (not necessarily direct download links).
UNRAR_URL = "https://www.rarlab.com/rar_add.htm"
BANDIZIP_URL = "https://www.bandisoft.com/bandizip/"
UNRAR_MEMBERS = {
    "UnRAR.exe": b"MZ-unrar-fake",
    "UnRAR-License.txt": b"unrar licence text",
    "WinRAR-License.txt": b"winrar licence text",
}
UNRAR_LICENSES = ["UnRAR-License.txt", "WinRAR-License.txt"]
BANDIZIP_MEMBERS = {"Bandizip/Bandizip.exe": b"MZ-bandizip-fake", "Bandizip/License.txt": b"bandizip licence"}
BANDIZIP_LICENSES = ["Bandizip/License.txt"]
VENDOR_NAME = "unrar-7.13-windows-x64.zip"
VENDOR_RELATIVE = f"tools/desktop-vendor/{VENDOR_NAME}"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as handle:
        for name, data in members.items():
            handle.writestr(name, data)
    return buffer.getvalue()


def _tool(tool_id, name, version, url, archive_bytes, members, license_files, *,
          archive_format="zip", vendored_archive=None) -> dict:
    tool = {
        "id": tool_id, "name": name, "version": version, "url": url,
        "sha256": _sha(archive_bytes), "archive_format": archive_format,
        "files": [{"path": path, "sha256": _sha(data)} for path, data in members.items()],
        "license_files": list(license_files),
    }
    if vendored_archive is not None:
        tool["vendored_archive"] = vendored_archive
    return tool


def _unrar_tool(archive_bytes=None, members=None, *, vendored_archive=None, archive_format="zip") -> dict:
    members = dict(UNRAR_MEMBERS if members is None else members)
    archive_bytes = _zip_bytes(members) if archive_bytes is None else archive_bytes
    return _tool("unrar", "UnRAR", "7.13.0", UNRAR_URL, archive_bytes, members, UNRAR_LICENSES,
                 archive_format=archive_format, vendored_archive=vendored_archive)


def _bandizip_tool() -> dict:
    return _tool("bandizip", "Bandizip", "6.29", BANDIZIP_URL, _zip_bytes(BANDIZIP_MEMBERS),
                 BANDIZIP_MEMBERS, BANDIZIP_LICENSES)


def _lock(tools: list[dict]) -> dict:
    return {"schema_version": 1, "platform": "windows-x64", "tools": tools}


def _write_lock(repo: Path, lock: dict) -> None:
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    (repo / "scripts/desktop-tools.lock.json").write_text(json.dumps(lock, indent=2), encoding="utf-8")


def _write_cache(repo: Path, tool: dict, archive_bytes: bytes) -> None:
    name = Path(tool.get("vendored_archive") or tool["url"]).name
    directory = repo / "runtime/desktop-tool-cache" / tool["id"] / tool["version"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(archive_bytes)


def _write_vendor(repo: Path, relative: str, archive_bytes: bytes) -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(archive_bytes)
    return path


def make_repo(tmp_path, *, members=None, vendored=False, include_bandizip=False, cache=True):
    """A fake repo: UnRAR-only lock by default, optional synthetic Bandizip entry."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    members = dict(UNRAR_MEMBERS if members is None else members)
    archive = _zip_bytes(members)
    unrar = _unrar_tool(archive, members, vendored_archive=VENDOR_RELATIVE if vendored else None)
    tools = [unrar]
    if include_bandizip:
        tools.append(_bandizip_tool())
    _write_lock(repo, _lock(tools))
    if vendored:
        _write_vendor(repo, VENDOR_RELATIVE, archive)
    elif cache:
        _write_cache(repo, unrar, archive)
    if include_bandizip and cache:
        _write_cache(repo, tools[1], _zip_bytes(BANDIZIP_MEMBERS))
    stage = tmp_path / "stage"
    stage.mkdir(exist_ok=True)
    return repo, stage


# --------------------------------------------------------------------------- #
# Lock shape
# --------------------------------------------------------------------------- #
def test_missing_lock_is_never_fabricated(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(RuntimeError, match="desktop-tools.lock.json"):
        sdt.stage_fixed_tools(repo, stage)
    with pytest.raises(RuntimeError, match="desktop-tools.lock.json"):
        sdt.validate_fixed_tools(repo, stage)
    assert not (repo / "scripts/desktop-tools.lock.json").exists()


MUTATIONS = {
    "empty-tools": lambda lock: lock.__setitem__("tools", []),
    "missing-mandatory": lambda lock: lock.__setitem__("tools", [_bandizip_tool()]),
    "duplicate-id": lambda lock: lock["tools"].append(dict(lock["tools"][0])),
    "extra-tool": lambda lock: lock["tools"].append({**lock["tools"][0], "id": "winrar"}),
    "unsafe-path": lambda lock: lock["tools"][0]["files"][0].__setitem__("path", "../evil.exe"),
    "absolute-path": lambda lock: lock["tools"][0]["files"][0].__setitem__("path", "/etc/passwd"),
    "case-conflict": lambda lock: lock["tools"][0]["files"].append(
        {"path": lock["tools"][0]["files"][0]["path"].upper(), "sha256": "0" * 64}),
    "license-not-in-files": lambda lock: lock["tools"][0].__setitem__("license_files", ["nope.txt"]),
    "bad-sha": lambda lock: lock["tools"][0].__setitem__("sha256", "abc"),
    "bad-file-sha": lambda lock: lock["tools"][0]["files"][0].__setitem__("sha256", "zz"),
    "http-url": lambda lock: lock["tools"][0].__setitem__("url", "http://www.rarlab.com/tool.zip"),
    "third-party-host": lambda lock: lock["tools"][0].__setitem__("url", "https://example.com/tool.zip"),
    "cross-host-url": lambda lock: lock["tools"][0].__setitem__("url", "https://dl.bandisoft.com/x/UnRAR.zip"),
    "latest-url": lambda lock: lock["tools"][0].__setitem__("url", "https://www.rarlab.com/latest/tool.zip"),
    "latest-version": lambda lock: lock["tools"][0].__setitem__("version", "latest"),
    "non-version": lambda lock: lock["tools"][0].__setitem__("version", "current"),
    "version-traversal": lambda lock: lock["tools"][0].__setitem__("version", "../7.13"),
    "version-slash": lambda lock: lock["tools"][0].__setitem__("version", "7.13/evil"),
    "version-backslash": lambda lock: lock["tools"][0].__setitem__("version", "7.13\\evil"),
    "version-drive": lambda lock: lock["tools"][0].__setitem__("version", "C:7.13"),
    "version-whitespace": lambda lock: lock["tools"][0].__setitem__("version", "7.13 "),
    "vendored-absolute": lambda lock: lock["tools"][0].__setitem__("vendored_archive", "/etc/unrar.zip"),
    "vendored-traversal": lambda lock: lock["tools"][0].__setitem__("vendored_archive", "../unrar.zip"),
    "bad-schema": lambda lock: lock.__setitem__("schema_version", 2),
    "bad-platform": lambda lock: lock.__setitem__("platform", "linux-x64"),
    "bad-format": lambda lock: lock["tools"][0].__setitem__("archive_format", "rar"),
    "empty-files": lambda lock: lock["tools"][0].__setitem__("files", []),
}


@pytest.mark.parametrize("mutation", sorted(MUTATIONS), ids=sorted(MUTATIONS))
def test_lock_rejections(tmp_path, mutation):
    repo, stage = make_repo(tmp_path, cache=False)
    lock = json.loads((repo / "scripts/desktop-tools.lock.json").read_text(encoding="utf-8"))
    MUTATIONS[mutation](lock)
    _write_lock(repo, lock)
    with pytest.raises(RuntimeError, match="invalid fixed-tool lock"):
        sdt.load_lock(repo)
    with pytest.raises(RuntimeError):
        sdt.stage_fixed_tools(repo, stage)


def test_unrar_only_lock_is_valid_and_bandizip_is_optional(tmp_path):
    repo, _ = make_repo(tmp_path, cache=False)
    assert [tool["id"] for tool in sdt.lock_tools(sdt.load_lock(repo))] == ["unrar"]

    # A synthetic future entry is accepted, and normalised to the allowed order
    # even when the lock lists Bandizip first.
    _write_lock(repo, _lock([_bandizip_tool(), _unrar_tool()]))
    tools = sdt.lock_tools(sdt.load_lock(repo))
    assert [tool["id"] for tool in tools] == ["unrar", "bandizip"]


def test_valid_lock_accepts_seven_sfx_format(tmp_path):
    repo, _ = make_repo(tmp_path, cache=False)
    _write_lock(repo, _lock([_unrar_tool(archive_format="7z-sfx")]))
    assert sdt.lock_tools(sdt.load_lock(repo))[0]["archive_format"] == "7z-sfx"


def test_official_host_boundaries_are_per_tool():
    assert sdt._is_official_https_url(UNRAR_URL, "unrar")
    assert sdt._is_official_https_url(BANDIZIP_URL, "bandizip")
    assert not sdt._is_official_https_url("https://example.com/tool.zip", "unrar")
    assert not sdt._is_official_https_url("https://dl.bandisoft.com/x/UnRAR.zip", "unrar")
    assert not sdt._is_official_https_url("https://www.rarlab.com.evil.example/x.zip", "unrar")


# --------------------------------------------------------------------------- #
# Staging: cache, vendored snapshot, atomic reuse
# --------------------------------------------------------------------------- #
def test_stage_offline_requires_cached_archive(tmp_path):
    repo, stage = make_repo(tmp_path, cache=False)
    with pytest.raises(RuntimeError, match="no cached archive"):
        sdt.stage_fixed_tools(repo, stage, fetch=False)
    assert not (stage / "tools/unrar").exists()


def test_missing_archive_offline_leaves_the_stage_untouched(tmp_path):
    repo, stage = make_repo(tmp_path, cache=False, include_bandizip=True)
    with pytest.raises(RuntimeError, match="no cached archive"):
        sdt.stage_fixed_tools(repo, stage, fetch=False)
    assert not (stage / "tools/unrar").exists()
    assert not (stage / "tools/bandizip").exists()
    assert not (stage / "tools/fixed-tools.json").exists()


def test_stage_from_cache_then_validate(tmp_path):
    repo, stage = make_repo(tmp_path)
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    assert (stage / "tools/unrar/UnRAR.exe").read_bytes() == UNRAR_MEMBERS["UnRAR.exe"]
    assert (stage / "tools/unrar/WinRAR-License.txt").is_file()
    record = json.loads((stage / "tools/fixed-tools.json").read_text(encoding="utf-8"))
    assert [tool["id"] for tool in record["tools"]] == ["unrar"]
    sdt.validate_fixed_tools(repo, stage)  # must not raise
    assert sdt.staged_tool_problems(stage) == []


def test_stage_with_optional_bandizip_locked(tmp_path):
    repo, stage = make_repo(tmp_path, include_bandizip=True)
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    record = json.loads((stage / "tools/fixed-tools.json").read_text(encoding="utf-8"))
    assert [tool["id"] for tool in record["tools"]] == ["unrar", "bandizip"]
    assert (stage / "tools/bandizip/Bandizip/Bandizip.exe").is_file()
    assert sdt.staged_tool_problems(stage) == []
    sdt.validate_fixed_tools(repo, stage)


def test_stage_reuses_same_state_without_cache_or_network(tmp_path):
    repo, stage = make_repo(tmp_path)
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    shutil.rmtree(repo / "runtime")
    sdt.stage_fixed_tools(repo, stage, fetch=False)  # fast path, must not raise
    sdt.validate_fixed_tools(repo, stage)
    assert (stage / "tools/unrar/UnRAR.exe").read_bytes() == UNRAR_MEMBERS["UnRAR.exe"]


def test_stage_repairs_tampered_target_and_backs_up_old(tmp_path):
    repo, stage = make_repo(tmp_path)
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    (stage / "tools/unrar/UnRAR.exe").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="sha256"):
        sdt.validate_fixed_tools(repo, stage)
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    assert (stage / "tools/unrar/UnRAR.exe").read_bytes() == UNRAR_MEMBERS["UnRAR.exe"]
    assert list((repo / "artifacts/desktop/tool-backups").iterdir()), "the replaced stage must be kept as a backup"


# --------------------------------------------------------------------------- #
# Vendored archive snapshot
# --------------------------------------------------------------------------- #
def test_vendored_archive_is_used_without_network(tmp_path, monkeypatch):
    repo, stage = make_repo(tmp_path, vendored=True, cache=False)
    monkeypatch.setattr(sdt, "_download", lambda *a, **k: pytest.fail("vendored staging must not download"))
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    assert (stage / "tools/unrar/UnRAR.exe").read_bytes() == UNRAR_MEMBERS["UnRAR.exe"]
    record = json.loads((stage / "tools/fixed-tools.json").read_text(encoding="utf-8"))
    assert record["tools"][0]["vendored_archive"] == VENDOR_RELATIVE
    assert (repo / VENDOR_RELATIVE).is_file()  # the tracked snapshot is never modified
    sdt.validate_fixed_tools(repo, stage)


def test_vendored_archive_missing_fails_without_url_fallback(tmp_path, monkeypatch):
    repo, stage = make_repo(tmp_path, vendored=True, cache=False)
    (repo / VENDOR_RELATIVE).unlink()
    monkeypatch.setattr(sdt, "_download", lambda *a, **k: pytest.fail("must not fall back to the url"))
    with pytest.raises(RuntimeError, match="vendored archive"):
        sdt.stage_fixed_tools(repo, stage, fetch=False)
    with pytest.raises(RuntimeError, match="vendored archive"):
        sdt.validate_fixed_tools(repo, stage)
    assert not (stage / "tools/unrar").exists()


def test_vendored_archive_hash_mismatch_fails(tmp_path, monkeypatch):
    repo, stage = make_repo(tmp_path, vendored=True, cache=False)
    (repo / VENDOR_RELATIVE).write_bytes(b"tampered snapshot")
    monkeypatch.setattr(sdt, "_download", lambda *a, **k: pytest.fail("must not fall back to the url"))
    with pytest.raises(RuntimeError, match="sha256"):
        sdt.stage_fixed_tools(repo, stage, fetch=False)


def test_vendored_archive_must_be_a_real_file_not_a_symlink(tmp_path):
    repo, stage = make_repo(tmp_path, vendored=True, cache=False)
    target = tmp_path / "elsewhere.zip"
    shutil.move(str(repo / VENDOR_RELATIVE), str(target))
    os.symlink(target, repo / VENDOR_RELATIVE)
    with pytest.raises(RuntimeError, match="symlink"):
        sdt.stage_fixed_tools(repo, stage, fetch=False)


def test_vendored_archive_rejects_a_symlinked_ancestor_directory(tmp_path):
    repo, stage = make_repo(tmp_path, vendored=True, cache=False)
    real = tmp_path / "real-vendor"
    shutil.move(str(repo / "tools/desktop-vendor"), str(real))
    os.symlink(real, repo / "tools/desktop-vendor")
    with pytest.raises(RuntimeError, match="symlink"):
        sdt.stage_fixed_tools(repo, stage, fetch=False)
    with pytest.raises(RuntimeError, match="symlink"):
        sdt.validate_fixed_tools(repo, stage)


def test_record_tracks_the_vendored_identity(tmp_path):
    repo, stage = make_repo(tmp_path, vendored=True, cache=False)
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    # Drop the vendored snapshot from the lock (same archive hash). The record
    # still names it, so validation must fail until the stage is regenerated.
    _write_lock(repo, _lock([_unrar_tool()]))
    with pytest.raises(RuntimeError, match="vendored_archive"):
        sdt.validate_fixed_tools(repo, stage)
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    record = json.loads((stage / "tools/fixed-tools.json").read_text(encoding="utf-8"))
    assert record["tools"][0]["vendored_archive"] is None
    sdt.validate_fixed_tools(repo, stage)


# --------------------------------------------------------------------------- #
# Record id set exactness and stale trees
# --------------------------------------------------------------------------- #
def test_record_and_lock_id_sets_must_match_exactly(tmp_path):
    repo, stage = make_repo(tmp_path, include_bandizip=True)
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    _write_lock(repo, _lock([_unrar_tool()]))  # Bandizip no longer licensed
    with pytest.raises(RuntimeError, match="records .*bandizip"):
        sdt.validate_fixed_tools(repo, stage)
    assert any("stale tools/bandizip" in problem for problem in sdt._locked_stage_problems(stage, sdt.lock_tools(sdt.load_lock(repo))))
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    assert not (stage / "tools/bandizip").exists()
    assert list((repo / "artifacts/desktop/tool-backups").glob("bandizip-stale-*"))
    record = json.loads((stage / "tools/fixed-tools.json").read_text(encoding="utf-8"))
    assert [tool["id"] for tool in record["tools"]] == ["unrar"]
    sdt.validate_fixed_tools(repo, stage)


def test_standalone_record_flags_a_stale_bandizip_directory(tmp_path):
    repo, stage = make_repo(tmp_path)
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    stray = stage / "tools/bandizip/Bandizip/Bandizip.exe"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"historical bandizip")
    problems = sdt.staged_tool_problems(stage)
    assert any("stale tools/bandizip" in problem for problem in problems)


# --------------------------------------------------------------------------- #
# Corrupted stage / unsafe archives
# --------------------------------------------------------------------------- #
def test_validate_reports_missing_tool_symlink_and_hash_mismatch(tmp_path):
    repo, stage = make_repo(tmp_path)
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    shutil.rmtree(stage / "tools/unrar")
    with pytest.raises(RuntimeError, match="missing tools/unrar"):
        sdt.validate_fixed_tools(repo, stage)
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    (stage / "tools/unrar/UnRAR.exe").unlink()
    os.symlink("UnRAR-License.txt", stage / "tools/unrar/UnRAR.exe")
    with pytest.raises(RuntimeError, match="symlink"):
        sdt.validate_fixed_tools(repo, stage)
    (stage / "tools/unrar/UnRAR.exe").unlink()
    (stage / "tools/unrar/UnRAR.exe").write_bytes(b"wrong")
    with pytest.raises(RuntimeError, match="sha256"):
        sdt.validate_fixed_tools(repo, stage)


def test_zip_case_conflicting_members_rejected(tmp_path):
    members = {**UNRAR_MEMBERS, "unrar.exe": b"different bytes"}
    archive = _zip_bytes(members)
    repo, stage = make_repo(tmp_path, cache=False)
    tool = _unrar_tool(archive, UNRAR_MEMBERS)
    _write_lock(repo, _lock([tool]))
    _write_cache(repo, tool, archive)
    with pytest.raises(RuntimeError, match="case-conflicting"):
        sdt.stage_fixed_tools(repo, stage, fetch=False)


def test_zip_symlink_member_rejected(tmp_path):
    info = zipfile.ZipInfo("UnRAR.exe")
    info.external_attr = (0o120777 << 16)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr(info, "UnRAR-License.txt")
        for name in UNRAR_LICENSES:
            handle.writestr(name, UNRAR_MEMBERS[name])
    archive = buffer.getvalue()
    repo, stage = make_repo(tmp_path, cache=False)
    tool = _unrar_tool(archive, UNRAR_MEMBERS)
    _write_lock(repo, _lock([tool]))
    _write_cache(repo, tool, archive)
    with pytest.raises(RuntimeError, match="symlink member"):
        sdt.stage_fixed_tools(repo, stage, fetch=False)


# --------------------------------------------------------------------------- #
# Download boundary (bounded attempts, official redirects only)
# --------------------------------------------------------------------------- #
def test_fetch_publishes_only_on_matching_hash(tmp_path, monkeypatch):
    repo, stage = make_repo(tmp_path, cache=False)
    lock = json.loads((repo / "scripts/desktop-tools.lock.json").read_text(encoding="utf-8"))
    calls: list[str] = []

    def tampered(url, dest, *a, **k):
        calls.append(url)
        dest.write_bytes(b"tampered download")

    monkeypatch.setattr(sdt, "_download", tampered)
    with pytest.raises(RuntimeError, match="sha256"):
        sdt.stage_fixed_tools(repo, stage, fetch=True)
    # A hash mismatch is final: the same version is never downloaded again.
    assert calls == [lock["tools"][0]["url"]]
    assert not [p for p in (repo / "runtime/desktop-tool-cache").rglob("*") if p.is_file()]

    monkeypatch.setattr(sdt, "_download", lambda url, dest, *a, **k: dest.write_bytes(_zip_bytes(UNRAR_MEMBERS)))
    sdt.stage_fixed_tools(repo, stage, fetch=True)
    sdt.validate_fixed_tools(repo, stage)
    assert (stage / "tools/unrar/UnRAR.exe").read_bytes() == UNRAR_MEMBERS["UnRAR.exe"]


class _FakeResponse:
    def __init__(self, url: str):
        self._url = url
        self.status = 200

    def geturl(self) -> str:
        return self._url

    def read(self) -> bytes:
        return b"payload"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_is_bounded_and_rejects_off_official_redirect(tmp_path, monkeypatch):
    assert sdt._download.__defaults__ == (2,)
    observed: list[int] = []

    def redirecting(url, timeout=None):
        observed.append(timeout)
        return _FakeResponse("https://evil.example/tool.exe")

    monkeypatch.setattr(sdt.urllib.request, "urlopen", redirecting)
    with pytest.raises(sdt.OfficialHostError):
        sdt._download(UNRAR_URL, tmp_path / "a.bin", "unrar")
    assert observed == [20]  # short timeout, and an off-host redirect is not retried
    assert not (tmp_path / "a.bin").exists()

    monkeypatch.setattr(sdt.urllib.request, "urlopen",
                        lambda url, timeout=None: _FakeResponse("https://www.rarlab.com/rar/unrarw64.exe"))
    sdt._download(UNRAR_URL, tmp_path / "b.bin", "unrar")
    assert (tmp_path / "b.bin").read_bytes() == b"payload"


def test_download_retries_at_most_twice(tmp_path, monkeypatch):
    calls = []

    def down(url, timeout=None):
        calls.append(url)
        raise RuntimeError("network down")

    monkeypatch.setattr(sdt.urllib.request, "urlopen", down)
    with pytest.raises(RuntimeError, match="could not download"):
        sdt._download(UNRAR_URL, tmp_path / "a.bin", "unrar")
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# Wiring: engine validator, evidence collector, CLI, real repo input
# --------------------------------------------------------------------------- #
def test_lock_file_is_not_rewritten(tmp_path):
    repo, stage = make_repo(tmp_path, vendored=True, cache=False)
    before = (repo / "scripts/desktop-tools.lock.json").read_bytes()
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    assert (repo / "scripts/desktop-tools.lock.json").read_bytes() == before
    assert (repo / VENDOR_RELATIVE).read_bytes()  # snapshot untouched


def test_tools_only_connects_the_tools_to_an_old_engine(tmp_path):
    # An existing 0.2.0 stage has a build-info but no fixed-tools record yet; the
    # first connection must stage the tools first and only then report build-info.
    repo, stage = make_repo(tmp_path, vendored=True, cache=False)
    (stage / "build-info.json").write_text(json.dumps({
        "python": "3.13.5", "platform": "win32", "tool": "7z.exe",
        "excluded": ["passwords.txt", "config.json", "restoreAB.exe", "Bandizip", "UnRAR"],
    }), encoding="utf-8")
    assert sdt.staged_tool_problems(stage), "the old stage has no fixed-tools record yet"
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    stage_desktop_engine.write_build_info(stage, tool_name="reorder-engine")
    info = json.loads((stage / "build-info.json").read_text(encoding="utf-8"))
    assert info["excluded"] == ["passwords.txt", "config.json", "restoreAB.exe", "Bandizip"]
    assert [tool["id"] for tool in info["bundled_tools"]] == ["unrar"]
    assert info["bundled_tools"][0]["version"] == "7.13.0"
    assert info["bundled_tools"][0]["sha256"] == _sha(_zip_bytes(UNRAR_MEMBERS))
    assert info["python"] == "3.13.5" and info["tool"] == "7z.exe"  # old engine identity kept


def test_engine_validator_includes_fixed_tools(tmp_path):
    repo, stage = make_repo(tmp_path)
    with pytest.raises(RuntimeError) as missing:
        stage_desktop_engine.validate_staged_engine(stage)
    assert "fixed-tools.json" in str(missing.value)
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    with pytest.raises(RuntimeError) as incomplete:
        stage_desktop_engine.validate_staged_engine(stage, repo)
    assert "fixed tools:" not in str(incomplete.value)  # the tools part now passes
    assert "reorder-engine.exe" in str(incomplete.value)  # the rest of the stage is still required


def test_validate_only_cli(tmp_path, monkeypatch):
    repo, stage = make_repo(tmp_path)
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    monkeypatch.setattr(sys, "argv", ["stage_desktop_tools.py", "--repo", str(repo), "--stage", str(stage),
                                      "--validate-only"])
    assert sdt.main() == 0


def test_collect_desktop_licenses_reads_the_staged_license_files(tmp_path):
    # Proves the lock license_files and the staged layout stay aligned for the
    # evidence collector, which calls validate_fixed_tools(repo, stage).
    import collect_desktop_licenses

    repo, _ = make_repo(tmp_path)
    stage = repo / "apps/desktop/src-tauri/resources/engine"  # the collector uses this path directly
    stage.mkdir(parents=True)
    sdt.stage_fixed_tools(repo, stage, fetch=False)
    evidence = tmp_path / "evidence"
    entries = collect_desktop_licenses.collect_fixed_tools(repo, evidence)
    assert [entry["name"] for entry in entries] == ["UnRAR"]
    assert entries[0]["status"] == "text"
    assert len(entries[0]["license_files"]) == 2
    assert sum(1 for path in evidence.rglob("*") if path.is_file()) == 2


def test_real_repo_lock_and_vendored_snapshot(tmp_path):
    """Read-only check of the real tracked lock and UnRAR snapshot."""
    lock = sdt.load_lock(REPO_ROOT)
    tools = sdt.lock_tools(lock)
    assert [tool["id"] for tool in tools] == ["unrar"]
    tool = tools[0]
    assert tool["version"] == "7.13.0"
    assert tool["vendored_archive"] == VENDOR_RELATIVE
    assert sdt._vendored_problems(tool, REPO_ROOT) == []
    with zipfile.ZipFile(REPO_ROOT / VENDOR_RELATIVE) as handle:
        names = sorted(handle.namelist())
    assert names == ["UnRAR-License.txt", "UnRAR.exe", "WinRAR-License.txt"]  # flat, no rarreg.key
    # Unpack-only hash check of the three public files; nothing is executed.
    sdt._extract_zip(tool, REPO_ROOT / VENDOR_RELATIVE, tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == names
