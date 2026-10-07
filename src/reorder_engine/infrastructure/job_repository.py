from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from reorder_engine.application.errors import EngineError
from reorder_engine.application.models import JobSnapshot, PackageSnapshot, ProcessingPlan, TERMINAL_JOB_STATES


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


ACTIVE_PACKAGE_STATES = frozenset({"queued", "preparing", "extracting", "publishing", "archiving"})
RECOVER_PACKAGE_MESSAGE = "上次执行中断，原件不会自动删除；请检查结果后重试。"


class JobRepository:
    """Single synchronized writer for durable task snapshots and file intents."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise EngineError("DATABASE_VERSION", "任务数据库版本不兼容，未修改数据。")
        with self._connection:
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, plan_id TEXT NOT NULL,
                    request_key TEXT UNIQUE NOT NULL, body TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES plans(id));
                CREATE TABLE IF NOT EXISTS events(
                    job_id TEXT NOT NULL, seq INTEGER NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(job_id, seq), FOREIGN KEY(job_id) REFERENCES jobs(id));
                CREATE TABLE IF NOT EXISTS actions(
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, package_id TEXT NOT NULL,
                    phase TEXT NOT NULL, body TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id));
                PRAGMA user_version=1;
            """)

    def save_plan(self, plan: ProcessingPlan) -> None:
        with self._lock, self._connection:
            self._connection.execute("INSERT INTO plans VALUES(?,?)", (plan.plan_id, plan.model_dump_json()))

    def get_plan(self, plan_id: str) -> ProcessingPlan:
        with self._lock:
            row = self._connection.execute("SELECT body FROM plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            raise EngineError("PLAN_NOT_FOUND", "处理计划不存在，请重新添加文件。")
        return ProcessingPlan.model_validate_json(row["body"])

    def get_by_key(self, key: str) -> JobSnapshot | None:
        with self._lock:
            row = self._connection.execute("SELECT body FROM jobs WHERE request_key=?", (key,)).fetchone()
        return JobSnapshot.model_validate_json(row["body"]) if row else None

    def create_job(self, plan: ProcessingPlan, key: str, *, retry_of: str | None = None) -> JobSnapshot:
        with self._lock:
            existing = self.get_by_key(key)
            if existing:
                if existing.plan_id != plan.plan_id:
                    raise EngineError("IDEMPOTENCY_CONFLICT", "请求标识已被另一处理计划使用。")
                return existing
            now = utc_now()
            job = JobSnapshot(
                job_id=str(uuid4()), plan_id=plan.plan_id, state="queued",
                created_at=now, updated_at=now, retry_of=retry_of,
                packages=[PackageSnapshot(package_id=p.package_id, name=p.name) for p in plan.packages],
            )
            with self._connection:
                self._connection.execute("INSERT INTO jobs VALUES(?,?,?,?)", (job.job_id, plan.plan_id, key, job.model_dump_json()))
            return job

    def get_job(self, job_id: str) -> JobSnapshot:
        with self._lock:
            row = self._connection.execute("SELECT body FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise EngineError("JOB_NOT_FOUND", "任务不存在。")
        return JobSnapshot.model_validate_json(row["body"])

    def list_jobs(self, limit: int = 20) -> list[JobSnapshot]:
        with self._lock:
            rows = self._connection.execute("SELECT body FROM jobs ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [JobSnapshot.model_validate_json(row["body"]) for row in rows]

    def _save(self, job: JobSnapshot, event: dict) -> JobSnapshot:
        job.updated_at = utc_now()
        job.last_seq += 1
        event.update(job_id=job.job_id, seq=job.last_seq, timestamp=job.updated_at)
        with self._connection:
            self._connection.execute("UPDATE jobs SET body=? WHERE id=?", (job.model_dump_json(), job.job_id))
            self._connection.execute("INSERT INTO events VALUES(?,?,?)", (job.job_id, job.last_seq, json.dumps(event, ensure_ascii=False)))
            # Snapshots are authoritative; bounded event history is enough for UI catch-up.
            self._connection.execute("DELETE FROM events WHERE job_id=? AND seq<?", (job.job_id, max(0, job.last_seq - 1000)))
        return job

    def update_job(self, job_id: str, state: str) -> JobSnapshot:
        with self._lock:
            job = self.get_job(job_id)
            job.state = state
            return self._save(job, {"type": "job.state", "state": state})

    def update_package(self, job_id: str, package_id: str, *, state: str, message: str = "", error_code: str | None = None, results: list[str] | None = None) -> JobSnapshot:
        with self._lock:
            job = self.get_job(job_id)
            package = next((x for x in job.packages if x.package_id == package_id), None)
            if package is None:
                raise EngineError("PACKAGE_NOT_FOUND", "任务中没有该文件组。")
            package.state = state
            package.message = message[:2000]
            package.error_code = error_code
            if results is not None:
                package.results = results
            return self._save(job, {"type": "package.state", "package_id": package_id, "state": state, "message": package.message})

    def events(self, job_id: str, after_seq: int, limit: int) -> dict:
        with self._lock:
            job = self.get_job(job_id)
            rows = self._connection.execute("SELECT body FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?", (job_id, after_seq, limit)).fetchall()
        return {"events": [json.loads(row["body"]) for row in rows], "snapshot": job.model_dump(mode="json")}

    def record_action(self, action: dict) -> str:
        action_id = str(uuid4())
        body = dict(action, action_id=action_id)
        with self._lock, self._connection:
            self._connection.execute("INSERT INTO actions VALUES(?,?,?,?,?)", (action_id, action["job_id"], action["package_id"], "prepared", json.dumps(body, ensure_ascii=False)))
        return action_id

    def action_phase(self, action_id: str, phase: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("UPDATE actions SET phase=? WHERE id=?", (phase, action_id))

    def incomplete_actions(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute("SELECT phase,body FROM actions WHERE phase NOT IN ('committed','abandoned')").fetchall()
        return [dict(json.loads(row["body"]), phase=row["phase"]) for row in rows]

    def actions(self, job_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute("SELECT phase,body FROM actions WHERE job_id=?", (job_id,)).fetchall()
        return [dict(json.loads(row["body"]), phase=row["phase"]) for row in rows]

    def recover_interrupted(self) -> list[dict]:
        """Reconcile snapshots after an unclean stop without touching any file.

        Terminal jobs are inspected too: an unclean stop can leave an active package or
        a pending file journal inside a job that was already marked terminal, and
        skipping it would strand that package forever. Finished packages are never
        rewritten or redone, and a retained workspace is only reported for manual
        review (returned as ``job_id``/``package_id`` pairs); nothing is deleted or
        reconciled automatically.
        """
        recovered: list[dict] = []
        with self._lock:
            incomplete = self.incomplete_actions()
            review_jobs = {a["job_id"] for a in incomplete}
            review_packages = {(a["job_id"], a["package_id"]) for a in incomplete}
            rows = self._connection.execute("SELECT body FROM jobs").fetchall()
            for row in rows:
                job = JobSnapshot.model_validate_json(row["body"])
                changed: list[PackageSnapshot] = []
                for package in job.packages:
                    pending = (job.job_id, package.package_id) in review_packages
                    if not pending and package.state not in ACTIVE_PACKAGE_STATES:
                        continue  # finished package: never redone or rewritten
                    state = "needs_review" if pending else "interrupted"
                    if package.state == state and package.message == RECOVER_PACKAGE_MESSAGE:
                        continue  # already recovered on an earlier start
                    package.state = state
                    package.message = RECOVER_PACKAGE_MESSAGE
                    changed.append(package)
                if changed:
                    job.state = ("needs_review"
                        if job.job_id in review_jobs or any(p.state == "needs_review" for p in job.packages)
                        else "interrupted")
                    self._save(job, {"type": "job.recovered", "state": job.state})
                    recovered.extend({"job_id": job.job_id, "package_id": package.package_id,
                                      "state": package.state} for package in changed)
                    continue
                if job.state in TERMINAL_JOB_STATES:
                    continue
                # The job was left mid-run but every package already finished; report
                # the honest terminal state instead of leaving it "running" forever.
                derived = self._derive_job_state(job)
                if derived != job.state:
                    job.state = derived
                    self._save(job, {"type": "job.recovered", "state": derived})
        return recovered

    @staticmethod
    def _derive_job_state(job: JobSnapshot) -> str:
        states = {package.state for package in job.packages}
        if "needs_review" in states:
            return "needs_review"
        if states == {"succeeded"}:
            return "succeeded"
        if states and states <= {"cancelled"}:
            return "cancelled"
        if states and states <= {"failed", "interrupted"}:
            return "failed"
        return "partial"

    def close(self) -> None:
        with self._lock:
            self._connection.close()
