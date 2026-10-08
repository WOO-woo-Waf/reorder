#!/usr/bin/env python3
"""Collect third-party license evidence for the ReOrder desktop distribution.

This script is read-only against source manifests and installed dependency
packages. It writes only to the evidence directory (default
``artifacts/desktop/license-evidence``), which ``.gitignore`` covers, and it
never touches the frozen engine staging directory or the project root LICENSE.

It gathers, for the locked desktop dependency set:

* npm packages from ``apps/desktop/package-lock.json`` + ``node_modules``;
* Rust crates from ``apps/desktop/src-tauri/Cargo.lock`` + the local Cargo
  registry checkout;
* frozen Python distributions from
  ``scripts/requirements-desktop-windows.lock.txt`` + the build venv
  ``site-packages`` metadata;
* the 7-Zip redistribution license and locked UnRAR/Bandizip license texts.

Every entry is classified as one of:

* ``text``       - a license/notice file was copied into the evidence tree;
* ``metadata``   - only a declared license id is known (no text on disk);
* ``unresolved`` - neither a license id nor a text file was found.

The main thread performs final package integration. This script does not decide
the project's own root LICENSE and does not bundle restoreAB.

Reproduce (WSL or Windows):

    rtk proxy python3 scripts/collect_desktop_licenses.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tomllib
import urllib.request
from pathlib import Path

REPO_DEFAULT = Path(__file__).resolve().parents[1]

_RAW = "https://raw.githubusercontent.com"

# Upstream license/declaration sources for crates whose published package ships
# no license text (verified against local crate metadata repository/version).
# Keyed by (crate, version). ``files`` are fetched (``--fetch-upstream``) from
# the pinned ``url`` and reused offline otherwise.
UPSTREAM_LICENSES: dict[tuple[str, str], dict] = {
    ("alloc-stdlib", "0.3.0"): {
        "repository": "https://github.com/dropbox/rust-alloc-no-stdlib",
        "ref_kind": "tag",
        "ref": "0.3.0",
        "commit": "0a81fd6928ea3b33c8cd484aa4575d50ffb98012",
        "files": [
            {"name": "LICENSE", "url": f"{_RAW}/dropbox/rust-alloc-no-stdlib/0a81fd6928ea3b33c8cd484aa4575d50ffb98012/LICENSE"},
        ],
    },
    ("defmt-parser", "1.0.0"): {
        "repository": "https://github.com/knurling-rs/defmt",
        "ref_kind": "tag",
        "ref": "defmt-v1.0.0",
        "commit": "48c82e1cb7beb11bd9412954b61f8624f5bb1d8e",
        "note": "No defmt-parser-v1.0.0 tag exists; used the workspace 1.0.0 release tag defmt-v1.0.0.",
        "files": [
            {"name": "LICENSE-MIT", "url": f"{_RAW}/knurling-rs/defmt/48c82e1cb7beb11bd9412954b61f8624f5bb1d8e/LICENSE-MIT"},
            {"name": "LICENSE-APACHE", "url": f"{_RAW}/knurling-rs/defmt/48c82e1cb7beb11bd9412954b61f8624f5bb1d8e/LICENSE-APACHE"},
        ],
    },
    ("selectors", "0.38.0"): {
        "repository": "https://github.com/servo/stylo",
        "ref_kind": "commit",
        "ref": "default-branch-head@2026-10-07",
        "commit": "0cb50925b0259e53b953d72d5def3a24caa9ab58",
        "note": "The repository ships no LICENSE file; selectors declares license = \"MPL-2.0\" in Cargo.toml and README.md states 'Stylo is licensed under MPL 2.0'. MPL-2.0.txt is the canonical Mozilla text.",
        "files": [
            {"name": "README.md", "url": f"{_RAW}/servo/stylo/0cb50925b0259e53b953d72d5def3a24caa9ab58/README.md", "note": "contains the MPL 2.0 declaration"},
            {"name": "MPL-2.0.txt", "url": "https://www.mozilla.org/media/MPL/2.0/index.txt", "note": "canonical MPL-2.0 text (repo ships none)"},
        ],
    },
    ("tauri-plugin", "2.7.1"): {
        "repository": "https://github.com/tauri-apps/tauri",
        "ref_kind": "tag",
        "ref": "tauri-plugin-v2.7.1",
        "commit": "30da1fd6e17de6107ecc850c95dfb16b5729f2dd",
        "files": [
            {"name": "LICENSE-APACHE-2.0", "url": f"{_RAW}/tauri-apps/tauri/30da1fd6e17de6107ecc850c95dfb16b5729f2dd/LICENSE-APACHE-2.0"},
            {"name": "LICENSE-MIT", "url": f"{_RAW}/tauri-apps/tauri/30da1fd6e17de6107ecc850c95dfb16b5729f2dd/LICENSE-MIT"},
        ],
    },
    ("webview2-com", "0.39.1"): {
        "repository": "https://github.com/wravery/webview2-rs",
        "ref_kind": "commit",
        "ref": "default-branch-head@2026-10-07",
        "commit": "edc2caf886175ccaebe86078c9cfe1ae2a187328",
        "note": "Repository has no release tags; used the default-branch head commit.",
        "files": [
            {"name": "LICENSE", "url": f"{_RAW}/wravery/webview2-rs/edc2caf886175ccaebe86078c9cfe1ae2a187328/LICENSE"},
        ],
    },
    ("webview2-com-macros", "0.8.1"): {
        "repository": "https://github.com/wravery/webview2-rs",
        "ref_kind": "commit",
        "ref": "default-branch-head@2026-10-07",
        "commit": "edc2caf886175ccaebe86078c9cfe1ae2a187328",
        "note": "Same repository/license file as webview2-com.",
        "files": [
            {"name": "LICENSE", "url": f"{_RAW}/wravery/webview2-rs/edc2caf886175ccaebe86078c9cfe1ae2a187328/LICENSE"},
        ],
    },
    ("webview2-com-sys", "0.39.1"): {
        "repository": "https://github.com/wravery/webview2-rs",
        "ref_kind": "commit",
        "ref": "default-branch-head@2026-10-07",
        "commit": "edc2caf886175ccaebe86078c9cfe1ae2a187328",
        "note": "Same repository/license file as webview2-com.",
        "files": [
            {"name": "LICENSE", "url": f"{_RAW}/wravery/webview2-rs/edc2caf886175ccaebe86078c9cfe1ae2a187328/LICENSE"},
        ],
    },
}

# A file is treated as license/notice text when its lowercased name starts with
# one of these stems (e.g. LICENSE, COPYING.txt, LICENSE-APACHE-2.0).
LICENSE_STEMS = (
    "license",
    "licence",
    "copying",
    "copyright",
    "notice",
    "unlicense",
)

_SAFE = re.compile(r"[^A-Za-z0-9._@+-]+")


def _safe_component(value: str) -> str:
    """Return a filesystem-safe single path component."""
    cleaned = _SAFE.sub("_", value).strip("._") or "unknown"
    return cleaned[:120]


def _find_license_files(directory: Path) -> list[Path]:
    """Return license/notice files directly under ``directory`` (non-recursive)."""
    if not directory.is_dir():
        return []
    found: list[Path] = []
    for child in sorted(directory.iterdir()):
        if not child.is_file():
            continue
        if child.name.lower().startswith(LICENSE_STEMS):
            found.append(child)
    return found


def _copy_license_files(
    sources: list[Path],
    dest: Path,
    evidence_root: Path,
) -> list[str]:
    """Copy ``sources`` into ``dest``; return paths relative to ``evidence_root``."""
    if not sources:
        return []
    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    used: set[str] = set()
    for source in sources:
        name = source.name
        target = dest / name
        if name in used:
            suffix = 1
            while f"{name}.{suffix}" in used:
                suffix += 1
            target = dest / f"{name}.{suffix}"
            used.add(target.name)
        else:
            used.add(name)
        shutil.copy2(source, target)
        written.append(str(target.relative_to(evidence_root)))
    return written


def _entry_status(license_id: str | None, files: list[str]) -> str:
    if files:
        return "text"
    if license_id:
        return "metadata"
    return "unresolved"


# --------------------------------------------------------------------------- #
# Upstream license reuse / fetch
# --------------------------------------------------------------------------- #
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _download(url: str, dest: Path, attempts: int = 6) -> bool:
    """Download ``url`` to ``dest`` with retries. Honors *_proxy env vars."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                if response.status != 200:
                    continue
                dest.write_bytes(response.read())
            if dest.stat().st_size > 0:
                return True
        except Exception:  # noqa: BLE001 - network flakiness must not abort
            pass
    return False


def prepare_upstream(
    upstream_dir: Path,
    fetch: bool,
    allow_network: bool = True,
) -> dict:
    """Ensure upstream files exist (optionally fetch) and return an index.

    The index maps ``"<crate>@<version>"`` to provenance plus the list of
    present files with their sha256. Missing files are recorded but not fatal.
    """
    index: dict[str, dict] = {}
    for (name, version), spec in UPSTREAM_LICENSES.items():
        key = f"{name}@{version}"
        subdir = upstream_dir / f"{_safe_component(name)}-{_safe_component(version)}"
        files: list[dict] = []
        for entry in spec["files"]:
            target = subdir / entry["name"]
            if not target.is_file() and fetch and allow_network:
                _download(entry["url"], target)
            record = {"name": entry["name"], "url": entry["url"], "present": target.is_file()}
            if "note" in entry:
                record["note"] = entry["note"]
            if target.is_file():
                record["sha256"] = _sha256(target)
                record["path"] = str(target.relative_to(upstream_dir.parent))
            files.append(record)
        index[key] = {
            "repository": spec["repository"],
            "ref_kind": spec["ref_kind"],
            "ref": spec["ref"],
            "commit": spec["commit"],
            **({"note": spec["note"]} if "note" in spec else {}),
            "files": files,
        }
    upstream_dir.mkdir(parents=True, exist_ok=True)
    (upstream_dir / "SOURCES.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return index


def attach_upstream(entries: list[dict], upstream_dir: Path, index: dict) -> list[dict]:
    """Attach saved upstream texts to matching entries lacking local text."""
    for entry in entries:
        if entry["status"] == "text":
            continue
        if entry["ecosystem"] != "cargo":
            continue
        key = f"{entry['name']}@{entry['version']}"
        record = index.get(key)
        if not record:
            continue
        present = [f for f in record["files"] if f["present"]]
        if not present:
            entry["upstream_source"] = record
            continue
        for item in present:
            rel = item["path"]  # already relative to the evidence root
            if rel not in entry["license_files"]:
                entry["license_files"].append(rel)
        entry["upstream_source"] = record
        entry["status"] = _entry_status(entry["license"], entry["license_files"])
    return entries


# --------------------------------------------------------------------------- #
# npm
# --------------------------------------------------------------------------- #
def collect_npm(repo: Path, evidence_root: Path) -> list[dict]:
    lock_path = repo / "apps/desktop/package-lock.json"
    package_json = repo / "apps/desktop/package.json"
    node_modules = repo / "apps/desktop/node_modules"
    if not lock_path.is_file():
        print(f"[npm] missing {lock_path}", file=sys.stderr)
        return []

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    packages = lock.get("packages", {})

    direct_prod: set[str] = set()
    direct_dev: set[str] = set()
    if package_json.is_file():
        pj = json.loads(package_json.read_text(encoding="utf-8"))
        direct_prod = set(pj.get("dependencies", {}))
        direct_dev = set(pj.get("devDependencies", {}))

    entries: list[dict] = []
    for key, meta in packages.items():
        if not key:
            continue  # root project entry
        marker = "node_modules/"
        if marker not in key:
            continue
        name = key.rsplit(marker, 1)[1]
        version = meta.get("version")
        license_id = meta.get("license")
        dev = bool(meta.get("dev"))
        pkg_dir = node_modules / name
        installed = pkg_dir.is_dir()

        license_files: list[str] = []
        if installed:
            sources = _find_license_files(pkg_dir)
            dest = evidence_root / "npm" / f"{_safe_component(name)}@{_safe_component(str(version))}"
            license_files = _copy_license_files(sources, dest, evidence_root)

        scope = "dev" if dev else "prod"
        if name in direct_prod:
            scope = "direct-prod"
        elif name in direct_dev:
            scope = "direct-dev"

        entries.append(
            {
                "ecosystem": "npm",
                "name": name,
                "version": version,
                "license": license_id,
                "scope": scope,
                "installed": installed,
                "license_files": license_files,
                "status": _entry_status(license_id, license_files),
            }
        )
    return entries


# --------------------------------------------------------------------------- #
# Cargo
# --------------------------------------------------------------------------- #
def _cargo_license_from_toml(crate_dir: Path) -> tuple[str | None, list[Path]]:
    manifest = crate_dir / "Cargo.toml"
    license_id: str | None = None
    extra: list[Path] = []
    if manifest.is_file():
        try:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            data = {}
        package = data.get("package", {})
        license_id = package.get("license")
        license_file = package.get("license-file")
        if license_file:
            candidate = crate_dir / license_file
            if candidate.is_file():
                extra.append(candidate)
    return license_id, extra


def _locate_registry_src(repo: Path, override: Path | None) -> Path | None:
    if override is not None:
        return override if override.is_dir() else None
    root = repo / "runtime/toolchains/cargo/registry/src"
    if not root.is_dir():
        return None
    for index in sorted(root.iterdir()):
        if index.is_dir():
            return index
    return None


def collect_cargo(repo: Path, evidence_root: Path, registry_src: Path | None) -> list[dict]:
    lock_path = repo / "apps/desktop/src-tauri/Cargo.lock"
    if not lock_path.is_file():
        print(f"[cargo] missing {lock_path}", file=sys.stderr)
        return []

    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    entries: list[dict] = []
    for pkg in lock.get("package", []):
        name = pkg.get("name")
        version = pkg.get("version")
        source = pkg.get("source")
        if not name or not version:
            continue
        # Skip the local workspace crate (no registry source, no license text).
        is_local = source is None
        crate_dir = None
        if registry_src is not None:
            candidate = registry_src / f"{name}-{version}"
            if candidate.is_dir():
                crate_dir = candidate

        license_id: str | None = None
        license_files: list[str] = []
        if crate_dir is not None:
            license_id, extra = _cargo_license_from_toml(crate_dir)
            sources = _find_license_files(crate_dir) + [p for p in extra if p not in _find_license_files(crate_dir)]
            dest = evidence_root / "cargo" / f"{_safe_component(name)}-{_safe_component(str(version))}"
            license_files = _copy_license_files(sources, dest, evidence_root)

        if is_local:
            continue

        entries.append(
            {
                "ecosystem": "cargo",
                "name": name,
                "version": version,
                "license": license_id,
                "scope": "prod",
                "installed": crate_dir is not None,
                "license_files": license_files,
                "status": _entry_status(license_id, license_files),
            }
        )
    return entries


# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #
def _parse_requirements_lock(path: Path) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not path.is_file():
        return pairs
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        pairs.append((name.strip(), version.strip()))
    return pairs


def _normalize_dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def _locate_site_packages(repo: Path, override: Path | None) -> Path | None:
    if override is not None:
        return override if override.is_dir() else None
    candidates = [
        repo / "runtime/desktop-build-venv/Lib/site-packages",
        repo / "runtime/desktop-build-venv/lib",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _metadata_license(dist_info: Path) -> tuple[str | None, list[Path]]:
    license_id: str | None = None
    license_files: list[Path] = []
    metadata = dist_info / "METADATA"
    if metadata.is_file():
        text = metadata.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if line.startswith("License-Expression:"):
                license_id = line.split(":", 1)[1].strip()
                break
            if line.startswith("License:") and license_id is None:
                license_id = line.split(":", 1)[1].strip() or None
            if line.startswith("License-File:"):
                name = line.split(":", 1)[1].strip()
                for base in (dist_info / "licenses", dist_info):
                    candidate = base / name
                    if candidate.is_file():
                        license_files.append(candidate)
                        break
    # A wheel may place license text directly in the dist-info dir, in a PEP 639
    # ``licenses/`` subdir, or only reference it from METADATA.
    for base in (dist_info, dist_info / "licenses"):
        for child in _find_license_files(base):
            if child not in license_files:
                license_files.append(child)
    return license_id, license_files


def _find_dist_info(site_packages: Path, name: str, version: str) -> Path | None:
    wanted = _normalize_dist_name(name)
    for candidate in sorted(site_packages.glob("*.dist-info")):
        stem = candidate.name[: -len(".dist-info")]
        dist_name, _, dist_version = stem.rpartition("-")
        if dist_name and dist_version == version and _normalize_dist_name(dist_name) == wanted:
            return candidate
    return None


def collect_python(
    repo: Path,
    evidence_root: Path,
    site_packages: Path | None,
    lock_path: Path,
) -> list[dict]:
    pairs = _parse_requirements_lock(lock_path)
    if not pairs:
        print(f"[python] no pinned requirements in {lock_path}", file=sys.stderr)
    entries: list[dict] = []
    for name, version in pairs:
        dist_info = _find_dist_info(site_packages, name, version) if site_packages is not None else None
        license_id: str | None = None
        license_files: list[str] = []
        if dist_info is not None:
            license_id, sources = _metadata_license(dist_info)
            dest = evidence_root / "python" / f"{_safe_component(name)}-{_safe_component(version)}"
            license_files = _copy_license_files(sources, dest, evidence_root)
        entries.append(
            {
                "ecosystem": "python",
                "name": name,
                "version": version,
                "license": license_id,
                "scope": "prod",
                "installed": dist_info is not None,
                "license_files": license_files,
                "status": _entry_status(license_id, license_files),
            }
        )
    return entries


# --------------------------------------------------------------------------- #
# 7-Zip
# --------------------------------------------------------------------------- #
def collect_seven_zip(repo: Path, evidence_root: Path, override: Path | None) -> dict:
    directory = override
    if directory is None:
        candidate = repo / "tools/7zip/Files/7-Zip"
        directory = candidate if candidate.is_dir() else None
    entry = {
        "ecosystem": "7zip",
        "name": "7-Zip",
        "version": None,
        "license": "LGPL-2.1-or-later (7z.dll also BSD-3/BSD-2 and unRAR restriction)",
        "scope": "bundled-tool",
        "installed": bool(directory and (directory / "License.txt").is_file()),
        "license_files": [],
        "status": "unresolved",
    }
    if not directory or not (directory / "License.txt").is_file():
        print("[7zip] License.txt not found; provide --seven-zip", file=sys.stderr)
        return entry

    readme = directory / "readme.txt"
    if readme.is_file():
        match = re.search(r"7-Zip\s+([0-9]+\.[0-9]+(?:\.[0-9]+)?)", readme.read_text(encoding="utf-8", errors="replace"))
        if match:
            entry["version"] = match.group(1)
    if entry["version"] is None:
        match = re.search(r"(\d{4})", directory.name)
        if match:
            entry["version"] = match.group(1)

    sources = [directory / "License.txt"]
    if readme.is_file():
        sources.append(readme)
    dest = evidence_root / "7zip"
    entry["license_files"] = _copy_license_files(sources, dest, evidence_root)
    entry["status"] = "text" if entry["license_files"] else "metadata"
    return entry


def collect_fixed_tools(repo: Path, evidence_root: Path) -> list[dict]:
    """Copy license texts from the validated, version-locked desktop tools."""
    from stage_desktop_tools import validate_fixed_tools

    stage = repo / "apps/desktop/src-tauri/resources/engine"
    validate_fixed_tools(repo, stage)
    lock = json.loads((repo / "scripts/desktop-tools.lock.json").read_text(encoding="utf-8"))
    entries = []
    for tool in lock["tools"]:
        directory = stage / "tools" / tool["id"]
        sources = [directory / path for path in tool["license_files"]]
        entry = {
            "ecosystem": tool["id"], "name": tool["name"], "version": tool["version"],
            "license": tool.get("license", "See bundled upstream license text"),
            "scope": "bundled-tool", "installed": True,
            "source": tool["url"], "archive_sha256": tool["sha256"],
            "license_files": _copy_license_files(sources, evidence_root / tool["id"], evidence_root),
            "status": "text",
        }
        # Recorded redistribution permission (for example the owner-confirmed
        # Bandisoft written permission) is surfaced as the basis for bundling.
        permission = (tool.get("provenance") or {}).get("redistribution_permission")
        if permission:
            entry["redistribution_permission"] = permission
        entries.append(entry)
    return entries


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def _write_summary(evidence_root: Path, entries: list[dict], not_bundled: list[str]) -> None:
    by_eco: dict[str, list[dict]] = {}
    for entry in entries:
        by_eco.setdefault(entry["ecosystem"], []).append(entry)

    lines = ["# Desktop third-party license evidence", "",
             f"Entries: {len(entries)}", ""]
    for eco in sorted(by_eco):
        group = by_eco[eco]
        counts = {"text": 0, "metadata": 0, "unresolved": 0}
        for entry in group:
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        lines.append(f"## {eco} ({len(group)} entries)")
        lines.append("")
        lines.append(f"- text: {counts['text']}, metadata-only: {counts['metadata']}, unresolved: {counts['unresolved']}")
        lines.append("")
        unresolved = [e for e in group if e["status"] != "text"]
        if unresolved:
            lines.append("Not fully resolved:")
            lines.append("")
            for entry in unresolved:
                lines.append(f"- {entry['name']} {entry['version']} [{entry['status']}] license={entry['license']!r}")
            lines.append("")
    lines.append("## Explicitly not bundled")
    lines.append("")
    for item in not_bundled:
        lines.append(f"- {item}")
    lines.append("")
    permitted = [e for e in entries if e.get("redistribution_permission")]
    if permitted:
        lines.append("## Redistribution permission basis")
        lines.append("")
        for entry in permitted:
            lines.append(f"- {entry['name']} {entry['version']}: {entry['redistribution_permission']}")
        lines.append("")
    upstream = [e for e in entries if e.get("upstream_source")]
    if upstream:
        lines.append("## Upstream-supplemented entries")
        lines.append("")
        for entry in upstream:
            record = entry["upstream_source"]
            lines.append(
                f"- {entry['name']} {entry['version']} <- {record['repository']} "
                f"@{record['ref']} ({record['commit']}) [{entry['status']}]"
            )
        lines.append("")
    (evidence_root / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")


def _load_windows_graph(path: Path) -> set[str] | None:
    """Return the set of ``name@version`` registry packages in the Windows graph."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    graph: set[str] = set()
    for pkg in data.get("packages", []):
        if pkg.get("source") and pkg.get("name") and pkg.get("version"):
            graph.add(f"{pkg['name']}@{pkg['version']}")
    return graph


def _scope_counts(entries: list[dict]) -> dict:
    def tally(group: list[dict]) -> dict:
        return {
            "total": len(group),
            "text": sum(1 for e in group if e["status"] == "text"),
            "metadata": sum(1 for e in group if e["status"] == "metadata"),
            "unresolved": sum(1 for e in group if e["status"] == "unresolved"),
        }

    cargo = [e for e in entries if e["ecosystem"] == "cargo"]
    win = [e for e in cargo if e.get("windows_graph")]
    other = [e for e in cargo if e.get("windows_graph") is False]
    return {"windows_graph": tally(win), "not_in_windows_graph": tally(other)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_DEFAULT)
    parser.add_argument(
        "--evidence",
        type=Path,
        default=None,
        help="evidence output root (default <repo>/artifacts/desktop/license-evidence)",
    )
    parser.add_argument("--seven-zip", type=Path, default=None, help="directory containing License.txt")
    parser.add_argument("--cargo-registry-src", type=Path, default=None)
    parser.add_argument("--site-packages", type=Path, default=None)
    parser.add_argument("--python-lock", type=Path, default=None)
    parser.add_argument("--upstream-dir", type=Path, default=None, help="saved upstream license dir (default <evidence>/upstream)")
    parser.add_argument("--fetch-upstream", action="store_true", help="download missing upstream license texts (network)")
    parser.add_argument("--windows-deps", type=Path, default=None, help="cargo metadata JSON for the Windows graph")
    parser.add_argument("--windows-scope", type=Path, default=None, help="scope summary JSON from the main thread")
    args = parser.parse_args()

    repo = args.repo.resolve()
    evidence_root = (args.evidence or repo / "artifacts/desktop/license-evidence").resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)

    registry_src = _locate_registry_src(repo, args.cargo_registry_src)
    if registry_src is None:
        print("[cargo] no registry src found; crates will be unresolved", file=sys.stderr)
    site_packages = _locate_site_packages(repo, args.site_packages)
    if site_packages is None:
        print("[python] no site-packages found; dists will be unresolved", file=sys.stderr)
    python_lock = args.python_lock or repo / "scripts/requirements-desktop-windows.lock.txt"
    upstream_dir = (args.upstream_dir or evidence_root / "upstream").resolve()

    upstream_index = prepare_upstream(upstream_dir, fetch=args.fetch_upstream)

    windows_deps = args.windows_deps or repo / "artifacts/desktop/windows-dependencies.json"
    windows_graph = _load_windows_graph(windows_deps)
    windows_scope_path = args.windows_scope or repo / "artifacts/desktop/windows-license-scope.json"
    windows_scope = None
    if windows_scope_path.is_file():
        try:
            windows_scope = json.loads(windows_scope_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            windows_scope = None

    entries: list[dict] = []
    entries.extend(collect_npm(repo, evidence_root))
    entries.extend(collect_cargo(repo, evidence_root, registry_src))
    entries.extend(collect_python(repo, evidence_root, site_packages, python_lock))
    entries.append(collect_seven_zip(repo, evidence_root, args.seven_zip))
    entries.extend(collect_fixed_tools(repo, evidence_root))
    attach_upstream(entries, upstream_dir, upstream_index)

    if windows_graph is not None:
        for entry in entries:
            if entry["ecosystem"] == "cargo":
                entry["windows_graph"] = f"{entry['name']}@{entry['version']}" in windows_graph

    not_bundled = [
        "restoreAB.exe (legacy user tool) - not bundled in the desktop package",
        "The project's own root LICENSE is decided by the main thread, not here.",
    ]
    manifest = {
        "generated_by": "scripts/collect_desktop_licenses.py",
        "repo": str(repo),
        "evidence_root": str(evidence_root),
        "cargo_registry_src": str(registry_src) if registry_src else None,
        "site_packages": str(site_packages) if site_packages else None,
        "python_lock": str(python_lock),
        "upstream_dir": str(upstream_dir),
        "counts": {
            "total": len(entries),
            "text": sum(1 for e in entries if e["status"] == "text"),
            "metadata": sum(1 for e in entries if e["status"] == "metadata"),
            "unresolved": sum(1 for e in entries if e["status"] == "unresolved"),
        },
        "cargo_scope": _scope_counts(entries) if windows_graph is not None else None,
        "windows_scope_summary": windows_scope,
        "upstream_sources": str(upstream_dir / "SOURCES.json"),
        "not_bundled": not_bundled,
        "entries": entries,
    }
    (evidence_root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_summary(evidence_root, entries, not_bundled)

    counts = manifest["counts"]
    print(
        f"Collected {counts['total']} entries "
        f"(text={counts['text']}, metadata={counts['metadata']}, unresolved={counts['unresolved']})"
    )
    print(f"Evidence: {evidence_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
