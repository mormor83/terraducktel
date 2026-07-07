"""Phase-3 HIGH hardening invariants.

H1: PLANNED → CANCELLED via POST /api/v1/runs/{id}/cancel
H2: operator-PATCH cannot directly transition into APPLYING (only /approve can)
    and cannot transition into APPLIED unless current state is APPLYING.
H4: state.py:get_state surfaces real S3 errors as 503, NoSuchKey as empty {}.
"""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_aws_account")

from app.models.business_unit import DEFAULT_BU_ID

import uuid

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_cancel_run_from_planned_succeeds(
    auth_client, seeded_users, operator_token, _setup_db
):
    from app.models.run import Run, RunStatus
    from app.models.workspace import Workspace

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name=f"cancel-{ws_id[:8]}",
            repo_url="https://example.com/repo.git", tf_working_dir=".",
            aws_account_id="123456789012", environment="dev",
        ))
        session.add(Run(
            id=run_id, workspace_id=ws_id, command="plan", status=RunStatus.PLANNED,
        ))
        await session.commit()

    response = await auth_client.post(
        f"/api/v1/runs/{run_id}/cancel",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_run_from_applying_409(
    auth_client, seeded_users, operator_token, _setup_db
):
    """Once APPLYING, cancel must 409 — runs cannot be cancelled mid-apply."""
    from app.models.run import Run, RunStatus
    from app.models.workspace import Workspace

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name=f"cancel409-{ws_id[:8]}",
            repo_url="https://example.com/repo.git", tf_working_dir=".",
            aws_account_id="123456789012", environment="dev",
        ))
        session.add(Run(
            id=run_id, workspace_id=ws_id, command="apply", status=RunStatus.APPLYING,
        ))
        await session.commit()

    response = await auth_client.post(
        f"/api/v1/runs/{run_id}/cancel",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_patch_run_cannot_directly_set_applying(
    auth_client, seeded_users, operator_token, _setup_db
):
    """H2: operator-PATCH must reject status=applying — only /approve route can."""
    from app.models.run import Run, RunStatus
    from app.models.workspace import Workspace

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name=f"h2-{ws_id[:8]}",
            repo_url="https://example.com/repo.git", tf_working_dir=".",
            aws_account_id="123456789012", environment="dev",
        ))
        session.add(Run(
            id=run_id, workspace_id=ws_id, command="apply",
            status=RunStatus.AWAITING_APPROVAL,
        ))
        await session.commit()

    response = await auth_client.patch(
        f"/api/v1/runs/{run_id}",
        json={"status": "applying"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert response.status_code == 403, response.text
    # The hardening rule still stands post-4-eyes removal: only the /approve
    # route can move a run into APPLYING, never a raw PATCH.
    assert "approve" in response.text.lower()


@pytest.mark.asyncio
async def test_patch_run_applied_requires_applying(
    auth_client, seeded_users, operator_token, _setup_db
):
    """H2: operator-PATCH cannot set status=applied unless run is APPLYING."""
    from app.models.run import Run, RunStatus
    from app.models.workspace import Workspace

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name=f"applied-{ws_id[:8]}",
            repo_url="https://example.com/repo.git", tf_working_dir=".",
            aws_account_id="123456789012", environment="dev",
        ))
        # PLANNED → APPLIED would also be FSM-illegal, but we want to confirm
        # the auth-layer 403 fires before the FSM ValueError.
        session.add(Run(
            id=run_id, workspace_id=ws_id, command="apply",
            status=RunStatus.AWAITING_APPROVAL,
        ))
        await session.commit()

    response = await auth_client.patch(
        f"/api/v1/runs/{run_id}",
        json={"status": "applied"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_state_get_503_when_s3_unreachable(monkeypatch, _setup_db):
    """H4: real S3 errors surface as 503 (not silently as empty {})."""
    from httpx import AsyncClient, ASGITransport

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    from app.models.workspace import Workspace
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name=f"s3-503-{ws_id[:8]}",
            repo_url="https://example.com/repo.git", tf_working_dir=".",
            aws_account_id="123456789012", environment="dev",
        ))
        await session.commit()

    # Force the S3 client to raise a real (non-NoSuchKey) error.
    from app.routers import state as state_router

    class _ExplodingS3:
        bucket = "exploding"

        def get_state_at(self, *a, **kw):
            raise RuntimeError("simulated S3 outage")

    async def _exploding_service_for(_ws, _db):
        return _ExplodingS3(), "irrelevant/key"

    monkeypatch.setattr(state_router, "_service_for", _exploding_service_for)

    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/state/{ws_id}",
            headers={"X-Terraducktel-State-Token": "test-state-token-do-not-use-in-prod"},
        )

    assert response.status_code == 503, response.text
    assert "unavailable" in response.text.lower()


@pytest.mark.asyncio
async def test_drift_alert_swallows_slack_outage(monkeypatch, _setup_db):
    """H5: send_drift_alert must not raise when Slack is unreachable."""
    import httpx

    from app.services import notification_service as ns

    # Pre-seed the Slack webhook URL into the config table so the code path runs.
    from app.services.config_service import ConfigService

    factory = _setup_db
    async with factory() as session:
        cs = ConfigService(session, b"test_key_exactly_32_bytes_long!!")
        await cs.set("slack.webhook_url", "https://example.invalid/webhook")
        await session.commit()

    class _Boom:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw):
            raise httpx.ConnectError("simulated Slack outage")

    monkeypatch.setattr(ns.httpx, "AsyncClient", _Boom)

    async with factory() as session:
        # Should not raise — best-effort.
        await ns.send_drift_alert(session, "vpc", "drift summary text")
