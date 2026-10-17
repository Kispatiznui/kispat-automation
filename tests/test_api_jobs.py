from datetime import UTC, datetime

from tests.conftest import CLIENT_TOKEN, bearer

OK = {"status": "succeeded", "exit_code": 0, "stdout": "hola", "stderr": "", "duration_ms": 5}
FAIL = {"status": "failed", "exit_code": 1, "stdout": "", "stderr": "boom", "duration_ms": 5}


def pull(client, token, node_id="node-test"):
    return client.get("/api/v1/agent/jobs", params={"node_id": node_id}, headers=bearer(token))


def test_pull_with_node_token(client, seed_node, make_job):
    job = make_job()
    r = pull(client, seed_node.token)
    assert r.status_code == 200
    assert r.json()["id"] == str(job.id)
    assert r.json()["status"] == "assigned"


def test_pull_empty_is_204(client, seed_node):
    assert pull(client, seed_node.token).status_code == 204


def test_pull_with_client_token_is_403(client, seed_node, make_job):
    make_job()
    assert pull(client, CLIENT_TOKEN).status_code == 403


def test_pull_without_token_is_401(client):
    assert client.get("/api/v1/agent/jobs", params={"node_id": "x"}).status_code == 401


def test_pull_wrong_node_id_is_403(client, seed_node):
    assert pull(client, seed_node.token, node_id="otro").status_code == 403


def test_report_success(client, seed_node, make_job):
    make_job()
    job_id = pull(client, seed_node.token).json()["id"]
    r = client.post(f"/api/v1/agent/jobs/{job_id}/result", json=OK, headers=bearer(seed_node.token))
    assert r.status_code == 200
    assert r.json()["status"] == "succeeded"
    got = client.get(f"/api/v1/jobs/{job_id}", headers=bearer(CLIENT_TOKEN)).json()
    assert got["result"]["stdout"] == "hola"


def test_report_failure_requeues_with_backoff(client, seed_node, make_job):
    make_job()
    job_id = pull(client, seed_node.token).json()["id"]
    r = client.post(f"/api/v1/agent/jobs/{job_id}/result", json=FAIL, headers=bearer(seed_node.token))
    body = r.json()
    assert body["status"] == "queued"
    assert body["attempts"] == 1
    assert datetime.fromisoformat(body["scheduled_for"]) > datetime.now(UTC)
    assert pull(client, seed_node.token).status_code == 204  # aún en backoff


def test_report_for_job_of_another_node_is_403(client, session, seed_node, make_job):
    from control.db.models import Node
    from control.security import hash_token

    job = make_job()
    pull(client, seed_node.token)
    other = Node(node_id="otro", hostname="h", os="l", arch="x", capabilities={},
                 token_hash=hash_token("o" * 48), status="online")  # fmt: skip
    session.add(other)
    session.flush()
    r = client.post(f"/api/v1/agent/jobs/{job.id}/result", json=OK, headers=bearer("o" * 48))
    assert r.status_code == 403


def test_get_job_requires_client_token(client, seed_node, make_job):
    job = make_job()
    assert client.get(f"/api/v1/jobs/{job.id}").status_code == 401
    assert client.get(f"/api/v1/jobs/{job.id}", headers=bearer(CLIENT_TOKEN)).status_code == 200
