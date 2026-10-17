#!/usr/bin/env bash
# Desinstala KISPAT Automation. DESTRUCTIVO: borra datos, usuarios y configuracion.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "ejecuta como root" >&2; exit 1; }

cat <<MSG
Se va a:
  - parar y deshabilitar kispat-control y kispat-agent, borrar sus units
  - borrar los usuarios kispat-ctrl y kispat-agent
  - borrar /var/lib/kispat (paquetes, workdirs, auditoria), /etc/kispat y /opt/kispat
La base de datos PostgreSQL 'kispat' se conserva salvo que lo confirmes aparte.
MSG
read -r -p "Escribe 'si' para continuar: " ans
[[ "$ans" == "si" ]] || { echo "cancelado"; exit 0; }

for svc in kispat-agent kispat-control; do
    systemctl disable --now "$svc" 2>/dev/null || true
    rm -f "/etc/systemd/system/$svc.service"
done
systemctl daemon-reload

id -u kispat-agent >/dev/null 2>&1 && loginctl disable-linger kispat-agent 2>/dev/null || true
for u in kispat-agent kispat-ctrl; do
    id -u "$u" >/dev/null 2>&1 && userdel "$u" || true
done
rm -rf /var/lib/kispat /etc/kispat /opt/kispat

read -r -p "¿Borrar tambien la base de datos 'kispat' y el rol? (si/no): " dropdb
if [[ "$dropdb" == "si" ]]; then
    (cd /tmp && runuser -u postgres -- psql -c "DROP DATABASE IF EXISTS kispat" -c "DROP ROLE IF EXISTS kispat")
fi
echo "Desinstalado."
