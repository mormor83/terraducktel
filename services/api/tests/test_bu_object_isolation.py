"""Cross-BU object access is blocked on by-ID endpoints (tenant isolation).

Regression test for the IDOR where every by-ID workspace/run endpoint did a
bare `db.get(...)` with no BU check, so a member of BU-A could read, modify,
delete, trigger, or approve BU-B's resources just by knowing the id. The list
endpoints filtered by BU, but the object endpoints did not.

A cross-BU access must return 404 (not 403) so we don't leak existence. The
owning BU's member still gets 200, and a superadmin (`X-Business-Unit: all`)
bypasses the scope.
"""
from __future__ import annotations

import uuid

import pytest

from app.auth.jwt import hash_password
from app.models.business_unit import BusinessUnit, UserBusinessUnit
from app.models.run import Run, RunStatus
from app.models.user import User
from app.models.workspace import Workspace

BU_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
BU_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


async def _login(client, email: str) -> str:
    resp = await client.post(
        "/api/v1/auth/token", json={"email": email, "password": "password123"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
async def two_bus(_setup_db):
    """Two BUs, an operator in each, a superadmin, and a workspace+run in BU-A.

    Returns a dict with tokens and the BU-A workspace/run ids.
    """
    factory = _setup_db
    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    async with factory() as s:
        s.add(BusinessUnit(id=BU_A, slug="bu-a", name="BU A"))
        s.add(BusinessUnit(id=BU_B, slug="bu-b", name="BU B"))

        alice = User(id=str(uuid.uuid4()), email="alice@test.com",
                     hashed_password=hash_password("password123"),
                     role="operator", auth_provider="local")
        # bob is an admin *in his own BU* — proves the scope check (404) fires
        # even for a privileged caller, not just an under-privileged one (403).
        bob = User(id=str(uuid.uuid4()), email="bob@test.com",
                   hashed_password=hash_password("password123"),
                   role="admin", auth_provider="local")
        root = User(id=str(uuid.uuid4()), email="root@test.com",
                    hashed_password=hash_password("password123"),
                    role="admin", auth_provider="local", is_superadmin=True)
        s.add_all([alice, bob, root])
        s.add(UserBusinessUnit(user_id=alice.id, business_unit_id=BU_A, role="operator"))
        s.add(UserBusinessUnit(user_id=bob.id, business_unit_id=BU_B, role="operator"))

        s.add(Workspace(
            id=ws_id, name="iso-ws", business_unit_id=BU_A,
            repo_url="local://iso", tf_working_dir=".",
            aws_account_id="123456789012", region="us-east-1", environment="dev",
        ))
        s.add(Run(id=run_id, workspace_id=ws_id, command="plan",
                  status=RunStatus.AWAITING_APPROVAL))
        await s.commit()
    return {"ws_id": ws_id, "run_id": run_id}


@pytest.mark.asyncio
async def test_cross_bu_workspace_access_is_404(auth_client, two_bus):
    ws_id = two_bus["ws_id"]
    alice = await _login(auth_client, "alice@test.com")  # BU-A (owner)
    bob = await _login(auth_client, "bob@test.com")       # BU-B (stranger)

    a_hdr = {"Authorization": f"Bearer {alice}", "X-Business-Unit": "bu-a"}
    b_hdr = {"Authorization": f"Bearer {bob}", "X-Business-Unit": "bu-b"}

    # Owner sees it; stranger gets 404 (existence not leaked).
    assert (await auth_client.get(f"/api/v1/workspaces/{ws_id}", headers=a_hdr)).status_code == 200
    assert (await auth_client.get(f"/api/v1/workspaces/{ws_id}", headers=b_hdr)).status_code == 404

    # Stranger cannot modify or delete another BU's workspace.
    assert (await auth_client.put(
        f"/api/v1/workspaces/{ws_id}", headers=b_hdr, json={"region": "eu-west-1"}
    )).status_code == 404
    assert (await auth_client.delete(
        f"/api/v1/workspaces/{ws_id}?force=true", headers=b_hdr
    )).status_code == 404


@pytest.mark.asyncio
async def test_cross_bu_run_access_is_404(auth_client, two_bus):
    run_id = two_bus["run_id"]
    alice = await _login(auth_client, "alice@test.com")
    bob = await _login(auth_client, "bob@test.com")
    a_hdr = {"Authorization": f"Bearer {alice}", "X-Business-Unit": "bu-a"}
    b_hdr = {"Authorization": f"Bearer {bob}", "X-Business-Unit": "bu-b"}

    # Owner reads the run; stranger gets 404 on read and on approve.
    assert (await auth_client.get(f"/api/v1/runs/{run_id}", headers=a_hdr)).status_code == 200
    assert (await auth_client.get(f"/api/v1/runs/{run_id}", headers=b_hdr)).status_code == 404
    assert (await auth_client.post(
        f"/api/v1/runs/{run_id}/approve", headers=b_hdr
    )).status_code == 404
    # Same for the owner the approve must NOT 404 (it reaches the FSM logic).
    assert (await auth_client.post(
        f"/api/v1/runs/{run_id}/approve", headers=a_hdr
    )).status_code != 404


@pytest.mark.asyncio
async def test_superadmin_bypasses_bu_scope(auth_client, two_bus):
    ws_id, run_id = two_bus["ws_id"], two_bus["run_id"]
    root = await _login(auth_client, "root@test.com")
    hdr = {"Authorization": f"Bearer {root}", "X-Business-Unit": "all"}

    # Superadmin with the "all" scope sees BU-A's objects from outside the BU.
    assert (await auth_client.get(f"/api/v1/workspaces/{ws_id}", headers=hdr)).status_code == 200
    assert (await auth_client.get(f"/api/v1/runs/{run_id}", headers=hdr)).status_code == 200


@pytest.mark.asyncio
async def test_cross_bu_workspace_variables_is_404(auth_client, two_bus):
    """workspace variables must be BU-scoped. A stranger BU's member
    cannot read or write another tenant's workspace variables by id."""
    ws_id = two_bus["ws_id"]
    alice = await _login(auth_client, "alice@test.com")  # BU-A owner
    bob = await _login(auth_client, "bob@test.com")       # BU-B stranger (admin in BU-B)
    a_hdr = {"Authorization": f"Bearer {alice}", "X-Business-Unit": "bu-a"}
    b_hdr = {"Authorization": f"Bearer {bob}", "X-Business-Unit": "bu-b"}

    # Owner can create a var on the BU-A workspace.
    r = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/variables",
        json={"key": "region", "value": "us-east-1", "is_hcl": False},
        headers=a_hdr,
    )
    assert r.status_code == 201, r.text

    # Stranger BU cannot read or write those variables (404, existence hidden).
    assert (await auth_client.get(
        f"/api/v1/workspaces/{ws_id}/variables", headers=b_hdr
    )).status_code == 404
    assert (await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/variables",
        json={"key": "evil", "value": "x", "is_hcl": False},
        headers=b_hdr,
    )).status_code == 404


@pytest.mark.asyncio
async def test_cross_bu_aws_account_mutation_is_404(auth_client, two_bus, _setup_db):
    """AWS-account update/delete/test must be BU-scoped, even for a
    global-admin caller from another BU."""
    from app.models.aws_account import AwsAccount
    from app.services import aws_account_service as accs

    acc_pk = str(uuid.uuid4())
    async with _setup_db() as s:
        s.add(AwsAccount(
            id=acc_pk, business_unit_id=BU_A, account_id="123456789012",
            name="bu-a-acct", state_bucket="tf-state-bu-a",
            access_key_id_encrypted=accs.encrypt_secret("AKIAEXAMPLE"),
            secret_access_key_encrypted=accs.encrypt_secret("secret"),
        ))
        await s.commit()

    bob = await _login(auth_client, "bob@test.com")       # admin in BU-B
    root = await _login(auth_client, "root@test.com")     # superadmin
    b_hdr = {"Authorization": f"Bearer {bob}", "X-Business-Unit": "bu-b"}

    # BU-B admin cannot touch BU-A's account (404, not 200/204).
    assert (await auth_client.put(
        f"/api/v1/aws-accounts/{acc_pk}",
        json={"name": "hijacked"}, headers=b_hdr,
    )).status_code == 404
    assert (await auth_client.delete(
        f"/api/v1/aws-accounts/{acc_pk}", headers=b_hdr
    )).status_code == 404

    # Superadmin (all scope) still reaches it.
    all_hdr = {"Authorization": f"Bearer {root}", "X-Business-Unit": "all"}
    assert (await auth_client.delete(
        f"/api/v1/aws-accounts/{acc_pk}", headers=all_hdr
    )).status_code == 204
