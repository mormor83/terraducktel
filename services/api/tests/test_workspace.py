import pytest
from sqlalchemy import select

from app.models.business_unit import DEFAULT_BU_ID

# Workspace.business_unit_id is NOT NULL; seed the default BU so the model
# inserts below satisfy it (and the router test resolves X-Business-Unit).
pytestmark = pytest.mark.usefixtures("default_bu")


class TestWorkspaceModel:
    async def test_create_workspace(self, db_session):
        from app.models.workspace import Workspace
        ws = Workspace(
            business_unit_id=DEFAULT_BU_ID,
            name="test-workspace",
            aws_account_id="123456789012",
            environment="dev",
            region="us-east-1",
            repo_url="https://forgejo.local/org/infra.git",
            tf_working_dir="envs/dev/vpc",
        )
        db_session.add(ws)
        await db_session.flush()
        result = await db_session.get(Workspace, ws.id)
        assert result is not None
        assert result.name == "test-workspace"

    async def test_workspace_state_path(self, db_session):
        from app.models.workspace import Workspace
        ws = Workspace(
            name="vpc",
            aws_account_id="123456789012",
            environment="prod",
            region="us-east-1",
        )
        # Phase-6: state_path now includes region for per-leaf isolation across regions.
        assert ws.state_path == "tfstate/123456789012/us-east-1/prod/vpc/terraform.tfstate"

    async def test_create_run(self, db_session):
        from app.models.workspace import Workspace
        from app.models.run import Run, RunStatus
        ws = Workspace(business_unit_id=DEFAULT_BU_ID, name="ws1", aws_account_id="123456789012", environment="dev", region="us-east-1")
        db_session.add(ws)
        await db_session.flush()
        run = Run(workspace_id=ws.id, triggered_by=None, command="plan")
        db_session.add(run)
        await db_session.flush()
        assert run.status == RunStatus.PENDING
        assert run.workspace_id == ws.id


@pytest.mark.asyncio
async def test_state_aws_account_id_override_validation(
    auth_client, seeded_users, _setup_db
):
    """PUT /workspaces/{id} with a state_aws_account_id pointing at an
    AWS account that doesn't exist in the workspace's BU must 422 —
    silent fallback to legacy global creds is the exact behavior the
    override exists to fix. Empty string clears the override; null is
    stored when cleared."""
    from app.models.business_unit import DEFAULT_BU_ID
    from app.models.workspace import Workspace
    from app.db import AsyncSessionLocal

    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}", "X-Business-Unit": "default"}

    async with AsyncSessionLocal() as s:
        ws = Workspace(
            business_unit_id=DEFAULT_BU_ID,
            name="cloudflare-tenant",
            aws_account_id="global",
            region="global",
            environment="dev",
            tf_working_dir="cloudflare/tenant",
        )
        s.add(ws)
        await s.commit()
        ws_id = ws.id

    # Bogus account id → 422 + helpful detail.
    bad = await auth_client.put(
        f"/api/v1/workspaces/{ws_id}",
        json={"state_aws_account_id": "999999999999"},
        headers=h,
    )
    assert bad.status_code == 422, bad.text
    assert "not a registered AWS account" in bad.json()["detail"]

    # Empty string clears the override → response carries null.
    clear = await auth_client.put(
        f"/api/v1/workspaces/{ws_id}",
        json={"state_aws_account_id": ""},
        headers=h,
    )
    assert clear.status_code == 200, clear.text
    assert clear.json()["state_aws_account_id"] is None


@pytest.mark.asyncio
async def test_create_duplicate_workspace_returns_409(
    auth_client, default_aws_account, _setup_db
):
    """Creating a second workspace with the same identity tuple (account,
    region, environment, tf_working_dir) in a BU must return a clean 409 —
    not a raw 500 from the unique-constraint violation. Matters most for
    CLI/automation callers (admin API keys can now create workspaces)."""
    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "password123"},
    )
    h = {"Authorization": f"Bearer {r.json()['access_token']}", "X-Business-Unit": "default"}
    body = {
        "name": "dup-a",
        "environment": "dev",
        "aws_account_id": default_aws_account,
        "region": "us-east-1",
        "tf_working_dir": "envs/dup",
    }

    first = await auth_client.post("/api/v1/workspaces", json=body, headers=h)
    assert first.status_code == 201, first.text

    # Same identity tuple, different display name → 409, not 500.
    dup = await auth_client.post(
        "/api/v1/workspaces", json={**body, "name": "dup-b"}, headers=h
    )
    assert dup.status_code == 409, dup.text
    assert "already exists" in dup.json()["detail"]
