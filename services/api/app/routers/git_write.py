"""Settings → GitHub → Git write access (per BU) for environment promotions.

  GET    /api/v1/integrations/git-write        state (never the token itself)
  PUT    /api/v1/integrations/git-write        enable/disable, dedicated token, identity
  POST   /api/v1/integrations/git-write/test   can the credential push to each repo?

Always a specific BU (400 on the cross-BU view). Writes are BU-admin, like
starting a promotion. The token never leaves the API: GET returns
`token_configured` + a masked tail.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu
from app.auth.rbac import Role, require_bu_admin, require_role
from app.db import get_db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.workspace import Workspace
from app.services import git_write_config as gw
from app.services.audit_chain import stamp

router = APIRouter(prefix="/api/v1/integrations/git-write", tags=["integrations"])


class GitWriteState(BaseModel):
    enabled: bool
    token_configured: bool
    token_source: Optional[str] = None  # dedicated | github
    token_tail: Optional[str] = None
    username: Optional[str] = None
    bot_name: str
    bot_email: str


class GitWriteUpdate(BaseModel):
    enabled: Optional[bool] = None
    token: Optional[str] = Field(default=None, max_length=500)
    clear_token: bool = False
    username: Optional[str] = Field(default=None, max_length=100)
    bot_name: Optional[str] = Field(default=None, max_length=100)
    bot_email: Optional[str] = Field(default=None, max_length=200)


def _slug(bu: BUScope) -> str:
    if bu.slug is None:
        raise HTTPException(status_code=400, detail="Set X-Business-Unit header to a specific BU")
    return bu.slug


def _state(cfg: gw.WriteConfig) -> GitWriteState:
    return GitWriteState(
        enabled=cfg.enabled, token_configured=bool(cfg.token), token_source=cfg.token_source,
        token_tail=gw.mask_tail(cfg.token), username=cfg.username,
        bot_name=cfg.bot_name, bot_email=cfg.bot_email,
    )


@router.get("", response_model=GitWriteState)
async def get_git_write(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    return _state(await gw.load(db, _slug(bu)))


@router.put("", response_model=GitWriteState)
async def put_git_write(
    body: GitWriteUpdate,
    current_user: User = Depends(require_bu_admin),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    slug = _slug(bu)
    await gw.save(db, slug, current_user.id, enabled=body.enabled, token=body.token,
                  clear_token=body.clear_token, username=body.username,
                  bot_name=body.bot_name, bot_email=body.bot_email)
    audit = AuditLog(
        user_id=current_user.id, action="integration.git_write.update", resource_type="env_link",
        resource_id=f"git-write:{bu.bu_id}",
        details={"business_unit_id": bu.bu_id, "enabled": body.enabled,
                 "token_changed": bool(body.token) or body.clear_token},
    )
    db.add(audit)
    await stamp(db, audit)
    await db.commit()
    return _state(await gw.load(db, slug))


@router.post("/test")
async def test_git_write(
    current_user: User = Depends(require_role(Role.admin)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Check push access for every repo this BU's stacks are synced from."""
    slug = _slug(bu)
    cfg = await gw.load(db, slug)
    repos = sorted({
        r for (r,) in (await db.execute(
            select(Workspace.repo_url).where(Workspace.business_unit_id == bu.bu_id)
        )).all()
        if r and not r.startswith("local://")
    })
    results = [await gw.check_push(r, cfg) for r in repos]
    return {
        "enabled": cfg.enabled,
        "token_configured": bool(cfg.token),
        "repos": results,
        "ok": bool(results) and all(x["can_push"] for x in results),
    }
