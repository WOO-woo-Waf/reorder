from __future__ import annotations

from pathlib import Path

from reorder_engine.domain.models import ArchiveProbe, ExtractionRequest, ExtractionResult
from reorder_engine.infrastructure.command_runner import ExternalCommandRunner
from reorder_engine.infrastructure.tools import BandizipCli, SevenZipCli, UnrarCli
from reorder_engine.interfaces.extracting import ExtractorStrategy


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
    def __init__(self, extractors: list[ExtractorStrategy], *, compatibility_policy: ToolCompatibilityPolicy | None = None):
        self._extractors = extractors
        self._compatibility_policy = compatibility_policy or DefaultToolCompatibilityPolicy()

    def _failure_disposition(self, message: str | None) -> str:
        """Classify failures to reduce useless retries.

        Returns:
        - "retry"        : keep trying passwords/tools
        - "next_tool"    : don't try more passwords for this tool; try next tool once
        - "stop_all"     : stop immediately (e.g. missing volume)
        """

        if not message:
            return "retry"

        m = message.lower()

        # Multi-volume missing parts: retrying with other tools/passwords won't help.
        if "missing volume" in m or "unavailable data" in m:
            return "stop_all"

        # Definitive "not an archive" signals.
        if "is not archive" in m:
            return "stop_all"

        # 7z may emit a WARNING like:
        #   "Cannot open the file as [7z] archive" + "The file is open as [zip] archive"
        # This is not a definitive failure (it often means the extension is misleading).
        if "cannot open the file as" in m:
            if "the file is open as" in m:
                return "retry"
            # ambiguous: try next tool rather than stopping entirely
            return "next_tool"

        # Tool-specific definitive failures.
        if "unknown archive" in m:
            return "stop_all"
        if "not rar" in m and "archive" in m:
            return "stop_all"
        if "不是 rar" in message or "不是rar" in message:
            return "stop_all"
        if "没有要解压的文件" in message:
            return "stop_all"

        return "retry"

    def extract_one(
        self,
        request: ExtractionRequest,
        *,
        preference: str = "auto",
        probe: ArchiveProbe | None = None,
        dry_run: bool = False,
    ) -> ExtractionResult:
        """按顺序轮流尝试（工具×密码）直到成功。

        preference:
        - auto：按注册顺序
        - 7z/unrar/bandizip：把指定工具放到首位，其余作为 fallback
        """

        effective_preference = preference
        if effective_preference in {"", "auto"}:
            preferred = self._compatibility_policy.preferred_tool(probe)
            if preferred:
                effective_preference = preferred

        ordered = self._order_extractors(effective_preference)
        ordered = [e for e in ordered if e.is_available()]
        if not ordered:
            return ExtractionResult(volume_set=request.volume_set, ok=False, tool="none", message="No available extractor.")

        # Always probe with no password first. If the tool reports a definitive failure
        # (e.g. missing volumes / not an archive), do not spam retries across passwords.
        passwords: list[str] = [p for p in request.passwords if p]

        last: ExtractionResult | None = None
        for ext in ordered:
            # 1) First attempt without password
            last = self._try_extract(ext, request, None, dry_run=dry_run)
            if last.ok:
                return last

            disp = self._failure_disposition(last.message)
            if disp == "stop_all":
                return last
            if disp == "next_tool":
                continue

            # 2) Then try passwords (if any)
            for pwd in passwords:
                last = self._try_extract(ext, request, pwd, dry_run=dry_run)
                if last.ok:
                    return last
                disp = self._failure_disposition(last.message)
                if disp in {"stop_all", "next_tool"}:
                    break
            if disp == "stop_all":
                return last

        return last or ExtractionResult(volume_set=request.volume_set, ok=False, tool="none", message="No attempt executed")

    def _order_extractors(self, preference: str) -> list[ExtractorStrategy]:
        pref = preference.lower().strip()
        if pref in {"auto", ""}:
            return list(self._extractors)
        # 把匹配的放到最前
        primary = [e for e in self._extractors if e.name() == pref]
        rest = [e for e in self._extractors if e.name() != pref]
        return primary + rest

    def _try_extract(self, extractor: ExtractorStrategy, request: ExtractionRequest, password: str | None, *, dry_run: bool) -> ExtractionResult:
        # 兼容：如果 extractor 有 extract_with_password 则用它，否则回退到 extract
        fn = getattr(extractor, "extract_with_password", None)
        if callable(fn):
            return fn(request, password, dry_run=dry_run)
        return extractor.extract(request, dry_run=dry_run)
