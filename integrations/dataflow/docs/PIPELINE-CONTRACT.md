# Contrato de pipeline `dataflow/v1`

Estado: implementado en `dataflow/pipeline.py` y `dataflow/transforms.py` (verificado por `tests/`).

## Estructura
```json
{
  "contract_version": "dataflow/v1",
  "name": "...", "version": "1.0.0", "description": "...",
  "inputs":  {"files": ["ventas_*.csv"], "on_file_error": "fail|skip", "max_file_mb": 20, "max_rows": 200000,
              "delimiter": ",", "sheet": null},
  "steps":   [{"op": "<operación>", "...params"}],
  "outputs": {"formats": ["csv", "xlsx"], "summary": {"group_by": ["region"], "sum": ["total"]}},
  "policy":  {"max_rejected_ratio": 0.5}
}
```
Claves desconocidas se rechazan (ConfigError). Patrones de entrada absolutos, con `..`, `\` o `:` se rechazan.

## Operaciones (registro cerrado; no hay eval)
| op | Parámetros | Política |
|---|---|---|
| `normalize_headers` | — | Colisión de nombres → error definitivo |
| `trim_text` | `columns?` | Solo espacios |
| `require_fields` | `fields`, `on_missing` review\|reject | Vacío → bucket explícito |
| `normalize_dates` | `columns`, `formats`, `output?`, `on_error` | Sin formato válido → bucket; nunca se "arregla" |
| `cast_types` | `types` int\|decimal\|str, `on_error` | Rechaza NaN/Infinity |
| `dedupe` | `keys`, `on_duplicate` | Conserva la primera; el resto a bucket |
| `filter_rows` | `field`, `cmp` eq\|ne\|in\|not_in\|gt\|gte\|lt\|lte, `value` | Único borrado permitido; cuenta como `filtered` |

## Definición de "ejecución exitosa"
Exit 0 solo si: (1) todos los archivos requeridos se leyeron, (2) `recibidos = aceptados + revisión + rechazados + filtrados`, (3) `rechazados/recibidos <= policy.max_rejected_ratio`, (4) todos los artefactos se escribieron, hashearon (sha256) y se publicaron con renombrado atómico. Registros en revisión **no** hacen fallar la ejecución: `run_report.json` y `summary.md` indican que requiere revisión humana.

## Códigos de salida
| Código | Significado | Reintento |
|---|---|---|
| 0 | Éxito | — |
| 1 | Fallo inesperado / invariante | Sí (acotado) |
| 3 | Entrada inválida | No |
| 4 | Pipeline o run_id inválido | No |
| 5 | Política de rechazo violada | No |

La última línea de stdout es `KISPAT_RESULT {json}` (métricas y artefactos). Ningún mensaje contiene valores de celdas.
