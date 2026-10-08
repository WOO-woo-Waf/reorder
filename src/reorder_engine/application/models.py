from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

PackageState = Literal[
    "queued", "preparing", "extracting", "publishing", "archiving",
    "succeeded", "partial", "failed", "deferred", "cancelled",
    "interrupted", "needs_review",
]
JobState = Literal["queued", "running", "cancelling", "succeeded", "partial", "failed", "cancelled", "interrupted", "needs_review"]
TERMINAL_PACKAGE_STATES = frozenset({"succeeded", "partial", "failed", "deferred", "cancelled", "interrupted", "needs_review"})
TERMINAL_JOB_STATES = frozenset({"succeeded", "partial", "failed", "cancelled", "interrupted", "needs_review"})


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProcessingOptions(Contract):
    deep_extract: bool = True
    max_depth: int = Field(default=4, ge=1, le=20)
    min_archive_mb: int = Field(default=100, ge=1, le=1048576)
    final_single_mb: int = Field(default=200, ge=1, le=1048576)
    preserve_payload_names: bool = True
    use_builtin_passwords: bool = True  # Legacy field; the editable file is authoritative.
    clean_builtin_keywords: bool = False
    recursive: bool = False
    tool_timeout_sec: int = Field(default=3600, ge=1, le=86400)
    max_output_gb: int = Field(default=64, ge=1, le=1048576)
    keep_workspace: bool = False


class ToolPaths(Contract):
    seven_zip: str | None = None
    unrar: str | None = None
    bandizip: str | None = None


class DesktopSettings(Contract):
    version: int = 1
    work_root: str | None = None
    options: ProcessingOptions = Field(default_factory=ProcessingOptions)
    tools: ToolPaths = Field(default_factory=ToolPaths)


class SourceSnapshot(Contract):
    path: str
    size: int
    mtime_ns: int
    device: int
    inode: int


class PlannedPackage(Contract):
    package_id: str
    name: str
    group_key: str
    entry: str
    members: list[SourceSnapshot]


class PlanRequest(Contract):
    input_paths: list[str] = Field(min_length=1, max_length=10000)
    output_root: str
    options: ProcessingOptions = Field(default_factory=ProcessingOptions)


class ProcessingPlan(Contract):
    plan_id: str
    created_at: str
    output_root: str
    options: ProcessingOptions
    settings_revision: str
    packages: list[PlannedPackage]
    warnings: list[str] = Field(default_factory=list)


class PackageSnapshot(Contract):
    package_id: str
    name: str
    state: PackageState = "queued"
    message: str = ""
    error_code: str | None = None
    results: list[str] = Field(default_factory=list)


class JobSnapshot(Contract):
    job_id: str
    plan_id: str
    state: JobState
    created_at: str
    updated_at: str
    packages: list[PackageSnapshot]
    last_seq: int = 0
    retry_of: str | None = None


class StartRequest(Contract):
    plan_id: str
    idempotency_key: str = Field(min_length=1, max_length=128)


class JobIdRequest(Contract):
    job_id: str = Field(min_length=1, max_length=128)


class RetryRequest(JobIdRequest):
    package_ids: list[str] = Field(min_length=1, max_length=10000)
    idempotency_key: str = Field(min_length=1, max_length=128)


class EventsRequest(JobIdRequest):
    after_seq: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


class LogsRequest(JobIdRequest):
    cursor: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


class ListRequest(Contract):
    limit: int = Field(default=20, ge=1, le=100)


class PasswordRequest(Contract):
    passwords: list[str] = Field(max_length=10000)


class PasswordImportRequest(Contract):
    path: str


class ResultOpenRequest(Contract):
    job_id: str
    path: str
