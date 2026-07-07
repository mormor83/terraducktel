"""The executor's run-scoped token must be confined to its own run's
callback routes and can never act as a (super)admin elsewhere."""
from __future__ import annotations

import uuid

import pytest

from app.auth.jwt import create_run_token, decode_token
from app.models.business_unit import BusinessUnit, UserBusinessUnit
from app.models.run import Run, RunStatus
from app.models.user import User
from app.models.workspace import Workspace

BU_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


@pytest.fixture
async def run_env(_setup_db):
    """A BU, a superadmin triggerer, a workspace, and two runs in that BU."""
    factory = _setup_db
    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    other_run_id = str(uuid.uuid4())
    su_id = str(uuid.uuid4())
    async with factory() as s:
        s.add(BusinessUnit(id=BU_ID, slug="bu-c", name="BU C"))
        s.add(User(id=su_id, email="su@test.com", hashed_password="!x!",
                   role="admin", auth_provider="local", is_superadmin=True))
        s.add(UserBusinessUnit(user_id=su_id, business_unit_id=BU_ID, role="operator"))
        s.add(Workspace(id=ws_id, name="rt-ws", business_unit_id=BU_ID,
                        repo_url="local://rt", tf_working_dir=".",
                        aws_account_id="123456789012", region="us-east-1",
                        environment="dev"))
        s.add(Run(id=run_id, workspace_id=ws_id, command="plan",
                  status=RunStatus.RUNNING, triggered_by=su_id))
        s.add(Run(id=other_run_id, workspace_id=ws_id, command="plan",
                  status=RunStatus.AWAITING_APPROVAL, triggered_by=su_id))
        await s.commit()
    token = create_run_token(su_id, "su@test.com", run_id=run_id,
                             workspace_id=ws_id, business_unit_id=BU_ID)
    return {"ws_id": ws_id, "run_id": run_id, "other_run_id": other_run_id,
            "su_id": su_id, "token": token}


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_run_token_claims_have_no_role_or_superadmin():
    payload = decode_token(create_run_token(
        "u1", "u@test.com", run_id="r1", workspace_id="w1", business_unit_id="b1"))
    assert payload["type"] == "run"
    assert payload["run_id"] == "r1" and payload["workspace_id"] == "w1"
    assert payload["business_unit_id"] == "b1" and payload["sub"] == "u1"
    assert "role" not in payload and "is_superadmin" not in payload


@pytest.mark.asyncio
async def test_run_token_allowed_on_own_callbacks(auth_client, run_env):
    t, run_id = run_env["token"], run_env["run_id"]
    # PATCH plan output on its own run.
    r = await auth_client.patch(f"/api/v1/runs/{run_id}",
                                json={"plan_output": "hello"}, headers=_hdr(t))
    assert r.status_code == 200, r.text
    # GET steps on its own run.
    r = await auth_client.get(f"/api/v1/runs/{run_id}/steps", headers=_hdr(t))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_run_token_rejected_for_other_run(auth_client, run_env):
    t, other = run_env["token"], run_env["other_run_id"]
    assert (await auth_client.patch(f"/api/v1/runs/{other}",
            json={"plan_output": "x"}, headers=_hdr(t))).status_code == 403
    assert (await auth_client.get(f"/api/v1/runs/{other}/steps",
            headers=_hdr(t))).status_code == 403


@pytest.mark.asyncio
async def test_run_token_rejected_outside_allowlist(auth_client, run_env):
    """approve/cancel/get-run/workspaces/users are NOT executor callbacks."""
    t, run_id = run_env["token"], run_env["run_id"]
    assert (await auth_client.post(f"/api/v1/runs/{run_id}/approve",
            headers=_hdr(t))).status_code == 403
    assert (await auth_client.post(f"/api/v1/runs/{run_id}/cancel",
            headers=_hdr(t))).status_code == 403
    # get_run is not in the callback allowlist.
    assert (await auth_client.get(f"/api/v1/runs/{run_id}",
            headers=_hdr(t))).status_code == 403
    assert (await auth_client.get("/api/v1/workspaces", headers=_hdr(t))).status_code == 403
    assert (await auth_client.get("/api/v1/users", headers=_hdr(t))).status_code == 403


@pytest.mark.asyncio
async def test_superadmin_triggered_run_token_is_not_superadmin(auth_client, run_env):
    """Even though the triggerer is a superadmin, the run token cannot approve
    its run or reach cross-BU/admin surfaces."""
    t, run_id = run_env["token"], run_env["run_id"]
    # approve requires operator+ and is off-allowlist → 403, not a superadmin bypass.
    assert (await auth_client.post(f"/api/v1/runs/{run_id}/approve",
            headers=_hdr(t))).status_code == 403
    # Admin-gated, off-allowlist surface.
    assert (await auth_client.get("/api/v1/aws-accounts", headers=_hdr(t))).status_code == 403
