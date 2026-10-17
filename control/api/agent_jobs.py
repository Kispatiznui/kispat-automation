import uuid

from fastapi import APIRouter, Depends, Response
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from control.audit.jsonl import audit
from control.db.models import Automation, Job, Node
from control.db.session import get_session
from control.errors import ApiError
from control.queue import JobNotFound, JobStateError, pull_job, report_result
from control.security import require_client_token, require_node_token
from shared.contracts.job import JobOut, JobResult
from shared.contracts.lifecycle import JobStatus

router = APIRouter(prefix="/api/v1", tags=["agent"])


@router.get("/agent/jobs", response_model=JobOut, responses={204: {"description": "Sin jobs"}})
def pull(
    node_id: str,
    node: Node = Depends(require_node_token),
    session: Session = Depends(get_session),
):
    if node.node_id != node_id:
        raise ApiError(403, "forbidden", "El token no corresponde a este nodo")
    job = pull_job(session, node.id)
    session.commit()
    if job is None:
        return Response(status_code=204)
    audit("job_assigned", job_id=job.id, node_id=node.node_id, attempt=job.attempts)
    return JSONResponse(JobOut.model_validate(job).model_dump(mode="json"))


@router.post("/agent/jobs/{job_id}/result", response_model=JobOut)
def post_result(
    job_id: uuid.UUID,
    body: JobResult,
    node: Node = Depends(require_node_token),
    session: Session = Depends(get_session),
):
    job = session.get(Job, job_id)
    if job is None:
        raise ApiError(404, "not_found", "Job no encontrado")
    if job.node_id != node.id:
        raise ApiError(403, "forbidden", "El job no está asignado a este nodo")
    try:
        job = report_result(session, job_id, body.model_dump())
    except JobNotFound as exc:
        raise ApiError(404, "not_found", "Job no encontrado") from exc
    except JobStateError as exc:
        raise ApiError(409, "invalid_state", str(exc)) from exc
    session.commit()
    audit("job_result", job_id=job_id, node_id=node.node_id, status=job.status)
    return job


@router.get("/agent/automations/{automation_id}/package")
def download_package(
    automation_id: uuid.UUID,
    node: Node = Depends(require_node_token),
    session: Session = Depends(get_session),
):
    """Solo si el nodo tiene un job activo de esa automatización."""
    active = session.execute(
        select(Job.id).where(
            Job.automation_id == automation_id,
            Job.node_id == node.id,
            Job.status.in_([JobStatus.assigned.value, JobStatus.running.value]),
        )
    ).first()
    if not active:
        raise ApiError(403, "forbidden", "El nodo no tiene un job activo para este paquete")
    automation = session.get(Automation, automation_id)
    if automation is None:
        raise ApiError(404, "not_found", "Automatización no encontrada")
    return FileResponse(automation.package_path, media_type="application/gzip")


# --- consulta para el cliente (KISPAT CODE / VulkkRout) ---
@router.get("/jobs/{job_id}", response_model=JobOut, dependencies=[Depends(require_client_token)])
def get_job(job_id: uuid.UUID, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise ApiError(404, "not_found", "Job no encontrado")
    return job


@router.get("/jobs", response_model=list[JobOut], dependencies=[Depends(require_client_token)])
def list_jobs(status: str | None = None, limit: int = 50, session: Session = Depends(get_session)):
    q = select(Job).order_by(Job.created_at.desc()).limit(min(max(limit, 1), 200))
    if status:
        q = q.where(Job.status == status)
    return session.execute(q).scalars().all()
