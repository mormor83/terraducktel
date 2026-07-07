"""Verify fail-loud invariant when CREDENTIAL_ENCRYPTION_KEY missing under EXECUTOR_ENABLED=true.

`_get_executor_service` was using a bare `except Exception:`
that silently swallowed the RuntimeError raised by `get_credential_encryption_key()`
when CREDENTIAL_ENCRYPTION_KEY is unset. That made encryption-key misconfig invisible
to operators triggering runs — runs would silently stay in PENDING.

This test pins the contract: a misconfigured executor MUST surface a 5xx, not silently
fall through to PENDING.
"""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_aws_account")

from app.models.business_unit import DEFAULT_BU_ID

import uuid

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_trigger_run_with_executor_enabled_but_no_encryption_key_returns_5xx(
    monkeypatch, _setup_db, seeded_users, operator_token
):
    """Misconfigured executor (CREDENTIAL_ENCRYPTION_KEY unset under EXECUTOR_ENABLED=true)
    must surface a 5xx, not silently fall through to PENDING.
    """
    from app.db import AsyncSessionLocal
    from app.main import app
    from app.models.workspace import Workspace

    ws_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id,
            name=f"misconfig-{ws_id[:8]}",
            repo_url="https://example.com/repo.git",
            tf_working_dir=".",
            aws_account_id="123456789012",
            environment="dev",
        ))
        await session.commit()

    # Force EXECUTOR_ENABLED=true and remove the encryption key so the helper raises RuntimeError.
    monkeypatch.setenv("EXECUTOR_ENABLED", "true")
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)

    # raise_app_exceptions=False so the FastAPI exception handler converts the
    # RuntimeError into a 500 instead of letting httpx re-raise it. In production
    # (uvicorn / real ASGI server), unhandled exceptions are similarly converted
    # to 500 responses by the default ServerErrorMiddleware — so this matches
    # operator-observed behavior.
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        response = await ac.post(
            f"/api/v1/workspaces/{ws_id}/runs",
            json={"command": "plan"},
            headers={"Authorization": f"Bearer {operator_token}"},
        )

    # Expect a 5xx (500) — fail-loud propagates rather than being silently swallowed.
    assert response.status_code >= 500, (
        f"Expected 5xx for fail-loud key, got {response.status_code}: {response.text}"
    )
