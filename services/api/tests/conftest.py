import os
import uuid

# Tests OWN these env vars. Overwrite (not setdefault) so a developer with prod
# values exported in their shell can't silently sign tokens with prod secrets
# during pytest.
_TEST_JWT_SECRET = "test-secret-key-for-ci-not-production"
_TEST_ENCRYPTION_KEY = "test_key_exactly_32_bytes_long!!"
_TEST_STATE_TOKEN = "test-state-token-do-not-use-in-prod"
# Deliberately a DIFFERENT value than _TEST_STATE_TOKEN — tests should catch a
# regression where the two tokens get conflated back together.
_TEST_INTERNAL_TOKEN = "test-internal-token-do-not-use-in-prod"

# Guard: if a non-test-looking secret is already in env, refuse to run.
for _k, _expected_prefix in [
    ("JWT_SECRET_KEY", "test-"),
    ("CREDENTIAL_ENCRYPTION_KEY", "test_"),
    ("TERRADUCKTEL_STATE_TOKEN", "test-"),
    ("TERRADUCKTEL_INTERNAL_TOKEN", "test-"),
]:
    _pre = os.environ.get(_k)
    if _pre is not None and not _pre.startswith(_expected_prefix):
        raise RuntimeError(
            f"Refusing to run pytest: {_k} is set to a non-test value "
            f"(prefix '{_expected_prefix}' expected). "
            f"Unset it or use a test-prefixed value."
        )

os.environ["JWT_SECRET_KEY"] = _TEST_JWT_SECRET
os.environ["CREDENTIAL_ENCRYPTION_KEY"] = _TEST_ENCRYPTION_KEY
os.environ["TERRADUCKTEL_STATE_TOKEN"] = _TEST_STATE_TOKEN
os.environ["TERRADUCKTEL_INTERNAL_TOKEN"] = _TEST_INTERNAL_TOKEN

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool


@pytest_asyncio.fixture
async def _setup_db():
    """Create all tables in an in-memory SQLite DB and patch app.db to use it."""
    from app.db import Base, engine
    import app.models.config  # noqa: F401
    import app.models.state_lock  # noqa: F401
    import app.models.workspace  # noqa: F401
    import app.models.run  # noqa: F401
    import app.models.user  # noqa: F401
    import app.models.audit_log  # noqa: F401
    import app.models.drift_report  # noqa: F401
    import app.models.cloud_asset  # noqa: F401
    import app.models.inventory_ignore_rule  # noqa: F401
    import app.models.aws_account  # noqa: F401
    import app.models.azure_subscription  # noqa: F401
    import app.models.gcp_project  # noqa: F401
    import app.models.run_step  # noqa: F401
    import app.models.run_job  # noqa: F401
    import app.models.variable  # noqa: F401
    import app.models.user_presence  # noqa: F401
    import app.models.changelog_entry  # noqa: F401
    import app.models.k8s_cluster  # noqa: F401
    import app.models.business_unit  # noqa: F401
    import app.models.api_key  # noqa: F401
    import app.models.policy  # noqa: F401

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
async def auth_client(_setup_db):
    """HTTP client wired to the FastAPI app with a test DB."""
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def seeded_users(_setup_db):
    """Create admin, operator, and viewer users in the test DB."""
    from app.models.user import User
    from app.auth.jwt import hash_password

    factory = _setup_db
    users = {}
    async with factory() as session:
        for role in ("admin", "operator", "viewer"):
            u = User(
                id=str(uuid.uuid4()),
                email=f"{role}@test.com",
                hashed_password=hash_password("password123"),
                role=role,
                auth_provider="local",
            )
            session.add(u)
            users[role] = u
        await session.commit()
        for u in users.values():
            session.expunge(u)
    return users


@pytest_asyncio.fixture
async def default_bu(seeded_users, _setup_db):
    """Seed the default Business Unit + memberships for the seeded users.

    Most pre-tenancy tests assume a single-BU world: they don't send an
    `X-Business-Unit` header, so `current_bu` falls back to the caller's first
    membership. Without a membership that path 403s ("No business unit
    memberships"). Depending on this fixture (directly or via a module-level
    `pytest.mark.usefixtures("default_bu")`) gives the three seeded users a
    membership in the default BU so no-header requests resolve to it and
    BU-scoped creates can stamp `business_unit_id`.

    admin/operator → operator membership, viewer → viewer membership. The
    legacy `users.role` column still drives `require_role`; the membership role
    only matters for BU-scoped authorization. Returns DEFAULT_BU_ID.
    """
    from app.models.business_unit import (
        DEFAULT_BU_ID,
        DEFAULT_BU_SLUG,
        BusinessUnit,
        UserBusinessUnit,
    )

    factory = _setup_db
    async with factory() as session:
        session.add(BusinessUnit(id=DEFAULT_BU_ID, slug=DEFAULT_BU_SLUG, name="Default"))
        for role, u in seeded_users.items():
            session.add(
                UserBusinessUnit(
                    user_id=u.id,
                    business_unit_id=DEFAULT_BU_ID,
                    role=("viewer" if role == "viewer" else "operator"),
                )
            )
        await session.commit()
    return DEFAULT_BU_ID


@pytest_asyncio.fixture
async def default_aws_account(default_bu, _setup_db):
    """Register the common test AWS accounts in the default BU.

    `create_workspace` now validates that `aws_account_id` references an
    AwsAccount in the current BU (else 400). Pre-tenancy tests pass bare
    12-digit ids (`123456789012`, `000000000000`) without registering them.
    Seed both so those tests can create terraform workspaces. Depends on
    `default_bu`, so using this also pulls in the BU + memberships.
    """
    from app.models.aws_account import AwsAccount
    from app.services import aws_account_service as accs

    factory = _setup_db
    async with factory() as session:
        for acct in ("123456789012", "000000000000"):
            session.add(
                AwsAccount(
                    business_unit_id=default_bu,
                    account_id=acct,
                    name=f"test-{acct}",
                    state_bucket=f"tf-state-{acct}",
                    # Real Fernet ciphertext so list_account_credentials can
                    # decrypt these in tests that exercise the credential path.
                    access_key_id_encrypted=accs.encrypt_secret(f"AKIA{acct}"),
                    secret_access_key_encrypted=accs.encrypt_secret("secret"),
                )
            )
        await session.commit()
    return "123456789012"


async def _get_token(client: AsyncClient, email: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/auth/token",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def admin_token(auth_client, seeded_users) -> str:
    return await _get_token(auth_client, "admin@test.com", "password123")


@pytest_asyncio.fixture
async def operator_token(auth_client, seeded_users) -> str:
    return await _get_token(auth_client, "operator@test.com", "password123")


@pytest_asyncio.fixture
async def viewer_token(auth_client, seeded_users) -> str:
    return await _get_token(auth_client, "viewer@test.com", "password123")


@pytest_asyncio.fixture
async def client():
    """Async HTTP client fixture pointing at the FastAPI app via ASGI transport."""
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session():
    """Async SQLite in-memory session for unit tests.

    Creates all tables before the test and drops them after, giving each
    test a clean, isolated database.
    """
    from app.db import Base
    # Import models so their table metadata is registered on Base
    import app.models.config  # noqa: F401
    import app.models.state_lock  # noqa: F401
    import app.models.workspace  # noqa: F401
    import app.models.run  # noqa: F401
    import app.models.user  # noqa: F401
    import app.models.audit_log  # noqa: F401
    import app.models.drift_report  # noqa: F401
    import app.models.cloud_asset  # noqa: F401
    import app.models.inventory_ignore_rule  # noqa: F401
    import app.models.aws_account  # noqa: F401
    import app.models.azure_subscription  # noqa: F401
    import app.models.gcp_project  # noqa: F401
    import app.models.run_step  # noqa: F401
    import app.models.run_job  # noqa: F401
    import app.models.variable  # noqa: F401
    import app.models.user_presence  # noqa: F401
    import app.models.changelog_entry  # noqa: F401
    import app.models.k8s_cluster  # noqa: F401
    import app.models.business_unit  # noqa: F401
    import app.models.api_key  # noqa: F401
    import app.models.policy  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()
