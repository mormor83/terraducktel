"""Proxmox VE cluster CRUD with encrypted API tokens at rest."""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu
from app.auth.rbac import Role, require_role
from app.db import get_db
from app.models.audit_log import AuditLog
from app.models.proxmox_cluster import ProxmoxCluster
from app.models.user import User
from app.schemas.proxmox_cluster import (
    ProxmoxClusterCreate,
    ProxmoxClusterResponse,
    ProxmoxClusterTestResult,
    ProxmoxClusterUpdate,
)
from app.services import account_colors
from app.services import proxmox_cluster_service as svc
from app.services.audit_chain import stamp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/proxmox-clusters", tags=["proxmox-clusters"])


def _to_response(row: ProxmoxCluster) -> ProxmoxClusterResponse:
    # Masked tail is computed from the decrypted secret — cheap, and it means
    # the response reflects a rotation immediately.
    try:
        tail = svc.mask_tail(svc.decrypt_secret(row.api_token_secret_encrypted))
    except RuntimeError:
        tail = "…????"
    return ProxmoxClusterResponse(
        id=row.id,
        business_unit_id=row.business_unit_id,
        slug=row.slug,
        name=row.name,
        description=row.description,
        color=row.color,
        color_effective=account_colors.effective(row.color, row.slug),
        endpoint=row.endpoint,
        api_token_id=row.api_token_id,
        token_secret_masked_tail=tail,
        ssh_username=row.ssh_username or None,
        has_ssh_key=bool(row.ssh_private_key_encrypted),
        tls_insecure=bool(row.tls_insecure),
        ca_cert_pem=row.ca_cert_pem or None,
    )


async def _scoped_cluster(db: AsyncSession, cluster_pk: str, bu: BUScope) -> ProxmoxCluster:
    """Fetch by PK, enforcing the caller's BU scope (404 cross-BU)."""
    row = await db.get(ProxmoxCluster, cluster_pk)
    if row is None or (bu.bu_id is not None and row.business_unit_id != bu.bu_id):
        raise HTTPException(status_code=404, detail="Proxmox cluster not found")
    return row


@router.get("", response_model=list[ProxmoxClusterResponse])
async def list_proxmox_clusters(
    _: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    rows = await svc.list_clusters(db, business_unit_id=bu.bu_id)
    return [_to_response(r) for r in rows]


@router.post("", response_model=ProxmoxClusterResponse, status_code=status.HTTP_201_CREATED)
async def create_proxmox_cluster(
    body: ProxmoxClusterCreate,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    if bu.bu_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set X-Business-Unit header to a specific BU when creating a cluster",
        )
    existing = (
        await db.execute(
            select(ProxmoxCluster).where(
                ProxmoxCluster.business_unit_id == bu.bu_id,
                ProxmoxCluster.slug == body.slug,
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Proxmox cluster '{body.slug}' is already configured in this business unit",
        )
    color = await account_colors.assign_for_bu(db, bu.bu_id, body.color)
    row = ProxmoxCluster(
        id=str(uuid.uuid4()),
        business_unit_id=bu.bu_id,
        slug=body.slug,
        name=body.name,
        description=body.description,
        color=color,
        endpoint=svc.normalize_endpoint(body.endpoint),
        api_token_id=body.api_token_id,
        api_token_secret_encrypted=svc.encrypt_secret(body.api_token_secret),
        ssh_username=(body.ssh_username or "").strip() or None,
        ssh_private_key_encrypted=(
            svc.encrypt_secret(body.ssh_private_key) if body.ssh_private_key else None
        ),
        tls_insecure=body.tls_insecure,
        ca_cert_pem=body.ca_cert_pem or None,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Proxmox cluster '{body.slug}' is already configured",
        )
    await db.refresh(row)
    return _to_response(row)


@router.put("/{cluster_pk}", response_model=ProxmoxClusterResponse)
async def update_proxmox_cluster(
    cluster_pk: str,
    body: ProxmoxClusterUpdate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    row = await _scoped_cluster(db, cluster_pk, bu)
    data = body.model_dump(exclude_unset=True)
    # `name` / `endpoint` / `api_token_id` / `tls_insecure` are NOT NULL columns.
    # An explicit `null` from the client (as opposed to simply omitting the
    # key) means "leave unchanged" here, not "clear" — clearing isn't a valid
    # state for these fields, unlike the secret/SSH/CA fields below.
    for _not_nullable in ("name", "endpoint", "api_token_id", "tls_insecure"):
        if data.get(_not_nullable) is None:
            data.pop(_not_nullable, None)

    new_secret = data.pop("api_token_secret", None)

    # Redirecting the endpoint would otherwise let an admin who never knew the
    # stored secret have /test (or a run) send it to a host they control. So an
    # endpoint change must re-supply the secret, and it is audited. The schema
    # already normalised `endpoint`; normalise the stored value the same way so
    # re-submitting the unchanged endpoint is a no-op, not a "change".
    old_endpoint = row.endpoint
    endpoint_changed = "endpoint" in data and (
        svc.normalize_endpoint(data["endpoint"]) != svc.normalize_endpoint(old_endpoint)
    )
    if endpoint_changed and not new_secret:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="api_token_secret is required when changing the endpoint",
        )
    if new_secret:
        row.api_token_secret_encrypted = svc.encrypt_secret(new_secret)

    # SSH: "" clears, a value re-encrypts, omitted leaves as-is.
    if "ssh_private_key" in data:
        key = data.pop("ssh_private_key")
        row.ssh_private_key_encrypted = svc.encrypt_secret(key) if key else None
    if "ssh_username" in data:
        row.ssh_username = (data.pop("ssh_username") or "").strip() or None
    if row.ssh_private_key_encrypted and not row.ssh_username:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ssh_username is required while an SSH private key is stored",
        )

    if "ca_cert_pem" in data:
        row.ca_cert_pem = data.pop("ca_cert_pem") or None
    if "endpoint" in data:
        row.endpoint = svc.normalize_endpoint(data.pop("endpoint"))
    for k, v in data.items():
        setattr(row, k, v)
    if endpoint_changed:
        # Never include the secret — only who moved the cluster where.
        audit = AuditLog(
            user_id=current_user.id,
            action="proxmox_cluster.endpoint_change",
            resource_type="proxmox_cluster",
            resource_id=row.id,
            details={
                "business_unit_id": row.business_unit_id,
                "slug": row.slug,
                "old_endpoint": old_endpoint,
                "new_endpoint": row.endpoint,
            },
        )
        db.add(audit)
        await stamp(db, audit)
    await db.commit()
    await db.refresh(row)
    return _to_response(row)


@router.delete("/{cluster_pk}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_proxmox_cluster(
    cluster_pk: str,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    row = await _scoped_cluster(db, cluster_pk, bu)
    await db.delete(row)
    await db.commit()


@router.post("/{cluster_pk}/test", response_model=ProxmoxClusterTestResult)
async def test_proxmox_cluster(
    cluster_pk: str,
    _: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Probe GET /api2/json/version with the stored token. Never raises for
    remote/auth failures — returns ok=false with a truncated reason."""
    row = await _scoped_cluster(db, cluster_pk, bu)
    try:
        creds = await svc.get_cluster_credentials(db, row.id)
        if creds is None:
            raise RuntimeError("cluster credentials unavailable")
        version = await svc.probe_version(creds)
        return ProxmoxClusterTestResult(
            ok=True, detail=f"Connected — Proxmox VE {version}", version=version
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Proxmox connection test failed for cluster %s", row.slug, exc_info=True)
        return ProxmoxClusterTestResult(ok=False, detail=str(e)[:200])
