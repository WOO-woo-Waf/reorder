from __future__ import annotations

import argparse
import logging
from pathlib import Path

from reorder_engine.infrastructure.command_runner import ExternalCommandRunner
from reorder_engine.infrastructure.sevenzip_bootstrap import SevenZipBootstrapper
from reorder_engine.services.cleaning import (
    BasicPunctuationCleaner,
    CleaningContext,
    DefaultGroupingNormalizer,
    FilenameCleaningService,
    KeywordStripCleaner,
    SafeRenamer,
    TailIndexCleaner,
)
from reorder_engine.services.config import ConfigManager
from reorder_engine.services.decrypting import DecryptionService, PassthroughDecryptor
from reorder_engine.services.extracting import BandizipExtractor, ExtractionService, SevenZipExtractor, UnrarExtractor
from reorder_engine.services.grouping import DefaultVolumeGroupingStrategy
from reorder_engine.services.keywords import KeywordRepository
from reorder_engine.services.passwords import PasswordRepository
from reorder_engine.services.restoring import PassthroughRestorer, RestorationService
from reorder_engine.services.beta_pipeline import BetaFolderPipeline
from reorder_engine.services.flattening import FolderFlattener


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reorder_engine.beta", description="归序 Beta：对 bat 所在目录就地解压并分流")
    p.add_argument("--folder", default=None, help="要处理的目录（默认当前进程工作目录）")
    p.add_argument(
        "--workdir",
        default=None,
        help="工作目录（固定放 config/tools/keywords/passwords；默认当前进程工作目录）",
    )
    p.add_argument("--config", default=None, help="配置文件路径（默认 <workdir>/config.json）")
    p.add_argument("--no-flatten", action="store_true", help="不展平子目录文件（默认会展平）")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不实际改名/解压/移动")
    p.add_argument("--self-check", action="store_true", help="启动时自检：打印 7z/WinRAR/UnRAR/Bandizip 可用性与版本/帮助")
    p.add_argument("--log", default=None, help="日志文件路径（默认 <folder>/reorder_engine.log）")
    p.add_argument(
        "--tool-log",
        default=None,
        help="外部工具输出日志路径（默认 <folder>/reorder_engine.tools.log；主 log 会更简短）",
    )
    return p


def _setup_logging(folder: Path, log_path: Path, tool_log_path: Path) -> tuple[logging.Logger, logging.Logger]:
    folder.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("reorder_engine")
    logger.setLevel(logging.INFO)

    tool_logger = logging.getLogger("reorder_engine.tool")
    tool_logger.setLevel(logging.INFO)
    # 防止 tool logger 传播到父 logger（否则终端/主日志会重复一份）
    tool_logger.propagate = False

    # 避免重复添加 handler（例如在同一进程多次调用 main）
    if getattr(logger, "_configured", False) and getattr(tool_logger, "_configured", False):
        return logger, tool_logger

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)

    tfh = logging.FileHandler(tool_log_path, encoding="utf-8")
    tfh.setFormatter(fmt)
    tsh = logging.StreamHandler()
    tsh.setFormatter(fmt)
    tool_logger.addHandler(tfh)
    tool_logger.addHandler(tsh)

    setattr(logger, "_configured", True)
    setattr(tool_logger, "_configured", True)
    logger.info("LOG: file=%s", log_path)
    logger.info("LOG: tool_file=%s", tool_log_path)
    return logger, tool_logger


def _print_tool_probe(title: str, exe: Path | None, res) -> None:
    status = "OK" if res is not None else "MISSING"
    exe_s = str(exe) if exe else "<none>"
    print(f"SELF-CHECK[{status}] {title}: {exe_s}")
    if res is None:
        return
    out = (res.stdout or "").strip()
    err = (res.stderr or "").strip()
    head = out.splitlines()[:6] if out else []
    if not head and err:
        head = err.splitlines()[:6]
    if head:
        for line in head:
            print(f"  {line}")


def _print_tool_found(title: str, exe: Path | None) -> None:
    exe_s = str(exe) if exe else "<none>"
    status = "FOUND" if exe else "MISSING"
    print(f"SELF-CHECK[{status}] {title}: {exe_s}")


def _self_check(folder: Path, cfg_mgr: ConfigManager, cfg, *, runner: ExternalCommandRunner, seven_zip_exe: Path | None) -> None:
    # 尝试把用户给定的安装目录写回 config（仅当当前配置为空且文件存在时）。
    if not cfg.tools.bandizip.exe:
        bz = Path(r"D:\\bandzip\\bz.exe")
        if bz.exists():
            cfg_mgr.set_bandizip_exe(bz)
            cfg_mgr.save()
            cfg = cfg_mgr.to_app_config()

    if not cfg.tools.unrar.exe:
        rar_dir = Path(r"D:\\RAR")
        for candidate in ("Rar.exe", "UnRAR.exe"):
            p = rar_dir / candidate
            if p.exists():
                cfg_mgr.set_unrar_exe(p)
                cfg_mgr.save()
                cfg = cfg_mgr.to_app_config()
                break

    print("SELF-CHECK: begin")

    # Bandizip bz.exe：不带参数会输出 usage（部分版本 -h 会报 Parameter Parsing Error）
    bz_exe = cfg.tools.bandizip.exe
    if bz_exe and bz_exe.exists():
        res_bz = runner.run([str(bz_exe)], cwd=folder)
    else:
        res_bz = None
    _print_tool_probe("bandizip(bz)", bz_exe, res_bz)

    # WinRAR 家族：unrar/rar/winrar 都可能在同目录
    unrar_exe = cfg.tools.unrar.exe
    if unrar_exe and unrar_exe.exists():
        res_unrar = runner.run([str(unrar_exe)], cwd=folder)
    else:
        res_unrar = None
    _print_tool_probe("unrar/rar/winrar", unrar_exe, res_unrar)

    # 如果同目录存在 Rar.exe / WinRAR.exe，也顺便探测一下（不改 config schema）
    if unrar_exe and unrar_exe.exists():
        base_dir = unrar_exe.parent
        rar_exe = base_dir / "Rar.exe"
        if rar_exe.exists() and rar_exe != unrar_exe:
            _print_tool_probe("rar", rar_exe, runner.run([str(rar_exe)], cwd=folder))
        winrar_exe = base_dir / "WinRAR.exe"
        # WinRAR.exe 是 GUI 程序：不执行它，只提示存在（避免弹窗导致脚本卡住）
        if winrar_exe.exists():
            _print_tool_found("winrar", winrar_exe)

    # 7z：用 `i` 打印信息（比 -h 更稳定）
    if seven_zip_exe and seven_zip_exe.exists():
        res_7z = runner.run([str(seven_zip_exe), "i"], cwd=folder)
    else:
        res_7z = None
    _print_tool_probe("7z", seven_zip_exe, res_7z)

    print("SELF-CHECK: end")


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
    logger.info("CONFIG: seven_zip.exe=%s auto_download=%s", cfg.tools.seven_zip.exe, cfg.tools.seven_zip.auto_download)
    logger.info("CONFIG: bandizip.exe=%s unrar.exe=%s", cfg.tools.bandizip.exe, cfg.tools.unrar.exe)

    # libs
    keywords = KeywordRepository().load(cfg.paths.keywords)
    passwords = PasswordRepository().load(cfg.paths.passwords)
    logger.info("CONFIG: passwords_file=%s passwords_count=%s", cfg.paths.passwords, len(passwords.passwords))

    # cleaners
    ctx = CleaningContext(keywords=keywords)
    cleaning_service = FilenameCleaningService(
        cleaners=[
            BasicPunctuationCleaner(),
            TailIndexCleaner(),
            KeywordStripCleaner(ctx),
            BasicPunctuationCleaner(),
        ]
    )
    renamer = SafeRenamer()

    # stage1 decrypt/prepare + restore
    decrypt_service = DecryptionService([PassthroughDecryptor()])
    restore_service = RestorationService([PassthroughRestorer()])

    def sink(line: str) -> None:
        # 外部工具输出写到独立 log（主 log 更简短），并同时显示到终端
        tool_logger.info("TOOL: %s", line)

    # Beta 目标之一：把外部解压工具的完整输出直接显示在终端里，并写入日志。
    runner = ExternalCommandRunner(stream=not bool(args.dry_run), line_sink=sink)

    if cfg.beta.flatten.enabled and not args.no_flatten:
        moves = FolderFlattener().flatten(
            folder,
            dry_run=bool(args.dry_run),
            exclude_dirs=set(cfg.beta.flatten.exclude_dirs),
        )
        if moves:
            logger.info("FLATTEN: moved %s files into %s", len(moves), folder)

    if args.self_check:
        # self-check 不应该因为 7z 缺失而提前退出：先检查已配置工具，再尝试确保 7z。
        ensure_for_check = SevenZipBootstrapper().ensure(cfg, cfg_mgr)
        if not ensure_for_check.ok:
            logger.warning("%s", ensure_for_check.message)
        _self_check(folder, cfg_mgr, cfg, runner=runner, seven_zip_exe=ensure_for_check.exe)
        # self-check 可能写回配置，刷新一次
        cfg = cfg_mgr.to_app_config()

    # ensure 7z exists (download if needed)
    ensure = SevenZipBootstrapper().ensure(cfg, cfg_mgr)
    if not ensure.ok:
        logger.error("%s", ensure.message)
        return 3

    extractors = [SevenZipExtractor(runner, exe=str(ensure.exe) if ensure.exe else None)]
    # beta 保留可扩展：如果你未来在 config.json 配了 unrar/bandizip，就加入轮询
    if cfg.tools.unrar.exe:
        extractors.append(UnrarExtractor(runner, exe=str(cfg.tools.unrar.exe)))
    if cfg.tools.bandizip.exe:
        extractors.append(BandizipExtractor(runner, exe=str(cfg.tools.bandizip.exe)))

    extraction_service = ExtractionService(extractors=extractors)

    pipeline = BetaFolderPipeline(
        folder=folder,
        cleaning_service=cleaning_service,
        renamer=renamer,
        decrypt_service=decrypt_service,
        restore_service=restore_service,
        grouper=DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()),
        extractor=extraction_service,
        passwords=passwords.passwords,
        exclude_names=set(cfg.beta.exclude.names) | {log_path.name, "reorder_engine.bat.log"},
        exclude_exts=set(cfg.beta.exclude.exts),
        guess_suffixes=list(cfg.beta.guess_suffixes),
        log_passwords=bool(cfg.beta.log_passwords),
        deep_extract=cfg.beta.deep_extract,
        emit=lambda s: logger.info("PIPE: %s", s),
    )

    result = pipeline.run(dry_run=bool(args.dry_run))
    logger.info("DONE: ok=%s fail=%s total=%s", result.ok_count, result.fail_count, result.total)
    return 0 if result.fail_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
