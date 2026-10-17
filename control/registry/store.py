"""Valida y guarda paquetes de automatizaciones en el filesystem del control."""
import io
import shutil
import tarfile
import uuid
from pathlib import Path

import yaml
from pydantic import ValidationError

from control.config import get_settings
from shared.contracts.manifest import AutomationManifest


class ManifestError(Exception):
    def __init__(self, details: list[dict]):
        super().__init__("manifest inválido")
        self.details = details


class PackageError(Exception):
    pass


def parse_manifest(raw: bytes) -> AutomationManifest:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ManifestError([{"field": "manifest.yaml", "message": f"YAML inválido: {exc}"}]) from exc
    if not isinstance(data, dict):
        raise ManifestError([{"field": "manifest.yaml", "message": "debe ser un mapa YAML"}])
    try:
        return AutomationManifest.model_validate(data)
    except ValidationError as exc:
        details = [
            {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
            for e in exc.errors()
        ]
        raise ManifestError(details) from exc


def validate_package(raw: bytes, manifest: AutomationManifest) -> None:
    """tar.gz seguro: sin rutas absolutas/'..', sin enlaces ni dispositivos, con entrypoint."""
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            names = set()
            for m in tar.getmembers():
                n = m.name.lstrip("./")
                if m.name.startswith("/") or ".." in Path(m.name).parts:
                    raise PackageError(f"ruta insegura en el paquete: {m.name}")
                if not (m.isfile() or m.isdir()):
                    raise PackageError(f"tipo de entrada no permitido: {m.name}")
                names.add(n)
    except tarfile.TarError as exc:
        raise PackageError(f"package.tar.gz inválido: {exc}") from exc
    if manifest.spec.entrypoint.lstrip("./") not in names:
        raise PackageError(f"entrypoint '{manifest.spec.entrypoint}' no está en el paquete")


def package_dir(automation_id: uuid.UUID, version: str) -> Path:
    return get_settings().AUTOMATIONS_DIR / str(automation_id) / version


def save_package(
    automation_id: uuid.UUID, manifest_raw: bytes, package_raw: bytes, version: str
) -> Path:
    d = package_dir(automation_id, version)
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_bytes(manifest_raw)
    (d / "package.tar.gz").write_bytes(package_raw)
    return d / "package.tar.gz"


def remove_package(automation_id: uuid.UUID) -> None:
    shutil.rmtree(get_settings().AUTOMATIONS_DIR / str(automation_id), ignore_errors=True)
