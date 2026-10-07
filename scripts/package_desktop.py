"""Assemble a Windows desktop preview from explicit, already-built inputs."""
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    repo = args.repo.resolve()
    config = json.loads((repo / "apps/desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    version = config["version"]
    release = repo / "apps/desktop/src-tauri/target/release"
    host = release / "reorder-desktop.exe"
    engine = repo / "apps/desktop/src-tauri/resources/engine"
    if not host.is_file():
        raise RuntimeError(f"Incomplete desktop inputs: missing {host}")
    # Require the real staged engine files, not just directories.
    stage_desktop_engine.validate_staged_engine(engine)
    forbidden = {"passwords.txt", "config.json", "restoreab.exe", "rarreg.key", ".env"}
    unexpected = [str(path.relative_to(engine)) for path in engine.rglob("*")
                  if path.name.lower() in forbidden]
    if unexpected:
        raise RuntimeError(f"Private/optional files must not enter the desktop package: {unexpected}")
    output = repo / "artifacts/desktop"
    output.mkdir(parents=True, exist_ok=True)
    package_name = f"ReOrder-{version}-windows-x64"
    destination = output / "ReOrder-portable"
    if destination.exists():
        # Never replace a directory that the user may already have run from.
        destination = output / package_name
        if destination.exists():
            destination = Path(tempfile.mkdtemp(prefix=package_name + "-", dir=output))
    with tempfile.TemporaryDirectory(prefix="desktop-package-", dir=output) as temp:
        stage = Path(temp) / package_name
        stage.mkdir()
        shutil.copy2(host, stage / "ReOrder.exe")
        shutil.copytree(engine, stage / "engine")
        # Same explicit docs/diagrams/license selection the NSIS bundle uses.
        stage_desktop_resources.stage_resources(repo, stage)
        (stage / "Start-Portable.cmd").write_bytes(
            b'@echo off\r\nstart "ReOrder" "%~dp0ReOrder.exe" --portable\r\n')
        (stage / "README.txt").write_text(
            "ReOrder " + version + " / Windows x64 desktop preview\n"
            "Start ReOrder.exe. Keep engine/ and its _internal/ directory together.\n"
            "Microsoft Edge WebView2 Runtime is required. No Python/Node/Rust installation is required.\n"
            "Default state is in %LOCALAPPDATA%\\io.reorder.desktop.\n"
            "Start-Portable.cmd uses data/ next to the EXE and session-only passwords.\n"
            "See docs/desktop-user-guide.md for operation and manual acceptance.\n"
            "This build is unsigned. Real files and user experience await human acceptance.\n",
            encoding="utf-8")
        entries = [{"path": str(p.relative_to(stage)).replace("\\", "/"),
                    "size": p.stat().st_size, "sha256": sha256(p)}
                   for p in sorted(stage.rglob("*")) if p.is_file()]
        (stage / "package-manifest.json").write_text(json.dumps(
            {"version": version, "platform": "windows-x64", "files": entries},
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
        installers = list((release / "bundle/nsis").glob("*.exe"))
        hashes = [(archive.name, sha256(archive))]
        for installer in installers:
            target = output / installer.name
            shutil.copy2(installer, target)
            hashes.append((target.name, sha256(target)))
        (output / "SHA256SUMS.txt").write_text(
            "".join(f"{digest}  {name}\n" for name, digest in hashes), encoding="utf-8")
    print(json.dumps({"portable": str(destination), "zip": str(archive),
                      "files": len(entries), "sha256": str(output / "SHA256SUMS.txt")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
