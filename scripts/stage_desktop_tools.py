#!/usr/bin/env python3
"""Stage the version-locked UnRAR and Bandizip tools into the desktop engine.

The mandatory Windows tool is UnRAR; ``bandizip`` is an allowed optional entry
for a future build that has a written redistribution licence. Both are pinned by
the tracked ``scripts/desktop-tools.lock.json`` (``schema_version`` 1,
``platform`` ``windows-x64``). This module never guesses a version, never falls
back to a tool found on ``PATH``, and never accepts an unpinned ``latest`` build:

* :func:`stage_fixed_tools` reuses ``runtime/desktop-tool-cache/<id>/<version>/``
  offline and only downloads the locked ``https`` archive when ``fetch=True``; a
  tool with a ``vendored_archive`` snapshot is taken from that locked repo file
  and never downloaded;
* archives are unpacked into a unique temporary directory and published to the
  cache and stage only after every recorded ``sha256`` matches;
* :func:`validate_fixed_tools` re-checks the staged files against the lock and
  requires the record id set to equal the lock id set, so a package can never be
  reported as complete while UnRAR is missing or a stale Bandizip tree leaks in.

Only the approved ``files`` are copied out; a ``7z-sfx`` dependency is unpacked
with a trusted 7-Zip binary, never executed as a self-extracting installer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPO_DEFAULT = Path(__file__).resolve().parents[1]
LOCK_RELATIVE = Path("scripts/desktop-tools.lock.json")
CACHE_RELATIVE = Path("runtime/desktop-tool-cache")
BACKUP_RELATIVE = Path("artifacts/desktop/tool-backups")
TOOLS_DIRECTORY = "tools"
RECORD_NAME = "fixed-tools.json"

# ``unrar`` is mandatory; ``bandizip`` may only be added once a written
# redistribution licence exists. No other id is accepted.
REQUIRED_TOOL_IDS = ("unrar",)
ALLOWED_TOOL_IDS = ("unrar", "bandizip")
ARCHIVE_FORMATS = ("zip", "7z-sfx")

_HEX256 = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
# Characters that cannot appear in a portable, reproducible Windows file name.
_BAD_SEGMENT = re.compile(r'[<>:"|?*\x00-\x1f]')

# The lock may only point at these official vendor hosts (or their exact vendor
# hosts listed below); an arbitrary third-party https mirror is not acceptable.
OFFICIAL_TOOL_HOSTS = {
    "unrar": frozenset({"rarlab.com", "www.rarlab.com"}),
    "bandizip": frozenset({"bandisoft.com", "www.bandisoft.com", "dl.bandisoft.com"}),
}


# A download that lands anywhere else is a hard failure, not a retryable blip.
class OfficialHostError(RuntimeError):
    """Raised when a download resolves outside the tool's official hosts."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_hex_digest(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX256.match(value.lower()))


def _host_is_official(tool_id: object, host: object) -> bool:
    allowed = OFFICIAL_TOOL_HOSTS.get(tool_id, frozenset())
    return isinstance(host, str) and host.lower() in allowed


def _is_official_https_url(value: object, tool_id: object) -> bool:
    if not isinstance(value, str) or not value.startswith("https://"):
        return False
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.fragment:
        return False
    if any(segment.lower() in {"latest", "current"} for segment in parsed.path.split("/")):
        return False
    return _host_is_official(tool_id, parsed.hostname)


def _safe_relative_path(value: object) -> str | None:
    """Return a normalized archive-relative path, or ``None`` when it is unsafe."""
    if not isinstance(value, str):
        return None
    text = value.strip().replace("\\", "/")
    if not text or text.endswith("/") or text.startswith("/") or _DRIVE_PREFIX.match(text):
        return None
    segments: list[str] = []
    for segment in text.split("/"):
        if segment in ("", ".", ".."):
            return None
        if _BAD_SEGMENT.search(segment):
            return None
        if segment != segment.rstrip(" ."):
            return None
        segments.append(segment)
    return "/".join(segments)


# --------------------------------------------------------------------------- #
# Lock parsing / validation
# --------------------------------------------------------------------------- #
def _lock_problems(lock: object) -> list[str]:
    """Return every reason the fixed-tool lock is not usable (empty means valid)."""
    if not isinstance(lock, dict):
        return ["lock root must be a JSON object"]
    problems: list[str] = []
    if lock.get("schema_version") != 1:
        problems.append("schema_version must be the integer 1")
    if lock.get("platform") != "windows-x64":
        problems.append("platform must be 'windows-x64'")
    tools = lock.get("tools")
    if not isinstance(tools, list):
        return problems + ["tools must be a JSON array"]
    seen: set[str] = set()
    for index, tool in enumerate(tools):
        prefix = f"tools[{index}]"
        if not isinstance(tool, dict):
            problems.append(f"{prefix} must be a JSON object")
            continue
        tool_id = tool.get("id")
        if tool_id not in ALLOWED_TOOL_IDS:
            problems.append(f"{prefix}.id must be one of {list(ALLOWED_TOOL_IDS)}, got {tool_id!r}")
        elif tool_id in seen:
            problems.append(f"{prefix}.id duplicates {tool_id!r}")
        else:
            seen.add(tool_id)
        for key in ("name", "version"):
            value = tool.get(key)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{prefix}.{key} must be a non-empty string")
        version = tool.get("version")
        if isinstance(version, str) and version.strip():
            normalized_version = _safe_relative_path(version)
            if normalized_version is None or normalized_version != version or "/" in normalized_version:
                problems.append(f"{prefix}.version must be one safe path segment usable as a cache directory, got {version!r}")
            if not re.search(r"\d", version):
                problems.append(f"{prefix}.version must be a pinned version, got {version!r}")
            if version.strip().lower() in {"latest", "*"}:
                problems.append(f"{prefix}.version must not be {version!r}; the lock has to pin a released version")
        if not _is_official_https_url(tool.get("url"), tool_id):
            problems.append(f"{prefix}.url must be a pinned https URL on the official host for {tool_id!r} "
                            f"({sorted(OFFICIAL_TOOL_HOSTS.get(tool_id, ()))})")
        if not _is_hex_digest(tool.get("sha256")):
            problems.append(f"{prefix}.sha256 must be a 64 character sha256 hex digest")
        if tool.get("archive_format") not in ARCHIVE_FORMATS:
            problems.append(f"{prefix}.archive_format must be one of {list(ARCHIVE_FORMATS)}")
        vendored = tool.get("vendored_archive")
        if vendored is not None:
            normalized_vendored = _safe_relative_path(vendored)
            if normalized_vendored is None or normalized_vendored != vendored:
                problems.append(f"{prefix}.vendored_archive must be a safe repo-relative path, got {vendored!r}")
        file_paths: dict[str, str] = {}
        files = tool.get("files")
        if not isinstance(files, list) or not files:
            problems.append(f"{prefix}.files must be a non-empty array")
        else:
            for position, item in enumerate(files):
                item_prefix = f"{prefix}.files[{position}]"
                if not isinstance(item, dict):
                    problems.append(f"{item_prefix} must be a JSON object")
                    continue
                raw_path = item.get("path")
                normalized = _safe_relative_path(raw_path)
                if normalized is None:
                    problems.append(f"{item_prefix}.path is not a safe archive-relative path: {raw_path!r}")
                else:
                    key = normalized.lower()
                    if key in file_paths:
                        problems.append(f"{item_prefix}.path {normalized!r} conflicts case-insensitively with {file_paths[key]!r}")
                    else:
                        file_paths[key] = normalized
                if not _is_hex_digest(item.get("sha256")):
                    problems.append(f"{item_prefix}.sha256 must be a 64 character sha256 hex digest")
        licenses = tool.get("license_files")
        if not isinstance(licenses, list) or not licenses:
            problems.append(f"{prefix}.license_files must be a non-empty array")
        else:
            for position, raw_path in enumerate(licenses):
                normalized = _safe_relative_path(raw_path)
                if normalized is None:
                    problems.append(f"{prefix}.license_files[{position}] is not a safe path: {raw_path!r}")
                elif normalized.lower() not in file_paths:
                    problems.append(f"{prefix}.license_files[{position}] ({normalized}) is not listed in files")
    missing = [tool_id for tool_id in REQUIRED_TOOL_IDS if tool_id not in seen]
    unexpected = sorted(tool_id for tool_id in seen if tool_id not in ALLOWED_TOOL_IDS)
    if missing:
        problems.append(f"lock must contain the mandatory tool(s) {list(REQUIRED_TOOL_IDS)}; missing {missing}")
    if unexpected:
        problems.append(f"lock contains unexpected tool ids: {unexpected}")
    if not tools:
        problems.append("lock tools must not be empty")
    if len(tools) > len(ALLOWED_TOOL_IDS):
        problems.append(f"lock must contain at most {len(ALLOWED_TOOL_IDS)} tools, found {len(tools)}")
    return problems


def load_lock(repo: Path) -> dict:
    """Read and strictly validate the tracked lock; raise with every problem found."""
    path = Path(repo) / LOCK_RELATIVE
    if not path.is_file():
        raise RuntimeError(
            f"missing tracked lock {path}; UnRAR/Bandizip staging needs the pinned lock "
            "and must never guess a version or reuse a tool from PATH"
        )
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{path} is not valid JSON: {exc}") from exc
    problems = _lock_problems(lock)
    if problems:
        raise RuntimeError(f"invalid fixed-tool lock {path}: " + "; ".join(problems))
    return lock


def lock_tools(lock: dict) -> list[dict]:
    """Return the normalized tool entries of an already validated lock.

    Entries are ordered by ``ALLOWED_TOOL_IDS`` so the staged record and
    ``build-info`` stay deterministic no matter how the lock lists them.
    """
    order = {tool_id: index for index, tool_id in enumerate(ALLOWED_TOOL_IDS)}
    tools: list[dict] = []
    for tool in sorted(lock["tools"], key=lambda item: order[item["id"]]):
        tools.append({
            "id": tool["id"],
            "name": tool["name"],
            "version": tool["version"],
            "url": tool["url"],
            "sha256": tool["sha256"].lower(),
            "archive_format": tool["archive_format"],
            "vendored_archive": tool.get("vendored_archive"),
            "license": tool.get("license"),
            "files": [{"path": _safe_relative_path(item["path"]), "sha256": item["sha256"].lower()}
                      for item in tool["files"]],
            "license_files": [_safe_relative_path(item) for item in tool["license_files"]],
        })
    return tools


def _record_tools(record: object) -> tuple[list[str], list[dict]]:
    """Parse the staged ``fixed-tools.json`` record without consulting the lock."""
    if not isinstance(record, dict):
        return ["tools/fixed-tools.json root must be a JSON object"], []
    problems: list[str] = []
    if record.get("schema_version") != 1:
        problems.append("tools/fixed-tools.json schema_version must be the integer 1")
    if record.get("platform") != "windows-x64":
        problems.append("tools/fixed-tools.json platform must be 'windows-x64'")
    raw_tools = record.get("tools")
    if not isinstance(raw_tools, list):
        return problems + ["tools/fixed-tools.json tools must be an array"], []
    tools: list[dict] = []
    for index, entry in enumerate(raw_tools):
        if not isinstance(entry, dict):
            problems.append(f"record tools[{index}] must be a JSON object")
            continue
        tool_id = entry.get("id")
        if tool_id not in ALLOWED_TOOL_IDS:
            problems.append(f"record tools[{index}].id must be one of {list(ALLOWED_TOOL_IDS)}, got {tool_id!r}")
            continue
        vendored = entry.get("vendored_archive")
        normalized_vendored = None
        if vendored is not None:
            normalized_vendored = _safe_relative_path(vendored)
            if normalized_vendored is None or normalized_vendored != vendored:
                problems.append(f"record {tool_id}.vendored_archive must be a safe repo-relative path")
                continue
        files: list[dict] = []
        valid = True
        raw_files = entry.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            problems.append(f"record {tool_id}.files must be a non-empty array")
            valid = False
        else:
            for position, item in enumerate(raw_files):
                if not isinstance(item, dict):
                    problems.append(f"record {tool_id}.files[{position}] must be a JSON object")
                    valid = False
                    continue
                normalized = _safe_relative_path(item.get("path"))
                if normalized is None or not _is_hex_digest(item.get("sha256")):
                    problems.append(f"record {tool_id}.files[{position}] needs a safe path and sha256")
                    valid = False
                    continue
                files.append({"path": normalized, "sha256": item["sha256"].lower()})
        licenses: list[str] = []
        raw_licenses = entry.get("license_files")
        if not isinstance(raw_licenses, list) or not raw_licenses:
            problems.append(f"record {tool_id}.license_files must be a non-empty array")
            valid = False
        else:
            for position, raw_path in enumerate(raw_licenses):
                normalized = _safe_relative_path(raw_path)
                if normalized is None:
                    problems.append(f"record {tool_id}.license_files[{position}] is not a safe path")
                    valid = False
                    continue
                licenses.append(normalized)
        listed = {item["path"].lower() for item in files}
        if files and any(name.lower() not in listed for name in licenses):
            problems.append(f"record {tool_id}.license_files must be listed in files")
            valid = False
        if not valid:
            continue
        tools.append({
            "id": tool_id,
            "name": entry.get("name"),
            "version": entry.get("version"),
            "url": entry.get("url"),
            "sha256": str(entry.get("sha256", "")).lower(),
            "archive_format": entry.get("archive_format"),
            "vendored_archive": normalized_vendored,
            "files": files,
            "license_files": licenses,
        })
    ids = {tool["id"] for tool in tools}
    if len(tools) != len(ids):
        problems.append("record must not repeat a tool id")
    missing = [tool_id for tool_id in REQUIRED_TOOL_IDS if tool_id not in ids]
    if missing:
        problems.append(f"record must contain the mandatory tool(s) {list(REQUIRED_TOOL_IDS)}; missing {missing}")
    return problems, tools


# --------------------------------------------------------------------------- #
# Stage inspection
# --------------------------------------------------------------------------- #
def _scan_irregular(directory: Path) -> list[str]:
    """Report symlinks and non-regular entries anywhere under ``directory``."""
    problems: list[str] = []
    stack = [directory]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir())
        except OSError:
            continue
        for child in children:
            relative = child.relative_to(directory).as_posix()
            try:
                if child.is_symlink():
                    problems.append(f"{relative} is a symlink")
                    continue
                mode = child.stat().st_mode
            except OSError:
                problems.append(f"{relative} is not readable")
                continue
            if stat.S_ISDIR(mode):
                stack.append(child)
            elif not stat.S_ISREG(mode):
                problems.append(f"{relative} is not a regular file")
    return problems


def _file_problems(directory: Path, relative: str) -> list[str]:
    """Check one approved file exists as a real, non-symlinked regular file."""
    node = directory
    for part in Path(relative).parts:
        node = node / part
        if node.is_symlink():
            return [f"{relative} is a symlink"]
    if not node.exists():
        return [f"missing {relative}"]
    try:
        mode = node.stat().st_mode
    except OSError as exc:
        return [f"{relative} is not readable: {exc}"]
    if not stat.S_ISREG(mode):
        return [f"{relative} is not a regular file"]
    return []


def _stage_problems(tool: dict, directory: Path) -> list[str]:
    """Return every problem with one staged tool directory against its identity."""
    if directory.is_symlink():
        return [f"tools/{tool['id']} is a symlink"]
    if not directory.is_dir():
        return [f"missing tools/{tool['id']}"]
    problems: list[str] = []
    for entry in tool["files"]:
        found = _file_problems(directory, entry["path"])
        problems.extend(f"tools/{tool['id']}/{item}" for item in found)
        if found:
            continue
        actual = sha256_file(directory / entry["path"])
        if actual != entry["sha256"]:
            problems.append(f"tools/{tool['id']}/{entry['path']} sha256 {actual} != {entry['sha256']}")
    problems.extend(f"tools/{tool['id']}/{item}" for item in _scan_irregular(directory))
    return list(dict.fromkeys(problems))


def _stale_directory_problems(stage: Path, locked_ids: set[str]) -> list[str]:
    """Flag an allowed tool directory that the lock (or record) does not include."""
    problems: list[str] = []
    for tool_id in ALLOWED_TOOL_IDS:
        if tool_id in locked_ids:
            continue
        path = Path(stage) / TOOLS_DIRECTORY / tool_id
        if path.exists() or path.is_symlink():
            problems.append(f"stale tools/{tool_id} is not part of the locked set; "
                            "remove it or list it in the lock once redistribution is licensed")
    return problems


def _record_problems(stage: Path, tools: list[dict]) -> list[str]:
    """Compare the staged record against the lock-derived tool identities.

    The record id set must equal the lock id set exactly, so a historical
    Bandizip record can never ride along with an UnRAR-only lock.
    """
    path = Path(stage) / TOOLS_DIRECTORY / RECORD_NAME
    if not path.is_file():
        return [f"missing {TOOLS_DIRECTORY}/{RECORD_NAME}"]
    try:
        problems, recorded_tools = _record_tools(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{TOOLS_DIRECTORY}/{RECORD_NAME} is not valid JSON: {exc}"]
    if problems:
        return problems
    recorded = {tool["id"]: tool for tool in recorded_tools}
    problems = []
    expected_ids = {tool["id"] for tool in tools}
    if set(recorded) != expected_ids:
        problems.append(f"{TOOLS_DIRECTORY}/{RECORD_NAME} records {sorted(recorded)} "
                        f"but the lock lists {sorted(expected_ids)}")
    for tool in tools:
        entry = recorded.get(tool["id"])
        if entry is None:
            problems.append(f"{TOOLS_DIRECTORY}/{RECORD_NAME} does not record {tool['id']}")
            continue
        if str(entry.get("version")) != str(tool["version"]):
            problems.append(f"{TOOLS_DIRECTORY}/{RECORD_NAME} {tool['id']} version {entry.get('version')!r} != {tool['version']!r}")
        if str(entry.get("sha256", "")).lower() != tool["sha256"]:
            problems.append(f"{TOOLS_DIRECTORY}/{RECORD_NAME} {tool['id']} archive sha256 does not match the lock")
        expected = {item["path"].lower(): item["sha256"] for item in tool["files"]}
        actual = {item["path"].lower(): item["sha256"] for item in entry["files"]}
        if expected != actual:
            problems.append(f"{TOOLS_DIRECTORY}/{RECORD_NAME} {tool['id']} files do not match the lock")
        if (entry.get("vendored_archive") or None) != (tool.get("vendored_archive") or None):
            problems.append(f"{TOOLS_DIRECTORY}/{RECORD_NAME} {tool['id']} vendored_archive does not match the lock")
    return problems


def staged_tool_problems(stage: Path) -> list[str]:
    """Validate the staged fixed tools from the record alone (no lock, no network)."""
    stage = Path(stage)
    path = stage / TOOLS_DIRECTORY / RECORD_NAME
    if not path.is_file():
        return [f"missing {TOOLS_DIRECTORY}/{RECORD_NAME} (fixed UnRAR staging record)"]
    try:
        problems, tools = _record_tools(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{TOOLS_DIRECTORY}/{RECORD_NAME} is not valid JSON: {exc}"]
    if problems:
        return problems
    collected: list[str] = []
    for tool in tools:
        collected.extend(_stage_problems(tool, stage / TOOLS_DIRECTORY / tool["id"]))
    collected.extend(_stale_directory_problems(stage, {tool["id"] for tool in tools}))
    return list(dict.fromkeys(collected))


def validate_fixed_tools(repo: Path, stage: Path) -> None:
    """Fail unless the staged tools and record match the tracked lock exactly."""
    lock = load_lock(repo)
    tools = lock_tools(lock)
    problems = list(_locked_stage_problems(stage, tools))
    for tool in tools:
        if tool.get("vendored_archive"):
            problems.extend(_vendored_problems(tool, repo))
    if problems:
        raise RuntimeError("Fixed desktop tools are incomplete: " + "; ".join(problems))


def _locked_stage_problems(stage: Path, tools: list[dict]) -> list[str]:
    """Stage problems for a lock tool set: files, record identity, stale dirs."""
    problems: list[str] = []
    for tool in tools:
        problems.extend(_stage_problems(tool, Path(stage) / TOOLS_DIRECTORY / tool["id"]))
    problems.extend(_record_problems(Path(stage), tools))
    problems.extend(_stale_directory_problems(Path(stage), {tool["id"] for tool in tools}))
    return list(dict.fromkeys(problems))


def write_record(stage: Path, tools: list[dict]) -> None:
    """Atomically record the locked tool identities inside the staged engine."""
    directory = Path(stage) / TOOLS_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "platform": "windows-x64",
        "tools": [{
            "id": tool["id"],
            "name": tool["name"],
            "version": tool["version"],
            "url": tool["url"],
            "sha256": tool["sha256"],
            "archive_format": tool["archive_format"],
            "vendored_archive": tool.get("vendored_archive"),
            "files": [{"path": item["path"], "sha256": item["sha256"]} for item in tool["files"]],
            "license_files": list(tool["license_files"]),
        } for tool in tools],
    }
    target = directory / RECORD_NAME
    handle, temporary = tempfile.mkstemp(prefix=".fixed-tools-", suffix=".tmp", dir=directory)
    os.close(handle)
    try:
        Path(temporary).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------- #
# Archive acquisition / extraction
# --------------------------------------------------------------------------- #
def _archive_name(tool: dict) -> str:
    """Cache file name: prefer the vendored snapshot name, then the url basename."""
    vendored = tool.get("vendored_archive")
    if vendored:
        name = Path(vendored).name
        if name and _safe_relative_path(name) is not None:
            return name
    name = Path(urllib.parse.urlsplit(tool["url"]).path).name
    if name and _safe_relative_path(name) is not None:
        return name
    suffix = "zip" if tool["archive_format"] == "zip" else "exe"
    return f"{tool['id']}-{tool['version']}.{suffix}"


def resolve_seven_zip(repo: Path, explicit: Path | None) -> Path:
    """Return a redistributable 7-Zip binary for unpacking 7z-sfx dependencies."""
    if explicit is not None:
        tool = Path(explicit)
        if not tool.is_file():
            raise RuntimeError(f"--seven-zip does not exist: {tool}")
        return tool
    # Imported lazily: stage_desktop_engine imports this module at load time.
    from stage_desktop_engine import find_seven_zip

    return find_seven_zip(Path(repo), None)


def _download(url: str, destination: Path, tool_id: str, attempts: int = 2) -> None:
    """Download ``url`` at most twice; a redirect must stay on the official hosts.

    Honours ``*_proxy`` environment variables. An off-host redirect is final.
    """
    last: Exception | None = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                final_host = urllib.parse.urlsplit(response.geturl()).hostname or ""
                if not _host_is_official(tool_id, final_host):
                    raise OfficialHostError(f"{url} resolved to non-official host {final_host!r}")
                if response.status != 200:
                    last = RuntimeError(f"HTTP {response.status}")
                    continue
                destination.write_bytes(response.read())
            if destination.stat().st_size > 0:
                return
            last = RuntimeError("empty response body")
        except OfficialHostError:
            raise
        except Exception as exc:  # noqa: BLE001 - network flakiness must not abort early
            last = exc
    raise RuntimeError(f"could not download {url}: {last}")


def _cached_archive(tool: dict, cache_root: Path) -> Path | None:
    directory = Path(cache_root) / tool["id"] / tool["version"]
    if not directory.is_dir():
        return None
    for candidate in sorted(directory.rglob("*")):
        try:
            if candidate.is_file() and not candidate.is_symlink() and sha256_file(candidate) == tool["sha256"]:
                return candidate
        except OSError:
            continue
    return None


def _vendored_problems(tool: dict, repo: Path) -> list[str]:
    """Check the optional vendored snapshot exists as a real file with the locked hash.

    ``_file_problems`` walks every path component, so a symlinked ancestor of the
    repo-relative path is rejected, not only a symlinked leaf.
    """
    relative = tool.get("vendored_archive")
    if not relative:
        return []
    found = _file_problems(Path(repo), relative)
    if found:
        problems = [f"vendored archive {relative}: {item}" for item in found]
        if any("missing" in item for item in found):
            problems.append(f"refusing to fall back to {tool['url']}")
        return problems
    actual = sha256_file(Path(repo) / relative)
    if actual != tool["sha256"]:
        return [f"vendored archive {relative} sha256 {actual} != locked {tool['sha256']}"]
    return []


def resolve_vendored_archive(tool: dict, repo: Path) -> Path | None:
    """Return the locked repo-relative snapshot, or raise; never a network fallback."""
    problems = _vendored_problems(tool, repo)
    if problems:
        raise RuntimeError("; ".join(problems))
    relative = tool.get("vendored_archive")
    return Path(repo) / relative if relative else None


def ensure_cached_archive(tool: dict, repo: Path, *, fetch: bool, vendored: Path | None = None) -> Path:
    """Return the locked archive from the snapshot or cache; download only with ``fetch``."""
    cache_root = Path(repo) / CACHE_RELATIVE
    cached = _cached_archive(tool, cache_root)
    if vendored is not None:
        if cached is not None:
            return cached
        directory = cache_root / tool["id"] / tool["version"]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / _archive_name(tool)
        handle, temporary = tempfile.mkstemp(prefix=".vendored-", suffix=".tmp", dir=directory)
        os.close(handle)
        staging = Path(temporary)
        try:
            shutil.copy2(vendored, staging)
            os.replace(staging, target)
        except BaseException:
            staging.unlink(missing_ok=True)
            raise
        return target
    if cached is not None:
        return cached
    if not fetch:
        raise RuntimeError(
            f"no cached archive for {tool['id']} {tool['version']} under "
            f"{cache_root / tool['id'] / tool['version']}; the lock pins {tool['url']} "
            "(re-run with fetch enabled to download it)"
        )
    directory = cache_root / tool["id"] / tool["version"]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / _archive_name(tool)
    handle, temporary = tempfile.mkstemp(prefix=".download-", suffix=".tmp", dir=directory)
    os.close(handle)
    staging = Path(temporary)
    try:
        _download(tool["url"], staging, tool["id"])
        actual = sha256_file(staging)
        if actual != tool["sha256"]:
            # A hash mismatch is final: never silently retry the same version.
            raise RuntimeError(f"downloaded {tool['url']} sha256 {actual} != locked {tool['sha256']}")
        os.replace(staging, target)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    return target


def _extract_zip(tool: dict, archive: Path, destination: Path) -> None:
    """Safely unpack only the approved files from a locked zip archive."""
    approved = {entry["path"].lower(): entry for entry in tool["files"]}
    with zipfile.ZipFile(archive) as handle:
        members: dict[str, str] = {}
        for info in handle.infolist():
            if info.is_dir():
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise RuntimeError(f"{archive.name} contains symlink member {info.filename!r}")
            if mode not in (0, 0o100000) and not stat.S_ISREG(mode):
                raise RuntimeError(f"{archive.name} member {info.filename!r} is not a regular file")
            normalized = _safe_relative_path(info.filename)
            if normalized is None:
                raise RuntimeError(f"{archive.name} contains unsafe member {info.filename!r}")
            key = normalized.lower()
            if key in members:
                raise RuntimeError(f"{archive.name} contains case-conflicting member {info.filename!r}")
            members[key] = info.filename
        missing = [entry["path"] for entry in tool["files"] if entry["path"].lower() not in members]
        if missing:
            raise RuntimeError(f"{archive.name} is missing approved files: {missing}")
        for key, entry in approved.items():
            data = handle.read(members[key])
            actual = hashlib.sha256(data).hexdigest()
            if actual != entry["sha256"]:
                raise RuntimeError(f"{archive.name} member {entry['path']!r} sha256 {actual} != {entry['sha256']}")
            target = destination / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def _extract_7z(tool: dict, archive: Path, destination: Path, seven_zip: Path | None, repo: Path) -> None:
    """Unpack a locked 7z-sfx package with trusted 7-Zip; never run the SFX."""
    binary = resolve_seven_zip(repo, seven_zip)
    completed = subprocess.run(
        [str(binary), "x", "-y", f"-o{destination}", str(archive)],
        capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{binary} could not unpack {archive.name} (exit {completed.returncode}): "
            f"{(completed.stderr or completed.stdout).strip()[:400]}"
        )
    missing = [entry["path"] for entry in tool["files"] if not (destination / entry["path"]).is_file()]
    if missing:
        raise RuntimeError(f"{archive.name} is missing approved files after extraction: {missing}")


def _publish_tool(tool: dict, archive: Path, stage: Path, temp_root: Path, *, seven_zip: Path | None, repo: Path) -> None:
    """Unpack into a unique temp dir and publish only after full verification."""
    target = Path(stage) / TOOLS_DIRECTORY / tool["id"]
    if not _stage_problems(tool, target):
        return
    extract_dir = temp_root / f"{tool['id']}-extract"
    extract_dir.mkdir(parents=True, exist_ok=True)
    if tool["archive_format"] == "zip":
        _extract_zip(tool, archive, extract_dir)
    else:
        _extract_7z(tool, archive, extract_dir, seven_zip, repo)
    candidate = temp_root / f"{tool['id']}-candidate"
    for entry in tool["files"]:
        source = extract_dir / entry["path"]
        found = _file_problems(extract_dir, entry["path"])
        if found:
            raise RuntimeError(f"{archive.name}: " + "; ".join(found))
        if sha256_file(source) != entry["sha256"]:
            raise RuntimeError(f"{archive.name} member {entry['path']!r} does not match the locked sha256")
        destination = candidate / entry["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    if _stage_problems(tool, candidate):
        raise RuntimeError(f"staged {tool['id']} did not verify before publishing")
    target.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if target.exists() or target.is_symlink():
        backup_root = repo / BACKUP_RELATIVE
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = backup_root / f"{tool['id']}-{time.strftime('%Y%m%d-%H%M%S')}"
        suffix = 1
        while backup.exists():
            backup = backup_root / f"{tool['id']}-{time.strftime('%Y%m%d-%H%M%S')}-{suffix}"
            suffix += 1
        shutil.move(str(target), str(backup))
    try:
        shutil.move(str(candidate), str(target))
    except BaseException:
        if backup is not None:
            shutil.move(str(backup), str(target))
        raise


def _unique_backup(backup_root: Path, prefix: str) -> Path:
    """Return a fresh, non-existing backup path under the ignored backup root."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = backup_root / f"{prefix}-{stamp}"
    suffix = 1
    while backup.exists():
        backup = backup_root / f"{prefix}-{stamp}-{suffix}"
        suffix += 1
    return backup


def _archive_stale_tool_dirs(stage: Path, repo: Path, tools: list[dict]) -> None:
    """Move an allowed-but-unlocked tool dir (for example a stale Bandizip) aside."""
    locked = {tool["id"] for tool in tools}
    for tool_id in ALLOWED_TOOL_IDS:
        if tool_id in locked:
            continue
        path = Path(stage) / TOOLS_DIRECTORY / tool_id
        if not (path.exists() or path.is_symlink()):
            continue
        backup_root = repo / BACKUP_RELATIVE
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(_unique_backup(backup_root, f"{tool_id}-stale")))


def stage_fixed_tools(repo: Path, stage: Path, seven_zip: Path | None = None, fetch: bool = False) -> None:
    """Stage the locked tools, reusing the vendored snapshot or cache unless ``fetch``."""
    repo = Path(repo)
    stage = Path(stage)
    if not stage.is_dir():
        raise RuntimeError(f"engine stage does not exist: {stage}")
    lock = load_lock(repo)
    tools = lock_tools(lock)
    # Verify a declared vendored snapshot even when the stage already matches: a
    # missing or changed Git snapshot is a hard failure, never a url fallback.
    vendored = {tool["id"]: resolve_vendored_archive(tool, repo)
                for tool in tools if tool.get("vendored_archive")}
    locked_ids = {tool["id"] for tool in tools}
    stale = [tool_id for tool_id in ALLOWED_TOOL_IDS
             if tool_id not in locked_ids
             and ((stage / TOOLS_DIRECTORY / tool_id).exists()
                  or (stage / TOOLS_DIRECTORY / tool_id).is_symlink())]
    if not stale and not _locked_stage_problems(stage, tools):
        return  # already staged from the same lock state; do not touch cache or stage
    # Acquire every locked archive first, so a transient download/cache failure
    # cannot leave the stage half-updated.
    archives = [(tool, ensure_cached_archive(tool, repo, fetch=fetch, vendored=vendored.get(tool["id"])))
                for tool in tools]
    # Keep interrupted staging work outside the engine resource tree so bundling
    # cannot copy a partially extracted tool; the sibling remains on the same disk.
    temp_root = Path(tempfile.mkdtemp(prefix=".desktop-tool-staging-", dir=stage.parent))
    try:
        for tool, archive in archives:
            _publish_tool(tool, archive, stage, temp_root, seven_zip=seven_zip, repo=repo)
        write_record(stage, tools)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
    _archive_stale_tool_dirs(stage, repo, tools)
    validate_fixed_tools(repo, stage)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_DEFAULT)
    parser.add_argument("--stage", type=Path, default=None,
                        help="engine stage (default <repo>/apps/desktop/src-tauri/resources/engine)")
    parser.add_argument("--seven-zip", type=Path, default=None, help="path to a trusted 7z.exe/7zz")
    parser.add_argument("--fetch-tools", action="store_true",
                        help="download the locked archives from their pinned https URLs when the cache is empty")
    parser.add_argument("--validate-only", action="store_true",
                        help="validate the staged tools without touching the cache or stage")
    args = parser.parse_args()
    repo = args.repo.resolve()
    stage = (args.stage or repo / "apps/desktop/src-tauri/resources/engine").resolve()
    if args.validate_only:
        validate_fixed_tools(repo, stage)
        print(f"Fixed tools validated: {stage / TOOLS_DIRECTORY}")
        return 0
    stage_fixed_tools(repo, stage, seven_zip=args.seven_zip, fetch=args.fetch_tools)
    print(f"Fixed tools staged: {stage / TOOLS_DIRECTORY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
