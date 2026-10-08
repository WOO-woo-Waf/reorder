"""Private desktop entry point. stdout is exclusively JSON-RPC, never CLI logs."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reorder_engine.application.facade import EngineFacade
from reorder_engine.infrastructure.desktop_paths import DesktopPaths
from reorder_engine.infrastructure.json_rpc import JsonRpcServer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-root", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--session-secrets", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    reader, writer = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr
    facade = EngineFacade(DesktopPaths.discover(app_root=args.app_root, data_root=args.data_root))
    try:
        JsonRpcServer(facade).serve(reader, writer)
    finally:
        facade.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
