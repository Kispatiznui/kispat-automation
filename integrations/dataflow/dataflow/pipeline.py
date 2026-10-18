"""Carga y validación del pipeline declarativo (JSON) y ejecución de sus pasos."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from . import CONTRACT_VERSION
from .errors import ConfigError, DataError, InternalError
from .ingestion import read_file, resolve_inputs
from .transforms import OPS, State, validate_step

TOP = {"contract_version", "name", "version", "description", "inputs", "steps", "outputs", "policy"}
IN_KEYS = {"files", "on_file_error", "max_file_mb", "max_rows", "delimiter", "sheet"}


def load_pipeline(path: Path) -> dict:
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError("PIPELINE_UNREADABLE", f"no se pudo leer el pipeline ({type(e).__name__})") from None
    return validate_pipeline(cfg)


def validate_pipeline(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ConfigError("BAD_PIPELINE", "el pipeline debe ser un objeto JSON")
    extra = set(cfg) - TOP
    if extra:
        raise ConfigError("UNKNOWN_KEY", f"claves no permitidas: {sorted(extra)}")
    if cfg.get("contract_version") != CONTRACT_VERSION:
        raise ConfigError("CONTRACT_VERSION", f"contract_version debe ser '{CONTRACT_VERSION}'")
    for k in ("name", "version"):
        if not isinstance(cfg.get(k), str) or not cfg[k]:
            raise ConfigError("MISSING_KEY", f"falta '{k}'")
    inp = cfg.get("inputs")
    if not isinstance(inp, dict) or set(inp) - IN_KEYS:
        raise ConfigError("BAD_INPUTS", f"inputs inválido; claves admitidas {sorted(IN_KEYS)}")
    if not (isinstance(inp.get("files"), list) and inp["files"] and all(isinstance(f, str) for f in inp["files"])):
        raise ConfigError("BAD_INPUTS", "inputs.files debe ser lista no vacía de patrones")
    if inp.get("on_file_error", "fail") not in ("fail", "skip"):
        raise ConfigError("BAD_INPUTS", "on_file_error debe ser fail|skip")
    mb = inp.get("max_file_mb", 20)
    if not (isinstance(mb, int) and 1 <= mb <= 100):
        raise ConfigError("BAD_INPUTS", "max_file_mb debe estar entre 1 y 100")
    mr = inp.get("max_rows", 200_000)
    if not (isinstance(mr, int) and 1 <= mr <= 1_000_000):
        raise ConfigError("BAD_INPUTS", "max_rows debe estar entre 1 y 1000000")
    steps = cfg.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ConfigError("BAD_STEPS", "steps debe ser una lista no vacía")
    cfg["_steps"] = [validate_step(s, i) for i, s in enumerate(steps, 1)]
    out = cfg.get("outputs", {})
    if set(out) - {"formats", "summary"}:
        raise ConfigError("BAD_OUTPUTS", "outputs admite formats y summary")
    fmts = out.get("formats", ["csv"])
    if not (isinstance(fmts, list) and fmts and set(fmts) <= {"csv", "xlsx"}):
        raise ConfigError("BAD_OUTPUTS", "formats admite csv|xlsx")
    s = out.get("summary")
    if s is not None and (not isinstance(s, dict) or set(s) - {"group_by", "sum"}
                          or not isinstance(s.get("group_by"), list) or not s["group_by"]
                          or not isinstance(s.get("sum", []), list)):
        raise ConfigError("BAD_OUTPUTS", "summary requiere group_by (lista) y opcional sum (lista)")
    ratio = cfg.get("policy", {}).get("max_rejected_ratio", 0.5)
    if set(cfg.get("policy", {})) - {"max_rejected_ratio"} or not (isinstance(ratio, (int, float)) and 0 <= ratio <= 1):
        raise ConfigError("BAD_POLICY", "policy admite max_rejected_ratio entre 0 y 1")
    return cfg


def run_pipeline(cfg: dict, input_dir: Path):
    inp = cfg["inputs"]
    root, files = resolve_inputs(input_dir, inp["files"])
    state, stats = State(), []
    seen_cols: list[str] = []
    for f in files:
        try:
            cols, rows, stat = read_file(
                f, root, max_bytes=inp.get("max_file_mb", 20) * 1024 * 1024,
                max_rows=inp.get("max_rows", 200_000), delimiter=inp.get("delimiter", ","),
                sheet=inp.get("sheet"))
        except DataError as e:
            if inp.get("on_file_error", "fail") == "fail":
                raise
            stats.append({"file": f.relative_to(root).as_posix(), "status": "skipped", "error_code": e.code})
            state.incident("error", "FILE_SKIPPED", f"archivo omitido: {e.code}")
            continue
        stats.append(stat)
        for c in cols:
            if c not in seen_cols:
                seen_cols.append(c)
        state.rows.extend(rows)
        state.received += len(rows)
    if not any(s["status"] == "read" for s in stats):
        raise DataError("NO_READABLE_FILES", "ningún archivo pudo leerse")
    state.columns = seen_cols
    for r in state.rows:
        for c in seen_cols:
            r.setdefault(c, "")
    for name, params in cfg["_steps"]:
        OPS[name]["fn"](state, **params)
    if not state.invariant_ok():
        raise InternalError("INVARIANT", "recibidos != aceptados+revisión+rechazados+filtrados")
    return state, stats


def build_summary(cfg: dict, state: State):
    s = cfg.get("outputs", {}).get("summary")
    if not s:
        return None
    gb, sums = s["group_by"], s.get("sum", [])
    for c in gb + sums:
        if c not in state.columns:
            raise ConfigError("BAD_SUMMARY", f"columna '{c}' no existe tras los pasos")
    acc: dict = {}
    for r in state.rows:
        k = tuple(str(r.get(c)) for c in gb)
        g = acc.setdefault(k, {"pedidos": 0, **{c: Decimal(0) for c in sums}})
        g["pedidos"] += 1
        for c in sums:
            v = r.get(c)
            if v in ("", None):
                continue
            if not isinstance(v, (int, Decimal)):
                raise ConfigError("BAD_SUMMARY", f"'{c}' debe ser int o decimal (usa cast_types antes)")
            g[c] += Decimal(v)
    cols = gb + ["pedidos"] + sums
    return cols, [dict(zip(gb, k)) | g for k, g in sorted(acc.items())]
