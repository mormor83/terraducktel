"""Business Unit CRUD.

Visibility rules:
  - superadmin sees all BUs
  - everyone else sees only BUs they are a member of (via user_business_units)

Creating / deleting a BU is superadmin-only.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.db import get_db
from app.models.business_unit import BusinessUnit, UserBusinessUnit
from app.models.user import User
from app.schemas.business_unit import (
    BusinessUnitCreate,
    BusinessUnitResponse,
    BusinessUnitUpdate,
)
from app.services import api_key_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/business-units", tags=["business-units"])


async def _require_superadmin(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> User:
    # BU CRUD is identity/tenancy — never reachable by automation, even an
    # admin-tier key owned by a superadmin (which would pass the flag check).
    api_key_service.block_api_keys(request, action="manage Business Units")
    if not bool(getattr(current_user, "is_superadmin", False)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires superadmin",
        )
    return current_user


@router.get("", response_model=list[BusinessUnitResponse])
async def list_business_units(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List BUs visible to the caller.

    Superadmin: all BUs. Anyone else: only their memberships.
    API keys are bound to exactly one BU and only ever see that BU — never the
    owner's full membership set, even if the owner is a superadmin.
    """
    key = api_key_service.get_request_key(request)
    if key is not None:
        bu = await db.get(BusinessUnit, key.business_unit_id)
        return [bu] if bu is not None else []
    if bool(getattr(current_user, "is_superadmin", False)):
        rows = (await db.execute(select(BusinessUnit).order_by(BusinessUnit.slug))).scalars().all()
        return list(rows)
    rows = (
        await db.execute(
            select(BusinessUnit)
            .join(
                UserBusinessUnit,
                UserBusinessUnit.business_unit_id == BusinessUnit.id,
            )
            .where(UserBusinessUnit.user_id == current_user.id)
            .order_by(BusinessUnit.slug)
        )
    ).scalars().all()
    return list(rows)


@router.post(
    "",
    response_model=BusinessUnitResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_business_unit(
    body: BusinessUnitCreate,
    current_user: User = Depends(_require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(select(BusinessUnit).where(BusinessUnit.slug == body.slug))
    ).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Business unit '{body.slug}' already exists",
        )
    bu = BusinessUnit(id=str(uuid.uuid4()), slug=body.slug, name=body.name)
    db.add(bu)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Business unit '{body.slug}' already exists",
        )
    await db.refresh(bu)
    return bu


@router.put("/{bu_id}", response_model=BusinessUnitResponse)
async def update_business_unit(
    bu_id: str,
    body: BusinessUnitUpdate,
    current_user: User = Depends(_require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    bu = await db.get(BusinessUnit, bu_id)
    if bu is None:
        raise HTTPException(status_code=404, detail="Business unit not found")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(bu, k, v)
    await db.commit()
    await db.refresh(bu)
    return bu
