"""Router coverage for /api/v1/gcp-projects: CRUD + BU scope + RBAC +
SA-JSON validation + project-id mismatch + /test + /bucket guards.

google-auth / google-cloud-storage are NOT installed in the test image, so
the /test and /bucket endpoints exercise their graceful-fallback branches.
"""
import json

import pytest

pytestmark = pytest.mark.usefixtures("default_bu")


def _h(token, bu="default"):
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


def _sa_json(project_id="acme-prod-1234", email=None):
    email = email or f"sa@{project_id}.iam.gserviceaccount.com"
    return json.dumps({
        "type": "service_account",
        "project_id": project_id,
        "private_key_id": "abc123def456",
        "private_key": "-----BEGIN PRIVATE KEY-----\nMIIfakekey\n-----END PRIVATE KEY-----\n",
        "client_email": email,
        "client_id": "123456789",
        "token_uri": "https://oauth2.googleapis.com/token",
    })


def _body(project_id="acme-prod-1234", **over):
    b = {
        "project_id": project_id,
        "name": f"gcp-{project_id}",
        "default_region": "us-central1",
        "service_account_json": _sa_json(project_id=project_id),
    }
    b.update(over)
    return b


async def _create(client, token, **over):
    r = await client.post("/api/v1/gcp-projects", json=_body(**over), headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()


async def test_crud_and_list(auth_client, admin_token):
    proj = await _create(auth_client, admin_token)
    # Masked SA = the client_email (an identifier); the key JSON never leaks.
    assert proj["service_account_masked"].endswith("gserviceaccount.com")
    assert "service_account_json" not in proj
    assert "service_account_json_encrypted" not in proj
    lst = await auth_client.get("/api/v1/gcp-projects", headers=_h(admin_token))
    assert any(p["id"] == proj["id"] for p in lst.json())
    upd = await auth_client.put(
        f"/api/v1/gcp-projects/{proj['id']}",
        json={"name": "renamed", "state_bucket": "acme-tfstate"},
        headers=_h(admin_token),
    )
    assert upd.status_code == 200
    assert upd.json()["name"] == "renamed"
    assert upd.json()["state_bucket"] == "acme-tfstate"
    d = await auth_client.delete(f"/api/v1/gcp-projects/{proj['id']}", headers=_h(admin_token))
    assert d.status_code == 204


async def test_duplicate_409_and_404s(auth_client, admin_token):
    await _create(auth_client, admin_token, project_id="dup-proj-9999")
    dup = await auth_client.post(
        "/api/v1/gcp-projects", json=_body(project_id="dup-proj-9999"), headers=_h(admin_token)
    )
    assert dup.status_code == 409
    assert (
        await auth_client.put("/api/v1/gcp-projects/x", json={"name": "n"}, headers=_h(admin_token))
    ).status_code == 404
    assert (
        await auth_client.delete("/api/v1/gcp-projects/x", headers=_h(admin_token))
    ).status_code == 404


async def test_sa_json_must_be_valid(auth_client, admin_token):
    r = await auth_client.post(
        "/api/v1/gcp-projects", json=_body(service_account_json="not json"), headers=_h(admin_token)
    )
    assert r.status_code == 422
    bad = json.dumps({"type": "user", "project_id": "acme-prod-1234", "client_email": "x", "private_key": "y"})
    r = await auth_client.post(
        "/api/v1/gcp-projects", json=_body(service_account_json=bad), headers=_h(admin_token)
    )
    assert r.status_code == 422


async def test_project_id_mismatch_422(auth_client, admin_token):
    body = _body(project_id="declared-proj-1")
    body["service_account_json"] = _sa_json(project_id="other-proj-2")
    r = await auth_client.post("/api/v1/gcp-projects", json=body, headers=_h(admin_token))
    assert r.status_code == 422
    assert "other-proj-2" in r.text


async def test_rbac_viewer_cannot_create(auth_client, viewer_token):
    r = await auth_client.post("/api/v1/gcp-projects", json=_body(), headers=_h(viewer_token))
    assert r.status_code == 403


async def test_create_requires_concrete_bu(auth_client, admin_token, _setup_db):
    from app.models.user import User
    from sqlalchemy import select

    async with _setup_db() as s:
        u = (await s.execute(select(User).where(User.email == "admin@test.com"))).scalars().first()
        u.is_superadmin = True
        await s.commit()
    r = await auth_client.post("/api/v1/gcp-projects", json=_body(), headers=_h(admin_token, bu="all"))
    assert r.status_code == 400


async def test_test_connection_invalid_key(auth_client, admin_token):
    # The stored SA key is structurally valid JSON but has a fake private_key,
    # so minting a token fails → ok=False (never raises). Covers the real
    # path when google-auth IS installed (as it is in the API image).
    proj = await _create(auth_client, admin_token, project_id="test-proj-5555")
    r = await auth_client.post(f"/api/v1/gcp-projects/{proj['id']}/test", headers=_h(admin_token))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["detail"]  # some validation error message is surfaced


async def test_bucket_requires_state_bucket(auth_client, admin_token):
    proj = await _create(auth_client, admin_token, project_id="nobucket-8888")
    r = await auth_client.post(f"/api/v1/gcp-projects/{proj['id']}/bucket", headers=_h(admin_token))
    assert r.status_code == 400
