"""Regression: an earlier password failure must survive a later format mismatch.

Everything here is synthetic: a fake ``ExtractorStrategy`` and a synthetic
``Path`` only. No CLI, GUI, archive, or pipeline content is executed, and the
failure category is derived without an EngineFacade or SQLite.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from reorder_engine.domain.models import ArchiveKind, ExtractionRequest, ExtractionResult, VolumeSet
from reorder_engine.interfaces.extracting import ExtractorStrategy
from reorder_engine.services.beta_pipeline import BetaFolderPipeline
from reorder_engine.services.extracting import ExtractionService


class FakeExtractor(ExtractorStrategy):
    """Return canned ``(ok, message)`` pairs; never touches a real tool."""

    def __init__(self, name: str, responses: list[tuple[bool, str | None]]):
        self._name = name
        self._responses = list(responses)
        self.calls: list[str | None] = []

    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return True

    def extract(self, request: ExtractionRequest, *, dry_run: bool = False) -> ExtractionResult:
        return self.extract_with_password(request, None, dry_run=dry_run)

    def extract_with_password(
        self, request: ExtractionRequest, password: str | None, *, dry_run: bool = False
    ) -> ExtractionResult:
        self.calls.append(password)
        ok, message = self._responses.pop(0)
        return ExtractionResult(volume_set=request.volume_set, ok=ok, tool=self._name,
                                message=message, password=password)


class _StubProbe:
    kind = ArchiveKind.ARCHIVE


class _StubRestore:
    """Only satisfies ``_failure_category`` without building a real pipeline."""

    def identify(self, entry: Path) -> _StubProbe:
        return _StubProbe()


def make_request(tmp_path: Path, passwords: tuple[str, ...] = ("pw1",)) -> ExtractionRequest:
    entry = tmp_path / "synthetic.rar"
    volume_set = VolumeSet(entry=entry, members=(entry,), group_key="g")
    return ExtractionRequest(volume_set=volume_set, output_dir=tmp_path / "out", passwords=passwords)


def category_of(result: ExtractionResult, entry: Path) -> str:
    pipeline = BetaFolderPipeline.__new__(BetaFolderPipeline)
    pipeline._restore = _StubRestore()
    return pipeline._failure_category(result, entry)


@pytest.mark.parametrize("password_message,format_message", [
    ("Wrong password", "not RAR archive"),
    ("ERROR: Wrong password?", "Can not open the file as archive"),
    ("密码错误", "不是压缩文件"),
    ("非法密码", "不是有效的压缩文件"),
])
def test_password_failure_is_not_masked_by_a_later_format_mismatch(tmp_path, password_message, format_message):
    req = make_request(tmp_path)
    seven = FakeExtractor("7z", [(False, password_message), (False, password_message)])
    unrar = FakeExtractor("unrar", [(False, format_message), (False, format_message)])

    result = ExtractionService([seven, unrar]).extract_one(req)

    assert not result.ok
    assert result.message == password_message  # the earlier, actionable failure survives
    assert category_of(result, req.volume_set.entry) == "password_error"
    # The full tool x password matrix is unchanged: None then pw1, for every tool.
    assert seven.calls == [None, "pw1"]
    assert unrar.calls == [None, "pw1"]


def test_later_tool_success_still_succeeds_without_shrinking_the_matrix(tmp_path):
    req = make_request(tmp_path)
    seven = FakeExtractor("7z", [(False, "Wrong password"), (False, "Wrong password")])
    unrar = FakeExtractor("unrar", [(False, "not RAR archive"), (True, "ok")])

    result = ExtractionService([seven, unrar]).extract_one(req)

    assert result.ok and result.tool == "unrar" and result.password == "pw1"
    assert seven.calls == [None, "pw1"]
    assert unrar.calls == [None, "pw1"]


def test_missing_volume_still_stops_immediately(tmp_path):
    req = make_request(tmp_path, passwords=("pw1", "pw2"))
    seven = FakeExtractor("7z", [(False, "Missing volume : synthetic.002")])
    unrar = FakeExtractor("unrar", [])

    result = ExtractionService([seven, unrar]).extract_one(req)

    assert not result.ok and result.message == "Missing volume : synthetic.002"
    assert seven.calls == [None]
    assert unrar.calls == []
    assert category_of(result, req.volume_set.entry) == "missing_volume"


def test_a_specific_final_error_is_not_replaced_by_a_password_failure(tmp_path):
    req = make_request(tmp_path)
    seven = FakeExtractor("7z", [(False, "Wrong password"), (False, "Wrong password")])
    unrar = FakeExtractor("unrar", [(False, "cannot write to the output file"), (False, "disk full")])

    result = ExtractionService([seven, unrar]).extract_one(req)

    assert result.tool == "unrar" and result.message == "disk full"  # a disk error stays visible
    assert category_of(result, req.volume_set.entry) == "extract_failed"
    pipeline = BetaFolderPipeline.__new__(BetaFolderPipeline)
    assert not pipeline._looks_like_password_error(result.message or "")


def test_format_mismatch_without_a_password_signal_is_kept(tmp_path):
    req = make_request(tmp_path)
    seven = FakeExtractor("7z", [(False, "Can not open the file as archive"), (False, "is not archive")])
    unrar = FakeExtractor("unrar", [(False, "not RAR archive"), (False, "not RAR archive")])

    result = ExtractionService([seven, unrar]).extract_one(req)

    assert result.tool == "unrar" and result.message == "not RAR archive"  # never forced to password
    assert not BetaFolderPipeline.__new__(BetaFolderPipeline)._looks_like_password_error(result.message or "")
