"""Router coverage for /api/v1/azure-subscriptions: CRUD + test-connection
(httpx mocked) + unreadable-secret + commit-race 409."""
import sys

import httpx
import pytest

pytestmark = pytest.mark.usefixtures("default_bu")


def _h(token, bu="default"):
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


def _guid(n):
    return f"00000000-0000-0000-0000-{n:012d}"


def _body(n=1, **over):
    b = {
        "subscription_id": _guid(n),
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "client_id": "22222222-2222-2222-2222-222222222222",
        "client_secret": "sp-secret",
        "name": f"azure-{n}",
        "default_location": "eastus",
    }
    b.update(over)
    return b


async def _create(client, token, **over):
    r = await client.post("/api/v1/azure-subscriptions", json=_body(**over), headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()


async def test_crud_and_list(auth_client, admin_token):
    sub = await _create(auth_client, admin_token)
    assert sub["client_secret_masked"].startswith("…")
    lst = await auth_client.get("/api/v1/azure-subscriptions", headers=_h(admin_token))
    assert any(s["id"] == sub["id"] for s in lst.json())
    upd = await auth_client.put(
        f"/api/v1/azure-subscriptions/{sub['id']}",
        json={"name": "renamed", "client_secret": "new-secret"},
        headers=_h(admin_token),
    )
    assert upd.status_code == 200 and upd.json()["name"] == "renamed"
    d = await auth_client.delete(
        f"/api/v1/azure-subscriptions/{sub['id']}", headers=_h(admin_token)
    )
    assert d.status_code == 204


async def test_duplicate_409_and_404s(auth_client, admin_token):
    await _create(auth_client, admin_token, n=2)
    dup = await auth_client.post(
        "/api/v1/azure-subscriptions", json=_body(n=2), headers=_h(admin_token)
    )
    assert dup.status_code == 409
    assert (
        await auth_client.put(
            "/api/v1/azure-subscriptions/x", json={"name": "n"}, headers=_h(admin_token)
        )
    ).status_code == 404
    assert (
        await auth_client.delete("/api/v1/azure-subscriptions/x", headers=_h(admin_token))
    ).status_code == 404


async def test_create_requires_concrete_bu(auth_client, admin_token, _setup_db):
    from app.models.user import User
    from sqlalchemy import select

    async with _setup_db() as s:
        u = (await s.execute(select(User).where(User.email == "admin@test.com"))).scalars().first()
        u.is_superadmin = True
        await s.commit()
    r = await auth_client.post(
        "/api/v1/azure-subscriptions", json=_body(), headers=_h(admin_token, bu="all")
    )
    assert r.status_code == 400


async def test_list_marks_unreadable_secret(auth_client, admin_token, _setup_db):
    sub = await _create(auth_client, admin_token, n=3)
    from app.models.azure_subscription import AzureSubscription

    async with _setup_db() as s:
        row = await s.get(AzureSubscription, sub["id"])
        row.client_secret_encrypted = "bad-token"
        await s.commit()
    lst = await auth_client.get("/api/v1/azure-subscriptions", headers=_h(admin_token))
    target = next(s for s in lst.json() if s["id"] == sub["id"])
    assert target["client_secret_masked"] == "(unreadable)"


class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


async def test_test_connection_paths(auth_client, admin_token, monkeypatch):
    sub = await _create(auth_client, admin_token, n=4)
    # 200 → ok
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(200))
    ok = await auth_client.post(f"/api/v1/azure-subscriptions/{sub['id']}/test", headers=_h(admin_token))
    assert ok.json()["ok"] is True
    # non-200 → ok False with status in detail
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(401, "unauthorized"))
    bad = await auth_client.post(f"/api/v1/azure-subscriptions/{sub['id']}/test", headers=_h(admin_token))
    assert bad.json()["ok"] is False and "401" in bad.json()["detail"]
    # httpx.post raises → exception branch
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(httpx, "post", boom)
    err = await auth_client.post(f"/api/v1/azure-subscriptions/{sub['id']}/test", headers=_h(admin_token))
    assert err.json()["ok"] is False


async def test_test_connection_httpx_missing(auth_client, admin_token, monkeypatch):
    sub = await _create(auth_client, admin_token, n=5)
    # Force `import httpx` inside the endpoint to fail.
    monkeypatch.setitem(sys.modules, "httpx", None)
    r = await auth_client.post(f"/api/v1/azure-subscriptions/{sub['id']}/test", headers=_h(admin_token))
    assert r.json()["ok"] is False and "httpx not available" in r.json()["detail"]


async def test_test_connection_404(auth_client, admin_token):
    r = await auth_client.post("/api/v1/azure-subscriptions/missing/test", headers=_h(admin_token))
    assert r.status_code == 404


async def test_create_commit_race_409(auth_client, admin_token, _setup_db):
    from app.main import app
    from app.db import get_db

    class _CommitFailOnce:
        def __init__(self, real):
            self._real, self._failed = real, False

        def __getattr__(self, n):
            return getattr(self._real, n)

        async def commit(self):
            if not self._failed:
                self._failed = True
                raise RuntimeError("race")
            return await self._real.commit()

    async def _override():
        async with _setup_db() as s:
            yield _CommitFailOnce(s)

    app.dependency_overrides[get_db] = _override
    try:
        r = await auth_client.post(
            "/api/v1/azure-subscriptions", json=_body(n=6), headers=_h(admin_token)
        )
        assert r.status_code == 409
    finally:
        app.dependency_overrides.pop(get_db, None)
