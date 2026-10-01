"""State read/write must report an unusable fallback store as 503, not 500.

GET already did (catch-all → 503 "State backend unavailable"); PUT used to
return a 500 "Failed to persist state" for the same half-configured key pair,
which Terraform treats as a hard failure rather than "backend unavailable".
"""
import json

import pytest

import app.routers.state as state
from app.models.workspace import Workspace
from app.services import state_store_config as ssc

pytestmark = pytest.mark.usefixtures("default_bu")

HEADERS = {"X-Terraducktel-State-Token": "test-state-token-do-not-use-in-prod"}
WS_ID = "ws-state-503"


async def _make_workspace(setup_db):
    async with setup_db() as s:
        s.add(Workspace(id=WS_ID, name="state-503", business_unit_id="default",
                        aws_account_id="global", region="us-east-1",
                        repo_url="local://x", tf_working_dir=".", environment="dev"))
        await s.commit()


async def _store_access_key_only(setup_db):
    from app.auth.encryption_key import get_credential_encryption_key
    from app.services.config_service import ConfigService

    async with setup_db() as s:
        await ConfigService(s, get_credential_encryption_key()).set(
            ssc.ACCESS_KEY_ID_KEY, "GKabc", is_secret=True
        )
        await s.commit()


async def test_put_state_half_configured_keys_is_503_like_get(auth_client, _setup_db, monkeypatch):
    monkeypatch.setattr(state, "_USE_LOCALSTACK", False)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", "https://s3.example.internal")
    await _make_workspace(_setup_db)
    await _store_access_key_only(_setup_db)

    got = await auth_client.get(f"/api/v1/state/{WS_ID}", headers=HEADERS)
    put = await auth_client.post(
        f"/api/v1/state/{WS_ID}", content=json.dumps({"version": 4, "resources": []}),
        headers=HEADERS,
    )
    assert got.status_code == 503
    assert put.status_code == 503
    assert put.json()["detail"] == got.json()["detail"] == "State backend unavailable"
