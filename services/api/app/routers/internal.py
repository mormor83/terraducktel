"""Internal endpoints authenticated with a dedicated internal token (not JWT).

These exist so background services like the drift-detector don't need to mint
short-lived JWTs. They authenticate with `TERRADUCKTEL_INTERNAL_TOKEN` —
DELIBERATELY NOT the `TERRADUCKTEL_STATE_TOKEN` that the Terraform HTTP state
backend uses, because that token is also handed to every executor container.
This router hands out plaintext AWS credentials and the platform GitHub
token and can delete any workspace across any Business Unit, so it must stay
unreachable by anything that runs workspace-supplied Terraform/Helm code.
See app/auth/internal_token.py for the full rationale.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from app.auth.internal_token import require_internal_token
from app.db import get_db
from app.models.audit_log import AuditLog
from app.models.drift_report import DriftReport
from app.models.run import Run, RunArtifact
from app.models.run_step import RunStep
from app.models.state_lock import StateLockEntry
from app.models.workspace import Workspace
from app.schemas.drift import DriftReportIn, DriftReportOut
from app.schemas.workspace import WorkspaceResponse

router = APIRouter(
    prefix="/api/v1/internal",
    tags=["internal"],
    dependencies=[Depends(require_internal_token)],
)


class AutoDeleteRequest(BaseModel):
    reason: str


@router.get("/workspaces", response_model=list[WorkspaceResponse])
async def list_workspaces_internal(db: AsyncSession = Depends(get_db)):
    """List all workspaces — auth via X-Terraducktel-Internal-Token header."""
    result = await db.execute(select(Workspace))
    return result.scalars().all()


@router.post("/drift/{workspace_id}/report", response_model=DriftReportOut)
async def submit_drift_report_internal(
    workspace_id: str,
    body: DriftReportIn,
    db: AsyncSession = Depends(get_db),
):
    """Drift detector posts here — auth via X-Terraducktel-Internal-Token header."""
    if body.workspace_id != workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id mismatch")
    ws = await db.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    report = DriftReport(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        has_drift=body.has_drift,
        summary=body.summary,
        plan_output=body.plan_output,
        modified_count=body.modified_count,
        untracked_count=body.untracked_count,
        deleted_count=body.deleted_count,
        mismatch_count=body.mismatch_count,
        resources=[r.model_dump() for r in body.resources],
    )
    db.add(report)
    ws.drift_status = "drifted" if body.has_drift else "clean"
    db.add(ws)
    await db.commit()
    await db.refresh(report)

    # Refresh the cloud-asset inventory from this report. Best-effort and in its
    # own transaction so an inventory hiccup never loses the drift record.
    if body.assets:
        try:
            from app.services.inventory_service import refresh_workspace_assets

            await refresh_workspace_assets(db, ws, body.assets)
            await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()
            import logging
            logging.getLogger(__name__).warning(
                "cloud-asset inventory refresh failed for workspace %s",
                workspace_id, exc_info=True,
            )

    # Slack notification on transition into drifted state. Best-effort —
    # the report has already been committed so a Slack outage cannot lose
    # the drift record.
    if body.has_drift:
        try:
            from app.services.notification_service import send_slack_drift_detected

            await send_slack_drift_detected(
                db,
                workspace_id=workspace_id,
                workspace_name=ws.name,
                summary=body.summary or "",
                environment=ws.environment,
                region=ws.region,
                working_dir=ws.tf_working_dir,
            )
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "Slack drift notification failed for workspace %s",
                workspace_id, exc_info=True,
            )

    return DriftReportOut(
        report_id=report.id,
        workspace_id=report.workspace_id,
        has_drift=report.has_drift,
    )


@router.get("/workspaces/{workspace_id}/aws-credentials")
async def get_workspace_aws_credentials_internal(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Hand decrypted AWS creds for a workspace to internal services.

    Internal-token authenticated. The drift detector needs these to run
    `terraform plan` against the workspace's account AND to enumerate live
    resources (Resource Groups Tagging API) when hunting untracked/ghost infra.
    Mirrors the executor's credential precedence (state-account override first,
    then the resource-owning account) and the `/github-token` plaintext pattern.
    Returns empty strings if the account has no stored credentials so the caller
    can fall back to ambient/instance creds.
    """
    from app.services import aws_account_service as accs

    ws = await db.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    cred_account_id = getattr(ws, "state_aws_account_id", None) or ws.aws_account_id
    access_key = ""
    secret_key = ""
    if cred_account_id:
        creds = await accs.list_account_credentials(
            db, cred_account_id, business_unit_id=ws.business_unit_id
        )
        if creds is not None:
            access_key, secret_key = creds

    return {
        "access_key_id": access_key,
        "secret_access_key": secret_key,
        "account_id": cred_account_id or "",
        "region": ws.region,
    }


@router.get("/github-token")
async def get_github_token_internal(db: AsyncSession = Depends(get_db)):
    """Return the configured GitHub token in plaintext to internal services.

    Internal-token authenticated. The drift / liveness crons run on the same
    private network as the API and need this to call api.github.com on behalf
    of the platform. Falls back to the `GITHUB_TOKEN` env var if no DB row.
    """
    import os as _os

    from app.auth.encryption_key import get_credential_encryption_key
    from app.services.config_service import ConfigService

    env_token = _os.environ.get("GITHUB_TOKEN", "").strip()
    if env_token:
        return {"token": env_token, "source": "env"}
    svc = ConfigService(db, get_credential_encryption_key())
    token = (await svc.get("github.token") or "").strip()
    return {"token": token, "source": "config" if token else "none"}


@router.post("/workspaces/{workspace_id}/auto-delete", status_code=204)
async def auto_delete_workspace_internal(
    workspace_id: str,
    body: AutoDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Liveness cron deletes a workspace when its repo dir disappears upstream.

    Audits as `auto_delete_orphan` so the action is traceable. Mirrors the
    cleanup the user-facing DELETE endpoint does (children first; no FK cascade
    in the schema yet). Idempotent on missing rows.
    """
    from sqlalchemy import delete as sql_delete

    ws = await db.get(Workspace, workspace_id)
    if ws is None:
        return

    audit_details = {
        "reason": body.reason,
        "workspace_name": ws.name,
        "repo_url": ws.repo_url,
        "tf_working_dir": ws.tf_working_dir,
        "repo_ref": ws.repo_ref,
    }

    run_ids = (
        await db.execute(select(Run.id).where(Run.workspace_id == workspace_id))
    ).scalars().all()
    if run_ids:
        await db.execute(sql_delete(RunArtifact).where(RunArtifact.run_id.in_(run_ids)))
        await db.execute(sql_delete(RunStep).where(RunStep.run_id.in_(run_ids)))
        await db.execute(sql_delete(Run).where(Run.workspace_id == workspace_id))
    await db.execute(sql_delete(DriftReport).where(DriftReport.workspace_id == workspace_id))
    await db.execute(sql_delete(StateLockEntry).where(StateLockEntry.workspace_id == workspace_id))

    from app.services.audit_chain import stamp

    _row = AuditLog(
        user_id=None,
        action="auto_delete_orphan",
        resource_type="workspace",
        resource_id=workspace_id,
        workspace_id=workspace_id,
        details=audit_details,
    )
    db.add(_row)
    await stamp(db, _row)
    await db.delete(ws)
    await db.commit()
