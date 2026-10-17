"""Bounded native Windows regression for partial 7-Zip password retries.

Run only with the repository's existing desktop-build-venv Windows Python.
The script uses only the checked-in 7-Zip binary and generated synthetic data;
it does not read repository configuration, user archives, or password files.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
ARTIFACTS = ROOT / "artifacts" / "desktop" / "partial-retry-20261017" / "native"
WINDOWS_PYTHON = ROOT / "runtime" / "desktop-build-venv" / "Scripts" / "python.exe"
SEVEN_ZIP = ROOT / "tools" / "7zip" / "26.04" / "7z.exe"
PAYLOAD_NAME = "synthetic-payload.bin"
PAYLOAD_SIZE = 48 * 1024
PASSWORDS = ("wrongA", "wrongB", "native-retry-correct")
MAX_ATTEMPTS = 4  # no password, wrongA, wrongB, correct


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_identity() -> dict[str, str]:
    paths = (
        ROOT / "src" / "reorder_engine" / "services" / "extracting.py",
        ROOT / "src" / "reorder_engine" / "services" / "beta_pipeline.py",
        ROOT / "src" / "reorder_engine" / "domain" / "models.py",
        ROOT / "src" / "reorder_engine" / "infrastructure" / "extraction_output.py",
        ROOT / "src" / "reorder_engine" / "infrastructure" / "tools.py",
    )
    return {str(path.relative_to(ROOT)): _sha256(path) for path in paths}


def _run_7z(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SEVEN_ZIP), *args],
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def _make_encrypted_volumes(case_root: Path) -> tuple[bytes, list[Path]]:
    source = case_root / "source"
    archive_dir = case_root / "archive-maker"
    source.mkdir()
    archive_dir.mkdir()
    payload = bytes((index * 73 + 19) % 256 for index in range(PAYLOAD_SIZE))
    (source / PAYLOAD_NAME).write_bytes(payload)
    archive = archive_dir / "synthetic.7z"
    result = _run_7z(
        [
            "a",
            "-t7z",
            "-m0=Copy",
            f"-p{PASSWORDS[-1]}",
            "-v16k",
            str(archive),
            str(source / PAYLOAD_NAME),
        ],
        cwd=case_root,
    )
    _check(result.returncode == 0, f"7-Zip archive creation failed: rc={result.returncode}")
    volumes = sorted(archive_dir.glob("synthetic.7z.*"), key=lambda path: path.name.lower())
    _check(len(volumes) >= 2, f"expected multi-volume output, found {[p.name for p in volumes]}")
    return payload, volumes


def _copy_volumes(volumes: list[Path], workspace: Path) -> tuple[Path, tuple[Path, ...]]:
    workspace.mkdir()
    members = tuple(workspace / source.name for source in volumes)
    for source, target in zip(volumes, members):
        shutil.copyfile(source, target)
    entry = next((path for path in members if path.name.lower().endswith(".7z.001")), None)
    _check(entry is not None, "7-Zip did not produce the expected .7z.001 entry")
    return entry, members


def _direct_wrong_password_baseline(
    entry: Path, case_root: Path, receipt: dict[str, Any]
) -> None:
    output = case_root / "direct-wrong-password"
    output.mkdir()
    result = _run_7z(
        ["x", "-y", "-sccUTF-8", f"-o{output}", f"-p{PASSWORDS[0]}", str(entry)],
        cwd=case_root,
    )
    nonempty = [
        {"relative_path": str(path.relative_to(case_root)), "size": path.stat().st_size}
        for path in output.rglob("*")
        if path.is_file() and path.stat().st_size > 0
    ]
    receipt["direct_wrong_password"] = {
        "exit_code": result.returncode,
        "nonempty_files": nonempty,
        "output_tail": (result.stdout + result.stderr)[-2000:],
    }


def _build_pipeline(
    workspace: Path,
    passwords: tuple[str, ...],
    attempt_log: list[dict[str, Any]],
    log_lines: list[str],
):
    from reorder_engine.services.beta_pipeline import BetaFolderPipeline
    from reorder_engine.services.config import BetaDeepExtractConfig
    from reorder_engine.services.decrypting import DecryptionService, PassthroughDecryptor
    from reorder_engine.services.extracting import ExtractionService, SevenZipExtractor
    from reorder_engine.services.grouping import DefaultVolumeGroupingStrategy
    from reorder_engine.services.cleaning import DefaultGroupingNormalizer
    from reorder_engine.services.restoring import PassthroughRestorer, RestorationService
    from reorder_engine.infrastructure.command_runner import ExternalCommandRunner

    tool_tmp = workspace / "intermediate" / "tool_tmp"
    tool_tmp.mkdir(parents=True, exist_ok=True)

    def record_attempt(tool: str, request, password: str | None) -> None:
        attempt_log.append(
            {
                "tool": tool,
                "entry": request.volume_set.entry.name,
                "password_label": (
                    "none"
                    if password is None
                    else "correct"
                    if password == PASSWORDS[-1]
                    else "wrongA"
                    if password == PASSWORDS[0]
                    else "wrongB"
                ),
                "output_dir": str(request.output_dir),
            }
        )

    extractor = ExtractionService(
        [
            SevenZipExtractor(
                ExternalCommandRunner(
                    timeout_sec=30,
                    cwd=tool_tmp,
                    env={
                        "TEMP": str(tool_tmp),
                        "TMP": str(tool_tmp),
                        "TMPDIR": str(tool_tmp),
                    },
                ),
                exe=str(SEVEN_ZIP),
            )
        ],
        attempt_sink=record_attempt,
    )
    pipeline = BetaFolderPipeline(
        folder=workspace,
        decrypt_service=DecryptionService([PassthroughDecryptor()]),
        restore_service=RestorationService([PassthroughRestorer()]),
        grouper=DefaultVolumeGroupingStrategy(DefaultGroupingNormalizer()),
        extractor=extractor,
        passwords=passwords,
        deep_extract=BetaDeepExtractConfig(
            enabled=False, max_depth=1, min_archive_mb=100, final_single_mb=100
        ),
        emit=log_lines.append,
        archive_min_mb=1,
    )
    return pipeline


def _nonempty_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return [path for path in folder.rglob("*") if path.is_file() and path.stat().st_size > 0]


def _assert_source_volumes_at(root: Path, members: tuple[Path, ...]) -> None:
    actual = {path.name for path in root.rglob("*") if path.is_file()}
    expected = {path.name for path in members}
    _check(expected <= actual, f"source volumes missing from {root}: {sorted(expected - actual)}")


def _assert_no_extra_original_copies(
    workspace: Path, expected: dict[str, str]
) -> dict[str, int]:
    found = {name: [] for name in expected}
    for path in workspace.rglob("*"):
        if path.is_file() and path.name in expected and _sha256(path) == expected[path.name]:
            found[path.name].append(path)
    counts = {name: len(paths) for name, paths in found.items()}
    _check(
        all(count == 1 for count in counts.values()),
        f"original archive volumes were copied or lost during processing: {counts}",
    )
    return counts


def _run_pipeline_case(
    run_root: Path,
    name: str,
    passwords: tuple[str, ...],
    expected_payload: bytes,
    volumes: list[Path],
    receipt: dict[str, Any],
) -> None:
    case_root = run_root / name
    workspace = case_root / "workspace"
    case_root.mkdir()
    entry, members = _copy_volumes(volumes, workspace)
    original_hashes = {path.name: _sha256(path) for path in members}
    sentinel = workspace / "success" / "archives" / "pre-existing-sentinel.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("keep-existing-user-data", encoding="utf-8")
    attempt_log: list[dict[str, Any]] = []
    log_lines: list[str] = []
    pipeline = _build_pipeline(workspace, passwords, attempt_log, log_lines)
    result = pipeline.run()
    case_receipt: dict[str, Any] = {
        "state": result.packages[0].state if result.packages else "no-package-result",
        "category": result.packages[0].category if result.packages else None,
        "message": result.packages[0].message if result.packages else "",
        "ok_count": result.ok_count,
        "fail_count": result.fail_count,
        "attempts": attempt_log,
        "fixture_archive_copy": "synthetic generated volumes copied into this isolated workspace before pipeline start",
    }
    (case_root / "pipeline.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    _check(sentinel.read_text(encoding="utf-8") == "keep-existing-user-data", "pipeline changed an existing sentinel")
    _check(len(attempt_log) <= MAX_ATTEMPTS, f"attempt count exceeded bound: {len(attempt_log)}")
    case_receipt["attempt_order"] = [attempt["password_label"] for attempt in attempt_log]
    case_receipt["original_volume_workspace_copies"] = _assert_no_extra_original_copies(
        workspace, original_hashes
    )

    if name == "retry-correct":
        attempted_labels = [attempt["password_label"] for attempt in attempt_log]
        _check(
            attempted_labels == ["none", "wrongA", "wrongB", "correct"],
            f"password retry order did not reach correct password: {attempted_labels}",
        )
        final_payloads = [
            path
            for path in (workspace / "final").rglob("*")
            if path.is_file() and path.name == PAYLOAD_NAME
        ]
        final_files = _nonempty_files(workspace / "final")
        _check(final_files == final_payloads and len(final_payloads) == 1, f"unexpected final output files: {final_files}")
        _check(final_payloads[0].read_bytes() == expected_payload, "final payload differs from generated bytes")
        _check(_sha256(final_payloads[0]) == hashlib.sha256(expected_payload).hexdigest(), "final payload hash mismatch")
        _check(len(_nonempty_files(workspace / "intermediate")) == 0, "failed-attempt debris remains in intermediate")
        _assert_source_volumes_at(workspace / "success" / "archives", members)
        _check(not _nonempty_files(workspace / "incomplete"), "successful retry left incomplete output")
        _check(not _nonempty_files(workspace / "error_files"), "successful retry left error output")
        _check(result.ok_count == 1 and result.fail_count == 0, f"unexpected successful result: {case_receipt}")
    else:
        wrong_labels = {"none", "wrongA", "wrongB"}
        _check(
            all(attempt["password_label"] in wrong_labels for attempt in attempt_log),
            f"all-wrong case attempted an unexpected password: {attempt_log}",
        )
        incomplete_outputs = _nonempty_files(workspace / "incomplete" / "outputs")
        if incomplete_outputs:
            _check(case_receipt["state"] == "partial", f"retained failed output was not classified partial: {case_receipt}")
            _assert_source_volumes_at(workspace / "incomplete" / "archives", members)
            success_archive_names = {path.name for path in _nonempty_files(workspace / "success" / "archives")}
            _check(
                not ({path.name for path in members} & success_archive_names),
                "partial archive entered success",
            )
            _check(not _nonempty_files(workspace / "final"), "partial output entered final")
        else:
            _check(case_receipt["state"] == "failed", f"empty failure was not classified failed: {case_receipt}")
            _check(not _nonempty_files(workspace / "incomplete"), "empty failure was forced into incomplete")
            _check(bool(_nonempty_files(workspace / "error_files")), "empty failure was not routed to error_files")
            _assert_source_volumes_at(workspace / "error_files", members)
        _check(result.fail_count == 1, f"all-wrong case was not counted as failed: {case_receipt}")
    receipt["cases"][name] = case_receipt


def _run() -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("Run this harness with native Windows Python")
    _check(Path(sys.executable).resolve() == WINDOWS_PYTHON.resolve(), f"use required Python: {WINDOWS_PYTHON}")
    _check(SEVEN_ZIP.is_file(), f"checked-in 7-Zip binary missing: {SEVEN_ZIP}")
    _check(not ARTIFACTS.exists() or ARTIFACTS.is_dir(), f"unsafe artifact path: {ARTIFACTS}")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    run_root = ARTIFACTS / f"run-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    run_root.mkdir()  # exclusive: never overwrite a previous run or sentinel
    receipt: dict[str, Any] = {
        "task": "PARTIAL-NATIVE-1018",
        "status": "running",
        "python": str(Path(sys.executable).resolve()),
        "seven_zip": str(SEVEN_ZIP),
        "source_identity_sha256": _source_identity(),
        "fixture_copy_note": "Only generated synthetic 7z volumes are copied from baseline-source into each isolated pipeline workspace.",
        "cases": {},
    }
    receipt_path = run_root / "receipt.json"
    try:
        correct_case_root = run_root / "baseline-source"
        correct_case_root.mkdir()
        payload, volumes = _make_encrypted_volumes(correct_case_root)
        receipt["synthetic_archive"] = {
            "volume_names": [path.name for path in volumes],
            "volume_sizes": {path.name: path.stat().st_size for path in volumes},
            "payload_size": len(payload),
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "headers_encrypted": False,
        }
        _direct_wrong_password_baseline(volumes[0], correct_case_root, receipt)
        _run_pipeline_case(run_root, "retry-correct", PASSWORDS, payload, volumes, receipt)
        _run_pipeline_case(run_root, "all-passwords-wrong", PASSWORDS[:2], payload, volumes, receipt)
        receipt["status"] = "passed"
    except BaseException as exc:
        receipt["status"] = "failed"
        receipt["failure"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    print(json.dumps(_run(), ensure_ascii=False, indent=2))
