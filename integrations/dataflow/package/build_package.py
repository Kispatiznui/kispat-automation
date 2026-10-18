"""Construye el paquete KISPAT (manifest.yaml + package.tar.gz) listo para POST /api/v1/automations.

Valida localmente las mismas reglas estrictas del contrato de paquete de KISPAT Automation
(Contrato_paquete.md). El control volverá a validar: esto solo falla rápido.
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OPENPYXL = "openpyxl==3.1.5"
RUN_PY = '''import pathlib, sys
root = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(root))
if (root / "vendor").is_dir():
    sys.path.insert(0, str(root / "vendor"))
from dataflow.runner import main
sys.exit(main(sys.argv[1:], default_base=root))
'''
SEMVER = __import__("re").compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")


def validate_manifest(m: dict):
    errs = []
    sp = m["spec"]
    ep = sp["entrypoint"]
    if ".." in Path(ep).parts or Path(ep).is_absolute() or "\\" in ep or ":" in ep:
        errs.append("entrypoint con '..' o ruta absoluta")
    if any("=" in s for s in sp["permissions"]["secrets"]):
        errs.append("secrets parecen valores")
    lim = sp["limits"]
    if not 32 <= lim["memory_mb"] <= 4096:
        errs.append("memory_mb fuera de 32..4096")
    if lim["cpu"] > 4:
        errs.append("cpu > 4")
    if not sp["triggers"]:
        errs.append("triggers vacío")
    if not SEMVER.match(m["metadata"]["version"]):
        errs.append("version no es SemVer")
    if errs:
        sys.exit("Manifiesto inválido: " + "; ".join(errs))


def manifest_yaml(m: dict) -> str:
    j = lambda v: json.dumps(v, ensure_ascii=False)  # JSON es subconjunto de YAML  # noqa: E731
    md, sp = m["metadata"], m["spec"]
    pe, lim = sp["permissions"], sp["limits"]
    return f"""apiVersion: kispat/v1
kind: Automation
metadata:
  name: {j(md['name'])}
  version: {j(md['version'])}
  author: {j(md['author'])}
  description: {j(md['description'])}
  license: {j(md['license'])}
spec:
  runtime: python3.12
  entrypoint: {j(sp['entrypoint'])}
  args: {j(sp['args'])}
  env: {j(sp['env'])}
  permissions:
    filesystem:
      read: {j(pe['filesystem']['read'])}
      write: {j(pe['filesystem']['write'])}
    network: false
    secrets: {j(pe['secrets'])}
  limits:
    cpu: {lim['cpu']}
    memory_mb: {lim['memory_mb']}
    pids: {lim['pids']}
    timeout_s: {lim['timeout_s']}
  triggers:
    - type: manual
  dependencies: {j(sp['dependencies'])}
"""


def _vendor(target: Path):
    """openpyxl dentro del paquete (el contenedor no tiene red). pip si hay red; si no, copia local."""
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(target), OPENPYXL],
                       capture_output=True)
    if r.returncode == 0:
        return
    import et_xmlfile  # noqa: PLC0415
    import openpyxl  # noqa: PLC0415
    if openpyxl.__version__ != OPENPYXL.split("==")[1]:
        sys.exit(f"pip falló y la copia local de openpyxl es {openpyxl.__version__}, se esperaba {OPENPYXL}")
    print("AVISO: pip sin red; vendorizando la copia local de openpyxl/et_xmlfile")
    for mod in (openpyxl, et_xmlfile):
        src = Path(mod.__file__).parent
        shutil.copytree(src, target / src.name, ignore=shutil.ignore_patterns("__pycache__"))


def build(pipeline: Path, dist: Path, with_demo: bool, vendor: bool) -> tuple[Path, Path]:
    cfg = json.loads(pipeline.read_text(encoding="utf-8"))
    m = {
        "metadata": {"name": f"dataflow-{cfg['name']}", "version": cfg["version"], "author": "kispat",
                     "description": cfg.get("description", "Automatización DataFlow"), "license": "Proprietary"},
        "spec": {"entrypoint": "run.py", "args": [f"pipelines/{pipeline.name}"], "env": {},
                 "permissions": {"filesystem": {"read": ["input"], "write": ["output"]}, "network": False,
                                 "secrets": []},
                 "limits": {"cpu": 1.0, "memory_mb": 512, "pids": 128, "timeout_s": 300},
                 "triggers": [{"type": "manual"}],
                 "dependencies": [OPENPYXL] if vendor else []},
    }
    validate_manifest(m)
    dist.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "pkg"
        shutil.copytree(ROOT / "dataflow", stage / "dataflow", ignore=shutil.ignore_patterns("__pycache__"))
        (stage / "pipelines").mkdir()
        shutil.copy(pipeline, stage / "pipelines" / pipeline.name)
        (stage / "run.py").write_text(RUN_PY, encoding="utf-8")
        if with_demo:
            sys.path.insert(0, str(ROOT / "demo"))
            from generate_demo_data import generate  # noqa: PLC0415
            generate(stage / "input")
        else:
            (stage / "input").mkdir()
        if vendor:
            _vendor(stage / "vendor")
        pkg = dist / "package.tar.gz"
        files = sorted(p for p in stage.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
        # gzip con mtime=0 y sin nombre: la cabecera gzip no debe llevar la hora actual
        with open(pkg, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz, \
                tarfile.open(fileobj=gz, mode="w") as tar:  # orden y metadatos fijos -> paquete reproducible
            for p in files:
                ti = tar.gettarinfo(str(p), arcname=p.relative_to(stage).as_posix())
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = ""
                ti.mtime = 0
                ti.mode = 0o644
                with open(p, "rb") as f:
                    tar.addfile(ti, f)
    man = dist / "manifest.yaml"
    man.write_text(manifest_yaml(m), encoding="utf-8")
    return man, pkg


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", default=str(ROOT / "pipelines" / "sales_consolidation.json"))
    ap.add_argument("--dist", default=str(ROOT / "dist"))
    ap.add_argument("--with-demo-data", action="store_true")
    ap.add_argument("--vendor", action="store_true", help="vendoriza openpyxl (requiere red en ESTA máquina)")
    a = ap.parse_args()
    man, pkg = build(Path(a.pipeline), Path(a.dist), a.with_demo_data, a.vendor)
    print(f"manifest: {man}\npackage:  {pkg}")
