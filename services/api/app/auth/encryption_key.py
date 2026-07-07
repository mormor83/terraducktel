"""Credential encryption key loader (fail-loud).

Mirrors the JWT_SECRET_KEY pattern in app/auth/jwt.py: never use a hardcoded
fallback in production code paths. If the operator forgot to configure
CREDENTIAL_ENCRYPTION_KEY, refuse to start instead of silently using a
predictable test key (which would let an attacker decrypt all stored
credentials).
"""
import os


def get_credential_encryption_key() -> bytes:
    """Return CREDENTIAL_ENCRYPTION_KEY as bytes.

    Raises RuntimeError when env var is unset or empty.
    """
    raw = os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
    if not raw:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must be configured")
    return raw.encode("utf-8")
