"""Environment compare + promotion endpoints (Governance › Environments).

  /api/v1/env-links/{id}/compare              enqueue (re)compare
  /api/v1/env-links/{id}/promotions/preview   exact changes, blockers, warnings
  /api/v1/env-links/{id}/promotions           commit + push + ordinary runs
  /api/v1/env-links/stack-index               stack id → links (Dashboard badge)
  /api/v1/env-pairs/{id}/compare              result; 202 while computing
  /api/v1/env-pairs/{id}/refresh-state        reload, or a gated refresh-only run
  /api/v1/env-pairs/{id}/history              promotions/runs that touched it
  /api/v1/promotions[?link_id=]               list
  /api/v1/promotions/{id}                     detail incl. per-stack stage tracker
  /api/v1/promotions/{id}/revert              revert commit through the pipeline

Viewing needs viewer + BU scope. Starting a promotion, toggling protected
hunks, creating in target and reverting are BU-admin (`require_bu_admin`) —
enforced here, not just hidden in the UI. Approval of the resulting runs stays
with the normal approvers.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.bu_context import BUScope, current_bu
from app.auth.rbac import Role, require_bu_admin, require_role
from app.db import get_db
from app.models.env_link import EnvLink, EnvPair
from app.models.promotion import Promotion, PromotionRun
from app.models.run import Run
from app.models.user import User
from app.models.workspace import Workspace
from app.services import bg_worker
from app.services import env_compare_service as cmp
from app.services import env_link_service, promotion_service, run_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["environments"])

Direction = Literal["forward", "reverse"]


class CompareRequest(BaseModel):
    pair_ids: Optional[list[str]] = None
    direction: Direction = "forward"
    force: bool = False


class RefreshStateRequest(BaseModel):
    side: Literal["source", "target"]


async def _link(db: AsyncSession, bu: BUScope, link_id: str) -> EnvLink:
    return await env_link_service.get_link(db, bu.bu_id, link_id)


async def _pair(db: AsyncSession, bu: BUScope, pair_id: str) -> tuple[EnvPair, EnvLink]:
    pair = await db.get(EnvPair, pair_id)
    if pair is None:
        raise HTTPException(status_code=404, detail="Pair not found")
    link = await env_link_service.get_link(db, bu.bu_id, pair.link_id)
    return pair, link


def _compare_key(pair_id: str, direction: str) -> str:
    return f"compare:{pair_id}:{direction}"


# ─── compare ─────────────────────────────────────────────────────────────────


@router.post("/api/v1/env-links/{link_id}/compare", status_code=status.HTTP_202_ACCEPTED)
async def enqueue_compare(
    link_id: str,
    body: CompareRequest = Body(default_factory=CompareRequest),
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    link = await _link(db, bu, link_id)
    stacks, _ = await env_link_service._bu_stacks(db, link.business_unit_id)
    await env_link_service.reconcile_pairs(db, link, stacks)
    q = select(EnvPair).where(EnvPair.link_id == link.id, EnvPair.status != "excluded")
    if body.pair_ids:
        q = q.where(EnvPair.id.in_(body.pair_ids))
    queued = 0
    for pair in (await db.execute(q)).scalars():
        if pair.status == "missing_in_source" and body.direction == "forward":
            continue
        if body.force:
            pair.cache_key = None
        job = await bg_worker.enqueue(
            db, "env_compare", {"pair_id": pair.id, "direction": body.direction, "force": body.force},
            dedupe_key=_compare_key(pair.id, body.direction),
        )
        queued += job is not None
    await db.commit()
    return {"queued": queued}


@router.get("/api/v1/env-pairs/{pair_id}/compare")
async def get_compare(
    pair_id: str,
    direction: Direction = "forward",
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    pair, link = await _pair(db, bu, pair_id)
    result = await cmp.get_result(db, pair.id, direction)
    fresh = cmp.is_fresh(result)
    computing = await bg_worker.is_pending(db, _compare_key(pair.id, direction))
    if not fresh and not computing:
        await bg_worker.enqueue(db, "env_compare", {"pair_id": pair.id, "direction": direction},
                                dedupe_key=_compare_key(pair.id, direction))
        await db.commit()
        computing = True
    payload = {
        "pair_id": pair.id,
        "direction": direction,
        "status": "ready" if fresh and not computing else "computing",
        "stale": not fresh,
        "computed_at": result.computed_at if result else None,
        "refs": result.refs if result else None,
        "config_diff": result.config_diff if result else None,
        "state_diff": result.state_diff if result else None,
        "error": result.error if result else None,
        "pair": {"status": pair.status, "summary": pair.summary, "relative_path": pair.relative_path},
    }
    if payload["status"] == "computing":
        return JSONResponse(status_code=202, content=_jsonable(payload))
    return payload


def _jsonable(v):
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(v)


@router.post("/api/v1/env-pairs/{pair_id}/refresh-state")
async def refresh_state(
    pair_id: str,
    body: RefreshStateRequest,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Re-read the state TDT holds for one side and recompare (no AWS scan)."""
    pair, link = await _pair(db, bu, pair_id)
    if (pair.source_stack_id if body.side == "source" else pair.target_stack_id) is None:
        raise HTTPException(status_code=400, detail=f"This pair has no {body.side} stack")
    await cmp._invalidate_pairs(db, select(EnvPair.id).where(EnvPair.id == pair.id))
    await bg_worker.enqueue(db, "env_compare", {"pair_id": pair.id, "direction": "forward"},
                            dedupe_key=_compare_key(pair.id, "forward"))
    await db.commit()
    return {"queued": True}


@router.post("/api/v1/env-pairs/{pair_id}/refresh-state/run", status_code=status.HTTP_201_CREATED)
async def refresh_state_run(
    pair_id: str,
    body: RefreshStateRequest,
    current_user: User = Depends(require_role(Role.operator)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """Queue a `refresh` run (plan -refresh-only → approval → apply) on one side.

    It writes state, so it is an ordinary gated run: operator+, and it waits
    for approval like any other.
    """
    pair, link = await _pair(db, bu, pair_id)
    stack_id = pair.source_stack_id if body.side == "source" else pair.target_stack_id
    if stack_id is None:
        raise HTTPException(status_code=400, detail=f"This pair has no {body.side} stack")
    ws = await db.get(Workspace, stack_id)
    try:
        run = await run_service.create_run(db, ws, command="refresh", triggered_by=current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    await db.commit()
    return {"run_id": run.id}


@router.get("/api/v1/env-pairs/{pair_id}/history")
async def pair_history(
    pair_id: str,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    pair, link = await _pair(db, bu, pair_id)
    stack_ids = [s for s in (pair.source_stack_id, pair.target_stack_id) if s]
    promos = []
    if stack_ids or pair.id:
        q = (
            select(Promotion)
            .join(PromotionRun, PromotionRun.promotion_id == Promotion.id)
            .where(Promotion.link_id == link.id,
                   or_(PromotionRun.pair_id == pair.id, PromotionRun.target_stack_id.in_(stack_ids or [""])))
            .order_by(Promotion.created_at.desc())
        )
        seen = set()
        for p in (await db.execute(q)).scalars():
            if p.id in seen:
                continue
            seen.add(p.id)
            promos.append(await promotion_service.to_dict(db, p))
    runs = []
    if stack_ids:
        for r in (await db.execute(
            select(Run).where(Run.workspace_id.in_(stack_ids)).order_by(Run.created_at.desc()).limit(20)
        )).scalars():
            runs.append({
                "id": r.id, "workspace_id": r.workspace_id, "command": r.command,
                "status": r.status.value if hasattr(r.status, "value") else r.status,
                "branch": r.branch, "promotion_id": r.promotion_id, "created_at": r.created_at,
                "side": "source" if r.workspace_id == pair.source_stack_id else "target",
            })
    await db.commit()
    return {"promotions": promos, "runs": runs}


# ─── promotions ──────────────────────────────────────────────────────────────


@router.post("/api/v1/env-links/{link_id}/promotions/preview")
async def preview_promotion(
    link_id: str,
    body: dict = Body(...),
    current_user: User = Depends(require_bu_admin),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    link = await _link(db, bu, link_id)
    pv = await promotion_service.build_preview(db, link, body, current_user)
    await db.commit()
    return pv.to_dict()


@router.post("/api/v1/env-links/{link_id}/promotions", status_code=status.HTTP_201_CREATED)
async def create_promotion(
    link_id: str,
    body: dict = Body(...),
    current_user: User = Depends(require_bu_admin),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    link = await _link(db, bu, link_id)
    promo, created = await promotion_service.create_promotion(db, link, body, current_user)
    out = await promotion_service.to_dict(db, promo, detail=True)
    await db.commit()
    if not created:
        return JSONResponse(status_code=200, content=_jsonable({**out, "replayed": True}))
    return out


@router.get("/api/v1/promotions")
async def list_promotions(
    link_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    q = select(Promotion).order_by(Promotion.created_at.desc()).limit(limit)
    if bu.bu_id is not None:
        q = q.where(Promotion.business_unit_id == bu.bu_id)
    if link_id:
        q = q.where(Promotion.link_id == link_id)
    items = [await promotion_service.to_dict(db, p) for p in (await db.execute(q)).scalars()]
    await db.commit()
    return {"items": items}


async def _promo(db: AsyncSession, bu: BUScope, promotion_id: str) -> Promotion:
    p = await db.get(Promotion, promotion_id)
    if p is None or (bu.bu_id is not None and p.business_unit_id != bu.bu_id):
        raise HTTPException(status_code=404, detail="Promotion not found")
    return p


@router.get("/api/v1/promotions/{promotion_id}")
async def get_promotion(
    promotion_id: str,
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    p = await _promo(db, bu, promotion_id)
    out = await promotion_service.to_dict(db, p, detail=True)
    await db.commit()
    return out


@router.post("/api/v1/promotions/{promotion_id}/revert", status_code=status.HTTP_201_CREATED)
async def revert_promotion(
    promotion_id: str,
    current_user: User = Depends(require_bu_admin),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    p = await _promo(db, bu, promotion_id)
    rev = await promotion_service.revert(db, p, current_user)
    out = await promotion_service.to_dict(db, rev, detail=True)
    await db.commit()
    return out


# ─── dashboard index ─────────────────────────────────────────────────────────


@router.get("/api/v1/env-links-stack-index")
async def stack_index(
    current_user: User = Depends(require_role(Role.viewer)),
    bu: BUScope = Depends(current_bu),
    db: AsyncSession = Depends(get_db),
):
    """{stack_id: [{link_id, link_name, pair_id, side, status}]} for Dashboard badges."""
    q = select(EnvPair, EnvLink.name).join(EnvLink, EnvLink.id == EnvPair.link_id)
    if bu.bu_id is not None:
        q = q.where(EnvLink.business_unit_id == bu.bu_id)
    out: dict[str, list[dict]] = {}
    for pair, name in (await db.execute(q)).all():
        for side, sid in (("source", pair.source_stack_id), ("target", pair.target_stack_id)):
            if sid:
                out.setdefault(sid, []).append({
                    "link_id": pair.link_id, "link_name": name, "pair_id": pair.id,
                    "side": side, "status": pair.status,
                })
    return out
