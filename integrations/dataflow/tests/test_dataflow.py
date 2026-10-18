import csv
import hashlib
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo"))

from dataflow.errors import ConfigError, DataError  # noqa: E402
from dataflow.exporters import neutralize  # noqa: E402
from dataflow.pipeline import run_pipeline, validate_pipeline  # noqa: E402
from dataflow.runner import run  # noqa: E402
from generate_demo_data import generate  # noqa: E402

PIPE = ROOT / "pipelines" / "sales_consolidation.json"


def cfg(**over):
    c = json.loads(PIPE.read_text())
    c.update(over)
    return c


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.inp, self.out = self.tmp / "input", self.tmp / "output"
        self.out.mkdir()

    def tearDown(self):
        self._t.cleanup()

    def write_csv(self, name, text):
        self.inp.mkdir(exist_ok=True)
        (self.inp / name).write_text(text, encoding="utf-8")


class TestPipelineConfig(unittest.TestCase):
    def test_unknown_op_rejected(self):
        c = cfg(steps=[{"op": "os_system", "cmd": "x"}])
        with self.assertRaises(ConfigError) as e:
            validate_pipeline(c)
        self.assertEqual(e.exception.code, "UNKNOWN_OP")

    def test_unknown_param_and_top_key_rejected(self):
        with self.assertRaises(ConfigError):
            validate_pipeline(cfg(steps=[{"op": "dedupe", "keys": ["a"], "eval": "1"}]))
        with self.assertRaises(ConfigError):
            validate_pipeline(cfg(extra="x"))

    def test_absolute_or_traversal_patterns_rejected(self):
        for pat in ("/etc/*", "../x.csv", "C:\\x.csv"):
            c = cfg(inputs={"files": [pat]})
            c = validate_pipeline(c)
            with self.assertRaises(ConfigError):
                run_pipeline(c, Path(tempfile.gettempdir()))


class TestEngine(Base):
    def test_demo_accounting_and_explicit_buckets(self):
        generate(self.inp)
        state, stats = run_pipeline(validate_pipeline(cfg()), self.inp)
        self.assertEqual(state.received, 56)
        self.assertTrue(state.invariant_ok())
        self.assertEqual((len(state.rows), len(state.review), len(state.rejected)), (50, 4, 2))
        codes = {i["code"] for i in state.incidents}
        self.assertTrue({"BAD_DATE", "BAD_TYPE", "DUPLICATE", "MISSING_FIELD"} <= codes)

    def test_no_silent_fix_of_bad_date(self):
        self.write_csv("ventas_a.csv", "Fecha Venta,ID Pedido,Cliente,Total,Cantidad\n31/02/2026,P1,Acme,10,1\n")
        state, _ = run_pipeline(validate_pipeline(cfg()), self.inp)
        self.assertEqual(len(state.rows), 0)
        self.assertEqual(state.review[0]["__reason"], "BAD_DATE")

    def test_incidents_do_not_contain_cell_values(self):
        self.write_csv("ventas_a.csv", "Fecha Venta,ID Pedido,Cliente,Total,Cantidad\n2026-01-01,P1,SECRETO-PII,abc,1\n")
        state, _ = run_pipeline(validate_pipeline(cfg()), self.inp)
        self.assertNotIn("SECRETO-PII", json.dumps(state.incidents))
        self.assertNotIn("abc", json.dumps(state.incidents))

    def test_malformed_files_fail_with_data_error(self):
        self.write_csv("ventas_a.csv", "a,a\n1,2\n")  # encabezado duplicado
        with self.assertRaises(DataError):
            run_pipeline(validate_pipeline(cfg()), self.inp)
        self.write_csv("ventas_a.csv", "a,b\n1,2,3\n")  # fila con más campos
        with self.assertRaises(DataError):
            run_pipeline(validate_pipeline(cfg()), self.inp)

    def test_file_too_large(self):
        self.write_csv("ventas_a.csv", "a,b\n" + "1,2\n" * 10)
        c = cfg()
        c["inputs"]["max_rows"] = 3
        with self.assertRaises(DataError):
            run_pipeline(validate_pipeline(c), self.inp)

    def test_symlink_escape_blocked(self):
        self.inp.mkdir()
        outside = self.tmp / "secret.csv"
        outside.write_text("a,b\n1,2\n")
        os.symlink(outside, self.inp / "ventas_x.csv")
        with self.assertRaises(DataError) as e:
            run_pipeline(validate_pipeline(cfg()), self.inp)
        self.assertEqual(e.exception.code, "PATH_ESCAPE")


class TestExport(unittest.TestCase):
    def test_neutralize(self):
        for bad in ("=1+1", "+cmd", "-x", "@SUM(A1)", "\tx"):
            self.assertTrue(neutralize(bad).startswith("'"))
        for ok in ("hola", "-5", "3.14", 7, ""):
            self.assertEqual(neutralize(ok), ok)


class TestRunner(Base):
    def test_success_artifacts_and_formula_neutralized(self):
        generate(self.inp)
        code, rep = run(PIPE, self.inp, self.out, "run1")
        self.assertEqual(code, 0)
        self.assertTrue((self.out / "run1" / "consolidated.csv").is_file())
        self.assertFalse((self.out / "run1.partial").exists())
        text = (self.out / "run1" / "consolidated.csv").read_text(encoding="utf-8")
        self.assertNotIn(",=HYPERLINK", text)
        self.assertIn("'=HYPERLINK", text)
        for a in rep["artifacts"]:
            self.assertEqual(hashlib.sha256((self.out / "run1" / a["name"]).read_bytes()).hexdigest(), a["sha256"])

    def test_deterministic_outputs(self):
        generate(self.inp)
        run(PIPE, self.inp, self.out, "a")
        run(PIPE, self.inp, self.out, "b")
        for n in ("consolidated.csv", "review.csv", "rejected.csv", "incidents.csv", "summary.csv"):
            self.assertEqual((self.out / "a" / n).read_bytes(), (self.out / "b" / n).read_bytes(), n)

    def test_idempotent_replay_does_not_rewrite(self):
        generate(self.inp)
        run(PIPE, self.inp, self.out, "r")
        mtime = (self.out / "r" / "consolidated.csv").stat().st_mtime_ns
        code, rep = run(PIPE, self.inp, self.out, "r")
        self.assertEqual(code, 0)
        self.assertTrue(rep.get("idempotent_replay"))
        self.assertEqual(mtime, (self.out / "r" / "consolidated.csv").stat().st_mtime_ns)

    def test_failure_leaves_no_partial_outputs_and_exit_codes(self):
        self.write_csv("ventas_a.csv", "a,a\n1,2\n")
        code, rep = run(PIPE, self.inp, self.out, "f")
        self.assertEqual(code, 3)
        self.assertFalse((self.out / "f").exists())
        self.assertFalse((self.out / "f.partial").exists())
        self.assertEqual(json.loads((self.out / "f.failed" / "run_report.json").read_text())["status"], "failed")
        bad = self.tmp / "bad.json"
        bad.write_text("{}")
        self.assertEqual(run(bad, self.inp, self.out, "g")[0], 4)

    def test_reject_ratio_policy(self):
        self.write_csv("ventas_a.csv", "Fecha Venta,ID Pedido,Cliente,Total,Cantidad\n2026-01-01,P1,A,xx,1\n2026-01-01,P2,A,yy,1\n")
        code, _ = run(PIPE, self.inp, self.out, "p")
        self.assertEqual(code, 5)
        self.assertFalse((self.out / "p").exists())

    def test_originals_untouched(self):
        generate(self.inp)
        before = {p.name: p.read_bytes() for p in self.inp.iterdir()}
        run(PIPE, self.inp, self.out, "o")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.inp.iterdir()})

    def test_bad_run_id_rejected_by_cli(self):
        from dataflow.runner import main
        self.assertEqual(main([str(PIPE), "--run-id", "../x", "--input-dir", str(self.inp),
                               "--output-dir", str(self.out)]), 4)


class TestPackageAndClient(Base):
    def test_package_build_and_safety(self):
        from package.build_package import build, validate_manifest
        man, pkg = build(PIPE, self.tmp / "dist", with_demo=True, vendor=False)
        text = man.read_text()
        for needle in ("apiVersion: kispat/v1", "kind: Automation", "network: false", "type: manual",
                       'entrypoint: "run.py"'):
            self.assertIn(needle, text)
        with tarfile.open(pkg) as t:
            names = t.getnames()
        self.assertIn("run.py", names)
        self.assertIn("dataflow/runner.py", names)
        self.assertTrue(all(not n.startswith("/") and ".." not in n.split("/") for n in names))
        with self.assertRaises(SystemExit):
            validate_manifest({"metadata": {"version": "1.0.0"}, "spec": {
                "entrypoint": "../x.py", "permissions": {"secrets": ["A=1"]},
                "limits": {"memory_mb": 8, "cpu": 9}, "triggers": []}})

    def test_package_reproducible_and_runs_like_container(self):
        from package.build_package import build
        _, p1 = build(PIPE, self.tmp / "d1", True, False)
        _, p2 = build(PIPE, self.tmp / "d2", True, False)
        self.assertEqual(hashlib.sha256(p1.read_bytes()).hexdigest(), hashlib.sha256(p2.read_bytes()).hexdigest())
        import subprocess
        work = self.tmp / "work"
        work.mkdir()
        with tarfile.open(p1) as t:
            t.extractall(work, filter="data")
        r = subprocess.run([sys.executable, "run.py", "pipelines/sales_consolidation.json"], cwd=work,
                           capture_output=True, text=True, env={**os.environ, "KISPAT_JOB_ID": "job-1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip().splitlines()[-1].startswith("KISPAT_RESULT "))
        self.assertTrue((work / "output" / "job-1" / "run_report.json").is_file())

    def test_client_multipart_and_token_header(self):
        from client.kispat_client import MANIFEST_FIELD, PACKAGE_FIELD, Client, _multipart
        self.assertEqual((MANIFEST_FIELD, PACKAGE_FIELD), ("manifest.yaml", "package.tar.gz"))
        body, ct = _multipart({"manifest": ("m.yaml", b"x: 1", "application/x-yaml")})
        self.assertIn(b'name="manifest"', body)
        self.assertTrue(ct.startswith("multipart/form-data; boundary="))
        os.environ.pop("KISPAT_CLIENT_TOKEN", None)
        with self.assertRaises(SystemExit):
            Client(base_url="http://x")
        c = Client(base_url="http://x:1/", token="t")
        self.assertEqual(c.base, "http://x:1")


if __name__ == "__main__":
    unittest.main()
