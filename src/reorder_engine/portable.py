from __future__ import annotations

import sys
from pathlib import Path

from reorder_engine.beta import main as beta_main


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _looks_like_option(value: str) -> bool:
    return value.startswith("-") or value.startswith("/")


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    app_dir = _app_dir()
    value_options = {
        "--folder",
        "-f",
        "--workdir",
        "--config",
        "--log",
        "--tool-log",
        "--deep-max-depth",
        "--deep-min-archive-mb",
        "--deep-final-single-mb",
        "--archive-mode",
        "--archive-min-mb",
        "--deep-mode",
        "--deep-max-candidates",
        "--dynamic-lib-patterns",
    }

    beta_args: list[str] = []
    has_folder = False
    has_workdir = False
    has_log = False
    has_tool_log = False
    has_deep_extract = False
    has_preserve_payload_names = False

    index = 0
    while index < len(raw_args):
        arg = raw_args[index]
        if arg == "--":
            beta_args.extend(raw_args[index + 1 :])
            break
        if arg in {"--folder", "-f"}:
            has_folder = True
            beta_args.append("--folder")
            if index + 1 < len(raw_args):
                beta_args.append(raw_args[index + 1])
                index += 2
                continue
        elif arg.startswith("--folder="):
            has_folder = True
            beta_args.append(arg)
            index += 1
            continue
        elif arg == "--workdir":
            has_workdir = True
        elif arg.startswith("--workdir="):
            has_workdir = True
        elif arg == "--log":
            has_log = True
        elif arg.startswith("--log="):
            has_log = True
        elif arg == "--tool-log":
            has_tool_log = True
        elif arg.startswith("--tool-log="):
            has_tool_log = True
        elif arg == "--deep-extract":
            has_deep_extract = True
        elif arg == "--preserve-payload-names":
            has_preserve_payload_names = True
        elif not _looks_like_option(arg) and not has_folder and Path(arg).exists():
            has_folder = True
            beta_args.extend(["--folder", arg])
            index += 1
            continue

        beta_args.append(arg)
        if arg in value_options and index + 1 < len(raw_args):
            beta_args.append(raw_args[index + 1])
            index += 2
            continue
        index += 1

    target_dir = Path.cwd()
    if not has_folder:
        target_dir = app_dir
        beta_args.extend(["--folder", str(target_dir)])

    if has_folder:
        folder_value = None
        for index, arg in enumerate(beta_args):
            if arg == "--folder" and index + 1 < len(beta_args):
                folder_value = beta_args[index + 1]
                break
            if arg.startswith("--folder="):
                folder_value = arg.split("=", 1)[1]
                break
        if folder_value:
            target_dir = Path(folder_value)

    if not has_workdir:
        beta_args.extend(["--workdir", str(app_dir)])
    if not has_log:
        beta_args.extend(["--log", str(target_dir / "reorder_engine.extract.log")])
    if not has_tool_log:
        beta_args.extend(["--tool-log", str(target_dir / "reorder_engine.extract.tools.log")])
    if not has_deep_extract:
        beta_args.append("--deep-extract")
    if not has_preserve_payload_names:
        beta_args.append("--preserve-payload-names")

    return beta_main(beta_args)


if __name__ == "__main__":
    raise SystemExit(main())
