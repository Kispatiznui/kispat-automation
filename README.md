# KISPAT Automation

Plataforma de ejecución persistente de automatizaciones del ecosistema KISPAT.
Recibe paquetes autorizados (KISPAT CODE), los registra, los despliega en nodos y administra ejecuciones.

- **control/**: API FastAPI + cola en PostgreSQL (`SKIP LOCKED`). Nunca ejecuta código de usuario.
- **agent/**: proceso del nodo. Hace pull de jobs, ejecuta en Podman rootless y reporta. No decide qué ejecutar.
- **shared/**: contratos Pydantic (manifest, estados, jobs).

## Arranque local (desarrollo)

```bash
uv sync --all-extras                      # control + agente + dev
cp .env.example .env                      # ajusta DATABASE_URL y tokens
alembic upgrade head
uv run kispat-control                     # http://127.0.0.1:8000/docs
# en otra terminal (necesita podman):
CTRL_URL=http://127.0.0.1:8000 NODE_ID=dev NODE_TOKEN=$(openssl rand -hex 32) uv run kispat-agent
```

## Tests (PostgreSQL real, no SQLite)

```bash
sudo -u postgres createdb kispat_test
sudo -u postgres createuser kispat_test
sudo -u postgres psql -c "ALTER USER kispat_test PASSWORD 'kispat_test';"
sudo -u postgres psql -c "ALTER DATABASE kispat_test OWNER TO kispat_test;"
export TEST_DATABASE_URL="postgresql+psycopg://kispat_test:kispat_test@127.0.0.1:5432/kispat_test"
uv run pytest
```

> `conftest.py` hace `drop_all/create_all` en esa base: usa una base dedicada, nunca la de producción.
> En Windows no hay `runuser`/`sudo -u`: usa un PostgreSQL local y el mismo `TEST_DATABASE_URL`.

## Despliegue en el i3 (Debian 13)

`sudo deploy/install.sh` y luego `sudo scripts/e2e_test.sh`. Detalle en [docs/DEPLOY.md](docs/DEPLOY.md).

## Docs

[ARCHITECTURE](docs/ARCHITECTURE.md) · [CONTRACT](docs/CONTRACT.md) · [DEPLOY](docs/DEPLOY.md) · [API](docs/API.md)
