import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DB_URL:
    pytest.exit("Falta TEST_DATABASE_URL. Crea la DB kispat_test y exporta la variable.", 2)

# Debe fijarse ANTES de importar control.* (get_settings está cacheado).
_TMP = Path(tempfile.mkdtemp(prefix="kispat-tests-"))
os.environ.update(
    DATABASE_URL=TEST_DB_URL,
    CLIENT_TOKEN="test-client-token-0123456789abcdef0123456789",
    REGISTRATION_TOKEN="",
    AUTOMATIONS_DIR=str(_TMP / "automations"),
    AUDIT_PATH=str(_TMP / "audit.jsonl"),
)

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from control.db import models  # noqa: E402
from control.db.base import Base  # noqa: E402
from control.db.models import Automation, Deployment, Job, Node  # noqa: E402
from control.db.session import get_session  # noqa: E402
from control.main import app  # noqa: E402
from control.security import hash_token  # noqa: E402
from shared.contracts.manifest import AutomationManifest  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[1] / "shared/contracts/examples/hello-world.yaml"
CLIENT_TOKEN = os.environ["CLIENT_TOKEN"]


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def example_manifest_dict() -> dict:
    return yaml.safe_load(EXAMPLE.read_text())


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DB_URL)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    """Transacción externa + SAVEPOINT: los commit() de los endpoints no escapan; rollback al final."""
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    yield s
    s.close()
    trans.rollback()
    conn.close()


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@dataclass
class SeededNode:
    node: Node
    token: str


@pytest.fixture
def seed_node(session) -> SeededNode:
    token = "n" * 48
    node = Node(
        node_id="node-test",
        hostname="host",
        os="linux",
        arch="x86_64",
        capabilities={},
        token_hash=hash_token(token),
        status="online",
    )
    session.add(node)
    session.flush()
    return SeededNode(node, token)


@pytest.fixture
def seed_automation(session) -> Automation:
    manifest = AutomationManifest.model_validate(example_manifest_dict())
    auto = Automation(
        name=manifest.metadata.name,
        version=manifest.metadata.version,
        state="approved",
        manifest=manifest.model_dump(mode="json", by_alias=True),
        package_path="/nonexistent/package.tar.gz",
        author=manifest.metadata.author,
    )
    session.add(auto)
    session.flush()
    return auto


@pytest.fixture
def make_job(session, seed_automation, seed_node):
    def factory(node_id=..., **overrides) -> Job:
        dep = Deployment(
            automation_id=seed_automation.id, target_node_ids=[seed_node.node.node_id],
            status="active", approved_by="test",
        )  # fmt: skip
        session.add(dep)
        session.flush()
        spec = seed_automation.manifest["spec"]
        job = Job(
            automation_id=seed_automation.id,
            deployment_id=dep.id,
            node_id=seed_node.node.id if node_id is ... else node_id,
            status="queued",
            timeout_s=spec["limits"]["timeout_s"],
            limits=spec["limits"],
            payload={"automation_id": str(seed_automation.id), "runtime": spec["runtime"],
                     "entrypoint": spec["entrypoint"], "args": [], "env": {},
                     "permissions": spec["permissions"]},
            **overrides,
        )  # fmt: skip
        session.add(job)
        session.flush()
        return job

    return factory


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


__all__ = ["CLIENT_TOKEN", "bearer", "example_manifest_dict", "models", "unique"]
