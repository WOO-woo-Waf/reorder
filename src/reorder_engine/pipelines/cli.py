from __future__ import annotations

import argparse
from pathlib import Path

from reorder_engine.domain.models import ExtractionRequest
from reorder_engine.domain.models import PipelineOptions
from reorder_engine.infrastructure.sevenzip_bootstrap import SevenZipBootstrapper
from reorder_engine.infrastructure.command_runner import ExternalCommandRunner
from reorder_engine.services.cleaning import (
    BasicPunctuationCleaner,
    CleaningContext,
    DefaultGroupingNormalizer,
    FilenameCleaningService,
    KeywordStripCleaner,
    SafeRenamer,
    TailIndexCleaner,
)
from reorder_engine.services.discovery import ArchiveDiscoveryService
from reorder_engine.services.decrypting import DecryptionService, PassthroughDecryptor
from reorder_engine.services.extracting import ExtractionService, SevenZipExtractor
from reorder_engine.services.grouping import DefaultVolumeGroupingStrategy
from reorder_engine.services.archive_naming import split_archive_name
from reorder_engine.services.keywords import KeywordRepository
from reorder_engine.services.passwords import PasswordRepository
from reorder_engine.services.restoring import PassthroughRestorer, RestorationService
from reorder_engine.services.config import ConfigManager


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reorder_engine", description="归序（ReOrder Engine）：批量清洗命名并解压")
    p.add_argument("--config", default=None, help="配置文件路径（默认项目根目录 config.json）")
    p.add_argument("--input", required=True, help="输入目录（包含压缩包）")
    p.add_argument("--output", required=True, help="输出目录（解压目标）")
    p.add_argument("--keywords", default=None, help="关键字库（覆盖 config.json）")
    p.add_argument("--passwords", default=None, help="密码库（覆盖 config.json）")
    p.add_argument("--tool", default="auto", choices=["auto", "7z"], help="解压工具选择")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不实际改名/解压")
    p.add_argument("--no-recursive", action="store_true", help="不递归扫描")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    project_root = Path(__file__).resolve().parents[3]
    config_path = Path(args.config) if args.config else (project_root / "config.json")
    cfg_mgr = ConfigManager(config_path, root_dir=project_root)
    cfg_mgr.load_or_create_default()
    cfg = cfg_mgr.to_app_config()

    keywords_path = Path(args.keywords) if args.keywords else cfg.paths.keywords
    passwords_path = Path(args.passwords) if args.passwords else cfg.paths.passwords

    opts = PipelineOptions(
        input_dir=Path(args.input),
        output_dir=Path(args.output),
        keyword_file=keywords_path,
        # passwords 走单独 repository，不塞进 PipelineOptions，避免扩散
        tool_preference=args.tool,
        dry_run=bool(args.dry_run),
        recursive=not bool(args.no_recursive),
    )

    # Stage 1: keywords + cleaning
    lib = KeywordRepository().load(opts.keyword_file)
    pw_lib = PasswordRepository().load(passwords_path)
    ctx = CleaningContext(keywords=lib)
    cleaning_service = FilenameCleaningService(
        cleaners=[
            BasicPunctuationCleaner(),
            TailIndexCleaner(),
            KeywordStripCleaner(ctx),
            BasicPunctuationCleaner(),
        ]
    )
    renamer = SafeRenamer()

    # Discover
    discovery = ArchiveDiscoveryService()
    candidates = discovery.discover(opts.input_dir, recursive=opts.recursive)
    if not candidates:
        print("No archive candidates found.")
        return 0

    # Rename (stem only, keep suffix)
    rename_count = 0
    renamed_paths: list[Path] = []
    for p in sorted(candidates, key=lambda x: x.name.lower()):
        parts = split_archive_name(p.name)
        cleaned_base = cleaning_service.clean_stem(parts.base)
        dst = p.with_name(f"{cleaned_base}{parts.mid}{parts.end}")
        rec = renamer.rename_file(p, dst, dry_run=opts.dry_run)
        if rec is None:
            renamed_paths.append(p)
            continue
        rename_count += 1
        renamed_paths.append(rec.after)
        print(f"RENAME: {rec.before.name} -> {rec.after.name}")

    print(f"Renamed: {rename_count}")

    # Stage 1 (optional): decrypt/prepare (v0.1 默认直通)
    decrypt_service = DecryptionService([PassthroughDecryptor()])
    prepared_paths: list[Path] = []
    for p in renamed_paths:
        prepared_paths.extend(decrypt_service.prepare(p, dry_run=opts.dry_run))

    # Stage 1.5 (optional): restore/repair (v0.1 默认直通)
    restore_service = RestorationService([PassthroughRestorer()])
    restored_paths: list[Path] = []
    for p in prepared_paths:
        restored_paths.extend(restore_service.restore(p, dry_run=opts.dry_run))

    # Stage 2: group volumes + extract
    normalizer = DefaultGroupingNormalizer()
    grouper = DefaultVolumeGroupingStrategy(normalizer)
    volume_sets = grouper.group(restored_paths)
    print(f"Groups: {len(volume_sets)}")

    runner = ExternalCommandRunner()

    # 确保 7z 可用（必要时联网下载并写回 config.json）
    ensure = SevenZipBootstrapper().ensure(cfg, cfg_mgr)
    if not ensure.ok:
        print(ensure.message)
        print("Tip: set tools.seven_zip.exe in config.json or place 7z.exe under tools/7zip/.")
        return 3

    extraction_service = ExtractionService(
        extractors=[
            SevenZipExtractor(runner, exe=str(ensure.exe) if ensure.exe else None),
        ]
    )

    ok = 0
    fail = 0
    for vs in volume_sets:
        out_dir = opts.output_dir / vs.entry.stem
        req = ExtractionRequest(volume_set=vs, output_dir=out_dir, passwords=pw_lib.passwords)
        res = extraction_service.extract_one(req, preference=opts.tool_preference, dry_run=opts.dry_run)
        status = "OK" if res.ok else "FAIL"
        print(f"EXTRACT[{status}] tool={res.tool} entry={vs.entry.name} -> {out_dir}")
        if res.message:
            print(f"  msg: {res.message}")
        ok += 1 if res.ok else 0
        fail += 1 if not res.ok else 0

    print(f"Done. ok={ok} fail={fail}")
    return 0 if fail == 0 else 2
