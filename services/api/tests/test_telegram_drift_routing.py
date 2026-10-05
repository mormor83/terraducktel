"""PUT /integrations/telegram/drift — where drift alerts go.

Telegram twin of test_slack_drift_routing.py. Unlike Slack's route (picked
from a channel list the API already fetched), the drift chat id is typed by
hand, so it is verified with getChat before saving.
"""
import httpx
import pytest

from app.routers.integrations import (
    TELEGRAM_BOT_TOKEN_KEY,
    TELEGRAM_CHAT_ID_KEY,
)
from app.auth.encryption_key import get_credential_encryption_key
from app.services import telegram as tg
from app.services.config_service import ConfigService

pytestmark = pytest.mark.usefixtures("default_bu")

DRIFT = "/api/v1/integrations/telegram/drift"


def _h(token: str, bu: str = "default") -> dict:
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


async def _configure_telegram(session_factory):
    async with session_factory() as session:
        svc = ConfigService(session, get_credential_encryption_key())
        await svc.set_for_bu("default", TELEGRAM_BOT_TOKEN_KEY, "777:secrettoken", is_secret=True)
        await svc.set_for_bu("default", TELEGRAM_CHAT_ID_KEY, "-100111")
        await session.commit()


@pytest.fixture
def seen_chats(monkeypatch):
    """getChat succeeds and records which chat ids were checked."""
    checked: list = []

    async def _get_chat(token, chat_id):
        checked.append(chat_id)
        return tg.TelegramChat(id=str(chat_id), title="Drift Watch", type="supergroup")

    monkeypatch.setattr(tg, "get_chat", _get_chat)
    return checked


async def test_drift_routing_requires_telegram(auth_client, admin_token, seen_chats):
    r = await auth_client.put(DRIFT, json={"drift_chat_id": "-100222"}, headers=_h(admin_token))
    assert r.status_code == 400


async def test_drift_routing_requires_admin(auth_client, operator_token, viewer_token):
    for tok in (operator_token, viewer_token):
        r = await auth_client.put(DRIFT, json={}, headers=_h(tok))
        assert r.status_code == 403


async def test_drift_routing_round_trip(auth_client, admin_token, _setup_db, seen_chats):
    await _configure_telegram(_setup_db)

    r = await auth_client.get("/api/v1/integrations/telegram", headers=_h(admin_token))
    body = r.json()
    assert body["drift_chat_id"] is None and body["drift_alerts_enabled"] is True

    r = await auth_client.put(DRIFT, json={"drift_chat_id": "-100222"}, headers=_h(admin_token))
    assert r.status_code == 200
    body = r.json()
    assert body["drift_chat_id"] == "-100222"
    # Title comes from getChat, not the client — it's what the bot actually sees.
    assert body["drift_chat_title"] == "Drift Watch"
    assert body["chat_id"] == "-100111"
    assert seen_chats == ["-100222"]
    assert "777:secrettoken" not in r.text

    # empty chat → back to the main chat; can switch alerts off
    r = await auth_client.put(
        DRIFT, json={"drift_chat_id": "", "drift_alerts_enabled": False}, headers=_h(admin_token)
    )
    body = r.json()
    assert body["drift_chat_id"] is None and body["drift_chat_title"] is None
    assert body["drift_alerts_enabled"] is False
    # Resetting to the main chat needs no Bot API call.
    assert seen_chats == ["-100222"]

    r = await auth_client.get("/api/v1/integrations/telegram", headers=_h(admin_token))
    assert r.json()["drift_alerts_enabled"] is False


async def test_drift_routing_rejects_a_malformed_chat_id(
    auth_client, admin_token, _setup_db, seen_chats
):
    await _configure_telegram(_setup_db)
    r = await auth_client.put(DRIFT, json={"drift_chat_id": "not a chat"}, headers=_h(admin_token))
    assert r.status_code == 422
    assert seen_chats == []


async def test_drift_routing_rejects_a_chat_the_bot_cannot_see(
    auth_client, admin_token, _setup_db, monkeypatch
):
    await _configure_telegram(_setup_db)

    async def _get_chat(token, chat_id):
        raise tg.TelegramError(code=400, description="Bad Request: chat not found")

    monkeypatch.setattr(tg, "get_chat", _get_chat)
    r = await auth_client.put(DRIFT, json={"drift_chat_id": "-100222"}, headers=_h(admin_token))
    assert r.status_code == 400
    assert "chat not found" in r.text

    g = await auth_client.get("/api/v1/integrations/telegram", headers=_h(admin_token))
    assert g.json()["drift_chat_id"] is None


async def test_drift_routing_returns_502_when_telegram_is_unreachable(
    auth_client, admin_token, _setup_db, monkeypatch
):
    await _configure_telegram(_setup_db)

    async def _get_chat(token, chat_id):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(tg, "get_chat", _get_chat)
    r = await auth_client.put(DRIFT, json={"drift_chat_id": "-100222"}, headers=_h(admin_token))
    assert r.status_code == 502
