from pathlib import Path

import pytest

from reorder_engine.infrastructure import file_transaction as ft
from reorder_engine.infrastructure.source_session import DirectSourceSession
from reorder_engine.domain.models import ExtractionResult
from test_desktop_work_folder import engine, archive, wait_job, single_package_plan, SyntheticExtractor


def no_copy(*args, **kwargs):
    raise AssertionError("disk-first desktop routing must not copy an original")


def test_cross_volume_retains_original_and_registers_its_current_path(engine, tmp_path, monkeypatch):
    source = tmp_path / "selected.zip"
    expected = archive(source)
    sibling = tmp_path / "unselected.zip"
    sibling.write_bytes(b"leave me")
    output = tmp_path / "work"
    (output / "final" / "selected").mkdir(parents=True)
    sentinel = output / "final" / "selected" / "existing.txt"
    sentinel.write_bytes(b"already here")
    monkeypatch.setattr(ft, "_device_of", lambda path: source.stat().st_dev + 1)
    monkeypatch.setattr(ft, "copy_verified", no_copy)
    monkeypatch.setattr("reorder_engine.application.processing.SevenZipExtractor", SyntheticExtractor)
    preview = engine.dispatch("plans.create", {"input_paths": [str(source)], "output_root": str(output)})
    job = engine.dispatch("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "cross-retain"})
    finished = wait_job(engine, job["job_id"])
    assert finished.state == "succeeded", finished.packages[0].message
    assert source.read_bytes() == expected
    assert sibling.read_bytes() == b"leave me"
    assert sentinel.read_bytes() == b"already here"
    assert (output / "final" / "selected" / "payload.txt").is_file()
    assert not (output / "success" / "archives" / source.name).exists()
    assert "跨盘" in finished.packages[0].message
    results = engine.dispatch("results.get", {"job_id": job["job_id"]})
    assert str(source) in results["paths"]
    assert not engine.repository.incomplete_actions()


def test_cross_volume_transformed_group_stays_together_without_copy(engine, tmp_path, monkeypatch):
    first, second = tmp_path / "piece.001", tmp_path / "piece.002"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    plan = single_package_plan(engine, first, tmp_path / "work")
    from reorder_engine.application.planning import source_snapshot
    package = plan.packages[0].model_copy(update={"members": [source_snapshot(first), source_snapshot(second)]})
    job = engine.repository.create_job(plan, "group-retain")
    session = DirectSourceSession(package, engine.repository, job_id=job.job_id)
    session.change(first, lambda: first.write_bytes(b"repaired"))
    monkeypatch.setattr(ft, "_device_of", lambda path: first.stat().st_dev + 1)
    monkeypatch.setattr(ft, "copy_verified", no_copy)
    transaction = ft.FileTransaction(engine.repository, job_id=job.job_id,
                                    package_id=package.package_id, output_root=tmp_path / "work")
    paths = transaction.route_sources(session.routing_package(), tmp_path / "work/success/archives",
                                     copy_across_volumes=False)
    session.complete()
    assert paths == [str(first), str(second)]
    assert first.read_bytes() == b"repaired" and second.read_bytes() == b"second"
    assert not engine.repository.incomplete_actions()


def test_cross_volume_incomplete_result_retains_original_without_success(engine, tmp_path, monkeypatch):
    class IncompleteExtractor(SyntheticExtractor):
        def extract_with_password(self, request, password, *, dry_run=False):
            request.output_dir.mkdir(parents=True, exist_ok=True)
            (request.output_dir / "partial.bin").write_bytes(b"unverified bytes")
            return ExtractionResult(request.volume_set, ok=False, tool="synthetic", exit_code=2,
                                    message="Wrong password", password=password)

    source = tmp_path / "incomplete.zip"
    expected = archive(source)
    output = tmp_path / "work"
    monkeypatch.setattr(ft, "_device_of", lambda path: source.stat().st_dev + 1)
    monkeypatch.setattr(ft, "copy_verified", no_copy)
    monkeypatch.setattr("reorder_engine.application.processing.SevenZipExtractor", IncompleteExtractor)
    preview = engine.dispatch("plans.create", {"input_paths": [str(source)], "output_root": str(output)})
    job = engine.dispatch("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "cross-incomplete"})
    finished = wait_job(engine, job["job_id"])
    assert finished.packages[0].state == "partial", finished.packages[0].message
    assert source.read_bytes() == expected
    assert not (output / "success").exists()
    assert not (output / "final").exists()
    assert any(p.name == "partial.bin" for p in (output / "incomplete" / "outputs").rglob("*"))
    results = engine.dispatch("results.get", {"job_id": job["job_id"]})
    assert str(source) in results["paths"]
    assert "跨盘" in finished.packages[0].message
    assert not engine.repository.incomplete_actions()


def test_changed_input_cannot_be_retained_as_a_verified_result(engine, tmp_path, monkeypatch):
    source = tmp_path / "one.zip"
    archive(source)
    plan = single_package_plan(engine, source, tmp_path / "work")
    job = engine.repository.create_job(plan, "changed-retain")
    source.write_bytes(b"replaced after scan")
    monkeypatch.setattr(ft, "_device_of", lambda path: 999999)
    transaction = ft.FileTransaction(engine.repository, job_id=job.job_id,
                                    package_id=plan.packages[0].package_id, output_root=tmp_path / "work")
    from reorder_engine.application.errors import EngineError
    with pytest.raises(EngineError, match="扫描后输入文件发生变化"):
        transaction.route_sources(plan.packages[0], tmp_path / "work/success/archives", copy_across_volumes=False)
    assert source.read_bytes() == b"replaced after scan"
    assert not any(a["kind"] == "retain_source" for a in engine.repository.actions(job.job_id))
