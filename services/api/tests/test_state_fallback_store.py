"""_fallback_s3_store() picks endpoint + credentials for the shared bucket
used by workspaces without a linked AwsAccount (every non-AWS workspace)."""
import pytest

import app.routers.state as state


class _Recorder:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def recorder(monkeypatch):
    monkeypatch.setattr(state, "S3StateService", _Recorder)
    monkeypatch.setattr(state, "_FALLBACK_BUCKET", "bkt")
    monkeypatch.setattr(state, "_S3_REGION", "us-east-1")
    return _Recorder


def test_custom_endpoint_with_explicit_creds(recorder, monkeypatch):
    monkeypatch.setattr(state, "_USE_LOCALSTACK", False)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", "http://192.168.0.52:3900")
    monkeypatch.setattr(state, "_S3_STATE_ACCESS_KEY_ID", "GKabc")
    monkeypatch.setattr(state, "_S3_STATE_SECRET_ACCESS_KEY", "sekrit")
    svc = state._fallback_s3_store()
    assert svc.kwargs == {
        "bucket": "bkt",
        "use_localstack": False,
        "region": "us-east-1",
        "endpoint_url": "http://192.168.0.52:3900",
        "access_key_id": "GKabc",
        "secret_access_key": "sekrit",
    }


def test_localstack_defaults_to_test_creds(recorder, monkeypatch):
    monkeypatch.setattr(state, "_USE_LOCALSTACK", True)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", None)
    monkeypatch.setattr(state, "_S3_STATE_ACCESS_KEY_ID", None)
    monkeypatch.setattr(state, "_S3_STATE_SECRET_ACCESS_KEY", None)
    svc = state._fallback_s3_store()
    assert svc.kwargs["use_localstack"] is True
    assert svc.kwargs["endpoint_url"] is None
    assert (svc.kwargs["access_key_id"], svc.kwargs["secret_access_key"]) == ("test", "test")


def test_real_aws_uses_default_chain(recorder, monkeypatch):
    monkeypatch.setattr(state, "_USE_LOCALSTACK", False)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", None)
    monkeypatch.setattr(state, "_S3_STATE_ACCESS_KEY_ID", None)
    monkeypatch.setattr(state, "_S3_STATE_SECRET_ACCESS_KEY", None)
    svc = state._fallback_s3_store()
    assert svc.kwargs["endpoint_url"] is None
    assert svc.kwargs["access_key_id"] is None
