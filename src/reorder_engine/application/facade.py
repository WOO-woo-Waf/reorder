from __future__ import annotations

import os
import platform
from pathlib import Path

from reorder_engine.application.errors import EngineError
from reorder_engine.application.jobs import JobRunner
from reorder_engine.application.models import (DesktopSettings, PlanRequest, StartRequest,
    JobIdRequest, RetryRequest, EventsRequest, LogsRequest, ListRequest,
    PasswordRequest, PasswordImportRequest)
from reorder_engine.application.planning import PlanService
from reorder_engine.application.processing import PackageProcessor
from reorder_engine.infrastructure.builtin_defaults import BuiltinDefaults
from reorder_engine.infrastructure.desktop_paths import DesktopPaths
from reorder_engine.infrastructure.engine_lock import EngineLock
from reorder_engine.infrastructure.job_repository import JobRepository
from reorder_engine.infrastructure.secret_store import SecretStore, PasswordFile, parse_password_text
from reorder_engine.infrastructure.settings_repository import SettingsRepository

METHODS = frozenset({"system.info", "plans.create", "jobs.start", "jobs.get", "jobs.list",
    "jobs.cancel", "jobs.retry", "jobs.events", "jobs.logs", "settings.get", "settings.update",
    "passwords.replace", "passwords.import", "results.get"})


class EngineFacade:
    def __init__(self, paths: DesktopPaths, *, secrets: SecretStore | None = None,
                 defaults: BuiltinDefaults | None = None):
        paths.initialize()
        self.paths = paths
        self._lock = EngineLock(paths.data_root / "engine.lock")
        self.settings = SettingsRepository(paths)
        self.repository = JobRepository(paths.data_root / "jobs.sqlite3")
        # 内置词库只读安装资源；Facade 与 Processor 共享同一实例。
        self.defaults = defaults if defaults is not None else BuiltinDefaults(paths.app_root)
        self.secrets = secrets if secrets is not None else PasswordFile(
            paths.data_root / "passwords.txt", initial=self.defaults.passwords)
        self.planner = PlanService(paths, self.settings)
        self.processor = PackageProcessor(paths, self.settings, self.repository, self.secrets,
                                          builtin=self.defaults)
        self.runner = JobRunner(self.repository, self.planner, self.processor)

    def settings_info(self) -> dict:
        settings = self.settings.get()
        options = settings.options
        try:
            passwords = self.secrets.info()
        except EngineError as exc:
            # Keep settings accessible so an external encoding/size mistake can
            # be repaired explicitly, without replacing the user's file here.
            passwords = {"count": 0, "storage": "plaintext",
                "path": str(getattr(self.secrets, "path", "")), "values": [],
                "error": str(exc), "error_code": exc.code}
        return {"settings": settings.model_dump(mode="json"),
            "tools": {name: self.settings.resolve_tool(name) for name in ("seven_zip", "unrar", "bandizip")},
            "passwords": passwords,
            "defaults": {"password_count": self.defaults.password_count,
                "keyword_count": self.defaults.keyword_count,
                "passwords_enabled": True,
                "keyword_cleaning_enabled": options.clean_builtin_keywords,
                "version": self.defaults.version or ""}}

    def _require_idle(self) -> None:
        if self.runner.busy:
            raise EngineError("BUSY", "请在任务完成后修改设置或密码。")

    def dispatch(self, method: str, params: dict) -> dict | list:
        if method not in METHODS:
            raise EngineError("METHOD_NOT_FOUND", "不支持的操作。")
        if method in {"system.info", "settings.get"}:
            if params:
                raise EngineError("INVALID_PARAMS", "该操作不接受参数。")
            if method == "system.info":
                return {"version": "0.3.1", "protocol_version": 1, "platform": platform.system(),
                    "data_root": str(self.paths.data_root), "capabilities": ["manual_batch", "restore_ab", "cancel", "retry"],
                    **self.settings_info()}
            return self.settings_info()
        if method == "settings.update":
            self._require_idle()
            self.settings.update(DesktopSettings.model_validate(params))
            return self.settings_info()
        if method.startswith("passwords."):
            self._require_idle()
            if method == "passwords.replace":
                values = PasswordRequest.model_validate(params).passwords
            else:
                path = Path(PasswordImportRequest.model_validate(params).path)
                if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024:
                    raise EngineError("INVALID_PASSWORD_FILE", "请选择不超过 512 KiB 的普通 UTF-8 密码文件。")
                try:
                    values = list(parse_password_text(path.read_text(encoding="utf-8-sig")))
                except UnicodeError as exc:
                    raise EngineError("INVALID_PASSWORD_FILE", "密码文件需要使用 UTF-8 编码。") from exc
                values = [*self.secrets.load(), *values]
            return self.secrets.replace(values)
        if method == "plans.create":
            values = dict(params)
            values.setdefault("options", self.settings.get().options.model_dump())
            plan = self.planner.create(PlanRequest.model_validate(values))
            self.repository.save_plan(plan)
            # Source identities stay in the engine. UI receives a compact read-only preview.
            return {"plan_id": plan.plan_id, "output_root": plan.output_root, "warnings": plan.warnings,
                "packages": [{"package_id": p.package_id, "name": p.name[:200], "members": len(p.members),
                              "bytes": sum(m.size for m in p.members)} for p in plan.packages]}
        if method == "jobs.start":
            request = StartRequest.model_validate(params)
            return self.runner.submit(self.repository.get_plan(request.plan_id), request.idempotency_key).model_dump(mode="json")
        if method == "jobs.retry":
            return self.runner.retry(RetryRequest.model_validate(params)).model_dump(mode="json")
        if method == "jobs.list":
            result = []
            for job in self.repository.list_jobs(ListRequest.model_validate(params).limit):
                view = self._job_view(job)
                view["package_count"] = len(job.packages)
                view["packages"] = view["packages"][:5]
                result.append(view)
            return result
        if method == "jobs.events":
            request = EventsRequest.model_validate(params)
            result = self.repository.events(request.job_id, request.after_seq, request.limit)
            snapshot = self.repository.get_job(request.job_id)
            result["snapshot"] = {"job_id": snapshot.job_id, "state": snapshot.state, "last_seq": snapshot.last_seq}
            for event in result["events"]:
                if "message" in event:
                    event["message"] = event["message"][:200]
            return result
        if method == "jobs.logs":
            request = LogsRequest.model_validate(params)
            self.repository.get_job(request.job_id)
            path = self.paths.logs_root / (request.job_id + ".log")
            if not path.exists():
                return {"lines": [], "cursor": 0}
            lines = []
            with path.open("rb") as stream:
                stream.seek(request.cursor if request.cursor <= path.stat().st_size else 0)
                for _ in range(request.limit):
                    line = stream.readline(16384)
                    if not line:
                        break
                    lines.append(self.secrets.redact(line.decode("utf-8", errors="replace").rstrip())[:1000])
                return {"lines": lines, "cursor": stream.tell()}
        request = JobIdRequest.model_validate(params)
        job = self.repository.get_job(request.job_id)
        if method == "jobs.get":
            return self._job_view(job)
        if method == "jobs.cancel":
            return self.runner.cancel(request.job_id)
        if method == "results.get":
            paths = [a["destination"] for a in self.repository.actions(job.job_id)
                     if a["kind"] != "workspace"
                     and a["phase"] in {"committed", "published", "copied", "source_removed"}
                     and Path(a["destination"]).exists()]
            # Open actions are bounded and the output root is always registered.
            root = self.repository.get_plan(job.plan_id).output_root
            return {"output_root": root, "paths": paths[:200], "total": len(paths),
                    "actions": self.repository.actions(job.job_id)[:200] if job.state == "needs_review" else []}
        raise EngineError("METHOD_NOT_FOUND", "不支持的操作。")

    @staticmethod
    def _job_view(job) -> dict:
        result = job.model_dump(mode="json")
        for package in result["packages"]:
            package["name"] = package["name"][:100]
            package["message"] = package["message"][:120]
            package["results"] = []  # Complete registered paths are available in results.get.
        return result

    def close(self) -> None:
        self.runner.close()
        self.repository.close()
        self._lock.close()
