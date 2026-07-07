"""Global variables — org-wide TF_VAR_* defaults.

Admin write, viewer+ read (metadata only — plaintext for secrets is never
returned after first save; `value` is null and `masked_tail` carries the
last-4 fingerprint instead).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu
from app.auth.rbac import Role, require_role
from app.db import get_db
from app.models.user import User
from app.models.variable import GlobalVariable
from app.schemas.variable import VariableCreate, VariableResponse, VariableUpdate
from app.services import variable_service as varsvc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/variables", tags=["variables"])


def _to_response(row: GlobalVariable) -> VariableResponse:
    """Build the response shape — masks secret values."""
    plain = ""
    try:
        plain = varsvc.decrypt_value(row.value_encrypted)
    except Exception:
        # Decryption failure (typically a rotated key) shouldn't 500 the list
        # endpoint — surface the row with an unreadable marker so an operator
        # can still see and replace it.
        plain = ""
    return VariableResponse(
        id=row.id,
        scope="global",
        key=row.key,
        is_secret=row.is_secret,
        is_hcl=row.is_hcl,
        description=row.description,
        value=None if row.is_secret else plain,
        masked_tail=varsvc.mask_tail(plain) if (row.is_secret and plain) else None,
    )


@router.get("", response_model=list[VariableResponse])
async def list_variables(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    # bu.bu_id is None only for a superadmin viewing "all" → list every BU's.
    rows = await varsvc.list_globals(db, bu.bu_id)
    return [_to_response(r) for r in rows]


@router.post("", response_model=VariableResponse, status_code=status.HTTP_201_CREATED)
async def create_variable(
    body: VariableCreate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    if bu.bu_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set X-Business-Unit header to a specific BU when creating a variable",
        )
    row = await varsvc.create_global(db, body, bu.bu_id)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        # Either the unique (key) constraint tripped or something else. Return
        # 409 for the former, the standard handler covers the rest.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Global variable '{body.key}' already exists",
        )
    await db.refresh(row)
    return _to_response(row)


@router.patch("/{var_id}", response_model=VariableResponse)
async def update_variable(
    var_id: str,
    body: VariableUpdate,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    row = await varsvc.get_global_by_id(db, var_id, business_unit_id=bu.bu_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Global variable not found")
    row = await varsvc.update_global(db, row, body)
    await db.commit()
    await db.refresh(row)
    return _to_response(row)


@router.delete("/{var_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_variable(
    var_id: str,
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    row = await varsvc.get_global_by_id(db, var_id, business_unit_id=bu.bu_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Global variable not found")
    await db.delete(row)
    await db.commit()
