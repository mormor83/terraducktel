"""Drift detection API: ingest reports from detector, trigger scans, read summary."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu
from app.db import get_db
from app.auth.rbac import Role, require_role
from app.models.workspace import Workspace
from app.models.drift_report import DriftReport
from app.models.user import User
from app.schemas.drift import (
    DriftReportDetailOut,
    DriftReportIn,
    DriftReportOut,
    DriftResource,
    DriftSummaryOut,
    DriftWorkspaceSummary,
)
from app.services.notification_service import send_drift_alert

router = APIRouter(prefix="/api/v1/drift", tags=["drift"])


async def _latest_reports_by_workspace(
    db: AsyncSession, workspace_ids: list[str]
) -> dict[str, DriftReport]:
    """Return the most-recent drift report for each of the given workspaces.

    One query, ordered newest-first; we keep the first row seen per workspace.
    Workspaces with no reports are simply absent from the dict.
    """
    if not workspace_ids:
        return {}
    rows = (
        await db.execute(
            select(DriftReport)
            .where(DriftReport.workspace_id.in_(workspace_ids))
            .order_by(DriftReport.detected_at.desc())
        )
    ).scalars().all()
    latest: dict[str, DriftReport] = {}
    for r in rows:
        latest.setdefault(r.workspace_id, r)
    return latest


@router.get("/summary", response_model=DriftSummaryOut)
async def drift_summary(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Per-BU drift breakdown: aggregate the latest report for each workspace.

    Scoped to the caller's selected Business Unit; superadmin with `all` (or no
    header) sees every workspace across BUs.
    """
    stmt = select(Workspace)
    if bu.bu_id is not None:
        stmt = stmt.where(Workspace.business_unit_id == bu.bu_id)
    workspaces = (await db.execute(stmt)).scalars().all()

    latest = await _latest_reports_by_workspace(db, [w.id for w in workspaces])

    out = DriftSummaryOut(workspaces_total=len(workspaces))
    for ws in workspaces:
        rep = latest.get(ws.id)
        row = DriftWorkspaceSummary(
            workspace_id=ws.id,
            name=ws.name,
            environment=ws.environment,
            region=ws.region,
            drift_status=ws.drift_status,
            modified_count=rep.modified_count if rep else 0,
            untracked_count=rep.untracked_count if rep else 0,
            deleted_count=rep.deleted_count if rep else 0,
            mismatch_count=rep.mismatch_count if rep else 0,
        )
        out.by_workspace.append(row)
        out.modified_count += row.modified_count
        out.untracked_count += row.untracked_count
        out.deleted_count += row.deleted_count
        out.mismatch_count += row.mismatch_count
        if ws.drift_status == "drifted":
            out.workspaces_drifted += 1
    return out


@router.get("/{workspace_id}", response_model=DriftReportDetailOut)
async def drift_detail(
    workspace_id: str,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Latest drift report for one workspace, with per-resource detail."""
    ws = await db.get(Workspace, workspace_id)
    if ws is None or (bu.bu_id is not None and ws.business_unit_id != bu.bu_id):
        raise HTTPException(status_code=404, detail="Workspace not found")

    latest = await _latest_reports_by_workspace(db, [workspace_id])
    rep = latest.get(workspace_id)
    if rep is None:
        return DriftReportDetailOut(workspace_id=workspace_id, has_drift=False)

    return DriftReportDetailOut(
        workspace_id=workspace_id,
        has_drift=rep.has_drift,
        summary=rep.summary or "",
        detected_at=rep.detected_at.isoformat() if rep.detected_at else None,
        modified_count=rep.modified_count,
        untracked_count=rep.untracked_count,
        deleted_count=rep.deleted_count,
        mismatch_count=rep.mismatch_count,
        resources=[DriftResource(**r) for r in (rep.resources or [])],
    )


@router.post("/{workspace_id}/scan", status_code=status.HTTP_202_ACCEPTED)
async def trigger_drift_scan(
    workspace_id: str,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Accept drift scan request; real detector runs async. Returns a report id."""
    ws = await db.get(Workspace, workspace_id)
    if ws is None or (bu.bu_id is not None and ws.business_unit_id != bu.bu_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    report = DriftReport(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        has_drift=False,
        summary="Scan queued",
        plan_output="",
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return {"report_id": report.id, "status": "accepted"}


@router.post("/{workspace_id}/report", response_model=DriftReportOut)
async def submit_drift_report(
    workspace_id: str,
    body: DriftReportIn,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Record drift scan results (from detector or tests)."""
    if body.workspace_id != workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id mismatch")
    ws = await db.get(Workspace, workspace_id)
    if ws is None or (bu.bu_id is not None and ws.business_unit_id != bu.bu_id):
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

    if body.assets:
        from app.services.inventory_service import refresh_workspace_assets

        await refresh_workspace_assets(db, ws, body.assets)
        await db.commit()

    if body.has_drift:
        await send_drift_alert(db, ws.name, body.summary)

    return DriftReportOut(
        report_id=report.id,
        workspace_id=workspace_id,
        has_drift=body.has_drift,
    )
