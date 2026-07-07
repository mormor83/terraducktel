"""Audit log read API."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.auth.bu_context import BUScope, current_bu
from app.auth.rbac import Role, require_role
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.audit import AuditLogEntry, AuditLogListResponse

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("/verify")
async def verify_audit_chain(
    limit: int | None = Query(None, ge=1, le=100000),
    current_user: User = Depends(require_role(Role.admin)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Walk the audit-log hash chain and report any tampering.

    Returns `{"ok": true, "total": N, "broken_at": []}` on a clean chain. If
    any row's stored hash doesn't reproduce, returns the first few offending
    row ids — that's the breakpoint for forensic review.

    Superadmin-only: the chain is global (spans all BUs), so its total row
    count and offending row ids would leak cross-tenant metadata to a per-BU
    admin.
    """
    if not bool(getattr(current_user, "is_superadmin", False)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Audit-chain verification is superadmin-only",
        )
    from app.services.audit_chain import verify_chain

    return await verify_chain(db, limit=limit)


@router.get("", response_model=AuditLogListResponse)
async def list_audit_logs(
    run_id: str | None = Query(None),
    workspace_id: str | None = Query(None),
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """List audit entries (admin). Filter by run_id and/or workspace_id.

    Scoped to the caller's Business Unit: a non-superadmin admin only sees
    entries tied to a workspace in their BU. Cross-cutting entries with no
    workspace (identity / key / BU management) are visible to superadmin only,
    so one tenant's admin can't read another tenant's audit trail.
    """
    q = select(AuditLog).order_by(AuditLog.created_at.desc())
    if run_id:
        q = q.where(AuditLog.resource_id == run_id)
    if workspace_id:
        q = q.where(AuditLog.workspace_id == workspace_id)
    if bu.bu_id is not None:
        q = q.where(
            AuditLog.workspace_id.in_(
                select(Workspace.id).where(Workspace.business_unit_id == bu.bu_id)
            )
        )
    result = await db.execute(q)
    rows = result.scalars().all()
    return AuditLogListResponse(items=[AuditLogEntry.model_validate(r) for r in rows])
