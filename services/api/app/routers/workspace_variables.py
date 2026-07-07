"""Per-workspace variables — operator+ write, viewer+ read.

Shape mirrors the global variables router; merge semantics
(`global ← workspace ← run`) live in `variable_service.get_merged_for_run`,
not here. Endpoints are nested under the workspace to keep the URL hierarchy
honest and let RBAC scope check the workspace's existence first.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu, scoped_workspace
from app.auth.rbac import Role, require_role
from app.db import get_db
from app.models.user import User
from app.models.variable import WorkspaceVariable
from app.schemas.variable import VariableCreate, VariableResponse, VariableUpdate
from app.services import variable_service as varsvc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspace-variables"])


def _to_response(row: WorkspaceVariable) -> VariableResponse:
    plain = ""
    try:
        plain = varsvc.decrypt_value(row.value_encrypted)
    except Exception:
        plain = ""
    return VariableResponse(
        id=row.id,
        scope="workspace",
        workspace_id=row.workspace_id,
        key=row.key,
        is_secret=row.is_secret,
        is_hcl=row.is_hcl,
        description=row.description,
        value=None if row.is_secret else plain,
        masked_tail=varsvc.mask_tail(plain) if (row.is_secret and plain) else None,
    )


@router.get("/{workspace_id}/variables", response_model=list[VariableResponse])
async def list_workspace_variables(
    workspace_id: str,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    await scoped_workspace(workspace_id, bu, db)
    rows = await varsvc.list_for_workspace(db, workspace_id)
    return [_to_response(r) for r in rows]


@router.post(
    "/{workspace_id}/variables",
    response_model=VariableResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_variable(
    workspace_id: str,
    body: VariableCreate,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    await scoped_workspace(workspace_id, bu, db)
    row = await varsvc.create_workspace_var(db, workspace_id, body)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Variable '{body.key}' already exists on this workspace",
        )
    await db.refresh(row)
    return _to_response(row)


@router.patch(
    "/{workspace_id}/variables/{var_id}",
    response_model=VariableResponse,
)
async def update_workspace_variable(
    workspace_id: str,
    var_id: str,
    body: VariableUpdate,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    await scoped_workspace(workspace_id, bu, db)
    row = await varsvc.get_workspace_var_by_id(db, var_id)
    if row is None or row.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Variable not found on this workspace")
    row = await varsvc.update_workspace_var(db, row, body)
    await db.commit()
    await db.refresh(row)
    return _to_response(row)


@router.delete(
    "/{workspace_id}/variables/{var_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace_variable(
    workspace_id: str,
    var_id: str,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    await scoped_workspace(workspace_id, bu, db)
    row = await varsvc.get_workspace_var_by_id(db, var_id)
    if row is None or row.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Variable not found on this workspace")
    await db.delete(row)
    await db.commit()
