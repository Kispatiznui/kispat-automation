"""Lectura segura de CSV y xlsx. Nunca registra valores de celdas."""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from .errors import ConfigError, DataError

SRC, ROW = "__src", "__row"


def resolve_inputs(input_dir: Path, patterns: list[str], max_files: int = 200):
    root = Path(input_dir).resolve()
    if not root.is_dir():
        raise DataError("INPUT_DIR_MISSING", "el directorio de entrada no existe")
    found: set[Path] = set()
    for pat in patterns:
        pp = Path(pat)
        if pat.startswith(("/", "\\")) or pp.is_absolute() or ".." in pp.parts or "\\" in pat or ":" in pat:
            raise ConfigError("BAD_INPUT_PATTERN", "patrón de entrada no permitido")
        try:
            matches = list(root.glob(pat))
        except (NotImplementedError, ValueError):
            raise ConfigError("BAD_INPUT_PATTERN", "patrón de entrada no permitido") from None
        for p in matches:
            rp = p.resolve()
            if not rp.is_relative_to(root):
                raise DataError("PATH_ESCAPE", "un archivo de entrada apunta fuera del directorio autorizado")
            if rp.is_file():
                found.add(rp)
    files = sorted(found, key=lambda p: p.relative_to(root).as_posix())
    if not files:
        raise DataError("NO_INPUT_FILES", "ningún archivo coincide con los patrones")
    if len(files) > max_files:
        raise DataError("TOO_MANY_FILES", f"más de {max_files} archivos")
    return root, files


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_header(header: list) -> list[str]:
    cols = ["" if h is None else str(h).strip() for h in header]
    while cols and cols[-1] == "":
        cols.pop()
    if not cols or any(c == "" for c in cols):
        raise DataError("BAD_HEADER", "encabezado vacío o con columnas sin nombre")
    if len({c.lower() for c in cols}) != len(cols):
        raise DataError("BAD_HEADER", "encabezados duplicados")
    return cols


def _iter_csv(path: Path, delimiter: str):
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            yield from csv.reader(f, delimiter=delimiter)
    except (csv.Error, UnicodeDecodeError) as e:
        raise DataError("READ_ERROR", f"CSV ilegible ({type(e).__name__})") from None


def _iter_xlsx(path: Path, sheet: str | None):
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError:
        raise DataError("XLSX_UNAVAILABLE", "openpyxl no está disponible en este entorno") from None
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb[sheet] if sheet else wb.worksheets[0]
            yield from ws.iter_rows(values_only=True)
        finally:
            wb.close()
    except DataError:
        raise
    except Exception as e:  # noqa: BLE001 - archivo malformado, sin filtrar contenido
        raise DataError("READ_ERROR", f"xlsx ilegible ({type(e).__name__})") from None


def read_file(path: Path, root: Path, *, max_bytes: int, max_rows: int, delimiter: str = ",",
              sheet: str | None = None):
    """Devuelve (columns, rows, stat). Cada fila lleva __src y __row (línea 1-based, encabezado=1)."""
    rel = path.relative_to(root).as_posix()
    size = path.stat().st_size
    if size > max_bytes:
        raise DataError("FILE_TOO_LARGE", f"{rel} excede el máximo permitido")
    ext = path.suffix.lower()
    if ext in (".csv", ".txt"):
        it = _iter_csv(path, delimiter)
    elif ext == ".xlsx":
        it = _iter_xlsx(path, sheet)
    else:
        raise DataError("UNSUPPORTED_FORMAT", f"{rel}: formato no soportado")
    columns: list[str] | None = None
    rows, blank = [], 0
    for n, raw in enumerate(it, start=1):
        if columns is None:
            columns = _check_header(list(raw))
            continue
        vals = list(raw)
        if all(v is None or (isinstance(v, str) and v.strip() == "") for v in vals):
            blank += 1
            continue
        if len(vals) > len(columns) and any(v not in (None, "") for v in vals[len(columns):]):
            raise DataError("MALFORMED_ROW", f"{rel}: fila {n} con más campos que el encabezado")
        vals = (vals + [""] * len(columns))[: len(columns)]
        row = {c: ("" if v is None else v) for c, v in zip(columns, vals)}
        row[SRC], row[ROW] = rel, n
        rows.append(row)
        if len(rows) > max_rows:
            raise DataError("TOO_MANY_ROWS", f"{rel} excede el máximo de filas")
    if columns is None:
        raise DataError("EMPTY_FILE", f"{rel} no tiene encabezado")
    stat = {"file": rel, "sha256": sha256_file(path), "bytes": size, "rows": len(rows),
            "blank_rows_skipped": blank, "status": "read"}
    return columns, rows, stat
