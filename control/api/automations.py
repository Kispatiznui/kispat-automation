import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from control.audit.jsonl import audit
from control.config import get_settings
from control.db.models import Automation
from control.db.session import get_session
from control.errors import ApiError
from control.registry.store import (
    ManifestError,
    PackageError,
    parse_manifest,
    remove_package,
    save_package,
    validate_package,
)
from control.security import require_client_token
from shared.contracts.lifecycle import AutomationState

router = APIRouter(
    prefix="/api/v1", tags=["automations"], dependencies=[Depends(require_client_token)]
)

MAX_MANIFEST_BYTES = 256 * 1024


class AutomationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    version: str
    state: str
    author: str
    manifest: dict[str, Any]
    created_at: datetime
    approved_at: datetime | None


@router.post("/automations", status_code=201, response_model=AutomationOut)
def create_automation(
    manifest_file: UploadFile = File(..., alias="manifest.yaml"),
    package_file: UploadFile = File(..., alias="package.tar.gz"),
    session: Session = Depends(get_session),
) -> Automation:
    manifest_raw = manifest_file.file.read(MAX_MANIFEST_BYTES + 1)
    if len(manifest_raw) > MAX_MANIFEST_BYTES:
        raise ApiError(413, "too_large", "manifest.yaml excede 256 KB")
    max_pkg = get_settings().MAX_PACKAGE_MB * 1024 * 1024
    package_raw = package_file.file.read(max_pkg + 1)
    if len(package_raw) > max_pkg:
        raise ApiError(413, "too_large", f"package.tar.gz excede {get_settings().MAX_PACKAGE_MB} MB")

    try:
        manifest = parse_manifest(manifest_raw)
    except ManifestError as exc:
        raise ApiError(422, "invalid_manifest", "manifest.yaml inválido", exc.details) from exc
    try:
        validate_package(package_raw, manifest)
    except PackageError as exc:
        raise ApiError(422, "invalid_package", str(exc)) from exc

    meta = manifest.metadata
    exists = session.execute(
        select(Automation.id).where(Automation.name == meta.name, Automation.version == meta.version)
    ).first()
    if exists:
        raise ApiError(409, "conflict", f"{meta.name}@{meta.version} ya existe")

    automation_id = uuid.uuid4()
    path = save_package(automation_id, manifest_raw, package_raw, meta.version)
    automation = Automation(
        id=automation_id,
        name=meta.name,
        version=meta.version,
        state=AutomationState.pending_approval.value,
        manifest=manifest.model_dump(mode="json", by_alias=True),
        package_path=str(path),
        author=meta.author,
    )
    session.add(automation)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        remove_package(automation_id)
        raise ApiError(409, "conflict", f"{meta.name}@{meta.version} ya existe") from exc
    session.refresh(automation)
    audit("automation_created", automation_id=automation_id, name=meta.name, version=meta.version)
    return automation


@router.post("/automations/{automation_id}/approve", response_model=AutomationOut)
def approve_automation(automation_id: uuid.UUID, session: Session = Depends(get_session)):
    automation = session.get(Automation, automation_id)
    if automation is None:
        raise ApiError(404, "not_found", "Automatización no encontrada")
    if automation.state != AutomationState.pending_approval.value:
        raise ApiError(409, "invalid_state", f"estado actual: {automation.state}")
    automation.state = AutomationState.approved.value
    automation.approved_at = datetime.now(UTC)
    session.commit()
    audit("automation_approved", automation_id=automation_id)
    return automation


@router.get("/automations", response_model=list[AutomationOut])
def list_automations(session: Session = Depends(get_session)):
    return session.execute(select(Automation).order_by(Automation.created_at.desc())).scalars().all()
