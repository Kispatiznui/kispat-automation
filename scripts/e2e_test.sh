#!/usr/bin/env bash
# Prueba end-to-end en el host: nodo -> automatizacion -> aprobacion -> deployment -> job -> stdout.
# Requiere: control y agente corriendo (systemd), curl y jq.
set -euo pipefail

BASE="${KISPAT_URL:-http://127.0.0.1:8000}"
TIMEOUT="${E2E_TIMEOUT:-60}"
EXPECTED="hola desde kispat automation"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STEP="inicio"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

fail() { printf '\033[1;31m✗ FALLO en el paso "%s":\033[0m %s\n' "$STEP" "$*" >&2; exit 1; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
step() { STEP="$1"; printf '\n== %s\n' "$1"; }
envval() { grep -s "^$2=" "$1" | head -n1 | cut -d= -f2-; }

command -v jq   >/dev/null || fail "falta jq (apt install jq)"
command -v curl >/dev/null || fail "falta curl"

CLIENT_TOKEN="${CLIENT_TOKEN:-$(envval /etc/kispat/control.env CLIENT_TOKEN)}"
[[ -n "$CLIENT_TOKEN" ]] || fail "no hay CLIENT_TOKEN (exportalo o ejecuta como root para leer /etc/kispat/control.env)"
NODE_ID="${NODE_ID:-$(envval /etc/kispat/agent.env NODE_ID)}"; NODE_ID="${NODE_ID:-$(hostname)}"
AUTH=(-H "Authorization: Bearer $CLIENT_TOKEN")

CODE=""; BODY=""
req() {  # req METODO RUTA [args curl]  -> CODE, BODY
    local method="$1" path="$2"; shift 2
    local out
    out="$(curl -sS -m 30 -w $'\n%{http_code}' -X "$method" "$BASE$path" "$@")" \
        || fail "curl no pudo conectar con $BASE$path (¿esta corriendo kispat-control?)"
    CODE="${out##*$'\n'}"; BODY="${out%$'\n'*}"
}
expect() { [[ "$CODE" == "$1" ]] || fail "$2 -> HTTP $CODE (esperado $1): $BODY"; }

step "1. health del control"
req GET /health; expect 200 "GET /health"
[[ "$(jq -r .db <<<"$BODY")" == "ok" ]] || fail "la base de datos no responde: $BODY"
ok "control y DB OK"

step "2. nodo '$NODE_ID' registrado"
req GET /api/v1/nodes "${AUTH[@]}"; expect 200 "GET /api/v1/nodes"
if ! jq -e --arg n "$NODE_ID" 'any(.[]; .node_id == $n)' <<<"$BODY" >/dev/null; then
    NODE_TOKEN="${NODE_TOKEN:-$(envval /etc/kispat/agent.env NODE_TOKEN)}"
    REG="${REGISTRATION_TOKEN:-$(envval /etc/kispat/agent.env REGISTRATION_TOKEN)}"
    [[ -n "$NODE_TOKEN" ]] || fail "el nodo no existe y no puedo leer NODE_TOKEN para registrarlo"
    req POST /api/v1/nodes/register -H "Authorization: Bearer $NODE_TOKEN" \
        -H "X-Registration-Token: $REG" -H 'Content-Type: application/json' \
        -d "$(jq -n --arg n "$NODE_ID" --arg h "$(hostname)" '{node_id:$n,hostname:$h,os:"linux",arch:"x86_64",capabilities:{}}')"
    expect 200 "POST /api/v1/nodes/register"
fi
ok "nodo presente"

step "3. construir paquete hello-world"
VERSION="0.1.$(date +%s)"
sed -E "s/^(  version: ).*/\1${VERSION}/" "$ROOT/shared/contracts/examples/hello-world.yaml" > "$TMP/manifest.yaml"
cp "$ROOT/shared/contracts/examples/main.py" "$TMP/main.py"
tar -C "$TMP" -czf "$TMP/package.tar.gz" main.py
ok "hello-world@$VERSION"

step "4. crear automatizacion"
req POST /api/v1/automations "${AUTH[@]}" \
    -F "manifest.yaml=@$TMP/manifest.yaml" -F "package.tar.gz=@$TMP/package.tar.gz"
expect 201 "POST /api/v1/automations"
AUTO_ID="$(jq -r .id <<<"$BODY")"
[[ "$(jq -r .state <<<"$BODY")" == "pending_approval" ]] || fail "estado inesperado: $BODY"
ok "automatizacion $AUTO_ID (pending_approval)"

step "5. aprobar"
req POST "/api/v1/automations/$AUTO_ID/approve" "${AUTH[@]}"; expect 200 "approve"
ok "aprobada"

step "6. crear deployment"
req POST /api/v1/deployments "${AUTH[@]}" -H 'Content-Type: application/json' \
    -d "$(jq -n --arg a "$AUTO_ID" --arg n "$NODE_ID" '{automation_id:$a,target_node_ids:[$n]}')"
expect 201 "POST /api/v1/deployments"
JOB_ID="$(jq -r '.jobs[0].id' <<<"$BODY")"
ok "job $JOB_ID encolado"

step "7. esperar job succeeded (max ${TIMEOUT}s)"
STATUS=""
for ((i = 0; i < TIMEOUT; i++)); do
    req GET "/api/v1/jobs/$JOB_ID" "${AUTH[@]}"; expect 200 "GET /api/v1/jobs/$JOB_ID"
    STATUS="$(jq -r .status <<<"$BODY")"
    [[ "$STATUS" == "succeeded" ]] && break
    if [[ "$STATUS" == "failed" ]]; then
        fail "el job fallo: error=$(jq -r .error <<<"$BODY") stderr=$(jq -r '.result.stderr // ""' <<<"$BODY")"
    fi
    sleep 1
done
[[ "$STATUS" == "succeeded" ]] || fail "timeout: el job sigue en '$STATUS' (error=$(jq -r '.error // "-"' <<<"$BODY")). ¿Corre kispat-agent? journalctl -u kispat-agent -n 50"
ok "job succeeded"

step "8. verificar stdout"
OUT="$(jq -r '.result.stdout // ""' <<<"$BODY")"
[[ "$OUT" == *"$EXPECTED"* ]] || fail "stdout no contiene '$EXPECTED'. stdout='$OUT'"
ok "stdout contiene: $EXPECTED"

printf '\n\033[1;32mE2E OK\033[0m\n'
