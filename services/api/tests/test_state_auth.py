"""Phase 1 critical: state router rejects unauthenticated callers (401).

Terraform HTTP backend does not support bearer tokens, so state endpoints use
a custom header: X-Terraducktel-State-Token (HCL: backend "http" { headers = {} }).

The expected token comes from env TERRADUCKTEL_STATE_TOKEN — fail-loud if unset.
"""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_aws_account")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool


_TEST_TOKEN = "test-state-token-do-not-use-in-prod"


@pytest_asyncio.fixture
async def state_auth_db():
    """Create all tables in an in-memory SQLite DB and patch app.db to use it."""
    from app.db import Base
    import app.models.config  # noqa: F401
    import app.models.state_lock  # noqa: F401
    import app.models.workspace  # noqa: F401
    import app.models.run  # noqa: F401
    import app.models.user  # noqa: F401
    import app.models.audit_log  # noqa: F401
    import app.models.drift_report  # noqa: F401
    import app.models.aws_account  # noqa: F401
    import app.models.run_step  # noqa: F401

    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    import app.db as db_mod
    original_engine = db_mod.engine
    original_factory = db_mod.AsyncSessionLocal
    db_mod.engine = test_engine
    db_mod.AsyncSessionLocal = test_session_factory

    yield test_session_factory

    db_mod.engine = original_engine
    db_mod.AsyncSessionLocal = original_factory
    await test_engine.dispose()


@pytest_asyncio.fixture
async def state_client(state_auth_db):
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


async def test_get_state_no_token_401(state_client):
    """GET /api/v1/state/{ws} without X-Terraducktel-State-Token → 401."""
    response = await state_client.get("/api/v1/state/some-ws")
    assert response.status_code == 401, response.text


async def test_get_state_wrong_token_401(state_client):
    """GET with wrong X-Terraducktel-State-Token → 401."""
    response = await state_client.get(
        "/api/v1/state/some-ws",
        headers={"X-Terraducktel-State-Token": "definitely-wrong-token"},
    )
    assert response.status_code == 401, response.text


async def test_get_state_valid_token_404_when_missing(state_client):
    """GET with valid token on a missing workspace → 404.

    Terraform's HTTP backend treats 404 as "no state yet, will create on
    first write". 200 + empty JSON (the old behavior) was parsed as a
    corrupted state file and broke `terraform init` on fresh workspaces,
    most visibly on destroy where the run died with "state file does not
    have a 'version' attribute".
    """
    response = await state_client.get(
        "/api/v1/state/missing-ws",
        headers={"X-Terraducktel-State-Token": _TEST_TOKEN},
    )
    assert response.status_code == 404, response.text


async def test_put_state_no_token_401(state_client):
    """POST /api/v1/state/{ws} without token → 401."""
    response = await state_client.post(
        "/api/v1/state/some-ws",
        json={"version": 4, "resources": []},
    )
    assert response.status_code == 401, response.text


async def test_lock_state_no_token_401(state_client):
    """POST /api/v1/state/{ws}/lock without token → 401."""
    response = await state_client.post(
        "/api/v1/state/some-ws/lock",
        json={"ID": "lock-123"},
    )
    assert response.status_code == 401, response.text


async def test_unlock_state_no_token_401(state_client):
    """DELETE /api/v1/state/{ws}/lock without token → 401."""
    response = await state_client.delete("/api/v1/state/some-ws/lock")
    assert response.status_code == 401, response.text


async def test_basic_auth_password_accepted(state_client):
    """Phase-2: Terraform's http backend sends HTTP Basic where the password
    equals TERRADUCKTEL_STATE_TOKEN (TF_HTTP_PASSWORD). State routes must accept
    this — proven by getting PAST auth: a missing workspace yields 404, not the
    401 an unauthenticated / wrong-password caller gets. (The 404-on-missing
    contract itself is covered by test_get_state_valid_token_404_when_missing;
    returning 200 + empty would corrupt Terraform on a fresh workspace.)
    """
    import base64
    creds = base64.b64encode(f"terraducktel:{_TEST_TOKEN}".encode()).decode()
    response = await state_client.get(
        "/api/v1/state/missing-ws",
        headers={"Authorization": f"Basic {creds}"},
    )
    assert response.status_code == 404, response.text


async def test_basic_auth_wrong_password_401(state_client):
    """Wrong Basic Auth password → 401 (constant-time compare; can't probe length)."""
    import base64
    creds = base64.b64encode(b"terraducktel:definitely-wrong").decode()
    response = await state_client.get(
        "/api/v1/state/missing-ws",
        headers={"Authorization": f"Basic {creds}"},
    )
    assert response.status_code == 401, response.text


async def test_basic_auth_malformed_header_401(state_client):
    """Malformed Basic Auth header → 401 (no 500 from base64 decode failure)."""
    response = await state_client.get(
        "/api/v1/state/missing-ws",
        headers={"Authorization": "Basic !!not-base64!!"},
    )
    assert response.status_code == 401, response.text


async def test_no_token_env_returns_503(monkeypatch, state_client):
    """When TERRADUCKTEL_STATE_TOKEN is unset, state route returns 503 (not 500).

    `require_state_token` raised RuntimeError when env var
    was missing, which FastAPI surfaced as a 500 — confusing for the operator. Should
    instead surface a structured 503 "State token not configured".
    """
    monkeypatch.delenv("TERRADUCKTEL_STATE_TOKEN", raising=False)
    response = await state_client.get("/api/v1/state/some-workspace-id")
    assert response.status_code == 503, response.text
    assert "not configured" in response.text.lower()


# ─── per-workspace run-token scoping ────────────────────────────────


async def test_run_token_basic_auth_own_workspace_passes(state_client):
    """A run-scoped token (type=run) via Basic auth authenticates for its OWN
    workspace — proven by getting past auth to the 404-on-missing contract."""
    import base64
    from app.auth.jwt import create_run_token
    tok = create_run_token("u1", "u@test.com", run_id="r1",
                           workspace_id="ws-A", business_unit_id="bu1")
    creds = base64.b64encode(f"terraducktel:{tok}".encode()).decode()
    r = await state_client.get("/api/v1/state/ws-A",
                               headers={"Authorization": f"Basic {creds}"})
    assert r.status_code == 404, r.text  # past auth (no state yet), not 401/403


async def test_run_token_basic_auth_other_workspace_403(state_client):
    """The  regression: a run token for ws-A cannot touch ws-B's state."""
    import base64
    from app.auth.jwt import create_run_token
    tok = create_run_token("u1", "u@test.com", run_id="r1",
                           workspace_id="ws-A", business_unit_id="bu1")
    creds = base64.b64encode(f"terraducktel:{tok}".encode()).decode()
    for path in ("/api/v1/state/ws-B", "/api/v1/state/ws-B/lock"):
        method = state_client.get if path.endswith("ws-B") else state_client.post
        r = await method(path, headers={"Authorization": f"Basic {creds}"})
        assert r.status_code == 403, f"{path}: {r.status_code} {r.text}"


async def test_access_jwt_as_basic_password_rejected(state_client):
    """Only a run-scoped token (or the global token) is a state credential — a
    normal user access JWT as the Basic password must not authenticate."""
    import base64
    from app.auth.jwt import create_access_token
    tok = create_access_token("u1", "u@test.com", "admin", is_superadmin=True)
    creds = base64.b64encode(f"terraducktel:{tok}".encode()).decode()
    r = await state_client.get("/api/v1/state/ws-A",
                               headers={"Authorization": f"Basic {creds}"})
    assert r.status_code == 401, r.text
