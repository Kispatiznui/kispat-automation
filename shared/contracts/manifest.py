"""Contrato del paquete: valida manifest.yaml de una automatización KISPAT."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SECRET_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
SECRETISH_ENV_RE = re.compile(r"(secret|passw(or)?d|token|api_?key|private_?key)", re.I)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _check_relative_path(value: str, what: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{what} no puede estar vacío")
    if ".." in value:
        raise ValueError(f"{what} no puede contener '..'")
    if value.startswith("/") or value.startswith("\\") or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"{what} no puede ser una ruta absoluta")
    return value


class Metadata(_Strict):
    name: str
    version: str
    author: str = Field(min_length=1)
    description: str = ""
    license: str = "MIT"

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("name debe ser minúsculas/dígitos/guiones (2-63 caracteres)")
        return v

    @field_validator("version")
    @classmethod
    def _version(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError("version debe cumplir SemVer (p. ej. 1.2.3 o 1.2.3-rc.1)")
        return v


class Filesystem(_Strict):
    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)

    @field_validator("read", "write")
    @classmethod
    def _paths(cls, v: list[str]) -> list[str]:
        return [_check_relative_path(p, "ruta de filesystem") for p in v]


class Permissions(_Strict):
    filesystem: Filesystem = Field(default_factory=Filesystem)
    network: bool = False  # sin declararlo => sin red
    secrets: list[str] = Field(default_factory=list)  # SOLO nombres lógicos

    @field_validator("secrets")
    @classmethod
    def _secrets(cls, v: list[str]) -> list[str]:
        for s in v:
            if "=" in s:
                raise ValueError("secrets debe contener nombres lógicos, nunca NOMBRE=valor")
            if not SECRET_NAME_RE.match(s):
                raise ValueError(f"nombre de secreto inválido: {s!r}")
        return v


class Limits(_Strict):
    cpu: float = Field(default=0.5, ge=0.1, le=4.0)
    memory_mb: int = Field(default=256, ge=32, le=4096)
    pids: int = Field(default=64, ge=1, le=1024)
    timeout_s: int = Field(default=300, ge=1, le=86400)


class Trigger(_Strict):
    type: Literal["manual", "cron", "event"]
    schedule: str | None = None
    event: str | None = None

    @model_validator(mode="after")
    def _consistency(self) -> "Trigger":
        if self.type == "cron" and not self.schedule:
            raise ValueError("trigger cron requiere 'schedule'")
        if self.type == "event" and not self.event:
            raise ValueError("trigger event requiere 'event'")
        return self


class Spec(_Strict):
    runtime: Literal["python3.12", "node22", "bash", "binary"]
    entrypoint: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    permissions: Permissions = Field(default_factory=Permissions)
    limits: Limits = Field(default_factory=Limits)
    triggers: list[Trigger] = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)

    @field_validator("entrypoint")
    @classmethod
    def _entrypoint(cls, v: str) -> str:
        return _check_relative_path(v, "entrypoint")

    @field_validator("env")
    @classmethod
    def _env(cls, v: dict[str, str]) -> dict[str, str]:
        for k in v:
            if not ENV_KEY_RE.match(k):
                raise ValueError(f"nombre de variable inválido: {k!r}")
            if SECRETISH_ENV_RE.search(k):
                raise ValueError(
                    f"{k!r} parece un secreto: decláralo en permissions.secrets, no en env"
                )
        return v


class AutomationManifest(_Strict):
    api_version: Literal["kispat/v1"] = Field(alias="apiVersion")
    kind: Literal["Automation"]
    metadata: Metadata
    spec: Spec
