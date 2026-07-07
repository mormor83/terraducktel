"""Drift detection API (Phase 7)."""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_aws_account")

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def workspace_id(auth_client, admin_token):
    create = await auth_client.post(
        "/api/v1/workspaces",
        json={
            "name": "drift-ws",
            "environment": "dev",
            "aws_account_id": "123456789012",
            "region": "us-east-1",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create.status_code == 201
    return create.json()["id"]


async def test_drift_report_created_on_scan(auth_client, admin_token, workspace_id):
    response = await auth_client.post(
        f"/api/v1/drift/{workspace_id}/scan",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 202
    body = response.json()
    assert "report_id" in body


async def test_drift_detected_creates_alert(auth_client, admin_token, workspace_id):
    with patch(
        "app.routers.drift.send_drift_alert",
        new_callable=AsyncMock,
    ) as mock_alert:
        await auth_client.post(
            f"/api/v1/drift/{workspace_id}/report",
            json={
                "workspace_id": workspace_id,
                "has_drift": True,
                "summary": "2 resources to add",
                "plan_output": "...",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        mock_alert.assert_called_once()


async def test_no_drift_does_not_alert(auth_client, admin_token, workspace_id):
    with patch(
        "app.routers.drift.send_drift_alert",
        new_callable=AsyncMock,
    ) as mock_alert:
        await auth_client.post(
            f"/api/v1/drift/{workspace_id}/report",
            json={
                "workspace_id": workspace_id,
                "has_drift": False,
                "summary": "No changes",
                "plan_output": "",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        mock_alert.assert_not_called()


async def test_workspace_includes_drift_status(auth_client, admin_token, workspace_id):
    r = await auth_client.get(
        f"/api/v1/workspaces/{workspace_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    assert "drift_status" in r.json()
    assert r.json()["drift_status"] in ("unknown", "clean", "drifted")


# ─── breakdown persistence + per-BU summary ──────────────────────────────────

_BREAKDOWN_BODY = {
    "has_drift": True,
    "summary": "drift",
    "plan_output": "...",
    "modified_count": 2,
    "untracked_count": 1,
    "deleted_count": 0,
    "mismatch_count": 3,
    "resources": [
        {"address": "aws_s3_bucket.a", "type": "aws_s3_bucket", "provider": "aws",
         "drift_type": "modified", "summary": "update"},
        {"address": "arn:aws:s3:::ghost", "type": "s3", "provider": "aws",
         "drift_type": "untracked", "summary": "live not in state"},
    ],
}


async def _post_breakdown(auth_client, admin_token, workspace_id):
    with patch("app.routers.drift.send_drift_alert", new_callable=AsyncMock):
        r = await auth_client.post(
            f"/api/v1/drift/{workspace_id}/report",
            json={"workspace_id": workspace_id, **_BREAKDOWN_BODY},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert r.status_code == 200, r.text


async def test_report_persists_breakdown_in_detail(auth_client, admin_token, workspace_id):
    await _post_breakdown(auth_client, admin_token, workspace_id)
    r = await auth_client.get(
        f"/api/v1/drift/{workspace_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["modified_count"] == 2
    assert body["untracked_count"] == 1
    assert body["mismatch_count"] == 3
    assert {res["drift_type"] for res in body["resources"]} == {"modified", "untracked"}


async def test_summary_aggregates_latest_report(auth_client, admin_token, workspace_id):
    await _post_breakdown(auth_client, admin_token, workspace_id)
    r = await auth_client.get(
        "/api/v1/drift/summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["modified_count"] == 2
    assert body["untracked_count"] == 1
    assert body["mismatch_count"] == 3
    assert body["workspaces_drifted"] >= 1
    row = next(w for w in body["by_workspace"] if w["workspace_id"] == workspace_id)
    assert row["modified_count"] == 2 and row["mismatch_count"] == 3


async def test_summary_excludes_other_bu_workspace(
    auth_client, admin_token, workspace_id, _setup_db
):
    """A workspace in a different BU must not appear in the caller's summary."""
    import uuid as _uuid

    from app.models.business_unit import BusinessUnit
    from app.models.workspace import Workspace

    other_ws_id = str(_uuid.uuid4())
    async with _setup_db() as session:
        session.add(BusinessUnit(id="other-bu", slug="other-bu", name="Other"))
        session.add(
            Workspace(
                id=other_ws_id,
                business_unit_id="other-bu",
                name="other-ws",
                environment="dev",
                aws_account_id="123456789012",
                region="us-east-1",
            )
        )
        await session.commit()

    # admin@test.com is a (non-superadmin) member of the default BU only, so a
    # no-header request resolves to the default BU and must exclude other-bu.
    r = await auth_client.get(
        "/api/v1/drift/summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    ids = {w["workspace_id"] for w in r.json()["by_workspace"]}
    assert other_ws_id not in ids
    assert workspace_id in ids


async def test_internal_aws_credentials_endpoint(auth_client, workspace_id):
    """Detector creds endpoint returns decrypted keys for the workspace account."""
    r = await auth_client.get(
        f"/api/v1/internal/workspaces/{workspace_id}/aws-credentials",
        headers={"X-Terraducktel-Internal-Token": "test-internal-token-do-not-use-in-prod"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["access_key_id"] == "AKIA123456789012"  # seeded ciphertext decrypts
    assert body["region"] == "us-east-1"


async def test_internal_endpoint_rejects_state_token(auth_client, workspace_id):
    """ regression: the state token (handed to every executor container)
    must NOT authenticate against /api/v1/internal/* — only the separate,
    executor-inaccessible internal token may. If this ever passes with a 200,
    the two tokens have been conflated back together and any workspace's
    Terraform/Helm run can read every tenant's AWS credentials again."""
    r = await auth_client.get(
        f"/api/v1/internal/workspaces/{workspace_id}/aws-credentials",
        headers={"X-Terraducktel-State-Token": "test-state-token-do-not-use-in-prod"},
    )
    assert r.status_code == 401

    r2 = await auth_client.get(
        "/api/v1/internal/workspaces",
        headers={"X-Terraducktel-State-Token": "test-state-token-do-not-use-in-prod"},
    )
    assert r2.status_code == 401
