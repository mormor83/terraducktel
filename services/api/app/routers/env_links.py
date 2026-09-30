"""Environment links router (Governance › Environments).

A link pairs the stacks under two repo-tree nodes so their differences can be
compared and promoted. Reading is open to anyone who can see the BU's stacks
(viewer+); creating, editing and deleting a link is a BU-admin action
(`require_bu_admin`). Every write lands in the audit log (in the service).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu
from app.auth.rbac import Role, require_bu_admin, require_role
from app.db import get_db
from app.models.user import User
from app.schemas.env_link import (
    EnvLinkCreate,
    EnvLinkResponse,
    EnvLinkRules,
    EnvLinkUpdate,
    PairingPreview,
    PairListResponse,
)
from app.services import env_link_service as svc
from app.services.env_pairing import PAIR_STATUSES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/env-links", tags=["environments"])


def _require_bu(bu: BUScope) -> str:
    """Links are bound to one concrete BU — creating/previewing needs one."""
    if bu.bu_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select a specific Business Unit (X-Business-Unit) to manage environment links",
        )
    return bu.bu_id


@router.get("", response_model=list[EnvLinkResponse])
async def list_links(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    return await svc.list_links(db, bu.bu_id)


@router.post("", response_model=EnvLinkResponse, status_code=status.HTTP_201_CREATED)
async def create_link(
    body: EnvLinkCreate,
    current_user: User = Depends(require_bu_admin),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    return await svc.create_link(db, _require_bu(bu), body, current_user.id)


# Declared before `/{link_id}` so the static path isn't shadowed.
@router.post("/preview-pairs", response_model=PairingPreview)
async def preview_pairs(
    body: EnvLinkRules,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Dry-run pairing with candidate nodes/rules (the link builder's live preview)."""
    return await svc.preview_pairs(db, _require_bu(bu), body)


@router.get("/{link_id}", response_model=EnvLinkResponse)
async def get_link(
    link_id: str,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    return await svc.read_link(db, bu.bu_id, link_id)


@router.put("/{link_id}", response_model=EnvLinkResponse)
async def update_link(
    link_id: str,
    body: EnvLinkUpdate,
    current_user: User = Depends(require_bu_admin),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    return await svc.update_link(db, bu.bu_id, link_id, body, current_user.id)


@router.delete("/{link_id}", status_code=status.HTTP_200_OK)
async def delete_link(
    link_id: str,
    current_user: User = Depends(require_bu_admin),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    await svc.delete_link(db, bu.bu_id, link_id, current_user.id)
    return {"deleted": link_id}


@router.get("/{link_id}/pairs", response_model=PairListResponse)
async def list_pairs(
    link_id: str,
    status_filter: Optional[str] = Query(
        None, alias="status", description="Comma-separated pair statuses"
    ),
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    wanted: Optional[set[str]] = None
    if status_filter:
        wanted = {s.strip() for s in status_filter.split(",") if s.strip()}
        unknown = wanted - set(PAIR_STATUSES)
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown pair status: {', '.join(sorted(unknown))}",
            )
    return await svc.list_pairs(db, bu.bu_id, link_id, wanted)
