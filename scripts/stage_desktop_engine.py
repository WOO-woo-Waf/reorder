"""Build on the target OS.

Never freeze developer config or the raw password library into the binary: the
builtin password/keyword libraries ship as locked, hashed data files staged under
``engine/defaults`` (never embedded in source, SQLite or stored credentials).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import stage_desktop_tools  # noqa: E402  (sibling build script; scripts/ is on sys.path)
import stage_desktop_defaults  # noqa: E402  (sibling build script; scripts/ is on sys.path)

# Files the frozen engine needs at runtime; a plain directory is not enough.
CORE_INTERNAL_FILES = ("base_library.zip", "VCRUNTIME140.dll", "_sqlite3.pyd")
FROZEN_ENGINE_ENTRY = "reorder-engine.exe"


def _seven_zip_problems(tools: Path) -> list[str]:
    """Report what the staged 7-Zip bundle is missing (empty means complete)."""
    problems: list[str] = []
    has_7z = (tools / "7z.exe").is_file() and (tools / "7z.dll").is_file()
    has_7zz = (tools / "7zz.exe").is_file()
    if not (has_7z or has_7zz):
        problems.append("tools/7zip needs 7z.exe + 7z.dll (or a standalone 7zz.exe)")
    if not (tools / "License.txt").is_file():
        problems.append("tools/7zip/License.txt is required for redistribution")
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
    problems.extend(_seven_zip_problems(stage / "tools/7zip"))
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
        # Identity of the read-only builtin word libraries (version/count/sha256 only).
        "builtin_defaults": stage_desktop_defaults.catalog_summary(stage),
    }
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def find_seven_zip(repo: Path, explicit: Path | None) -> Path:
    """Return the redistributable 7-Zip executable, or raise (never stage a bare dir)."""
    if explicit is not None:
        tool = Path(explicit)
        if not tool.is_file():
            raise RuntimeError(f"--seven-zip does not exist: {tool}")
        return tool
    for name in ("7z.exe", "7zz.exe", "7zz"):
        candidate = next((p for p in sorted((repo / "tools").rglob(name)) if p.is_file()), None)
        if candidate is not None:
            return candidate
    raise RuntimeError("A redistributable 7-Zip (7z.exe + 7z.dll, or 7zz.exe) is required. "
                       "Pass --seven-zip or place it under tools/.")


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
    tool = find_seven_zip(repo, args.seven_zip)
    tools = stage / "tools/7zip"
    tools.mkdir(parents=True, exist_ok=True)
    for name in (tool.name, "7z.dll", "License.txt", "readme.txt"):
        source = tool.parent / name
        if source.is_file():
            shutil.copy2(source, tools / source.name)
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
