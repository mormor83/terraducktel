"""Router coverage for users, business_units, drift, audit, runtime_config."""
import uuid

import pytest
from sqlalchemy import select

from app.models.business_unit import DEFAULT_BU_ID
from app.models.user import User

pytestmark = pytest.mark.usefixtures("default_bu")


def _h(token, bu="default"):
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


async def _promote(setup_db, email="admin@test.com"):
    async with setup_db() as s:
        u = (await s.execute(select(User).where(User.email == email))).scalars().first()
        u.is_superadmin = True
        await s.commit()
        return u.id


# ─── users ───────────────────────────────────────────────────────────────────


async def test_list_users_with_memberships(auth_client, admin_token):
    r = await auth_client.get("/api/v1/users", headers=_h(admin_token))
    assert r.status_code == 200
    admin = next(u for u in r.json() if u["email"] == "admin@test.com")
    assert any(m["business_unit_slug"] == "default" for m in admin["memberships"])


async def test_eligible_reviewers_deprecated_empty(auth_client, operator_token):
    r = await auth_client.get("/api/v1/users/eligible-reviewers", headers=_h(operator_token))
    assert r.status_code == 200 and r.json() == []


async def test_patch_user_requires_superadmin(auth_client, admin_token):
    # admin (not superadmin) → 403
    r = await auth_client.patch(
        "/api/v1/users/whatever", json={"is_superadmin": True}, headers=_h(admin_token)
    )
    assert r.status_code == 403


async def test_patch_user_memberships_and_flags(auth_client, admin_token, _setup_db):
    await _promote(_setup_db)
    # target = operator user; also seed a second BU the operator is NOT in yet
    # so add_memberships exercises the insert branch (not the update branch).
    from app.models.business_unit import BusinessUnit

    async with _setup_db() as s:
        op = (await s.execute(select(User).where(User.email == "operator@test.com"))).scalars().first()
        op_id = op.id
        s.add(BusinessUnit(id="bu-second", slug="second", name="Second"))
        await s.commit()

    # 404 missing user
    assert (
        await auth_client.patch("/api/v1/users/missing", json={}, headers=_h(admin_token))
    ).status_code == 404

    # add membership (new) + bad-BU 400
    bad = await auth_client.patch(
        f"/api/v1/users/{op_id}",
        json={"add_memberships": [{"business_unit_id": "nope", "role": "operator"}]},
        headers=_h(admin_token),
    )
    assert bad.status_code == 400

    ok = await auth_client.patch(
        f"/api/v1/users/{op_id}",
        json={
            "is_superadmin": True,
            # bu-second is a NEW membership (insert branch); default already
            # exists (update branch) — covers both.
            "add_memberships": [
                {"business_unit_id": "bu-second", "role": "operator"},
                {"business_unit_id": DEFAULT_BU_ID, "role": "viewer"},
            ],
        },
        headers=_h(admin_token),
    )
    assert ok.status_code == 200 and ok.json()["is_superadmin"] is True
    # add again → updates existing role (operator), then remove it
    upd = await auth_client.patch(
        f"/api/v1/users/{op_id}",
        json={
            "add_memberships": [{"business_unit_id": DEFAULT_BU_ID, "role": "operator"}],
            "remove_memberships": ["nonexistent-bu"],
        },
        headers=_h(admin_token),
    )
    assert upd.status_code == 200
    rem = await auth_client.patch(
        f"/api/v1/users/{op_id}",
        json={"remove_memberships": [DEFAULT_BU_ID]},
        headers=_h(admin_token),
    )
    assert all(m["business_unit_id"] != DEFAULT_BU_ID for m in rem.json()["memberships"])


async def test_patch_cannot_demote_last_superadmin(auth_client, admin_token, _setup_db):
    admin_id = await _promote(_setup_db)
    r = await auth_client.patch(
        f"/api/v1/users/{admin_id}", json={"is_superadmin": False}, headers=_h(admin_token)
    )
    assert r.status_code == 409


# ─── business_units ──────────────────────────────────────────────────────────


async def test_bu_list_member_vs_superadmin(auth_client, admin_token, viewer_token, _setup_db):
    # viewer (member of default only) sees just default
    vr = await auth_client.get("/api/v1/business-units", headers=_h(viewer_token))
    assert [b["slug"] for b in vr.json()] == ["default"]
    # superadmin sees all
    await _promote(_setup_db)
    sr = await auth_client.get("/api/v1/business-units", headers=_h(admin_token))
    assert any(b["slug"] == "default" for b in sr.json())


async def test_bu_create_update_and_conflicts(auth_client, admin_token, viewer_token, _setup_db):
    # non-superadmin → 403
    assert (
        await auth_client.post(
            "/api/v1/business-units", json={"slug": "x", "name": "X"}, headers=_h(viewer_token)
        )
    ).status_code == 403
    await _promote(_setup_db)
    created = await auth_client.post(
        "/api/v1/business-units", json={"slug": "acme", "name": "Acme"}, headers=_h(admin_token)
    )
    assert created.status_code == 201
    # duplicate slug → 409
    dup = await auth_client.post(
        "/api/v1/business-units", json={"slug": "acme", "name": "Acme2"}, headers=_h(admin_token)
    )
    assert dup.status_code == 409
    # update + 404
    upd = await auth_client.put(
        f"/api/v1/business-units/{created.json()['id']}",
        json={"name": "Renamed"},
        headers=_h(admin_token),
    )
    assert upd.status_code == 200 and upd.json()["name"] == "Renamed"
    assert (
        await auth_client.put(
            "/api/v1/business-units/missing", json={"name": "n"}, headers=_h(admin_token)
        )
    ).status_code == 404


async def test_bu_create_commit_race_409(auth_client, admin_token, _setup_db):
    await _promote(_setup_db)
    from app.main import app
    from app.db import get_db

    class _Fail:
        def __init__(self, real):
            self._real, self._f = real, False

        def __getattr__(self, n):
            return getattr(self._real, n)

        async def commit(self):
            if not self._f:
                self._f = True
                raise RuntimeError("race")
            return await self._real.commit()

    async def _ov():
        async with _setup_db() as s:
            yield _Fail(s)

    app.dependency_overrides[get_db] = _ov
    try:
        r = await auth_client.post(
            "/api/v1/business-units", json={"slug": "racey", "name": "R"}, headers=_h(admin_token)
        )
        assert r.status_code == 409
    finally:
        app.dependency_overrides.pop(get_db, None)


# ─── drift ───────────────────────────────────────────────────────────────────


async def _make_ws(setup_db, name="drift-ws"):
    from app.models.workspace import Workspace

    async with setup_db() as s:
        ws = Workspace(
            business_unit_id=DEFAULT_BU_ID,
            name=name,
            aws_account_id="123456789012",
            region="us-east-1",
            environment="dev",
        )
        s.add(ws)
        await s.commit()
        return ws.id


async def test_drift_scan_and_report(auth_client, admin_token, _setup_db, monkeypatch):
    ws_id = await _make_ws(_setup_db)
    # scan: 404 + 202
    assert (
        await auth_client.post("/api/v1/drift/missing/scan", headers=_h(admin_token))
    ).status_code == 404
    scan = await auth_client.post(f"/api/v1/drift/{ws_id}/scan", headers=_h(admin_token))
    assert scan.status_code == 202 and "report_id" in scan.json()

    # report: workspace mismatch → 400
    mismatch = await auth_client.post(
        f"/api/v1/drift/{ws_id}/report",
        json={"workspace_id": "other", "has_drift": False, "summary": "s", "plan_output": ""},
        headers=_h(admin_token),
    )
    assert mismatch.status_code == 400
    # 404 unknown workspace (matching ids)
    nf = await auth_client.post(
        "/api/v1/drift/ghost/report",
        json={"workspace_id": "ghost", "has_drift": False, "summary": "s", "plan_output": ""},
        headers=_h(admin_token),
    )
    assert nf.status_code == 404

    # clean report
    clean = await auth_client.post(
        f"/api/v1/drift/{ws_id}/report",
        json={"workspace_id": ws_id, "has_drift": False, "summary": "ok", "plan_output": ""},
        headers=_h(admin_token),
    )
    assert clean.json()["has_drift"] is False

    # drifted report → triggers the alert hook (mocked)
    sent = {}

    async def fake_alert(db, name, summary):
        sent["name"] = name

    monkeypatch.setattr("app.routers.drift.send_drift_alert", fake_alert)
    drifted = await auth_client.post(
        f"/api/v1/drift/{ws_id}/report",
        json={"workspace_id": ws_id, "has_drift": True, "summary": "changed", "plan_output": "x"},
        headers=_h(admin_token),
    )
    assert drifted.json()["has_drift"] is True and sent["name"] == "drift-ws"


# ─── audit ───────────────────────────────────────────────────────────────────


async def test_audit_verify_and_list(auth_client, admin_token, _setup_db):
    from app.services import audit_chain as ac
    from app.models.audit_log import AuditLog
    from app.models.workspace import Workspace

    # /audit/verify walks the global cross-BU chain → superadmin-only.
    await _promote(_setup_db)

    async with _setup_db() as s:
        # Audit list is BU-scoped via the referenced workspace, so the row's
        # workspace must exist in the caller's BU for a non-superadmin admin.
        s.add(Workspace(
            id="w1", name="audit-ws", business_unit_id=DEFAULT_BU_ID,
            repo_url="local://audit", tf_working_dir=".",
            aws_account_id="123456789012", environment="dev",
        ))
        row = AuditLog(user_id="u", action="login", resource_type="run", resource_id="r1",
                       workspace_id="w1")
        s.add(row)
        await ac.stamp(s, row)
        await s.commit()

    v = await auth_client.get("/api/v1/audit/verify", headers=_h(admin_token))
    assert v.json()["ok"] is True

    all_logs = await auth_client.get("/api/v1/audit", headers=_h(admin_token))
    assert len(all_logs.json()["items"]) >= 1
    # filters
    by_run = await auth_client.get("/api/v1/audit?run_id=r1", headers=_h(admin_token))
    assert all(i["resource_id"] == "r1" for i in by_run.json()["items"])
    by_ws = await auth_client.get("/api/v1/audit?workspace_id=w1", headers=_h(admin_token))
    assert len(by_ws.json()["items"]) >= 1


# ─── runtime_config ──────────────────────────────────────────────────────────


async def test_runtime_config_list_and_update(auth_client, admin_token, _setup_db):
    # Platform-global runtime settings are superadmin-only to change;
    # promote the seeded admin so the write path is exercised.
    async with _setup_db() as s:
        u = (await s.execute(select(User).where(User.email == "admin@test.com"))).scalars().first()
        u.is_superadmin = True
        await s.commit()

    lst = await auth_client.get("/api/v1/runtime-config", headers=_h(admin_token))
    assert "drift.interval_seconds" in lst.json()

    ok = await auth_client.put(
        "/api/v1/runtime-config/drift.interval_seconds", json={"value": 120}, headers=_h(admin_token)
    )
    assert ok.status_code == 200 and ok.json()["value"] == 120

    # unknown key → 404
    assert (
        await auth_client.put(
            "/api/v1/runtime-config/nope", json={"value": 1}, headers=_h(admin_token)
        )
    ).status_code == 404
    # non-positive → 422
    assert (
        await auth_client.put(
            "/api/v1/runtime-config/drift.interval_seconds", json={"value": 0},
            headers=_h(admin_token),
        )
    ).status_code == 422
