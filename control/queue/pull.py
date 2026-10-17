import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from control.db.models import Job
from shared.contracts.lifecycle import JobStatus


def pull_job(session: Session, node_id: uuid.UUID | str) -> Job | None:
    """Asigna atómicamente el siguiente job elegible al nodo.

    `node_id` es el UUID interno del nodo (Node.id), no el nombre lógico.
    Una sola sentencia UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED).
    NO hace commit: lo hace el llamador.
    """
    nid = node_id if isinstance(node_id, uuid.UUID) else uuid.UUID(str(node_id))
    candidate = (
        select(Job.id)
        .where(
            Job.status == JobStatus.queued.value,
            Job.scheduled_for <= func.now(),
            or_(Job.node_id.is_(None), Job.node_id == nid),
        )
        .order_by(Job.priority.desc(), Job.scheduled_for.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    stmt = (
        update(Job)
        .where(Job.id == candidate)
        .values(
            status=JobStatus.assigned.value,
            node_id=nid,
            attempts=Job.attempts + 1,
            started_at=func.now(),
        )
        .returning(Job)
        .execution_options(synchronize_session=False, populate_existing=True)
    )
    return session.execute(stmt).scalars().first()
