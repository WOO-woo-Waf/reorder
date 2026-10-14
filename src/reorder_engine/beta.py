from __future__ import annotations

import argparse
import logging
import uuid
from pathlib import Path
from dataclasses import replace

from reorder_engine.infrastructure.command_runner import ExternalCommandRunner
from reorder_engine.application.errors import EngineError
from reorder_engine.services.beta_pipeline import BetaFolderPipeline
from reorder_engine.services.config import ConfigManager
from reorder_engine.services.decrypting import DecryptionService, PassthroughDecryptor
from reorder_engine.domain.models import ExtractionRequest
from reorder_engine.services.extracting import BandizipExtractor, ExtractionService, SevenZipExtractor, UnrarExtractor
from reorder_engine.services.flattening import FolderFlattener, flatten_safety_check
from reorder_engine.services.grouping import DefaultVolumeGroupingStrategy
from reorder_engine.services.cleaning import DefaultGroupingNormalizer
from reorder_engine.infrastructure.tool_bootstrap import ExtractToolBootstrapper
from reorder_engine.services.keywords import KeywordRepository
from reorder_engine.services.passwords import PasswordRepository
from reorder_engine.services.restoring import (
    ApateRestorer,
    ArchiveSignatureInspector,
    EmbeddedArchiveRestorer,
    RestorationService,
    SuffixVariantBuilder,
)
from reorder_engine.services.restore_ab import RestoreABRestorer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reorder_engine.beta", description="Beta extraction pipeline.")
    parser.add_argument("--folder", default=None, help="Target folder to process. Defaults to current working directory.")
    parser.add_argument("--workdir", default=None, help="Project workdir that holds config/tools/resources.")
    parser.add_argument("--config", default=None, help="Optional config file path.")
    parser.add_argument("--no-flatten", action="store_true", help="Do not flatten nested files into the target folder.")
    parser.add_argument("--allow-flatten-in-project", action="store_true", help="Allow flattening inside the repository checkout.")
    parser.add_argument("--dry-run", action="store_true", help="Plan only. Do not move, copy, or extract files.")
    parser.add_argument("--self-check", action="store_true", help="Probe configured command-line tools and print their status.")
    parser.add_argument("--prepare-tools", action="store_true", help="Prepare configured Windows extraction tools and exit.")
    parser.add_argument("--log", default=None, help="Main log file path.")
    parser.add_argument("--tool-log", default=None, help="Tool output log file path.")
    parser.add_argument("--deep-extract", action="store_true", help="Enable recursive nested extraction.")
    parser.add_argument("--deep-max-depth", type=int, default=None, help="Override recursive extraction depth.")
    parser.add_argument("--deep-min-archive-mb", type=int, default=None, help="Override nested archive minimum size in MB.")
    parser.add_argument("--deep-final-single-mb", type=int, default=None, help="Override large file fallback threshold in MB.")
    parser.add_argument("--disable-bandizip", action="store_true", help="Do not use Bandizip even if configured.")
    parser.add_argument("--preserve-payload-names", action="store_true", help="Keep final payload names unchanged.")
    parser.add_argument("--archive-mode", choices=("wide",), default="wide", help="Archive discovery policy supported by this CLI.")
    parser.add_argument("--archive-min-mb", type=int, default=100, help="Minimum size in MB for generic archive candidates.")
    parser.add_argument("--deep-mode", choices=("basic", "smart"), default=None, help="Nested archive candidate policy.")
    parser.add_argument("--deep-max-candidates", type=_positive_int, default=None, help="Maximum nested candidates to try per layer in smart mode.")
    parser.add_argument("--cleanup-dynamic-libs", action="store_true", help="Remove matching DLLs produced during this run before publishing a complete payload.")
    parser.add_argument(
        "--dynamic-lib-patterns",
        default="*dynamic*config*.dll;*dyn*config*.dll;*动态*配置*.dll",
        help="Semicolon-separated filename globs used only with --cleanup-dynamic-libs.",
    )
    return parser


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _tool_runtime_paths(folder: Path) -> tuple[Path, dict[str, str]]:
    tool_tmp_dir = folder / "intermediate" / "tool_tmp" / f"run_{uuid.uuid4().hex}"
    if not tool_tmp_dir.resolve().is_relative_to(folder.resolve()):
        raise EngineError("UNSAFE_WORKSPACE", "中间目录指向工作文件夹外部，请调整目录后重试。")
    return tool_tmp_dir, {
        "TEMP": str(tool_tmp_dir),
        "TMP": str(tool_tmp_dir),
        "TMPDIR": str(tool_tmp_dir),
    }


def _ensure_tool_runtime_dir(tool_tmp_dir: Path, *, dry_run: bool) -> None:
    if not dry_run:
        tool_tmp_dir.mkdir(parents=True, exist_ok=False)


class _ConsoleProgressFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "PIPE:   msg:" in message:
            return False
        if "\n" in message and ("ERROR-FILE" in message or "ERROR-PARTIAL" in message):
            return False
        return True


def _setup_logging(folder: Path, log_path: Path, tool_log_path: Path) -> tuple[logging.Logger, logging.Logger]:
    folder.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("reorder_engine")
    logger.setLevel(logging.INFO)
    tool_logger = logging.getLogger("reorder_engine.tool")
    tool_logger.setLevel(logging.INFO)
    tool_logger.propagate = False

    if getattr(logger, "_configured", False) and getattr(tool_logger, "_configured", False):
        return logger, tool_logger

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(_ConsoleProgressFilter())
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    tool_file_handler = logging.FileHandler(tool_log_path, encoding="utf-8")
    tool_file_handler.setFormatter(formatter)
    tool_logger.addHandler(tool_file_handler)

    setattr(logger, "_configured", True)
    setattr(tool_logger, "_configured", True)
    return logger, tool_logger


def _print_tool_probe(title: str, exe: Path | None, result) -> None:
    status = "OK" if result is not None else "MISSING"
    exe_text = str(exe) if exe else "<none>"
    print(f"SELF-CHECK[{status}] {title}: {exe_text}")
    if result is None:
        return
    output = (result.stdout or result.stderr or "").strip()
    for line in output.splitlines()[:6]:
        print(f"  {line}")


def _self_check(folder: Path, cfg, *, runner: ExternalCommandRunner, seven_zip_exe: Path | None) -> None:
    print("SELF-CHECK: begin")
    if cfg.tools.bandizip.exe and cfg.tools.bandizip.exe.exists():
        _print_tool_probe("bandizip(bz)", cfg.tools.bandizip.exe, runner.run([str(cfg.tools.bandizip.exe)], cwd=folder))
    else:
        _print_tool_probe("bandizip(bz)", cfg.tools.bandizip.exe, None)

    if cfg.tools.unrar.exe and cfg.tools.unrar.exe.exists():
        _print_tool_probe("rar/unrar", cfg.tools.unrar.exe, runner.run([str(cfg.tools.unrar.exe)], cwd=folder))
    else:
        _print_tool_probe("rar/unrar", cfg.tools.unrar.exe, None)

    if seven_zip_exe and seven_zip_exe.exists():
        _print_tool_probe("7z", seven_zip_exe, runner.run([str(seven_zip_exe), "i"], cwd=folder))
    else:
        _print_tool_probe("7z", seven_zip_exe, None)
    print("SELF-CHECK: end")


def _build_extractors(cfg, runner: ExternalCommandRunner, seven_zip_exe: Path | None, *, disable_bandizip: bool) -> list:
    available = {
        "7z": SevenZipExtractor(runner, exe=str(seven_zip_exe) if seven_zip_exe else None),
        "unrar": UnrarExtractor(runner, exe=str(cfg.tools.unrar.exe) if cfg.tools.unrar.exe else None),
        "bandizip": BandizipExtractor(runner, exe=str(cfg.tools.bandizip.exe) if cfg.tools.bandizip.exe else None),
    }
    ordered: list = []
    for name in cfg.beta.extractor_order:
        key = name.lower().strip()
        if key == "bandizip" and disable_bandizip:
            continue
        extractor = available.get(key)
        if extractor is not None:
            ordered.append(extractor)
    fallback_order = ["7z", "unrar", "bandizip"]
    for key in fallback_order:
        if key == "bandizip" and disable_bandizip:
            continue
        extractor = available.get(key)
        if extractor is None or extractor in ordered:
            continue
        ordered.append(extractor)
    return ordered


def _should_abort_tool_line(line: str) -> bool:
    normalized = line.lower()
    return any(
        token in normalized
        for token in (
            "wrong password",
            "password is incorrect",
            "incorrect password",
            "invalid password",
            "illegal password",
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    invocation_dir = Path.cwd()
    folder = (Path(args.folder) if args.folder else invocation_dir).resolve()
    workdir = (Path(args.workdir) if args.workdir else invocation_dir).resolve()
    if args.config:
        config_arg = Path(args.config)
        config_path = (config_arg if config_arg.is_absolute() else workdir / config_arg).resolve()
    else:
        config_path = (workdir / "config.json").resolve()

    cfg_mgr = ConfigManager(config_path, root_dir=workdir)
    cfg_mgr.load_or_create_default()
    cfg = cfg_mgr.to_app_config()

    tool_tmp_dir, tool_env = _tool_runtime_paths(folder)
    runner = ExternalCommandRunner(
        stream=not bool(args.dry_run),
        line_sink=None,
        abort_on_line=_should_abort_tool_line,
        cwd=tool_tmp_dir,
        env=tool_env,
    )
    if args.prepare_tools:
        summary = ExtractToolBootstrapper().ensure_all(cfg, cfg_mgr)
        cfg = cfg_mgr.to_app_config()
        for result in summary.results:
            status = "OK" if result.ok else ("REQUIRED-MISSING" if result.required else "OPTIONAL-MISSING")
            exe_text = str(result.exe) if result.exe else "<none>"
            print(f"PREPARE-TOOLS[{status}] {result.name}: {exe_text} ({result.message})")
        if args.self_check and summary.required_ok:
            _ensure_tool_runtime_dir(tool_tmp_dir, dry_run=bool(args.dry_run))
            seven = summary.get("7z")
            _self_check(folder, cfg, runner=runner, seven_zip_exe=seven.exe if seven else None)
        return 0 if summary.required_ok else 3

    _ensure_tool_runtime_dir(tool_tmp_dir, dry_run=bool(args.dry_run))

    log_path = (Path(args.log) if args.log else (folder / "reorder_engine.log")).resolve()
    tool_log_path = (Path(args.tool_log) if args.tool_log else (folder / "reorder_engine.tools.log")).resolve()
    logger, tool_logger = _setup_logging(folder, log_path, tool_log_path)

    logger.info("START: folder=%s workdir=%s config=%s dry_run=%s", folder, workdir, config_path, bool(args.dry_run))

    _ = KeywordRepository().load(cfg.paths.keywords)
    passwords = PasswordRepository().load(cfg.paths.passwords)
    logger.info("CONFIG: passwords_file=%s passwords_count=%s", cfg.paths.passwords, len(passwords.passwords))

    deep_cfg = cfg.beta.deep_extract
    if args.deep_extract:
        deep_cfg = type(deep_cfg)(
            enabled=True,
            max_depth=args.deep_max_depth if args.deep_max_depth is not None else deep_cfg.max_depth,
            min_archive_mb=args.deep_min_archive_mb if args.deep_min_archive_mb is not None else deep_cfg.min_archive_mb,
            final_single_mb=args.deep_final_single_mb if args.deep_final_single_mb is not None else deep_cfg.final_single_mb,
            mode=args.deep_mode if args.deep_mode is not None else deep_cfg.mode,
            max_candidates=args.deep_max_candidates if args.deep_max_candidates is not None else deep_cfg.max_candidates,
        )
    elif args.deep_mode is not None or args.deep_max_candidates is not None:
        deep_cfg = replace(
            deep_cfg,
            mode=args.deep_mode if args.deep_mode is not None else deep_cfg.mode,
            max_candidates=args.deep_max_candidates if args.deep_max_candidates is not None else deep_cfg.max_candidates,
        )

    def sink(line: str) -> None:
        tool_logger.info("TOOL: %s", line)

    runner = ExternalCommandRunner(
        stream=not bool(args.dry_run),
        line_sink=sink,
        abort_on_line=_should_abort_tool_line,
        cwd=tool_tmp_dir,
        env=tool_env,
    )

    tool_summary = ExtractToolBootstrapper().ensure_all(cfg, cfg_mgr)
    cfg = cfg_mgr.to_app_config()
    if not tool_summary.required_ok:
        failures = [result for result in tool_summary.results if result.required and not result.ok]
        if not failures:
            message = "Required extraction tools are unavailable."
            print(f"REQUIRED-TOOL-MISSING: {message}")
            logger.error("%s", message)
        for failure in failures:
            message = f"REQUIRED-TOOL-MISSING[{failure.name}]: {failure.message}"
            print(message)
            logger.error("%s", message)
        return 3
    ensure = tool_summary.get("7z")
    if args.self_check:
        if ensure and not ensure.ok:
            logger.warning("%s", ensure.message)
        _self_check(folder, cfg, runner=runner, seven_zip_exe=ensure.exe if ensure else None)

    if not ensure or not ensure.ok:
        logger.error("%s", ensure.message if ensure else "7z not checked")
        return 3

    if cfg.beta.flatten.enabled and not args.no_flatten:
        allow_inside = bool(cfg.beta.flatten.allow_inside_project_repo) or bool(args.allow_flatten_in_project)
        skip_reason = flatten_safety_check(
            folder,
            allowed_roots=cfg.beta.flatten.allowed_roots,
            allow_inside_project_repo=allow_inside,
        )
        if skip_reason:
            logger.warning("FLATTEN: %s", skip_reason)
        else:
            moves = FolderFlattener().flatten(folder, dry_run=bool(args.dry_run), exclude_dirs=set(cfg.beta.flatten.exclude_dirs))
            logger.info("FLATTEN: moved=%s", len(moves))

    inspector = ArchiveSignatureInspector()
    restore_service = RestorationService(
        [
            RestoreABRestorer(in_place=True),
            ApateRestorer(inspector, rounds=cfg.beta.rules.max_restore_rounds),
            EmbeddedArchiveRestorer(inspector),
            SuffixVariantBuilder(inspector),
        ],
        inspector=inspector,
    )

    extractors = _build_extractors(cfg, runner, ensure.exe, disable_bandizip=bool(args.disable_bandizip))
    def attempt_sink(tool: str, request: ExtractionRequest, password: str | None) -> None:
        password_text = password if password else "<none>"
        logger.info(
            "TRY: entry=%s method=%s tool=%s password=%s output=%s",
            request.volume_set.entry.name,
            request.method,
            tool,
            password_text,
            request.output_dir,
        )

    extraction_service = ExtractionService(extractors=extractors, attempt_sink=attempt_sink)

    pipeline = BetaFolderPipeline(
        folder=folder,
        decrypt_service=DecryptionService([PassthroughDecryptor()]),
        restore_service=restore_service,
        grouper=DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()),
        extractor=extraction_service,
        passwords=passwords.passwords,
        exclude_names=set(cfg.beta.exclude.names) | {log_path.name, tool_log_path.name, "reorder_engine.bat.log"},
        exclude_exts=set(cfg.beta.exclude.exts),
        log_passwords=bool(cfg.beta.log_passwords),
        deep_extract=deep_cfg,
        emit=lambda message: logger.info("PIPE: %s", message),
        duplicates_dir_name=cfg.beta.duplicates_dir_name,
        preserve_payload_names=bool(args.preserve_payload_names or cfg.beta.preserve_payload_names),
        path_compress=cfg.beta.path_compress,
        archive_min_mb=max(1, int(args.archive_min_mb)),
        cleanup_dynamic_libs=bool(args.cleanup_dynamic_libs),
        dynamic_lib_patterns=tuple(pattern.strip() for pattern in args.dynamic_lib_patterns.split(";") if pattern.strip()),
    )

    result = pipeline.run(dry_run=bool(args.dry_run))
    logger.info("DONE: ok=%s fail=%s total=%s", result.ok_count, result.fail_count, result.total)
    return 0 if result.fail_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
