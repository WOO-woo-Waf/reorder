from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _import_script_module(repo: Path, name: str):
    scripts_dir = str(Path(repo) / "scripts")
    added = scripts_dir not in sys.path
    if added:
        sys.path.insert(0, scripts_dir)
    try:
        return importlib.import_module(name)
    finally:
        if added:
            try:
                sys.path.remove(scripts_dir)
            except ValueError:
                pass


def find_locked_seven_zip(repo: Path) -> Path:
    """Resolve the repository's pinned 7-Zip through its staging validator."""
    module = _import_script_module(repo, "stage_desktop_engine")
    repo = Path(repo)
    executable = Path(module.find_seven_zip(repo, None))
    entry = module.load_seven_zip_lock(repo)
    bundle = repo / entry["vendored_directory"]
    for item in entry["files"]:
        path = bundle / item["path"]
        if not path.is_file():
            raise RuntimeError(f"locked 7-Zip file {path} is missing")
        actual = module.stage_desktop_tools.sha256_file(path)
        if actual != item["sha256"]:
            raise RuntimeError(f"locked 7-Zip file {path} sha256 {actual} != {item['sha256']}")
    return executable


def locked_seven_zip_path(repo: Path) -> Path:
    """Return the configured path managed by the 7-Zip lock without checking files."""
    module = _import_script_module(Path(repo), "stage_desktop_engine")
    repo = Path(repo)
    entry = module.load_seven_zip_lock(repo)
    executable = next(
        (item for item in entry["files"] if item["path"].lower() in module.SEVEN_ZIP_EXECUTABLES),
        entry["files"][0],
    )
    return repo / entry["vendored_directory"] / executable["path"]


def is_managed_cli_tool_path(repo: Path, tool_id: str, candidate: Path | None) -> bool:
    """Whether a configured optional executable belongs to the CLI fixed-tool stage."""
    if candidate is None:
        return False
    root = (Path(repo) / "runtime" / "cli-tools" / "tools" / tool_id).resolve()
    try:
        Path(candidate).resolve().relative_to(root)
        return True
    except ValueError:
        return False


def stage_locked_cli_tools(repo: Path, destination: Path, seven_zip: Path | None) -> None:
    """Expand the tracked UnRAR/Bandizip snapshots into an isolated CLI stage."""
    module = _import_script_module(repo, "stage_desktop_tools")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    module.stage_fixed_tools(Path(repo), destination, seven_zip=seven_zip, fetch=False)
