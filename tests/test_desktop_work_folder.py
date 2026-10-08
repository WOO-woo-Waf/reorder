from __future__ import annotations

import io
import json
import os
import shutil
import sys
import threading
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from reorder_engine.application.errors import EngineError, ProcessingCancelled
from reorder_engine.application.facade import EngineFacade
from reorder_engine.application.models import (PlannedPackage, ProcessingOptions,
    ProcessingPlan, PlanRequest)
from reorder_engine.application.planning import source_snapshot
from reorder_engine.domain.models import ExtractionResult
from reorder_engine.infrastructure import file_transaction as ft
from reorder_engine.infrastructure import workspace as ws
from reorder_engine.infrastructure.command_runner import ExternalCommandRunner
from reorder_engine.infrastructure.desktop_paths import DesktopPaths
from reorder_engine.infrastructure.file_transaction import FileTransaction
from reorder_engine.infrastructure.secret_store import SecretStore


class SessionSecrets(SecretStore):
    def __init__(self):
        self._lock = threading.RLock()
        self._passwords = ()
        self.mode = "session"


class SyntheticExtractor:
    """Extract a ZIP into the requested directory without running a real tool."""

    def __init__(self, *args, **kwargs):
        pass

    def name(self):
        return "synthetic"

    def is_available(self):
        return True

    def extract(self, request, *, dry_run=False):
        return self.extract_with_password(request, None, dry_run=dry_run)

    def extract_with_password(self, request, password, *, dry_run=False):
        request.output_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(request.volume_set.entry) as handle:
            handle.extractall(request.output_dir)
        return ExtractionResult(request.volume_set, ok=True, tool="synthetic", message=None, password=password)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    tool = app / "tools/7z"
    tool.parent.mkdir()
    tool.write_bytes(b"synthetic tool; never executed")
    engine = EngineFacade(DesktopPaths(app, tmp_path / "data"), secrets=SessionSecrets())
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


def single_package_plan(engine, source: Path, output: Path) -> ProcessingPlan:
    plan = ProcessingPlan(plan_id=str(uuid4()), created_at="t0", output_root=str(output),
        options=ProcessingOptions(), settings_revision=engine.settings.revision(),
        packages=[PlannedPackage(package_id=str(uuid4()), name=source.name, group_key="k",
                                 entry=str(source), members=[source_snapshot(source)])])
    engine.repository.save_plan(plan)
    return plan


# 1. New work runs under the chosen output folder, records its real location, and is
#    cleaned up on success while no AppData work directory is created.
def test_work_runs_under_output_and_is_removed_after_success(engine, tmp_path, monkeypatch):
    monkeypatch.setattr("reorder_engine.application.processing.SevenZipExtractor", SyntheticExtractor)
    source = tmp_path / "cover.pdf"
    source.write_bytes(b"%PDF-1.7\ncover\n%%EOF\n" + archive(source))
    output = tmp_path / "results"
    preview = engine.dispatch("plans.create", {"input_paths": [str(source)], "output_root": str(output)})
    job = engine.dispatch("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "ws-loc"})
    final = wait_job(engine, job["job_id"])
    assert final.state == "succeeded", final.packages[0].message

    workspaces = [a for a in engine.repository.actions(job["job_id"]) if a["kind"] == "workspace"]
    assert len(workspaces) == 1
    run_dir = Path(workspaces[0]["destination"])
    assert run_dir == output / "intermediate" / "workspaces" / job["job_id"] / final.packages[0].package_id
    assert not run_dir.exists()                     # finished work cleaned up
    assert not engine.paths.work_root.exists()      # no AppData work folder for new tasks
    assert (output / "final").is_dir()              # published output is retained
    assert list(output.rglob("payload.txt"))        # the published payload survived the move


@pytest.mark.parametrize("cancelled", [False, True])
def test_workspace_note_does_not_block_failure_or_cancel_retry(engine, tmp_path, monkeypatch, cancelled):
    class FailingExtractor(SyntheticExtractor):
        def extract_with_password(self, request, password, *, dry_run=False):
            if cancelled:
                raise ProcessingCancelled()
            raise EngineError("TOOL_FAILED", "synthetic tool failure")

    monkeypatch.setattr("reorder_engine.application.processing.SevenZipExtractor", FailingExtractor)
    source = tmp_path / "cover.pdf"
    original = b"%PDF-1.7\ncover\n%%EOF\n" + archive(source)
    source.write_bytes(original)
    preview = engine.dispatch("plans.create", {"input_paths": [str(source)], "output_root": str(tmp_path / "results")})
    job = engine.dispatch("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "workspace-exception"})
    final = wait_job(engine, job["job_id"])
    assert final.state == ("cancelled" if cancelled else "failed")
    assert final.packages[0].state == final.state
    assert final.packages[0].results == []
    assert source.read_bytes() == original
    notes = engine.repository.actions(job["job_id"])
    assert notes and all(a["kind"] == "workspace" for a in notes)
    assert engine.dispatch("results.get", {"job_id": job["job_id"]})["paths"] == []
    monkeypatch.setattr("reorder_engine.application.processing.SevenZipExtractor", SyntheticExtractor)
    retry = engine.dispatch("jobs.retry", {"job_id": job["job_id"],
        "package_ids": [final.packages[0].package_id], "idempotency_key": "workspace-retry"})
    assert wait_job(engine, retry["job_id"]).state == "succeeded"


# 2. Parent directories are reused and preexisting content is never cleared; an
#    occupied fixed location yields a uniquely-owned sibling run directory.
def test_allocate_run_workspace_reuses_parents_and_preserves_content(tmp_path):
    output = tmp_path / "out"
    job_id, package_id = "job-1", "pkg-1"
    fixed = ws.planned_run_workspace(output, job_id, package_id)
    fixed.mkdir(parents=True)
    (fixed / "keep.bin").write_bytes(b"preexisting")
    sibling_note = fixed.parent / "note.txt"
    sibling_note.write_bytes(b"note")

    run = ws.allocate_run_workspace(output, job_id, package_id)
    assert run != fixed and run.parent == fixed.parent and run.is_dir()
    assert not any(run.iterdir())                              # owned, empty run dir
    assert (fixed / "keep.bin").read_bytes() == b"preexisting"  # preexisting dir untouched
    assert sibling_note.read_bytes() == b"note"

    fresh = ws.allocate_run_workspace(tmp_path / "out2", "job-2", "pkg-2")
    assert fresh == ws.planned_run_workspace(tmp_path / "out2", "job-2", "pkg-2")


# 3. A symlink/junction component under the output root must be rejected before
#    anything is created inside the link target.
def test_workspace_guard_rejects_link_escape(tmp_path):
    real = tmp_path / "outside"
    real.mkdir()
    output = tmp_path / "out"
    output.mkdir()
    (output / "intermediate").symlink_to(real, target_is_directory=True)
    with pytest.raises(EngineError) as exc:
        ws.allocate_run_workspace(output, "job", "pkg")
    assert exc.value.code == "UNSAFE_OUTPUT"
    assert list(real.iterdir()) == []  # nothing escaped into the link target


# 4. Publishing a directory merges into an existing same-named directory: old files
#    are never overwritten and file collisions go to _duplicates.
def test_publish_directory_merges_reusing_existing_directories(engine, tmp_path):
    output = tmp_path / "out"
    target = output / "final" / "payload_dir"
    target.mkdir(parents=True)
    (target / "keep.txt").write_bytes(b"keep")
    (target / "dup.txt").write_bytes(b"old")
    source = tmp_path / "stage" / "payload_dir"
    source.mkdir(parents=True)
    (source / "dup.txt").write_bytes(b"new")
    (source / "added.txt").write_bytes(b"added")
    (source / "nested").mkdir()
    (source / "nested" / "deep.txt").write_bytes(b"deep")

    plan = _plan_for_engine(engine, tmp_path, output)
    job = engine.repository.create_job(plan, "merge")
    transaction = FileTransaction(engine.repository, job_id=job.job_id,
        package_id=plan.packages[0].package_id, output_root=output)

    result = transaction.publish(source, target)
    assert result == str(target)
    assert (target / "keep.txt").read_bytes() == b"keep"       # untouched existing file
    assert (target / "dup.txt").read_bytes() == b"old"         # collision never overwritten
    assert (target / "added.txt").read_bytes() == b"added"
    assert (target / "nested" / "deep.txt").read_bytes() == b"deep"
    # Collisions keep the existing safe convention: an exclusive copy alongside the
    # merge target, never an overwrite of the reused directory's file.
    duplicates = list((target / "_duplicates").rglob("dup.txt"))
    assert len(duplicates) == 1 and duplicates[0].read_bytes() == b"new"
    assert not engine.repository.incomplete_actions()          # every action committed


def _plan_for_engine(engine, tmp_path, output):
    source = tmp_path / "cover.bin"
    if not source.exists():
        source.write_bytes(b"cover")
    return single_package_plan(engine, source, output)


# 5. On the shared work/output volume, publishing and routing are exclusive moves and
#    never copy or re-hash source bytes.
def test_same_volume_publish_and_route_do_not_copy_or_rehash(engine, tmp_path, monkeypatch):
    calls: list[tuple[str, str]] = []
    real_copy = ft.copy_verified

    def spy(source, destination, **kwargs):
        calls.append((str(source), str(destination)))
        return real_copy(source, destination, **kwargs)

    monkeypatch.setattr(ft, "copy_verified", spy)
    output = tmp_path / "out"
    plan = _plan_for_engine(engine, tmp_path, output)
    job = engine.repository.create_job(plan, "move")
    transaction = FileTransaction(engine.repository, job_id=job.job_id,
        package_id=plan.packages[0].package_id, output_root=output)

    published = tmp_path / "payload.bin"
    published.write_bytes(b"payload")
    transaction.publish(published, output / "final" / "payload.bin")

    original = tmp_path / "archive.7z"
    original.write_bytes(b"original archive")
    package = PlannedPackage(package_id=str(uuid4()), name="archive.7z", group_key="a",
        entry=str(original), members=[source_snapshot(original)])
    transaction.route_sources(package, output / "success" / "archives")

    assert calls == []  # no copy/hash on the same-volume fast path
    assert (output / "final" / "payload.bin").read_bytes() == b"payload"
    assert (output / "success" / "archives" / "archive.7z").read_bytes() == b"original archive"
    assert not original.exists()
    assert not engine.repository.incomplete_actions()


# 6. runtime_passwords returns only the user library; built-in defaults are never
#    appended and the legacy switch cannot resurrect removed values.
def test_runtime_passwords_are_only_the_editable_library(engine):
    engine.secrets.replace(["user-only"])

    class StubDefaults:
        passwords = ("builtin-only",)
        keywords = ()

    engine.processor.builtin = StubDefaults()
    assert engine.processor.runtime_passwords(ProcessingOptions()) == ("user-only",)
    if "use_builtin_passwords" in ProcessingOptions.model_fields:
        legacy = ProcessingOptions(use_builtin_passwords=False)
        assert engine.processor.runtime_passwords(legacy) == ("user-only",)
    engine.secrets.replace([])  # an intentionally empty library stays empty
    assert engine.processor.runtime_passwords(ProcessingOptions()) == ()


# 7. The runner applies a default cwd and TMP/TEMP/TMPDIR env to the tool process
#    only, never mutating the parent environment.
def test_command_runner_default_cwd_and_env_are_process_local(tmp_path):
    workdir = tmp_path / "tooltmp"
    workdir.mkdir()
    marker = "REORDER_WS031_ENV"
    script = workdir / "probe.py"
    script.write_text(
        "import json, os\n"
        "print(json.dumps({'cwd': os.getcwd(), 'TMP': os.environ.get('TMP'),"
        " 'TEMP': os.environ.get('TEMP'), 'TMPDIR': os.environ.get('TMPDIR'),"
        " 'MARK': os.environ.get('REORDER_WS031_ENV')}))\n",
        encoding="utf-8")
    runner = ExternalCommandRunner(encoding="utf-8", cwd=workdir,
        env={"TMP": str(workdir), "TEMP": str(workdir), "TMPDIR": str(workdir), marker: "1"})
    result = runner.run([sys.executable, str(script)])
    assert result.ok, result.stdout
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["cwd"] == os.path.realpath(workdir)
    assert data["TMP"] == str(workdir) == data["TEMP"] == data["TMPDIR"]
    assert data["MARK"] == "1"
    assert marker not in os.environ  # parent environment untouched


# 8. The pre-flight disk quota is measured on the output volume, never on AppData.
def test_disk_quota_checked_on_output_volume(engine, tmp_path, monkeypatch):
    recorded: list[Path] = []
    real_usage = shutil.disk_usage

    def spy(path):
        recorded.append(Path(path))
        return real_usage(path)

    monkeypatch.setattr("reorder_engine.application.processing.shutil.disk_usage", spy)
    monkeypatch.setattr("reorder_engine.application.processing.SevenZipExtractor", SyntheticExtractor)
    source = tmp_path / "cover.pdf"
    source.write_bytes(b"%PDF-1.7\ncover\n%%EOF\n" + archive(source))
    output = tmp_path / "results"
    plan = engine.planner.create(PlanRequest(input_paths=[str(source)], output_root=str(output)))
    engine.repository.save_plan(plan)
    job = engine.repository.create_job(plan, "disk")
    engine.processor.process(plan.packages[0], plan.options, job_id=job.job_id, output_root=output,
        cancel=threading.Event(), progress=lambda _: None, log=lambda _: None)

    assert recorded
    resolved_output = output.resolve()
    resolved_data = engine.paths.data_root.resolve()
    for path in recorded:
        resolved = path.resolve()
        assert resolved == resolved_output or resolved_output in resolved.parents
        assert resolved != resolved_data and resolved_data not in resolved.parents


# 9. Recovery finds output-based work (journaled location first, deterministic path
#    otherwise) and keeps the legacy AppData fallback for pre-upgrade tasks.
def test_recovery_finds_output_work_and_prefers_journal(engine, tmp_path):
    output = tmp_path / "out"
    plan = _plan_for_engine(engine, tmp_path, output)
    job = engine.repository.create_job(plan, "recover-output")
    package_id = plan.packages[0].package_id

    fixed = ws.planned_run_workspace(output, job.job_id, package_id)
    fixed.mkdir(parents=True)
    (fixed / "legacy.bin").write_bytes(b"legacy fixed location")

    engine.runner._register_retained_workspaces([{"job_id": job.job_id, "package_id": package_id}])
    notes = [a for a in engine.repository.actions(job.job_id) if a["kind"] == "recovery_note"]
    assert [Path(n["destination"]) for n in notes] == [fixed]
    assert (fixed / "legacy.bin").read_bytes() == b"legacy fixed location"  # review-only, not moved

    # A journaled unique run directory wins over the deterministic path.
    run = ws.allocate_run_workspace(output, job.job_id, package_id)
    assert run != fixed
    action = engine.repository.record_action({"job_id": job.job_id, "package_id": package_id,
        "kind": "workspace", "source": "", "destination": str(run), "snapshot": None})
    engine.repository.action_phase(action, "committed")
    engine.runner._register_retained_workspaces([{"job_id": job.job_id, "package_id": package_id}])
    tracked = [a for a in engine.repository.actions(job.job_id) if a["kind"] == "workspace"]
    assert [Path(a["destination"]) for a in tracked] == [run]


def test_recovery_falls_back_to_legacy_appdata_work(engine, tmp_path):
    output = tmp_path / "out"
    plan = _plan_for_engine(engine, tmp_path, output)
    job = engine.repository.create_job(plan, "recover-legacy")
    package_id = plan.packages[0].package_id
    legacy = engine.paths.work_root / job.job_id / package_id
    legacy.mkdir(parents=True)
    (legacy / "partial.out").write_bytes(b"partial")

    engine.runner._register_retained_workspaces([{"job_id": job.job_id, "package_id": package_id}])
    notes = [a for a in engine.repository.actions(job.job_id) if a["kind"] == "recovery_note"]
    assert [Path(n["destination"]) for n in notes] == [legacy]
    assert (legacy / "partial.out").read_bytes() == b"partial"


# 10. A link in the chosen-root ancestry (above the output folder) must be rejected
#     before anything is created, so work cannot appear outside the chosen root.
def test_allocate_rejects_ancestor_link_before_creating(tmp_path):
    real = tmp_path / "real_parent"
    real.mkdir()
    linked = tmp_path / "linked_parent"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(EngineError):
        ws.allocate_run_workspace(linked / "out", "job", "pkg")
    assert not (real / "out").exists()  # nothing created through the ancestor link


# 11. A link at an output destination is detected before its parent is created, so
#     routing never writes through the link into another location.
def test_route_destination_link_is_not_traversed(engine, tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (output / "success").symlink_to(outside, target_is_directory=True)
    source = tmp_path / "archive.7z"
    source.write_bytes(b"payload")
    package = PlannedPackage(package_id=str(uuid4()), name="archive.7z", group_key="a",
        entry=str(source), members=[source_snapshot(source)])
    plan = _plan_for_engine(engine, tmp_path, output)
    job = engine.repository.create_job(plan, "link-dest")
    transaction = FileTransaction(engine.repository, job_id=job.job_id,
        package_id=plan.packages[0].package_id, output_root=output)
    with pytest.raises(EngineError):
        transaction.route_sources(package, output / "success" / "archives")
    assert list(outside.iterdir()) == []       # nothing written through the link
    assert source.read_bytes() == b"payload"   # original untouched


# 12. Same-volume routing stages every target link before removing any original, so
#     an allocation error leaves complete originals and reusable target links.
def test_same_volume_route_keeps_originals_until_targets_ready(engine, tmp_path, monkeypatch):
    first = tmp_path / "v.001"
    first.write_bytes(b"first")
    second = tmp_path / "v.002"
    second.write_bytes(b"second")
    package = PlannedPackage(package_id=str(uuid4()), name="v.001", group_key="v",
        entry=str(first), members=[source_snapshot(first), source_snapshot(second)])
    output = tmp_path / "out"
    plan = _plan_for_engine(engine, tmp_path, output)
    job = engine.repository.create_job(plan, "two-phase")
    transaction = FileTransaction(engine.repository, job_id=job.job_id,
        package_id=plan.packages[0].package_id, output_root=output)

    real_link = ft.link_exclusive
    calls = {"count": 0}

    def flaky_link(source, target):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("synthetic allocation failure")
        return real_link(source, target)

    monkeypatch.setattr(ft, "link_exclusive", flaky_link)
    with pytest.raises(OSError):
        transaction.route_sources(package, output / "success" / "archives")
    assert first.read_bytes() == b"first" and second.read_bytes() == b"second"  # no source removed
    assert (output / "success" / "archives" / "v.001").read_bytes() == b"first"  # reusable link


# 13. Where hard links are unavailable the same-volume path still moves with an
#     exclusive rename, removing the source only after the target exists.
def test_same_volume_route_without_hardlinks_still_moves(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(ft, "link_exclusive", lambda source, target: False)
    source = tmp_path / "v.001"
    source.write_bytes(b"payload")
    package = PlannedPackage(package_id=str(uuid4()), name="v.001", group_key="v",
        entry=str(source), members=[source_snapshot(source)])
    output = tmp_path / "out"
    plan = _plan_for_engine(engine, tmp_path, output)
    job = engine.repository.create_job(plan, "no-hardlink")
    transaction = FileTransaction(engine.repository, job_id=job.job_id,
        package_id=plan.packages[0].package_id, output_root=output)
    results = transaction.route_sources(package, output / "success" / "archives")
    assert results == [str(output / "success" / "archives" / "v.001")]
    assert (output / "success" / "archives" / "v.001").read_bytes() == b"payload"
    assert not source.exists()
    assert not engine.repository.incomplete_actions()
