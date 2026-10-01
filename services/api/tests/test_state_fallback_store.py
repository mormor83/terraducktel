"""_fallback_s3_store() picks endpoint + credentials for the shared bucket
used by workspaces without a linked AwsAccount (every non-AWS workspace).

The endpoint is an env var (S3_ENDPOINT_URL, not secret); the key pair lives
in the encrypted `config` table (services/state_store_config.py) and is read
through ConfigService, never from the environment.
"""
import logging

import pytest

# Imported at module load: app.main's configure_logging() replaces the root
# handlers, which would drop caplog's handler if it ran inside a test.
import app.main as main_mod
import app.routers.state as state
from app.services import state_store_config as ssc


class _Recorder:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def recorder(monkeypatch):
    monkeypatch.setattr(state, "S3StateService", _Recorder)
    monkeypatch.setattr(state, "_FALLBACK_BUCKET", "bkt")
    monkeypatch.setattr(state, "_S3_REGION", "us-east-1")
    monkeypatch.setattr(state, "_insecure_endpoint_warned", set())
    return _Recorder


async def _store_keys(session, ak=None, sk=None):
    from app.services.config_service import ConfigService
    from app.auth.encryption_key import get_credential_encryption_key

    svc = ConfigService(session, get_credential_encryption_key())
    if ak is not None:
        await svc.set(ssc.ACCESS_KEY_ID_KEY, ak, is_secret=True)
    if sk is not None:
        await svc.set(ssc.SECRET_ACCESS_KEY_KEY, sk, is_secret=True)
    await session.commit()


async def test_custom_endpoint_with_creds_from_config_table(recorder, monkeypatch, _setup_db):
    monkeypatch.setattr(state, "_USE_LOCALSTACK", False)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", "https://s3.example.internal:3900")
    async with _setup_db() as s:
        await _store_keys(s, "GKabc", "sekrit")
        svc = await state._fallback_s3_store(s)
    assert svc.kwargs == {
        "bucket": "bkt",
        "use_localstack": False,
        "region": "us-east-1",
        "endpoint_url": "https://s3.example.internal:3900",
        "access_key_id": "GKabc",
        "secret_access_key": "sekrit",
    }


async def test_keys_are_stored_encrypted(_setup_db):
    from app.models.config import Config

    async with _setup_db() as s:
        await ssc.save(s, "GKabc123", "sekrit-value")
        await s.commit()
        for key, plain in ((ssc.ACCESS_KEY_ID_KEY, "GKabc123"),
                           (ssc.SECRET_ACCESS_KEY_KEY, "sekrit-value")):
            row = await s.get(Config, key)
            assert row.is_secret is True
            assert plain not in row.value


async def test_env_key_pair_is_ignored(recorder, monkeypatch, _setup_db):
    """The old S3_STATE_* env vars are gone: nothing reads them any more."""
    monkeypatch.setattr(state, "_USE_LOCALSTACK", False)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", "https://s3.example.internal")
    monkeypatch.setenv("S3_STATE_ACCESS_KEY_ID", "from-env")
    monkeypatch.setenv("S3_STATE_SECRET_ACCESS_KEY", "from-env")
    async with _setup_db() as s:
        svc = await state._fallback_s3_store(s)
    assert svc.kwargs["access_key_id"] is None
    assert svc.kwargs["secret_access_key"] is None


async def test_localstack_passes_no_creds_so_the_service_injects_test_pair(
    recorder, monkeypatch, _setup_db
):
    """LocalStack's test/test now comes from S3StateService itself (only for
    the bundled LocalStack endpoint) — the router no longer defaults it."""
    monkeypatch.setattr(state, "_USE_LOCALSTACK", True)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", None)
    async with _setup_db() as s:
        svc = await state._fallback_s3_store(s)
    assert svc.kwargs["use_localstack"] is True
    assert svc.kwargs["endpoint_url"] is None
    assert svc.kwargs["access_key_id"] is None
    assert svc.kwargs["secret_access_key"] is None


async def test_localstack_end_to_end_uses_test_creds(monkeypatch, _setup_db):
    """Through the real S3StateService: bundled LocalStack gets test/test."""
    from app.services import s3_state_service as s3mod

    captured: dict = {}
    monkeypatch.setattr(s3mod.boto3, "client", lambda _svc, **kw: captured.update(kw))
    monkeypatch.setattr(state, "_USE_LOCALSTACK", True)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", None)
    async with _setup_db() as s:
        await state._fallback_s3_store(s)
    assert captured["endpoint_url"] == "http://localstack:4566"
    assert (captured["aws_access_key_id"], captured["aws_secret_access_key"]) == ("test", "test")


async def test_localstack_flag_with_custom_endpoint_and_no_creds_sends_no_test_pair(
    monkeypatch, _setup_db
):
    from app.services import s3_state_service as s3mod

    captured: dict = {}
    monkeypatch.setattr(s3mod.boto3, "client", lambda _svc, **kw: captured.update(kw))
    monkeypatch.setattr(state, "_USE_LOCALSTACK", True)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", "https://minio.example.com")
    async with _setup_db() as s:
        await state._fallback_s3_store(s)
    assert captured["endpoint_url"] == "https://minio.example.com"
    assert "aws_access_key_id" not in captured


async def test_real_aws_uses_default_chain(recorder, monkeypatch, _setup_db):
    monkeypatch.setattr(state, "_USE_LOCALSTACK", False)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", None)
    async with _setup_db() as s:
        svc = await state._fallback_s3_store(s)
    assert svc.kwargs["endpoint_url"] is None
    assert svc.kwargs["access_key_id"] is None


@pytest.mark.parametrize("half", ["ak", "sk"])
async def test_half_configured_key_pair_is_refused(recorder, monkeypatch, _setup_db, half):
    monkeypatch.setattr(state, "_USE_LOCALSTACK", False)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", "https://s3.example.internal")
    async with _setup_db() as s:
        await _store_keys(s, **({"ak": "GKabc"} if half == "ak" else {"sk": "sekrit"}))
        with pytest.raises(ssc.PartialS3CredentialsError, match="half-configured"):
            await state._fallback_s3_store(s)


async def test_s3_store_for_awaits_fallback_without_account(recorder, monkeypatch, _setup_db):
    """_s3_store_for (the caller) reaches the async fallback when the
    workspace has no registered AwsAccount."""
    from types import SimpleNamespace

    monkeypatch.setattr(state, "_USE_LOCALSTACK", False)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", "https://s3.example.internal")
    async with _setup_db() as s:
        await _store_keys(s, "GKabc", "sekrit")
        svc = await state._s3_store_for(SimpleNamespace(aws_account_id="global"), s)
    assert svc.kwargs["bucket"] == "bkt"
    assert svc.kwargs["access_key_id"] == "GKabc"


# ─── plaintext http:// warning ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,insecure",
    [
        ("http://garage.internal:3900", True),
        ("HTTP://10.0.0.5:9000", True),
        ("https://garage.internal:3900", False),
        ("http://localhost:9000", False),
        ("http://127.0.0.1:9000", False),
        ("http://[::1]:9000", False),
        ("http://localstack:4566", False),
        (None, False),
    ],
)
def test_is_insecure_endpoint(url, insecure):
    assert state.is_insecure_endpoint(url) is insecure


async def test_warns_once_for_plaintext_remote_endpoint(recorder, monkeypatch, _setup_db, caplog):
    monkeypatch.setattr(state, "_USE_LOCALSTACK", False)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", "http://garage.internal:3900")
    caplog.set_level(logging.WARNING, logger=state.logger.name)
    async with _setup_db() as s:
        await state._fallback_s3_store(s)
        await state._fallback_s3_store(s)
    warnings = [r for r in caplog.records if "plaintext http://" in r.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert "garage.internal" in warnings[0].getMessage()


@pytest.mark.parametrize(
    "url", ["https://garage.internal:3900", "http://localhost:9000", "http://localstack:4566", None]
)
async def test_no_warning_for_https_or_local(recorder, monkeypatch, _setup_db, caplog, url):
    monkeypatch.setattr(state, "_USE_LOCALSTACK", False)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", url)
    caplog.set_level(logging.WARNING, logger=state.logger.name)
    async with _setup_db() as s:
        await state._fallback_s3_store(s)
    assert not [r for r in caplog.records if "plaintext http://" in r.getMessage()]


async def _idle_loop(*_args, **_kwargs):
    import asyncio

    await asyncio.Event().wait()


@pytest.mark.parametrize(
    "url,expect_warning",
    [("http://garage.internal:3900", True), ("https://garage.internal:3900", False)],
)
async def test_api_startup_warns_for_plaintext_remote_endpoint(
    recorder, monkeypatch, caplog, url, expect_warning
):
    """The operator sees the http:// WARNING at boot, not only once the first
    non-AWS workspace happens to touch its state."""
    import app.services.bg_worker as bg_worker
    import app.services.repo_sync as repo_sync
    import app.services.run_worker as run_worker

    for mod, names in (
        (run_worker, ("worker_loop", "reaper_loop", "gauges_loop", "drift_retention_loop")),
        (repo_sync, ("repo_sync_loop",)),
        (bg_worker, ("bg_loop",)),
    ):
        for name in names:
            monkeypatch.setattr(mod, name, _idle_loop)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", url)
    caplog.set_level(logging.WARNING, logger=state.logger.name)

    async with main_mod.lifespan(main_mod.app):
        warnings = [r for r in caplog.records if "plaintext http://" in r.getMessage()]
        assert bool(warnings) is expect_warning
        if expect_warning:
            assert warnings[0].levelno == logging.WARNING


# ─── optional TLS enforcement (state_store.s3.require_tls) ──────────────────


async def _set_require_tls(session, value: bool):
    await ssc.save_require_tls(session, value)
    await session.commit()


@pytest.mark.parametrize("url", ["http://garage.internal:3900", "HTTP://10.0.0.5:9000"])
async def test_require_tls_refuses_plaintext_remote_endpoint(recorder, monkeypatch, _setup_db, url):
    monkeypatch.setattr(state, "_USE_LOCALSTACK", False)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", url)
    async with _setup_db() as s:
        await _set_require_tls(s, True)
        with pytest.raises(ssc.InsecureStateEndpointError, match="https://"):
            await state._fallback_s3_store(s)


@pytest.mark.parametrize(
    "url", ["https://garage.internal:3900", "http://localhost:9000", "http://localstack:4566", None]
)
async def test_require_tls_allows_https_and_local(recorder, monkeypatch, _setup_db, url):
    monkeypatch.setattr(state, "_USE_LOCALSTACK", False)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", url)
    async with _setup_db() as s:
        await _set_require_tls(s, True)
        svc = await state._fallback_s3_store(s)
    assert svc.kwargs["endpoint_url"] == url


async def test_require_tls_defaults_off_so_plaintext_still_builds(recorder, monkeypatch, _setup_db):
    monkeypatch.setattr(state, "_USE_LOCALSTACK", False)
    monkeypatch.setattr(state, "_S3_ENDPOINT_URL", "http://garage.internal:3900")
    async with _setup_db() as s:
        assert await ssc.load_require_tls(s) is False
        svc = await state._fallback_s3_store(s)
    assert svc.kwargs["endpoint_url"] == "http://garage.internal:3900"


async def test_require_tls_roundtrip_is_plain_config_not_secret(_setup_db):
    from app.models.config import Config

    async with _setup_db() as s:
        await _set_require_tls(s, True)
        assert await ssc.load_require_tls(s) is True
        row = await s.get(Config, ssc.REQUIRE_TLS_KEY)
        assert row.is_secret is False
        await _set_require_tls(s, False)
        assert await ssc.load_require_tls(s) is False
