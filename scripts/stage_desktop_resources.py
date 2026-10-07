#!/usr/bin/env python3
"""Stage the explicit desktop docs and license evidence used by every package.

Both the Windows bundler (``build_desktop_windows.ps1`` populates
``apps/desktop/src-tauri/resources`` for the NSIS bundle) and the portable
assembler (``package_desktop.py``) call this module, so the shipped documentation
and license evidence always come from one explicit allowlist. A wildcard copy of a
destination directory is deliberately avoided: it can drag stale generated files
into a release.

Guarantees:

* Only the eight named documents and the explicit current diagram outputs are copied.
* License files come from ``license-evidence/manifest.json`` (every entry's
  ``license_files``) plus ``manifest.json``, ``SUMMARY.md`` and the upstream
  ``SOURCES.json``; every reference must resolve inside the evidence root.
* A selected resource whose basename looks sensitive is rejected.
* An existing generated ``docs``/``licenses`` target is moved to a unique, ignored
  ``artifacts/desktop/resource-backups`` snapshot before it is replaced; nothing is
  deleted in place.

This module never opens ``resources/passwords.txt``, a user ``config.json``,
``docs/todo.md``, ``.env`` or stored credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

# The eight documents that belong in the package, and the current diagram outputs.
DOC_NAMES = (
    "desktop-user-guide.md",
    "desktop-code-guide.md",
    "desktop-language-guide.md",
    "desktop-design.md",
    "desktop-status.md",
    "desktop-third-party.md",
    "product_plan.md",
    "portable_user_guide.md",
)
DIAGRAM_NAMES = (
    "architecture.json",
    "architecture.svg",
    "modules.mmd",
    "modules.svg",
    "classes.mmd",
    "classes.svg",
    "sequence.mmd",
    "sequence.svg",
    "states.mmd",
    "states.svg",
    "package_states.mmd",
    "package_states.svg",
)
LICENSE_METADATA_NAMES = ("manifest.json", "SUMMARY.md")

# Basenames that must never be staged, even if a manifest accidentally references them.
SENSITIVE_BASENAMES = frozenset({
    "passwords.txt", "config.json", "config.local.json", "todo.md", ".env",
    ".env.local", "credentials", "credentials.json", "credentials.txt",
    "secrets.json", "secret.txt", "rarreg.key", "license.key", "product.key",
    "id_rsa", "id_ed25519",
})


class ResourceSelectionError(RuntimeError):
    """Raised when the explicit resource selection cannot be satisfied safely."""


def _absolute(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _within(child: Path, root: Path) -> bool:
    """True when ``child`` resolves inside ``root`` (case-insensitive on Windows)."""
    def parts(path: Path) -> list[str]:
        return [os.path.normcase(part) for part in _absolute(path).parts]

    child_parts, root_parts = parts(child), parts(root)
    return bool(root_parts) and child_parts[:len(root_parts)] == root_parts


def _split_reference(raw: str) -> tuple[str, ...]:
    return tuple(part for part in raw.replace("\\", "/").split("/") if part not in ("", "."))


def _reject_sensitive(paths) -> None:
    for path in paths:
        if Path(path).name.lower() in SENSITIVE_BASENAMES:
            raise ResourceSelectionError(f"sensitive resource must not be staged: {path}")


def _require(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ResourceSelectionError(f"{label} is not a regular file: {path}")
    return path


def select_documents(repo: Path) -> list[Path]:
    docs_root = repo / "docs"
    documents = [_require(docs_root / name, "document") for name in DOC_NAMES]
    _reject_sensitive(documents)
    return documents


def select_diagrams(repo: Path) -> list[Path]:
    diagram_root = repo / "docs/diagrams"
    diagrams = [_require(diagram_root / name, "diagram") for name in DIAGRAM_NAMES]
    _reject_sensitive(diagrams)
    return diagrams


def _resolve_upstream(raw: str, evidence: Path) -> tuple[Path, str]:
    """Resolve the manifest's ``upstream_sources`` and prove it stays inside evidence."""
    candidate = Path(raw)
    if candidate.is_absolute() and _within(candidate, evidence) and candidate.is_file():
        return candidate, str(candidate.relative_to(evidence)).replace("\\", "/")
    # The manifest may record an absolute build-host path; accept only the part that
    # follows the evidence directory name and still resolves inside the evidence root.
    parts = _split_reference(raw)
    name = evidence.name.lower()
    for index, part in enumerate(parts):
        if part.lower() == name and index + 1 < len(parts):
            tail = parts[index + 1:]
            if tail and ".." not in tail:
                resolved = evidence.joinpath(*tail)
                if _within(resolved, evidence) and resolved.is_file():
                    return resolved, "/".join(tail)
    raise ResourceSelectionError(f"upstream_sources escapes license-evidence: {raw}")


def select_license_files(repo: Path, evidence: Path) -> list[tuple[str, Path]]:
    """Return ``(relative posix path, source file)`` pairs from the explicit manifest."""
    evidence = _absolute(evidence)
    manifest_path = _require(evidence / "manifest.json", "license manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ResourceSelectionError("license manifest has no entries list")
    selected: dict[str, Path] = {}
    for name in LICENSE_METADATA_NAMES:
        selected[name] = _require(evidence / name, "license metadata")
    for entry in entries:
        for raw in entry.get("license_files") or ():
            parts = _split_reference(str(raw))
            if not parts or parts[0].endswith(":") or ".." in parts:
                raise ResourceSelectionError(f"license reference escapes evidence: {raw}")
            relative = "/".join(parts)
            candidate = evidence.joinpath(*parts)
            if not _within(candidate, evidence):
                raise ResourceSelectionError(f"license reference escapes evidence: {raw}")
            selected[relative] = _require(candidate, f"license file {relative}")
    upstream = manifest.get("upstream_sources")
    if upstream:
        source, relative = _resolve_upstream(str(upstream), evidence)
        selected[relative] = source
    _reject_sensitive(list(selected.values()))
    return sorted(selected.items())


def _backup(target: Path, backup_root: Path, name: str) -> Path:
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_root / f"{stamp}-{name}"
    counter = 1
    while destination.exists():
        counter += 1
        destination = backup_root / f"{stamp}-{name}-{counter}"
    shutil.move(str(target), str(destination))
    return destination


def stage_resources(repo: Path, out_root: Path, *, backup_root: Path | None = None,
                    evidence: Path | None = None) -> dict:
    """Build a fresh ``docs``/``licenses`` tree at ``out_root`` from the allowlist."""
    repo = _absolute(Path(repo))
    out_root = _absolute(Path(out_root))
    evidence = _absolute(Path(evidence)) if evidence else (repo / "artifacts/desktop/license-evidence")
    documents = select_documents(repo)
    diagrams = select_diagrams(repo)
    licenses = select_license_files(repo, evidence)
    backups: list[str] = []
    for name in ("docs", "licenses"):
        target = out_root / name
        if target.exists() or target.is_symlink():
            if backup_root is None:
                raise ResourceSelectionError(f"refusing to replace existing {target} without a backup root")
            backups.append(str(_backup(target, _absolute(Path(backup_root)), name)))
    document_root = out_root / "docs"
    document_root.mkdir(parents=True, exist_ok=True)
    for path in documents:
        shutil.copy2(path, document_root / path.name)
    diagram_root = document_root / "diagrams"
    diagram_root.mkdir(parents=True, exist_ok=True)
    for path in diagrams:
        shutil.copy2(path, diagram_root / path.name)
    license_root = out_root / "licenses"
    license_root.mkdir(parents=True, exist_ok=True)
    for relative, source in licenses:
        destination = license_root.joinpath(*relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return {
        "docs": [path.name for path in documents],
        "diagrams": [path.name for path in diagrams],
        "license_files": [relative for relative, _source in licenses],
        "backups": backups,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--resources", type=Path, default=None,
                        help="destination resources root (default <repo>/apps/desktop/src-tauri/resources)")
    parser.add_argument("--evidence", type=Path, default=None,
                        help="license evidence root (default <repo>/artifacts/desktop/license-evidence)")
    parser.add_argument("--backup-root", type=Path, default=None,
                        help="where to snapshot a replaced docs/licenses tree")
    args = parser.parse_args()
    repo = _absolute(args.repo)
    resources = _absolute(args.resources or repo / "apps/desktop/src-tauri/resources")
    backup_root = args.backup_root or repo / "artifacts/desktop/resource-backups"
    result = stage_resources(repo, resources, backup_root=backup_root, evidence=args.evidence)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
