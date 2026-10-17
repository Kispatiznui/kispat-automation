"""Ejecutor Podman rootless. Límites de CPU/memoria/pids/timeout SIEMPRE aplicados."""
import logging
import shutil
import subprocess
import tarfile
import time
from collections.abc import Callable
from pathlib import Path

from agent.executors.base import ExecResult

log = logging.getLogger("kispat.agent.podman")

MAX_RESULT_CHARS = 64 * 1024

# runtime -> (imagen, comando base). El entrypoint se añade al final.
RUNTIMES: dict[str, tuple[str, list[str]]] = {
    "python3.12": ("docker.io/library/python:3.12-slim", ["python"]),
    "node22": ("docker.io/library/node:22-slim", ["node"]),
    "bash": ("docker.io/library/debian:stable-slim", ["bash"]),
    "binary": ("docker.io/library/debian:stable-slim", []),
}


def safe_extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(dest, filter="data")  # rechaza rutas absolutas, '..', enlaces fuera, etc.


def _tail(path: Path) -> tuple[str, bool]:
    if not path.exists():
        return "", False
    size = path.stat().st_size
    with path.open("rb") as fh:
        data = fh.read(MAX_RESULT_CHARS)
    return data.decode("utf-8", errors="replace"), size > MAX_RESULT_CHARS


class PodmanExecutor:
    def __init__(
        self,
        fetch_package: Callable[[str, Path], None] | None = None,
        podman_bin: str = "podman",
        keep_workdir: bool = False,
    ):
        # fetch_package(automation_id, dest_tar_gz): descarga vía HTTP desde el control.
        self.fetch_package = fetch_package
        self.podman_bin = podman_bin
        self.keep_workdir = keep_workdir

    def build_args(self, job: dict, app_dir: Path) -> list[str]:
        payload, limits = job["payload"], job["limits"]
        image, base_cmd = RUNTIMES[payload["runtime"]]
        timeout_s = int(limits.get("timeout_s") or job["timeout_s"])
        args = [
            self.podman_bin, "run", "--rm",
            "--name", f"kispat-{job['id']}",
            f"--memory={int(limits['memory_mb'])}m",
            f"--cpus={float(limits['cpu']):g}",
            f"--pids-limit={int(limits['pids'])}",
            f"--timeout={timeout_s}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--read-only",
            "--tmpfs=/tmp:rw,size=64m",
        ]  # fmt: skip
        if not payload.get("permissions", {}).get("network", False):
            args.append("--network=none")
        for key, value in payload.get("env", {}).items():
            args += ["-e", f"{key}={value}"]
        args += ["-v", f"{app_dir}:/work:Z", "-w", "/work", image]
        entry = payload["entrypoint"]
        args += base_cmd + [entry if base_cmd else f"./{entry}", *payload.get("args", [])]
        return args

    def run(self, job: dict, workdir: str) -> ExecResult:
        job_dir = Path(workdir)
        app_dir = job_dir / "app"
        stdout_path, stderr_path = job_dir / "stdout.log", job_dir / "stderr.log"
        job_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()

        def finish(status: str, exit_code: int | None, error: str | None = None) -> ExecResult:
            out, out_trunc = _tail(stdout_path)
            err, err_trunc = _tail(stderr_path)
            if not self.keep_workdir:
                shutil.rmtree(app_dir, ignore_errors=True)
            return {
                "status": status,
                "exit_code": exit_code,
                "stdout": out,
                "stderr": err,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "duration_ms": int((time.monotonic() - started) * 1000),
                "truncated": out_trunc or err_trunc,
                "error": error,
            }

        stdout_path.touch()
        stderr_path.touch()
        runtime = job["payload"].get("runtime")
        if runtime not in RUNTIMES:
            return finish("failed", None, f"runtime no soportado: {runtime}")
        try:
            if self.fetch_package is not None and not app_dir.exists():
                archive = job_dir / "package.tar.gz"
                self.fetch_package(job["payload"]["automation_id"], archive)
                safe_extract(archive, app_dir)
            app_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # descarga/extracción: el job falla, el agente sigue vivo
            log.exception("no se pudo preparar el paquete")
            return finish("failed", None, f"preparación del paquete: {exc}")

        args = self.build_args(job, app_dir)
        timeout_s = int(job["limits"].get("timeout_s") or job["timeout_s"])
        try:
            with stdout_path.open("wb") as fo, stderr_path.open("wb") as fe:
                proc = subprocess.run(  # noqa: S603
                    args, stdout=fo, stderr=fe, timeout=timeout_s + 10, check=False
                )
        except subprocess.TimeoutExpired:
            subprocess.run([self.podman_bin, "kill", f"kispat-{job['id']}"], check=False)  # noqa: S603
            return finish("failed", None, f"timeout tras {timeout_s}s")
        except FileNotFoundError:
            return finish("failed", None, "podman no está instalado")

        ok = proc.returncode == 0
        error = None if ok else f"exit_code={proc.returncode}"
        return finish("succeeded" if ok else "failed", proc.returncode, error)
