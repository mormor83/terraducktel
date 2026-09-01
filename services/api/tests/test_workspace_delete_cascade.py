"""Workspace delete must clean up dependent rows (no FK CASCADE in the schema)."""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_aws_account")

from app.models.business_unit import DEFAULT_BU_ID

import uuid

import pytest
from sqlalchemy import select


async def _admin_token(client) -> str:
    """Log in as the seeded admin — the role workspace delete requires."""
    resp = await client.post(
        "/api/v1/auth/token",
        json={"email": "admin@test.com", "password": "password123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


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


@pytest.mark.asyncio
async def test_delete_workspace_cascades_to_run_jobs(
    auth_client, seeded_users, _setup_db
):
    """A run with a `run_jobs` row must not block the delete.

    `run_worker.create_job` writes a RunJob per executor phase, but
    delete_workspace only cleaned run_artifacts and run_steps. Deleting the
    parent Run then violated run_jobs.run_id and the whole transaction rolled
    back as a 500 — leaving the workspace and every run in place.

    Local dev launches the executor directly and never writes RunJob rows, so
    this only ever bit deployments running the queued worker.
    """
    from app.models.run import Run, RunStatus
    from app.models.run_job import RunJob
    from app.models.workspace import Workspace

    factory = _setup_db
    ws_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name=f"job-{ws_id[:8]}", aws_account_id="123456789012",
            region="us-east-1", environment="dev",
            tf_working_dir="account-123/us-east-1/job", repo_url=None,
        ))
        session.add(Run(
            id=run_id, workspace_id=ws_id, command="apply", status=RunStatus.APPLIED,
        ))
        session.add(RunJob(run_id=run_id, phase="apply"))
        await session.commit()

    token = await _admin_token(auth_client)
    resp = await auth_client.delete(
        f"/api/v1/workspaces/{ws_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204, resp.text

    async with factory() as session:
        assert await session.get(Workspace, ws_id) is None
        assert await session.get(Run, run_id) is None
        rows = (await session.execute(
            select(RunJob).where(RunJob.run_id == run_id)
        )).scalars().all()
        assert rows == [], "run_jobs left dangling"


@pytest.mark.asyncio
async def test_force_delete_of_a_git_synced_workspace_with_jobs_succeeds(
    auth_client, seeded_users, _setup_db
):
    """The exact path that 500'd in production: force-delete + run_jobs.

    Git-synced workspaces are the ones that need `?force=true`, and a
    git-synced workspace is by definition one that has really run — so it is
    the most likely to have run_jobs. This combination was unreachable from the
    old tests, which only force-deleted workspaces with no runs at all.
    """
    from app.models.run import Run, RunStatus
    from app.models.run_job import RunJob
    from app.models.workspace import Workspace

    factory = _setup_db
    ws_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name=f"gitjob-{ws_id[:8]}", aws_account_id="123456789012",
            region="us-east-1", environment="dev",
            tf_working_dir="account-123/us-east-1/gitjob",
            repo_url="https://github.com/Example/infra",
        ))
        session.add(Run(
            id=run_id, workspace_id=ws_id, command="destroy", status=RunStatus.APPLIED,
        ))
        session.add(RunJob(run_id=run_id, phase="plan"))
        session.add(RunJob(run_id=run_id, phase="apply"))
        await session.commit()

    token = await _admin_token(auth_client)
    resp = await auth_client.delete(
        f"/api/v1/workspaces/{ws_id}?force=true&delete_state=false",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204, resp.text

    async with factory() as session:
        assert await session.get(Workspace, ws_id) is None


@pytest.mark.asyncio
async def test_delete_workspace_releases_cloud_assets(
    auth_client, seeded_users, _setup_db
):
    """`cloud_assets.workspace_id` is nullable, so inventory rows are unlinked
    rather than deleted — losing the asset record would lose inventory history
    for resources that still exist in the cloud."""
    from app.models.cloud_asset import CloudAsset
    from app.models.workspace import Workspace

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name=f"asset-{ws_id[:8]}", aws_account_id="123456789012",
            region="us-east-1", environment="dev",
            tf_working_dir="account-123/us-east-1/asset", repo_url=None,
        ))
        await session.commit()
        session.add(CloudAsset(
            business_unit_id=DEFAULT_BU_ID,
            workspace_id=ws_id,
            asset_id="arn:aws:s3:::some-bucket",
            address="aws_s3_bucket.example",
            asset_type="aws_s3_bucket",
            provider="aws",
            region="us-east-1",
            account_id="123456789012",
        ))
        await session.commit()

    token = await _admin_token(auth_client)
    resp = await auth_client.delete(
        f"/api/v1/workspaces/{ws_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204, resp.text

    async with factory() as session:
        assert await session.get(Workspace, ws_id) is None
        assert (await session.execute(
            select(CloudAsset).where(CloudAsset.workspace_id == ws_id)
        )).scalars().all() == [], "cloud_assets still point at the deleted workspace"
        # Unlinked, not deleted: the bucket still exists in AWS, so its
        # inventory row should survive TDT forgetting about the workspace.
        surviving = (await session.execute(
            select(CloudAsset).where(CloudAsset.asset_id == "arn:aws:s3:::some-bucket")
        )).scalars().all()
        assert len(surviving) == 1
        assert surviving[0].workspace_id is None
