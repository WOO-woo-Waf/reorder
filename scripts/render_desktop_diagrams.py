#!/usr/bin/env python3
"""Reproducibly render the ReOrder desktop design diagrams.

Sources (tracked, hand-authored):
  docs/diagrams/architecture.json -> docs/diagrams/architecture.svg
      rendered by the fireworks-tech-graph renderer (JSON geometry, no browser)
  docs/diagrams/<name>.mmd        -> docs/diagrams/<name>.svg
      rendered by the pinned runtime/diagram-tools Mermaid CLI (Windows + Chrome)

Generated previews (git-ignored):
  artifacts/diagrams/continuation/<name>.png

Run from the repository root, prefixing the shell command with ``rtk``::

  rtk proxy /usr/local/bin/python scripts/render_desktop_diagrams.py
  rtk proxy /usr/local/bin/python scripts/render_desktop_diagrams.py --only classes
  rtk proxy /usr/local/bin/python scripts/render_desktop_diagrams.py --list

The Mermaid route calls the Windows ``node.exe`` with ``wslpath``-translated
arguments because the pinned ``runtime/diagram-tools/puppeteer.json`` points at
the Windows Chrome install. The script performs no network access, installs
nothing, and never changes global fonts.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

FIREWORKS = Path("/mnt/c/Users/98289/.codex/skills/fireworks-tech-graph/scripts/fireworks.py")
FIREWORKS_PYTHON = os.environ.get("REORDER_DIAGRAM_PYTHON", "/usr/local/bin/python")
WIN_NODE = Path("/mnt/c/Program Files/nodejs/node.exe")
MERMAID_CLI = REPO / "runtime/diagram-tools/node_modules/@mermaid-js/mermaid-cli/src/cli.js"
PUPPETEER = REPO / "runtime/diagram-tools/puppeteer.json"

DIAGRAMS = REPO / "docs/diagrams"
PNG_OUT = REPO / "artifacts/diagrams/continuation"

ARCHITECTURE = "architecture"
MERMAID_NAMES = ("modules", "classes", "sequence", "states", "package_states")
ALL_NAMES = (ARCHITECTURE,) + MERMAID_NAMES


def win_path(path: Path) -> str:
    """Translate a WSL path to a Windows path for the Windows Mermaid CLI."""
    result = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def run(cmd: list[str]) -> None:
    print("+ " + " ".join(str(part) for part in cmd), flush=True)
    subprocess.run(cmd, check=True)


def render_architecture(png_width: int) -> None:
    source = DIAGRAMS / "architecture.json"
    svg = DIAGRAMS / "architecture.svg"
    report = PNG_OUT / "architecture.render.json"
    run([FIREWORKS_PYTHON, str(FIREWORKS), "validate", ARCHITECTURE, str(source)])
    run([FIREWORKS_PYTHON, str(FIREWORKS), "render", ARCHITECTURE, str(source), str(svg), "--report", str(report)])
    run([FIREWORKS_PYTHON, str(FIREWORKS), "check", str(svg)])
    run([FIREWORKS_PYTHON, str(FIREWORKS), "export-png", str(svg), str(PNG_OUT / "architecture.png"), "--width", str(png_width)])


def render_mermaid(name: str) -> None:
    source = DIAGRAMS / f"{name}.mmd"
    svg = DIAGRAMS / f"{name}.svg"
    png = PNG_OUT / f"{name}.png"
    base = [str(WIN_NODE), win_path(MERMAID_CLI), "-i", win_path(source), "-p", win_path(PUPPETEER)]
    run(base + ["-o", win_path(svg)])
    run(base + ["-o", win_path(png), "-s", "2"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", action="append", choices=ALL_NAMES, help="render only this diagram (repeatable)")
    parser.add_argument("--list", action="store_true", help="print diagram names and exit")
    parser.add_argument("--png-width", type=int, default=1920, help="architecture PNG width (default 1920)")
    args = parser.parse_args(argv)

    if args.list:
        print("\n".join(ALL_NAMES))
        return 0

    missing = [str(path) for path in (FIREWORKS, WIN_NODE, MERMAID_CLI, PUPPETEER) if not path.exists()]
    if missing:
        print("missing renderer dependency:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 2

    PNG_OUT.mkdir(parents=True, exist_ok=True)
    for name in (args.only or ALL_NAMES):
        print(f"== {name} ==", flush=True)
        if name == ARCHITECTURE:
            render_architecture(args.png_width)
        else:
            render_mermaid(name)

    print("done. svg -> docs/diagrams, png -> artifacts/diagrams/continuation", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
