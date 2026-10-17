import hmac
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from control.audit.jsonl import audit
from control.config import get_settings
from control.db.models import Node
from control.db.session import get_session
from control.errors import ApiError
from control.security import bearer, hash_token, require_client_token, require_node_token
from shared.contracts.node import NodeHeartbeat, NodeRegisterRequest, NodeRegisterResponse

router = APIRouter(prefix="/api/v1", tags=["nodes"])


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    node_id: str
    hostname: str
    os: str
    arch: str
    capabilities: dict
    status: str
    last_heartbeat_at: datetime | None
    created_at: datetime


@router.post("/nodes/register", response_model=NodeRegisterResponse)
def register(
    body: NodeRegisterRequest,
    authorization: str | None = Header(default=None),
    x_registration_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> NodeRegisterResponse:
    """Registro con token propuesto por el nodo (trust-on-first-use).
    Si REGISTRATION_TOKEN está definido, además se exige X-Registration-Token."""
    reg = get_settings().REGISTRATION_TOKEN
    if reg and not (x_registration_token and hmac.compare_digest(x_registration_token, reg)):
        raise ApiError(401, "unauthorized", "X-Registration-Token ausente o inválido")
    token = bearer(authorization)
    if token is None or len(token) < 32:
        raise ApiError(401, "unauthorized", "Bearer con token de nodo (mín. 32 caracteres)")

    now = datetime.now(UTC)
    node = session.execute(select(Node).where(Node.node_id == body.node_id)).scalars().first()
    if node is None:
        node = Node(node_id=body.node_id, token_hash=hash_token(token))
        session.add(node)
        audit("node_registered", node_id=body.node_id)
    elif node.token_hash != hash_token(token):
        raise ApiError(401, "unauthorized", "El nodo ya existe con otro token")
    node.hostname, node.os, node.arch = body.hostname, body.os, body.arch
    node.capabilities = body.capabilities
    node.status, node.last_heartbeat_at = "online", now
    session.commit()
    return NodeRegisterResponse(node_id=node.node_id, node_token=token)


@router.post("/nodes/{node_id}/heartbeat")
def heartbeat(
    node_id: str,
    body: NodeHeartbeat | None = None,
    node: Node = Depends(require_node_token),
    session: Session = Depends(get_session),
) -> dict:
    if node.node_id != node_id:
        raise ApiError(403, "forbidden", "El token no corresponde a este nodo")
    node.last_heartbeat_at = datetime.now(UTC)
    node.status = "online"
    session.commit()
    return {"ok": True}


@router.get("/nodes", response_model=list[NodeOut], dependencies=[Depends(require_client_token)])
def list_nodes(session: Session = Depends(get_session)):
    return session.execute(select(Node).order_by(Node.created_at)).scalars().all()
