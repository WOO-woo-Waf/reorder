#!/usr/bin/env python3
"""Stage the version-locked builtin password/keyword libraries into the engine.

The two libraries are pinned by the tracked ``scripts/desktop-defaults.lock.json``
(``schema_version`` 1) and come from exactly two hard-coded canonical sources:

* ``resources/passwords.txt`` -> ``defaults/builtin-passwords.txt``
* ``resources/keywords.txt``  -> ``defaults/builtin-keywords.txt``

This module never guesses a source, never copies a private ``config.json`` or any
other file, and never echoes the word lists: it reads the two canonical text files
locally, recomputes the ``count``/``sha256`` identity, and returns only counts and
digests. A source whose path, size, hash, entry count or symlink boundary does not
match the lock is a hard failure.

The staged ``defaults/manifest.json`` is exactly the lock content (no extra
``source`` field leaks the private word lists), and the two ``.txt`` files are
copied unchanged. Fresh files are built in a temporary tree outside the resource
tree on the same disk, verified, and only then published (with any previous tree
moved to an ignored backup) so a partially written stage can never be bundled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

REPO_DEFAULT = Path(__file__).resolve().parents[1]
LOCK_RELATIVE = Path("scripts/desktop-defaults.lock.json")
BACKUP_RELATIVE = Path("artifacts/desktop/defaults-backups")
DEFAULTS_DIR_NAME = "defaults"
MANIFEST_NAME = "manifest.json"

# The only two source mappings this module knows about. ``source`` is resolved
# against the repository root; ``path`` is the staged file name declared by the
# lock and read by the frozen engine.
SOURCES = (
    {"id": "passwords", "source": Path("resources/passwords.txt"), "path": "builtin-passwords.txt"},
    {"id": "keywords", "source": Path("resources/keywords.txt"), "path": "builtin-keywords.txt"},
)
SOURCE_BY_ID = {item["id"]: item for item in SOURCES}
ALLOWED_IDS = tuple(item["id"] for item in SOURCES)

_HEX256 = re.compile(r"^[0-9a-f]{64}$")
_BAD_SEGMENT = re.compile(r'[<>:"|?*\x00-\x1f]')


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_hex_digest(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX256.match(value.lower()))


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _safe_file_name(value: object) -> str | None:
    """Return a safe single-segment staged file name, or ``None`` when unsafe."""
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    text = value.replace("\\", "/")
    if "/" in text or text in (".", "..") or text.startswith("."):
        return None
    if _BAD_SEGMENT.search(text) or text != text.rstrip(" ."):
        return None
    return text


# --------------------------------------------------------------------------- #
# Parsing (mirrors the frozen engine so the recorded count matches runtime)
# --------------------------------------------------------------------------- #
def parse_passwords(text: str) -> tuple[str, ...]:
    """Ignore blank/``#`` lines, strip, and de-duplicate preserving first order."""
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
    """Ignore blank/``#`` lines, strip, and keep long words first."""
    values: list[str] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        values.append(raw)
    values.sort(key=len, reverse=True)
    return tuple(values)


def _parse(text: str, entry_id: str) -> tuple[str, ...]:
    return parse_passwords(text) if entry_id == "passwords" else parse_keywords(text)


# --------------------------------------------------------------------------- #
# Lock parsing / validation
# --------------------------------------------------------------------------- #
def _lock_problems(lock: object) -> list[str]:
    """Return every reason the builtin-defaults lock is unusable (empty is valid)."""
    if not isinstance(lock, dict):
        return ["lock root must be a JSON object"]
    problems: list[str] = []
    schema = lock.get("schema_version")
    if isinstance(schema, bool) or schema != 1:
        problems.append("schema_version must be the integer 1")
    version = lock.get("version")
    if not isinstance(version, str) or not version.strip():
        problems.append("version must be a non-empty string")
    files = lock.get("files")
    if not isinstance(files, list):
        return problems + ["files must be a JSON array"]
    if len(files) != len(SOURCES):
        problems.append(f"files must list exactly the {len(SOURCES)} builtin libraries, found {len(files)}")
    seen: set[str] = set()
    for index, item in enumerate(files):
        prefix = f"files[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{prefix} must be a JSON object")
            continue
        entry_id = item.get("id")
        if entry_id not in SOURCE_BY_ID:
            problems.append(f"{prefix}.id must be one of {list(ALLOWED_IDS)}, got {entry_id!r}")
            continue
        if entry_id in seen:
            problems.append(f"{prefix}.id duplicates {entry_id!r}")
            continue
        seen.add(entry_id)
        if item.get("path") != SOURCE_BY_ID[entry_id]["path"]:
            problems.append(f"{prefix}.path must be {SOURCE_BY_ID[entry_id]['path']!r}, "
                            f"got {item.get('path')!r}")
        if _safe_file_name(item.get("path")) is None:
            problems.append(f"{prefix}.path must be a safe single-segment file name, got {item.get('path')!r}")
        if not _is_hex_digest(item.get("sha256")):
            problems.append(f"{prefix}.sha256 must be a 64 character sha256 hex digest")
        if not _is_count(item.get("size")):
            problems.append(f"{prefix}.size must be a non-negative integer")
        if not _is_count(item.get("count")):
            problems.append(f"{prefix}.count must be a non-negative integer")
    missing = [entry_id for entry_id in ALLOWED_IDS if entry_id not in seen]
    if missing:
        problems.append(f"lock must contain {list(ALLOWED_IDS)}; missing {missing}")
    return problems


def load_lock(repo: Path) -> dict:
    """Read and strictly validate the tracked defaults lock; raise on any problem."""
    path = Path(repo) / LOCK_RELATIVE
    if not path.is_file():
        raise RuntimeError(
            f"missing tracked lock {path}; builtin password/keyword staging needs the "
            "pinned desktop-defaults.lock.json and must never guess a version or count"
        )
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{path} is not valid JSON: {exc}") from exc
    problems = _lock_problems(lock)
    if problems:
        raise RuntimeError(f"invalid builtin-defaults lock {path}: " + "; ".join(problems))
    return lock


def lock_files(lock: dict) -> list[dict]:
    """Return the normalized file entries ordered by ``ALLOWED_IDS``."""
    order = {entry_id: index for index, entry_id in enumerate(ALLOWED_IDS)}
    return [{
        "id": item["id"],
        "path": item["path"],
        "sha256": item["sha256"].lower(),
        "size": item["size"],
        "count": item["count"],
    } for item in sorted(lock["files"], key=lambda entry: order[entry["id"]])]


def _canonical_manifest(lock: dict) -> dict:
    """The exact content written to ``defaults/manifest.json`` (no source field)."""
    return {
        "schema_version": lock["schema_version"],
        "version": lock["version"],
        "files": lock_files(lock),
    }


# --------------------------------------------------------------------------- #
# Source and stage inspection
# --------------------------------------------------------------------------- #
def _walk_components(root: Path, relative: Path):
    node = Path(root)
    for part in relative.parts:
        node = node / part
        yield node


def _source_problems(repo: Path, lock: dict) -> list[str]:
    """Validate the lock against the canonical ``resources/{id}.txt`` sources.

    Checks the ancestor chain for symlinks, the regular-file boundary, the byte
    size, the sha256 and the parsed entry count. Returns digests/counts only.
    """
    repo = Path(repo)
    problems: list[str] = []
    for entry in lock_files(lock):
        source_rel = SOURCE_BY_ID[entry["id"]]["source"]
        symlinked = next((node for node in _walk_components(repo, source_rel) if node.is_symlink()), None)
        if symlinked is not None:
            problems.append(f"source {source_rel} has a symlinked ancestor: {symlinked}")
            continue
        source = repo / source_rel
        if not source.exists():
            problems.append(f"missing builtin defaults source {source_rel}")
            continue
        try:
            mode = source.stat().st_mode
        except OSError as exc:
            problems.append(f"builtin defaults source {source_rel} is not readable: {exc}")
            continue
        if not stat.S_ISREG(mode):
            problems.append(f"builtin defaults source {source_rel} is not a regular file")
            continue
        data = source.read_bytes()
        if len(data) != entry["size"]:
            problems.append(f"source {source_rel} size {len(data)} != locked {entry['size']}")
        actual = hashlib.sha256(data).hexdigest()
        if actual != entry["sha256"]:
            problems.append(f"source {source_rel} sha256 {actual} != locked {entry['sha256']}")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeError as exc:
            problems.append(f"source {source_rel} is not valid UTF-8: {exc}")
            continue
        count = len(_parse(text, entry["id"]))
        if count != entry["count"]:
            problems.append(f"source {source_rel} count {count} != locked {entry['count']}")
    return problems


def _dir_problems(root: Path, manifest: dict) -> list[str]:
    """Validate ``root`` (a ``defaults`` directory) against an expected manifest."""
    root = Path(root)
    if root.is_symlink():
        return [f"{DEFAULTS_DIR_NAME}/ is a symlink"]
    if not root.is_dir():
        return [f"missing {DEFAULTS_DIR_NAME}/ directory"]
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_symlink():
        return [f"{DEFAULTS_DIR_NAME}/{MANIFEST_NAME} is a symlink"]
    if not manifest_path.is_file():
        return [f"missing {DEFAULTS_DIR_NAME}/{MANIFEST_NAME}"]
    try:
        staged = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{DEFAULTS_DIR_NAME}/{MANIFEST_NAME} is not valid JSON: {exc}"]
    problems: list[str] = []
    if staged != manifest:
        problems.append(f"{DEFAULTS_DIR_NAME}/{MANIFEST_NAME} does not match the locked catalog")
    for entry in manifest["files"]:
        relative = f"{DEFAULTS_DIR_NAME}/{entry['path']}"
        node = root / entry["path"]
        if node.is_symlink():
            problems.append(f"{relative} is a symlink")
            continue
        if not node.is_file():
            problems.append(f"missing {relative}")
            continue
        size = node.stat().st_size
        if size != entry["size"]:
            problems.append(f"{relative} size {size} != locked {entry['size']}")
        actual = sha256_file(node)
        if actual != entry["sha256"]:
            problems.append(f"{relative} sha256 {actual} != locked {entry['sha256']}")
    return problems


def staged_default_problems(stage: Path) -> list[str]:
    """Validate the staged ``defaults`` tree from its own manifest (no lock)."""
    root = Path(stage) / DEFAULTS_DIR_NAME
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return [f"missing {DEFAULTS_DIR_NAME}/{MANIFEST_NAME} (builtin library catalog)"]
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{DEFAULTS_DIR_NAME}/{MANIFEST_NAME} is not valid JSON: {exc}"]
    problems = _lock_problems(raw)
    if problems:
        return [f"{DEFAULTS_DIR_NAME}/{MANIFEST_NAME}: {item}" for item in problems]
    return _dir_problems(root, _canonical_manifest(raw))


def validate_defaults(repo: Path, stage: Path) -> None:
    """Fail unless the staged builtin defaults match the tracked lock and sources."""
    lock = load_lock(repo)
    manifest = _canonical_manifest(lock)
    problems = list(_dir_problems(Path(stage) / DEFAULTS_DIR_NAME, manifest))
    problems.extend(_source_problems(repo, lock))
    if problems:
        raise RuntimeError("Builtin desktop defaults are incomplete: " + "; ".join(dict.fromkeys(problems)))


def _unique_backup(backup_root: Path, prefix: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = backup_root / f"{prefix}-{stamp}"
    suffix = 1
    while backup.exists():
        backup = backup_root / f"{prefix}-{stamp}-{suffix}"
        suffix += 1
    return backup


def catalog_summary(stage: Path) -> dict | None:
    """Return the recorded catalog identity (version/count/sha256), or None.

    Used by ``build-info``. Only counts, sizes and digests are returned; the word
    lists themselves are never read here beyond what the manifest already records.
    """
    manifest_path = Path(stage) / DEFAULTS_DIR_NAME / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return None
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if _lock_problems(raw):
        return None
    summary = {"version": raw["version"], "schema_version": raw["schema_version"]}
    for entry in lock_files(raw):
        summary[entry["id"]] = {"path": entry["path"], "count": entry["count"],
                                "size": entry["size"], "sha256": entry["sha256"]}
    return summary


def _summary(lock: dict, *, staged: bool, backup: Path | None) -> dict:
    return {
        "version": lock["version"],
        "staged": staged,
        "backup": str(backup) if backup else None,
        "files": [{"id": entry["id"], "path": entry["path"], "sha256": entry["sha256"],
                   "size": entry["size"], "count": entry["count"]} for entry in lock_files(lock)],
    }


def stage_defaults(repo: Path, stage: Path, *, backup_root: Path | None = None) -> dict:
    """Build ``stage/defaults`` from the locked canonical sources.

    Returns only counts, sizes and digests; the word lists are never returned,
    logged or written anywhere except the staged ``.txt`` copies.
    """
    repo = Path(repo)
    stage = Path(stage)
    if not stage.is_dir():
        raise RuntimeError(f"engine stage does not exist: {stage}")
    lock = load_lock(repo)
    source_problems = _source_problems(repo, lock)
    if source_problems:
        raise RuntimeError("Builtin defaults sources are invalid: " + "; ".join(source_problems))
    manifest = _canonical_manifest(lock)
    target = stage / DEFAULTS_DIR_NAME
    if not _dir_problems(target, manifest):
        return _summary(lock, staged=False, backup=None)
    # Build a fresh tree outside the resource tree but on the same disk so the
    # final publish is a same-filesystem move that never half-updates the stage.
    temp_root = Path(tempfile.mkdtemp(prefix=".desktop-default-staging-", dir=stage.parent.parent))
    backup: Path | None = None
    try:
        candidate = temp_root / DEFAULTS_DIR_NAME
        candidate.mkdir()
        for entry in lock_files(lock):
            source = repo / SOURCE_BY_ID[entry["id"]]["source"]
            shutil.copy2(source, candidate / entry["path"])
        (candidate / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        staged_problems = _dir_problems(candidate, manifest)
        if staged_problems:
            raise RuntimeError("staged builtin defaults did not verify before publishing: "
                               + "; ".join(staged_problems))
        if target.exists() or target.is_symlink():
            root = Path(backup_root) if backup_root is not None else repo / BACKUP_RELATIVE
            root.mkdir(parents=True, exist_ok=True)
            backup = _unique_backup(root, DEFAULTS_DIR_NAME)
            shutil.move(str(target), str(backup))
        try:
            shutil.move(str(candidate), str(target))
        except BaseException:
            if backup is not None:
                shutil.move(str(backup), str(target))
            raise
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
    validate_defaults(repo, stage)
    return _summary(lock, staged=True, backup=backup)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_DEFAULT)
    parser.add_argument("--stage", type=Path, default=None,
                        help="engine stage (default <repo>/apps/desktop/src-tauri/resources/engine)")
    parser.add_argument("--validate-only", action="store_true",
                        help="validate the staged builtin defaults without touching the stage")
    args = parser.parse_args()
    repo = args.repo.resolve()
    stage = (args.stage or repo / "apps/desktop/src-tauri/resources/engine").resolve()
    if args.validate_only:
        validate_defaults(repo, stage)
        print(f"Builtin defaults validated: {stage / DEFAULTS_DIR_NAME}")
        return 0
    result = stage_defaults(repo, stage)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
