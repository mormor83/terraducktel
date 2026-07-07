"""Business Unit scoping dependency.

Resolves the current BU for a request, honoring the `X-Business-Unit` header
(slug) and the caller's membership/superadmin status.

Returns a `BUScope` dataclass:
  - bu_id: str | None   — None means "all BUs" (only possible for superadmin)
  - slug:  str | None
  - is_superadmin: bool

Rules:
  superadmin, no header             → all BUs (no filter)
  superadmin, header='all'          → all BUs
  superadmin, header=<slug>         → that BU (must exist)
  member, no header                 → caller's first BU by slug
  member, header=<slug>             → that BU (must be a member)
  member, header='all'              → 403
  no membership and not superadmin  → 403
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.db import get_db
from app.models.business_unit import BusinessUnit, UserBusinessUnit
from app.models.user import User
from app.services import api_key_service


@dataclass
class BUScope:
    bu_id: Optional[str]
    slug: Optional[str]
    is_superadmin: bool
    # The caller's per-BU role (user_business_units.role: operator|viewer) for
    # the resolved BU, or None for superadmin / API-key / run-token scopes.
    # Used by require_role to floor the effective role.
    membership_role: Optional[str] = None

    @property
    def all_bus(self) -> bool:
        return self.bu_id is None


async def scoped_workspace(workspace_id: str, bu: "BUScope", db: AsyncSession):
    """Fetch a workspace by id, enforcing the caller's BU scope.

    Returns the workspace, or raises 404 if it doesn't exist OR belongs to a
    different BU. We deliberately return 404 (not 403) on a cross-BU access so
    we don't leak the existence of another tenant's resources.

    `bu.bu_id is None` means "all BUs" (superadmin / API-key paths already
    resolved by `current_bu`) and bypasses the scope check — identical to the
    guard the list endpoints use.
    """
    from app.models.workspace import Workspace

    ws = await db.get(Workspace, workspace_id)
    if ws is None or (bu.bu_id is not None and ws.business_unit_id != bu.bu_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws


async def scoped_run(run_id: str, bu: "BUScope", db: AsyncSession):
    """Fetch a run by id, enforcing the caller's BU scope via its workspace.

    Runs have no `business_unit_id` of their own — tenancy is derived from the
    owning workspace. Raises 404 if the run is missing or its workspace is in a
    different BU (existence not leaked across tenants).
    """
    from app.models.run import Run
    from app.models.workspace import Workspace

    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if bu.bu_id is not None:
        ws = await db.get(Workspace, run.workspace_id)
        if ws is None or ws.business_unit_id != bu.bu_id:
            raise HTTPException(status_code=404, detail="Run not found")
    return run


async def current_bu(
    request: Request,
    x_business_unit: Optional[str] = Header(default=None, alias="X-Business-Unit"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BUScope:
    """Resolve + memoize the request's BU scope. Memoization lets `bu_role_cap`
    reuse the same resolution without re-running the queries."""
    cached = getattr(request.state, "_bu_scope", None)
    if cached is not None:
        return cached
    scope = await _resolve_bu(request, x_business_unit, current_user, db)
    request.state._bu_scope = scope
    return scope


async def bu_role_cap(
    request: Request,
    x_business_unit: Optional[str] = Header(default=None, alias="X-Business-Unit"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Optional[str]:
    """The caller's per-BU membership role for the request's BU scope, or None
    (= no cap). NEVER raises: a scope-resolution error ('no membership', 'all',
    unknown BU) → None, so `require_role` doesn't introduce new 403s on the
    endpoints that use it without `current_bu` (users, runtime_config). The
    endpoint's own `current_bu` dependency still enforces hard scoping.
    """
    try:
        cached = getattr(request.state, "_bu_scope", None)
        scope = cached or await _resolve_bu(request, x_business_unit, current_user, db)
        request.state._bu_scope = scope
    except HTTPException:
        return None
    return None if scope.is_superadmin else scope.membership_role


async def _resolve_bu(
    request: Request,
    x_business_unit: Optional[str],
    current_user: User,
    db: AsyncSession,
) -> BUScope:
    # API-key callers are pinned to the key's BU regardless of any header they
    # send. Tenancy is decided by the credential, not the request.
    api_key = api_key_service.get_request_key(request)
    if api_key is not None:
        bu = await db.get(BusinessUnit, api_key.business_unit_id)
        if bu is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API key business unit no longer exists",
            )
        return BUScope(bu_id=bu.id, slug=bu.slug, is_superadmin=False)

    # Run-scoped executor token: pinned to the run's BU, never all-BU
    # and never superadmin — even if the triggering user is a superadmin.
    run_tok = getattr(request.state, "run_token", None)
    if run_tok is not None:
        bu = await db.get(BusinessUnit, run_tok.get("business_unit_id") or "")
        if bu is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Run token business unit no longer exists",
            )
        return BUScope(bu_id=bu.id, slug=bu.slug, is_superadmin=False)

    is_super = bool(getattr(current_user, "is_superadmin", False))
    raw = (x_business_unit or "").strip()

    if is_super:
        if not raw or raw.lower() == "all":
            return BUScope(bu_id=None, slug=None, is_superadmin=True)
        bu = (
            await db.execute(select(BusinessUnit).where(BusinessUnit.slug == raw))
        ).scalars().first()
        if bu is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Business unit '{raw}' not found",
            )
        return BUScope(bu_id=bu.id, slug=bu.slug, is_superadmin=True)

    # Non-superadmin: must be a member of the chosen BU (or pick a default).
    if raw.lower() == "all":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only superadmin can view all business units",
        )

    if raw:
        row = (
            await db.execute(
                select(BusinessUnit, UserBusinessUnit.role)
                .join(
                    UserBusinessUnit,
                    UserBusinessUnit.business_unit_id == BusinessUnit.id,
                )
                .where(
                    BusinessUnit.slug == raw,
                    UserBusinessUnit.user_id == current_user.id,
                )
            )
        ).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Not a member of business unit '{raw}'",
            )
        bu_row, member_role = row
        return BUScope(bu_id=bu_row.id, slug=bu_row.slug, is_superadmin=False,
                       membership_role=member_role)

    # No header → pick caller's first membership (by slug, deterministic).
    row = (
        await db.execute(
            select(BusinessUnit, UserBusinessUnit.role)
            .join(
                UserBusinessUnit,
                UserBusinessUnit.business_unit_id == BusinessUnit.id,
            )
            .where(UserBusinessUnit.user_id == current_user.id)
            .order_by(BusinessUnit.slug)
            .limit(1)
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No business unit memberships",
        )
    bu_row, member_role = row
    return BUScope(bu_id=bu_row.id, slug=bu_row.slug, is_superadmin=False,
                   membership_role=member_role)
