from __future__ import annotations

import argparse
import logging
from pathlib import Path

from reorder_engine.infrastructure.command_runner import ExternalCommandRunner
from reorder_engine.infrastructure.sevenzip_bootstrap import SevenZipBootstrapper
from reorder_engine.services.beta_pipeline import BetaFolderPipeline
from reorder_engine.services.config import ConfigManager
from reorder_engine.services.decrypting import DecryptionService, PassthroughDecryptor
from reorder_engine.services.extracting import BandizipExtractor, ExtractionService, SevenZipExtractor, UnrarExtractor
from reorder_engine.services.flattening import FolderFlattener, flatten_safety_check
from reorder_engine.services.grouping import DefaultVolumeGroupingStrategy
from reorder_engine.services.cleaning import DefaultGroupingNormalizer
from reorder_engine.services.keywords import KeywordRepository
from reorder_engine.services.passwords import PasswordRepository
from reorder_engine.services.restoring import (
    ApateRestorer,
    ArchiveSignatureInspector,
    RepeatedApateRestorer,
    RestorationService,
    SuffixVariantBuilder,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reorder_engine.beta", description="Beta extraction pipeline.")
    parser.add_argument("--folder", default=None, help="Target folder to process. Defaults to current working directory.")
    parser.add_argument("--workdir", default=None, help="Project workdir that holds config/tools/resources.")
    parser.add_argument("--config", default=None, help="Optional config file path.")
    parser.add_argument("--no-flatten", action="store_true", help="Do not flatten nested files into the target folder.")
    parser.add_argument("--allow-flatten-in-project", action="store_true", help="Allow flattening inside the repository checkout.")
    parser.add_argument("--dry-run", action="store_true", help="Plan only. Do not move, copy, or extract files.")
    parser.add_argument("--self-check", action="store_true", help="Probe configured command-line tools and print their status.")
    parser.add_argument("--log", default=None, help="Main log file path.")
    parser.add_argument("--tool-log", default=None, help="Tool output log file path.")
    parser.add_argument("--deep-extract", action="store_true", help="Enable recursive nested extraction.")
    parser.add_argument("--deep-max-depth", type=int, default=None, help="Override recursive extraction depth.")
    parser.add_argument("--deep-min-archive-mb", type=int, default=None, help="Override nested archive minimum size in MB.")
    parser.add_argument("--deep-final-single-mb", type=int, default=None, help="Override large file fallback threshold in MB.")
    parser.add_argument("--disable-bandizip", action="store_true", help="Do not use Bandizip even if configured.")
    parser.add_argument("--preserve-payload-names", action="store_true", help="Keep final payload names unchanged.")
    parser.add_argument("--archive-mode", default="wide", help="Compatibility flag for existing BAT launchers.")
    parser.add_argument("--archive-min-mb", type=int, default=100, help="Minimum size in MB for generic archive candidates.")
    parser.add_argument("--deep-mode", default="smart", help="Compatibility flag for existing BAT launchers.")
    parser.add_argument("--deep-max-candidates", type=int, default=5, help="Compatibility flag for existing BAT launchers.")
    parser.add_argument("--cleanup-dynamic-libs", action="store_true", help="Compatibility flag for existing BAT launchers.")
    parser.add_argument("--dynamic-lib-patterns", default="", help="Compatibility flag for existing BAT launchers.")
    return parser


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
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    tool_file_handler = logging.FileHandler(tool_log_path, encoding="utf-8")
    tool_file_handler.setFormatter(formatter)
    tool_stream_handler = logging.StreamHandler()
    tool_stream_handler.setFormatter(formatter)
    tool_logger.addHandler(tool_file_handler)
    tool_logger.addHandler(tool_stream_handler)

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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    folder = Path(args.folder) if args.folder else Path.cwd()
    workdir = Path(args.workdir) if args.workdir else Path.cwd()
    config_path = Path(args.config) if args.config else (workdir / "config.json")

    log_path = Path(args.log) if args.log else (folder / "reorder_engine.log")
    tool_log_path = Path(args.tool_log) if args.tool_log else (folder / "reorder_engine.tools.log")
    logger, tool_logger = _setup_logging(folder, log_path, tool_log_path)

    logger.info("START: folder=%s workdir=%s config=%s dry_run=%s", folder, workdir, config_path, bool(args.dry_run))
    cfg_mgr = ConfigManager(config_path, root_dir=workdir)
    cfg_mgr.load_or_create_default()
    cfg = cfg_mgr.to_app_config()

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
        )

    def sink(line: str) -> None:
        tool_logger.info("TOOL: %s", line)

    runner = ExternalCommandRunner(stream=not bool(args.dry_run), line_sink=sink)

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

    ensure = SevenZipBootstrapper().ensure(cfg, cfg_mgr)
    if args.self_check:
        if not ensure.ok:
            logger.warning("%s", ensure.message)
        _self_check(folder, cfg, runner=runner, seven_zip_exe=ensure.exe)

    if not ensure.ok:
        logger.error("%s", ensure.message)
        return 3

    inspector = ArchiveSignatureInspector()
    restore_service = RestorationService(
        [
            ApateRestorer(inspector),
            RepeatedApateRestorer(inspector, rounds=cfg.beta.rules.max_restore_rounds),
            SuffixVariantBuilder(inspector),
        ],
        inspector=inspector,
    )

    extractors = _build_extractors(cfg, runner, ensure.exe, disable_bandizip=bool(args.disable_bandizip))
    extraction_service = ExtractionService(extractors=extractors)

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
    )

    result = pipeline.run(dry_run=bool(args.dry_run))
    logger.info("DONE: ok=%s fail=%s total=%s", result.ok_count, result.fail_count, result.total)
    return 0 if result.fail_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
