"""Build on the target OS.

Never freeze developer config or the raw password library into the binary: the
builtin password/keyword libraries ship as locked, hashed data files staged under
``engine/defaults`` (never embedded in source, SQLite or stored credentials).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

import stage_desktop_tools  # noqa: E402  (sibling build script; scripts/ is on sys.path)
import stage_desktop_defaults  # noqa: E402  (sibling build script; scripts/ is on sys.path)

# Files the frozen engine needs at runtime; a plain directory is not enough.
CORE_INTERNAL_FILES = ("base_library.zip", "VCRUNTIME140.dll", "_sqlite3.pyd")
FROZEN_ENGINE_ENTRY = "reorder-engine.exe"

SEVEN_ZIP_LOCK_RELATIVE = Path("scripts/desktop-sevenzip.lock.json")
SEVEN_ZIP_IDENTITY_NAME = "identity.json"
# The pinned redistributable bundle is exactly these four files: no GUI binaries
# (7zFM.exe/7zG.exe) and no extra DLLs (7-zip.dll/7zxa.dll) may be present.
SEVEN_ZIP_REQUIRED_FILES = ("7z.exe", "7z.dll", "License.txt", "readme.txt")
SEVEN_ZIP_EXECUTABLES = ("7z.exe", "7zz.exe", "7zz")
# Official 7-Zip distribution hosts; the pinned archive is the ip7z GitHub
# release that https://www.7-zip.org/download.html points at.
SEVEN_ZIP_OFFICIAL_HOSTS = frozenset({"github.com", "www.github.com", "7-zip.org", "www.7-zip.org"})

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_BAD_SEGMENT = re.compile(r'[<>:"|?*\x00-\x1f]')


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256.match(value.lower()))


def _safe_lock_path(value: object) -> str | None:
    """Return a normalized repo-relative path, or ``None`` when it is unsafe."""
    if not isinstance(value, str):
        return None
    text = value.strip().replace("\\", "/")
    if not text or text.startswith("/") or _DRIVE_PREFIX.match(text):
        return None
    segments: list[str] = []
    for segment in text.split("/"):
        if segment in ("", ".", "..") or _BAD_SEGMENT.search(segment):
            return None
        if segment != segment.rstrip(" ."):
            return None
        segments.append(segment)
    return "/".join(segments)


def _is_official_seven_zip_url(value: object, version: object) -> bool:
    if not isinstance(value, str) or not value.startswith("https://"):
        return False
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.fragment:
        return False
    if any(segment.lower() in {"latest", "current"} for segment in parsed.path.split("/")):
        return False
    if parsed.hostname is None or parsed.hostname.lower() not in SEVEN_ZIP_OFFICIAL_HOSTS:
        return False
    if isinstance(version, str) and version and version not in parsed.path:
        return False
    return True


def _seven_zip_entry_problems(entry: object) -> list[str]:
    """Return every reason the pinned 7-Zip identity is unusable (empty is valid)."""
    if not isinstance(entry, dict):
        return ["seven_zip must be a JSON object"]
    problems: list[str] = []
    for key in ("name", "version"):
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"seven_zip.{key} must be a non-empty string")
    version = entry.get("version")
    if isinstance(version, str) and version.strip() and not re.fullmatch(r"\d+(?:\.\d+)*", version):
        problems.append(f"seven_zip.version must be a numeric released version, got {version!r}")
    if not _is_official_seven_zip_url(entry.get("url"), version):
        problems.append("seven_zip.url must be a pinned https URL on the official 7-Zip source "
                        f"({sorted(SEVEN_ZIP_OFFICIAL_HOSTS)}) that contains the locked version")
    if not _is_sha256(entry.get("sha256")):
        problems.append("seven_zip.sha256 must be the 64 character sha256 of the official source archive")
    vendored = entry.get("vendored_directory")
    normalized_vendored = _safe_lock_path(vendored)
    if normalized_vendored is None or normalized_vendored != vendored:
        problems.append(f"seven_zip.vendored_directory must be a safe repo-relative path, got {vendored!r}")
    elif isinstance(version, str) and version.strip() and Path(normalized_vendored).name != version:
        problems.append(f"seven_zip.vendored_directory must end with the locked version {version!r}, got {vendored!r}")
    files = entry.get("files")
    listed: dict[str, str] = {}
    if not isinstance(files, list) or not files:
        problems.append("seven_zip.files must be a non-empty array")
    else:
        for index, item in enumerate(files):
            prefix = f"seven_zip.files[{index}]"
            if not isinstance(item, dict):
                problems.append(f"{prefix} must be a JSON object")
                continue
            raw_path = item.get("path")
            normalized = _safe_lock_path(raw_path)
            if normalized is None or "/" in normalized:
                problems.append(f"{prefix}.path must be a flat safe file name, got {raw_path!r}")
            else:
                key = normalized.lower()
                if key in listed:
                    problems.append(f"{prefix}.path {normalized!r} duplicates {listed[key]!r}")
                else:
                    listed[key] = normalized
            if not _is_sha256(item.get("sha256")):
                problems.append(f"{prefix}.sha256 must be a 64 character sha256 hex digest")
    required = {name.lower() for name in SEVEN_ZIP_REQUIRED_FILES}
    for name in SEVEN_ZIP_REQUIRED_FILES:
        if name.lower() not in listed:
            problems.append(f"seven_zip.files must include {name!r}")
    unexpected = sorted(shown for key, shown in listed.items() if key not in required)
    if unexpected:
        problems.append(f"seven_zip.files must contain only {list(SEVEN_ZIP_REQUIRED_FILES)}, found {unexpected}")
    licenses = entry.get("license_files")
    if licenses is not None:
        if not isinstance(licenses, list) or not licenses:
            problems.append("seven_zip.license_files must be a non-empty array when present")
        else:
            for position, raw_path in enumerate(licenses):
                normalized = _safe_lock_path(raw_path)
                if normalized is None or normalized.lower() not in listed:
                    problems.append(f"seven_zip.license_files[{position}] ({raw_path!r}) is not listed in files")
    return problems


def _seven_zip_lock_problems(lock: object) -> list[str]:
    """Return every reason the 7-Zip lock file is not usable (empty means valid)."""
    if not isinstance(lock, dict):
        return ["seven-zip lock root must be a JSON object"]
    problems: list[str] = []
    if lock.get("schema_version") != 1:
        problems.append("schema_version must be the integer 1")
    if lock.get("platform") != "windows-x64":
        problems.append("platform must be 'windows-x64'")
    problems.extend(_seven_zip_entry_problems(lock.get("seven_zip")))
    return problems


def load_seven_zip_lock(repo: Path) -> dict:
    """Read and validate the tracked 7-Zip lock; raise with every problem found."""
    path = Path(repo) / SEVEN_ZIP_LOCK_RELATIVE
    if not path.is_file():
        raise RuntimeError(
            f"missing tracked lock {path}; 7-Zip staging needs the pinned lock and "
            "must never scan for a tool or reuse a 7-Zip found under tools/"
        )
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid seven-zip lock {path}: {exc}")
    problems = _seven_zip_lock_problems(lock)
    if problems:
        raise RuntimeError(f"invalid seven-zip lock {path}: " + "; ".join(problems))
    return lock["seven_zip"]


def seven_zip_identity(directory: Path) -> dict:
    """Read the staged 7-Zip identity next to the bundle; raise when it is unusable."""
    path = Path(directory) / SEVEN_ZIP_IDENTITY_NAME
    if not path.is_file():
        raise RuntimeError(f"tools/7zip/{SEVEN_ZIP_IDENTITY_NAME} is required to validate a "
                           "stage without the repository lock")
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"tools/7zip/{SEVEN_ZIP_IDENTITY_NAME} is unreadable: {exc}")
    problems = _seven_zip_entry_problems(entry)
    if problems:
        raise RuntimeError(f"tools/7zip/{SEVEN_ZIP_IDENTITY_NAME} is invalid: " + "; ".join(problems))
    return entry


def primary_seven_zip_identity(stage: Path) -> dict | None:
    """Summarize the staged 7-Zip identity for build-info, or ``None`` when absent."""
    directory = Path(stage) / "tools/7zip"
    try:
        entry = seven_zip_identity(directory)
    except RuntimeError:
        return None
    return {
        "name": entry["name"],
        "version": entry["version"],
        "url": entry["url"],
        "sha256": entry["sha256"],
        "vendored_directory": entry["vendored_directory"],
        "files": [{"path": item["path"], "sha256": item["sha256"]} for item in entry["files"]],
    }


def _seven_zip_stage_problems(stage: Path, repo: Path | None) -> list[str]:
    """Verify the staged 7-Zip bundle against the tracked lock or its staged identity."""
    directory = Path(stage) / "tools/7zip"
    if not directory.is_dir():
        return ["missing tools/7zip directory"]
    if repo is not None:
        try:
            entry = load_seven_zip_lock(Path(repo))
        except RuntimeError as exc:
            return [f"seven-zip lock: {exc}"]
    else:
        try:
            entry = seven_zip_identity(directory)
        except RuntimeError as exc:
            return [str(exc)]
    problems: list[str] = []
    for item in entry["files"]:
        path = directory / item["path"]
        if path.is_symlink():
            problems.append(f"tools/7zip/{item['path']} is a symlink")
            continue
        if not path.is_file():
            problems.append(f"missing tools/7zip/{item['path']}")
            continue
        actual = stage_desktop_tools.sha256_file(path)
        if actual != item["sha256"]:
            problems.append(f"tools/7zip/{item['path']} sha256 {actual} != {item['sha256']} "
                            f"(locked 7-Zip {entry['version']})")
    expected = {item["path"].lower() for item in entry["files"]}
    expected.add(SEVEN_ZIP_IDENTITY_NAME.lower())
    for child in sorted(directory.iterdir()):
        if child.name.lower() not in expected:
            problems.append(f"tools/7zip/{child.name} is not part of the pinned 7-Zip bundle")
    return problems


def validate_staged_engine(stage: Path, repo: Path | None = None) -> None:
    """Fail unless the staged engine holds the real runtime files the package needs.

    The locked extraction tools and the builtin word libraries are validated too:
    both UnRAR and Bandizip are mandatory for this release. Validation checks the
    staged records always, and ``scripts/desktop-tools.lock.json`` plus
    ``scripts/desktop-defaults.lock.json`` when the repository root is known. A
    package may never report a missing tool or library as success.
    """
    stage = Path(stage)
    problems = [f"missing {name}" for name in (FROZEN_ENGINE_ENTRY, "build-info.json", "tools/apate.py")
                if not (stage / name).is_file()]
    internal = stage / "_internal"
    if not internal.is_dir():
        problems.append("missing _internal/ directory")
    else:
        problems.extend(f"missing _internal/{name}" for name in CORE_INTERNAL_FILES
                        if not (internal / name).is_file())
        if not any(path.is_file() and path.suffix.lower() == ".dll"
                   and path.name.lower().startswith("python3") for path in internal.iterdir()):
            problems.append("missing _internal/python3*.dll")
        if not (internal / "pydantic_core").exists():
            problems.append("missing _internal/pydantic_core")
    problems.extend(_seven_zip_stage_problems(stage, repo))
    problems.extend(f"fixed tools: {item}" for item in stage_desktop_tools.staged_tool_problems(stage))
    problems.extend(f"builtin defaults: {item}" for item in stage_desktop_defaults.staged_default_problems(stage))
    if problems:
        raise RuntimeError("Staged engine is incomplete: " + "; ".join(problems))
    if repo is not None:
        stage_desktop_tools.validate_fixed_tools(repo, stage)
        stage_desktop_defaults.validate_defaults(repo, stage)


def bundled_tools(stage: Path) -> list[dict]:
    """Summarize the locked tools for build-info from the staged record."""
    record = json.loads((stage / "tools/fixed-tools.json").read_text(encoding="utf-8"))
    return [{"id": tool["id"], "name": tool["name"], "version": tool["version"], "sha256": tool["sha256"]}
            for tool in record["tools"]]


def write_build_info(stage: Path, *, tool_name: str) -> None:
    """Record the frozen engine identity, the bundled locked tools and the catalog."""
    path = stage / "build-info.json"
    previous: dict = {}
    if path.is_file():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    manifest = {
        "python": previous.get("python", sys.version.split()[0]),
        "platform": previous.get("platform", sys.platform),
        "tool": previous.get("tool", tool_name),
        # Personal/secret inputs stay out of the frozen engine. Both locked tools are
        # bundled with their licence texts, so no tool id is excluded here.
        "excluded": ["passwords.txt", "config.json", "restoreAB.exe"],
        "bundled_tools": bundled_tools(stage),
        # Identity of the pinned 7-Zip bundle (tracked lock plus the staged files).
        # Kept separate from bundled_tools, whose two-tool contract is unchanged.
        "primary_seven_zip": primary_seven_zip_identity(stage),
        # Identity of the read-only builtin word libraries (version/count/sha256 only).
        "builtin_defaults": stage_desktop_defaults.catalog_summary(stage),
    }
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def find_seven_zip(repo: Path, explicit: Path | None) -> Path:
    """Return the pinned redistributable 7-Zip; never a scan or a mismatched package.

    The tracked scripts/desktop-sevenzip.lock.json is authoritative. The default
    executable comes from its vendored_directory (no directory scan), and an
    explicit --seven-zip must match the locked executable byte for byte, so a
    mixed-version or half-swapped package can never be staged.
    """
    entry = load_seven_zip_lock(Path(repo))
    executable = next((item for item in entry["files"] if item["path"].lower() in SEVEN_ZIP_EXECUTABLES),
                      entry["files"][0])
    if explicit is not None:
        tool = Path(explicit)
        if not tool.is_file():
            raise RuntimeError(f"--seven-zip does not exist: {tool}")
        actual = stage_desktop_tools.sha256_file(tool)
        if actual != executable["sha256"]:
            raise RuntimeError(
                f"--seven-zip {tool} is not the locked 7-Zip {entry['version']} "
                f"({executable['path']} sha256 {actual} != {executable['sha256']})"
            )
        return tool
    directory = Path(repo) / entry["vendored_directory"]
    if not directory.is_dir():
        raise RuntimeError(f"locked 7-Zip directory {directory} is missing; "
                           f"the pinned {entry['version']} bundle is required")
    tool = directory / executable["path"]
    if not tool.is_file():
        raise RuntimeError(f"locked 7-Zip executable {tool} is missing")
    actual = stage_desktop_tools.sha256_file(tool)
    if actual != executable["sha256"]:
        raise RuntimeError(f"locked 7-Zip executable {tool} sha256 {actual} != {executable['sha256']}")
    return tool


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seven-zip", type=Path, help="Path to redistributable 7z.exe/7zz")
    parser.add_argument("--validate-only", action="store_true",
                        help="validate the existing staged engine instead of freezing a new one")
    parser.add_argument("--tools-only", action="store_true",
                        help="only (re)stage the locked UnRAR/Bandizip tools and build-info of an existing engine")
    parser.add_argument("--fetch-tools", action="store_true",
                        help="download the locked tool archives from their pinned https URLs when the cache is empty")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    stage = repo / "apps/desktop/src-tauri/resources/engine"
    if args.validate_only:
        validate_staged_engine(stage, repo)
        print(f"Engine stage validated: {stage}")
        return
    if args.tools_only:
        # Update only the fixed tools and build-info; the frozen engine stays as-is.
        stage_desktop_tools.stage_fixed_tools(repo, stage, seven_zip=args.seven_zip, fetch=args.fetch_tools)
        stage_desktop_defaults.stage_defaults(repo, stage)
        write_build_info(stage, tool_name="reorder-engine")
        validate_staged_engine(stage, repo)
        print(f"Fixed tools staged into existing engine: {stage}")
        return
    build = repo / "artifacts/desktop/engine-build"
    dist = repo / "artifacts/desktop/engine-dist"
    # A venv made from Conda does not inherit its DLL search directories.
    # Collect only the native libraries required by Python's standard modules.
    native = []
    for name in ("sqlite3.dll", "ffi.dll", "libexpat.dll", "LIBBZ2.dll", "liblzma.dll"):
        candidate = Path(sys.base_prefix) / "Library/bin" / name
        if candidate.is_file():
            native.extend(["--add-binary", str(candidate) + ";."])
    build.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console", *native,
        "--name", "reorder-engine", "--paths", str(repo / "src"),
        "--collect-submodules", "keyring", "--collect-submodules", "pyzipper",
        "--hidden-import", "keyring.backends.Windows", "--hidden-import", "keyring.backends.macOS",
        "--hidden-import", "keyring.backends.SecretService", "--hidden-import", "keyring.backends.libsecret",
        "--distpath", str(dist), "--workpath", str(build), "--specpath", str(build),
        str(repo / "scripts/desktop_engine_entry.py")]
    log = build / "freeze.log"
    print(f"Building Python engine; diagnostics: {log}", flush=True)
    with log.open("w", encoding="utf-8") as stream:
        subprocess.run(command, cwd=repo, stdout=stream, stderr=subprocess.STDOUT, check=True)
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(dist / "reorder-engine", stage)
    (stage / "tools").mkdir(exist_ok=True)
    shutil.copy2(repo / "tools/apate.py", stage / "tools/apate.py")
    entry = load_seven_zip_lock(repo)
    tool = find_seven_zip(repo, args.seven_zip)
    tools = stage / "tools/7zip"
    tools.mkdir(parents=True, exist_ok=True)
    for item in entry["files"]:
        source = tool.parent / item["path"]
        if not source.is_file():
            raise RuntimeError(f"7-Zip source {source} is missing the pinned file {item['path']}")
        shutil.copy2(source, tools / item["path"])
    (tools / SEVEN_ZIP_IDENTITY_NAME).write_text(json.dumps(entry, indent=2), encoding="utf-8")
    for name in ("LICENSE", "NOTICE"):
        if (repo / name).is_file():
            shutil.copy2(repo / name, stage / name)
    stage_desktop_tools.stage_fixed_tools(repo, stage, seven_zip=args.seven_zip, fetch=args.fetch_tools)
    stage_desktop_defaults.stage_defaults(repo, stage)
    write_build_info(stage, tool_name=tool.name)
    # Reflect the validation before reporting success.
    validate_staged_engine(stage, repo)
    print(f"Engine staged: {stage}")


if __name__ == "__main__":
    main()
