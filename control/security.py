import hashlib
import hmac

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from control.config import get_settings
from control.db.models import Node
from control.db.session import get_session
from control.errors import ApiError


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _is_client_token(token: str) -> bool:
    expected = get_settings().CLIENT_TOKEN
    return bool(expected) and hmac.compare_digest(token, expected)


def require_client_token(authorization: str | None = Header(default=None)) -> None:
    token = bearer(authorization)
    if token is None or not _is_client_token(token):
        raise ApiError(401, "unauthorized", "Token de cliente ausente o inválido")


def require_node_token(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> Node:
    token = bearer(authorization)
    if token is None:
        raise ApiError(401, "unauthorized", "Falta Authorization: Bearer <token>")
    if _is_client_token(token):
        raise ApiError(403, "forbidden", "Este endpoint es solo para nodos")
    node = session.execute(
        select(Node).where(Node.token_hash == hash_token(token))
    ).scalars().first()
    if node is None:
        raise ApiError(401, "unauthorized", "Token de nodo inválido")
    return node
