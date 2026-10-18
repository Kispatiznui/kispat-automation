# Integración de KISPAT DataFlow con KISPAT Automation (instrucciones para Claude Code)

Este paquete se construyó a partir de los documentos de diseño del proyecto (`Contrato_paquete.md`, `agente.md`, `API FastAPI.md`, `Cola con SKIP LOCKED.md`), **sin ver el código real del repo**. Todo lo que dependa del repo está marcado `PENDIENTE DE VERIFICACIÓN`. Tu primera tarea es confirmarlo leyendo el código.

## Modelo de conexión (por API, sin tocar el núcleo)
```
Windows/KISPAT CODE ──client/kispat_client.py──► control (POST /api/v1/automations, /approve, /deployments)
                                                   │ job queued
                                   agente (pull) ◄─┘  descarga package.tar.gz a /var/lib/kispat/work/{job_id}/
                                        └─► podman run --network=none ... python run.py pipelines/<x>.json
                                              └─► imprime KISPAT_RESULT {json} y escribe /work/output/<run_id>/
```
DataFlow es un paquete más. El control no ejecuta nada; el nodo no decide qué ejecutar; no hay runtime paralelo.

## Reglas
1. No reescribas DataFlow dentro de `control/` ni `agent/`. Solo se permiten los cambios mínimos de las etapas de abajo.
2. No agregues dependencias al control ni al agente. DataFlow es stdlib + openpyxl vendorizado.
3. No metas secretos en el repo; `KISPAT_CLIENT_TOKEN` va por variable de entorno.
4. Cada etapa se prueba end-to-end antes de pasar a la siguiente. Marca cada hecho como VERIFICADO (`path:line`), PROPUESTA o PENDIENTE.

## Etapa 0 — Verificar supuestos (solo lectura)
Confirma en el repo y anota la respuesta con `path:line`:
| # | Supuesto | Dónde mirar |
|---|---|---|
| 0.1 | Campos multipart de `POST /api/v1/automations` (aquí: `manifest` y `package`) | `control/api/automations.py` |
| 0.2 | Las respuestas devuelven JSON con `id` (automation, deployment) | `control/api/*` |
| 0.3 | Auth del cliente: `Authorization: Bearer <CLIENT_TOKEN>` | `control/security.py` |
| 0.4 | El agente extrae el paquete en la raíz del workdir y ejecuta `python {entrypoint} {args}` con cwd=/work | `agent/executors/podman.py` |
| 0.5 | Cómo se aplica el timeout (`podman run --timeout` existe en Podman reciente; comprobar que realmente mata el contenedor) | `agent/executors/podman.py` |
| 0.6 | `--network=none` cuando `permissions.network=false`; límites siempre aplicados | idem |
| 0.7 | Qué pasa con el workdir tras la ejecución (¿se borra?) | idem / `agent/main.py` |
| 0.8 | Cómo `report_result` guarda stdout/resultado en `Job.result` y si hay endpoint de consulta de jobs para clientes | `control/queue/complete.py`, `control/api/agent_jobs.py` |
| 0.9 | Imagen base (`python:3.12-slim`) y si el usuario del contenedor puede escribir en `/work/output` | `podman.py` |
| 0.10 | Validación del manifest acepta `dependencies: ["openpyxl==3.1.5"]` y `args` con ruta relativa | `shared/contracts/manifest.py` |
Si algo difiere, ajusta **`client/kispat_client.py`** (constantes `MANIFEST_FIELD`/`PACKAGE_FIELD`) o el builder; no el control.

## Resultado de la Etapa 0 (ejecutada por Claude Code; pendiente de confirmar con el smoke test)
- 0.1: los campos multipart se llaman `manifest.yaml` y `package.tar.gz` (alias). `client/kispat_client.py` ya está corregido.
- 0.4: el paquete se extrae en `/work/app` y `agent/executors/podman.py` borra `app_dir` al terminar. Consecuencia: `output/` dentro del paquete se pierde (confirma el hueco B). Antes de la Etapa 1 decidir dónde escribe DataFlow (p. ej. `KISPAT_OUTPUT_DIR` fuera de `/work/app`) y quién lo recoge.
- 0.7: solo se borra `/work/app`; `/work/{job_id}/` persiste hasta enviar el resultado.
- 0.9: el contenedor corre como root (mapeado al usuario rootless) con `--read-only`: comprobar que `/work` es escribible para `output/`.
- 0.5: hay dos timeouts (`--timeout` de Podman y `subprocess.run(timeout+10)`); falta una prueba real de que el contenedor muere.

## Etapa 1 — Smoke test sin cambios al repo
1. Copia esta carpeta al repo (sugerido: `integrations/dataflow/`) y ejecuta `python -m unittest discover -s tests` (debe dar 20/20).
2. `python package/build_package.py --with-demo-data --vendor`.
3. Publica, aprueba y despliega con `client/kispat_client.py` contra el nodo real.
4. Espera el job y verifica que su stdout contenga `KISPAT_RESULT` con `status: succeeded` y métricas `received=56 accepted=50 review=4 rejected=2`.
Condición de avance: el job termina `succeeded` y los números coinciden.

## Etapa 2 — Huecos probables (cambio mínimo, uno por vez, con prueba)
Estos huecos se deducen de los documentos de diseño; confírmalos en la Etapa 0 antes de tocar nada.
| Hueco | Síntoma | Cambio mínimo propuesto |
|---|---|---|
| A. `KISPAT_JOB_ID` no llega al contenedor | `run_id` aleatorio; los reintentos no son idempotentes | `agent/executors/podman.py`: pasar `-e KISPAT_JOB_ID={job_id}` (y opcional `KISPAT_AUTOMATION_ID`, `KISPAT_IMAGE`) |
| B. Los artefactos se pierden al limpiar el workdir | `consolidated.xlsx` desaparece | El agente copia `output/<run_id>/` a `/var/lib/kispat/jobs/{job_id}/artifacts/` (dueño `kispat-agent`) **antes** de limpiar, y reporta rutas y sha256 leídos de `run_report.json` |
| C. El resultado estructurado no llega a `Job.result` | Solo hay stdout crudo | `report_result`: parsear la última línea `KISPAT_RESULT ` y guardar el JSON en `Job.result` (sin valores sensibles; DataFlow ya no los imprime) |
| D. Reintentos ciegos | Un exit 3/4/5 se reintenta 3 veces sin sentido | Tratar exit 3, 4 y 5 como definitivos (`failed` sin backoff); solo exit 1, timeout y OOM reintentan |
| E. Entradas de clientes reales | El contrato solo monta el workdir; no hay forma de entregar archivos del cliente | Definir (como decisión, no implementar a ciegas) una de: archivos de entrada dentro del paquete por ejecución, o montaje de solo lectura de un directorio autorizado vía `permissions.filesystem.read` |
Para cada hueco: pregunta si de verdad existe, propón el diff mínimo, escribe la prueba, aplícalo, verifica.

## Etapa 3 — Reconciliación y trazabilidad (opcional, después de A–D)
Relacionar `Job.id` ↔ `run_id` ↔ `run_report.json` (hashes de entradas y salidas, versión de pipeline, imagen) en `Job.result`. Sin tablas nuevas salvo necesidad demostrada.

## Criterios de aceptación finales
- [ ] Smoke test E2E con el job `succeeded` y métricas esperadas.
- [ ] Un reintento del mismo job no duplica salidas (mismo `run_id`).
- [ ] Un pipeline inválido o entrada malformada termina `failed` sin reintentos.
- [ ] Ningún cambio en `control/` más allá de lo listado; ninguna dependencia nueva en control/agente.
- [ ] Las salidas del job están disponibles tras limpiar el workdir.
- [ ] Nada de tokens en logs ni en el repo.

## Prompt para pegar en Claude Code
> Lee `INTEGRATION.md` de esta carpeta y ejecútalo por etapas. Empieza por la Etapa 0 (solo lectura) y muéstrame la tabla de supuestos verificados antes de modificar nada. No cambies `control/` ni `agent/` fuera de los cambios mínimos de la Etapa 2, y solo tras mi confirmación de cada uno. Usa Graphify para explorar el repo.
