"""Cliente HTTP (stdlib) hacia el control de KISPAT Automation. Token SOLO en cabecera.

Campos multipart verificados en Etapa 0 por lectura de código; se confirman con el smoke test (Etapa 1).
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# Verificado en Etapa 0 (control/api/automations.py:48-49): alias de los UploadFile
MANIFEST_FIELD, PACKAGE_FIELD = "manifest.yaml", "package.tar.gz"


def _multipart(fields: dict[str, tuple[str, bytes, str]]):
    b = uuid.uuid4().hex
    parts = []
    for name, (fname, data, ctype) in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{name}"; filename="{fname}"\r\n'
                     f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n")
    return b"".join(parts) + f"--{b}--\r\n".encode(), f"multipart/form-data; boundary={b}"


class Client:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: int = 30):
        self.base = (base_url or os.environ.get("KISPAT_CONTROL_URL", "http://192.168.1.50:8000")).rstrip("/")
        self.token = token or os.environ.get("KISPAT_CLIENT_TOKEN")
        if not self.token:
            raise SystemExit("Falta KISPAT_CLIENT_TOKEN (variable de entorno)")
        self.timeout = timeout

    def _req(self, method, path, body=None, ctype="application/json"):
        req = urllib.request.Request(self.base + path, data=body, method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{method} {path} -> HTTP {e.code}: {e.read()[:500].decode(errors='replace')}")

    def publish(self, manifest: Path, package: Path) -> dict:
        body, ct = _multipart({MANIFEST_FIELD: ("manifest.yaml", Path(manifest).read_bytes(), "application/x-yaml"),
                               PACKAGE_FIELD: ("package.tar.gz", Path(package).read_bytes(), "application/gzip")})
        return self._req("POST", "/api/v1/automations", body, ct)

    def approve(self, automation_id: str) -> dict:
        return self._req("POST", f"/api/v1/automations/{automation_id}/approve", b"{}")

    def deploy(self, automation_id: str, node_ids: list[str]) -> dict:
        return self._req("POST", "/api/v1/deployments",
                         json.dumps({"automation_id": automation_id, "target_node_ids": node_ids}).encode())


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kispat_client")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publish"); p.add_argument("manifest"); p.add_argument("package")
    a_ = sub.add_parser("approve"); a_.add_argument("automation_id")
    d = sub.add_parser("deploy"); d.add_argument("automation_id"); d.add_argument("node_ids", nargs="+")
    a = ap.parse_args(argv)
    c = Client()
    res = (c.publish(Path(a.manifest), Path(a.package)) if a.cmd == "publish"
           else c.approve(a.automation_id) if a.cmd == "approve" else c.deploy(a.automation_id, a.node_ids))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
