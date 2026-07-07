"""Firefly-style cloud asset inventory API (BU-scoped, read-only).

Backed by the `cloud_assets` table the drift-detector refreshes each scan.
- GET /v1/inventory/summary → codification %, per-state counts, filter facets.
- GET /v1/inventory/assets  → filterable, paginated asset list.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu
from app.auth.rbac import Role, require_role
from app.db import get_db
from app.models.cloud_asset import EXCLUDED_STATES, IAC_STATES, MANAGED_STATES, CloudAsset
from app.models.inventory_ignore_rule import IGNORE_MATCH_TYPES, InventoryIgnoreRule
from app.models.user import User
from app.schemas.inventory import (
    AssetListOut,
    AssetOut,
    IgnoreRuleIn,
    IgnoreRuleOut,
    InventoryFacets,
    InventorySummaryOut,
)
from app.services import inventory_service

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])


def _bu_filter(stmt, bu: BUScope):
    if bu.bu_id is not None:
        stmt = stmt.where(CloudAsset.business_unit_id == bu.bu_id)
    return stmt


def _scope_filters(stmt, *, provider=None, region=None, account_id=None, search=None):
    """Apply the cross-cutting scope filters (everything except iac_status).

    Shared by the summary KPIs and the asset list so the cards rescope to the
    same provider/region/account/search the table is filtered by. `iac_status`
    is intentionally NOT here — the cards *are* the status breakdown, so they
    always show every status within the current scope.
    """
    if provider:
        stmt = stmt.where(CloudAsset.provider == provider)
    if region:
        stmt = stmt.where(CloudAsset.region == region)
    if account_id:
        stmt = stmt.where(CloudAsset.account_id == account_id)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(CloudAsset.asset_id.ilike(like) | CloudAsset.address.ilike(like))
    return stmt


@router.get("/summary", response_model=InventorySummaryOut)
async def inventory_summary(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
    provider: str | None = Query(default=None),
    region: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
):
    """Headline KPIs: codification %, counts per IaC state, and filter facets.

    The counts/codification reflect the active provider/region/account/search
    scope; the facets stay BU-global so the filter dropdowns never empty out.
    """
    scope = dict(provider=provider, region=region, account_id=account_id, search=search)
    rows = (
        await db.execute(
            _scope_filters(
                _bu_filter(
                    select(CloudAsset.iac_status, func.count()).group_by(CloudAsset.iac_status),
                    bu,
                ),
                **scope,
            )
        )
    ).all()
    counts = {state: 0 for state in IAC_STATES}
    for state, n in rows:
        counts[state] = counts.get(state, 0) + n

    total = sum(counts.values())
    tracked = sum(counts[s] for s in MANAGED_STATES)
    # Codification = share of discovered assets that IaC knows about. Unmanaged
    # and undetermined are outside IaC; ignored + service_managed are excluded
    # from the base entirely (neither tracked nor counted against).
    base = total - sum(counts[s] for s in EXCLUDED_STATES)
    codification_pct = round(100 * tracked / base) if base else 0

    async def _distinct(col):
        vals = (
            await db.execute(_bu_filter(select(col).where(col != "").distinct(), bu))
        ).scalars().all()
        return sorted(v for v in vals if v)

    facets = InventoryFacets(
        providers=await _distinct(CloudAsset.provider),
        regions=await _distinct(CloudAsset.region),
        accounts=await _distinct(CloudAsset.account_id),
        asset_types=await _distinct(CloudAsset.asset_type),
    )

    return InventorySummaryOut(
        total=total,
        codification_pct=codification_pct,
        counts=counts,
        facets=facets,
    )


@router.get("/assets", response_model=AssetListOut)
async def list_assets(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
    iac_status: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    region: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    asset_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=200, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """Filterable asset list. All filters are exact except `search` (substring)."""
    stmt = _scope_filters(
        _bu_filter(select(CloudAsset), bu),
        provider=provider, region=region, account_id=account_id, search=search,
    )
    if iac_status:
        stmt = stmt.where(CloudAsset.iac_status == iac_status)
    if asset_type:
        stmt = stmt.where(CloudAsset.asset_type == asset_type)

    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()

    rows = (
        await db.execute(
            stmt.order_by(CloudAsset.iac_status, CloudAsset.asset_id).limit(limit).offset(offset)
        )
    ).scalars().all()

    items = [
        AssetOut(
            asset_id=a.asset_id,
            address=a.address or "",
            asset_type=a.asset_type,
            provider=a.provider,
            region=a.region,
            account_id=a.account_id,
            iac_status=a.iac_status,
            drift_summary=a.drift_summary or "",
            workspace_id=a.workspace_id,
            last_seen=a.last_seen.isoformat() if a.last_seen else None,
        )
        for a in rows
    ]
    return AssetListOut(total=total, items=items)


# ─── ignore rules ─────────────────────────────────────────────────────────────


def _rule_out(r: InventoryIgnoreRule) -> IgnoreRuleOut:
    return IgnoreRuleOut(
        id=r.id,
        match_type=r.match_type,
        pattern=r.pattern,
        note=r.note or "",
        created_at=r.created_at.isoformat() if r.created_at else None,
    )


@router.get("/ignore-rules", response_model=list[IgnoreRuleOut])
async def list_ignore_rules(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """List the current BU's inventory ignore rules."""
    stmt = select(InventoryIgnoreRule).order_by(InventoryIgnoreRule.created_at.desc())
    if bu.bu_id is not None:
        stmt = stmt.where(InventoryIgnoreRule.business_unit_id == bu.bu_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [_rule_out(r) for r in rows]


@router.post("/ignore-rules", response_model=IgnoreRuleOut, status_code=status.HTTP_201_CREATED)
async def create_ignore_rule(
    body: IgnoreRuleIn,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Add an ignore rule (admin) and immediately reclassify matching assets.

    Matching non-managed assets in this BU flip to `ignored` now, so the effect
    is visible without waiting for the next collector scan.
    """
    if bu.bu_id is None:
        raise HTTPException(status_code=400, detail="Set X-Business-Unit to a specific BU")
    if body.match_type not in IGNORE_MATCH_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"match_type must be one of {IGNORE_MATCH_TYPES}",
        )
    if not body.pattern.strip():
        raise HTTPException(status_code=422, detail="pattern is required")
    rule = InventoryIgnoreRule(
        id=str(uuid.uuid4()),
        business_unit_id=bu.bu_id,
        match_type=body.match_type,
        pattern=body.pattern.strip(),
        note=body.note or None,
        created_by=current_user.id,
    )
    db.add(rule)
    await db.flush()
    await inventory_service.reapply_ignore_rules(db, bu.bu_id)
    await db.commit()
    await db.refresh(rule)
    return _rule_out(rule)


@router.delete("/ignore-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ignore_rule(
    rule_id: str,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Delete an ignore rule. Suppressed assets revert on the next collector scan."""
    rule = await db.get(InventoryIgnoreRule, rule_id)
    if rule is None or (bu.bu_id is not None and rule.business_unit_id != bu.bu_id):
        raise HTTPException(status_code=404, detail="Ignore rule not found")
    await db.delete(rule)
    await db.commit()
