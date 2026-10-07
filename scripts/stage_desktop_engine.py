"""Build on the target OS. Never package developer config or password libraries."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


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


def validate_staged_engine(stage: Path) -> None:
    """Fail unless the staged engine holds the real runtime files the package needs."""
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
    if problems:
        raise RuntimeError("Staged engine is incomplete: " + "; ".join(problems))


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
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    stage = repo / "apps/desktop/src-tauri/resources/engine"
    if args.validate_only:
        validate_staged_engine(stage)
        print(f"Engine stage validated: {stage}")
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
    manifest = {"python": sys.version.split()[0], "platform": sys.platform,
        "tool": tool.name, "excluded": ["passwords.txt", "config.json", "restoreAB.exe", "Bandizip", "UnRAR"]}
    (stage / "build-info.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # Reflect the validation before reporting success.
    validate_staged_engine(stage)
    print(f"Engine staged: {stage}")


if __name__ == "__main__":
    main()
