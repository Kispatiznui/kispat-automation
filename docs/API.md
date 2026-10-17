# API v0

Base: `http://192.168.1.50:8000`. Todo bajo `/api/v1` salvo `/health`. Swagger en `/docs`.
Errores: `{"error": {"code": "...", "message": "...", "details": [...]}}`.

```bash
export B=http://127.0.0.1:8000
export C="Authorization: Bearer $CLIENT_TOKEN"     # cliente (KISPAT CODE / VulkkRout)
export N="Authorization: Bearer $NODE_TOKEN"       # nodo
```

## Público
`GET /health` → `{"status":"ok","db":"ok","version":"0.1.0"}` (503 si la DB falla)
```bash
curl $B/health
```

## Nodos
`POST /api/v1/nodes/register` — Bearer = token propuesto por el nodo (≥32 chars), más `X-Registration-Token` si el control lo exige. Idempotente con el mismo token.
```bash
curl -X POST $B/api/v1/nodes/register -H "$N" -H "X-Registration-Token: $REG" -H 'Content-Type: application/json' \
  -d '{"node_id":"i3","hostname":"i3","os":"linux","arch":"x86_64","capabilities":{}}'
```
`POST /api/v1/nodes/{node_id}/heartbeat` (token de nodo) · `GET /api/v1/nodes` (token de cliente)
```bash
curl -X POST $B/api/v1/nodes/i3/heartbeat -H "$N"
curl $B/api/v1/nodes -H "$C"
```

## Automatizaciones (token de cliente)
```bash
curl -X POST $B/api/v1/automations -H "$C" -F "manifest.yaml=@manifest.yaml" -F "package.tar.gz=@package.tar.gz"   # 201 pending_approval | 422 | 409
curl -X POST $B/api/v1/automations/$ID/approve -H "$C"                                                              # 200 approved
curl $B/api/v1/automations -H "$C"
```

## Deployments (token de cliente)
```bash
curl -X POST $B/api/v1/deployments -H "$C" -H 'Content-Type: application/json' \
  -d '{"automation_id":"'$ID'","target_node_ids":["i3"]}'      # 201 { id, jobs:[{id,node_id}] }; 409 si no está aprobada
curl $B/api/v1/deployments -H "$C"
```

## Jobs
Nodo:
```bash
curl "$B/api/v1/agent/jobs?node_id=i3" -H "$N"                  # 200 JobOut | 204 sin trabajo | 403 si el token no es de ese nodo
curl -X POST $B/api/v1/agent/jobs/$JOB/result -H "$N" -H 'Content-Type: application/json' \
  -d '{"status":"succeeded","exit_code":0,"stdout":"hola","stderr":"","duration_ms":120}'
curl -o package.tar.gz $B/api/v1/agent/automations/$ID/package -H "$N"   # solo con un job activo de esa automatización
```
Cliente:
```bash
curl $B/api/v1/jobs/$JOB -H "$C"                                # estado + result (stdout/stderr truncados a 64 KB)
curl "$B/api/v1/jobs?status=failed&limit=20" -H "$C"
```
Un resultado `failed` con `attempts < max_attempts` reencola el job con backoff 60 s → 300 s → 900 s → 3600 s.
Reportar dos veces un job ya terminado es idempotente.
