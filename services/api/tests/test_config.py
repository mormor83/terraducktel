import pytest
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# FIX B — HKDF key derivation tests
# ---------------------------------------------------------------------------

def test_short_key_raises_value_error():
    from app.services.config_service import ConfigService
    import pytest
    with pytest.raises(ValueError, match="at least 16 bytes"):
        ConfigService(None, encryption_key=b"short")


def test_long_key_works():
    from app.services.config_service import ConfigService
    svc = ConfigService(None, encryption_key=b"a" * 64)
    assert svc._fernet is not None


# ---------------------------------------------------------------------------
# FIX A — history must not store plaintext secrets
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_history_does_not_leak_secret_plaintext(db_session):
    from app.services.config_service import ConfigService
    from app.models.config import ConfigHistory
    from sqlalchemy import select
    svc = ConfigService(db_session, encryption_key=b"test_key_exactly_32_bytes_long!!")
    await svc.set("slack.webhook", "https://hooks.slack.com/secret1", is_secret=True)
    await svc.set("slack.webhook", "https://hooks.slack.com/secret2", is_secret=True)
    result = await db_session.execute(select(ConfigHistory).where(ConfigHistory.key == "slack.webhook"))
    history = result.scalars().all()
    assert len(history) >= 1
    for h in history:
        assert "hooks.slack.com" not in (h.old_value or "")
        assert "hooks.slack.com" not in (h.new_value or "")
        assert "[REDACTED-" in (h.old_value or "") or h.old_value is None


class TestConfigService:
    @pytest.mark.asyncio
    async def test_set_and_get_plaintext_config(self, db_session):
        """Non-secret values stored and retrieved as-is"""
        from app.services.config_service import ConfigService
        svc = ConfigService(db_session, encryption_key=b"test_key_exactly_32_bytes_long!!")
        await svc.set("smtp.host", "smtp.example.com", is_secret=False)
        value = await svc.get("smtp.host")
        assert value == "smtp.example.com"

    @pytest.mark.asyncio
    async def test_set_and_get_secret_config(self, db_session):
        """Secret values are stored encrypted, retrieved decrypted"""
        from app.services.config_service import ConfigService
        from app.models.config import Config as ConfigModel
        svc = ConfigService(db_session, encryption_key=b"test_key_exactly_32_bytes_long!!")
        await svc.set("slack.webhook_url", "https://hooks.slack.com/secret", is_secret=True)
        # Raw DB value should be encrypted
        raw = await db_session.get(ConfigModel, "slack.webhook_url")
        assert raw.value != "https://hooks.slack.com/secret"
        # Retrieved value should be decrypted
        value = await svc.get("slack.webhook_url")
        assert value == "https://hooks.slack.com/secret"

    @pytest.mark.asyncio
    async def test_config_history_recorded(self, db_session):
        """Updating a config key records old and new value in history"""
        from app.services.config_service import ConfigService
        svc = ConfigService(db_session, encryption_key=b"test_key_exactly_32_bytes_long!!")
        await svc.set("smtp.host", "first.example.com", is_secret=False)
        await svc.set("smtp.host", "second.example.com", is_secret=False)
        history = await svc.get_history("smtp.host")
        assert len(history) >= 1
        assert history[-1].old_value == "first.example.com"
        assert history[-1].new_value == "second.example.com"

    @pytest.mark.asyncio
    async def test_get_missing_key_returns_none(self, db_session):
        from app.services.config_service import ConfigService
        svc = ConfigService(db_session, encryption_key=b"test_key_exactly_32_bytes_long!!")
        value = await svc.get("nonexistent.key")
        assert value is None

    @pytest.mark.asyncio
    async def test_cache_returns_same_value_without_db_hit(self, db_session):
        """Second get() within TTL should use cache, not DB"""
        from app.services.config_service import ConfigService
        from app.models.config import Config as ConfigModel
        svc = ConfigService(db_session, encryption_key=b"test_key_exactly_32_bytes_long!!")
        await svc.set("smtp.port", "587", is_secret=False)
        val1 = await svc.get("smtp.port")
        # Manually corrupt DB to verify cache is used
        raw = await db_session.get(ConfigModel, "smtp.port")
        raw.value = "CORRUPTED"
        val2 = await svc.get("smtp.port")
        assert val1 == val2 == "587"
