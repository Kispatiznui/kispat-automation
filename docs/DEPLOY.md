# Despliegue en el i3 (Debian 13 minimal, 192.168.1.50)

Todo en un solo host; el único acceso a Internet es `apt`, el instalador de `uv` y la descarga de la imagen de Podman.

## Pasos
1. **Copiar el repo** al i3 (p. ej. `scp -r kispat-automation usuario@192.168.1.50:~`).
2. **Instalar**: `cd kispat-automation && sudo deploy/install.sh`.
   *Verificar*: termina con `systemctl status` de ambos servicios en `active (running)`.
3. **Control sano**: `curl http://127.0.0.1:8000/health` → `{"status":"ok","db":"ok","version":"0.1.0"}`.
4. **Agente registrado**: `sudo grep CLIENT_TOKEN /etc/kispat/control.env`, luego
   `curl -H "Authorization: Bearer <CLIENT_TOKEN>" http://127.0.0.1:8000/api/v1/nodes` → debe listar el nodo con `status: online`.
5. **Podman rootless del agente**: `journalctl -u kispat-agent -n 30` sin errores de `XDG_RUNTIME_DIR`/`newuidmap`.
   *Verificar imagen*: `sudo runuser -u kispat-agent -- env HOME=/var/lib/kispat/agent-home XDG_RUNTIME_DIR=/run/user/$(id -u kispat-agent) podman images`.
6. **E2E**: `sudo scripts/e2e_test.sh` → debe terminar en `E2E OK`. Si falla, indica el paso exacto.
7. **Acceso desde Windows (KISPAT CODE)**: `http://192.168.1.50:8000/docs`; usa `CLIENT_TOKEN` como Bearer.

## Qué hace el instalador (idempotente)
Usuarios `kispat-ctrl`/`kispat-agent` (nologin) · `/var/lib/kispat/{automations,jobs,work}` · `/etc/kispat/{control,agent}.env` (0600, tokens con `openssl rand -hex 32`, solo se generan si faltan) ·
PostgreSQL + DB/rol `kispat` · código en `/opt/kispat` + `uv sync --all-extras` · `alembic upgrade head` · `loginctl enable-linger kispat-agent` (Podman rootless) · units systemd.

## Operación
- Logs: `journalctl -u kispat-control -f`, `journalctl -u kispat-agent -f`. Auditoría: `/var/lib/kispat/audit.jsonl`.
- Actualizar: `git pull && sudo deploy/install.sh` (conserva tokens y datos).
- Desinstalar: `sudo deploy/uninstall.sh` (pide confirmación).

## Problemas típicos
- `/run/user/<uid>` no existe → `loginctl enable-linger kispat-agent` y reiniciar el agente.
- `podman` falla con `newuidmap`/subuid → comprobar `/etc/subuid` y `/etc/subgid` (entrada `kispat-agent`).
- El primer job tarda: descarga de `python:3.12-slim`; el instalador intenta pre-descargarla.
- El puerto 8000 queda abierto a la LAN (`HOST=0.0.0.0`); no hay TLS. No lo expongas fuera de la LAN.
