from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

from reorder_engine.application.errors import EngineError, ProcessingCancelled
from reorder_engine.domain.models import ExtractionRequest, ExtractionResult, VolumeSet
from reorder_engine.infrastructure.command_runner import ExternalCommandRunner
from reorder_engine.infrastructure.tools import (
    BandizipCli,
    SevenZipCli,
    UnrarCli,
    _extract_abort_guard,
    contains_nonempty_regular_file,
)
from reorder_engine.interfaces.extracting import ExtractorStrategy
from reorder_engine.services.extracting import ExtractionService, SevenZipExtractor


@dataclass(frozen=True)
class _CmdResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    aborted: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class _WritingExtractor(ExtractorStrategy):
    """Fake tool that records attempts, optionally writes output, then answers."""

    def __init__(self, name: str, responses: list[ExtractionResult], *, write=None, raises=None):
        self._name = name
        self._responses = list(responses)
        self._write = write
        self._raises = raises
        self.calls: list[str | None] = []

    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return True

    def extract(self, request: ExtractionRequest, *, dry_run: bool = False) -> ExtractionResult:
        return self.extract_with_password(request, None, dry_run=dry_run)

    def extract_with_password(self, request: ExtractionRequest, password: str | None, *, dry_run: bool = False) -> ExtractionResult:
        self.calls.append(password)
        if self._raises is not None:
            raise self._raises
        assert self._write is not None
        self._write(request.output_dir, password)
        return self._responses.pop(0)


class _RecordingRunner:
    """Stand-in for ExternalCommandRunner that captures the per-run abort guard."""

    def __init__(self, *, exit_code: int = 2, stdout: str = "") -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.abort = None
        self.args: list[str] = []
        self.calls = 0

    def run(self, args, *, cwd=None, timeout_sec=None, stream=None, output_sink=None, abort_on_line=None):
        self.calls += 1
        self.args = list(args)
        self.abort = abort_on_line
        return _CmdResult(self.exit_code, self.stdout)


def _write_nonempty(output_dir: Path, password: str | None) -> None:
    _ = password
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "recovered.bin").write_bytes(b"payload")


def _write_zero_byte(output_dir: Path, password: str | None) -> None:
    _ = password
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "placeholder.bin").write_bytes(b"")


def _fail(message: str, *, exit_code: int = 2) -> ExtractionResult:
    # volume_set is filled by the service-level helper; the fake pops this object
    # so we only need a placeholder here.
    return ExtractionResult(volume_set=None, ok=False, tool="fake", exit_code=exit_code, message=message)  # type: ignore[arg-type]


def _native_path(path: Path) -> str:
    """Path form a Windows tool can open (WSL ``/mnt/<drive>`` -> ``<Drive>:\\``)."""

    if os.name == "nt":
        return str(path)
    text = path.as_posix()
    if text.startswith("/mnt/") and len(text) > 6 and text[6] == "/":
        return f"{text[5].upper()}:{text[6:]}"
    return text


class ExtractionStopPolicyTests(unittest.TestCase):
    def _volume_set(self, root: Path) -> tuple[VolumeSet, ExtractionRequest]:
        archive = root / "a.7z.001"
        archive.write_text("x", encoding="utf-8")
        vs = VolumeSet(entry=archive, members=(archive,), group_key="g")
        req = ExtractionRequest(volume_set=vs, output_dir=root / "out", passwords=("pw1", "pw2"))
        return vs, req

    def test_partial_output_defaults_false(self) -> None:
        result = ExtractionResult(volume_set=None, ok=True, tool="7z")  # type: ignore[arg-type]
        self.assertFalse(result.partial_output)

    def test_nonzero_with_nonempty_output_stops_all_and_marks_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vs, req = self._volume_set(root)
            req = ExtractionRequest(volume_set=vs, output_dir=root / "out", passwords=("pw1", "pw2"))
            first = _WritingExtractor("7z", [_fail("ERROR: Data Error in file.jpg")], write=_write_nonempty)
            second = _WritingExtractor("unrar", [ExtractionResult(volume_set=vs, ok=True, tool="unrar")], write=_write_nonempty)

            result = ExtractionService([first, second]).extract_one(req)

            self.assertTrue(result.ok)
            self.assertTrue(result.partial_output)
            self.assertEqual(result.tool, "7z")
            self.assertIn("Data Error in file.jpg", result.message or "")
            self.assertIn("非空文件", result.message or "")
            # no further password on this tool, and no other tool was touched
            self.assertEqual(first.calls, [None])
            self.assertEqual(second.calls, [])

    def test_zero_byte_output_is_not_partial_and_switches_on(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vs, _ = self._volume_set(root)
            req = ExtractionRequest(volume_set=vs, output_dir=root / "out", passwords=("pw1",))
            first = _WritingExtractor(
                "7z",
                [_fail("Wrong password"), _fail("Wrong password")],
                write=_write_zero_byte,
            )
            second = _WritingExtractor("unrar", [ExtractionResult(volume_set=vs, ok=True, tool="unrar")], write=_write_zero_byte)

            result = ExtractionService([first, second]).extract_one(req)

            self.assertTrue(result.ok)
            self.assertFalse(result.partial_output)
            self.assertEqual(result.tool, "unrar")
            self.assertEqual(first.calls, [None, "pw1"])
            self.assertEqual(second.calls, [None])

    def test_format_mismatch_does_not_poll_passwords(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vs, req = self._volume_set(root)
            first = _WritingExtractor("7z", [_fail("is not archive")], write=_write_zero_byte)
            second = _WritingExtractor("unrar", [ExtractionResult(volume_set=vs, ok=True, tool="unrar")], write=_write_zero_byte)

            result = ExtractionService([first, second]).extract_one(req)

            self.assertTrue(result.ok)
            self.assertEqual(first.calls, [None])
            self.assertEqual(second.calls, [None])

    def test_crc_without_output_does_not_repeat_password_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vs, req = self._volume_set(root)
            first = _WritingExtractor("7z", [_fail("ERROR: CRC Failed in file.jpg")], write=_write_zero_byte)
            second = _WritingExtractor("unrar", [ExtractionResult(volume_set=vs, ok=True, tool="unrar")], write=_write_zero_byte)

            result = ExtractionService([first, second]).extract_one(req)

            self.assertTrue(result.ok)
            self.assertEqual(first.calls, [None])
            self.assertEqual(second.calls, [None])

    def test_missing_volume_stops_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vs, req = self._volume_set(root)
            first = _WritingExtractor("7z", [_fail("Missing volume : a.002")], write=_write_zero_byte)
            second = _WritingExtractor("unrar", [ExtractionResult(volume_set=vs, ok=True, tool="unrar")], write=_write_zero_byte)

            result = ExtractionService([first, second]).extract_one(req)

            self.assertFalse(result.ok)
            self.assertEqual(first.calls, [None])
            self.assertEqual(second.calls, [])

    def test_permission_failure_stops_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vs, req = self._volume_set(root)
            first = _WritingExtractor("7z", [_fail("ERROR: Can not write to output. Access is denied.")], write=_write_zero_byte)
            second = _WritingExtractor("unrar", [ExtractionResult(volume_set=vs, ok=True, tool="unrar")], write=_write_zero_byte)

            result = ExtractionService([first, second]).extract_one(req)

            self.assertFalse(result.ok)
            self.assertEqual(first.calls, [None])
            self.assertEqual(second.calls, [])

    def test_timeout_exit_code_is_not_converted_to_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vs, _ = self._volume_set(root)
            req = ExtractionRequest(volume_set=vs, output_dir=root / "out", passwords=())
            first = _WritingExtractor("7z", [_fail("tool timeout", exit_code=124)], write=_write_nonempty)

            result = ExtractionService([first]).extract_one(req)

            self.assertFalse(result.ok)
            self.assertFalse(result.partial_output)
            self.assertEqual(first.calls, [None])

    def test_cancel_is_not_converted_to_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vs, req = self._volume_set(root)
            first = _WritingExtractor("7z", [], write=_write_nonempty, raises=ProcessingCancelled())

            with self.assertRaises(ProcessingCancelled):
                ExtractionService([first]).extract_one(req)

    def test_guard_error_is_not_converted_to_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vs, req = self._volume_set(root)
            first = _WritingExtractor(
                "7z",
                [],
                write=_write_nonempty,
                raises=EngineError("UNSAFE_ARCHIVE", "解压结果包含链接，已停止处理。"),
            )

            with self.assertRaises(EngineError):
                ExtractionService([first]).extract_one(req)

    def test_warning_exit_code_with_output_is_partial(self) -> None:
        # 7-Zip can report ok for exit code 1 (warning); with output it is partial.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vs, _ = self._volume_set(root)
            req = ExtractionRequest(volume_set=vs, output_dir=root / "out", passwords=())
            first = _WritingExtractor(
                "7z",
                [ExtractionResult(volume_set=vs, ok=True, tool="7z", exit_code=1, message="WARNING: some files were not processed")],
                write=_write_nonempty,
            )

            result = ExtractionService([first]).extract_one(req)

            self.assertTrue(result.ok)
            self.assertTrue(result.partial_output)
            self.assertEqual(first.calls, [None])
            self.assertIn("not processed", result.message or "")

    def test_command_error_exit_code_stops_all_without_output(self) -> None:
        for code in (7, 8, 124, 255):
            with self.subTest(exit_code=code), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                vs, req = self._volume_set(root)  # passwords ("pw1", "pw2")
                first = _WritingExtractor("7z", [_fail("tool failure", exit_code=code)], write=_write_zero_byte)
                second = _WritingExtractor("unrar", [ExtractionResult(volume_set=vs, ok=True, tool="unrar")], write=_write_zero_byte)

                result = ExtractionService([first, second]).extract_one(req)

                self.assertFalse(result.ok)
                self.assertFalse(result.partial_output)
                self.assertEqual(first.calls, [None], f"exit {code} must not retry passwords")
                self.assertEqual(second.calls, [], f"exit {code} must not try other tools")

    def test_user_break_exit_code_is_never_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vs, _ = self._volume_set(root)
            req = ExtractionRequest(volume_set=vs, output_dir=root / "out", passwords=())
            first = _WritingExtractor("7z", [_fail("brake signal", exit_code=255)], write=_write_nonempty)

            result = ExtractionService([first]).extract_one(req)

            self.assertFalse(result.ok)
            self.assertFalse(result.partial_output)

    def test_mixed_password_and_format_message_keeps_retrying(self) -> None:
        # A native encrypted-header failure can look like a format error; the
        # explicit password signal must win and keep walking the password list.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vs, _ = self._volume_set(root)
            req = ExtractionRequest(volume_set=vs, output_dir=root / "out", passwords=("pw1",))
            mixed = "ERROR: Can not open the file as archive. Cannot open encrypted archive. Wrong password?"
            first = _WritingExtractor(
                "7z",
                [_fail(mixed), ExtractionResult(volume_set=vs, ok=True, tool="7z", password="pw1")],
                write=_write_zero_byte,
            )

            result = ExtractionService([first]).extract_one(req)

            self.assertTrue(result.ok)
            self.assertFalse(result.partial_output)
            self.assertEqual(first.calls, [None, "pw1"])


    def test_bracket_format_message_without_password_advances_once(self) -> None:
        # The user's real BG57 message: 7-Zip names its own format in brackets.
        user_message = (
            "Cannot open the file as [zip] archive\n"
            "Headers Error\n"
            "Unconfirmed start of archive"
        )
        # Both service entries must agree this is a format mismatch, not a password
        # problem, so the mismatch tool is advanced once instead of 115 retries.
        self.assertEqual(ExtractionService.classify_failure(user_message), "next_tool")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vs, req = self._volume_set(root)  # passwords ("pw1", "pw2")
            first = _WritingExtractor("7z", [_fail(user_message)], write=_write_zero_byte)
            second = _WritingExtractor("unrar", [ExtractionResult(volume_set=vs, ok=True, tool="unrar")], write=_write_zero_byte)

            result = ExtractionService([first, second]).extract_one(req)

            self.assertTrue(result.ok)
            self.assertEqual(first.calls, [None])  # no password polling on the mismatch tool
            self.assertEqual(second.calls, [None])

    def test_bracket_format_with_wrong_password_keeps_retrying(self) -> None:
        # A bracketed format line followed by a password signal must stay a retry:
        # the explicit password wins over the format marker.
        mixed = "Cannot open the file as [7z] archive\nERROR: Wrong password : payload\\a.txt"
        self.assertEqual(ExtractionService.classify_failure(mixed), "retry")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vs, _ = self._volume_set(root)
            req = ExtractionRequest(volume_set=vs, output_dir=root / "out", passwords=("pw1",))
            first = _WritingExtractor(
                "7z",
                [_fail(mixed), ExtractionResult(volume_set=vs, ok=True, tool="7z", password="pw1")],
                write=_write_zero_byte,
            )

            result = ExtractionService([first]).extract_one(req)

            self.assertTrue(result.ok)
            self.assertFalse(result.partial_output)
            self.assertEqual(first.calls, [None, "pw1"])
            self.assertEqual(result.password, "pw1")


class ContainsNonemptyFileTests(unittest.TestCase):
    def test_ignores_empty_special_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            out.mkdir()
            (out / "empty.bin").write_bytes(b"")
            (out / "sub").mkdir()
            (out / "sub" / "inner_empty.bin").write_bytes(b"")
            self.assertFalse(contains_nonempty_regular_file(out))

            (root / "target.bin").write_bytes(b"real")
            try:
                (out / "link.bin").symlink_to(root / "target.bin")
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            self.assertFalse(contains_nonempty_regular_file(out))

            (out / "real.bin").write_bytes(b"payload")
            self.assertTrue(contains_nonempty_regular_file(out))

    def test_missing_directory_is_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(contains_nonempty_regular_file(Path(tmp) / "nope"))


class ExtractionCommandWiringTests(unittest.TestCase):
    def test_7z_extract_passes_abort_guard_and_utf8_charset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            runner = _RecordingRunner()
            SevenZipCli(runner, exe="7z").extract(Path(tmp) / "a.7z", out)
            self.assertIsNotNone(runner.abort)
            self.assertIn("-sccUTF-8", runner.args)
            # No password uses the defined literal "-p-" (value present) so 7-Zip
            # never falls into its interactive prompt; bare "-p" is forbidden.
            self.assertIn("-p-", runner.args)
            self.assertNotIn("-p", runner.args)
            self.assertTrue(runner.abort("ERROR: Wrong password : file.jpg"))
            out.mkdir(parents=True, exist_ok=True)
            (out / "partial.bin").write_bytes(b"bytes")
            self.assertFalse(runner.abort("ERROR: Wrong password : file.jpg"))

    def test_7z_password_is_forwarded_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = _RecordingRunner()
            SevenZipCli(runner, exe="7z").extract(Path(tmp) / "a.7z", Path(tmp) / "out", password="secret")
            self.assertIn("-psecret", runner.args)

    def test_unrar_extract_keeps_partial_files_and_passes_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = _RecordingRunner()
            UnrarCli(runner, exe="unrar").extract(Path(tmp) / "a.rar", Path(tmp) / "out")
            self.assertIn("-kb", runner.args)
            self.assertIn("-p-", runner.args)
            self.assertIsNotNone(runner.abort)

    def test_bandizip_extract_passes_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = _RecordingRunner()
            BandizipCli(runner, exe="bz").extract(Path(tmp) / "a.zip", Path(tmp) / "out")
            self.assertIsNotNone(runner.abort)


class RealSubprocessAbortTests(unittest.TestCase):
    def test_run_level_guard_overrides_and_kills_process_quickly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            runner = ExternalCommandRunner(encoding="utf-8", abort_on_line=lambda _line: False)
            started = time.monotonic()
            result = runner.run(
                [
                    sys.executable,
                    "-c",
                    "import time; print('begin', flush=True); "
                    "print('ERROR: Wrong password : file.jpg', flush=True); "
                    "time.sleep(30); print('late', flush=True)",
                ],
                abort_on_line=_extract_abort_guard(out),
            )
            elapsed = time.monotonic() - started

            self.assertTrue(result.aborted)
            self.assertEqual(result.exit_code, 130)
            self.assertLess(elapsed, 10)
            self.assertNotIn("late", result.stdout)


class NativeSevenZipAesTests(unittest.TestCase):
    """Real Windows 7-Zip against the synthetic AES fixture.

    Proves the no-password probe is non-interactive (never the 255 interactive-EOF
    code) and that the following real password still recovers through the full
    ExtractionService retry, so a 255->stop_all can no longer block recovery.
    """

    def test_none_probe_is_non_interactive_then_real_password_succeeds(self) -> None:
        root = Path(__file__).resolve().parents[1]
        seven = root / "tools" / "7zip" / "Files" / "7-Zip" / "7z.exe"
        aes = root / "artifacts" / "desktop" / "hoshiribbon-0.3.3" / "research" / "h033-exp" / "aes.zip"
        if not seven.exists() or not aes.exists():
            self.skipTest("native 7z.exe or synthetic aes fixture unavailable")

        out = root / "artifacts" / "desktop" / "hoshiribbon-0.3.3" / "stop" / "native" / "aes_min"
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)

        runner = ExternalCommandRunner(encoding="utf-8", timeout_sec=60)
        # The executable must be a Linux-visible path (WSL interop spawns it); the
        # archive/output arguments are handed to the Windows tool as native paths.
        extractor = SevenZipExtractor(runner, exe=str(seven))
        native_archive = Path(_native_path(aes))
        native_out = Path(_native_path(out))
        vs = VolumeSet(entry=native_archive, members=(native_archive,), group_key="aes")
        req = ExtractionRequest(volume_set=vs, output_dir=native_out, passwords=("SECRET",))

        try:
            none_result = extractor.extract_with_password(req, None)
        except OSError as exc:  # exe not runnable in this environment (no interop)
            self.skipTest(f"cannot execute native 7z.exe: {exc}")

        # The defined value keeps 7-Zip non-interactive: no 255 (prompt + stdin EOF).
        self.assertNotEqual(none_result.exit_code, 255)
        self.assertIn("password", (none_result.message or "").lower())

        # ... and the real password still recovers through the service retry.
        service_result = ExtractionService([extractor]).extract_one(req)
        self.assertTrue(service_result.ok, service_result.message)
        self.assertFalse(service_result.partial_output)
        self.assertEqual(service_result.password, "SECRET")
        files = sorted(p.name for p in (out / "payload").rglob("*") if p.is_file())
        self.assertEqual(files, ["a.txt", "c.txt", "data.bin"])


if __name__ == "__main__":
    unittest.main()
