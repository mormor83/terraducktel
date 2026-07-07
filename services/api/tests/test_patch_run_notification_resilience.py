"""patch_run response stays 200 even when notification raises; DB transition persists.

`patch_run` previously called the notification service
BEFORE committing the FSM transition, with no try/except wrapper. A transient Slack
or SMTP outage therefore caused the entire PATCH to 500 and the FSM transition to
roll back. The fix:
  1. Commit FIRST.
  2. Wrap notification in try/except so transient outages don't surface to the caller.
"""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_aws_account")

from app.models.business_unit import DEFAULT_BU_ID

import uuid

import pytest


@pytest.mark.asyncio
async def test_patch_run_returns_200_when_notification_fails(
    monkeypatch, auth_client, seeded_users, operator_token, _setup_db
):
    from app.models.workspace import Workspace
    from app.models.run import Run, RunStatus

    factory = _setup_db
    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id,
            name=f"resilience-{ws_id[:8]}",
            repo_url="https://example.com/repo.git",
            tf_working_dir=".",
            aws_account_id="123456789012",
            environment="dev",
        ))
        session.add(Run(
            id=run_id,
            workspace_id=ws_id,
            command="plan",
            status=RunStatus.PLANNED,
            plan_output="example plan",
        ))
        await session.commit()

    # Force the notification module to raise a TRANSIENT outage. The narrow except
    # in runs.py catches (httpx.RequestError, smtplib.SMTPException, OSError) — those
    # are the legitimate transient classes; bare RuntimeError is intentionally NOT
    # caught (those are config bugs, not outages).
    import httpx

    from app.routers import runs as runs_router

    async def _boom(*a, **kw):
        raise httpx.ConnectError("simulated Slack outage")

    monkeypatch.setattr(runs_router, "send_plan_approval_notification", _boom)

    response = await auth_client.patch(
        f"/api/v1/runs/{run_id}",
        json={"status": "awaiting_approval"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "awaiting_approval"

    # Re-read the run to confirm DB committed.
    async with factory() as session:
        from sqlalchemy import select

        r = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
        assert r.status == RunStatus.AWAITING_APPROVAL


@pytest.mark.asyncio
async def test_patch_run_uses_module_session_factory(
    monkeypatch, auth_client, seeded_users, operator_token, _setup_db
):
    """NEW-C8 regression: patch_run must reach the test's monkeypatched AsyncSessionLocal.

    runs.py used to do `from app.db import AsyncSessionLocal` at the top, which
    captured the production sessionmaker reference at import time. The conftest
    fixture rebinds `app.db.AsyncSessionLocal` per test, but the captured symbol
    in runs.py was unaffected, so notifications opened a session against the
    original engine — invisible to tests, silently bypassing fixtures in CI.
    The fix: import the module and dereference at call site (`_db.AsyncSessionLocal()`).
    """
    factory = _setup_db
    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    from app.models.workspace import Workspace
    from app.models.run import Run, RunStatus

    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id, name=f"binding-{ws_id[:8]}",
            repo_url="https://example.com/repo.git", tf_working_dir=".",
            aws_account_id="123456789012", environment="dev",
        ))
        session.add(Run(
            id=run_id, workspace_id=ws_id, command="plan",
            status=RunStatus.PLANNED, plan_output="example plan",
        ))
        await session.commit()

    captured: dict = {}

    async def _capturing_notify(notify_session, *args, **kwargs):
        # Confirm the session we receive is bound to the test engine, not the
        # production engine. The conftest replaces app.db.engine, so the bind on
        # the test session must equal that test engine.
        from app import db as _db_mod
        captured["session_bind"] = notify_session.bind or notify_session.get_bind()
        captured["expected_bind"] = _db_mod.engine

    from app.routers import runs as runs_router
    monkeypatch.setattr(runs_router, "send_plan_approval_notification", _capturing_notify)

    response = await auth_client.patch(
        f"/api/v1/runs/{run_id}",
        json={"status": "awaiting_approval"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )

    assert response.status_code == 200, response.text
    assert "session_bind" in captured, "notification was never called"
    assert captured["session_bind"] is captured["expected_bind"], (
        "notification opened a session against the WRONG engine — "
        "patch_run is capturing AsyncSessionLocal at import time and bypassing "
        "test fixtures. Use `_db.AsyncSessionLocal()` not `AsyncSessionLocal()`."
    )
