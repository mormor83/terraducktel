"""Service layer for GcpProject.

Shares the same Fernet/HKDF crypto context as the other cloud-account services
— a distinct HKDF ``salt`` derives an independent Fernet key so a GCP SA key
cannot be confused with an AWS access key or an Azure SP secret even if a row's
ciphertext were swapped at the DB level. Uses the fail-loud
``get_credential_encryption_key()`` helper (parity with aws_account_service).
"""
from __future__ import annotations

import base64
import json
from typing import Optional

from cryptography.exceptions import InvalidKey
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption_key import get_credential_encryption_key
from app.models.gcp_project import GcpProject


def _fernet() -> Fernet:
    key = get_credential_encryption_key()
    if len(key) < 16:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must be at least 16 bytes")
    try:
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"terraducktel-gcp-credentials-v1",
            info=b"fernet-key",
        ).derive(key)
    except InvalidKey as e:  # pragma: no cover — HKDF.derive can't currently raise for valid lens
        raise RuntimeError("HKDF derivation failed for GCP credentials") from e
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError("GCP credential decryption failed") from e


def parse_sa_json(raw: str) -> tuple[str, str]:
    """Return (project_id, client_email) from a service-account key JSON.

    Assumes the JSON already passed schema validation (GcpProjectCreate).
    """
    data = json.loads(raw)
    return data.get("project_id", ""), data.get("client_email", "")


def mask_sa(client_email: str) -> str:
    """The SA email is an identifier, not a secret — surface it directly."""
    return client_email or "(unknown)"


async def list_projects(
    session: AsyncSession, business_unit_id: Optional[str] = None
) -> list[GcpProject]:
    stmt = select(GcpProject).order_by(GcpProject.name)
    if business_unit_id is not None:
        stmt = stmt.where(GcpProject.business_unit_id == business_unit_id)
    return list((await session.execute(stmt)).scalars().all())


async def get_project(session: AsyncSession, project_pk: str) -> Optional[GcpProject]:
    return await session.get(GcpProject, project_pk)


async def get_project_credentials(
    session: AsyncSession, project_pk: str
) -> tuple[str, str] | None:
    """Return (project_id, service_account_json) plaintext.

    Used at run-time only by the executor service so it can write the SA key to
    a 0600 file and export GOOGLE_APPLICATION_CREDENTIALS / GOOGLE_PROJECT.
    """
    proj = await get_project(session, project_pk)
    if proj is None:
        return None
    return (proj.project_id, decrypt_secret(proj.service_account_json_encrypted))
