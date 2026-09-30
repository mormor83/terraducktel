"""Credentials for the fallback S3 state bucket, stored in the `config` table.

The fallback bucket (`S3_STATE_BUCKET`) holds state for every workspace
without a linked AwsAccount — i.e. every Azure/GCP/Proxmox workspace. Its
endpoint (`S3_ENDPOINT_URL`) is a plain env var because it isn't secret; the
key pair lives here instead, encrypted (`is_secret=True`) by ConfigService
with fingerprinted history, and is set from Settings → State store like any
other integration.

The bucket is deployment-wide, not per Business Unit, so the keys are global
(no `bu.<slug>.` prefix) and only a superadmin may change them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption_key import get_credential_encryption_key
from app.services.config_service import ConfigService

ACCESS_KEY_ID_KEY = "state_store.s3.access_key_id"
SECRET_ACCESS_KEY_KEY = "state_store.s3.secret_access_key"


class PartialS3CredentialsError(RuntimeError):
    """Exactly one half of the fallback-bucket key pair is configured."""


@dataclass(frozen=True)
class FallbackS3Credentials:
    access_key_id: Optional[str]
    secret_access_key: Optional[str]

    @property
    def configured(self) -> bool:
        return bool(self.access_key_id and self.secret_access_key)

    @property
    def partial(self) -> bool:
        return bool(self.access_key_id) != bool(self.secret_access_key)


def _svc(db: AsyncSession) -> ConfigService:
    return ConfigService(db, get_credential_encryption_key())


async def load(db: AsyncSession) -> FallbackS3Credentials:
    """Read the key pair (decrypted). Blank values count as unset."""
    svc = _svc(db)
    ak = (await svc.get(ACCESS_KEY_ID_KEY) or "").strip() or None
    sk = (await svc.get(SECRET_ACCESS_KEY_KEY) or "").strip() or None
    return FallbackS3Credentials(access_key_id=ak, secret_access_key=sk)


async def save(
    db: AsyncSession,
    access_key_id: str,
    secret_access_key: str,
    *,
    updated_by: Optional[str] = None,
) -> None:
    """Store both halves together — a half-updated pair is never written."""
    ak, sk = access_key_id.strip(), secret_access_key.strip()
    if not ak or not sk:
        raise ValueError("access_key_id and secret_access_key are both required")
    svc = _svc(db)
    await svc.set(
        ACCESS_KEY_ID_KEY, ak, is_secret=True,
        description="Access key ID for the fallback S3 state bucket (S3_ENDPOINT_URL).",
        updated_by=updated_by,
    )
    await svc.set(
        SECRET_ACCESS_KEY_KEY, sk, is_secret=True,
        description="Secret access key for the fallback S3 state bucket (S3_ENDPOINT_URL).",
        updated_by=updated_by,
    )


async def clear(db: AsyncSession) -> None:
    svc = _svc(db)
    await svc.delete(ACCESS_KEY_ID_KEY)
    await svc.delete(SECRET_ACCESS_KEY_KEY)
