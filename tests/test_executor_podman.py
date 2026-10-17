import subprocess
from types import SimpleNamespace

import pytest

from agent.executors import podman as podman_mod
from agent.executors.podman import PodmanExecutor


def make_job(network=False, runtime="python3.12"):
    return {
        "id": "job-1",
        "timeout_s": 30,
        "limits": {"cpu": 0.5, "memory_mb": 256, "pids": 32, "timeout_s": 45},
        "payload": {
            "automation_id": "auto-1",
            "runtime": runtime,
            "entrypoint": "main.py",
            "args": ["--x"],
            "env": {"FOO": "bar"},
            "permissions": {"network": network},
        },
    }


@pytest.fixture
def fake_run(monkeypatch):
    calls = []

    def _install(returncode=0, stdout=b"", raises=None):
        def fake(args, **kw):
            calls.append((args, kw))
            if raises and "run" in args:
                raise raises
            if "stdout" in kw and hasattr(kw["stdout"], "write"):
                kw["stdout"].write(stdout)
            return SimpleNamespace(returncode=returncode)

        monkeypatch.setattr(podman_mod.subprocess, "run", fake)
        return calls

    return _install


def test_limits_and_network_none(fake_run, tmp_path):
    calls = fake_run()
    res = PodmanExecutor().run(make_job(network=False), str(tmp_path))
    args = calls[0][0]
    for expected in ("--memory=256m", "--cpus=0.5", "--pids-limit=32", "--network=none",
                     "--timeout=45", "--cap-drop=ALL", "--rm"):  # fmt: skip
        assert expected in args
    assert args[-3:] == ["python", "main.py", "--x"]
    assert "FOO=bar" in args
    assert calls[0][1]["timeout"] == 55  # timeout_s + 10
    assert res["status"] == "succeeded"
    assert res["exit_code"] == 0


def test_network_allowed_omits_flag(fake_run, tmp_path):
    calls = fake_run()
    PodmanExecutor().run(make_job(network=True), str(tmp_path))
    assert "--network=none" not in calls[0][0]


def test_exit_code_propagates(fake_run, tmp_path):
    fake_run(returncode=3)
    res = PodmanExecutor().run(make_job(), str(tmp_path))
    assert res["status"] == "failed"
    assert res["exit_code"] == 3
    assert res["error"] == "exit_code=3"


def test_stdout_captured(fake_run, tmp_path):
    fake_run(stdout=b"hola desde kispat automation\n")
    res = PodmanExecutor().run(make_job(), str(tmp_path))
    assert "hola desde kispat automation" in res["stdout"]
    assert (tmp_path / "stdout.log").exists()


def test_timeout_marks_failed(fake_run, tmp_path):
    fake_run(raises=subprocess.TimeoutExpired(cmd="podman", timeout=55))
    res = PodmanExecutor().run(make_job(), str(tmp_path))
    assert res["status"] == "failed"
    assert "timeout" in res["error"]


def test_unsupported_runtime(fake_run, tmp_path):
    calls = fake_run()
    res = PodmanExecutor().run(make_job(runtime="cobol"), str(tmp_path))
    assert res["status"] == "failed"
    assert calls == []


def test_fetch_package_failure_does_not_crash(fake_run, tmp_path):
    fake_run()

    def boom(_aid, _dest):
        raise RuntimeError("control caído")

    res = PodmanExecutor(fetch_package=boom).run(make_job(), str(tmp_path))
    assert res["status"] == "failed"
    assert "control caído" in res["error"]
