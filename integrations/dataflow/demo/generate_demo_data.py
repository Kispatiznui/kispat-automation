"""Datos ficticios deterministas con defectos sembrados. Sin datos reales."""
from __future__ import annotations

import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

HEADER = ["Fecha Venta", "ID Pedido", "Cliente", "Región", "Producto", "Cantidad", "Total"]
PRODUCTS = [("Teclado", 45.5), ("Monitor", 210.0), ("Mouse", 18.9), ("Cable HDMI", 7.25)]
CLIENTS = ["Acme SAS", "Delta Ltda", "Orion SA", "Nova Corp", "Pixel SAS", "Andes Trading"]


def _rows(rng, region, start_id, n, fmt):
    out = []
    for i in range(n):
        prod, price = rng.choice(PRODUCTS)
        qty = rng.randint(1, 8)
        d = date(2026, 9, 1) + timedelta(days=rng.randint(0, 29))
        out.append([d.strftime(fmt), f"P-{start_id + i:04d}", rng.choice(CLIENTS), region, prod, qty,
                    f"{qty * price:.2f}"])
    return out


def _freeze_zip(path: Path):
    """Reescribe el xlsx con fechas de entrada fijas para que el paquete sea reproducible byte a byte."""
    import zipfile
    with zipfile.ZipFile(path) as z:
        items = [(i.filename, z.read(i)) for i in z.infolist()]
    import re
    items = [(n, re.sub(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*", rb"\g<1>2026-01-01T00:00:00Z", d)
              if n == "docProps/core.xml" else d) for n, d in items]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in items:
            zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)


def generate(out_dir: Path) -> list[str]:
    rng = random.Random(42)
    out_dir.mkdir(parents=True, exist_ok=True)
    norte = _rows(rng, "Norte", 1, 20, "%Y-%m-%d")
    sur = _rows(rng, "Sur", 101, 20, "%d/%m/%Y")
    centro = _rows(rng, "Centro", 201, 15, "%Y-%m-%d")
    # Defectos sembrados (cada uno debe terminar en un bucket explícito, nunca corregido en silencio):
    norte[3][2] = ""                        # cliente vacío        -> review
    norte[5][0] = "31/02/2026"              # fecha imposible      -> review
    norte[7][6] = "abc"                     # total no numérico    -> rechazado
    norte[9][1] = ""                        # id vacío             -> rechazado
    norte[11][2] = "=HYPERLINK(\"http://x\",\"clic\")"  # fórmula en texto -> se neutraliza al exportar
    sur[2][1] = norte[1][1]                 # id repetido entre archivos -> duplicado -> review
    sur[4][2] = "  Orion   SA  "            # espacios             -> trim_text normaliza
    sur.append(list(sur[6]))                # fila duplicada exacta -> duplicado -> review
    with open(out_dir / "ventas_norte.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(norte)
    with open(out_dir / "ventas_sur.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(sur)
    import openpyxl  # solo para fabricar el xlsx de la demo
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADER)
    for r in centro:
        d = datetime.strptime(r[0], "%Y-%m-%d")
        ws.append([d, r[1], r[2], r[3], r[4], r[5], float(r[6])])
    fixed = datetime(2026, 1, 1)
    wb.properties.created = wb.properties.modified = fixed
    path = out_dir / "ventas_centro.xlsx"
    wb.save(path)
    _freeze_zip(path)
    return ["ventas_norte.csv", "ventas_sur.csv", "ventas_centro.xlsx"]


if __name__ == "__main__":
    import sys
    print(generate(Path(sys.argv[1] if len(sys.argv) > 1 else "input")))
