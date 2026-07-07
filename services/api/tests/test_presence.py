"""Presence endpoint tests.

Covers ping/list semantics for the cross-BU presence indicator: every BU's
users show up in the list (the whole point of the feature), stale rows
fall off the window, and the bu_slug a user pings with is what other users
see in the avatar stack.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.user_presence import UserPresence
from app.routers.presence import PRESENCE_WINDOW_SECONDS


@pytest.mark.asyncio
async def test_ping_inserts_then_updates_row(auth_client, operator_token, _setup_db):
    h = {"Authorization": f"Bearer {operator_token}"}
    r = await auth_client.post("/api/v1/presence", headers=h, json={"bu_slug": "home"})
    assert r.status_code == 204

    factory = _setup_db
    async with factory() as session:
        rows = (await session.execute(__import__("sqlalchemy").select(UserPresence))).all()
        assert len(rows) == 1
        assert rows[0][0].bu_slug == "home"

    # Second ping with a different BU should update in place.
    r = await auth_client.post("/api/v1/presence", headers=h, json={"bu_slug": "partners"})
    assert r.status_code == 204
    async with factory() as session:
        rows = (await session.execute(__import__("sqlalchemy").select(UserPresence))).all()
        assert len(rows) == 1
        assert rows[0][0].bu_slug == "partners"


@pytest.mark.asyncio
async def test_list_returns_users_seen_in_window(
    auth_client, admin_token, operator_token, viewer_token
):
    # Three users ping from different BUs.
    for tok, bu in (
        (admin_token, "home"),
        (operator_token, "partners"),
        (viewer_token, None),
    ):
        r = await auth_client.post(
            "/api/v1/presence",
            headers={"Authorization": f"Bearer {tok}"},
            json={"bu_slug": bu},
        )
        assert r.status_code == 204

    # Any authenticated caller can see the cross-BU stack.
    r = await auth_client.get(
        "/api/v1/presence", headers={"Authorization": f"Bearer {viewer_token}"}
    )
    assert r.status_code == 200
    body = r.json()
    emails = {u["email"] for u in body["users"]}
    assert emails == {"admin@test.com", "operator@test.com", "viewer@test.com"}
    by_email = {u["email"]: u["bu_slug"] for u in body["users"]}
    assert by_email["admin@test.com"] == "home"
    assert by_email["operator@test.com"] == "partners"
    assert by_email["viewer@test.com"] is None


@pytest.mark.asyncio
async def test_list_drops_stale_rows(auth_client, operator_token, viewer_token, _setup_db):
    # Operator pings now; viewer's row is force-aged past the window.
    await auth_client.post(
        "/api/v1/presence",
        headers={"Authorization": f"Bearer {operator_token}"},
        json={"bu_slug": "home"},
    )
    await auth_client.post(
        "/api/v1/presence",
        headers={"Authorization": f"Bearer {viewer_token}"},
        json={"bu_slug": "home"},
    )

    factory = _setup_db
    from sqlalchemy import select, update

    stale_at = datetime.now(timezone.utc) - timedelta(seconds=PRESENCE_WINDOW_SECONDS + 10)
    async with factory() as session:
        # Age the viewer row out of the window.
        rows = (
            await session.execute(
                select(UserPresence).join(
                    __import__("app").models.user.User,
                    __import__("app").models.user.User.id == UserPresence.user_id,
                )
            )
        ).all()
        for (p,) in rows:
            from app.models.user import User as _U
            u = await session.get(_U, p.user_id)
            if u.email == "viewer@test.com":
                await session.execute(
                    update(UserPresence)
                    .where(UserPresence.user_id == p.user_id)
                    .values(last_seen_at=stale_at)
                )
        await session.commit()

    r = await auth_client.get(
        "/api/v1/presence",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()["users"]}
    assert "operator@test.com" in emails
    assert "viewer@test.com" not in emails


@pytest.mark.asyncio
async def test_ping_requires_auth(auth_client):
    r = await auth_client.post("/api/v1/presence", json={"bu_slug": "home"})
    assert r.status_code == 401
