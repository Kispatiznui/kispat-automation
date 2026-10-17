#!/usr/bin/env bash
# Instala KISPAT Automation en un host Debian 13 (control + agente en la misma maquina).
# Idempotente: se puede relanzar; no regenera tokens ni contrasenas existentes.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR=/opt/kispat
STATE_DIR=/var/lib/kispat
CONF_DIR=/etc/kispat
AGENT_HOME="$STATE_DIR/agent-home"
CTRL_PORT=8000
IMAGE="docker.io/library/python:3.12-slim"

log()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install] AVISO:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[install] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }
trap 'printf "\033[1;31m[install] FALLO en la linea %s\033[0m\n" "$LINENO" >&2' ERR

[[ $EUID -eq 0 ]] || die "ejecuta como root (sudo $0)"
[[ -f "$SRC_DIR/pyproject.toml" ]] || die "no encuentro pyproject.toml en $SRC_DIR"

# ---------------------------------------------------------------- 1. paquetes
log "1/9 Paquetes del sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq postgresql postgresql-contrib podman uidmap passt \
    curl openssl rsync jq ca-certificates >/dev/null
if ! command -v uv >/dev/null; then
    log "instalando uv"
    curl -LsSf https://astral.sh/uv/install.sh | UV_UNMANAGED_INSTALL=/usr/local/bin sh
fi

# ---------------------------------------------------------------- 2. usuarios
log "2/9 Usuarios (sin shell, sin login)"
id -u kispat-ctrl >/dev/null 2>&1 || \
    useradd --system --no-create-home --shell /usr/sbin/nologin kispat-ctrl
id -u kispat-agent >/dev/null 2>&1 || \
    useradd --system --create-home --home-dir "$AGENT_HOME" --shell /usr/sbin/nologin kispat-agent
grep -q '^kispat-agent:' /etc/subuid || usermod --add-subuids 200000-265535 kispat-agent
grep -q '^kispat-agent:' /etc/subgid || usermod --add-subgids 200000-265535 kispat-agent
AGENT_UID="$(id -u kispat-agent)"

# ---------------------------------------------------------------- 3. directorios
log "3/9 Directorios"
install -d -o kispat-ctrl  -g kispat-ctrl  -m 0755 "$STATE_DIR"
install -d -o kispat-ctrl  -g kispat-ctrl  -m 0750 "$STATE_DIR/automations" "$STATE_DIR/jobs"
install -d -o kispat-agent -g kispat-agent -m 0750 "$STATE_DIR/work"
install -d -o kispat-agent -g kispat-agent -m 0700 "$AGENT_HOME"
install -d -o root -g root -m 0755 "$CONF_DIR" "$APP_DIR"

# ---------------------------------------------------------------- 4. PostgreSQL
log "4/9 PostgreSQL"
systemctl enable --now postgresql
pg() { (cd /tmp && runuser -u postgres -- psql -v ON_ERROR_STOP=1 -q "$@"); }
pg -tAc "SELECT 1 FROM pg_roles WHERE rolname='kispat'" | grep -q 1 || pg -c "CREATE ROLE kispat LOGIN"
pg -tAc "SELECT 1 FROM pg_database WHERE datname='kispat'" | grep -q 1 || \
    pg -c "CREATE DATABASE kispat OWNER kispat"

# ---------------------------------------------------------------- 5. env + tokens
log "5/9 Configuracion y tokens"
umask 077
if [[ ! -f "$CONF_DIR/control.env" ]]; then
    DB_PASS="$(openssl rand -hex 24)"
    printf "ALTER ROLE kispat PASSWORD '%s';\n" "$DB_PASS" | pg
    cat > "$CONF_DIR/control.env" <<ENV
DATABASE_URL=postgresql+psycopg://kispat:${DB_PASS}@127.0.0.1:5432/kispat
HOST=0.0.0.0
PORT=${CTRL_PORT}
CLIENT_TOKEN=$(openssl rand -hex 32)
REGISTRATION_TOKEN=$(openssl rand -hex 32)
AUTOMATIONS_DIR=${STATE_DIR}/automations
AUDIT_PATH=${STATE_DIR}/audit.jsonl
ENV
    log "creado $CONF_DIR/control.env"
fi
if [[ ! -f "$CONF_DIR/agent.env" ]]; then
    REG="$(grep '^REGISTRATION_TOKEN=' "$CONF_DIR/control.env" | cut -d= -f2-)"
    cat > "$CONF_DIR/agent.env" <<ENV
CTRL_URL=http://127.0.0.1:${CTRL_PORT}
NODE_ID=$(hostname)
NODE_TOKEN=$(openssl rand -hex 32)
REGISTRATION_TOKEN=${REG}
WORK_DIR=${STATE_DIR}/work
HOME=${AGENT_HOME}
XDG_RUNTIME_DIR=/run/user/${AGENT_UID}
ENV
    log "creado $CONF_DIR/agent.env"
fi
chown kispat-ctrl:kispat-ctrl "$CONF_DIR/control.env"; chmod 0600 "$CONF_DIR/control.env"
chown kispat-agent:kispat-agent "$CONF_DIR/agent.env"; chmod 0600 "$CONF_DIR/agent.env"
umask 022

# ---------------------------------------------------------------- 6. codigo + venv
log "6/9 Codigo en $APP_DIR y entorno uv"
rsync -a --delete --exclude .git --exclude .venv --exclude __pycache__ --exclude python \
    "$SRC_DIR"/ "$APP_DIR"/
(cd "$APP_DIR" && UV_PROJECT_ENVIRONMENT="$APP_DIR/.venv" UV_PYTHON_INSTALL_DIR="$APP_DIR/python" \
    uv sync --all-extras --no-dev)
chmod -R a+rX "$APP_DIR"

# ---------------------------------------------------------------- 7. migraciones
log "7/9 Migraciones (alembic upgrade head)"
set -a; . "$CONF_DIR/control.env"; set +a
(cd "$APP_DIR" && runuser -u kispat-ctrl -- env DATABASE_URL="$DATABASE_URL" \
    "$APP_DIR/.venv/bin/alembic" -c "$APP_DIR/alembic.ini" upgrade head)

# ---------------------------------------------------------------- 8. podman rootless
log "8/9 Podman rootless para kispat-agent"
loginctl enable-linger kispat-agent
for _ in $(seq 1 30); do [[ -d /run/user/$AGENT_UID ]] && break; sleep 1; done
[[ -d /run/user/$AGENT_UID ]] || warn "/run/user/$AGENT_UID no existe: revisa 'loginctl enable-linger'"
(cd /tmp && runuser -u kispat-agent -- env HOME="$AGENT_HOME" XDG_RUNTIME_DIR="/run/user/$AGENT_UID" \
    podman pull "$IMAGE") || warn "no se pudo pre-descargar $IMAGE (el primer job la descargara)"

# ---------------------------------------------------------------- 9. systemd
log "9/9 Units systemd"
install -m 0644 "$SRC_DIR/deploy/kispat-control.service" /etc/systemd/system/
install -m 0644 "$SRC_DIR/deploy/kispat-agent.service"   /etc/systemd/system/
systemctl daemon-reload
systemctl enable kispat-control kispat-agent
systemctl restart kispat-control
for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:$CTRL_PORT/health" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "http://127.0.0.1:$CTRL_PORT/health" >/dev/null || die "el control no responde en /health (journalctl -u kispat-control)"
systemctl restart kispat-agent

echo
systemctl --no-pager status kispat-control kispat-agent || true
echo
log "Listo. Tokens en $CONF_DIR/*.env (solo root los lee). Prueba: $APP_DIR/scripts/e2e_test.sh"
