"""Registro CERRADO de operaciones. Sin eval/exec: el pipeline solo nombra operaciones de aquí.

Regla: ninguna operación corrige o elimina datos en silencio. Todo movimiento de filas
(review/rejected/filtered) genera un incidente estructurado sin volcar valores (PII).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .errors import ConfigError, DataError
from .ingestion import ROW, SRC

REASON = "__reason"


class State:
    def __init__(self):
        self.columns: list[str] = []
        self.rows: list[dict] = []
        self.review: list[dict] = []
        self.rejected: list[dict] = []
        self.filtered = 0
        self.received = 0
        self.incidents: list[dict] = []

    def incident(self, severity, code, message, row=None, field=None):
        self.incidents.append({
            "severity": severity, "code": code, "message": message,
            "file": row.get(SRC) if row else None, "row": row.get(ROW) if row else None,
            "field": field,
        })

    def move(self, row, bucket, code, message, field=None):
        row[REASON] = f"{row[REASON]};{code}" if row.get(REASON) else code
        (self.review if bucket == "review" else self.rejected).append(row)
        self.incident("warning" if bucket == "review" else "error", code, message, row, field)

    def invariant_ok(self) -> bool:
        return self.received == len(self.rows) + len(self.review) + len(self.rejected) + self.filtered


OPS: dict = {}


def op(name, required=(), optional=()):
    def deco(fn):
        OPS[name] = {"fn": fn, "required": set(required), "allowed": set(required) | set(optional)}
        return fn
    return deco


def validate_step(step: dict, idx: int):
    if not isinstance(step, dict) or "op" not in step:
        raise ConfigError("BAD_STEP", f"paso {idx}: debe ser un objeto con 'op'")
    name = step["op"]
    if name not in OPS:
        raise ConfigError("UNKNOWN_OP", f"paso {idx}: operación '{name}' no existe en el registro")
    params = {k: v for k, v in step.items() if k != "op"}
    spec = OPS[name]
    extra = set(params) - spec["allowed"]
    if extra:
        raise ConfigError("UNKNOWN_PARAM", f"paso {idx} ({name}): parámetros no permitidos {sorted(extra)}")
    missing = spec["required"] - set(params)
    if missing:
        raise ConfigError("MISSING_PARAM", f"paso {idx} ({name}): faltan {sorted(missing)}")
    for key in ("columns", "fields", "keys", "formats"):
        if key in params and not (isinstance(params[key], list) and params[key]
                                  and all(isinstance(x, str) for x in params[key])):
            raise ConfigError("BAD_PARAM", f"paso {idx} ({name}): '{key}' debe ser lista no vacía de textos")
    for key in ("on_error", "on_missing", "on_duplicate"):
        if key in params and params[key] not in ("review", "reject"):
            raise ConfigError("BAD_PARAM", f"paso {idx} ({name}): '{key}' debe ser review|reject")
    if name == "cast_types":
        t = params["types"]
        if not isinstance(t, dict) or not t or any(v not in ("int", "decimal", "str") for v in t.values()):
            raise ConfigError("BAD_PARAM", f"paso {idx}: types admite int|decimal|str")
    if name == "filter_rows" and params["cmp"] not in _CMP:
        raise ConfigError("BAD_PARAM", f"paso {idx}: cmp de filtro debe ser {sorted(_CMP)}")
    return name, params


def _norm_header(h: str) -> str:
    s = unicodedata.normalize("NFKD", h).encode("ascii", "ignore").decode()
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", s.lower())).strip("_")


@op("normalize_headers")
def normalize_headers(state: State):
    mapping = {c: _norm_header(c) for c in state.columns}
    if "" in mapping.values() or len(set(mapping.values())) != len(mapping):
        raise DataError("HEADER_COLLISION", "la normalización de encabezados produce nombres vacíos o repetidos")
    state.columns = [mapping[c] for c in state.columns]
    for bucket in (state.rows,):
        for r in bucket:
            for old, new in mapping.items():
                if old != new:
                    r[new] = r.pop(old)


@op("trim_text", optional=("columns",))
def trim_text(state: State, columns=None):
    cols = columns or state.columns
    for r in state.rows:
        for c in cols:
            if isinstance(r.get(c), str):
                r[c] = re.sub(r"\s+", " ", r[c]).strip()


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


@op("require_fields", required=("fields",), optional=("on_missing",))
def require_fields(state: State, fields, on_missing="review"):
    keep = []
    for r in state.rows:
        bad = [f for f in fields if _blank(r.get(f))]
        if bad:
            state.move(r, on_missing, "MISSING_FIELD", f"campos obligatorios vacíos: {','.join(bad)}", bad[0])
        else:
            keep.append(r)
    state.rows = keep


@op("normalize_dates", required=("columns", "formats"), optional=("output", "on_error"))
def normalize_dates(state: State, columns, formats, output="%Y-%m-%d", on_error="review"):
    keep = []
    for r in state.rows:
        failed = None
        for c in columns:
            v = r.get(c)
            if _blank(v):
                continue
            if isinstance(v, (datetime, date)):
                r[c] = v.strftime(output)
                continue
            for fmt in formats:
                try:
                    r[c] = datetime.strptime(str(v).strip(), fmt).strftime(output)
                    break
                except ValueError:
                    continue
            else:
                failed = c
                break
        if failed:
            state.move(r, on_error, "BAD_DATE", "fecha no coincide con ningún formato declarado", failed)
        else:
            keep.append(r)
    state.rows = keep


def _to_decimal(v):
    if isinstance(v, bool):
        raise ValueError
    if isinstance(v, (int, float)):
        v = repr(v)
    d = Decimal(str(v).strip())
    if not d.is_finite():
        raise ValueError
    return d


@op("cast_types", required=("types",), optional=("on_error",))
def cast_types(state: State, types, on_error="reject"):
    keep = []
    for r in state.rows:
        failed = None
        for c, t in types.items():
            v = r.get(c)
            if _blank(v):
                continue
            try:
                if t == "int":
                    if isinstance(v, bool) or (isinstance(v, float) and not v.is_integer()):
                        raise ValueError
                    r[c] = int(v) if isinstance(v, (int, float)) else int(str(v).strip())
                elif t == "decimal":
                    r[c] = _to_decimal(v)
                else:
                    r[c] = str(v)
            except (ValueError, InvalidOperation):
                failed = c
                break
        if failed:
            state.move(r, on_error, "BAD_TYPE", f"valor no convertible a {types[failed]}", failed)
        else:
            keep.append(r)
    state.rows = keep


@op("dedupe", required=("keys",), optional=("on_duplicate",))
def dedupe(state: State, keys, on_duplicate="review"):
    seen, keep = set(), []
    for r in state.rows:
        k = tuple(str(r.get(c)) for c in keys)
        if k in seen:
            state.move(r, on_duplicate, "DUPLICATE", f"duplicado por clave {','.join(keys)}", keys[0])
        else:
            seen.add(k)
            keep.append(r)
    state.rows = keep


_CMP = {"eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte"}


@op("filter_rows", required=("field", "cmp", "value"))
def filter_rows(state: State, field, cmp, value):
    keep, removed = [], 0
    for r in state.rows:
        v = r.get(field)
        if cmp in ("eq", "ne", "in", "not_in"):
            sv = str(v)
            hit = {"eq": sv == str(value), "ne": sv != str(value),
                   "in": sv in [str(x) for x in value] if isinstance(value, list) else False,
                   "not_in": sv not in [str(x) for x in value] if isinstance(value, list) else False}[cmp]
        else:
            try:
                a, b = _to_decimal(v), _to_decimal(value)
            except (ValueError, InvalidOperation):
                state.move(r, "review", "FILTER_NON_NUMERIC", "valor no numérico en filtro numérico", field)
                continue
            hit = {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[cmp]
        if hit:
            keep.append(r)
        else:
            removed += 1
    state.rows = keep
    state.filtered += removed
    if removed:
        state.incident("info", "ROWS_FILTERED", f"{removed} filas excluidas por filtro explícito", field=field)
