from __future__ import annotations

import errno
import io
import os
import shutil
import sys
import threading
import time
import types
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from reorder_engine.application.errors import EngineError, ProcessingCancelled
from reorder_engine.application.facade import EngineFacade
from reorder_engine.application.jobs import JobRunner
from reorder_engine.application.models import (PlannedPackage, ProcessingOptions, ProcessingPlan,
    PlanRequest)
from reorder_engine.application.planning import source_snapshot
from reorder_engine.domain.models import ExtractionResult
from reorder_engine.infrastructure.command_runner import ExternalCommandRunner
from reorder_engine.infrastructure.desktop_paths import DesktopPaths
from reorder_engine.infrastructure.file_transaction import copy_verified, install_exclusive
from reorder_engine.infrastructure.secret_store import SecretStore


class SessionSecrets(SecretStore):
    def __init__(self):
        self._lock = threading.RLock()
        self._passwords = ()
        self.mode = "session"
        self._backend = None


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr("reorder_engine.application.facade.SecretStore", SessionSecrets)
    app = tmp_path / "app"
    app.mkdir()
    tool = app / "tools/7z"
    tool.parent.mkdir()
    tool.write_bytes(b"synthetic tool; never executed")
    engine = EngineFacade(DesktopPaths(app, tmp_path / "data"))
    monkeypatch.setattr(engine.settings, "resolve_tool", lambda name: str(tool) if name == "seven_zip" else None)
    yield engine
    engine.close()


def archive(path: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("payload.txt", b"known payload bytes")
    value = buffer.getvalue()
    path.write_bytes(value)
    return value


def wait_job(engine, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = engine.repository.get_job(job_id)
        if job.state not in {"queued", "running", "cancelling"} and not engine.runner.busy:
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def fail_log_device(monkeypatch):
    """Make every ``*.log`` open raise OSError so the log sink is a failing device."""
    real_open = Path.open

    def failing_open(self, *args, **kwargs):
        if self.name.endswith(".log"):
            raise PermissionError(errno.EACCES, "synthetic log device failure")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)


def test_recovery_journal_is_scoped_to_job_when_retry_reuses_package_id(engine, tmp_path):
    source = tmp_path / "same.bin"
    source.write_bytes(b"same original")
    package = PlannedPackage(package_id=str(uuid4()), name=source.name,
        group_key="same", entry=str(source), members=[source_snapshot(source)])
    plan = ProcessingPlan(plan_id=str(uuid4()), created_at="t0", output_root=str(tmp_path / "out"),
        options=ProcessingOptions(), settings_revision=engine.settings.revision(), packages=[package])
    engine.repository.save_plan(plan)
    old = engine.repository.create_job(plan, "old-review")
    retry = engine.repository.create_job(plan, "completed-retry", retry_of=old.job_id)
    engine.repository.update_job(old.job_id, "needs_review")
    engine.repository.update_package(old.job_id, package.package_id, state="needs_review")
    engine.repository.record_action({"job_id": old.job_id, "package_id": package.package_id,
        "kind": "publish", "source": str(source), "destination": str(tmp_path / "out/result"),
        "snapshot": None})
    engine.repository.update_package(retry.job_id, package.package_id, state="succeeded", message="ok")
    engine.repository.update_job(retry.job_id, "succeeded")
    engine.repository.recover_interrupted()
    completed = engine.repository.get_job(retry.job_id)
    assert completed.state == "succeeded"
    assert completed.packages[0].state == "succeeded"
    assert completed.packages[0].message == "ok"


# 1. A failing log device must not kill a running tool, must not block a package
#    terminal state, and must only emit a generic, redacted stderr diagnostic.
def test_log_failure_does_not_kill_running_tool(engine, monkeypatch, capsys):
    fail_log_device(monkeypatch)
    runner = ExternalCommandRunner(encoding="utf-8", timeout_sec=10,
        line_sink=lambda line: engine.runner._log("tool-job", line))
    result = runner.run([sys.executable, "-c", "print('first line'); print('second line')"])
    assert result.ok and result.exit_code == 0
    assert "first line" in result.stdout and "second line" in result.stdout
    err = capsys.readouterr().err
    assert "日志写入失败" in err
    assert "tool-job" not in err and ".log" not in err and "first line" not in err


def test_log_failure_does_not_block_package_terminal_state(engine, tmp_path, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise EngineError("TOOL_TIMEOUT", "synthetic tool failure")

    monkeypatch.setattr("reorder_engine.application.processing.SevenZipExtractor", boom)
    fail_log_device(monkeypatch)
    source = tmp_path / "broken.zip"
    archive(source)
    preview = engine.dispatch("plans.create", {"input_paths": [str(source)], "output_root": str(tmp_path / "out")})
    job = engine.dispatch("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "log-failure"})
    final = wait_job(engine, job["job_id"])
    assert final.state in {"failed", "needs_review"}
    assert final.packages[0].state in {"failed", "needs_review"}
    assert not engine.runner.busy
    assert "日志写入失败" in capsys.readouterr().err


# 2. Recovery must not skip an active package inside an already-terminal job, must
#    leave finished packages untouched, and must register a retained workspace for
#    manual review without deleting it.
def test_recover_active_package_in_terminal_job_registers_workspace(engine, tmp_path):
    left = tmp_path / "left.bin"
    left.write_bytes(b"left")
    right = tmp_path / "right.bin"
    right.write_bytes(b"right")
    plan = ProcessingPlan(plan_id=str(uuid4()), created_at="t0", output_root=str(tmp_path / "out"),
        options=ProcessingOptions(), settings_revision=engine.settings.revision(),
        packages=[
            PlannedPackage(package_id=str(uuid4()), name="left.bin", group_key="left",
                           entry=str(left), members=[source_snapshot(left)]),
            PlannedPackage(package_id=str(uuid4()), name="right.bin", group_key="right",
                           entry=str(right), members=[source_snapshot(right)]),
        ])
    engine.repository.save_plan(plan)
    job = engine.repository.create_job(plan, "recover-mixed")
    done, active = plan.packages
    engine.repository.update_job(job.job_id, "failed")  # terminal job holding a stranded package
    engine.repository.update_package(job.job_id, done.package_id, state="succeeded", message="ok")
    engine.repository.update_package(job.job_id, active.package_id, state="archiving")
    workspace = engine.paths.work_root / job.job_id / active.package_id
    workspace.mkdir(parents=True)
    (workspace / "partial.out").write_bytes(b"partial")

    restart = JobRunner(engine.repository, engine.planner, engine.processor)
    try:
        recovered = engine.repository.get_job(job.job_id)
    finally:
        restart.close()

    states = {package.package_id: package.state for package in recovered.packages}
    assert states[done.package_id] == "succeeded"       # finished package never redone
    assert states[active.package_id] == "interrupted"
    assert recovered.state == "interrupted"
    assert workspace.is_dir() and (workspace / "partial.out").read_bytes() == b"partial"
    notes = [action for action in engine.repository.actions(job.job_id) if action["kind"] == "recovery_note"]
    assert [action["destination"] for action in notes] == [str(workspace)]
    assert not engine.repository.incomplete_actions()

    again = JobRunner(engine.repository, engine.planner, engine.processor)
    try:
        pass
    finally:
        again.close()
    notes_again = [a for a in engine.repository.actions(job.job_id) if a["kind"] == "recovery_note"]
    assert len(notes_again) == 1  # idempotent second start, no duplicate note


def test_recover_derives_terminal_state_when_all_packages_finished(engine, tmp_path):
    source = tmp_path / "done.bin"
    source.write_bytes(b"done")
    plan = ProcessingPlan(plan_id=str(uuid4()), created_at="t0", output_root=str(tmp_path / "out"),
        options=ProcessingOptions(), settings_revision=engine.settings.revision(),
        packages=[PlannedPackage(package_id=str(uuid4()), name="done.bin", group_key="done",
                                 entry=str(source), members=[source_snapshot(source)])])
    engine.repository.save_plan(plan)
    job = engine.repository.create_job(plan, "recover-running")
    engine.repository.update_job(job.job_id, "running")
    engine.repository.update_package(job.job_id, plan.packages[0].package_id, state="succeeded", message="ok")
    engine.repository.recover_interrupted()
    assert engine.repository.get_job(job.job_id).state == "succeeded"


# 3. Without hard links (FAT/exFAT/network volumes) the final path must still never be
#    overwritten, a truncated final file must not be exposed, and a failure must clean
#    only the incomplete target this call created while keeping the source.
def test_install_without_hardlinks_never_overwrites_and_keeps_source(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EPERM, "no hard links")))
    temporary = tmp_path / "temp.partial"
    temporary.write_bytes(b"payload")
    target = tmp_path / "final.bin"
    install_exclusive(temporary, target)
    assert target.read_bytes() == b"payload" and not temporary.exists()

    existing = tmp_path / "existing.bin"
    existing.write_bytes(b"existing")
    second = tmp_path / "temp2.partial"
    second.write_bytes(b"payload")
    with pytest.raises(OSError):
        install_exclusive(second, existing)
    assert existing.read_bytes() == b"existing" and second.read_bytes() == b"payload"


def test_install_without_hardlinks_failure_exposes_no_truncated_target(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EPERM, "no hard links")))
    monkeypatch.setattr(shutil, "copystat", lambda *a, **k: (_ for _ in ()).throw(OSError("stat failure")))
    temporary = tmp_path / "temp.partial"
    temporary.write_bytes(b"payload")
    target = tmp_path / "final.bin"
    with pytest.raises(OSError):
        install_exclusive(temporary, target)
    assert not target.exists() and temporary.read_bytes() == b"payload"


def test_install_without_hardlinks_windows_branch_uses_rename(tmp_path, monkeypatch):
    import reorder_engine.infrastructure.file_transaction as ft

    calls: dict[str, tuple[str, str]] = {}

    def fake_link(*args, **kwargs):
        raise OSError(errno.EPERM, "no hard links")

    def fake_rename(source, destination):
        calls["rename"] = (str(source), str(destination))
        os.rename(source, destination)

    monkeypatch.setattr(ft, "os", types.SimpleNamespace(name="nt", link=fake_link, rename=fake_rename))
    temporary = tmp_path / "temp.partial"
    temporary.write_bytes(b"payload")
    target = tmp_path / "final.bin"
    install_exclusive(temporary, target)
    assert calls["rename"] == (str(temporary), str(target))
    assert target.read_bytes() == b"payload" and not temporary.exists()


def test_install_with_hardlinks_is_exclusive(tmp_path):
    temporary = tmp_path / "temp.partial"
    temporary.write_bytes(b"payload")
    target = tmp_path / "final.bin"
    install_exclusive(temporary, target)
    assert target.read_bytes() == b"payload" and not temporary.exists()
    second = tmp_path / "temp2.partial"
    second.write_bytes(b"payload")
    with pytest.raises(FileExistsError):
        install_exclusive(second, target)
    assert target.read_bytes() == b"payload" and second.read_bytes() == b"payload"


def test_copy_verified_failure_removes_only_new_target(tmp_path, monkeypatch):
    source = tmp_path / "src.bin"
    source.write_bytes(b"payload")
    existing = tmp_path / "dst.bin"
    existing.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        copy_verified(source, existing)
    assert existing.read_bytes() == b"original" and source.read_bytes() == b"payload"

    monkeypatch.setattr(shutil, "copystat", lambda *a, **k: (_ for _ in ()).throw(OSError("stat failure")))
    fresh = tmp_path / "new.bin"
    with pytest.raises(OSError):
        copy_verified(source, fresh)
    assert not fresh.exists() and source.read_bytes() == b"payload"


def test_copy_verified_cancel_removes_new_target(tmp_path):
    source = tmp_path / "big.bin"
    payload = b"x" * (1024 * 1024)
    source.write_bytes(payload)
    cancel = threading.Event()
    cancel.set()
    fresh = tmp_path / "cancelled.bin"
    with pytest.raises(ProcessingCancelled):
        copy_verified(source, fresh, cancel_event=cancel)
    assert not fresh.exists() and source.read_bytes() == payload


# 4. Retrying a failed original whose routed path already equals the expected
#    destination must register in place, never shuffling it into _duplicates.
def test_repeated_failed_retry_registers_original_in_place(engine, tmp_path, monkeypatch):
    class FailingExtractor:
        def __init__(self, *args, **kwargs):
            pass

        def name(self):
            return "synthetic"

        def is_available(self):
            return True

        def extract_with_password(self, request, password, *, dry_run=False):
            return ExtractionResult(request.volume_set, ok=False, tool="synthetic",
                message="wrong password", password=password)

    monkeypatch.setattr("reorder_engine.application.processing.SevenZipExtractor", FailingExtractor)
    source = tmp_path / "cover.pdf"
    data = archive(source)
    original = b"%PDF-1.7\ncover\n%%EOF\n" + data
    source.write_bytes(original)
    output = tmp_path / "results"
    preview = engine.dispatch("plans.create", {"input_paths": [str(source)], "output_root": str(output)})
    first = engine.dispatch("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "retry-base"})
    final = wait_job(engine, first["job_id"])
    assert final.packages[0].state == "failed"
    archived = output / "error_files" / "password_error" / "cover.pdf"
    assert archived.read_bytes() == original

    job_id, package_id = final.job_id, final.packages[0].package_id
    for index in range(2):
        retried = engine.dispatch("jobs.retry", {"job_id": job_id, "package_ids": [package_id],
            "idempotency_key": f"retry-{index + 2}"})
        result = wait_job(engine, retried["job_id"])
        assert result.packages[0].state == "failed"
        assert not (output / "_duplicates").exists()
        assert archived.read_bytes() == original
        in_place = [action for action in engine.repository.actions(result.job_id)
            if action["kind"] == "route_source" and action["phase"] == "committed"]
        assert [action["destination"] for action in in_place] == [str(archived)]
        assert all(action["source"] == str(archived) for action in in_place)
        job_id, package_id = result.job_id, result.packages[0].package_id
