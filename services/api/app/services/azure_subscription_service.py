"""Service layer for AzureSubscription.

Shares the same Fernet/HKDF crypto context as aws_account_service — we
derive different Fernet keys per resource family via the HKDF `salt`, so
an AWS access key and an Azure SP secret cannot be confused with each
other even if a row's ciphertext is swapped at the DB level.
"""
from __future__ import annotations

import base64
import os
from typing import Optional

from cryptography.exceptions import InvalidKey
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.azure_subscription import AzureSubscription


def _fernet() -> Fernet:
    raw = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "")
    if not raw:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY env var must be set to encrypt Azure SP secrets"
        )
    key = raw.encode("utf-8")
    if len(key) < 16:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must be at least 16 bytes")
    try:
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"terraducktel-azure-credentials-v1",
            info=b"fernet-key",
        ).derive(key)
    except InvalidKey as e:  # pragma: no cover — HKDF.derive can't currently raise for valid lens
        raise RuntimeError("HKDF derivation failed for Azure credentials") from e
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError("Azure credential decryption failed") from e


def mask_secret_tail(plain: str) -> str:
    """Return only the last 4 chars of the SP secret for UI display."""
    if not plain:
        return ""
    return f"…{plain[-4:]}" if len(plain) > 4 else "***"


async def list_subscriptions(
    session: AsyncSession, business_unit_id: Optional[str] = None
) -> list[AzureSubscription]:
    stmt = select(AzureSubscription).order_by(AzureSubscription.name)
    if business_unit_id is not None:
        stmt = stmt.where(AzureSubscription.business_unit_id == business_unit_id)
    return list((await session.execute(stmt)).scalars().all())


async def get_subscription(
    session: AsyncSession, sub_pk: str
) -> Optional[AzureSubscription]:
    return await session.get(AzureSubscription, sub_pk)


async def get_subscription_credentials(
    session: AsyncSession, sub_pk: str
) -> tuple[str, str, str, str] | None:
    """Return (subscription_id, tenant_id, client_id, client_secret) plaintext.

    Used at run-time only by the executor service so it can populate the
    standard ARM_* env vars consumed by terraform's azurerm provider.
    """
    sub = await get_subscription(session, sub_pk)
    if sub is None:
        return None
    return (
        sub.subscription_id,
        sub.tenant_id,
        sub.client_id,
        decrypt_secret(sub.client_secret_encrypted),
    )
