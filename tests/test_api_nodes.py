from sqlalchemy import select

from control.config import get_settings
from control.db.models import Node
from tests.conftest import CLIENT_TOKEN, bearer

REG = {"node_id": "nuevo", "hostname": "h", "os": "linux", "arch": "x86_64", "capabilities": {}}


def test_register_creates_node(client, session):
    token = "t" * 48
    r = client.post("/api/v1/nodes/register", json=REG, headers=bearer(token))
    assert r.status_code == 200
    assert r.json() == {"node_id": "nuevo", "node_token": token}
    node = session.execute(select(Node).where(Node.node_id == "nuevo")).scalars().one()
    assert node.token_hash != token  # solo se guarda el hash
    assert node.status == "online"


def test_register_existing_with_other_token_rejected(client):
    client.post("/api/v1/nodes/register", json=REG, headers=bearer("a" * 48))
    r = client.post("/api/v1/nodes/register", json=REG, headers=bearer("b" * 48))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_register_requires_registration_token_when_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "REGISTRATION_TOKEN", "reg-secret")
    h = bearer("t" * 48)
    assert client.post("/api/v1/nodes/register", json=REG, headers=h).status_code == 401
    h["X-Registration-Token"] = "reg-secret"
    assert client.post("/api/v1/nodes/register", json=REG, headers=h).status_code == 200


def test_heartbeat_updates_timestamp(client, session, seed_node):
    assert seed_node.node.last_heartbeat_at is None
    r = client.post("/api/v1/nodes/node-test/heartbeat", json={}, headers=bearer(seed_node.token))
    assert r.status_code == 200
    session.refresh(seed_node.node)
    assert seed_node.node.last_heartbeat_at is not None


def test_heartbeat_invalid_token(client, seed_node):
    r = client.post("/api/v1/nodes/node-test/heartbeat", headers=bearer("x" * 48))
    assert r.status_code == 401
    assert set(r.json()["error"]) >= {"code", "message"}


def test_heartbeat_other_nodes_path_forbidden(client, seed_node):
    r = client.post("/api/v1/nodes/otro/heartbeat", headers=bearer(seed_node.token))
    assert r.status_code == 403


def test_list_nodes_requires_client_token(client, seed_node):
    assert client.get("/api/v1/nodes").status_code == 401
    assert client.get("/api/v1/nodes", headers=bearer(seed_node.token)).status_code == 401
    r = client.get("/api/v1/nodes", headers=bearer(CLIENT_TOKEN))
    assert r.status_code == 200
    assert "token_hash" not in r.json()[0]
