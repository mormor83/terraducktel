"""Admin endpoints for runtime-tunable settings (worker timings, detector
intervals, etc.) — see `app/services/runtime_settings.py` for the catalog."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Role, require_role
from app.db import get_db
from app.models.user import User
from app.services import runtime_settings


router = APIRouter(prefix="/api/v1/runtime-config", tags=["runtime-config"])


class RuntimeSettingValue(BaseModel):
    value: int | float
    default: int | float


class RuntimeSettingUpdate(BaseModel):
    value: int | float = Field(..., description="New value (must be > 0)")


@router.get("", response_model=dict[str, RuntimeSettingValue])
async def list_runtime_settings(
    current_user: User = Depends(require_role(Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    """List every tunable + its default and active value."""
    return await runtime_settings.get_all(db)


@router.put("/{key}", response_model=RuntimeSettingValue)
async def update_runtime_setting(
    key: str,
    body: RuntimeSettingUpdate,
    current_user: User = Depends(require_role(Role.admin)),
    db: AsyncSession = Depends(get_db),
):
    """Persist a runtime setting. Workers pick the new value up within ~60s
    (ConfigService TTL cache + the next poll iteration).

    These are platform-global tunables (worker timings, detector intervals),
    not BU-scoped — so only a superadmin may change them. A per-BU admin
    changing them would silently affect every tenant.
    """
    if not bool(getattr(current_user, "is_superadmin", False)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a superadmin can change platform-global runtime settings",
        )
    try:
        await runtime_settings.set_value(db, key, body.value, updated_by=current_user.id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown runtime setting: {key}")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    new_value = await runtime_settings.get_value(db, key)
    return RuntimeSettingValue(value=new_value, default=runtime_settings.DEFAULTS[key])
