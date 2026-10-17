from typing import Protocol, TypedDict


class ExecResult(TypedDict, total=False):
    status: str  # "succeeded" | "failed"
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_path: str
    stderr_path: str
    duration_ms: int
    truncated: bool
    error: str | None


class Executor(Protocol):
    """Adaptador de ejecución. Hoy Podman; mañana otro, sin tocar el agente."""

    def run(self, job: dict, workdir: str) -> ExecResult: ...
