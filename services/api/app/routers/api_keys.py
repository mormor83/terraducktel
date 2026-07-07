"""API keys router: admin-minted, BU-scoped automation credentials.

All endpoints require the admin role and are scoped to the caller's current
Business Unit (superadmins may target any BU via `X-Business-Unit`). The
plaintext token is returned exactly once, on creation. Creation and revocation
are written to the tamper-evident audit log.

Note: API keys themselves can never reach these endpoints — the router carries
a blanket `forbid_api_keys` dependency, so key management stays interactive-only
even for `admin`-tier keys (which otherwise satisfy `require_role(admin)`).
Letting a key mint or revoke keys would be a privilege-escalation hole.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu
from app.auth.rbac import Role, require_role
from app.db import get_db
from app.models.api_key import APIKey
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.api_key import APIKeyCreate, APIKeyCreateResponse, APIKeyResponse
from app.services import api_key_service
from app.services.audit_chain import stamp

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/api-keys",
    tags=["api-keys"],
    # Identity surface — interactive-only, even for admin-tier keys.
    dependencies=[Depends(api_key_service.forbid_api_keys("manage API keys"))],
)


def _require_bu(bu: BUScope) -> str:
    """API keys are always bound to one concrete BU — reject the 'all' scope."""
    if bu.bu_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select a specific Business Unit (X-Business-Unit) to manage API keys",
        )
    return bu.bu_id


@router.get("", response_model=list[APIKeyResponse])
async def list_api_keys(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """List API keys in the current BU (masked — never the token itself)."""
    bu_id = _require_bu(bu)
    rows = (
        await db.execute(
            select(APIKey)
            .where(APIKey.business_unit_id == bu_id)
            .order_by(APIKey.created_at.desc())
        )
    ).scalars().all()
    return rows


@router.post("", response_model=APIKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: APIKeyCreate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Mint a new API key bound to the current BU. Returns the plaintext once."""
    bu_id = _require_bu(bu)

    # Validate the workspace allowlist: every id must belong to this BU.
    workspace_ids = body.workspace_ids or None
    if workspace_ids:
        found = (
            await db.execute(
                select(Workspace.id).where(
                    Workspace.id.in_(workspace_ids),
                    Workspace.business_unit_id == bu_id,
                )
            )
        ).scalars().all()
        missing = set(workspace_ids) - set(found)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Workspaces not in this BU: {', '.join(sorted(missing))}",
            )

    expires_at = body.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="expires_at must be in the future",
            )

    plaintext, prefix, token_hash = api_key_service.generate_token()
    key = APIKey(
        name=body.name,
        token_prefix=prefix,
        token_hash=token_hash,
        # The key authenticates as the admin who minted it, narrowed by the
        # capability ceiling + workspace allowlist below.
        user_id=current_user.id,
        business_unit_id=bu_id,
        capability=body.capability,
        workspace_ids=workspace_ids,
        expires_at=expires_at,
        created_by=current_user.id,
    )
    db.add(key)
    await db.flush()

    audit = AuditLog(
        user_id=current_user.id,
        action="api_key.create",
        resource_type="api_key",
        resource_id=key.id,
        details={
            "name": key.name,
            "capability": key.capability,
            "business_unit_id": bu_id,
            "workspace_ids": workspace_ids,
            "token_prefix": prefix,
        },
    )
    db.add(audit)
    await stamp(db, audit)
    await db.commit()
    await db.refresh(key)

    base = APIKeyResponse.model_validate(key, from_attributes=True)
    return APIKeyCreateResponse(**base.model_dump(), token=plaintext)


@router.post(
    "/{key_id}/regenerate",
    response_model=APIKeyCreateResponse,
    status_code=status.HTTP_200_OK,
)
async def regenerate_api_key(
    key_id: str,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Rotate a live key's secret in place. Keeps the key's name, capability,
    workspace scope and expiry; mints a fresh token (returned once) and resets
    last-used. The old token stops working immediately.

    Regenerate only *replaces* an active key's secret — it never revives a dead
    one. A revoked key stays revoked and an expired key stays expired (rotating
    the secret wouldn't move the expiry), so both are rejected here: create a new
    key instead."""
    bu_id = _require_bu(bu)
    key = await db.get(APIKey, key_id)
    if key is None or key.business_unit_id != bu_id:
        raise HTTPException(status_code=404, detail="API key not found")
    if key.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot regenerate a revoked key — create a new one instead.",
        )
    if not api_key_service.is_active(key):
        # Not revoked (handled above) → it's expired. The expiry is unchanged by
        # a rotation, so the new secret would be born expired.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot regenerate an expired key — create a new one instead.",
        )

    old_prefix = key.token_prefix
    plaintext, prefix, token_hash = api_key_service.generate_token()
    key.token_prefix = prefix
    key.token_hash = token_hash
    # Fresh secret → the old secret's usage trail no longer applies.
    key.last_used_at = None

    audit = AuditLog(
        user_id=current_user.id,
        action="api_key.regenerate",
        resource_type="api_key",
        resource_id=key.id,
        details={
            "name": key.name,
            "capability": key.capability,
            "business_unit_id": bu_id,
            "old_token_prefix": old_prefix,
            "token_prefix": prefix,
        },
    )
    db.add(audit)
    await stamp(db, audit)
    await db.commit()
    await db.refresh(key)

    base = APIKeyResponse.model_validate(key, from_attributes=True)
    return APIKeyCreateResponse(**base.model_dump(), token=plaintext)


@router.delete("/{key_id}", status_code=status.HTTP_200_OK)
async def revoke_api_key(
    key_id: str,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Soft-revoke an API key. Idempotent within the current BU."""
    bu_id = _require_bu(bu)
    key = await db.get(APIKey, key_id)
    if key is None or key.business_unit_id != bu_id:
        raise HTTPException(status_code=404, detail="API key not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        audit = AuditLog(
            user_id=current_user.id,
            action="api_key.revoke",
            resource_type="api_key",
            resource_id=key.id,
            details={"name": key.name, "token_prefix": key.token_prefix},
        )
        db.add(audit)
        await stamp(db, audit)
        await db.commit()
    return {"status": "revoked", "id": key.id}
