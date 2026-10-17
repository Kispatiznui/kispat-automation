from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    VERSION: str = "0.1.0"
    DATABASE_URL: str = "postgresql+psycopg://kispat:kispat@127.0.0.1:5432/kispat"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # Auth (Bearer estático, Fase 1). Vacío = rechaza todo (fail closed).
    CLIENT_TOKEN: str = ""
    REGISTRATION_TOKEN: str = ""  # si se define, register exige X-Registration-Token

    NODE_TOKEN_TTL_DAYS: int = 365
    AGENT_PULL_INTERVAL_S: int = 5

    AUTOMATIONS_DIR: Path = Path("/var/lib/kispat/automations")
    AUDIT_PATH: Path = Path("/var/lib/kispat/audit.jsonl")
    MAX_PACKAGE_MB: int = 50

    # Orígenes web permitidos (el cliente Electron no depende de CORS)
    CORS_ORIGIN_REGEX: str = r"^http://(192\.168\.1\.\d{1,3}|localhost|127\.0\.0\.1)(:\d+)?$"


@lru_cache
def get_settings() -> Settings:
    return Settings()
