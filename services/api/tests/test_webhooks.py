"""TDD RED tests for Forgejo webhook handler.

These tests verify:
1. Valid HMAC-SHA256 signature -> 202 accepted
2. Invalid/missing signature -> 403 rejected
3. Webhook creates a plan run for the matched workspace
"""
import pytest

# Pre-tenancy tests: seed the default BU (+ AWS accounts) so BU-scoped
# endpoints resolve and workspace creation succeeds.
pytestmark = pytest.mark.usefixtures("default_aws_account")

from app.models.business_unit import DEFAULT_BU_ID

import hashlib
import hmac
import json
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool


@pytest_asyncio.fixture
async def _webhook_db():
    """Create all tables in an in-memory SQLite DB for webhook tests."""
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
async def webhook_client(_webhook_db):
    """HTTP client for webhook tests."""
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def webhook_workspace(_webhook_db):
    """Create a workspace with a repo_url that matches the webhook payload."""
    from app.models.workspace import Workspace
    from app.models.config import Config

    factory = _webhook_db
    ws_id = str(uuid.uuid4())
    async with factory() as session:
        ws = Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id,
            name="tf-repo",
            aws_account_id="123456789012",
            environment="dev",
            region="us-east-1",
            repo_url="https://forgejo.local/org/tf-repo",
            # Webhooks are opt-in per workspace (migration 012). The handler
            # returns 'ignored' for workspaces with webhook_enabled=False, so
            # the test fixture has to flip this explicitly.
            webhook_enabled=True,
        )
        session.add(ws)
        # Store webhook secret in config table
        cfg = Config(
            key="webhook.secret",
            value="test-webhook-secret",
            is_secret=False,
            description="Webhook HMAC secret",
        )
        session.add(cfg)
        await session.commit()
    return ws_id


def _sign_payload(payload: bytes, secret: str) -> str:
    """Generate HMAC-SHA256 signature matching Forgejo format."""
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


async def test_forgejo_webhook_valid_signature_accepted(webhook_client, webhook_workspace):
    """Valid HMAC signature -> 202 Accepted."""
    payload = json.dumps({
        "ref": "refs/heads/main",
        "repository": {"full_name": "org/tf-repo"},
    }).encode()
    sig = _sign_payload(payload, "test-webhook-secret")

    response = await webhook_client.post(
        "/api/v1/webhooks/forgejo",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Gitea-Event": "push",
            "X-Gitea-Signature": sig,
        },
    )
    assert response.status_code == 202


async def test_forgejo_webhook_invalid_signature_rejected(webhook_client, webhook_workspace):
    """Invalid HMAC signature -> 403."""
    payload = json.dumps({
        "ref": "refs/heads/main",
        "repository": {"full_name": "org/tf-repo"},
    }).encode()

    response = await webhook_client.post(
        "/api/v1/webhooks/forgejo",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Gitea-Event": "push",
            "X-Gitea-Signature": "invalid-signature",
        },
    )
    assert response.status_code == 403


async def test_forgejo_webhook_missing_signature_rejected(webhook_client, webhook_workspace):
    """Missing signature header -> 403."""
    payload = json.dumps({
        "ref": "refs/heads/main",
        "repository": {"full_name": "org/tf-repo"},
    }).encode()

    response = await webhook_client.post(
        "/api/v1/webhooks/forgejo",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Gitea-Event": "push",
        },
    )
    assert response.status_code == 403


async def test_forgejo_webhook_creates_plan_run(webhook_client, webhook_workspace, _webhook_db):
    """Webhook should create a pending plan run for the matching workspace."""
    payload = json.dumps({
        "ref": "refs/heads/main",
        "repository": {"full_name": "org/tf-repo"},
    }).encode()
    sig = _sign_payload(payload, "test-webhook-secret")

    await webhook_client.post(
        "/api/v1/webhooks/forgejo",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Gitea-Event": "push",
            "X-Gitea-Signature": sig,
        },
    )

    # Verify a run was created AND a run_jobs row was enqueued. Without the
    # enqueue, the worker never claims the row and the Run sits in PENDING
    # forever — the latent bug that left webhook-triggered runs invisible
    # to operators until we wired enqueue_job into all three handlers.
    from app.models.run import Run
    from app.models.run_job import RunJob
    from sqlalchemy import select
    factory = _webhook_db
    async with factory() as session:
        result = await session.execute(
            select(Run).where(Run.workspace_id == webhook_workspace)
        )
        runs = result.scalars().all()
        assert len(runs) >= 1
        assert runs[0].command == "plan"
        assert runs[0].status.value == "pending"

        jobs = (
            await session.execute(select(RunJob).where(RunJob.run_id == runs[0].id))
        ).scalars().all()
        assert len(jobs) == 1, "webhook handler must enqueue a run_jobs row"
        assert jobs[0].phase == "plan"


# ─── legacy GitHub webhook must not cross BU boundaries ───────────


async def test_github_webhook_does_not_cross_bu_boundary_on_repo_name_collision(
    webhook_client, _webhook_db,
):
    """A push to one BU's repo must not also trigger a run for a *different*
    BU's workspace just because that workspace's `repo_url` happens to
    contain the pushed repo's `full_name` as a substring.

    Regression test for the legacy, non-BU-scoped `/webhooks/github`
    route matched workspaces with `Workspace.repo_url.ilike(f"%{repo_full_name}%")`
    and no Business Unit filter, so a push to `acme/infra` would also match
    (and trigger a plan for) an unrelated workspace in another BU pointing at
    `acme/infra-fork`, since that repo_url contains `acme/infra` as a
    substring.
    """
    from app.models.workspace import Workspace
    from app.models.config import Config
    from app.models.run import Run
    from sqlalchemy import select

    factory = _webhook_db
    exact_ws_id = str(uuid.uuid4())
    collide_ws_id = str(uuid.uuid4())
    async with factory() as session:
        # BU "alpha" owns the repo that actually pushed — exact repo_url match.
        exact_ws = Workspace(
            business_unit_id="bu-alpha",
            id=exact_ws_id,
            name="infra-alpha",
            aws_account_id="123456789012",
            environment="dev",
            region="us-east-1",
            repo_url="https://github.com/acme/infra",
            repo_ref="main",
            webhook_enabled=True,
        )
        # BU "beta" has an unrelated workspace whose repo_url merely CONTAINS
        # "acme/infra" as a substring — this must NOT be triggered by a push
        # to "acme/infra".
        collide_ws = Workspace(
            business_unit_id="bu-beta",
            id=collide_ws_id,
            name="infra-beta-fork",
            aws_account_id="210987654321",
            environment="dev",
            region="us-east-1",
            repo_url="https://github.com/acme/infra-fork",
            repo_ref="main",
            webhook_enabled=True,
        )
        session.add_all([exact_ws, collide_ws])
        session.add(Config(
            key="webhook.secret",
            value="test-webhook-secret",
            is_secret=False,
            description="Webhook HMAC secret",
        ))
        await session.commit()

    payload = json.dumps({
        "ref": "refs/heads/main",
        "repository": {"full_name": "acme/infra"},
        "commits": [],
    }).encode()
    sig = "sha256=" + hmac.new(b"test-webhook-secret", payload, hashlib.sha256).hexdigest()

    response = await webhook_client.post(
        "/api/v1/webhooks/github",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": sig,
        },
    )
    assert response.status_code == 202
    body = response.json()
    triggered_ids = {t["workspace_id"] for t in body["triggered"]}
    assert triggered_ids == {exact_ws_id}, (
        "push to 'acme/infra' must only trigger the exact-match workspace, "
        f"never the colliding BU-beta workspace; got {body}"
    )

    async with factory() as session:
        runs = (await session.execute(select(Run))).scalars().all()
        run_workspace_ids = {r.workspace_id for r in runs}
        assert collide_ws_id not in run_workspace_ids, (
            "BU-beta's workspace must not get a run from a push to BU-alpha's repo"
        )
        assert exact_ws_id in run_workspace_ids
