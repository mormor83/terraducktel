"""Approvals router: approve/reject runs awaiting approval.

Any operator+ user can approve or reject — 4-eyes was removed.
"""
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu, scoped_run
from app.db import get_db
from app.auth.rbac import Role, require_role
from app.auth.jwt import create_access_token
from app.models.user import User
from app.schemas.run import ApprovalBody
from app.services.approval_service import ApprovalService
from app.services import api_key_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/runs", tags=["approvals"])
_approval = ApprovalService()


@router.post("/{run_id}/approve")
async def approve_run(
    run_id: str,
    request: Request,
    body: ApprovalBody | None = None,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Approve a run so the apply phase can proceed. Operator+ role."""
    run = await scoped_run(run_id, bu, db)

    # Approving advances a run into apply — API keys need the `apply` tier and
    # the workspace must be in their allowlist.
    api_key_service.enforce(request, need="apply", workspace_id=run.workspace_id)

    comment = body.comment if body else None
    await _approval.approve(db, run, current_user, comment=comment)
    await db.commit()
    await db.refresh(run)

    # Queue the apply-phase job. The worker (run_worker.py) restores the saved
    # tfplan_b64 and runs `terraform apply tfplan` against the exact plan the
    # approver reviewed. No more inline docker-run from the request handler.
    from app.services.run_worker import enqueue_job

    await enqueue_job(db, run_id=run.id, phase="apply")
    await db.commit()

    return {"status": "approved", "run_id": run.id, "new_status": run.status.value}


@router.post("/{run_id}/reject")
async def reject_run(
    run_id: str,
    request: Request,
    body: ApprovalBody | None = None,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Reject a run that is awaiting approval. Operator+ role."""
    run = await scoped_run(run_id, bu, db)

    api_key_service.enforce(request, need="apply", workspace_id=run.workspace_id)

    comment = body.comment if body else None
    await _approval.reject(db, run, current_user, comment=comment)
    await db.commit()
    await db.refresh(run)
    return {"status": "rejected", "run_id": run.id, "new_status": run.status.value}
