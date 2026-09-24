"""PUT /integrations/slack/drift — where drift alerts go."""
import pytest

from app.auth.encryption_key import get_credential_encryption_key
from app.routers.integrations import SLACK_BOT_TOKEN_KEY, SLACK_CHANNEL_ID_KEY
from app.services.config_service import ConfigService

pytestmark = pytest.mark.usefixtures("default_bu")


def _h(token: str, bu: str = "default") -> dict:
    return {"Authorization": f"Bearer {token}", "X-Business-Unit": bu}


async def _configure_slack(session_factory):
    async with session_factory() as session:
        svc = ConfigService(session, get_credential_encryption_key())
        await svc.set_for_bu("default", SLACK_BOT_TOKEN_KEY, "xoxb-test-token", is_secret=True)
        await svc.set_for_bu("default", SLACK_CHANNEL_ID_KEY, "C-DEFAULT")
        await session.commit()


async def test_drift_routing_requires_slack(auth_client, admin_token):
    r = await auth_client.put(
        "/api/v1/integrations/slack/drift", json={"drift_channel_id": "C1"}, headers=_h(admin_token)
    )
    assert r.status_code == 400


async def test_drift_routing_requires_admin(auth_client, operator_token):
    r = await auth_client.put("/api/v1/integrations/slack/drift", json={}, headers=_h(operator_token))
    assert r.status_code == 403


async def test_drift_routing_round_trip(auth_client, admin_token, _setup_db):
    await _configure_slack(_setup_db)

    r = await auth_client.get("/api/v1/integrations/slack", headers=_h(admin_token))
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["drift_channel_id"] is None and body["drift_alerts_enabled"] is True
    assert "xoxb-test-token" not in r.text

    r = await auth_client.put(
        "/api/v1/integrations/slack/drift",
        json={"drift_channel_id": "C-DRIFT", "drift_channel_name": "drift-alerts"},
        headers=_h(admin_token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["drift_channel_id"] == "C-DRIFT" and body["drift_channel_name"] == "drift-alerts"
    assert body["channel_id"] == "C-DEFAULT"
    assert "xoxb-test-token" not in r.text

    # empty channel → back to default; can switch alerts off
    r = await auth_client.put(
        "/api/v1/integrations/slack/drift",
        json={"drift_channel_id": "", "drift_alerts_enabled": False},
        headers=_h(admin_token),
    )
    body = r.json()
    assert body["drift_channel_id"] is None and body["drift_alerts_enabled"] is False

    r = await auth_client.get("/api/v1/integrations/slack", headers=_h(admin_token))
    assert r.json()["drift_alerts_enabled"] is False
