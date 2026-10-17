import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from control.db.base import Base
from shared.contracts.lifecycle import AutomationState, DeploymentStatus, JobStatus


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (Index("idx_nodes_heartbeat", "last_heartbeat_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    node_id: Mapped[str] = mapped_column(String(128), unique=True)
    hostname: Mapped[str] = mapped_column(String(255))
    os: Mapped[str] = mapped_column(String(64))
    arch: Mapped[str] = mapped_column(String(32))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    token_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="offline")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class Automation(Base):
    __tablename__ = "automations"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_automation_name_version"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default=AutomationState.pending_approval.value)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    package_path: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = _now()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    automation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("automations.id"))
    target_node_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), default=DeploymentStatus.active.value)
    created_at: Mapped[datetime] = _now()
    approved_by: Mapped[str] = mapped_column(String(128), default="client")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    automation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("automations.id"))
    deployment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deployments.id"))
    node_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nodes.id"))
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.queued.value)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    timeout_s: Mapped[int] = mapped_column(Integer)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Índice parcial para el pull: solo indexa jobs en cola.
Index(
    "idx_jobs_pull",
    Job.priority.desc(),
    Job.scheduled_for.asc(),
    postgresql_where=text("status = 'queued'"),
)
