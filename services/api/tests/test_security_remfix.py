"""TDD RED tests for Phase 3 REM-FIX: critical security issues.

CRITICAL-1: JWT must raise RuntimeError when JWT_SECRET_KEY not configured.
CRITICAL-2: State lock endpoint must return 400 on malformed JSON body.
"""
import os
import importlib

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool


# ---------------------------------------------------------------------------
# CRITICAL-1: JWT secret must not fall back to hardcoded value
# ---------------------------------------------------------------------------

class TestJWTSecretRequired:
    """_get_secret() must raise RuntimeError when JWT_SECRET_KEY is not set."""

    def test_get_secret_raises_without_env_var(self, monkeypatch):
        """When JWT_SECRET_KEY is absent, _get_secret() must raise RuntimeError."""
        # Ensure env var is not set
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

        # Force module to reload so _JWT_SECRET picks up the missing env var
        import app.auth.jwt as jwt_mod
        monkeypatch.setattr(jwt_mod, "_JWT_SECRET", None)

        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY must be configured"):
            jwt_mod._get_secret()

    def test_get_secret_returns_value_when_env_var_set(self, monkeypatch):
        """When JWT_SECRET_KEY is set, _get_secret() must return it."""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-value-for-jwt")

        import app.auth.jwt as jwt_mod
        monkeypatch.setattr(jwt_mod, "_JWT_SECRET", None)

        result = jwt_mod._get_secret()
        assert result == "test-secret-value-for-jwt"


# ---------------------------------------------------------------------------
# CRITICAL-2: State lock endpoint must reject malformed JSON with 400
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def state_setup_db():
    """Create all tables in an in-memory SQLite DB for state tests."""
    from app.db import Base
    import app.models.config  # noqa: F401
    import app.models.state_lock  # noqa: F401
    import app.models.workspace  # noqa: F401
    import app.models.run  # noqa: F401
    import app.models.user  # noqa: F401

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
async def state_client(state_setup_db):
    """HTTP client wired to the FastAPI app for state endpoint tests."""
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


class TestStateLockMalformedBody:
    """POST /api/v1/state/{workspace_id}/lock must return 400 on malformed JSON."""

    async def test_malformed_json_returns_400(self, state_client):
        """Sending invalid JSON to the lock endpoint must return 400, not silently accept."""
        response = await state_client.post(
            "/api/v1/state/test-ws-123/lock",
            content=b"this is not valid json {{{",
            headers={
                "Content-Type": "application/json",
                "X-Terraducktel-State-Token": "test-state-token-do-not-use-in-prod",
            },
        )
        assert response.status_code == 400, (
            f"Expected 400 for malformed JSON body, got {response.status_code}"
        )

    async def test_empty_body_returns_400(self, state_client):
        """Sending empty body to lock endpoint must return 400."""
        response = await state_client.post(
            "/api/v1/state/test-ws-123/lock",
            content=b"",
            headers={
                "Content-Type": "application/json",
                "X-Terraducktel-State-Token": "test-state-token-do-not-use-in-prod",
            },
        )
        assert response.status_code == 400, (
            f"Expected 400 for empty body, got {response.status_code}"
        )
