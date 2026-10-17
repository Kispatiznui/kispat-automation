from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NodeRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    hostname: str = Field(min_length=1, max_length=255)
    os: str = Field(default="linux", max_length=64)
    arch: str = Field(default="x86_64", max_length=32)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class NodeRegisterResponse(BaseModel):
    node_id: str
    node_token: str


class NodeHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = "online"
