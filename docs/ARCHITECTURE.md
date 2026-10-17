# Arquitectura (resumen)

```
KISPAT CODE / VulkkRout ──HTTP + Bearer cliente──▶  CONTROL (kispat-ctrl, :8000)
                                                      │  FastAPI · registry · cola
                                                      ▼
                                                  PostgreSQL 16 (localhost)
                                                      ▲
AGENTE (kispat-agent) ──HTTP + Bearer nodo (pull)─────┘
   └─ Podman rootless: --memory --cpus --pids-limit --timeout [--network=none]
```

## Principios
1. **Control ≠ nodo**: dos units systemd, dos usuarios Linux (`kispat-ctrl`, `kispat-agent`), aunque compartan máquina.
2. **El control no ejecuta código de usuario; el nodo no decide qué ejecutar.** Solo corre jobs ya creados por un deployment de una automatización *aprobada*.
3. **El agente no toca la DB**: solo HTTP al control, siempre con Bearer.
4. **Núcleo pequeño, adaptadores intercambiables**: `Executor` (Protocol) → hoy `PodmanExecutor`.

## Flujo
`POST /automations` (manifest + tar.gz, valida → `pending_approval`) → `approve` → `POST /deployments` (1 job por nodo) →
el agente hace `GET /agent/jobs` (`UPDATE … FOR UPDATE SKIP LOCKED`, estado `assigned`) → descarga el paquete (solo si tiene un job activo de esa automatización) →
Podman → `POST /agent/jobs/{id}/result` (`succeeded`, o reencola con backoff 60/300/900/3600 s, o `failed` al agotar `max_attempts`).

## Seguridad (Fase 1)
- Tokens estáticos: `CLIENT_TOKEN` (clientes) y un token por nodo (se guarda solo su sha256). Un token de cliente no sirve en endpoints de nodo (403) y viceversa (401).
- Registro de nodo: trust-on-first-use + `REGISTRATION_TOKEN` compartido (`X-Registration-Token`).
- Paquetes: tar validado (sin `..`, sin rutas absolutas, sin enlaces), manifest estricto (`extra="forbid"`), secretos solo como nombres.
- Contenedor: `--cap-drop=ALL`, `no-new-privileges`, `--read-only`, `/tmp` tmpfs, sin red salvo `permissions.network: true`.

## Límites conocidos (Fase 1)
- Sin recuperación de jobs `assigned` huérfanos si el agente muere a mitad (no hay lease/timeout de asignación).
- Estado `running` definido pero sin uso; los triggers `cron`/`event` se validan pero no se ejecutan (Fase 2, `scheduler/`).
- Inyección de secretos en el job no implementada: el manifest solo declara nombres.
- HTTP sin TLS (LAN doméstica).
