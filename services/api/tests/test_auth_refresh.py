"""`POST /api/v1/auth/refresh` — redeem a refresh token for a fresh pair.

Both `/auth/token` and the OIDC callback always minted a refresh token, but
nothing could redeem one until this endpoint landed. These tests pin the two
properties that matter: only a *refresh*-typed JWT is accepted, and the new
access token's claims come from the live User row rather than the old token.
"""
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("default_bu")


async def _login(client, email="admin@test.com", password="password123") -> dict:
    resp = await client.post(
        "/api/v1/auth/token", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_refresh_returns_usable_access_and_renewed_refresh(
    auth_client, seeded_users
):
    """A valid refresh token yields a working access token + a new refresh token."""
    pair = await _login(auth_client)

    resp = await auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert resp.status_code == 200, resp.text
    new = resp.json()
    assert new["access_token"]
    assert new["refresh_token"]
    assert new["token_type"] == "bearer"

    # The freshly-minted access token actually authenticates.
    me = await auth_client.get(
        "/api/v1/workspaces",
        headers={"Authorization": f"Bearer {new['access_token']}"},
    )
    assert me.status_code == 200, me.text

    # Sliding session: the returned refresh token is itself redeemable, so a
    # long-lived CLI session never has to re-authenticate. Note the string can
    # equal the old one — a refresh JWT is {sub, type, exp} with no iat/jti, so
    # two mints in the same second are byte-identical. `exp` still advances on
    # any later redemption, which is what makes the session slide.
    again = await auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new["refresh_token"]}
    )
    assert again.status_code == 200, again.text


@pytest.mark.asyncio
async def test_access_token_is_rejected_as_a_refresh_token(auth_client, seeded_users):
    """An access token must not be upgradeable into a fresh long-lived pair."""
    pair = await _login(auth_client)
    resp = await auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": pair["access_token"]}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Not a refresh token"


@pytest.mark.asyncio
async def test_run_token_is_rejected_as_a_refresh_token(auth_client, seeded_users):
    """The executor's run-scoped token is not a refresh token either."""
    from app.auth.jwt import create_run_token

    admin = seeded_users["admin"]
    run_tok = create_run_token(
        admin.id,
        admin.email,
        run_id=str(uuid.uuid4()),
        workspace_id=str(uuid.uuid4()),
        business_unit_id=None,
    )
    resp = await auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": run_tok}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Not a refresh token"


@pytest.mark.asyncio
async def test_garbage_and_api_key_are_rejected(auth_client, seeded_users):
    """Non-JWT input (including a `tdt_` API key) fails closed with 401."""
    for bad in ("not-a-jwt", "tdt_deadbeefdeadbeef", ""):
        resp = await auth_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": bad}
        )
        assert resp.status_code == 401, f"{bad!r} -> {resp.status_code}"
        assert resp.json()["detail"] == "Invalid or expired refresh token"


@pytest.mark.asyncio
async def test_expired_refresh_token_is_rejected(auth_client, seeded_users):
    """A refresh token past its `exp` is refused rather than silently renewed."""
    from app.auth.jwt import create_refresh_token

    admin = seeded_users["admin"]
    stale = create_refresh_token(admin.id, expires_in_hours=-1)
    resp = await auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": stale}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired refresh token"


@pytest.mark.asyncio
async def test_refresh_for_deleted_user_is_rejected(auth_client, seeded_users, _setup_db):
    """Deleting the user is the revocation mechanism for a stateless refresh."""
    from app.models.user import User

    pair = await _login(auth_client, "viewer@test.com")
    viewer_id = seeded_users["viewer"].id

    factory = _setup_db
    async with factory() as session:
        row = await session.get(User, viewer_id)
        await session.delete(row)
        await session.commit()

    resp = await auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "User not found"


@pytest.mark.asyncio
async def test_refresh_reflects_a_demotion(auth_client, seeded_users, _setup_db):
    """Claims are re-read from the User row, so a demotion lands on next refresh.

    Guards against the tempting-but-wrong implementation that copies `role` /
    `is_superadmin` out of the incoming token.
    """
    from app.auth.jwt import decode_token
    from app.models.user import User

    pair = await _login(auth_client)
    assert decode_token(pair["access_token"])["role"] == "admin"

    factory = _setup_db
    async with factory() as session:
        row = await session.get(User, seeded_users["admin"].id)
        row.role = "viewer"
        row.is_superadmin = False
        await session.commit()

    resp = await auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    )
    assert resp.status_code == 200, resp.text
    claims = decode_token(resp.json()["access_token"])
    assert claims["role"] == "viewer"
    assert claims["is_superadmin"] is False
