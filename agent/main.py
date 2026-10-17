import json
import logging
import os
import platform
import signal
import socket
import threading
from pathlib import Path

import httpx

from agent.client import ControlClient
from agent.executors import PodmanExecutor

log = logging.getLogger("kispat.agent")

HEARTBEAT_INTERVAL_S = 30
PULL_INTERVAL_S = 5
ERROR_SLEEP_S = 10


class Agent:
    def __init__(self, client: ControlClient, executor, work_dir: Path, pull_interval: float):
        self.client = client
        self.executor = executor
        self.work_dir = work_dir
        self.pull_interval = pull_interval
        self.stop = threading.Event()

    # --- resultados pendientes (el control puede estar caído al terminar un job) ---
    def _pending_file(self, job_id: str) -> Path:
        return self.work_dir / job_id / "result.json"

    def _flush_pending(self) -> None:
        for f in sorted(self.work_dir.glob("*/result.json")):
            if self.stop.is_set():
                return
            job_id = f.parent.name
            try:
                self.client.report_result(job_id, json.loads(f.read_text()))
                f.unlink()
                log.info("resultado pendiente entregado: %s", job_id)
            except (httpx.HTTPError, OSError, ValueError):
                log.warning("control no disponible; resultado pendiente: %s", job_id)
                return

    def _handle(self, job: dict) -> None:
        job_id = job["id"]
        job_dir = self.work_dir / job_id
        log.info("ejecutando job %s", job_id)
        try:
            result = self.executor.run(job, str(job_dir))
        except Exception as exc:  # el agente nunca muere por un job
            log.exception("fallo inesperado ejecutando %s", job_id)
            result = {"status": "failed", "error": f"agent error: {exc}"}
        self._pending_file(job_id).parent.mkdir(parents=True, exist_ok=True)
        self._pending_file(job_id).write_text(json.dumps(result))  # durable antes de reportar
        self._flush_pending()

    def _heartbeat_loop(self) -> None:
        while not self.stop.wait(HEARTBEAT_INTERVAL_S):
            try:
                self.client.heartbeat()
            except httpx.HTTPError as exc:
                log.warning("heartbeat falló: %s", exc)

    def loop(self) -> None:
        threading.Thread(target=self._heartbeat_loop, daemon=True, name="heartbeat").start()
        while not self.stop.is_set():
            self._flush_pending()
            try:
                job = self.client.pull_job()
            except httpx.HTTPError as exc:
                log.warning("pull_job falló: %s", exc)
                self.stop.wait(ERROR_SLEEP_S)
                continue
            if job is None:
                self.stop.wait(self.pull_interval)
            else:
                self._handle(job)
        log.info("agente detenido")


def run() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    node_id = os.environ.get("NODE_ID") or socket.gethostname()
    work_dir = Path(os.environ.get("WORK_DIR", "/var/lib/kispat/work"))
    work_dir.mkdir(parents=True, exist_ok=True)
    client = ControlClient(
        base_url=os.environ.get("CTRL_URL", "http://127.0.0.1:8000"),
        token=os.environ["NODE_TOKEN"],
        node_id=node_id,
        registration_token=os.environ.get("REGISTRATION_TOKEN"),
    )
    agent = Agent(
        client,
        PodmanExecutor(fetch_package=client.download_package),
        work_dir,
        float(os.environ.get("PULL_INTERVAL_S", PULL_INTERVAL_S)),
    )
    signal.signal(signal.SIGTERM, lambda *_: agent.stop.set())
    signal.signal(signal.SIGINT, lambda *_: agent.stop.set())

    while not agent.stop.is_set():  # registro: reintenta hasta que el control responda
        try:
            client.register(
                hostname=socket.gethostname(),
                os=platform.system().lower(),
                arch=platform.machine(),
                capabilities={"executors": ["podman"], "runtimes": ["python3.12", "node22", "bash"]},
            )
            log.info("nodo %s registrado", node_id)
            break
        except httpx.HTTPError as exc:
            log.warning("registro falló: %s", exc)
            agent.stop.wait(ERROR_SLEEP_S)
    agent.loop()
    client.close()
