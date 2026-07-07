"""Workspace delete must clean up dependent rows (no FK CASCADE in the schema)."""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_aws_account")

from app.models.business_unit import DEFAULT_BU_ID

import uuid

import pytest


@pytest.mark.asyncio
async def test_delete_workspace_cascades_to_runs_and_locks(
    auth_client, seeded_users, _setup_db
):
    from app.models.run import Run, RunStatus
    from app.models.state_lock import StateLockEntry
    from app.models.workspace import Workspace
    from app.services.state_service import StateLockService

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    async with factory() as session:
        # `repo_url=None` makes this a local-only workspace. The new git-sync
        # gate on DELETE refuses to remove workspaces with a real repo_url
        # because they'd just re-sync; cascade behaviour is still tested on
        # the local-only path.
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name=f"del-{ws_id[:8]}", aws_account_id="123456789012",
            region="us-east-1", environment="dev",
            tf_working_dir="account-123/us-east-1/del", repo_url=None,
        ))
        session.add(Run(
            id=run_id, workspace_id=ws_id, command="plan", status=RunStatus.PLANNED,
        ))
        # Acquire a state lock so the StateLockEntry row also needs cleanup.
        await session.commit()
        svc = StateLockService(session)
        await svc.acquire_lock(ws_id, run_id)
        await session.commit()

    # Login as admin and DELETE the workspace.
    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]
    r = await auth_client.delete(
        f"/api/v1/workspaces/{ws_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 204, r.text

    # The workspace, its runs, and any state-lock row are gone.
    from sqlalchemy import select

    async with factory() as session:
        ws = await session.get(Workspace, ws_id)
        assert ws is None
        runs = (await session.execute(select(Run).where(Run.workspace_id == ws_id))).scalars().all()
        assert len(runs) == 0
        locks = (
            await session.execute(select(StateLockEntry).where(StateLockEntry.workspace_id == ws_id))
        ).scalars().all()
        assert len(locks) == 0


@pytest.mark.asyncio
async def test_delete_workspace_refuses_git_synced(auth_client, seeded_users, _setup_db):
    """Workspaces with a real repo_url cannot be manually deleted — they would
    just be re-imported on the next GitHub sync. The API must return 409 and
    the row must remain (regression coverage for the git-sync gate)."""
    from app.models.workspace import Workspace

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name=f"gh-{ws_id[:8]}", aws_account_id="123456789012",
            region="us-east-1", environment="dev",
            tf_working_dir="account-123/us-east-1/gh",
            repo_url="https://github.com/example/infra.git",
        ))
        await session.commit()

    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]
    r = await auth_client.delete(
        f"/api/v1/workspaces/{ws_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409, r.text
    assert "synced from a Git repository" in r.json()["detail"]

    async with factory() as session:
        ws = await session.get(Workspace, ws_id)
        assert ws is not None, "git-synced workspace must survive the delete attempt"


@pytest.mark.asyncio
async def test_delete_workspace_allows_local_prefix(auth_client, seeded_users, _setup_db):
    """`repo_url` of `local://...` is treated as local-only — delete must work."""
    from app.models.workspace import Workspace

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name=f"local-{ws_id[:8]}", aws_account_id="123456789012",
            region="us-east-1", environment="dev",
            tf_working_dir="account-123/us-east-1/local",
            repo_url="local:///host/repos/foo",
        ))
        await session.commit()

    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]
    r = await auth_client.delete(
        f"/api/v1/workspaces/{ws_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 204, r.text
