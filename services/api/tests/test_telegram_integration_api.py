"""Endpoint coverage for the per-BU Telegram integration.

app.services.telegram is monkeypatched at the router's import site so no
test here reaches api.telegram.org.
"""
import httpx
import pytest

from app.auth.encryption_key import get_credential_encryption_key
from app.routers.integrations import (
    TELEGRAM_BOT_TOKEN_KEY,
    TELEGRAM_BOT_USERNAME_KEY,
    TELEGRAM_CHAT_ID_KEY,
    TELEGRAM_CHAT_TITLE_KEY,
)
from app.services import telegram as tg
from app.services.config_service import ConfigService

# asyncio_mode = "auto" in pyproject.toml, so no asyncio mark is needed.
pytestmark = pytest.mark.usefixtures("default_bu")

BASE = "/api/v1/integrations/telegram"


def _h(token: str, bu: str = "default") -> dict:
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


@pytest.fixture
def good_telegram(monkeypatch):
    """getMe and getChat both succeed; sendMessage records its calls."""
    sent: list = []

    async def _verify(token):
        return tg.TelegramIdentity(bot_id=777, username="tdt_bot", first_name="TDT")

    async def _get_chat(token, chat_id):
        return tg.TelegramChat(id=str(chat_id), title="Platform Ops", type="supergroup")

    async def _send(token, chat_id, text, buttons=None):
        sent.append((chat_id, text))

    monkeypatch.setattr(tg, "verify_token", _verify)
    monkeypatch.setattr(tg, "get_chat", _get_chat)
    monkeypatch.setattr(tg, "send_message", _send)
    return sent


# ─── RBAC ────────────────────────────────────────────────────────────────────


async def test_viewer_and_operator_are_forbidden(
    auth_client, viewer_token, operator_token, good_telegram
):
    # (method, path, json body) for every route this router exposes. PUT's
    # body is `{}` — every TelegramUpdate field is optional, so an empty body
    # passes request validation and the 403 from require_role is what we're
    # actually checking, not a 422 from a missing field.
    calls = [
        ("get", BASE, None),
        ("put", BASE, {}),
        ("delete", BASE, None),
        ("post", f"{BASE}/test", None),
        ("post", f"{BASE}/test-message", None),
    ]
    for tok in (viewer_token, operator_token):
        for method, path, body in calls:
            kwargs = {"headers": _h(tok)}
            if body is not None:
                kwargs["json"] = body
            r = await getattr(auth_client, method)(path, **kwargs)
            assert r.status_code == 403, f"{method.upper()} {path} -> {r.status_code}"


async def test_unconfigured_reports_not_configured(auth_client, admin_token):
    r = await auth_client.get(BASE, headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["configured"] is False


# ─── PUT ─────────────────────────────────────────────────────────────────────


async def test_put_requires_a_token_on_first_save(auth_client, admin_token, good_telegram):
    r = await auth_client.put(BASE, json={"chat_id": "-1001"}, headers=_h(admin_token))
    assert r.status_code == 422


async def test_put_saves_token_and_chat_and_never_returns_the_token(
    auth_client, admin_token, good_telegram
):
    secret = "777:AAHsecrettokenvalue"
    r = await auth_client.put(
        BASE, json={"token": secret, "chat_id": "-1001234567890"},
        headers=_h(admin_token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["bot_username"] == "tdt_bot"
    assert body["chat_id"] == "-1001234567890"
    assert body["chat_title"] == "Platform Ops"
    assert secret not in r.text
    assert body["token_tail"] == "…alue"

    g = await auth_client.get(BASE, headers=_h(admin_token))
    assert secret not in g.text
    assert g.json()["chat_title"] == "Platform Ops"


async def test_put_rejects_a_malformed_chat_id_before_calling_telegram(
    auth_client, admin_token, monkeypatch
):
    async def _boom(*a, **k):
        raise AssertionError("must not reach Telegram for a malformed chat id")

    monkeypatch.setattr(tg, "get_chat", _boom)

    async def _verify(token):
        return tg.TelegramIdentity(bot_id=1, username="b", first_name="B")

    monkeypatch.setattr(tg, "verify_token", _verify)

    r = await auth_client.put(
        BASE, json={"token": "1:abcdefgh", "chat_id": "https://t.me/nope"},
        headers=_h(admin_token),
    )
    assert r.status_code == 422


async def test_put_rejects_a_token_telegram_refuses(auth_client, admin_token, monkeypatch):
    async def _verify(token):
        raise tg.TelegramError(code=401, description="Unauthorized")

    monkeypatch.setattr(tg, "verify_token", _verify)
    r = await auth_client.put(BASE, json={"token": "1:badtoken"}, headers=_h(admin_token))
    assert r.status_code == 400
    assert "401" in r.text or "Unauthorized" in r.text


async def test_put_returns_502_when_telegram_is_unreachable(
    auth_client, admin_token, monkeypatch
):
    async def _verify(token):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(tg, "verify_token", _verify)
    r = await auth_client.put(BASE, json={"token": "1:abcdefgh"}, headers=_h(admin_token))
    assert r.status_code == 502


async def test_put_rejects_a_chat_the_bot_cannot_see(auth_client, admin_token, monkeypatch):
    async def _verify(token):
        return tg.TelegramIdentity(bot_id=1, username="b", first_name="B")

    async def _get_chat(token, chat_id):
        raise tg.TelegramError(code=400, description="Bad Request: chat not found")

    monkeypatch.setattr(tg, "verify_token", _verify)
    monkeypatch.setattr(tg, "get_chat", _get_chat)
    r = await auth_client.put(
        BASE, json={"token": "1:abcdefgh", "chat_id": "-1009"}, headers=_h(admin_token)
    )
    assert r.status_code == 400
    assert "chat not found" in r.text


async def test_put_without_a_token_reuses_the_stored_one(
    auth_client, admin_token, good_telegram
):
    await auth_client.put(
        BASE, json={"token": "777:firstsecret"}, headers=_h(admin_token)
    )
    r = await auth_client.put(BASE, json={"chat_id": "-1002"}, headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["chat_id"] == "-1002"
    assert r.json()["token_tail"] == "…cret"


# ─── BU scoping ──────────────────────────────────────────────────────────────


async def test_config_is_scoped_to_the_business_unit(
    auth_client, admin_token, good_telegram, _setup_db
):
    # NOTE: UserBusinessUnit lives in app.models.business_unit, not in a
    # module of its own.
    from app.models.business_unit import BusinessUnit, UserBusinessUnit
    from app.models.user import User
    from sqlalchemy import select
    import uuid

    async with _setup_db() as s:
        bu = BusinessUnit(id=str(uuid.uuid4()), slug="other", name="Other")
        s.add(bu)
        admin = (
            await s.execute(select(User).where(User.email == "admin@test.com"))
        ).scalars().first()
        s.add(UserBusinessUnit(
            user_id=admin.id,
            business_unit_id=bu.id, role="operator",
        ))
        await s.commit()

    await auth_client.put(
        BASE, json={"token": "777:defaultsecret", "chat_id": "-1001"},
        headers=_h(admin_token, "default"),
    )
    r = await auth_client.get(BASE, headers=_h(admin_token, "other"))
    assert r.json()["configured"] is False


# ─── test / test-message / delete ────────────────────────────────────────────


async def test_test_endpoints_400_when_nothing_is_configured(auth_client, admin_token):
    for path in (f"{BASE}/test", f"{BASE}/test-message"):
        r = await auth_client.post(path, headers=_h(admin_token))
        assert r.status_code == 400


async def test_test_reverifies_the_saved_token(auth_client, admin_token, good_telegram):
    await auth_client.put(
        BASE, json={"token": "777:secret1234", "chat_id": "-1001"},
        headers=_h(admin_token),
    )
    r = await auth_client.post(f"{BASE}/test", headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["bot_username"] == "tdt_bot"


async def test_test_reports_a_revoked_token_without_raising(
    auth_client, admin_token, good_telegram, monkeypatch
):
    await auth_client.put(
        BASE, json={"token": "777:secret1234", "chat_id": "-1001"},
        headers=_h(admin_token),
    )

    async def _verify(token):
        raise tg.TelegramError(code=401, description="Unauthorized")

    monkeypatch.setattr(tg, "verify_token", _verify)
    r = await auth_client.post(f"{BASE}/test", headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "401" in r.json()["detail"]


async def test_test_message_posts_to_the_configured_chat(
    auth_client, admin_token, good_telegram
):
    await auth_client.put(
        BASE, json={"token": "777:secret1234", "chat_id": "-1001"},
        headers=_h(admin_token),
    )
    r = await auth_client.post(f"{BASE}/test-message", headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert good_telegram, "expected sendMessage to be called"
    chat_id, text = good_telegram[-1]
    assert chat_id == "-1001"
    assert "Terraducktel" in text


async def test_test_message_reports_a_send_failure(
    auth_client, admin_token, good_telegram, monkeypatch
):
    await auth_client.put(
        BASE, json={"token": "777:secret1234", "chat_id": "-1001"},
        headers=_h(admin_token),
    )

    async def _send(token, chat_id, text, buttons=None):
        raise tg.TelegramError(code=403, description="Forbidden: bot is not a member")

    monkeypatch.setattr(tg, "send_message", _send)
    r = await auth_client.post(f"{BASE}/test-message", headers=_h(admin_token))
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "403" in r.json()["detail"]


async def test_delete_removes_every_key(
    auth_client, admin_token, good_telegram, _setup_db
):
    await auth_client.put(
        BASE, json={"token": "777:secret1234", "chat_id": "-1001"},
        headers=_h(admin_token),
    )
    d = await auth_client.delete(BASE, headers=_h(admin_token))
    assert d.status_code == 204

    # Go straight to the config store rather than through GET: _telegram_status
    # short-circuits to configured=False (chat_id defaulting to None) the
    # moment the bot-token key is gone, without ever reading the other three
    # keys — so a GET-only check can't tell "all four keys deleted" from
    # "only the token was deleted". Assert each key directly instead.
    async with _setup_db() as s:
        svc = ConfigService(s, get_credential_encryption_key())
        for key in (
            TELEGRAM_BOT_TOKEN_KEY,
            TELEGRAM_BOT_USERNAME_KEY,
            TELEGRAM_CHAT_ID_KEY,
            TELEGRAM_CHAT_TITLE_KEY,
        ):
            assert await svc.get_for_bu("default", key) is None, key

    g = await auth_client.get(BASE, headers=_h(admin_token))
    assert g.json()["configured"] is False
    assert g.json()["chat_id"] is None
