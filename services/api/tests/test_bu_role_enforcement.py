"""A caller's per-BU membership role (operator|viewer) must actually
gate writes in that BU — the legacy global users.role is not enough. A global
`admin` and superadmins stay exempt (there is no per-BU admin role)."""
from __future__ import annotations

import uuid

import pytest

from app.auth.jwt import hash_password
from app.models.business_unit import BusinessUnit, UserBusinessUnit
from app.models.run import Run, RunStatus
from app.models.user import User
from app.models.workspace import Workspace

BU_X = "dddddddd-dddd-dddd-dddd-dddddddddddd"


async def _login(client, email):
    r = await client.post("/api/v1/auth/token",
                          json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _hdr(token, bu="bux"):
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


async def _user(s, email, role, membership_role, bu=BU_X, is_superadmin=False):
    uid = str(uuid.uuid4())
    s.add(User(id=uid, email=email, hashed_password=hash_password("password123"),
               role=role, auth_provider="local", is_superadmin=is_superadmin))
    if membership_role is not None:
        s.add(UserBusinessUnit(user_id=uid, business_unit_id=bu, role=membership_role))
    return uid


@pytest.fixture
async def role_env(_setup_db):
    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    async with _setup_db() as s:
        s.add(BusinessUnit(id=BU_X, slug="bux", name="BU X"))
        # carol: globally operator, but only a VIEWER in BU-X.
        await _user(s, "carol@test.com", "operator", "viewer")
        # dave: operator in BU-X (control — writes allowed).
        await _user(s, "dave@test.com", "operator", "operator")
        # erin: global admin (not superadmin), viewer membership — admin exempt.
        await _user(s, "erin@test.com", "admin", "viewer")
        s.add(Workspace(id=ws_id, name="role-ws", business_unit_id=BU_X,
                        repo_url="local://role", tf_working_dir=".",
                        aws_account_id="123456789012", region="us-east-1",
                        environment="dev"))
        s.add(Run(id=run_id, workspace_id=ws_id, command="plan",
                  status=RunStatus.AWAITING_APPROVAL))
        await s.commit()
    return {"ws_id": ws_id, "run_id": run_id}


@pytest.mark.asyncio
async def test_global_operator_bu_viewer_cannot_write(auth_client, role_env):
    ws_id, run_id = role_env["ws_id"], role_env["run_id"]
    carol = await _login(auth_client, "carol@test.com")
    # viewer-in-BU: reads OK, writes 403.
    assert (await auth_client.get(f"/api/v1/workspaces/{ws_id}", headers=_hdr(carol))).status_code == 200
    assert (await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs", json={"command": "plan"}, headers=_hdr(carol)
    )).status_code == 403
    assert (await auth_client.post(
        f"/api/v1/runs/{run_id}/approve", headers=_hdr(carol)
    )).status_code == 403


@pytest.mark.asyncio
async def test_bu_operator_can_write(auth_client, role_env):
    ws_id = role_env["ws_id"]
    dave = await _login(auth_client, "dave@test.com")
    r = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs", json={"command": "plan"}, headers=_hdr(dave)
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_global_admin_exempt_from_bu_role_floor(auth_client, role_env):
    """A global admin who is only a viewer in the BU keeps admin powers there
    (documents the Model-2 exemption: admin is global, not per-BU)."""
    ws_id = role_env["ws_id"]
    erin = await _login(auth_client, "erin@test.com")
    # local:// workspace is deletable; delete is admin-gated.
    assert (await auth_client.delete(
        f"/api/v1/workspaces/{ws_id}", headers=_hdr(erin)
    )).status_code == 204


@pytest.mark.asyncio
async def test_membershipless_admin_still_reaches_global_endpoints(auth_client, seeded_users):
    """Soft-resolver regression: a non-superadmin admin with no BU membership
    must still reach require_role endpoints that don't use current_bu (users)."""
    admin = await _login(auth_client, "admin@test.com")
    # No X-Business-Unit, no membership → bu_role_cap returns None (no cap).
    r = await auth_client.get("/api/v1/users", headers={"Authorization": f"Bearer {admin}"})
    assert r.status_code == 200
