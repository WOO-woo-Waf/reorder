"""Fake-only tests for the builtin-defaults staging and the release packaging names.

Covers ``scripts/stage_desktop_defaults.py`` (lock shape, canonical sources,
byte-identical staging, tamper detection), the engine wiring that validates and
records the builtin catalog, the Tauri product/version naming used by
``scripts/package_desktop.py``, and the fixture-only doc selection in
``scripts/stage_desktop_resources.py``. No real freeze/build is performed and no
word-list text is ever asserted or logged.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import package_desktop  # noqa: E402
import stage_desktop_defaults as sdd  # noqa: E402
import stage_desktop_engine  # noqa: E402
import stage_desktop_resources as sdr  # noqa: E402

PW_TEXT = "# comment\npass1\npass2\npass1\n\n  spaced  \n"
KW_TEXT = "# kw\nlongkeyword\nab\nabcd\n"


def _identity(text: str, entry_id: str) -> tuple[int, str, int]:
    data = text.encode("utf-8")
    values = sdd.parse_passwords(text) if entry_id == "passwords" else sdd.parse_keywords(text)
    return len(data), hashlib.sha256(data).hexdigest(), len(values)


def _defaults_lock(pw_text: str = PW_TEXT, kw_text: str = KW_TEXT) -> dict:
    files = []
    for entry_id, text in (("passwords", pw_text), ("keywords", kw_text)):
        size, digest, count = _identity(text, entry_id)
        files.append({"id": entry_id, "path": sdd.SOURCE_BY_ID[entry_id]["path"],
                      "sha256": digest, "size": size, "count": count})
    return {"schema_version": 1, "version": "2026.10.08", "files": files}


def make_defaults_repo(tmp_path, *, pw_text: str = PW_TEXT, kw_text: str = KW_TEXT, lock: dict | None = None):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "resources").mkdir(parents=True)
    (repo / "resources/passwords.txt").write_bytes(pw_text.encode("utf-8"))
    (repo / "resources/keywords.txt").write_bytes(kw_text.encode("utf-8"))
    _write_lock(repo, lock if lock is not None else _defaults_lock(pw_text, kw_text))
    stage = repo / "apps/desktop/src-tauri/resources/engine"
    stage.mkdir(parents=True)
    return repo, stage


def _write_lock(repo: Path, lock: dict) -> None:
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    (repo / "scripts/desktop-defaults.lock.json").write_text(json.dumps(lock, indent=2), encoding="utf-8")


def _read_lock(repo: Path) -> dict:
    return json.loads((repo / "scripts/desktop-defaults.lock.json").read_text(encoding="utf-8"))


def _write_tools_record(stage: Path, tools: list[dict] | None = None) -> None:
    directory = stage / "tools"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "fixed-tools.json").write_text(json.dumps(
        {"schema_version": 1, "platform": "windows-x64", "tools": tools or []}), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Lock shape
# --------------------------------------------------------------------------- #
def test_missing_lock_is_never_fabricated(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    stage = repo / "apps/desktop/src-tauri/resources/engine"
    stage.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="desktop-defaults.lock.json"):
        sdd.stage_defaults(repo, stage)
    with pytest.raises(RuntimeError, match="desktop-defaults.lock.json"):
        sdd.validate_defaults(repo, stage)
    assert not (repo / "scripts/desktop-defaults.lock.json").exists()


MUTATIONS = {
    "bad-schema": lambda lock: lock.__setitem__("schema_version", 2),
    "bool-schema": lambda lock: lock.__setitem__("schema_version", True),
    "bad-version": lambda lock: lock.__setitem__("version", ""),
    "wrong-id": lambda lock: lock["files"][0].__setitem__("id", "profile"),
    "wrong-path": lambda lock: lock["files"][0].__setitem__("path", "builtin-keywords.txt"),
    "unsafe-path": lambda lock: lock["files"][0].__setitem__("path", "../builtin-passwords.txt"),
    "duplicate-id": lambda lock: lock["files"][1].__setitem__("id", "passwords"),
    "missing-id": lambda lock: lock.__setitem__("files", lock["files"][:1]),
    "extra-file": lambda lock: lock["files"].append(dict(lock["files"][0])),
    "bad-sha": lambda lock: lock["files"][0].__setitem__("sha256", "zz"),
    "bad-size": lambda lock: lock["files"][0].__setitem__("size", -1),
    "bool-size": lambda lock: lock["files"][0].__setitem__("size", True),
    "bad-count": lambda lock: lock["files"][0].__setitem__("count", "3"),
    "no-files": lambda lock: lock.__setitem__("files", []),
}


@pytest.mark.parametrize("mutation", sorted(MUTATIONS), ids=sorted(MUTATIONS))
def test_lock_rejections(tmp_path, mutation):
    repo, stage = make_defaults_repo(tmp_path)
    lock = _read_lock(repo)
    MUTATIONS[mutation](lock)
    _write_lock(repo, lock)
    with pytest.raises(RuntimeError, match="invalid builtin-defaults lock"):
        sdd.load_lock(repo)
    with pytest.raises(RuntimeError):
        sdd.stage_defaults(repo, stage)


def test_valid_lock_is_read_from_the_repo(tmp_path):
    repo, _ = make_defaults_repo(tmp_path)
    lock = sdd.load_lock(repo)
    assert [entry["id"] for entry in sdd.lock_files(lock)] == ["passwords", "keywords"]


# --------------------------------------------------------------------------- #
# Canonical sources
# --------------------------------------------------------------------------- #
def test_source_hash_mismatch_is_rejected(tmp_path):
    repo, stage = make_defaults_repo(tmp_path)
    (repo / "resources/passwords.txt").write_bytes(b"# changed\nother\n")
    with pytest.raises(RuntimeError, match="Builtin defaults sources are invalid"):
        sdd.stage_defaults(repo, stage)
    assert not (stage / "defaults").exists()


def test_lock_size_and_count_must_match_the_source(tmp_path):
    repo, stage = make_defaults_repo(tmp_path)
    lock = _read_lock(repo)
    lock["files"][0]["size"] += 1
    _write_lock(repo, lock)
    with pytest.raises(RuntimeError, match=r"size .* != locked"):
        sdd.stage_defaults(repo, stage)
    lock = _read_lock(repo)
    lock["files"][1]["count"] += 1
    _write_lock(repo, lock)
    with pytest.raises(RuntimeError, match=r"count .* != locked"):
        sdd.stage_defaults(repo, stage)


def test_missing_source_is_rejected(tmp_path):
    repo, stage = make_defaults_repo(tmp_path)
    (repo / "resources/keywords.txt").unlink()
    with pytest.raises(RuntimeError, match="missing builtin defaults source"):
        sdd.stage_defaults(repo, stage)


def test_source_with_symlinked_ancestor_is_rejected(tmp_path):
    repo, stage = make_defaults_repo(tmp_path)
    real = tmp_path / "real-resources"
    shutil.move(str(repo / "resources"), str(real))
    os.symlink(real, repo / "resources")
    with pytest.raises(RuntimeError, match="symlinked ancestor"):
        sdd.stage_defaults(repo, stage)


def test_source_that_is_not_a_regular_file_is_rejected(tmp_path):
    repo, stage = make_defaults_repo(tmp_path)
    (repo / "resources/passwords.txt").unlink()
    os.mkfifo(repo / "resources/passwords.txt")
    with pytest.raises(RuntimeError, match="not a regular file"):
        sdd.stage_defaults(repo, stage)


# --------------------------------------------------------------------------- #
# Staging
# --------------------------------------------------------------------------- #
def test_stage_writes_the_manifest_and_byte_identical_text(tmp_path):
    repo, stage = make_defaults_repo(tmp_path)
    result = sdd.stage_defaults(repo, stage)
    assert result["staged"] is True
    defaults = stage / "defaults"
    assert sorted(path.name for path in defaults.iterdir()) == [
        "builtin-keywords.txt", "builtin-passwords.txt", "manifest.json"]
    # The manifest is exactly the lock content, with no extra source field.
    assert json.loads((defaults / "manifest.json").read_text(encoding="utf-8")) == _read_lock(repo)
    assert (defaults / "builtin-passwords.txt").read_bytes() == (repo / "resources/passwords.txt").read_bytes()
    assert (defaults / "builtin-keywords.txt").read_bytes() == (repo / "resources/keywords.txt").read_bytes()
    # The returned summary carries only counts, sizes and digests.
    assert [(f["id"], f["count"]) for f in result["files"]] == [("passwords", 3), ("keywords", 3)]
    assert "pass1" not in json.dumps(result)
    sdd.validate_defaults(repo, stage)  # must not raise


def test_stage_never_drags_a_private_config(tmp_path):
    repo, stage = make_defaults_repo(tmp_path)
    (repo / "config.json").write_text('{"private": true}', encoding="utf-8")
    (repo / "resources/config.json").write_text('{"private": true}', encoding="utf-8")
    sdd.stage_defaults(repo, stage)
    staged = sorted(path.name for path in (stage / "defaults").iterdir())
    assert staged == ["builtin-keywords.txt", "builtin-passwords.txt", "manifest.json"]
    assert "config" not in json.dumps(sdd.stage_defaults(repo, stage))


def test_stage_fast_path_reuses_without_touching_sources(tmp_path):
    repo, stage = make_defaults_repo(tmp_path)
    sdd.stage_defaults(repo, stage)
    before = (repo / "resources/passwords.txt").read_bytes()
    result = sdd.stage_defaults(repo, stage)
    assert result["staged"] is False
    assert (repo / "resources/passwords.txt").read_bytes() == before


def test_tampered_stage_is_repaired_with_a_backup(tmp_path):
    repo, stage = make_defaults_repo(tmp_path)
    sdd.stage_defaults(repo, stage)
    (stage / "defaults/builtin-passwords.txt").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="builtin-passwords.txt"):
        sdd.validate_defaults(repo, stage)
    result = sdd.stage_defaults(repo, stage)
    assert result["staged"] is True
    assert result["backup"] and Path(result["backup"]).is_dir()
    assert "defaults" in Path(result["backup"]).name
    assert list((repo / "artifacts/desktop/defaults-backups").iterdir())
    sdd.validate_defaults(repo, stage)


def test_manifest_with_an_extra_source_field_is_rejected(tmp_path):
    repo, stage = make_defaults_repo(tmp_path)
    sdd.stage_defaults(repo, stage)
    manifest_path = stage / "defaults/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"] = "resources/passwords.txt"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not match the locked catalog"):
        sdd.validate_defaults(repo, stage)
    assert sdd.staged_default_problems(stage)


def test_missing_or_symlinked_staged_file_is_rejected(tmp_path):
    repo, stage = make_defaults_repo(tmp_path)
    sdd.stage_defaults(repo, stage)
    (stage / "defaults/builtin-keywords.txt").unlink()
    with pytest.raises(RuntimeError, match="missing defaults/builtin-keywords.txt"):
        sdd.validate_defaults(repo, stage)
    sdd.stage_defaults(repo, stage)
    (stage / "defaults/builtin-keywords.txt").unlink()
    os.symlink("builtin-passwords.txt", stage / "defaults/builtin-keywords.txt")
    with pytest.raises(RuntimeError, match="symlink"):
        sdd.validate_defaults(repo, stage)


def test_staging_temp_tree_stays_outside_the_resources_tree(tmp_path):
    repo, stage = make_defaults_repo(tmp_path)
    sdd.stage_defaults(repo, stage)
    resources = stage.parent
    assert not list(resources.glob(".desktop-default-staging-*"))
    assert not list(stage.parent.parent.glob(".desktop-default-staging-*"))


def test_validate_only_cli(tmp_path, monkeypatch):
    repo, stage = make_defaults_repo(tmp_path)
    sdd.stage_defaults(repo, stage)
    monkeypatch.setattr(sys, "argv", ["stage_desktop_defaults.py", "--repo", str(repo), "--stage", str(stage),
                                      "--validate-only"])
    assert sdd.main() == 0


# --------------------------------------------------------------------------- #
# Engine wiring
# --------------------------------------------------------------------------- #
def test_engine_validate_reports_missing_defaults(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(RuntimeError) as error:
        stage_desktop_engine.validate_staged_engine(stage)
    assert "builtin defaults:" in str(error.value)
    assert "defaults/manifest.json" in str(error.value)


def test_write_build_info_records_the_catalog_identity(tmp_path):
    repo, stage = make_defaults_repo(tmp_path)
    sdd.stage_defaults(repo, stage)
    _write_tools_record(stage)  # build-info also reads the fixed-tools record
    stage_desktop_engine.write_build_info(stage, tool_name="reorder-engine")
    raw = (stage / "build-info.json").read_text(encoding="utf-8")
    info = json.loads(raw)
    catalog = info["builtin_defaults"]
    lock = _read_lock(repo)
    assert catalog["version"] == lock["version"]
    assert catalog["passwords"]["count"] == lock["files"][0]["count"]
    assert catalog["passwords"]["sha256"] == lock["files"][0]["sha256"]
    assert catalog["keywords"]["count"] == lock["files"][1]["count"]
    assert info["excluded"] == ["passwords.txt", "config.json", "restoreAB.exe"]
    # No word-list text may leak into build-info.
    assert "pass1" not in raw and "longkeyword" not in raw


# --------------------------------------------------------------------------- #
# Release naming (Tauri productName/version)
# --------------------------------------------------------------------------- #
def _write_tauri_config(repo: Path, product: str = "Hoshiribbon", version: str = "0.3.0") -> None:
    config = repo / "apps/desktop/src-tauri/tauri.conf.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"productName": product, "version": version}), encoding="utf-8")


def test_release_identity_comes_from_the_tauri_config(tmp_path):
    repo = tmp_path / "repo"
    _write_tauri_config(repo)
    names = package_desktop.load_release_identity(repo)
    assert names["exe"] == "Hoshiribbon.exe"
    assert names["package"] == "Hoshiribbon-0.3.0-windows-x64"
    assert names["portable"] == "Hoshiribbon-portable"
    assert package_desktop.HOST_BINARY == "reorder-desktop.exe"


def test_expected_installer_names_use_the_current_release():
    assert package_desktop.expected_installers("Hoshiribbon", "0.3.0") == ["Hoshiribbon_0.3.0_x64-setup.exe"]


def test_installer_selection_drops_a_stale_0_2(tmp_path):
    nsis = tmp_path / "nsis"
    nsis.mkdir()
    (nsis / "Hoshiribbon_0.3.0_x64-setup.exe").write_bytes(b"new")
    (nsis / "ReOrder_0.2.0_x64-setup.exe").write_bytes(b"stale")
    selected = package_desktop.select_current_installers(nsis, "Hoshiribbon", "0.3.0")
    assert [path.name for path in selected] == ["Hoshiribbon_0.3.0_x64-setup.exe"]


def test_pick_portable_destination_keeps_the_old_tree(tmp_path):
    output = tmp_path / "artifacts"
    output.mkdir()
    old = output / "ReOrder-portable"
    old.mkdir()
    (old / "ReOrder.exe").write_bytes(b"old build")
    names = {"portable": "Hoshiribbon-portable", "package": "Hoshiribbon-0.3.0-windows-x64"}
    # Only the old tree exists: the new tree simply takes the new name.
    destination = package_desktop.pick_portable_destination(output, names)
    assert destination == output / "Hoshiribbon-portable"
    # Both new names exist already: a fresh unique dir is used, never an old one.
    destination.mkdir()
    (output / names["package"]).mkdir()
    fresh = package_desktop.pick_portable_destination(output, names)
    assert fresh not in {old, destination, output / names["package"]}
    assert (old / "ReOrder.exe").read_bytes() == b"old build"


# --------------------------------------------------------------------------- #
# Resource doc fixtures
# --------------------------------------------------------------------------- #
def _fake_resources_repo(repo: Path, *, include_artwork: bool = True) -> None:
    docs = repo / "docs"
    (docs / "diagrams").mkdir(parents=True)
    for name in sdr.DOC_NAMES:
        if name == "desktop-artwork.md" and not include_artwork:
            continue
        (docs / name).write_text(f"# {name}\n", encoding="utf-8")
    for name in sdr.DIAGRAM_NAMES:
        (docs / "diagrams" / name).write_bytes(b"diagram")
    evidence = repo / "artifacts/desktop/license-evidence"
    evidence.mkdir(parents=True)
    (evidence / "manifest.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    (evidence / "SUMMARY.md").write_text("# summary\n", encoding="utf-8")


def test_stage_resources_includes_the_new_documents(tmp_path):
    repo = tmp_path / "repo"
    _fake_resources_repo(repo)
    out = tmp_path / "out"
    result = sdr.stage_resources(repo, out, backup_root=tmp_path / "backups",
                                 evidence=repo / "artifacts/desktop/license-evidence")
    assert "desktop-format-support.md" in result["docs"]
    assert "desktop-artwork.md" in result["docs"]
    assert (out / "docs/desktop-format-support.md").is_file()
    assert (out / "docs/desktop-artwork.md").is_file()


def test_stage_resources_rejects_a_missing_new_document(tmp_path):
    repo = tmp_path / "repo"
    _fake_resources_repo(repo, include_artwork=False)
    with pytest.raises(sdr.ResourceSelectionError, match="desktop-artwork.md"):
        sdr.stage_resources(repo, tmp_path / "out", backup_root=tmp_path / "backups",
                            evidence=repo / "artifacts/desktop/license-evidence")
