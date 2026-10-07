from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path
from uuid import uuid4

from reorder_engine.application.errors import EngineError, ProcessingCancelled
from reorder_engine.application.models import ProcessingPlan, RetryRequest, TERMINAL_JOB_STATES
from reorder_engine.application.planning import PlanService, source_snapshot
from reorder_engine.application.processing import PackageProcessor
from reorder_engine.infrastructure.job_repository import JobRepository, utc_now


class JobRunner:
    """One worker owns processing; protocol thread stays responsive to cancellation."""

    def __init__(self, repository: JobRepository, planner: PlanService, processor: PackageProcessor):
        self.repository, self.planner, self.processor = repository, planner, processor
        self._lock = threading.RLock()
        self._queue: queue.Queue[tuple[str, ProcessingPlan] | None] = queue.Queue()
        self._cancel: dict[str, threading.Event] = {}
        self._closed = False
        self._register_retained_workspaces(repository.recover_interrupted())
        self._thread = threading.Thread(target=self._work, name="reorder-worker", daemon=True)
        self._thread.start()

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._cancel)

    def submit(self, plan: ProcessingPlan, key: str, *, retry_of: str | None = None):
        with self._lock:
            existing = self.repository.get_by_key(key)
            if existing:
                if existing.plan_id != plan.plan_id:
                    raise EngineError("IDEMPOTENCY_CONFLICT", "请求标识已用于另一计划。")
                return existing
            if self._closed or self._cancel:
                raise EngineError("BUSY", "已有任务正在处理，请等待完成或取消。")
            self.planner.validate_sources(plan)
            job = self.repository.create_job(plan, key, retry_of=retry_of)
            self._cancel[job.job_id] = threading.Event()
            self._queue.put((job.job_id, plan))
            return job

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self.repository.get_job(job_id)
            token = self._cancel.get(job_id)
            if token and job.state not in TERMINAL_JOB_STATES:
                token.set()
                self.repository.update_job(job_id, "cancelling")
                return {"accepted": True}
            return {"accepted": False}

    def retry(self, request: RetryRequest):
        with self._lock:
            existing = self.repository.get_by_key(request.idempotency_key)
            if existing:
                if existing.retry_of != request.job_id:
                    raise EngineError("IDEMPOTENCY_CONFLICT", "重试标识冲突。")
                return existing
            old = self.repository.get_job(request.job_id)
            if old.state not in TERMINAL_JOB_STATES:
                raise EngineError("BUSY", "任务尚未结束。")
            selected = set(request.package_ids)
            eligible = {p.package_id for p in old.packages if p.state in {"failed", "deferred", "cancelled", "interrupted"}}
            if not selected <= eligible:
                raise EngineError("RETRY_NOT_ALLOWED", "只可重试失败、缺卷、取消或中断的包；需要检查的包请先人工核对。")
            plan = self.repository.get_plan(old.plan_id)
            # Routed failed sources are the retry inputs, never the restored variants.
            routed = {a["source"]: a["destination"] for a in self.repository.actions(old.job_id)
                      if a["kind"] == "route_source" and a["phase"] == "committed"}
            packages = []
            for package in plan.packages:
                if package.package_id not in selected:
                    continue
                members = [source_snapshot(Path(routed.get(m.path, m.path))) for m in package.members]
                entry = next((m.path for m, original in zip(members, package.members) if original.path == package.entry), members[0].path)
                packages.append(package.model_copy(update={"package_id": str(uuid4()), "entry": entry, "members": members}))
            retry_plan = plan.model_copy(update={"plan_id": str(uuid4()), "created_at": utc_now(),
                "settings_revision": self.planner.settings.revision(), "packages": packages})
            self.repository.save_plan(retry_plan)
            return self.submit(retry_plan, request.idempotency_key, retry_of=old.job_id)

    def _log(self, job_id: str, message: str) -> None:
        """Best-effort sanitized log; a failing log device never aborts processing."""
        try:
            text = self.processor.secrets.redact(message)[:8192]
            path = self.processor.paths.logs_root / (job_id + ".log")
            if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
                path.replace(path.with_suffix(".previous.log"))
            with path.open("a", encoding="utf-8") as stream:
                stream.write(text + "\n")
        except OSError:
            # Generic, redacted diagnostic only: never leak paths or values, never raise
            # so a log failure cannot kill a running tool or skip a package terminal state.
            self._stderr("日志写入失败，已跳过该条记录，业务继续。")

    def _register_retained_workspaces(self, recovered: list[dict]) -> None:
        """Register retained interrupted workspaces for manual review; never delete them."""
        for item in recovered:
            workspace = self.processor.paths.work_root / item["job_id"] / item["package_id"]
            try:
                if not workspace.is_dir() or self._workspace_registered(item["job_id"], workspace):
                    continue
                action = self.repository.record_action({
                    "job_id": item["job_id"], "package_id": item["package_id"],
                    "kind": "recovery_note", "source": "", "destination": str(workspace),
                    "snapshot": None,
                })
                self.repository.action_phase(action, "committed")
            except Exception:
                # Registration is best effort; a failure must not block engine start or
                # delete anything. Only a generic, redacted diagnostic is printed.
                self._stderr("中断工作区登记失败，未删除任何文件，请人工检查。")

    def _workspace_registered(self, job_id: str, workspace: Path) -> bool:
        return any(action["kind"] == "recovery_note" and action["destination"] == str(workspace)
                   for action in self.repository.actions(job_id))

    @staticmethod
    def _stderr(message: str) -> None:
        try:
            sys.stderr.write(message + "\n")
            sys.stderr.flush()
        except OSError:
            pass

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            job_id, plan = item
            token = self._cancel[job_id]
            try:
                self.repository.update_job(job_id, "running")
                for package in plan.packages:
                    if token.is_set():
                        self.repository.update_package(job_id, package.package_id, state="cancelled", message="尚未处理，原件保留。")
                        continue
                    try:
                        outcome = self.processor.process(package, plan.options, job_id=job_id,
                            output_root=Path(plan.output_root), cancel=token,
                            progress=lambda state, p=package: self.repository.update_package(job_id, p.package_id, state=state),
                            log=lambda msg: self._log(job_id, msg))
                        self.repository.update_package(job_id, package.package_id, state=outcome.state,
                            message=outcome.message, results=outcome.results[:32], error_code=outcome.error_code)
                    except Exception as exc:
                        actions = [a for a in self.repository.actions(job_id) if a["package_id"] == package.package_id]
                        committed = [a["destination"] for a in actions if a["phase"] == "committed"]
                        review = any(a["phase"] not in {"committed", "abandoned"} for a in actions) or bool(committed)
                        state = "needs_review" if review else ("cancelled" if isinstance(exc, ProcessingCancelled) else "failed")
                        code = exc.code if isinstance(exc, EngineError) else "PROCESSING_FAILED"
                        message = str(exc) if isinstance(exc, EngineError) else "处理异常，原件保留；请查看诊断记录。"
                        message = self.processor.secrets.redact(message)
                        self._log(job_id, f"{code}: {message}")
                        self.repository.update_package(job_id, package.package_id, state=state,
                            error_code=code, message=message, results=committed[:32])
                states = {p.state for p in self.repository.get_job(job_id).packages}
                if "needs_review" in states:
                    final = "needs_review"
                elif states == {"succeeded"}:
                    final = "succeeded"
                elif states == {"cancelled"}:
                    final = "cancelled"
                elif states <= {"failed", "interrupted"}:
                    final = "failed"
                else:
                    final = "partial"
                self.repository.update_job(job_id, final)
            except Exception:
                self.repository.update_job(job_id, "interrupted")
            finally:
                with self._lock:
                    self._cancel.pop(job_id, None)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for token in self._cancel.values():
                token.set()
            self._queue.put(None)
        self._thread.join(timeout=15)
        if self._thread.is_alive():
            # Do not close a connection still used by an in-progress commit.
            raise EngineError("SHUTDOWN_PENDING", "文件事务尚在完成安全收尾。")
