import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from control.audit.jsonl import audit
from control.db.models import Automation, Deployment, Job, Node
from control.db.session import get_session
from control.errors import ApiError
from control.security import require_client_token
from shared.contracts.lifecycle import AutomationState, DeploymentStatus, JobStatus

router = APIRouter(
    prefix="/api/v1", tags=["deployments"], dependencies=[Depends(require_client_token)]
)


class DeploymentCreate(BaseModel):
    automation_id: uuid.UUID
    target_node_ids: list[str] = Field(min_length=1)  # Node.node_id (nombre lógico)


def _payload(automation: Automation) -> dict:
    spec = automation.manifest["spec"]
    return {
        "automation_id": str(automation.id),
        "name": automation.name,
        "version": automation.version,
        "runtime": spec["runtime"],
        "entrypoint": spec["entrypoint"],
        "args": spec["args"],
        "env": spec["env"],
        "permissions": spec["permissions"],  # secretos: solo nombres lógicos
        "trigger": "manual",
    }


@router.post("/deployments", status_code=201)
def create_deployment(body: DeploymentCreate, session: Session = Depends(get_session)) -> dict:
    automation = session.get(Automation, body.automation_id)
    if automation is None:
        raise ApiError(404, "not_found", "Automatización no encontrada")
    if automation.state not in (AutomationState.approved.value, AutomationState.deployed.value):
        raise ApiError(409, "not_approved", f"La automatización está en estado {automation.state}")

    wanted = list(dict.fromkeys(body.target_node_ids))
    nodes = session.execute(select(Node).where(Node.node_id.in_(wanted))).scalars().all()
    missing = sorted(set(wanted) - {n.node_id for n in nodes})
    if missing:
        raise ApiError(404, "node_not_found", f"Nodos desconocidos: {', '.join(missing)}")

    limits = automation.manifest["spec"]["limits"]
    deployment = Deployment(
        automation_id=automation.id,
        target_node_ids=wanted,
        status=DeploymentStatus.active.value,
        approved_by="client",
        approved_at=datetime.now(UTC),
    )
    session.add(deployment)
    session.flush()
    jobs = [
        Job(
            automation_id=automation.id,
            deployment_id=deployment.id,
            node_id=node.id,
            status=JobStatus.queued.value,
            timeout_s=limits["timeout_s"],
            limits=limits,
            payload=_payload(automation),
        )
        for node in nodes
    ]
    session.add_all(jobs)
    automation.state = AutomationState.deployed.value
    session.commit()
    audit("deployment_created", deployment_id=deployment.id, nodes=wanted)
    return {
        "id": str(deployment.id),
        "automation_id": str(automation.id),
        "status": deployment.status,
        "target_node_ids": wanted,
        "jobs": [{"id": str(j.id), "node_id": n.node_id} for j, n in zip(jobs, nodes, strict=True)],
    }


@router.get("/deployments")
def list_deployments(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.execute(select(Deployment).order_by(Deployment.created_at.desc())).scalars()
    return [
        {
            "id": str(d.id),
            "automation_id": str(d.automation_id),
            "status": d.status,
            "target_node_ids": d.target_node_ids,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in rows
    ]
