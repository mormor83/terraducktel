"""Phase 1 critical: CREDENTIAL_ENCRYPTION_KEY must be fail-loud.

Mirrors the JWT_SECRET_KEY pattern: missing env var → RuntimeError.
No hardcoded fallback in production code paths.
"""
import pytest


def test_missing_credential_encryption_key_raises_runtime_error(monkeypatch):
    """When CREDENTIAL_ENCRYPTION_KEY is not set, helper must raise RuntimeError."""
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)

    from app.auth.encryption_key import get_credential_encryption_key

    with pytest.raises(RuntimeError, match="CREDENTIAL_ENCRYPTION_KEY must be configured"):
        get_credential_encryption_key()


def test_present_credential_encryption_key_returns_bytes(monkeypatch):
    """When CREDENTIAL_ENCRYPTION_KEY is set, helper returns the value as bytes."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test_key_exactly_32_bytes_long!!")

    from app.auth.encryption_key import get_credential_encryption_key

    result = get_credential_encryption_key()
    assert result == b"test_key_exactly_32_bytes_long!!"
    assert isinstance(result, bytes)
