"""Users router.

Lists, plus a superadmin-only PATCH endpoint that owns:
  - promoting/demoting `is_superadmin`
  - adding/removing per-BU memberships with role (operator | viewer)

Reads include each user's memberships (BU id + slug + role) so the UI can
render the per-BU role grid on the Users page.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.db import get_db
from app.auth.rbac import Role, require_role
from app.models.business_unit import BusinessUnit, UserBusinessUnit
from app.models.user import User
from app.schemas.user import (
    UserMembership,
    UserPatch,
    UserResponse,
)
from app.services import api_key_service

router = APIRouter(
    prefix="/api/v1/users",
    tags=["users"],
    # User management is identity — interactive-only, even for admin-tier keys
    # (whose owner may be a superadmin, which would otherwise satisfy the
    # superadmin gate on patch_user).
    dependencies=[Depends(api_key_service.forbid_api_keys("manage users"))],
)


async def _hydrate(db: AsyncSession, user: User) -> UserResponse:
    rows = (
        await db.execute(
            select(UserBusinessUnit, BusinessUnit)
            .join(BusinessUnit, BusinessUnit.id == UserBusinessUnit.business_unit_id)
            .where(UserBusinessUnit.user_id == user.id)
            .order_by(BusinessUnit.slug)
        )
    ).all()
    memberships = [
        UserMembership(
            business_unit_id=bu.id,
            business_unit_slug=bu.slug,
            business_unit_name=bu.name,
            role=ubu.role,
        )
        for ubu, bu in rows
    ]
    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        auth_provider=user.auth_provider,
        external_id=user.external_id,
        is_superadmin=bool(user.is_superadmin),
        memberships=memberships,
    )


@router.get("", response_model=list[UserResponse])
async def list_users(
    current_user: User = Depends(require_role(Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    """List all users with their per-BU memberships. Requires admin role."""
    result = await db.execute(select(User).order_by(User.email))
    users = list(result.scalars().all())
    return [await _hydrate(db, u) for u in users]


@router.get("/eligible-reviewers", response_model=list[UserResponse], deprecated=True)
async def list_eligible_reviewers(
    current_user: User = Depends(require_role(Role.operator)),
    db: AsyncSession = Depends(get_db),
):
    """Deprecated stub. 4-eyes was removed; the UI no longer calls this. Kept
    as an empty 200 so any in-flight client cache or external integration that
    still hits the URL gets an empty list instead of a 404."""
    return []


async def _require_superadmin(
    current_user: User = Depends(get_current_user),
) -> User:
    if not bool(getattr(current_user, "is_superadmin", False)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires superadmin",
        )
    return current_user


@router.patch("/{user_id}", response_model=UserResponse)
async def patch_user(
    user_id: str,
    body: UserPatch,
    current_user: User = Depends(_require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Update a user's superadmin flag and/or BU memberships.

    A user can be in multiple BUs with different roles. Per the design,
    superadmins skip the memberships table entirely — memberships still apply
    if you later demote them, so the UI may add them pre-emptively.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if body.is_superadmin is not None:
        # Foot-gun guard: don't let the last superadmin demote themselves and
        # lock the org out of the system.
        if body.is_superadmin is False and current_user.id == user.id:
            others = (
                await db.execute(
                    select(User).where(
                        User.is_superadmin == True,  # noqa: E712
                        User.id != user.id,
                    )
                )
            ).scalars().first()
            if others is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cannot demote the only remaining superadmin",
                )
        user.is_superadmin = body.is_superadmin

    if body.add_memberships:
        for m in body.add_memberships:
            bu = await db.get(BusinessUnit, m.business_unit_id)
            if bu is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Business unit '{m.business_unit_id}' not found",
                )
            existing = (
                await db.execute(
                    select(UserBusinessUnit).where(
                        UserBusinessUnit.user_id == user.id,
                        UserBusinessUnit.business_unit_id == bu.id,
                    )
                )
            ).scalars().first()
            if existing is None:
                db.add(
                    UserBusinessUnit(
                        user_id=user.id,
                        business_unit_id=bu.id,
                        role=m.role,
                    )
                )
            else:
                existing.role = m.role

    if body.remove_memberships:
        for bu_id in body.remove_memberships:
            row = (
                await db.execute(
                    select(UserBusinessUnit).where(
                        UserBusinessUnit.user_id == user.id,
                        UserBusinessUnit.business_unit_id == bu_id,
                    )
                )
            ).scalars().first()
            if row is not None:
                await db.delete(row)

    await db.commit()
    await db.refresh(user)
    return await _hydrate(db, user)
