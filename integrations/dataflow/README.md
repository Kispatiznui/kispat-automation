# KISPAT DataFlow (MVP 0.1.0)

Motor determinista de datos (CSV / xlsx) que se empaqueta como **una automatización más** de KISPAT Automation.
No toca `control/` ni `agent/`: se conecta por la API existente (publicar → aprobar → desplegar) y el agente lo ejecuta en Podman.

## Contenido
| Ruta | Función |
|---|---|
| `dataflow/` | Núcleo (stdlib; openpyxl solo para xlsx): ingesta, transformaciones, pipeline, exportadores, informe, runner |
| `pipelines/sales_consolidation.json` | Pipeline de ejemplo (consolidación de ventas) |
| `demo/generate_demo_data.py` | Datos ficticios deterministas con defectos sembrados |
| `package/build_package.py` | Genera `manifest.yaml` + `package.tar.gz` (reproducible) conforme al contrato de paquete |
| `client/kispat_client.py` | Cliente API del control (publish / approve / deploy), solo stdlib |
| `tests/` | 20 pruebas (`python -m unittest discover -s tests`) |
| `INTEGRATION.md` | **Instrucciones para Claude Code: cómo conectarlo al repo principal** |
| `docs/PIPELINE-CONTRACT.md` | Contrato del pipeline y definición de "ejecución exitosa" |

## Uso local (sin control ni Podman)
```bash
python demo/generate_demo_data.py input
python -m dataflow.runner pipelines/sales_consolidation.json --input-dir input --output-dir output --run-id demo1
```
Genera `output/demo1/`: `consolidated.csv|xlsx`, `review.csv`, `rejected.csv`, `incidents.csv`, `summary.csv`, `summary.md`, `run_report.json`.

## Empaquetar para KISPAT Automation
```bash
python package/build_package.py --with-demo-data --vendor    # --vendor: openpyxl dentro del paquete
# -> dist/manifest.yaml, dist/package.tar.gz
export KISPAT_CONTROL_URL=http://192.168.1.50:8000 KISPAT_CLIENT_TOKEN=...   # token real, nunca en el repo
python client/kispat_client.py publish dist/manifest.yaml dist/package.tar.gz
python client/kispat_client.py approve <automation_id>
python client/kispat_client.py deploy  <automation_id> <node_id>
```
`--vendor` es necesario: el contenedor corre con `--network=none` y no puede hacer `pip install`.

## Verificado en esta sesión (en un contenedor de pruebas, no en el servidor)
- 20/20 pruebas pasan: contabilidad `recibidos = aceptados + revisión + rechazados + filtrados`, sin correcciones silenciosas, sin valores de celdas en incidencias, determinismo byte a byte, reintento idempotente, salida atómica (sin parciales ante fallo), neutralización de fórmulas, bloqueo de symlink/traversal, originales intactos, paquete reproducible.
- El paquete se ejecuta con `python -S` (solo stdlib + `vendor/`), imitando el contenedor sin red.
- **No verificado:** la integración real con el control, el agente y Podman. Ver `INTEGRATION.md`.

## Límites conocidos (MVP)
- Fechas: se prueban solo los formatos declarados, en orden. `dd/mm` vs `mm/dd` es ambiguo: declara explícitamente el correcto por cliente.
- Decimales: formato `1234.50` (punto). Sin separador de miles ni coma decimal.
- xlsx: primera hoja o `inputs.sheet`; un encabezado en la fila 1; las fórmulas se leen con su valor en caché (si nunca se calcularon llegan vacías).
- Neutralización: cadenas que empiezan por `= + - @` reciben prefijo `'` (un teléfono `+57...` también). Es deliberado.
- Pipelines en JSON (sin dependencias). YAML queda como mejora futura.
