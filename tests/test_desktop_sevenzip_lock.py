"""Contract tests for the pinned 7-Zip build dependency (synthetic + real repo).

The desktop engine bundles exactly four redistributable 7-Zip files, pinned by
``scripts/desktop-sevenzip.lock.json``. These tests prove the default resolver
never scans ``tools/`` for an arbitrary 7-Zip, that an explicit ``--seven-zip``
must match the locked bytes, that staging validation detects a missing or
mixed-version bundle, and that ``--validate-only`` without a repository uses the
staged ``identity.json``. The real lock and its vendored directory are checked
read-only at the end; nothing is downloaded, executed or installed.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import stage_desktop_engine  # noqa: E402  (sibling build script)
import stage_desktop_tools as sdt  # noqa: E402

MEMBERS = {
    "7z.exe": b"MZ-7z-fake-26.04",
    "7z.dll": b"MZ-7zdll-fake-26.04",
    "License.txt": b"LGPL-2.1-or-later licence text",
    "readme.txt": b"7-Zip 26.04 readme",
}
URL = "https://github.com/ip7z/7zip/releases/download/26.04/7z2604-x64.exe"
ARCHIVE_SHA = hashlib.sha256(b"synthetic 26.04 installer").hexdigest()
REAL_ARCHIVE_SHA = "d54bf805f9f3704d1e8db2fa3498ae7ef2df0312b40b558e7c71c734430a665d"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entry(*, version="26.04", directory="tools/7zip/26.04", members=None, url=URL) -> dict:
    members = dict(MEMBERS if members is None else members)
    return {
        "name": "7-Zip",
        "version": version,
        "url": url,
        "sha256": ARCHIVE_SHA,
        "vendored_directory": directory,
        "files": [{"path": name, "sha256": _sha(data)} for name, data in members.items()],
        "license_files": ["License.txt"],
        "provenance": {"source_archive": url},
    }


def _lock(entry: dict) -> dict:
    return {"schema_version": 1, "platform": "windows-x64", "seven_zip": entry}


def _write_lock(repo: Path, entry: dict) -> None:
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    (repo / "scripts/desktop-sevenzip.lock.json").write_text(json.dumps(_lock(entry)), encoding="utf-8")


def make_repo(tmp_path, *, entry=None, members=None, mode="files"):
    """A fake repo carrying the lock and, unless ``mode`` says otherwise, its bundle."""
    repo = tmp_path / "repo"
    entry = _entry() if entry is None else entry
    _write_lock(repo, entry)
    if mode == "files":
        target = repo / entry["vendored_directory"]
        target.mkdir(parents=True, exist_ok=True)
        for name, data in (MEMBERS if members is None else members).items():
            (target / name).write_bytes(data)
    return repo, entry


def stage_from(repo: Path, entry: dict, tmp_path) -> Path:
    """Build a fake engine stage holding the pinned four files plus identity.json."""
    stage = tmp_path / "stage"
    tools = stage / "tools/7zip"
    tools.mkdir(parents=True)
    for item in entry["files"]:
        (tools / item["path"]).write_bytes((repo / entry["vendored_directory"] / item["path"]).read_bytes())
    (tools / "identity.json").write_text(json.dumps(entry), encoding="utf-8")
    return stage


# --------------------------------------------------------------------------- #
# Lock shape
# --------------------------------------------------------------------------- #
def test_missing_lock_is_never_fabricated(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="desktop-sevenzip.lock.json"):
        stage_desktop_engine.load_seven_zip_lock(repo)
    with pytest.raises(RuntimeError, match="desktop-sevenzip.lock.json"):
        stage_desktop_engine.find_seven_zip(repo, None)
    assert not (repo / "scripts/desktop-sevenzip.lock.json").exists()


MUTATIONS = {
    "bad-schema": lambda lock: lock.__setitem__("schema_version", 2),
    "bad-platform": lambda lock: lock.__setitem__("platform", "linux-x64"),
    "missing-entry": lambda lock: lock.__setitem__("seven_zip", None),
    "latest-version": lambda lock: lock["seven_zip"].__setitem__("version", "latest"),
    "http-url": lambda lock: lock["seven_zip"].__setitem__("url", "http://github.com/ip7z/7zip/x"),
    "third-party-host": lambda lock: lock["seven_zip"].__setitem__("url", "https://example.com/7z.exe"),
    "url-without-version": lambda lock: lock["seven_zip"].__setitem__(
        "url", "https://github.com/ip7z/7zip/releases/download/x/7z.exe"),
    "bad-archive-sha": lambda lock: lock["seven_zip"].__setitem__("sha256", "abc"),
    "missing-file": lambda lock: lock["seven_zip"]["files"].pop(0),
    "extra-file": lambda lock: lock["seven_zip"]["files"].append({"path": "7zFM.exe", "sha256": "0" * 64}),
    "duplicate-file": lambda lock: lock["seven_zip"]["files"].append(dict(lock["seven_zip"]["files"][0])),
    "bad-file-sha": lambda lock: lock["seven_zip"]["files"][0].__setitem__("sha256", "zz"),
    "slash-file": lambda lock: lock["seven_zip"]["files"][0].__setitem__("path", "sub/7z.exe"),
    "dir-traversal": lambda lock: lock["seven_zip"].__setitem__("vendored_directory", "../7zip"),
    "dir-version-mismatch": lambda lock: lock["seven_zip"].__setitem__("vendored_directory", "tools/7zip/25.01"),
    "license-not-listed": lambda lock: lock["seven_zip"].__setitem__("license_files", ["nope.txt"]),
}


@pytest.mark.parametrize("mutation", sorted(MUTATIONS), ids=sorted(MUTATIONS))
def test_lock_rejections(tmp_path, mutation):
    repo, entry = make_repo(tmp_path)
    lock = _lock(entry)
    MUTATIONS[mutation](lock)
    (repo / "scripts/desktop-sevenzip.lock.json").write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid seven-zip lock"):
        stage_desktop_engine.load_seven_zip_lock(repo)


# --------------------------------------------------------------------------- #
# Default path selection (no directory scan)
# --------------------------------------------------------------------------- #
def test_default_selection_uses_the_locked_directory(tmp_path):
    repo, _ = make_repo(tmp_path)
    # A decoy 25.01 tree must never win the default resolution.
    decoy = repo / "tools/7zip/Files/7-Zip"
    decoy.mkdir(parents=True)
    (decoy / "7z.exe").write_bytes(b"MZ-old-25.01")
    (decoy / "7z.dll").write_bytes(b"MZ-old-25.01-dll")
    chosen = stage_desktop_engine.find_seven_zip(repo, None)
    assert chosen == repo / "tools/7zip/26.04/7z.exe"


def test_find_seven_zip_requires_the_locked_directory(tmp_path):
    repo, _ = make_repo(tmp_path, mode="lock-only")
    with pytest.raises(RuntimeError, match="locked 7-Zip directory"):
        stage_desktop_engine.find_seven_zip(repo, None)


def test_find_seven_zip_detects_a_mixed_executable(tmp_path):
    repo, entry = make_repo(tmp_path)
    (repo / entry["vendored_directory"] / "7z.exe").write_bytes(b"MZ-half-swapped-25.01")
    with pytest.raises(RuntimeError, match="locked 7-Zip executable"):
        stage_desktop_engine.find_seven_zip(repo, None)


# --------------------------------------------------------------------------- #
# Explicit --seven-zip must match the lock (no mixed packages)
# --------------------------------------------------------------------------- #
def test_explicit_seven_zip_must_match_the_locked_bytes(tmp_path):
    repo, _ = make_repo(tmp_path)
    mismatched = tmp_path / "7z.exe"
    mismatched.write_bytes(b"MZ-different-build")
    with pytest.raises(RuntimeError, match="not the locked 7-Zip"):
        stage_desktop_engine.find_seven_zip(repo, mismatched)
    matching = repo / "tools/7zip/26.04/7z.exe"
    assert stage_desktop_engine.find_seven_zip(repo, matching) == matching


def test_explicit_seven_zip_missing_file(tmp_path):
    repo, _ = make_repo(tmp_path)
    with pytest.raises(RuntimeError, match="does not exist"):
        stage_desktop_engine.find_seven_zip(repo, tmp_path / "nope.exe")


# --------------------------------------------------------------------------- #
# Stage validation (missing / mixed / extra files)
# --------------------------------------------------------------------------- #
def test_stage_validation_accepts_the_intact_bundle(tmp_path):
    repo, entry = make_repo(tmp_path)
    stage = stage_from(repo, entry, tmp_path)
    assert stage_desktop_engine._seven_zip_stage_problems(stage, repo) == []
    assert stage_desktop_engine._seven_zip_stage_problems(stage, None) == []


def test_stage_validation_flags_a_missing_file(tmp_path):
    repo, entry = make_repo(tmp_path)
    stage = stage_from(repo, entry, tmp_path)
    (stage / "tools/7zip/7z.dll").unlink()
    assert any("missing tools/7zip/7z.dll" in item
               for item in stage_desktop_engine._seven_zip_stage_problems(stage, repo))


def test_stage_validation_flags_a_mixed_version(tmp_path):
    repo, entry = make_repo(tmp_path)
    stage = stage_from(repo, entry, tmp_path)
    (stage / "tools/7zip/7z.exe").write_bytes(b"MZ-25.01-swapped-in")
    assert any("7z.exe sha256" in item
               for item in stage_desktop_engine._seven_zip_stage_problems(stage, repo))


def test_stage_validation_rejects_extra_binaries(tmp_path):
    repo, entry = make_repo(tmp_path)
    stage = stage_from(repo, entry, tmp_path)
    (stage / "tools/7zip/7zFM.exe").write_bytes(b"MZ-gui-not-allowed")
    assert any("7zFM.exe" in item
               for item in stage_desktop_engine._seven_zip_stage_problems(stage, repo))


def test_stage_validation_without_repo_uses_the_staged_identity(tmp_path):
    repo, entry = make_repo(tmp_path)
    stage = stage_from(repo, entry, tmp_path)
    (stage / "tools/7zip/identity.json").unlink()
    problems = stage_desktop_engine._seven_zip_stage_problems(stage, None)
    assert any("identity.json" in item for item in problems)
    # The tracked lock must still validate the same stage.
    assert stage_desktop_engine._seven_zip_stage_problems(stage, repo) == []


# --------------------------------------------------------------------------- #
# build-info identity (separate field, bundled_tools contract unchanged)
# --------------------------------------------------------------------------- #
def test_build_info_records_primary_seven_zip(tmp_path):
    repo, entry = make_repo(tmp_path)
    stage = stage_from(repo, entry, tmp_path)
    (stage / "tools/fixed-tools.json").write_text(json.dumps({
        "schema_version": 1, "platform": "windows-x64",
        "tools": [
            {"id": "unrar", "name": "UnRAR", "version": "7.13.0", "sha256": "a" * 64},
            {"id": "bandizip", "name": "Bandizip CLI", "version": "7.40.0.1", "sha256": "b" * 64},
        ],
    }), encoding="utf-8")
    stage_desktop_engine.write_build_info(stage, tool_name="reorder-engine")
    info = json.loads((stage / "build-info.json").read_text(encoding="utf-8"))
    assert info["primary_seven_zip"]["version"] == "26.04"
    assert info["primary_seven_zip"]["sha256"] == ARCHIVE_SHA
    paths = [item["path"] for item in info["primary_seven_zip"]["files"]]
    assert paths == ["7z.exe", "7z.dll", "License.txt", "readme.txt"]
    # The bundled-tools contract stays the two locked extraction tools.
    assert [tool["id"] for tool in info["bundled_tools"]] == ["unrar", "bandizip"]


# --------------------------------------------------------------------------- #
# Real repository lock and vendored bundle (read-only)
# --------------------------------------------------------------------------- #
def test_real_repo_lock_and_vendored_bundle():
    entry = stage_desktop_engine.load_seven_zip_lock(REPO_ROOT)
    assert entry["version"] == "26.04"
    assert entry["vendored_directory"] == "tools/7zip/26.04"
    assert entry["url"] == URL
    assert entry["sha256"] == REAL_ARCHIVE_SHA
    directory = REPO_ROOT / entry["vendored_directory"]
    assert sorted(path.name for path in directory.iterdir()) == ["7z.dll", "7z.exe", "License.txt", "readme.txt"]
    for item in entry["files"]:
        assert sdt.sha256_file(directory / item["path"]) == item["sha256"]
    # The default resolver returns the locked 26.04 bundle, never the 25.01 tree.
    chosen = stage_desktop_engine.find_seven_zip(REPO_ROOT, None)
    assert chosen == directory / "7z.exe"
    assert "Files/7-Zip" not in chosen.as_posix()
    assert (REPO_ROOT / "tools/7zip/Files/7-Zip/7z.exe").is_file()
