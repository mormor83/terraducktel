"""Run-step timeline: rows are seeded on run creation; executor PATCHes them."""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_aws_account")

from app.models.business_unit import DEFAULT_BU_ID

import uuid

import pytest


@pytest.mark.asyncio
async def test_run_creation_seeds_canonical_step_list(
    auth_client, seeded_users, _setup_db
):
    """Triggering a `plan` run seeds 11 RunStep rows (Git Clone … Cost Estimation)."""
    from app.models.aws_account import AwsAccount
    from app.models.workspace import Workspace
    from app.services import aws_account_service as accs

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(AwsAccount(
            business_unit_id=DEFAULT_BU_ID,
            id=str(uuid.uuid4()),
            account_id="123456789012",
            name="test-acc",
            state_bucket="test-bucket",
            state_bucket_region="us-east-1",
            default_region="us-east-1",
            access_key_id_encrypted=accs.encrypt_secret("AKIATESTDUMMY"),
            secret_access_key_encrypted=accs.encrypt_secret("supersecret-dummy"),
        ))
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name="vpc",
            aws_account_id="123456789012", region="us-east-1", environment="dev",
            tf_working_dir="account-123/us-east-1/vpc",
            repo_url="https://example.com/x.git",
        ))
        await session.commit()

    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "operator@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]

    r = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs",
        json={"command": "plan"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]

    r = await auth_client.get(
        f"/api/v1/runs/{run_id}/steps",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    steps = r.json()
    # Pin to the actual canonical list rather than a hard-coded count so this
    # test moves in lockstep with `DEFAULT_STEP_NAMES` instead of going stale
    # every time a new step is inserted into the plan lifecycle.
    from app.models.run_step import DEFAULT_STEP_NAMES

    assert len(steps) == len(DEFAULT_STEP_NAMES)
    names = [s["name"] for s in steps]
    assert names == list(DEFAULT_STEP_NAMES)
    assert names[0] == "Git Clone"
    assert "Terraform Plan" in names
    # Position is contiguous and starts at 0.
    assert [s["position"] for s in steps] == list(range(len(DEFAULT_STEP_NAMES)))
    # All rows start as pending.
    assert all(s["status"] == "pending" for s in steps)


@pytest.mark.asyncio
async def test_apply_run_includes_approval_and_apply_steps(
    auth_client, seeded_users, _setup_db
):
    from app.models.workspace import Workspace

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name="vpc-apply",
            aws_account_id="123456789012", region="us-east-1", environment="dev",
            tf_working_dir=".", repo_url="https://example.com/x.git",
        ))
        await session.commit()

    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "operator@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]
    r = await auth_client.post(
        f"/api/v1/workspaces/{ws_id}/runs",
        json={"command": "apply"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    steps = (
        await auth_client.get(
            f"/api/v1/runs/{run_id}/steps",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).json()
    names = [s["name"] for s in steps]
    assert "Awaiting Approval" in names
    assert "Terraform Apply" in names
    assert names.index("Terraform Apply") > names.index("Awaiting Approval")


@pytest.mark.asyncio
async def test_patch_step_records_duration(auth_client, seeded_users, _setup_db):
    from app.models.workspace import Workspace

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name="dur",
            aws_account_id="123456789012", region="us-east-1", environment="dev",
            tf_working_dir=".", repo_url="https://example.com/x.git",
        ))
        await session.commit()

    r = await auth_client.post(
        "/api/v1/auth/token",
        json={"email": "operator@test.com", "password": "password123"},
    )
    token = r.json()["access_token"]
    run_id = (
        await auth_client.post(
            f"/api/v1/workspaces/{ws_id}/runs",
            json={"command": "plan"},
            headers={"Authorization": f"Bearer {token}"},
        )
    ).json()["id"]
    steps = (
        await auth_client.get(
            f"/api/v1/runs/{run_id}/steps",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).json()

    step_id = steps[0]["id"]
    r1 = await auth_client.patch(
        f"/api/v1/runs/{run_id}/steps/{step_id}",
        json={"status": "running"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "running"
    assert r1.json()["started_at"] is not None

    r2 = await auth_client.patch(
        f"/api/v1/runs/{run_id}/steps/{step_id}",
        json={"status": "success", "output": "Cloned 12 objects"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "success"
    assert body["output"] == "Cloned 12 objects"
    assert body["duration_seconds"] is not None
    assert body["duration_seconds"] >= 0
