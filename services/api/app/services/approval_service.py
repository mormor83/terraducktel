"""Approval workflow: FSM transitions + audit records.

4-eyes (triggerer ≠ approver) was removed. Any user with `operator+` role can
approve or reject any run, including their own. The runs.reviewer_id column
is preserved for back-compat on historical rows but is no longer set at
trigger time and no longer enforced at approval time.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.run import Run, RunStatus
from app.models.user import User


# Kept as an empty set so legacy callers that still import the symbol (and
# check membership) keep working without throwing. Membership is never true.
_FOUR_EYES_BRANCHES: frozenset[str] = frozenset()


async def _write_audit(
    session: AsyncSession,
    *,
    user_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str,
    workspace_id: str | None,
    details: dict | None = None,
) -> None:
    from app.services.audit_chain import stamp

    row = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        workspace_id=workspace_id,
        details=details,
    )
    session.add(row)
    await stamp(session, row)


class ApprovalService:
    async def approve(
        self,
        session: AsyncSession,
        run: Run,
        current_user: User,
        *,
        comment: str | None = None,
    ) -> Run:
        if run.status != RunStatus.AWAITING_APPROVAL:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Run is in '{run.status.value}' state, not awaiting approval",
            )
        run.transition(RunStatus.APPLYING)
        await _write_audit(
            session,
            user_id=current_user.id,
            action="approve",
            resource_type="run",
            resource_id=run.id,
            workspace_id=run.workspace_id,
            details={"comment": comment} if comment else None,
        )
        return run

    async def system_auto_approve(
        self,
        session: AsyncSession,
        run: Run,
        *,
        summary: dict,
        skip_apply: bool,
    ) -> Run:
        """Auto-approve a run with no human in the loop.

        Used when the run was created with `auto_approve_if_no_changes=True`
        and the plan came back 0/0/0 with all gates green. Writes an audit
        entry attributed to `user_id=None` (system) carrying the plan
        summary + skip_apply flag so a later reviewer can always trace why
        no human appears on the run.

        When `skip_apply` is true the run is short-circuited to APPLIED
        without spawning an executor; otherwise it transitions to APPLYING
        and the caller is expected to enqueue an apply job exactly like a
        normal approval.
        """
        if run.status != RunStatus.AWAITING_APPROVAL:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Run is in '{run.status.value}' state, not awaiting approval",
            )
        run.transition(RunStatus.APPLYING)
        details = {
            "auto_approved": True,
            "plan_summary": summary,
            "skip_apply": skip_apply,
        }
        await _write_audit(
            session,
            user_id=None,
            action="auto_approve",
            resource_type="run",
            resource_id=run.id,
            workspace_id=run.workspace_id,
            details=details,
        )
        if skip_apply:
            # Walk straight through to APPLIED — the FSM requires APPLYING →
            # APPLIED, which we've now satisfied. A second audit entry marks
            # the synthetic completion explicitly.
            run.transition(RunStatus.APPLIED)
            await _write_audit(
                session,
                user_id=None,
                action="auto_apply_skipped",
                resource_type="run",
                resource_id=run.id,
                workspace_id=run.workspace_id,
                details={"reason": "no_changes"},
            )
        return run

    async def reject(
        self,
        session: AsyncSession,
        run: Run,
        current_user: User,
        *,
        comment: str | None = None,
    ) -> Run:
        if run.status != RunStatus.AWAITING_APPROVAL:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Run is in '{run.status.value}' state, not awaiting approval",
            )
        run.transition(RunStatus.CANCELLED)
        await _write_audit(
            session,
            user_id=current_user.id,
            action="reject",
            resource_type="run",
            resource_id=run.id,
            workspace_id=run.workspace_id,
            details={"comment": comment} if comment else None,
        )
        return run
