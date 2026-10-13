from __future__ import annotations

from pathlib import Path
from typing import Callable

from reorder_engine.domain.models import ArchiveProbe, ExtractionRequest, ExtractionResult
from reorder_engine.infrastructure.command_runner import ExternalCommandRunner
from reorder_engine.infrastructure.tools import BandizipCli, SevenZipCli, UnrarCli, contains_nonempty_regular_file
from reorder_engine.interfaces.extracting import ExtractorStrategy


_PARTIAL_OUTPUT_WARNING = (
    "解压工具返回非零退出码，但输出目录已产生非空文件；按用户接受损坏、磁盘优先的策略保留这些结果。原始错误："
)


class SevenZipExtractor(ExtractorStrategy):
    def __init__(self, runner: ExternalCommandRunner, exe: str | None = None):
        self._cli = SevenZipCli(runner, exe=exe)

    def name(self) -> str:
        return "7z"

    def is_available(self) -> bool:
        return self._cli.is_available()

    def extract(self, request: ExtractionRequest, *, dry_run: bool = False) -> ExtractionResult:
        if dry_run:
            return ExtractionResult(volume_set=request.volume_set, ok=True, tool=self.name(), message="dry-run", password=None)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        res = self._cli.extract(request.volume_set.entry, request.output_dir)
        ok = res.exit_code in (0, 1)
        return ExtractionResult(
            volume_set=request.volume_set,
            ok=ok,
            tool=self.name(),
            exit_code=res.exit_code,
            message=(res.stderr.strip() or res.stdout.strip() or None),
            password=None,
        )

    def extract_with_password(self, request: ExtractionRequest, password: str | None, *, dry_run: bool = False) -> ExtractionResult:
        if dry_run:
            return ExtractionResult(volume_set=request.volume_set, ok=True, tool=self.name(), message="dry-run", password=password)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        res = self._cli.extract(request.volume_set.entry, request.output_dir, password=password)
        ok = res.exit_code in (0, 1)
        return ExtractionResult(
            volume_set=request.volume_set,
            ok=ok,
            tool=self.name(),
            exit_code=res.exit_code,
            message=(res.stderr.strip() or res.stdout.strip() or None),
            password=password,
        )


class BandizipExtractor(ExtractorStrategy):
    def __init__(self, runner: ExternalCommandRunner, exe: str | None = None):
        self._cli = BandizipCli(runner, exe=exe)

    def name(self) -> str:
        return "bandizip"

    def is_available(self) -> bool:
        return self._cli.is_available()

    def extract(self, request: ExtractionRequest, *, dry_run: bool = False) -> ExtractionResult:
        if dry_run:
            return ExtractionResult(volume_set=request.volume_set, ok=True, tool=self.name(), message="dry-run", password=None)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        res = self._cli.extract(request.volume_set.entry, request.output_dir)
        return ExtractionResult(
            volume_set=request.volume_set,
            ok=res.ok,
            tool=self.name(),
            exit_code=res.exit_code,
            message=(res.stderr.strip() or res.stdout.strip() or None),
            password=None,
        )

    def extract_with_password(self, request: ExtractionRequest, password: str | None, *, dry_run: bool = False) -> ExtractionResult:
        if dry_run:
            return ExtractionResult(volume_set=request.volume_set, ok=True, tool=self.name(), message="dry-run", password=password)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        res = self._cli.extract(request.volume_set.entry, request.output_dir, password=password)
        return ExtractionResult(
            volume_set=request.volume_set,
            ok=res.ok,
            tool=self.name(),
            exit_code=res.exit_code,
            message=(res.stderr.strip() or res.stdout.strip() or None),
            password=password,
        )


class UnrarExtractor(ExtractorStrategy):
    def __init__(self, runner: ExternalCommandRunner, exe: str | None = None):
        self._cli = UnrarCli(runner, exe=exe)

    def name(self) -> str:
        return "unrar"

    def is_available(self) -> bool:
        return self._cli.is_available()

    def extract(self, request: ExtractionRequest, *, dry_run: bool = False) -> ExtractionResult:
        return self.extract_with_password(request, None, dry_run=dry_run)

    def extract_with_password(self, request: ExtractionRequest, password: str | None, *, dry_run: bool = False) -> ExtractionResult:
        if dry_run:
            return ExtractionResult(volume_set=request.volume_set, ok=True, tool=self.name(), message="dry-run", password=password)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        res = self._cli.extract(request.volume_set.entry, request.output_dir, password=password)
        return ExtractionResult(
            volume_set=request.volume_set,
            ok=res.ok,
            tool=self.name(),
            exit_code=res.exit_code,
            message=(res.stderr.strip() or res.stdout.strip() or None),
            password=password,
        )


class ToolCompatibilityPolicy:
    def preferred_tool(self, probe: ArchiveProbe | None) -> str | None:
        raise NotImplementedError


class DefaultToolCompatibilityPolicy(ToolCompatibilityPolicy):
    def preferred_tool(self, probe: ArchiveProbe | None) -> str | None:
        if probe is None:
            return None
        return probe.preferred_tool


class ExtractionService:
    # An earlier password failure is more actionable than a later "this tool cannot
    # read the archive" message and must not be masked by it. Any other final error
    # (for example a disk or write error) is kept as-is.
    _PASSWORD_FAILURE_MARKERS = (
        "wrong password",
        "password is incorrect",
        "incorrect password",
        "invalid password",
        "illegal password",
        "encrypted",
        "密码错误",
        "密码不正确",
        "口令错误",
        "非法密码",
        "加密",
    )
    # Hard stops: no password/tool combination can repair these.
    _STOP_ALL_MARKERS = (
        "missing volume",
        "unavailable data",
        "no space",
        "not enough space",
        "disk full",
        "disk space",
        "write error",
        "error writing",
        "failed to write",
        "cannot write",
        "can not write",
        "permission denied",
        "access is denied",
        "access denied",
        "operation not permitted",
        "无法写入",
        "写入错误",
        "磁盘空间",
        "空间不足",
        "拒绝访问",
        "权限不足",
        "没有权限",
    )
    # Corruption markers (for example a trailing CRC/Data Error) are not password
    # failures: re-driving the whole password matrix for them just repeats the same
    # corrupt read up to ~115 times. They only advance to the next tool once.
    _CORRUPTION_MARKERS = (
        "data error",
        "crc error",
        "crc failed",
        "checksum error",
        "corrupt",
        "数据错误",
        "校验错误",
    )
    # Exit codes no password/tool retry can salvage: 7 command line error,
    # 8 out of memory, 255 user break, 124 runner timeout. Continuing would just
    # re-drive the whole password matrix for a failure that can never succeed.
    _STOP_ALL_EXIT_CODES = frozenset({7, 8, 124, 255})
    # Exit codes that must never be reported as partial success: a timeout or a
    # user break is an interruption, not recoverable output.
    _NEVER_PARTIAL_EXIT_CODES = frozenset({124, 255})
    _FORMAT_MISMATCH_MARKERS = (
        "not a rar archive",
        "not rar archive",
        "is not rar",
        "not a rar",
        "can not open the file as archive",
        "cannot open the file as archive",
        # 7-Zip names its own format in brackets on a mismatch, e.g. the real
        # "Cannot open the file as [zip] archive" / "... as [7z] archive". Match
        # the bracketed form too, otherwise those lines fall through to retry and
        # re-drive the whole password list.
        "open the file as [",
        "is not archive",
        "not archive",
        "unsupported archive type",
        "不是 rar",
        "不是rar",
        "不是压缩文件",
        "不是有效的压缩文件",
        "无法作为压缩文件打开",
    )

    def __init__(
        self,
        extractors: list[ExtractorStrategy],
        *,
        compatibility_policy: ToolCompatibilityPolicy | None = None,
        attempt_sink: Callable[[str, ExtractionRequest, str | None], None] | None = None,
    ):
        self._extractors = extractors
        self._compatibility_policy = compatibility_policy or DefaultToolCompatibilityPolicy()
        self._attempt_sink = attempt_sink

    @staticmethod
    def _message_contains(message: str | None, markers: tuple[str, ...]) -> bool:
        if not message:
            return False
        lowered = message.lower()
        return any(marker in lowered for marker in markers)

    def _looks_like_password_failure(self, message: str | None) -> bool:
        return self._message_contains(message, self._PASSWORD_FAILURE_MARKERS)

    def _looks_like_format_mismatch(self, message: str | None) -> bool:
        return self._message_contains(message, self._FORMAT_MISMATCH_MARKERS)

    @classmethod
    def classify_failure(cls, message: str | None, *, exit_code: int | None = None) -> str:
        """Classify a failure while preserving the full tool x password matrix.

        Shared by the service retry loop and the pipeline, which must not run an
        in-place rename or disguise restore after a serious no-output failure.

        Returns:
        - "retry"        : keep trying passwords/tools
        - "next_tool"    : don't try more passwords for this tool; try next tool once
        - "stop_all"     : stop immediately (e.g. missing volume)
        """

        # Command-line/router errors, out of memory, timeouts and user breaks can
        # never be fixed by another password or tool.
        if exit_code in cls._STOP_ALL_EXIT_CODES:
            return "stop_all"

        if not message:
            return "retry"

        m = message.lower()

        # Missing volumes, disk write failures and permission errors cannot be fixed
        # by another password or tool.
        if any(marker in m for marker in cls._STOP_ALL_MARKERS):
            return "stop_all"

        # A real password failure keeps walking the password list for this tool.
        if any(marker in m for marker in cls._PASSWORD_FAILURE_MARKERS):
            return "retry"

        # A format/tool mismatch means more passwords on this tool are pointless;
        # move to the next tool once. Routed through the same helper as
        # ``_looks_like_format_mismatch`` so both entries never diverge.
        if cls._message_contains(message, cls._FORMAT_MISMATCH_MARKERS):
            return "next_tool"

        # Trailing CRC/Data Error on a correctly read archive is not a password
        # problem; advance once instead of repeating the read for every password.
        if any(marker in m for marker in cls._CORRUPTION_MARKERS):
            return "next_tool"

        return "retry"

    def _failure_disposition(self, message: str | None, *, exit_code: int | None = None) -> str:
        """Classify failures; see classify_failure for the disposition."""

        return self.classify_failure(message, exit_code=exit_code)

    def extract_one(
        self,
        request: ExtractionRequest,
        *,
        preference: str = "auto",
        probe: ArchiveProbe | None = None,
        dry_run: bool = False,
    ) -> ExtractionResult:
        """Try the configured tool/password matrix until one attempt succeeds."""

        effective_preference = preference
        if effective_preference in {"", "auto"}:
            preferred = self._compatibility_policy.preferred_tool(probe)
            if preferred:
                effective_preference = preferred

        ordered = self._order_extractors(effective_preference)
        ordered = [e for e in ordered if e.is_available()]
        if not ordered:
            return ExtractionResult(volume_set=request.volume_set, ok=False, tool="none", message="No available extractor.")

        # Prefer a password that already worked for the same package/layer, then
        # preserve the full fallback matrix.
        passwords = self._password_attempt_order(request)

        last: ExtractionResult | None = None
        password_failure: ExtractionResult | None = None
        for ext in ordered:
            for pwd in passwords:
                last = self._try_extract(ext, request, pwd, dry_run=dry_run)
                if last.ok:
                    return last
                if self._looks_like_password_failure(last.message):
                    password_failure = last
                disp = self._failure_disposition(last.message, exit_code=last.exit_code)
                if disp == "stop_all":
                    return last
                if disp == "next_tool":
                    break

        if last is None:
            return ExtractionResult(volume_set=request.volume_set, ok=False, tool="none", message="No attempt executed")
        # The final tool may only be able to say "not an archive" while an earlier
        # attempt already showed the archive needs a password. Report that earlier,
        # more actionable failure instead of the format message.
        if password_failure is not None and self._looks_like_format_mismatch(last.message):
            return password_failure
        return last

    def _password_attempt_order(self, request: ExtractionRequest) -> list[str | None]:
        ordered: list[str | None] = []
        seen: set[str | None] = set()
        if request.preferred_password:
            ordered.append(request.preferred_password)
            seen.add(request.preferred_password)
        if None not in seen:
            ordered.append(None)
            seen.add(None)
        for password in request.passwords:
            if not password or password in seen:
                continue
            ordered.append(password)
            seen.add(password)
        return ordered

    def _order_extractors(self, preference: str) -> list[ExtractorStrategy]:
        pref = preference.lower().strip()
        if pref in {"auto", ""}:
            return list(self._extractors)
        primary = [e for e in self._extractors if e.name() == pref]
        rest = [e for e in self._extractors if e.name() != pref]
        return primary + rest

    def _try_extract(self, extractor: ExtractorStrategy, request: ExtractionRequest, password: str | None, *, dry_run: bool) -> ExtractionResult:
        if self._attempt_sink:
            self._attempt_sink(extractor.name(), request, password)
        fn = getattr(extractor, "extract_with_password", None)
        if callable(fn):
            result = fn(request, password, dry_run=dry_run)
        else:
            result = extractor.extract(request, dry_run=dry_run)
        if not dry_run:
            partial = self._partial_result_for_output(result, request, extractor.name(), password)
            if partial is not None:
                return partial
        return result

    def _partial_result_for_output(
        self,
        result: ExtractionResult,
        request: ExtractionRequest,
        tool: str,
        password: str | None,
    ) -> ExtractionResult | None:
        """Keep a non-zero attempt that still wrote non-empty output.

        The user accepts corrupted output and prefers the bytes on disk over a
        clean retry, so such an attempt is reported as a partial success and the
        caller stops the whole tool/password matrix. This covers a tool that still
        reports ``ok`` for a warning code (7-Zip exit 1) as long as the exit code is
        non-zero. Timeouts and user breaks are never converted; cancellation and
        guard errors already raise before reaching here.
        """

        # A genuine success (exit 0 / no exit code) is returned untouched.
        if result.ok and result.exit_code in (0, None):
            return None
        if result.exit_code in self._NEVER_PARTIAL_EXIT_CODES:
            return None
        if not contains_nonempty_regular_file(request.output_dir):
            return None
        original = (result.message or "").strip() or "解压工具返回非零退出码。"
        return ExtractionResult(
            volume_set=request.volume_set,
            ok=True,
            tool=tool,
            exit_code=result.exit_code,
            password=password,
            source_volume_set=result.source_volume_set,
            partial_output=True,
            message=f"{_PARTIAL_OUTPUT_WARNING}{original}",
        )
