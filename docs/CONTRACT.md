# Contrato de paquete

Una automatización = `manifest.yaml` + `package.tar.gz` (el entrypoint debe estar dentro del tar).
Se valida con `shared/contracts/manifest.py::AutomationManifest` (`extra="forbid"`: cualquier campo desconocido se rechaza).
Ejemplo mínimo: `shared/contracts/examples/hello-world.yaml`.

## Campos
| Campo | Obligatorio | Regla |
|---|---|---|
| `apiVersion` | sí | literal `kispat/v1` |
| `kind` | sí | literal `Automation` |
| `metadata.name` | sí | `^[a-z0-9][a-z0-9-]{1,62}$` |
| `metadata.version` | sí | SemVer `X.Y.Z[-pre]` |
| `metadata.author` | sí | no vacío |
| `metadata.description`, `license` | no | por defecto `""`, `MIT` |
| `spec.runtime` | sí | `python3.12` \| `node22` \| `bash` \| `binary` |
| `spec.entrypoint` | sí | relativo; sin `..`, sin `/` inicial |
| `spec.args` | no | lista de strings |
| `spec.env` | no | solo valores no secretos; se rechazan claves tipo `*TOKEN*`, `*PASSWORD*`, `*SECRET*`, `*API_KEY*` |
| `spec.permissions.network` | no | `false` por defecto (`--network=none`) |
| `spec.permissions.filesystem.read/write` | no | rutas relativas, sin `..` |
| `spec.permissions.secrets` | no | **nombres lógicos**; se rechaza cualquier `=` |
| `spec.limits.cpu` | no (0.5) | 0.1 – 4.0 |
| `spec.limits.memory_mb` | no (256) | 32 – 4096 |
| `spec.limits.pids` | no (64) | 1 – 1024 |
| `spec.limits.timeout_s` | no (300) | 1 – 86400 |
| `spec.triggers` | sí | **no vacío**; `cron` exige `schedule`, `event` exige `event` |
| `spec.dependencies` | no | lista de strings |

## Qué se rechaza (422)
- `entrypoint` con `..` o ruta absoluta; entrypoint que no existe en el tar.
- `secrets` con valores (`NOMBRE=valor`); `env` con claves que parecen secretos.
- `memory_mb` < 32 o > 4096; `cpu` > 4.
- `triggers` vacío; versión no SemVer; campos desconocidos; `runtime` no soportado.
- tar con rutas absolutas, `..`, enlaces simbólicos o dispositivos.
- Misma `name@version` ya registrada (409).

Respuesta de error: `{"error": {"code": "invalid_manifest", "message": "...", "details": [{"field": "spec.entrypoint", "message": "..."}]}}`.

## Ciclo de vida
`AutomationState`: draft → pending_approval → approved → deployed → retired.
`JobStatus`: queued → assigned → running → succeeded | failed | cancelled.
