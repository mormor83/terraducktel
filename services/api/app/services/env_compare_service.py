"""Compare one environment pair: config (repo) + live state, cached.

Config: each side is read at the HEAD of **its own pinned branch**
(`workspace.repo_ref`) through the git read cache (`git_repo`), then diffed
by the link's engine (`envdiff`). State: each side's tfstate is read from the
store the API already fronts (`routers/state.py::_service_for`) — the same
bytes Terraform and the drift detector use — so a compare never scans AWS.

The result is cached per (pair, direction) in `env_compare_results` under a
key over both commits, both state serials and the link's `rules_version`.
`env_pairs.cache_key` mirrors the latest key; invalidation (link edit, repo
sync, a run finishing on either stack) just nulls it, and the next view
recomputes in the background (`bg_worker`), serving the old result flagged
stale meanwhile.

For a `missing_in_target` pair there's nothing to diff against: the compare
instead produces the *create preview* — the source leaf with account ids
substituted, rewrite rules applied and backend blocks stripped, exactly what
"Create in target" would commit.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_unit import BusinessUnit
from app.models.env_link import EnvCompareResult, EnvLink, EnvPair
from app.models.workspace import Workspace
from app.services import env_pairing as ep
from app.services import git_repo
from app.services.envdiff import get_engine
from app.services.envdiff import state as statediff

logger = logging.getLogger(__name__)

ENGINE_VERSION = "1"
RESULT_TTL = timedelta(minutes=10)


@dataclass
class SideCtx:
    ws: Optional[Workspace]
    repo_url: Optional[str]
    branch: Optional[str]
    path: str
    account_id: Optional[str]
    commit: Optional[str] = None
    files: dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None
    state: Optional[statediff.Inventory] = None
    state_error: Optional[str] = None

    def ref(self) -> dict:
        return {
            "stack_id": self.ws.id if self.ws else None,
            "stack_name": self.ws.name if self.ws else None,
            "repo_url": self.repo_url,
            "branch": self.branch,
            "commit": self.commit,
            "path": self.path,
            "account_id": self.account_id,
            "drift_status": self.ws.drift_status if self.ws else None,
            "state_serial": self.state.meta.serial if self.state else None,
            "state_resources": self.state.meta.resource_count if self.state else None,
            "state_error": self.state_error,
            "error": self.error,
        }


# ─── reads ───────────────────────────────────────────────────────────────────


async def _bu_slug(db: AsyncSession, bu_id: str) -> Optional[str]:
    bu = await db.get(BusinessUnit, bu_id)
    return bu.slug if bu else None


async def read_creds(db: AsyncSession, repo_url: str, bu_slug: Optional[str]) -> git_repo.Creds:
    # Same credential resolution as repo sync / discovery (read-only PAT).
    from app.services.repo_sync import _resolve_token

    user, token = await _resolve_token(db, repo_url, bu_slug)
    return git_repo.Creds(username=user, token=token)


async def read_side_files(
    db: AsyncSession, side: SideCtx, bu_slug: Optional[str], force: bool = False
) -> None:
    if not side.repo_url or side.repo_url.startswith("local://"):
        side.error = "Stack is not git-synced — nothing to read"
        return
    creds = await read_creds(db, side.repo_url, bu_slug)
    try:
        side.commit = await asyncio.to_thread(
            git_repo.fetch_branch, side.repo_url, side.branch or "main", creds, force
        )
        side.files = await asyncio.to_thread(git_repo.read_dir, side.repo_url, side.commit, side.path)
    except git_repo.GitError as e:
        side.error = str(e)


async def read_side_state(db: AsyncSession, side: SideCtx) -> None:
    if side.ws is None:
        return
    try:
        from app.routers.state import _service_for

        store, key = await _service_for(side.ws, db)
        raw = await asyncio.to_thread(store.get_state_at, key)
        side.state = statediff.parse_state(raw)
    except Exception as e:  # noqa: BLE001 — a missing bucket must not break the config tab
        # Never include the exception text verbatim: backends can echo keys/URLs.
        side.state_error = f"State unavailable ({type(e).__name__})"


def _most_common(values: list[Optional[str]]) -> Optional[str]:
    vals = [v for v in values if v]
    return Counter(vals).most_common(1)[0][0] if vals else None


async def _node_defaults(db: AsyncSession, link: EnvLink, node_path: str) -> tuple[Optional[str], Optional[str]]:
    """(repo_url, branch) most used by the stacks under a node — the default
    home for a stack created there."""
    rows = (
        await db.execute(
            select(Workspace.tf_working_dir, Workspace.repo_url, Workspace.repo_ref).where(
                Workspace.business_unit_id == link.business_unit_id
            )
        )
    ).all()
    under = [r for r in rows if ep.under(r.tf_working_dir or "", node_path)]
    return _most_common([r.repo_url for r in under]), _most_common([r.repo_ref for r in under])


def sides_for(pair: EnvPair, link: EnvLink, direction: str) -> tuple[str, str, str, str]:
    """(from_stack_id, to_stack_id, from_node_path, to_node_path) for a direction."""
    if direction == "reverse":
        return (pair.target_stack_id, pair.source_stack_id,
                link.target_node["path"], link.source_node["path"])
    return (pair.source_stack_id, pair.target_stack_id,
            link.source_node["path"], link.target_node["path"])


def _rel_for(pair: EnvPair, direction: str, side: str) -> Optional[str]:
    """Relative path of a pair on the 'from'/'to' side for a direction."""
    fwd = {"from": pair.source_rel, "to": pair.target_rel}
    rev = {"from": pair.target_rel, "to": pair.source_rel}
    rel = (rev if direction == "reverse" else fwd)[side]
    if rel is None and side == "to" and direction == "forward":
        rel = pair.proposed_target_rel
    return rel


async def build_sides(
    db: AsyncSession, link: EnvLink, pair: EnvPair, direction: str, *, force: bool = False,
    with_state: bool = True,
) -> tuple[SideCtx, SideCtx]:
    from_id, to_id, from_node, to_node = sides_for(pair, link, direction)
    slug = await _bu_slug(db, link.business_unit_id)

    async def mk(stack_id: Optional[str], node: str, rel: Optional[str]) -> SideCtx:
        ws = await db.get(Workspace, stack_id) if stack_id else None
        if ws is not None:
            return SideCtx(ws=ws, repo_url=ws.repo_url, branch=ws.repo_ref, path=ws.tf_working_dir,
                           account_id=ep.node_account_id(ws.tf_working_dir or ""))
        repo_url, branch = await _node_defaults(db, link, node)
        path = f"{node}/{rel}".rstrip("/") if rel else node
        return SideCtx(ws=None, repo_url=repo_url, branch=branch, path=path,
                       account_id=ep.node_account_id(node))

    src = await mk(from_id, from_node, _rel_for(pair, direction, "from"))
    tgt = await mk(to_id, to_node, _rel_for(pair, direction, "to"))
    await read_side_files(db, src, slug, force)
    if tgt.ws is not None:
        await read_side_files(db, tgt, slug, force)
    if with_state:
        await read_side_state(db, src)
        await read_side_state(db, tgt)
    return src, tgt


# ─── rules helpers ───────────────────────────────────────────────────────────


def substitution_pairs(link: EnvLink, direction: str, src: SideCtx, tgt: SideCtx) -> list[tuple[str, str]]:
    """Plain substitutions that map source-environment literals to target ones."""
    pairs: list[tuple[str, str]] = []
    if src.account_id and tgt.account_id and src.account_id != tgt.account_id:
        pairs.append((src.account_id, tgt.account_id))
    for r in link.rewrite_rules or []:
        if r.get("regex") or not r.get("from"):
            continue
        pairs.append((r["to"], r["from"]) if direction == "reverse" else (r["from"], r["to"]))
    return pairs


def apply_substitutions(text: str, link: EnvLink, direction: str, subs: list[tuple[str, str]]) -> str:
    for a, b in subs:
        text = text.replace(a, b)
    if direction == "forward":
        import re

        for r in link.rewrite_rules or []:
            if r.get("regex") and r.get("from"):
                text = re.sub(r["from"], r["to"], text)
    return text


def create_preview(link: EnvLink, direction: str, src: SideCtx, tgt: SideCtx) -> dict:
    """The files "Create in target" would commit, and what was changed in them."""
    from app.services.envdiff.create import strip_backend

    subs = substitution_pairs(link, direction, src, tgt)
    files: list[dict] = []
    warnings: list[str] = []
    for rel, text in sorted(src.files.items()):
        if git_repo.is_placeholder(text):
            warnings.append(f"{rel}: binary or oversized — copied as-is")
            files.append({"path": rel, "text": None, "copy": True, "changes": []})
            continue
        new = apply_substitutions(text, link, direction, subs)
        changes = [f"{a} → {b}" for a, b in subs if a in text]
        if rel.endswith(".tf"):
            stripped, removed = strip_backend(new)
            if removed:
                new = stripped
                changes.append("backend block removed (TDT injects its own state backend)")
        if "terraform_remote_state" in new:
            warnings.append(f"{rel}: reads terraform_remote_state — check it points at the target environment")
        files.append({"path": rel, "text": new, "copy": False, "changes": changes})
    return {
        "path": tgt.path,
        "repo_url": tgt.repo_url,
        "branch": tgt.branch,
        "account_id": tgt.account_id,
        "files": files,
        "warnings": warnings,
        "substitutions": [{"from": a, "to": b} for a, b in subs],
    }


def state_normalizer(link: EnvLink, direction: str, src: SideCtx, tgt: SideCtx):
    return statediff.normalizer(substitution_pairs(link, direction, src, tgt))


# ─── compute ─────────────────────────────────────────────────────────────────


def cache_key(link: EnvLink, direction: str, src: SideCtx, tgt: SideCtx) -> str:
    raw = json.dumps([
        ENGINE_VERSION, direction, link.rules_version,
        src.commit, src.path, tgt.commit, tgt.path,
        src.state.meta.serial if src.state else None, src.state.meta.lineage if src.state else None,
        tgt.state.meta.serial if tgt.state else None, tgt.state.meta.lineage if tgt.state else None,
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def _summary(config_diff: Optional[dict], state: Optional[dict], src: SideCtx, tgt: SideCtx) -> dict:
    s = dict((config_diff or {}).get("summary") or {})
    s["branches_differ"] = bool(src.branch and tgt.branch and src.branch != tgt.branch)
    s["source_branch"], s["target_branch"] = src.branch, tgt.branch
    s["source_commit"], s["target_commit"] = src.commit, tgt.commit
    if state is not None:
        s["state"] = {
            "only_in_source": len(state.get("only_in_source") or []),
            "only_in_target": len(state.get("only_in_target") or []),
            "differing": len(state.get("differing") or []),
        }
    return s


async def compute(
    db: AsyncSession, pair: EnvPair, direction: str = "forward", *, force: bool = False
) -> EnvCompareResult:
    """Compute (or reuse) the compare for a pair; persist it. Caller commits."""
    link = await db.get(EnvLink, pair.link_id)
    src, tgt = await build_sides(db, link, pair, direction, force=force)
    key = cache_key(link, direction, src, tgt)

    existing = (
        await db.execute(
            select(EnvCompareResult).where(
                EnvCompareResult.pair_id == pair.id, EnvCompareResult.direction == direction
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.cache_key == key and not force:
        if direction == "forward":
            pair.cache_key = key
        return existing

    config: Optional[dict] = None
    state: Optional[dict] = None
    error = src.error or tgt.error
    extra: dict = {}
    if tgt.ws is None:
        # Missing in target: the "diff" is the create preview.
        if not src.error:
            extra["create_preview"] = create_preview(link, direction, src, tgt)
    elif not error:
        engine = get_engine(link.engine or "terraform")
        config = engine.config_diff(src.files, tgt.files, link.protected_rules or {}).to_dict()
    if src.state is not None and tgt.state is not None and tgt.ws is not None:
        state = statediff.state_diff(src.state, tgt.state, normalize=state_normalizer(link, direction, src, tgt))
    refs = {"source": src.ref(), "target": tgt.ref(), **extra}

    row = existing or EnvCompareResult(pair_id=pair.id, direction=direction)
    row.cache_key = key
    row.refs = refs
    row.config_diff = config
    row.state_diff = state
    row.error = error
    row.computed_at = datetime.now(timezone.utc)
    if existing is None:
        db.add(row)

    if direction == "forward":
        pair.cache_key = key
        pair.last_compared_at = row.computed_at
        pair.summary = _summary(config, state, src, tgt)
        if pair.status in ("not_compared", "in_sync", "diverged") and config is not None:
            promotable = (config.get("summary") or {}).get("promotable_count", 0)
            pair.status = "diverged" if promotable else "in_sync"
    await db.flush()
    return row


def is_fresh(result: Optional[EnvCompareResult]) -> bool:
    """Serveable without recomputing: not invalidated, and younger than the TTL
    (the TTL is the backstop for changes TDT can't see, like a push to a
    branch with no webhook)."""
    if result is None or not result.cache_key:
        return False
    at = result.computed_at
    if at is not None and at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at is None or datetime.now(timezone.utc) - at < RESULT_TTL


async def get_result(db: AsyncSession, pair_id: str, direction: str) -> Optional[EnvCompareResult]:
    return (
        await db.execute(
            select(EnvCompareResult).where(
                EnvCompareResult.pair_id == pair_id, EnvCompareResult.direction == direction
            )
        )
    ).scalar_one_or_none()


# ─── invalidation ────────────────────────────────────────────────────────────


async def _invalidate_pairs(db: AsyncSession, pair_ids_q) -> None:
    await db.execute(update(EnvPair).where(EnvPair.id.in_(pair_ids_q)).values(cache_key=None))
    await db.execute(
        update(EnvCompareResult).where(EnvCompareResult.pair_id.in_(pair_ids_q)).values(cache_key="")
    )


async def invalidate_stacks(db: AsyncSession, stack_ids: list[str]) -> None:
    """A run finished / state changed on these stacks — their pairs need a recompare."""
    if not stack_ids:
        return
    q = select(EnvPair.id).where(
        (EnvPair.source_stack_id.in_(stack_ids)) | (EnvPair.target_stack_id.in_(stack_ids))
    )
    await _invalidate_pairs(db, q)


async def invalidate_bu(db: AsyncSession, bu_id: Optional[str]) -> None:
    """Repo synced — every pair in the BU may have new commits."""
    links = select(EnvLink.id)
    if bu_id is not None:
        links = links.where(EnvLink.business_unit_id == bu_id)
    await _invalidate_pairs(db, select(EnvPair.id).where(EnvPair.link_id.in_(links)))
    for (repo_url, branch) in list(git_repo._last_fetch):
        git_repo.forget_fetch(repo_url, branch)


async def invalidate_link(db: AsyncSession, link_id: str) -> None:
    await _invalidate_pairs(db, select(EnvPair.id).where(EnvPair.link_id == link_id))
