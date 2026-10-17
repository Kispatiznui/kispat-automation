"""Estados del ciclo de vida. Compartidos por control y agente."""
from enum import StrEnum


class AutomationState(StrEnum):
    draft = "draft"
    pending_approval = "pending_approval"
    approved = "approved"
    deployed = "deployed"
    retired = "retired"


class JobStatus(StrEnum):
    queued = "queued"
    assigned = "assigned"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class DeploymentStatus(StrEnum):
    pending = "pending"
    active = "active"
    stopped = "stopped"


class ExecutorKind(StrEnum):
    podman = "podman"
