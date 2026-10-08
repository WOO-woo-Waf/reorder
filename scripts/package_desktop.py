"""Assemble a Windows desktop preview from explicit, already-built inputs.

Release naming comes from the Tauri product config (``productName``/``version``),
so the portable tree, EXE, ZIP and installer selection always follow the current
brand and release instead of a stale ``ReOrder`` 0.2.0 name. Previous portable
directories (for example an existing ``ReOrder-portable``) and their ``data/``
are never touched, and a stale ``*_0.2.0_*`` installer under ``bundle/nsis`` is
never copied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

# Reuse the same engine-resource selection the NSIS bundle uses.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stage_desktop_engine  # noqa: E402  (sibling build script)
import stage_desktop_resources  # noqa: E402  (sibling build script)

# The Rust binary the Tauri build produces; it is renamed to the product EXE.
HOST_BINARY = "reorder-desktop.exe"
TAURI_CONFIG_RELATIVE = Path("apps/desktop/src-tauri/tauri.conf.json")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_release_identity(repo: Path) -> dict:
    """Read the release naming from the Tauri product config."""
    config_path = Path(repo) / TAURI_CONFIG_RELATIVE
    config = json.loads(config_path.read_text(encoding="utf-8"))
    product, version = config.get("productName"), config.get("version")
    if not isinstance(product, str) or not product.strip():
        raise RuntimeError(f"{config_path} has no usable productName")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError(f"{config_path} has no usable version")
    return {
        "product": product,
        "version": version,
        "exe": f"{product}.exe",
        "package": f"{product}-{version}-windows-x64",
        "portable": f"{product}-portable",
    }


def expected_installers(product: str, version: str) -> list[str]:
    """The installer file names this release may ship (Tauri NSIS default scheme)."""
    return [f"{product}_{version}_x64-setup.exe"]


def select_current_installers(directory: Path, product: str, version: str) -> list[Path]:
    """Return only installers built for the current product and version.

    A stale installer from a previous release (for example ``ReOrder_0.2.0_...``)
    or a different product never matches and is therefore never copied.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    prefix = f"{product}_{version}"
    selected: list[Path] = []
    for path in sorted(directory.glob("*.exe")):
        if path.is_symlink() or not path.is_file():
            continue
        if path.name.startswith(prefix) and version in path.name:
            selected.append(path)
    return selected


def pick_portable_destination(output: Path, names: dict) -> Path:
    """Choose a portable directory, never replacing a tree a user may have run from."""
    destination = Path(output) / names["portable"]
    if destination.exists():
        destination = Path(output) / names["package"]
        if destination.exists():
            destination = Path(tempfile.mkdtemp(prefix=names["package"] + "-", dir=output))
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    repo = args.repo.resolve()
    names = load_release_identity(repo)
    version = names["version"]
    release = repo / "apps/desktop/src-tauri/target/release"
    host = release / HOST_BINARY
    engine = repo / "apps/desktop/src-tauri/resources/engine"
    if not host.is_file():
        raise RuntimeError(f"Incomplete desktop inputs: missing {host}")
    # Require the real staged engine files, not just directories.
    stage_desktop_engine.validate_staged_engine(engine, repo=repo)
    forbidden = {"passwords.txt", "config.json", "restoreab.exe", "rarreg.key", ".env"}
    unexpected = [str(path.relative_to(engine)) for path in engine.rglob("*")
                  if path.name.lower() in forbidden]
    if unexpected:
        raise RuntimeError(f"Private/optional files must not enter the desktop package: {unexpected}")
    output = repo / "artifacts/desktop"
    output.mkdir(parents=True, exist_ok=True)
    package_name = names["package"]
    destination = pick_portable_destination(output, names)
    with tempfile.TemporaryDirectory(prefix="desktop-package-", dir=output) as temp:
        stage = Path(temp) / package_name
        stage.mkdir()
        shutil.copy2(host, stage / names["exe"])
        shutil.copytree(engine, stage / "engine")
        # Same explicit docs/diagrams/license selection the NSIS bundle uses.
        stage_desktop_resources.stage_resources(repo, stage)
        (stage / "Start-Portable.cmd").write_bytes(
            (f'@echo off\r\nstart "{names["product"]}" "%~dp0{names["exe"]}" --portable\r\n').encode("utf-8"))
        (stage / "README.txt").write_text(
            f"{names['product']} {version} / Windows x64 desktop preview\n"
            f"Start {names['exe']}. Keep engine/ and its _internal/ directory together.\n"
            "Microsoft Edge WebView2 Runtime is required. No Python/Node/Rust installation is required.\n"
            "Default state is in %LOCALAPPDATA%\\io.reorder.desktop.\n"
            "Start-Portable.cmd uses data/ next to the EXE; passwords.txt is a public editable UTF-8 library.\n"
            "All archive processing and temporary files stay in the selected work folder.\n"
            "The last work folder and saved options are restored at next launch.\n"
            "See docs/desktop-user-guide.md for operation and manual acceptance.\n"
            "This build is unsigned. Real files and user experience await human acceptance.\n",
            encoding="utf-8")
        entries = [{"path": str(p.relative_to(stage)).replace("\\", "/"),
                    "size": p.stat().st_size, "sha256": sha256(p)}
                   for p in sorted(stage.rglob("*")) if p.is_file()]
        (stage / "package-manifest.json").write_text(json.dumps(
            {"product": names["product"], "version": version, "platform": "windows-x64", "files": entries},
            ensure_ascii=False, indent=2), encoding="utf-8")
        archive = output / (package_name + ".zip")
        temporary_zip = output / (package_name + ".zip.tmp")
        # Some frozen runtime inputs retain epoch timestamps; clamp them to
        # ZIP's 1980 lower bound while preserving their content and hashes.
        with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED,
                             strict_timestamps=False) as bundle:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    bundle.write(path, str(path.relative_to(stage)))
        temporary_zip.replace(archive)
        shutil.copytree(stage, destination, dirs_exist_ok=True)
        # Only the current product/version installer may be copied; a stale
        # 0.2.0 or differently named installer is left where it was found.
        installers = select_current_installers(release / "bundle/nsis", names["product"], version)
        hashes = [(archive.name, sha256(archive))]
        for installer in installers:
            target = output / installer.name
            shutil.copy2(installer, target)
            hashes.append((target.name, sha256(target)))
        (output / "SHA256SUMS.txt").write_text(
            "".join(f"{digest}  {name}\n" for name, digest in hashes), encoding="utf-8")
    print(json.dumps({"product": names["product"], "version": version,
                      "portable": str(destination), "zip": str(archive), "files": len(entries),
                      "installers": [path.name for path in installers],
                      "expected_installers": expected_installers(names["product"], version),
                      "sha256": str(output / "SHA256SUMS.txt")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
