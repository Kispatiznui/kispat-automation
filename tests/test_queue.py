import threading
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from control.db.models import Automation, Deployment, Job, Node
from control.queue import pull_job, report_result
from control.security import hash_token
from tests.conftest import example_manifest_dict, unique


def test_pull_empty(session, seed_node):
    assert pull_job(session, seed_node.node.id) is None


def test_pull_one(session, seed_node, make_job):
    job = make_job()
    got = pull_job(session, seed_node.node.id)
    assert got is not None
    assert got.id == job.id
    assert got.status == "assigned"
    assert got.attempts == 1
    assert got.node_id == seed_node.node.id
    assert got.started_at is not None


def test_pull_ignores_jobs_of_other_nodes(session, seed_node, make_job):
    other = Node(node_id="otro", hostname="h", os="linux", arch="x", capabilities={},
                 token_hash=hash_token("z" * 48), status="online")  # fmt: skip
    session.add(other)
    session.flush()
    make_job(node_id=other.id)
    assert pull_job(session, seed_node.node.id) is None


def test_pull_priority_order(session, seed_node, make_job):
    low = make_job(priority=10)
    high = make_job(priority=500)
    assert pull_job(session, seed_node.node.id).id == high.id
    assert pull_job(session, seed_node.node.id).id == low.id


def test_pull_concurrent(engine):
    """Dos sesiones reales: A toma el job y mantiene su transacción abierta;
    B debe terminar SIN bloquearse y sin obtener nada (SKIP LOCKED)."""
    ids = {}
    with Session(engine) as s:
        a, b = (Node(node_id=unique("cc"), hostname="h", os="linux", arch="x", capabilities={},
                     token_hash=hash_token(unique("t")), status="online") for _ in range(2))  # fmt: skip
        m = example_manifest_dict()
        auto = Automation(name=unique("cc"), version="0.0.1", state="approved", manifest=m,
                          package_path="/x", author="t")  # fmt: skip
        s.add_all([a, b, auto])
        s.flush()
        dep = Deployment(automation_id=auto.id, target_node_ids=[], status="active", approved_by="t")
        s.add(dep)
        s.flush()
        job = Job(automation_id=auto.id, deployment_id=dep.id, node_id=None, status="queued",
                  timeout_s=30, limits={}, payload={})  # fmt: skip
        s.add(job)
        s.commit()
        ids.update(a=a.id, b=b.id, auto=auto.id, dep=dep.id, job=job.id)

    a_has_job, b_done = threading.Event(), threading.Event()
    out: dict = {}

    def worker_a():
        with Session(engine) as sa:
            got = pull_job(sa, ids["a"])
            out["a"] = got.id if got else None
            a_has_job.set()
            out["b_finished_while_a_locked"] = b_done.wait(15)
            sa.commit()

    def worker_b():
        if not a_has_job.wait(15):
            return
        with Session(engine) as sb:
            got = pull_job(sb, ids["b"])
            out["b"] = got.id if got else None
            sb.commit()
        b_done.set()

    threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert out["a"] == ids["job"]
        assert out["b"] is None
        assert out["b_finished_while_a_locked"], "B se bloqueó esperando el lock: no hay SKIP LOCKED"
    finally:
        with Session(engine) as s:
            for model, key in ((Job, "job"), (Deployment, "dep"), (Automation, "auto")):
                s.delete(s.get(model, ids[key]))
            s.flush()
            for key in ("a", "b"):
                s.delete(s.get(Node, ids[key]))
            s.commit()


def test_backoff(session, seed_node, make_job):
    """max_attempts=4: fallos 1..3 reencolan con 60/300/900 s; el 4º es definitivo."""
    job = make_job(max_attempts=4)
    for expected in (60, 300, 900):
        session.execute(update(Job).where(Job.id == job.id).values(scheduled_for=func.now()))
        got = pull_job(session, seed_node.node.id)
        assert got is not None
        job = report_result(session, got.id, {"status": "failed", "exit_code": 1})
        now = session.execute(select(func.now())).scalar()
        assert job.status == "queued"
        assert job.scheduled_for - now == timedelta(seconds=expected)

    session.execute(update(Job).where(Job.id == job.id).values(scheduled_for=func.now()))
    got = pull_job(session, seed_node.node.id)
    job = report_result(session, got.id, {"status": "failed", "exit_code": 1})
    assert job.status == "failed"
    assert job.finished_at is not None
    assert job.error == "exit_code=1"


def test_report_success_and_idempotent(session, seed_node, make_job):
    make_job()
    got = pull_job(session, seed_node.node.id)
    job = report_result(session, got.id, {"status": "succeeded", "exit_code": 0, "stdout": "ok"})
    assert job.status == "succeeded"
    assert report_result(session, got.id, {"status": "failed"}).status == "succeeded"
