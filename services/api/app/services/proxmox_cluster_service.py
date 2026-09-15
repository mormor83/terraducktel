"""Service layer for ProxmoxCluster.

Same Fernet/HKDF crypto context as the other provider services, with its own
salt so a Proxmox token secret can't be replayed as an AWS/Azure/GCP secret.
Also owns endpoint normalisation and the live connection probe used by the
router's /test endpoint (kept here so tests can monkeypatch one function).
"""
from __future__ import annotations

import base64
import ssl
from dataclasses import dataclass
from typing import Optional

import httpx
from cryptography.exceptions import InvalidKey
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption_key import get_credential_encryption_key
from app.models.proxmox_cluster import ProxmoxCluster

_API_SUFFIX = "/api2/json"
PROBE_TIMEOUT_S = 10.0


def _fernet() -> Fernet:
    key = get_credential_encryption_key()
    if len(key) < 16:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must be at least 16 bytes")
    try:
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"terraducktel-proxmox-credentials-v1",
            info=b"fernet-key",
        ).derive(key)
    except InvalidKey as e:  # pragma: no cover
        raise RuntimeError("HKDF derivation failed for Proxmox credentials") from e
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError("Proxmox credential decryption failed") from e


def normalize_endpoint(raw: str) -> str:
    """Strip trailing slashes and any `/api2/json` suffix so we store the bare
    origin. bpg wants the origin; Telmate wants origin + /api2/json — the
    executor derives the latter."""
    v = (raw or "").strip().rstrip("/")
    if v.endswith(_API_SUFFIX):
        v = v[: -len(_API_SUFFIX)].rstrip("/")
    return v


def mask_tail(value: str) -> str:
    """Last 4 chars, ellipsis-prefixed — enough to tell two tokens apart."""
    return f"…{value[-4:]}" if len(value) > 4 else "…"


@dataclass(frozen=True)
class ProxmoxCredentials:
    endpoint: str
    token_id: str
    token_secret: str
    tls_insecure: bool
    ssh_username: Optional[str]
    ssh_private_key: Optional[str]
    ca_cert_pem: Optional[str]


async def list_clusters(
    session: AsyncSession, business_unit_id: Optional[str] = None
) -> list[ProxmoxCluster]:
    stmt = select(ProxmoxCluster).order_by(ProxmoxCluster.name)
    if business_unit_id is not None:
        stmt = stmt.where(ProxmoxCluster.business_unit_id == business_unit_id)
    return list((await session.execute(stmt)).scalars().all())


async def get_cluster(session: AsyncSession, cluster_pk: str) -> Optional[ProxmoxCluster]:
    return await session.get(ProxmoxCluster, cluster_pk)


async def get_cluster_credentials(
    session: AsyncSession, cluster_pk: str
) -> ProxmoxCredentials | None:
    """Plaintext credentials for the executor service. Run-time only."""
    row = await get_cluster(session, cluster_pk)
    if row is None:
        return None
    return ProxmoxCredentials(
        endpoint=row.endpoint,
        token_id=row.api_token_id,
        token_secret=decrypt_secret(row.api_token_secret_encrypted),
        tls_insecure=bool(row.tls_insecure),
        ssh_username=row.ssh_username or None,
        ssh_private_key=(
            decrypt_secret(row.ssh_private_key_encrypted)
            if row.ssh_private_key_encrypted
            else None
        ),
        ca_cert_pem=row.ca_cert_pem or None,
    )


def _verify_for(creds: ProxmoxCredentials) -> bool | ssl.SSLContext:
    if creds.tls_insecure:
        return False
    if creds.ca_cert_pem:
        return ssl.create_default_context(cadata=creds.ca_cert_pem)
    return True


async def probe_version(creds: ProxmoxCredentials) -> str:
    """GET /api2/json/version with the API token. Returns the version string.
    Raises httpx errors / RuntimeError on failure — the router turns those
    into `ok=false` results."""
    headers = {"Authorization": f"PVEAPIToken={creds.token_id}={creds.token_secret}"}
    async with httpx.AsyncClient(
        verify=_verify_for(creds), timeout=PROBE_TIMEOUT_S, follow_redirects=False
    ) as client:
        r = await client.get(f"{creds.endpoint}{_API_SUFFIX}/version", headers=headers)
    if r.status_code == 401:
        raise RuntimeError("401 Unauthorized — check token id / secret and its privileges")
    r.raise_for_status()
    data = r.json().get("data") or {}
    version = data.get("version") or "unknown"
    release = data.get("release")
    return f"{version}-{release}" if release else version
