import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger("kispat.agent.client")


class ControlClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        node_id: str,
        timeout: float = 30,
        registration_token: str | None = None,
        retries: int = 3,
        backoff_base: float = 1.0,
    ):
        self.node_id = node_id
        self.registration_token = registration_token
        self.retries = retries
        self.backoff_base = backoff_base
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
        )

    def _request(self, method: str, url: str, **kw) -> httpx.Response:
        """Reintenta errores de red y 5xx con backoff exponencial (1s, 2s, 4s...)."""
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._http.request(method, url, **kw)
                if resp.status_code < 500:
                    return resp
                last = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
            except httpx.TransportError as exc:
                last = exc
            if attempt < self.retries:
                delay = self.backoff_base * (2**attempt)
                log.warning("reintento %s %s en %.1fs: %s", method, url, delay, last)
                time.sleep(delay)
        assert last is not None
        raise last

    def register(self, hostname: str, os: str, arch: str, capabilities: dict) -> dict:
        headers = {}
        if self.registration_token:
            headers["X-Registration-Token"] = self.registration_token
        resp = self._request(
            "POST",
            "/api/v1/nodes/register",
            json={
                "node_id": self.node_id,
                "hostname": hostname,
                "os": os,
                "arch": arch,
                "capabilities": capabilities,
            },
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    def heartbeat(self) -> bool:
        resp = self._request("POST", f"/api/v1/nodes/{self.node_id}/heartbeat", json={})
        resp.raise_for_status()
        return True

    def pull_job(self) -> dict | None:
        resp = self._request("GET", "/api/v1/agent/jobs", params={"node_id": self.node_id})
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        return resp.json()

    def report_result(self, job_id: str, result: dict) -> bool:
        resp = self._request("POST", f"/api/v1/agent/jobs/{job_id}/result", json=result)
        if resp.status_code == 409:  # el control ya lo procesó (reintento tras respuesta perdida)
            log.warning("job %s ya procesado por el control", job_id)
            return True
        resp.raise_for_status()
        return True

    def download_package(self, automation_id: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._http.stream("GET", f"/api/v1/agent/automations/{automation_id}/package") as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)

    def close(self) -> None:
        self._http.close()
