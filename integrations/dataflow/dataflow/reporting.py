"""Métricas, informe de incidencias y resumen ejecutivo. Sin valores de celdas."""
from __future__ import annotations

from collections import Counter


def metrics(state) -> dict:
    return {"received": state.received, "accepted": len(state.rows), "review": len(state.review),
            "rejected": len(state.rejected), "filtered": state.filtered}


def summary_markdown(report: dict, state) -> str:
    m = report["metrics"]
    codes = Counter(i["code"] for i in state.incidents)
    lines = [
        f"# Resumen de ejecución — {report['pipeline']['name']} v{report['pipeline']['version']}", "",
        f"- Ejecución: `{report['run_id']}`", f"- Estado: **{report['status']}**",
        f"- Requiere revisión humana: {'sí' if m['review'] else 'no'}", "",
        "| Métrica | Registros |", "|---|---|",
        f"| Recibidos | {m['received']} |", f"| Aceptados | {m['accepted']} |",
        f"| En revisión | {m['review']} |", f"| Rechazados | {m['rejected']} |",
        f"| Filtrados (regla explícita) | {m['filtered']} |", "",
        "## Incidencias por tipo", "",
    ]
    lines += [f"- `{c}`: {n}" for c, n in sorted(codes.items())] or ["- Ninguna"]
    lines += ["", "## Archivos de entrada", ""]
    lines += [f"- `{s['file']}` — {s['status']}, {s.get('rows', 0)} filas" for s in report["inputs"]]
    lines += ["", "El detalle por fila está en `incidents.csv`; los registros no aceptados en `review.csv` y `rejected.csv`."]
    return "\n".join(lines) + "\n"
