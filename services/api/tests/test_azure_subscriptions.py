"""Azure subscription router smoke tests.

Mirrors the presence test pattern: drives the auth_client through the
real FastAPI app, seeds a BU + membership for the admin, and checks the
basic CRUD + masking semantics.
"""
from __future__ import annotations

import uuid

import pytest


SUB = "11111111-1111-1111-1111-111111111111"
TENANT = "22222222-2222-2222-2222-222222222222"
CLIENT = "33333333-3333-3333-3333-333333333333"


@pytest.fixture
def auth_headers_factory():
    def make(token: str, bu_slug: str | None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {token}"}
        if bu_slug is not None:
            h["X-Business-Unit"] = bu_slug
        return h

    return make


@pytest.mark.asyncio
async def test_create_then_list_azure_subscription(
    auth_client, admin_token, _setup_db, auth_headers_factory
):
    """Admin creates an Azure subscription in a fresh BU, then sees it
    listed without the plaintext client_secret."""
    # Seed a BU + admin membership so the BU scope dependency resolves.
    from app.models.business_unit import BusinessUnit, UserBusinessUnit
    from app.models.user import User
    from sqlalchemy import select

    factory = _setup_db
    bu_slug = f"bu-{uuid.uuid4().hex[:6]}"
    async with factory() as session:
        bu = BusinessUnit(id=str(uuid.uuid4()), slug=bu_slug, name="Test BU")
        session.add(bu)
        await session.flush()
        admin = (
            await session.execute(select(User).where(User.email == "admin@test.com"))
        ).scalars().first()
        session.add(
            UserBusinessUnit(
                user_id=admin.id, business_unit_id=bu.id, role="operator"
            )
        )
        await session.commit()

    h = auth_headers_factory(admin_token, bu_slug)
    r = await auth_client.post(
        "/api/v1/azure-subscriptions",
        headers=h,
        json={
            "subscription_id": SUB,
            "tenant_id": TENANT,
            "client_id": CLIENT,
            "client_secret": "super-secret-value",
            "name": "test-sub",
            "default_location": "eastus",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["subscription_id"] == SUB
    # Response NEVER includes the plaintext secret.
    assert "client_secret" not in body
    assert body["client_secret_masked"].endswith("alue")

    r = await auth_client.get("/api/v1/azure-subscriptions", headers=h)
    assert r.status_code == 200
    rows = r.json()
    assert any(row["subscription_id"] == SUB for row in rows)


@pytest.mark.asyncio
async def test_duplicate_subscription_returns_409(
    auth_client, admin_token, _setup_db, auth_headers_factory
):
    from app.models.business_unit import BusinessUnit, UserBusinessUnit
    from app.models.user import User
    from sqlalchemy import select

    factory = _setup_db
    bu_slug = f"bu-{uuid.uuid4().hex[:6]}"
    async with factory() as session:
        bu = BusinessUnit(id=str(uuid.uuid4()), slug=bu_slug, name="Test BU")
        session.add(bu)
        await session.flush()
        admin = (
            await session.execute(select(User).where(User.email == "admin@test.com"))
        ).scalars().first()
        session.add(
            UserBusinessUnit(user_id=admin.id, business_unit_id=bu.id, role="operator")
        )
        await session.commit()

    h = auth_headers_factory(admin_token, bu_slug)
    payload = {
        "subscription_id": SUB,
        "tenant_id": TENANT,
        "client_id": CLIENT,
        "client_secret": "x" * 8,
        "name": "first",
    }
    r1 = await auth_client.post("/api/v1/azure-subscriptions", headers=h, json=payload)
    assert r1.status_code == 201
    r2 = await auth_client.post("/api/v1/azure-subscriptions", headers=h, json=payload)
    assert r2.status_code == 409
