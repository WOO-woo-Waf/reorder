from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from reorder_engine.application.errors import EngineError, ProcessingCancelled
from reorder_engine.domain.models import ExtractionResult
from reorder_engine.infrastructure import file_transaction as ft
from reorder_engine.infrastructure.source_session import DirectSourceSession, SourceRestoration
from test_desktop_work_folder import engine, archive, wait_job, single_package_plan, SyntheticExtractor


def start(engine, source, output, key="direct"):
    preview = engine.dispatch("plans.create", {"input_paths": [str(source)], "output_root": str(output)})
    job = engine.dispatch("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": key})
    return wait_job(engine, job["job_id"])


def test_zip_reads_original_without_staging_or_verified_copy(engine, tmp_path, monkeypatch):
    source = tmp_path / "input" / "bundle.zip"
    source.parent.mkdir()
    expected = archive(source)
    untouched = source.parent / "other.zip"
    untouched.write_bytes(b"not selected")
    output = tmp_path / "out"
    (output / "final" / "bundle").mkdir(parents=True)
    sentinel = output / "final" / "bundle" / "keep.txt"
    sentinel.write_bytes(b"existing")
    entry_inode = source.stat().st_ino
    entries = []

    class DirectExtractor(SyntheticExtractor):
        def extract_with_password(self, request, password, *, dry_run=False):
            entries.append(request.volume_set.entry)
            assert request.volume_set.entry == source
            assert request.volume_set.members == (source,)
            assert not any(p.name == source.name for p in (output / "intermediate").rglob("*"))
            return super().extract_with_password(request, password, dry_run=dry_run)

    def no_copy(*args, **kwargs):
        raise AssertionError("same-volume direct extraction must not copy/hash an archive")

    monkeypatch.setattr("reorder_engine.application.processing.SevenZipExtractor", DirectExtractor)
    monkeypatch.setattr(ft, "copy_verified", no_copy)
    job = start(engine, source, output)
    assert job.state == "succeeded", job.packages[0].message
    archived = output / "success" / "archives" / source.name
    assert entries and archived.read_bytes() == expected
    assert archived.stat().st_ino == entry_inode
    assert not source.exists()
    assert untouched.read_bytes() == b"not selected"
    assert sentinel.read_bytes() == b"existing"
    assert (output / "final" / "bundle" / "payload.txt").is_file()
    assert not engine.repository.incomplete_actions()


def test_same_volume_space_check_does_not_reserve_two_archives(engine, tmp_path, monkeypatch):
    source = tmp_path / "bundle.zip"
    archive(source)
    original_size = 512 * 1024 * 1024
    # A sparse original models allocation without writing 512 MiB of data.
    with source.open("ab") as handle:
        handle.truncate(original_size)
    plan = single_package_plan(engine, source, tmp_path / "out")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: SimpleNamespace(free=96 * 1024 * 1024))
    # Produce tiny synthetic output to isolate the initial free-space policy.
    class TinyOutput(SyntheticExtractor):
        def extract_with_password(self, request, password, *, dry_run=False):
            request.output_dir.mkdir(parents=True, exist_ok=True)
            (request.output_dir / "payload.txt").write_bytes(b"tiny")
            return ExtractionResult(request.volume_set, True, "synthetic")
    monkeypatch.setattr("reorder_engine.application.processing.SevenZipExtractor", TinyOutput)
    # Large sparse ZIP trailers are intentionally unreadable by the actual
    # archive inspector. This test isolates preflight allocation, not validation.
    monkeypatch.setattr("reorder_engine.application.processing.ArchiveSafetyInspector.inspect", lambda *a, **k: None)
    job = engine.dispatch("jobs.start", {"plan_id": plan.plan_id, "idempotency_key": "sparse-space"})
    final = wait_job(engine, job["job_id"])
    assert final.state == "succeeded", final.packages[0].message


def test_noop_restore_probe_is_abandoned_without_blocking_retry(engine, tmp_path):
    source = tmp_path / "cover.bin"
    source.write_bytes(b"unchanged")
    plan = single_package_plan(engine, source, tmp_path / "out")
    job = engine.repository.create_job(plan, "noop")
    session = DirectSourceSession(plan.packages[0], engine.repository, job_id=job.job_id)
    assert session.change(source, lambda: "no change") == "no change"
    assert not engine.repository.incomplete_actions()
    assert source.read_bytes() == b"unchanged"


def test_source_edits_and_renames_remain_recoverable_until_routing(engine, tmp_path):
    source = tmp_path / "cover.mp4"
    source.write_bytes(b"cover bytes")
    plan = single_package_plan(engine, source, tmp_path / "out")
    job = engine.repository.create_job(plan, "edits")
    session = DirectSourceSession(plan.packages[0], engine.repository, job_id=job.job_id)
    session.change(source, lambda: source.write_bytes(b"repaired bytes"))
    renamed = source.with_name("cover.zip")
    session.rename(source, renamed)
    routed = session.routing_package()
    assert routed.entry == str(renamed)
    assert routed.members[0].size == len(b"repaired bytes")
    assert session.volume_set().members == (renamed,)
    assert len(engine.repository.incomplete_actions()) == 2
    engine.repository.update_package(job.job_id, plan.packages[0].package_id, state="extracting")
    recovered = engine.repository.recover_interrupted()
    assert recovered and recovered[0]["state"] == "needs_review"
    transaction = ft.FileTransaction(engine.repository, job_id=job.job_id,
                                    package_id=plan.packages[0].package_id, output_root=tmp_path / "out")
    transaction.route_sources(routed, tmp_path / "out" / "success" / "archives")
    session.complete()
    assert not engine.repository.incomplete_actions()
    assert (tmp_path / "out" / "success" / "archives" / renamed.name).read_bytes() == b"repaired bytes"
    # A later file at a historical source name is not a registered result.
    source.write_bytes(b"new unrelated file")
    renamed.write_bytes(b"another unrelated file")
    engine.repository.update_job(job.job_id, state="succeeded")
    results = engine.dispatch("results.get", {"job_id": job.job_id})
    assert results["paths"] == [str(tmp_path / "out" / "success" / "archives" / renamed.name)]


def test_source_rename_collision_keeps_both_files(engine, tmp_path):
    source, target = tmp_path / "cover.mp4", tmp_path / "cover.zip"
    source.write_bytes(b"source")
    target.write_bytes(b"existing")
    plan = single_package_plan(engine, source, tmp_path / "out")
    job = engine.repository.create_job(plan, "collision")
    session = DirectSourceSession(plan.packages[0], engine.repository, job_id=job.job_id)
    with pytest.raises(FileExistsError):
        session.rename(source, target)
    assert source.read_bytes() == b"source" and target.read_bytes() == b"existing"
    assert not engine.repository.incomplete_actions()


@pytest.mark.parametrize("cancelled", [False, True])
def test_partially_changed_source_is_not_abandoned_on_exception(engine, tmp_path, cancelled):
    source = tmp_path / "cover.bin"
    source.write_bytes(b"original")
    plan = single_package_plan(engine, source, tmp_path / "out")
    job = engine.repository.create_job(plan, "partial")
    session = DirectSourceSession(plan.packages[0], engine.repository, job_id=job.job_id)
    def partial():
        source.write_bytes(b"repaired")
        raise ProcessingCancelled() if cancelled else OSError("stopped")
    with pytest.raises(ProcessingCancelled if cancelled else OSError):
        session.change(source, partial)
    assert engine.repository.incomplete_actions()
    assert source.read_bytes() == b"repaired"


def test_external_input_change_is_not_adopted_as_our_restore(engine, tmp_path):
    source = tmp_path / "cover.bin"
    source.write_bytes(b"original")
    plan = single_package_plan(engine, source, tmp_path / "out")
    job = engine.repository.create_job(plan, "external")
    session = DirectSourceSession(plan.packages[0], engine.repository, job_id=job.job_id)
    source.write_bytes(b"external edit")
    with pytest.raises(EngineError) as exc:
        session.change(source, lambda: None)
    assert exc.value.code == "INPUT_CHANGED"


@pytest.mark.parametrize("legacy_source_parent", [True, False])
def test_derived_restore_stays_in_owned_work_folder(engine, tmp_path, legacy_source_parent):
    source = tmp_path / "cover.bin"
    source.write_bytes(b"original")
    plan = single_package_plan(engine, source, tmp_path / "out")
    job = engine.repository.create_job(plan, "derived")
    session = DirectSourceSession(plan.packages[0], engine.repository, job_id=job.job_id)
    workspace = tmp_path / "out" / "intermediate" / "owned"
    requested = source.parent if legacy_source_parent else workspace / "layer2"

    class DerivedRestorer:
        def restore_with_rollbacks(self, path, *, workspace, dry_run=False):
            workspace.mkdir(parents=True)
            derived = workspace / "recovered.zip"
            derived.write_bytes(b"derived archive")
            return [derived], []

    adapter = SourceRestoration(DerivedRestorer(), session, workspace)
    restored, records = adapter.restore_with_rollbacks(source, workspace=requested)
    assert restored[0].is_relative_to(workspace)
    if not legacy_source_parent:
        assert restored[0].parent == requested  # preserve nested recovery scope
    assert not (source.parent / "recovered.zip").exists()
    assert source.read_bytes() == b"original" and not records
    assert not engine.repository.incomplete_actions()
