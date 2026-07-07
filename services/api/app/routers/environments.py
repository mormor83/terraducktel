"""Environment promotion router: promote workspace configs across stages."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.auth.bu_context import BUScope, current_bu, scoped_workspace
from app.auth.rbac import Role, require_role
from app.models.workspace import Workspace
from app.models.run import Run, RunStatus
from app.models.user import User

router = APIRouter(prefix="/api/v1/environments", tags=["environments"])

PROMOTION_ORDER = ["dev", "staging", "prod"]


@router.get("")
async def list_environments(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """List workspaces grouped by environment stage (scoped to the caller's BU)."""
    stmt = select(Workspace).order_by(Workspace.environment, Workspace.name)
    if bu.bu_id is not None:
        stmt = stmt.where(Workspace.business_unit_id == bu.bu_id)
    result = await db.execute(stmt)
    workspaces = result.scalars().all()
    grouped: dict[str, list[dict]] = {}
    for ws in workspaces:
        env = ws.environment
        if env not in grouped:
            grouped[env] = []
        grouped[env].append({"id": ws.id, "name": ws.name, "drift_status": ws.drift_status})
    return {"environments": grouped, "promotion_order": PROMOTION_ORDER}


@router.post("/{workspace_id}/promote")
async def promote_workspace(
    workspace_id: str,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Promote a workspace to the next environment stage.

    Copies the workspace config to the next stage and triggers a plan run.
    Promotion chain: dev -> staging -> prod.
    """
    ws = await scoped_workspace(workspace_id, bu, db)

    current_env = ws.environment.lower()
    if current_env not in PROMOTION_ORDER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Environment '{ws.environment}' is not in the promotion chain",
        )
    idx = PROMOTION_ORDER.index(current_env)
    if idx >= len(PROMOTION_ORDER) - 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Workspace is already at the final stage: {ws.environment}",
        )

    next_env = PROMOTION_ORDER[idx + 1]

    # Scope the target lookup to the source workspace's BU so a name collision
    # across BUs can't alias the promotion onto another tenant's workspace.
    existing = await db.execute(
        select(Workspace).where(
            Workspace.name == ws.name,
            Workspace.environment == next_env,
            Workspace.business_unit_id == ws.business_unit_id,
        )
    )
    target = existing.scalars().first()

    if target is None:
        target = Workspace(
            id=str(uuid.uuid4()),
            # Promoted workspace stays in the same BU and mirrors the source's
            # cloud config. business_unit_id is NOT NULL — omitting it (as this
            # endpoint used to) makes promotion fail at INSERT.
            business_unit_id=ws.business_unit_id,
            name=ws.name,
            aws_account_id=ws.aws_account_id,
            environment=next_env,
            region=ws.region,
            repo_url=ws.repo_url,
            # Track the SAME branch as the source — without this the promoted
            # workspace silently falls back to the model default ("main"), so a
            # promotion from a non-main branch would plan/apply the wrong code.
            repo_ref=ws.repo_ref,
            tf_working_dir=ws.tf_working_dir,
            kind=ws.kind,
            cluster_id=ws.cluster_id,
            azure_subscription_id=ws.azure_subscription_id,
            state_aws_account_id=ws.state_aws_account_id,
        )
        # Intentionally NOT copied from the source (audited):
        #   - state_key: embeds the source `{env}` in its S3 path formula;
        #     copying it would alias the promoted workspace onto the SOURCE
        #     environment's tfstate. Leaving it NULL lets state_path derive the
        #     correct per-env key. DO NOT "fix" this by copying it.
        #   - webhook_enabled: a freshly promoted env should not auto-trigger
        #     on push until an admin opts in.
        #   - drift_status / path_status / path_status_checked_at: no scan/sync
        #     has run against the new env yet → correct to start at defaults.
        db.add(target)
        await db.flush()

    run = Run(
        id=str(uuid.uuid4()),
        workspace_id=target.id,
        triggered_by=current_user.id,
        command="plan",
        status=RunStatus.PENDING,
    )
    db.add(run)
    await db.commit()

    return {
        "promoted_from": current_env,
        "promoted_to": next_env,
        "target_workspace_id": target.id,
        "run_id": run.id,
    }
