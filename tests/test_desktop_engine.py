from __future__ import annotations

import io
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from reorder_engine.application.errors import EngineError, ProcessingCancelled
from reorder_engine.application.facade import EngineFacade
from reorder_engine.application.models import PlanRequest
from reorder_engine.application.processing import PackageProcessor
from reorder_engine.domain.models import ExtractionResult
from reorder_engine.infrastructure.archive_safety import validate_member, ArchiveSafetyInspector
from reorder_engine.infrastructure.command_runner import ExternalCommandRunner
from reorder_engine.infrastructure.desktop_paths import DesktopPaths
from reorder_engine.infrastructure.file_transaction import FileTransaction
from reorder_engine.infrastructure.json_rpc import JsonRpcServer


@pytest.fixture
def engine(tmp_path, monkeypatch):
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


@pytest.mark.parametrize("state,destination", [
    ("succeeded", "success/archives"), ("partial", "success/archives"), ("failed", "error_files/password_error"),
])
def test_work_copy_lifecycle_routes_real_original(engine, tmp_path, monkeypatch, state, destination):
    class SyntheticExtractor:
        def __init__(self, *args, **kwargs): pass
        def name(self): return "synthetic"
        def is_available(self): return True
        def extract_with_password(self, request, password, *, dry_run=False):
            if state != "failed":
                request.output_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(request.volume_set.entry) as handle:
                    handle.extractall(request.output_dir)
            return ExtractionResult(request.volume_set, ok=state == "succeeded", tool="synthetic",
                message=None if state == "succeeded" else "wrong password", password=password)
    monkeypatch.setattr("reorder_engine.application.processing.SevenZipExtractor", SyntheticExtractor)
    source = tmp_path / "cover.pdf"
    data = archive(source)
    # Real restoreAB copying, not just a renamed ordinary ZIP.
    original = b"%PDF-1.7\ncover\n%%EOF\n" + data
    source.write_bytes(original)
    output = tmp_path / "results"
    preview = engine.dispatch("plans.create", {"input_paths": [str(source)], "output_root": str(output)})
    job = engine.dispatch("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "start-1"})
    final = wait_job(engine, job["job_id"])
    assert final.packages[0].state == state
    assert not source.exists()
    assert (output / destination / "cover.pdf").read_bytes() == original
    if state != "failed":
        payload = list(output.rglob("payload.txt"))
        assert len(payload) == 1
        assert payload[0].read_bytes() == b"known payload bytes"
    assert not engine.repository.incomplete_actions()
    duplicate = engine.dispatch("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "start-1"})
    assert duplicate["job_id"] == final.job_id


def test_planner_keeps_disguised_txt_selected_scope_and_input_identity(engine, tmp_path):
    source = tmp_path / "bundle.001.txt"
    archive(source)
    archive(tmp_path / "unselected.zip")
    plan = engine.planner.create(PlanRequest(input_paths=[str(source)], output_root=str(tmp_path / "result")))
    assert sum(len(p.members) for p in plan.packages) == 1
    source.write_bytes(b"changed after scan")
    with pytest.raises(EngineError, match="变化"):
        engine.planner.validate_sources(plan)


def test_cancel_before_work_preserves_sources(engine, tmp_path):
    token = threading.Event()
    token.set()
    source = tmp_path / "cancel.zip"
    original = archive(source)
    plan = engine.planner.create(PlanRequest(input_paths=[str(source)], output_root=str(tmp_path / "result")))
    with pytest.raises(ProcessingCancelled):
        engine.processor.process(plan.packages[0], plan.options, job_id="not-started",
            output_root=tmp_path / "result", cancel=token, progress=lambda _: None, log=lambda _: None)
    assert source.read_bytes() == original


def test_multivolume_archive_failure_keeps_verified_copies(engine, tmp_path, monkeypatch):
    first, second = tmp_path / "v.001", tmp_path / "v.002"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    plan = engine.planner.create(PlanRequest(input_paths=[str(first), str(second)], output_root=str(tmp_path / "result")))
    engine.repository.save_plan(plan)
    job = engine.repository.create_job(plan, "route")
    transaction = FileTransaction(engine.repository, job_id=job.job_id, package_id=plan.packages[0].package_id, output_root=tmp_path / "result")
    original_unlink = Path.unlink
    def fail_second(self, *args, **kwargs):
        if self == second: raise PermissionError("simulated source lock")
        return original_unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_second)
    with pytest.raises(PermissionError):
        transaction.route_sources(plan.packages[0], tmp_path / "result/success/archives")
    assert (tmp_path / "result/success/archives/v.001").read_bytes() == b"first"
    assert (tmp_path / "result/success/archives/v.002").read_bytes() == b"second"
    assert second.read_bytes() == b"second"
    engine.repository.recover_interrupted()
    recovered = engine.repository.get_job(job.job_id)
    assert recovered.state == "needs_review"
    assert recovered.packages[0].state == "needs_review"


def test_publish_does_not_overwrite(engine, tmp_path):
    source = tmp_path / "source.zip"
    archive(source)
    plan = engine.planner.create(PlanRequest(input_paths=[str(source)], output_root=str(tmp_path / "result")))
    engine.repository.save_plan(plan)
    job = engine.repository.create_job(plan, "publish")
    target = tmp_path / "result/payload.bin"
    target.parent.mkdir()
    target.write_bytes(b"existing")
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"new")
    transaction = FileTransaction(engine.repository, job_id=job.job_id, package_id=plan.packages[0].package_id, output_root=target.parent)
    result = transaction.publish(payload, target)
    assert target.read_bytes() == b"existing"
    assert Path(result).read_bytes() == b"new"


@pytest.mark.parametrize("name", ["../outside.txt", "/absolute", "C:\\escape.txt", "x:stream", "CON.txt", "folder/../x", "bad\nname"])
def test_unsafe_members_rejected(name):
    with pytest.raises(EngineError): validate_member(name)


def test_zip_path_attack_fails_before_extract(tmp_path):
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as handle: handle.writestr("../escape.txt", "bad")
    with pytest.raises(EngineError):
        ArchiveSafetyInspector("never-called", ExternalCommandRunner(), 1024 ** 3).inspect(path, None)
    assert not (tmp_path / "escape.txt").exists()


def test_runner_deadline_cancel_and_bounded_output():
    runner = ExternalCommandRunner(timeout_sec=1, max_output_chars=4096)
    before = time.monotonic()
    result = runner.run([sys.executable, "-c", "import time; time.sleep(10)"])
    assert result.exit_code == 124 and time.monotonic() - before < 4
    result = runner.run([sys.executable, "-c", "print('x' * 1000000)"])
    assert result.ok and len(result.stdout) <= 4096
    cancel = threading.Event()
    timer = threading.Timer(.2, cancel.set)
    timer.start()
    try:
        with pytest.raises(ProcessingCancelled):
            ExternalCommandRunner(cancel_event=cancel).run([sys.executable, "-c", "import time; time.sleep(10)"])
    finally: timer.join()


def test_rpc_invalid_params_and_public_password_file(engine):
    server = JsonRpcServer(engine)
    reply = server.handle(b'{"jsonrpc":"2.0","id":1,"method":"jobs.get","params":{"job_id":42}}')
    assert reply["error"]["code"] == -32602
    assert server.handle(b'{bad json')["error"]["code"] == -32700
    assert server.handle(b'{"jsonrpc":"2.0","id":2,"method":"shell.exec"}')["error"]["code"] == -32601
    info = engine.secrets.replace(["synthetic-test-secret"])
    assert info["storage"] == "plaintext" and info["values"] == ["synthetic-test-secret"]
    path = engine.paths.data_root / "passwords.txt"
    assert path.read_text(encoding="utf-8") == "synthetic-test-secret\n"
    assert engine.secrets.redact("password=synthetic-test-secret") == "password=synthetic-test-secret"
    assert engine.settings_info()["passwords"]["values"] == ["synthetic-test-secret"]


def test_cancel_request_is_responsive_while_worker_runs(engine, tmp_path, monkeypatch):
    started = threading.Event()
    def wait_for_cancel(*args, **kwargs):
        started.set()
        assert kwargs["cancel"].wait(3)
        raise ProcessingCancelled()
    monkeypatch.setattr(engine.processor, "process", wait_for_cancel)
    source = tmp_path / "slow.zip"
    original = archive(source)
    preview = engine.dispatch("plans.create", {"input_paths": [str(source)], "output_root": str(tmp_path / "result")})
    job = engine.dispatch("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "slow"})
    assert started.wait(1)
    assert engine.dispatch("jobs.cancel", {"job_id": job["job_id"]})["accepted"]
    assert wait_job(engine, job["job_id"]).state == "cancelled"
    assert source.read_bytes() == original
