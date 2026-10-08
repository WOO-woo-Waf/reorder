#!/usr/bin/env python3
"""Isolated end-to-end regression check for disguised split archives (FMT-031).

The script never touches the supplied samples: it copies them into a dedicated
work directory, runs the same guarded extraction pipeline the desktop app uses,
and reports counts/hashes only. Intended for the authorized
``BG57.zip.001.mp4`` / ``BG57.zip.002.mp4`` sample pair, but any pair works.

Example (Windows host, bundled 7-Zip):

    python scripts/check_desktop_archive_regression.py \
        --sample1 "D:\\path\\BG57.zip.001.mp4" \
        --sample2 "D:\\path\\BG57.zip.002.mp4" \
        --work-dir "D:\\tmp\\hoshiribbon-check" \
        --seven "C:\\Program Files\\7-Zip\\7z.exe"

Exit codes: 0 = pipeline finished and the samples are byte-identical afterwards,
2 = input/integrity problem, 3 = pipeline reported a hard failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reorder_engine.infrastructure.archive_safety import (  # noqa: E402
    ArchiveSafetyInspector,
    GuardedExtractor,
    WorkspaceGuard,
)
from reorder_engine.infrastructure.command_runner import ExternalCommandRunner  # noqa: E402
from reorder_engine.application.planning import reject_links  # noqa: E402
from reorder_engine.infrastructure.secret_store import parse_password_text  # noqa: E402
from reorder_engine.services.beta_pipeline import BetaFolderPipeline  # noqa: E402
from reorder_engine.services.cleaning import DefaultGroupingNormalizer  # noqa: E402
from reorder_engine.services.decrypting import DecryptionService, PassthroughDecryptor  # noqa: E402
from reorder_engine.services.extracting import ExtractionService, SevenZipExtractor  # noqa: E402
from reorder_engine.services.grouping import DefaultVolumeGroupingStrategy  # noqa: E402
from reorder_engine.services.restore_ab import RestoreABRestorer  # noqa: E402
from reorder_engine.services.restoring import (  # noqa: E402
    ApateRestorer,
    ArchiveSignatureInspector,
    EmbeddedArchiveRestorer,
    RestorationService,
    SuffixVariantBuilder,
)

_SAFE_LINE = re.compile(
    r"^(SCAN|VOLUME-REVEAL|VOLUME-RENAME|CANDIDATE|IDENTIFY|EXTRACT-TRY|EXTRACT\[|FINAL|MOVE|RESULT|DEFER)"
)


def _digest(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _manifest(root: Path) -> dict[str, object]:
    files = sorted((p for p in root.rglob("*") if p.is_file()) if root.exists() else [])
    lines = "\n".join(f"{p.relative_to(root).as_posix()}\t{p.stat().st_size}" for p in files)
    return {
        "file_count": len(files),
        "total_bytes": sum(p.stat().st_size for p in files),
        "manifest_sha256": hashlib.sha256(lines.encode("utf-8")).hexdigest(),
    }


def _load_passwords(path: Path | None) -> tuple[str, ...]:
    if path is None:
        return ()
    return parse_password_text(path.read_text(encoding="utf-8-sig"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated disguised split-archive regression check.")
    parser.add_argument("--sample1", required=True, type=Path)
    parser.add_argument("--sample2", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path, help="Work parent; each check creates a fresh owned run directory.")
    parser.add_argument("--seven", default="7z", help="Path to the 7-Zip executable.")
    parser.add_argument("--passwords", type=Path, default=None, help="Optional newline password file.")
    parser.add_argument("--keep", action="store_true", help="Keep the copied inputs after the run.")
    args = parser.parse_args(argv)

    for sample in (args.sample1, args.sample2):
        if not sample.is_file():
            print(json.dumps({"error": "missing-sample", "sample": str(sample)}))
            return 2

    before = {}
    for sample in (args.sample1, args.sample2):
        digest, size = _digest(sample)
        before[sample.name] = {"sha256": digest, "size": size}

    # Never clear an existing input/final tree, even if the caller points this
    # checker at a parent holding previous runs or the supplied originals.
    reject_links(args.work_dir)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="check-", dir=args.work_dir))
    input_dir = work / "input"
    workspace = work / "workspace"
    input_dir.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    copies: list[Path] = []
    for sample in (args.sample1, args.sample2):
        target = input_dir / sample.name
        shutil.copy2(sample, target)
        copies.append(target)

    runner = ExternalCommandRunner(cwd=workspace,
        env={name: str(workspace) for name in ("TEMP", "TMP", "TMPDIR")})
    inspector = ArchiveSignatureInspector()
    restore = RestorationService(
        [RestoreABRestorer(), ApateRestorer(inspector, rounds=3), EmbeddedArchiveRestorer(inspector), SuffixVariantBuilder(inspector)],
        inspector=inspector,
    )
    safety = ArchiveSafetyInspector(args.seven, runner, 8 * 1024 ** 3)
    guard = WorkspaceGuard(input_dir, byte_limit=8 * 1024 ** 3)
    extractor = ExtractionService(
        extractors=[GuardedExtractor(SevenZipExtractor(runner, exe=args.seven), safety, guard)]
    )

    def emit(message: str) -> None:
        first = str(message).splitlines()[0] if message else ""
        if _SAFE_LINE.match(first):
            print("PIPE:", first[:300])

    pipeline = BetaFolderPipeline(
        folder=input_dir,
        decrypt_service=DecryptionService([PassthroughDecryptor()]),
        restore_service=restore,
        grouper=DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()),
        extractor=extractor,
        passwords=_load_passwords(args.passwords),
        emit=emit,
        deep_extract=None,
    )
    result = pipeline.run()

    after = {}
    for sample in (args.sample1, args.sample2):
        digest, size = _digest(sample)
        after[sample.name] = {"sha256": digest, "size": size}

    summary = {
        "samples": {name: {"before": before[name], "after": after[name], "unchanged": before[name] == after[name]}
                    for name in before},
        "pipeline": {"ok_count": result.ok_count, "fail_count": result.fail_count, "total": result.total},
        "final": _manifest(input_dir / "final"),
        "archived": _manifest(input_dir / "success" / "archives"),
        "error_files": _manifest(input_dir / "error_files"),
    }

    if not args.keep:
        for copy in copies:
            copy.unlink(missing_ok=True)
        for leftover in (input_dir / "success", input_dir / "error_files", input_dir / "intermediate",
                         input_dir / "deferred_volumes", input_dir / "final"):
            shutil.rmtree(leftover, ignore_errors=True)

    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if not all(item["unchanged"] for item in summary["samples"].values()):
        return 2
    return 0 if result.ok_count > 0 and result.fail_count == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
