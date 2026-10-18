"""Punto de entrada. Contrato de salida:

exit 0 = ejecución exitosa (invariante OK, política de rechazo respetada, artefactos escritos y hasheados).
exit 3 = entrada inválida (definitivo)   exit 4 = pipeline inválido (definitivo)
exit 5 = política violada (demasiados rechazados)   exit 1 = fallo inesperado (posible reintento)
Última línea de stdout: 'KISPAT_RESULT {json}' con métricas y artefactos.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import CONTRACT_VERSION, __version__
from .errors import ConfigError, DataflowError
from .exporters import write_csv, write_dataset_csv, write_xlsx
from .pipeline import build_summary, load_pipeline, run_pipeline
from .reporting import metrics, summary_markdown

EXIT_POLICY = 5


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def run(pipeline_path: Path, input_dir: Path, output_dir: Path, run_id: str):
    t0, started = time.monotonic(), _now()
    final, partial, failed = (output_dir / run_id, output_dir / f"{run_id}.partial",
                              output_dir / f"{run_id}.failed")
    # Idempotencia: un reintento con el mismo run_id no duplica efectos.
    if (final / "run_report.json").is_file():
        rep = json.loads((final / "run_report.json").read_text(encoding="utf-8"))
        if rep.get("status") == "succeeded":
            rep["idempotent_replay"] = True
            return 0, rep
    report = {"run_id": run_id, "dataflow_version": __version__, "contract_version": CONTRACT_VERSION,
              "started_at": started, "status": "failed", "pipeline": {}, "inputs": [], "metrics": {},
              "artifacts": [], "error": None}
    state = None
    try:
        cfg = load_pipeline(pipeline_path)
        report["pipeline"] = {"name": cfg["name"], "version": cfg["version"],
                              "automation_id": os.environ.get("KISPAT_AUTOMATION_ID"),
                              "image": os.environ.get("KISPAT_IMAGE")}
        state, stats = run_pipeline(cfg, input_dir)
        report["inputs"], report["metrics"] = stats, metrics(state)
        if state.received:
            ratio = len(state.rejected) / state.received
            if ratio > cfg.get("policy", {}).get("max_rejected_ratio", 0.5):
                report["error"] = {"code": "REJECT_RATIO", "message": f"rechazados {ratio:.0%} supera la política"}
                return EXIT_POLICY, _fail(report, failed, partial, t0)
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        arts = []
        cols = state.columns
        arts.append(write_dataset_csv(partial / "consolidated.csv", cols, state.rows))
        arts.append(write_dataset_csv(partial / "review.csv", cols, state.review))
        arts.append(write_dataset_csv(partial / "rejected.csv", cols, state.rejected))
        inc_cols = ["severity", "code", "message", "file", "row", "field"]
        arts.append(write_csv(partial / "incidents.csv", inc_cols,
                              [[i[c] for c in inc_cols] for i in state.incidents]))
        summ = build_summary(cfg, state)
        if summ:
            arts.append(write_csv(partial / "summary.csv", summ[0], [[r[c] for c in summ[0]] for r in summ[1]]))
        if "xlsx" in cfg.get("outputs", {}).get("formats", ["csv"]):
            sheets = {"consolidado": (cols, [[r.get(c, "") for c in cols] for r in state.rows])}
            if summ:
                sheets["resumen"] = (summ[0], [[r[c] for c in summ[0]] for r in summ[1]])
            arts.append(write_xlsx(partial / "consolidated.xlsx", sheets))
        report.update(status="succeeded", artifacts=arts, finished_at=_now(),
                      duration_ms=int((time.monotonic() - t0) * 1000))
        (partial / "summary.md").write_text(summary_markdown(report, state), encoding="utf-8")
        _write_json(partial / "run_report.json", report)
        os.rename(partial, final)  # publicación atómica: o está todo o no está nada
        return 0, report
    except DataflowError as e:
        report["error"] = {"code": e.code, "message": e.message}
        return e.exit_code, _fail(report, failed, partial, t0)
    except Exception as e:  # noqa: BLE001 - fallo inesperado; solo el tipo, nunca datos
        report["error"] = {"code": "UNEXPECTED", "message": type(e).__name__}
        return 1, _fail(report, failed, partial, t0)


def _fail(report, failed: Path, partial: Path, t0):
    shutil.rmtree(partial, ignore_errors=True)  # nada de salidas parciales
    report.update(status="failed", finished_at=_now(), duration_ms=int((time.monotonic() - t0) * 1000))
    shutil.rmtree(failed, ignore_errors=True)
    failed.mkdir(parents=True, exist_ok=True)
    _write_json(failed / "run_report.json", report)
    return report


def main(argv=None, default_base: Path | None = None) -> int:
    base = default_base or Path.cwd()
    ap = argparse.ArgumentParser(prog="kispat-dataflow")
    ap.add_argument("pipeline")
    ap.add_argument("--input-dir", default=str(base / "input"))
    ap.add_argument("--output-dir", default=str(base / "output"))
    ap.add_argument("--run-id", default=os.environ.get("KISPAT_JOB_ID") or uuid.uuid4().hex)
    a = ap.parse_args(argv)
    if not a.run_id.replace("-", "").isalnum():
        print("KISPAT_ERROR BAD_RUN_ID", file=sys.stderr)
        return 4
    pipeline = Path(a.pipeline)
    if not pipeline.is_absolute():
        pipeline = base / pipeline
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    code, rep = run(pipeline, Path(a.input_dir), out, a.run_id)
    if code:
        print(f"KISPAT_ERROR {rep['error']['code']}", file=sys.stderr)
    compact = {"run_id": rep["run_id"], "status": rep["status"], "metrics": rep["metrics"],
               "artifacts": [{k: x[k] for k in ("name", "sha256", "bytes")} for x in rep["artifacts"]],
               "error": rep["error"]}
    print("KISPAT_RESULT " + json.dumps(compact, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
