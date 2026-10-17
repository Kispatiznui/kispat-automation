import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.contracts.lifecycle import JobStatus

MAX_OUTPUT_CHARS = 64 * 1024


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    automation_id: uuid.UUID
    deployment_id: uuid.UUID
    node_id: uuid.UUID | None = None
    status: JobStatus
    priority: int
    scheduled_for: datetime
    attempts: int
    max_attempts: int
    timeout_s: int
    limits: dict[str, Any]
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobResult(BaseModel):
    """Lo que el agente reporta al control al terminar un job."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["succeeded", "failed"]
    exit_code: int | None = None
    stdout: str = Field(default="", max_length=MAX_OUTPUT_CHARS)
    stderr: str = Field(default="", max_length=MAX_OUTPUT_CHARS)
    stdout_path: str | None = None
    stderr_path: str | None = None
    duration_ms: int = 0
    truncated: bool = False
    error: str | None = None
