import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from control.db.models import Job
from shared.contracts.lifecycle import JobStatus

BACKOFF_S = [60, 300, 900, 3600]
TERMINAL = {JobStatus.succeeded.value, JobStatus.failed.value, JobStatus.cancelled.value}


class JobNotFound(Exception):
    pass


class JobStateError(Exception):
    pass


def backoff_seconds(attempts: int) -> int:
    return BACKOFF_S[min(max(attempts, 1) - 1, len(BACKOFF_S) - 1)]


def report_result(session: Session, job_id: uuid.UUID, result: dict[str, Any]) -> Job:
    """Registra el resultado de un job. Idempotente si el job ya está en estado final.
    NO hace commit: lo hace el llamador."""
    job = (
        session.execute(
            select(Job)
            .where(Job.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        .scalars()
        .first()
    )
    if job is None:
        raise JobNotFound(str(job_id))
    if job.status in TERMINAL:
        return job
    if job.status not in (JobStatus.assigned.value, JobStatus.running.value):
        raise JobStateError(f"job en estado {job.status}, no se esperaba resultado")

    job.result = result
    if result.get("status") == "succeeded":
        job.status = JobStatus.succeeded.value
        job.error = None
        job.finished_at = func.now()
    else:
        job.error = result.get("error") or f"exit_code={result.get('exit_code')}"
        if job.attempts < job.max_attempts:
            job.status = JobStatus.queued.value
            job.scheduled_for = func.now() + timedelta(seconds=backoff_seconds(job.attempts))
        else:
            job.status = JobStatus.failed.value
            job.finished_at = func.now()
    session.flush()
    session.refresh(job)
    return job
