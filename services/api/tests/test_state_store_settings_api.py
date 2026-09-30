"""/api/v1/integrations/state-store — fallback-bucket key pair in Settings.

GET is admin-only and never returns plaintext (only configured + masked
tails); PUT/DELETE are platform-global, so they additionally need a superadmin.
"""
import pytest
from sqlalchemy import select

import app.routers.state as state
from app.models.config import Config
from app.models.user import User
from app.services import state_store_config as ssc

pytestmark = pytest.mark.usefixtures("default_bu")

URL = "/api/v1/integrations/state-store"
AK = "GK1234567890abcdWXYZ"
SK = "super-secret-access-key-9876"


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _promote(setup_db, email="admin@test.com"):
    async with setup_db() as s:
        u = (await s.execute(select(User).where(User.email == email))).scalars().first()
        u.is_superadmin = True
        await s.commit()


async def test_get_unconfigured(auth_client, admin_token, monkeypatch):
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", "http://garage.internal:3900")
    monkeypatch.setattr(state, "_FALLBACK_BUCKET", "tf-state")
    monkeypatch.setattr(state, "_USE_LOCALSTACK", False)
    r = await auth_client.get(URL, headers=_h(admin_token))
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["partial"] is False
    assert body["access_key_id_tail"] is None
    assert body["secret_access_key_tail"] is None
    assert body["bucket"] == "tf-state"
    assert body["endpoint_url"] == "http://garage.internal:3900"
    assert body["insecure_endpoint"] is True


async def test_superadmin_put_get_delete_roundtrip(auth_client, admin_token, _setup_db):
    await _promote(_setup_db)
    r = await auth_client.put(
        URL, json={"access_key_id": AK, "secret_access_key": SK}, headers=_h(admin_token)
    )
    assert r.status_code == 200, r.text
    assert AK not in r.text and SK not in r.text

    r = await auth_client.get(URL, headers=_h(admin_token))
    body = r.json()
    assert body["configured"] is True
    assert body["access_key_id_tail"] == "…WXYZ"
    assert body["secret_access_key_tail"] == "…9876"
    assert AK not in r.text and SK not in r.text

    async with _setup_db() as s:
        for key in (ssc.ACCESS_KEY_ID_KEY, ssc.SECRET_ACCESS_KEY_KEY):
            row = await s.get(Config, key)
            assert row is not None and row.is_secret is True
        creds = await ssc.load(s)
        assert (creds.access_key_id, creds.secret_access_key) == (AK, SK)

    r = await auth_client.delete(URL, headers=_h(admin_token))
    assert r.status_code == 204
    r = await auth_client.get(URL, headers=_h(admin_token))
    assert r.json()["configured"] is False
    async with _setup_db() as s:
        assert await s.get(Config, ssc.ACCESS_KEY_ID_KEY) is None
        assert await s.get(Config, ssc.SECRET_ACCESS_KEY_KEY) is None


async def test_non_superadmin_admin_cannot_write(auth_client, admin_token):
    r = await auth_client.put(
        URL, json={"access_key_id": AK, "secret_access_key": SK}, headers=_h(admin_token)
    )
    assert r.status_code == 403
    assert (await auth_client.delete(URL, headers=_h(admin_token))).status_code == 403


async def test_operator_and_viewer_cannot_read_or_write(
    auth_client, operator_token, viewer_token, _setup_db
):
    for tok in (operator_token, viewer_token):
        assert (await auth_client.get(URL, headers=_h(tok))).status_code == 403
        assert (
            await auth_client.put(
                URL, json={"access_key_id": AK, "secret_access_key": SK}, headers=_h(tok)
            )
        ).status_code == 403
        assert (await auth_client.delete(URL, headers=_h(tok))).status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {"access_key_id": AK},
        {"secret_access_key": SK},
        {"access_key_id": "", "secret_access_key": SK},
        {"access_key_id": "   ", "secret_access_key": SK},
    ],
)
async def test_put_requires_both_halves(auth_client, admin_token, _setup_db, payload):
    await _promote(_setup_db)
    r = await auth_client.put(URL, json=payload, headers=_h(admin_token))
    assert r.status_code == 422
    async with _setup_db() as s:
        assert await s.get(Config, ssc.ACCESS_KEY_ID_KEY) is None


async def test_get_flags_partial_pair(auth_client, admin_token, _setup_db):
    from app.auth.encryption_key import get_credential_encryption_key
    from app.services.config_service import ConfigService

    async with _setup_db() as s:
        await ConfigService(s, get_credential_encryption_key()).set(
            ssc.ACCESS_KEY_ID_KEY, AK, is_secret=True
        )
        await s.commit()
    body = (await auth_client.get(URL, headers=_h(admin_token))).json()
    assert body["configured"] is False
    assert body["partial"] is True
    assert body["access_key_id_tail"] == "…WXYZ"
    assert body["secret_access_key_tail"] is None
