"""CRUD + encryption helpers for AwsAccount.

Encryption mirrors the ConfigService scheme: HKDF-derived Fernet key from the
process-level CREDENTIAL_ENCRYPTION_KEY env var. Plaintext credentials NEVER
hit disk and never appear in API responses — only the masked tail is returned.
"""
from __future__ import annotations

import base64
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption_key import get_credential_encryption_key
from app.models.aws_account import AwsAccount


def _fernet() -> Fernet:
    key = get_credential_encryption_key()
    if len(key) < 16:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must be at least 16 bytes")
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"terraducktel-aws-credentials-v1",
        info=b"fernet-key",
    ).derive(key)
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError("AWS credential decryption failed") from e


def mask_access_key_tail(plain: str) -> str:
    """Return only the last 4 chars of the access key id for UI display."""
    if not plain:
        return ""
    return f"AKIA…{plain[-4:]}" if len(plain) > 4 else "***"


async def get_account_by_account_id(
    session: AsyncSession,
    account_id: str,
    business_unit_id: str | None = None,
) -> Optional[AwsAccount]:
    """Fetch an AWS account row by its 12-digit AWS id.

    Since migration 018 the same `account_id` may legally exist in multiple
    BUs (`(business_unit_id, account_id)` is unique, not `account_id` alone).
    Pass `business_unit_id` whenever the caller knows which BU owns the
    workspace — picking the wrong BU's credentials would silently drive runs
    against the other tenant.
    """
    stmt = select(AwsAccount).where(AwsAccount.account_id == account_id)
    if business_unit_id is not None:
        stmt = stmt.where(AwsAccount.business_unit_id == business_unit_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def list_accounts(
    session: AsyncSession, business_unit_id: str | None = None
) -> list[AwsAccount]:
    """List AWS accounts, optionally filtered to one Business Unit.

    `business_unit_id=None` returns every account (superadmin "all BUs" view).
    Anything else applies a WHERE clause on the FK.
    """
    stmt = select(AwsAccount).order_by(AwsAccount.name)
    if business_unit_id is not None:
        stmt = stmt.where(AwsAccount.business_unit_id == business_unit_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_account_credentials(
    session: AsyncSession,
    account_id: str,
    business_unit_id: str | None = None,
) -> tuple[str, str] | None:
    """Return (access_key_id, secret_access_key) plaintext for the executor.

    Used at run-time only by the executor service. Not exposed via API.
    Pass `business_unit_id` to disambiguate when the same `account_id` is
    registered in multiple BUs.
    """
    acc = await get_account_by_account_id(session, account_id, business_unit_id)
    if acc is None:
        return None
    return decrypt_secret(acc.access_key_id_encrypted), decrypt_secret(acc.secret_access_key_encrypted)
