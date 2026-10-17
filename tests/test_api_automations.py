import io
import tarfile

import yaml

from tests.conftest import CLIENT_TOKEN, bearer, example_manifest_dict

H = bearer(CLIENT_TOKEN)


def make_tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def upload(client, manifest: dict, pkg: bytes | None = None, headers=H):
    pkg = pkg if pkg is not None else make_tar({"main.py": b"print('hola')"})
    return client.post(
        "/api/v1/automations",
        headers=headers,
        files={
            "manifest.yaml": ("manifest.yaml", yaml.safe_dump(manifest).encode(), "application/x-yaml"),
            "package.tar.gz": ("package.tar.gz", pkg, "application/gzip"),
        },
    )


def test_create_valid(client):
    r = upload(client, example_manifest_dict())
    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "pending_approval"
    assert body["name"] == "hello-world"


def test_create_requires_client_token(client):
    assert upload(client, example_manifest_dict(), headers={}).status_code == 401


def test_create_invalid_manifest_reports_field(client):
    m = example_manifest_dict()
    m["spec"]["entrypoint"] = "../evil.py"
    r = upload(client, m)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid_manifest"
    assert any("entrypoint" in d["field"] for d in err["details"])


def test_create_entrypoint_missing_in_package(client):
    r = upload(client, example_manifest_dict(), pkg=make_tar({"otro.py": b""}))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_package"


def test_create_unsafe_tar_rejected(client):
    r = upload(client, example_manifest_dict(), pkg=make_tar({"../escape.py": b"", "main.py": b""}))
    assert r.status_code == 422


def test_duplicate_version_conflict(client):
    assert upload(client, example_manifest_dict()).status_code == 201
    assert upload(client, example_manifest_dict()).status_code == 409


def test_approve(client):
    auto_id = upload(client, example_manifest_dict()).json()["id"]
    r = client.post(f"/api/v1/automations/{auto_id}/approve", headers=H)
    assert r.status_code == 200
    assert r.json()["state"] == "approved"
    assert r.json()["approved_at"] is not None
    assert client.post(f"/api/v1/automations/{auto_id}/approve", headers=H).status_code == 409


def test_deploy_requires_approval_then_enqueues(client, seed_node):
    auto_id = upload(client, example_manifest_dict()).json()["id"]
    body = {"automation_id": auto_id, "target_node_ids": ["node-test"]}
    assert client.post("/api/v1/deployments", json=body, headers=H).status_code == 409
    client.post(f"/api/v1/automations/{auto_id}/approve", headers=H)
    r = client.post("/api/v1/deployments", json=body, headers=H)
    assert r.status_code == 201
    assert len(r.json()["jobs"]) == 1
    bad = {"automation_id": auto_id, "target_node_ids": ["fantasma"]}
    assert client.post("/api/v1/deployments", json=bad, headers=H).status_code == 404
