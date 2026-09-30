"""Environment links: CRUD, pair reconciliation, audit.

Pairing itself is pure (`env_pairing.py`); this module feeds it the BU's live
workspace set, materialises the result into `env_pairs`, and records every
link change in the tamper-evident audit log.

Pairs are reconciled on read as well as on write: importing or untracking a
workspace changes what a link pairs, and there is no workspace-change hook to
hang that off. It's one indexed query for the BU's workspaces plus a diff
against the stored pairs — cheap at this fleet size.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.env_link import EnvLink, EnvPair
from app.models.workspace import Workspace
from app.schemas.env_link import (
    EnvLinkCreate,
    EnvLinkResponse,
    EnvLinkRules,
    EnvLinkUpdate,
    PairingPreview,
    PairListResponse,
    PairOut,
    PairSummary,
    ProtectedRules,
)
from app.services import env_pairing as ep
from app.services.audit_chain import stamp

logger = logging.getLogger(__name__)

# Fields whose change invalidates cached compares (bumps rules_version).
_RULE_FIELDS = ("source_node", "target_node", "rewrite_rules", "pair_overrides", "protected_rules")


# ─── helpers ─────────────────────────────────────────────────────────────────


async def _bu_stacks(db: AsyncSession, bu_id: str) -> tuple[list[ep.Stack], dict[str, str]]:
    rows = (
        await db.execute(
            select(Workspace.id, Workspace.tf_working_dir, Workspace.kind, Workspace.name).where(
                Workspace.business_unit_id == bu_id
            )
        )
    ).all()
    stacks = [ep.Stack(id=r.id, tf_working_dir=r.tf_working_dir or "", kind=r.kind or "terraform",
                       name=r.name) for r in rows]
    return stacks, {s.id: s.name for s in stacks}


def _bad_request(e: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


def _normalise_rules(rules: EnvLinkRules | EnvLinkUpdate) -> dict:
    """Validate the rule-ish fields present on a body; return them as plain dicts."""
    out: dict = {}
    try:
        if rules.rewrite_rules is not None:
            out["rewrite_rules"] = ep.validate_rewrite_rules([r.as_dict() for r in rules.rewrite_rules])
        if rules.pair_overrides is not None:
            out["pair_overrides"] = ep.validate_overrides(rules.pair_overrides.model_dump())
        pr = getattr(rules, "protected_rules", None)
        if pr is not None:
            out["protected_rules"] = ep.validate_protected_rules(pr.model_dump())
    except ep.PairingError as e:
        raise _bad_request(e) from None
    for side in ("source_node", "target_node"):
        node = getattr(rules, side, None)
        if node is not None:
            out[side] = {"level": node.level, "path": ep.norm_path(node.path)}
    return out


def _validate_nodes(source: dict, target: dict, stacks: list[ep.Stack]) -> str:
    try:
        return ep.validate_nodes(source["path"], target["path"], stacks, source["level"], target["level"])
    except ep.PairingError as e:
        raise _bad_request(e) from None


def _pair_out(p, names: dict[str, str], row: Optional[EnvPair] = None) -> PairOut:
    src = row if row is not None else p
    return PairOut(
        id=row.id if row is not None else None,
        relative_path=src.relative_path,
        source_rel=src.source_rel,
        target_rel=src.target_rel,
        source_stack_id=src.source_stack_id,
        target_stack_id=src.target_stack_id,
        source_stack_name=names.get(src.source_stack_id or ""),
        target_stack_name=names.get(src.target_stack_id or ""),
        status=src.status,
        reason=src.reason,
        proposed_target_rel=src.proposed_target_rel,
        summary=row.summary if row is not None else None,
        last_compared_at=row.last_compared_at if row is not None else None,
    )


def _summary(statuses: list[str]) -> PairSummary:
    counts = {s: 0 for s in ep.PAIR_STATUSES}
    for s in statuses:
        counts[s] = counts.get(s, 0) + 1
    return PairSummary(**counts, total=len(statuses))


def _audit_row(user_id: str, action: str, link: EnvLink, details: dict) -> AuditLog:
    return AuditLog(
        user_id=user_id,
        action=action,
        resource_type="env_link",
        resource_id=link.id,
        details={"business_unit_id": link.business_unit_id, "name": link.name, **details},
    )


def _rules_snapshot(link: EnvLink) -> dict:
    return {f: getattr(link, f) for f in _RULE_FIELDS}


# ─── pairing ─────────────────────────────────────────────────────────────────


def _run_pairing(rules: dict, stacks: list[ep.Stack]) -> ep.PairingResult:
    try:
        return ep.pair_stacks(
            rules["source_node"]["path"],
            rules["target_node"]["path"],
            stacks,
            rules.get("rewrite_rules") or [],
            rules.get("pair_overrides") or {},
        )
    except ep.PairingError as e:
        raise _bad_request(e) from None


async def preview_pairs(db: AsyncSession, bu_id: str, body: EnvLinkRules) -> PairingPreview:
    """Dry-run pairing for the link builder — nothing is persisted."""
    rules = _normalise_rules(body)
    stacks, names = await _bu_stacks(db, bu_id)
    level = _validate_nodes(rules["source_node"], rules["target_node"], stacks)
    result = _run_pairing(rules, stacks)
    src_acct = ep.node_account_id(rules["source_node"]["path"])
    tgt_acct = ep.node_account_id(rules["target_node"]["path"])
    return PairingPreview(
        level=level,
        source_account_id=src_acct,
        target_account_id=tgt_acct,
        pairs=[_pair_out(p, names) for p in result.pairs],
        summary=_summary([p.status for p in result.pairs]),
        helm_skipped=result.helm_skipped,
        warnings=result.warnings,
        default_protected_rules=ProtectedRules(**ep.default_protected_rules(src_acct, tgt_acct)),
    )


async def reconcile_pairs(
    db: AsyncSession, link: EnvLink, stacks: list[ep.Stack]
) -> ep.PairingResult:
    """Bring `env_pairs` in line with the current pairing. Caller commits.

    A pair keeps its row (id, compare summary) while its key and both stack
    ids are unchanged. If either stack id changed underneath the same paths
    (untrack + re-import), the compare result belongs to the old stacks, so it
    is reset to `not_compared`.
    """
    try:
        result = ep.pair_stacks(
            link.source_node["path"],
            link.target_node["path"],
            stacks,
            link.rewrite_rules or [],
            link.pair_overrides or {},
        )
    except ep.PairingError as e:
        # A link whose stored rules no longer pair (e.g. a regex that became
        # invalid under a newer Python) must not 500 every read of it.
        logger.warning("env_link %s: pairing failed: %s", link.id, e)
        result = ep.PairingResult(warnings=[f"Pairing failed: {e}"])

    existing = {
        r.pair_key: r
        for r in (await db.execute(select(EnvPair).where(EnvPair.link_id == link.id))).scalars()
    }
    seen: set[str] = set()
    for p in result.pairs:
        seen.add(p.key)
        row = existing.get(p.key)
        if row is None:
            db.add(EnvPair(
                link_id=link.id,
                pair_key=p.key,
                relative_path=p.relative_path,
                source_rel=p.source_rel,
                target_rel=p.target_rel,
                source_stack_id=p.source_stack_id,
                target_stack_id=p.target_stack_id,
                status=p.status,
                reason=p.reason,
                proposed_target_rel=p.proposed_target_rel,
            ))
            continue
        stacks_changed = (
            row.source_stack_id != p.source_stack_id or row.target_stack_id != p.target_stack_id
        )
        # Compare-derived statuses survive a reconcile; structural ones are
        # always re-derived from the pairing.
        compared = row.status in ("in_sync", "diverged")
        if p.status != "not_compared" or stacks_changed or not compared:
            row.status = p.status
        if stacks_changed:
            row.summary = None
            row.cache_key = None
            row.last_compared_at = None
        row.relative_path = p.relative_path
        row.source_rel = p.source_rel
        row.target_rel = p.target_rel
        row.source_stack_id = p.source_stack_id
        row.target_stack_id = p.target_stack_id
        row.reason = p.reason
        row.proposed_target_rel = p.proposed_target_rel
    stale = [k for k in existing if k not in seen]
    if stale:
        await db.execute(
            delete(EnvPair).where(EnvPair.link_id == link.id, EnvPair.pair_key.in_(stale))
        )
    await db.flush()
    return result


async def _pair_rows(db: AsyncSession, link_id: str) -> list[EnvPair]:
    return list(
        (
            await db.execute(
                select(EnvPair)
                .where(EnvPair.link_id == link_id)
                .order_by(EnvPair.relative_path, EnvPair.pair_key)
            )
        ).scalars()
    )


async def _response(
    db: AsyncSession, link: EnvLink, result: ep.PairingResult
) -> EnvLinkResponse:
    rows = await _pair_rows(db, link.id)
    return EnvLinkResponse(
        id=link.id,
        business_unit_id=link.business_unit_id,
        name=link.name,
        engine=link.engine,
        source_node=link.source_node,
        target_node=link.target_node,
        level=link.source_node["level"],
        source_account_id=ep.node_account_id(link.source_node["path"]),
        target_account_id=ep.node_account_id(link.target_node["path"]),
        rewrite_rules=link.rewrite_rules or [],
        pair_overrides=link.pair_overrides or {},
        protected_rules=link.protected_rules or {},
        rules_version=link.rules_version,
        created_by=link.created_by,
        updated_by=link.updated_by,
        created_at=link.created_at,
        updated_at=link.updated_at,
        pair_summary=_summary([r.status for r in rows]),
        helm_skipped=result.helm_skipped,
        warnings=result.warnings,
    )


# ─── CRUD ────────────────────────────────────────────────────────────────────


async def get_link(db: AsyncSession, bu_id: Optional[str], link_id: str) -> EnvLink:
    """Fetch a link, 404 if missing or outside the caller's BU (no existence leak)."""
    link = await db.get(EnvLink, link_id)
    if link is None or (bu_id is not None and link.business_unit_id != bu_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Environment link not found")
    return link


async def list_links(db: AsyncSession, bu_id: Optional[str]) -> list[EnvLinkResponse]:
    q = select(EnvLink).order_by(EnvLink.name)
    if bu_id is not None:
        q = q.where(EnvLink.business_unit_id == bu_id)
    links = list((await db.execute(q)).scalars())
    stacks_by_bu: dict[str, list[ep.Stack]] = {}
    out = []
    for link in links:
        if link.business_unit_id not in stacks_by_bu:
            stacks_by_bu[link.business_unit_id] = (await _bu_stacks(db, link.business_unit_id))[0]
        result = await reconcile_pairs(db, link, stacks_by_bu[link.business_unit_id])
        out.append(await _response(db, link, result))
    await db.commit()
    return out


async def read_link(db: AsyncSession, bu_id: Optional[str], link_id: str) -> EnvLinkResponse:
    link = await get_link(db, bu_id, link_id)
    stacks, _ = await _bu_stacks(db, link.business_unit_id)
    result = await reconcile_pairs(db, link, stacks)
    resp = await _response(db, link, result)
    await db.commit()
    return resp


async def list_pairs(
    db: AsyncSession, bu_id: Optional[str], link_id: str, status_filter: Optional[set[str]] = None
) -> PairListResponse:
    link = await get_link(db, bu_id, link_id)
    stacks, names = await _bu_stacks(db, link.business_unit_id)
    result = await reconcile_pairs(db, link, stacks)
    rows = await _pair_rows(db, link.id)
    await db.commit()
    items = [_pair_out(None, names, r) for r in rows if not status_filter or r.status in status_filter]
    return PairListResponse(
        items=items, summary=_summary([r.status for r in rows]), warnings=result.warnings
    )


async def create_link(
    db: AsyncSession, bu_id: str, body: EnvLinkCreate, user_id: str
) -> EnvLinkResponse:
    rules = _normalise_rules(body)
    stacks, _ = await _bu_stacks(db, bu_id)
    _validate_nodes(rules["source_node"], rules["target_node"], stacks)
    if "protected_rules" not in rules:
        rules["protected_rules"] = ep.default_protected_rules(
            ep.node_account_id(rules["source_node"]["path"]),
            ep.node_account_id(rules["target_node"]["path"]),
        )
    link = EnvLink(
        business_unit_id=bu_id,
        name=body.name.strip(),
        engine="terraform",
        source_node=rules["source_node"],
        target_node=rules["target_node"],
        rewrite_rules=rules.get("rewrite_rules", []),
        pair_overrides=rules.get("pair_overrides", {"pairs": [], "exclude": []}),
        protected_rules=rules["protected_rules"],
        rules_version=1,
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(link)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An environment link named '{body.name}' already exists in this Business Unit",
        ) from None
    result = await reconcile_pairs(db, link, stacks)
    audit = _audit_row(user_id, "env_link.create", link, {"rules": _rules_snapshot(link)})
    db.add(audit)
    await stamp(db, audit)
    await db.commit()
    await db.refresh(link)
    return await _response(db, link, result)


async def update_link(
    db: AsyncSession, bu_id: Optional[str], link_id: str, body: EnvLinkUpdate, user_id: str
) -> EnvLinkResponse:
    link = await get_link(db, bu_id, link_id)
    rules = _normalise_rules(body)
    stacks, _ = await _bu_stacks(db, link.business_unit_id)
    source = rules.get("source_node", link.source_node)
    target = rules.get("target_node", link.target_node)
    if "source_node" in rules or "target_node" in rules:
        _validate_nodes(source, target, stacks)

    before = _rules_snapshot(link)
    old_name = link.name
    for f, v in rules.items():
        setattr(link, f, v)
    if body.name is not None:
        link.name = body.name.strip()
    after = _rules_snapshot(link)
    changes = {f: {"old": before[f], "new": after[f]} for f in _RULE_FIELDS if before[f] != after[f]}
    if old_name != link.name:
        changes["name"] = {"old": old_name, "new": link.name}
    if not changes:
        return await read_link(db, bu_id, link_id)
    if any(f in changes for f in _RULE_FIELDS):
        link.rules_version = (link.rules_version or 1) + 1
        from app.services.env_compare_service import invalidate_link

        await invalidate_link(db, link.id)
    link.updated_by = user_id
    link.updated_at = datetime.now(timezone.utc)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An environment link named '{body.name}' already exists in this Business Unit",
        ) from None
    result = await reconcile_pairs(db, link, stacks)
    audit = _audit_row(
        user_id, "env_link.update", link,
        {"changes": changes, "rules_version": link.rules_version},
    )
    db.add(audit)
    await stamp(db, audit)
    await db.commit()
    await db.refresh(link)
    return await _response(db, link, result)


async def delete_link(db: AsyncSession, bu_id: Optional[str], link_id: str, user_id: str) -> None:
    link = await get_link(db, bu_id, link_id)
    audit = _audit_row(user_id, "env_link.delete", link, {"rules": _rules_snapshot(link)})
    # Children first — the schema has FK cascades but SQLite (tests) doesn't
    # enforce them without PRAGMA, and this codebase deletes explicitly anyway.
    from app.models.env_link import EnvCompareResult
    from app.models.promotion import Promotion, PromotionRun

    pair_ids = select(EnvPair.id).where(EnvPair.link_id == link.id)
    await db.execute(delete(EnvCompareResult).where(EnvCompareResult.pair_id.in_(pair_ids)))
    promo_ids = select(Promotion.id).where(Promotion.link_id == link.id)
    await db.execute(delete(PromotionRun).where(PromotionRun.promotion_id.in_(promo_ids)))
    await db.execute(delete(Promotion).where(Promotion.link_id == link.id))
    await db.execute(delete(EnvPair).where(EnvPair.link_id == link.id))
    await db.delete(link)
    db.add(audit)
    await stamp(db, audit)
    await db.commit()
