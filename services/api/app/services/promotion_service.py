"""Environment promotions: preview → commit → ordinary runs → verify.

Hard rule: a promotion never bypasses the pipeline. It commits to the target
stack's pinned branch and then creates ordinary `apply` runs through
`run_service.create_run` (which refuses auto-approve for promotion runs).
Checkov, OPA, cost and human approval happen exactly as for a manual run.

Flow
  preview   Re-read both sides at their *current* HEADs (fetch forced), re-diff,
            check the selection still exists, apply the selected hunks to the
            target text, group file changes per (repo, branch), and collect
            blockers + warnings. Nothing is written.
  create    Re-runs preview (a stale client can't commit what it didn't see),
            refuses on blockers, then commits one commit per (repo, branch)
            with the initiating user as author and the TDT bot as committer.
            On a non-fast-forward it re-syncs, re-applies the same hunks and
            retries once. Then registers created stacks and creates one run per
            affected target stack, tagged with `promotion_id`.
  verify    When a promotion run reaches `applied`, the pair is recompared and
            the residual difference recorded, so the screen shows the gap
            closing (or what's left).

Status: the git phase is stored; everything after is derived from the runs
(`derive_status`) and written back (with an audit row) whenever it changes.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.aws_account import AwsAccount
from app.models.business_unit import BusinessUnit
from app.models.env_link import EnvLink, EnvPair
from app.models.promotion import Promotion, PromotionRun
from app.models.run import Run, RunStatus
from app.models.run_step import RunStep
from app.models.user import User
from app.models.workspace import Workspace
from app.services import env_compare_service as cmp
from app.services import env_link_service
from app.services import git_repo, git_write_config, run_service
from app.services.audit_chain import stamp
from app.services.envdiff import get_engine
from app.services.repo_discovery import _classify

logger = logging.getLogger(__name__)

PROMOTION_TRAILER = "Terraducktel-Promotion"
MANY_STACKS_WARNING = 10
IDEMPOTENCY_WINDOW = timedelta(minutes=15)

TERMINAL_RUN = {RunStatus.APPLIED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value}
ACTIVE_STATUSES = {"pending", "committing", "running", "awaiting_approval", "applying", "verifying"}
FINAL_STATUSES = {"commit_failed", "succeeded", "partially_succeeded", "failed", "rejected"}


# ─── selection ───────────────────────────────────────────────────────────────


@dataclass
class PairSel:
    pair_id: str
    hunk_ids: list[str] = field(default_factory=list)
    create_in_target: bool = False
    target_branch: Optional[str] = None


@dataclass
class Selection:
    direction: str
    pairs: list[PairSel]
    overrides: dict[str, str]  # hunk_id -> reason
    confirm_reverse: bool = False

    @classmethod
    def from_body(cls, body: dict) -> "Selection":
        direction = body.get("direction") or "forward"
        if direction not in ("forward", "reverse"):
            raise HTTPException(status_code=422, detail="direction must be 'forward' or 'reverse'")
        pairs = []
        for p in body.get("pairs") or []:
            pairs.append(PairSel(
                pair_id=str(p.get("pair_id") or ""),
                hunk_ids=sorted({str(h) for h in p.get("hunk_ids") or []}),
                create_in_target=bool(p.get("create_in_target")),
                target_branch=(p.get("target_branch") or None),
            ))
        overrides = {}
        for o in body.get("protected_overrides") or []:
            overrides[str(o.get("hunk_id"))] = str(o.get("reason") or "").strip()
        return cls(direction, pairs, overrides, bool(body.get("confirm_reverse")))

    def canonical(self) -> dict:
        return {
            "direction": self.direction,
            "pairs": sorted(
                [{"pair_id": p.pair_id, "hunk_ids": p.hunk_ids, "create_in_target": p.create_in_target,
                  "target_branch": p.target_branch} for p in self.pairs],
                key=lambda x: x["pair_id"],
            ),
            "protected_overrides": sorted(
                [{"hunk_id": h, "reason": r} for h, r in self.overrides.items()], key=lambda x: x["hunk_id"]
            ),
        }

    def hash(self, link_id: str) -> str:
        return hashlib.sha256(json.dumps([link_id, self.canonical()], sort_keys=True).encode()).hexdigest()


# ─── preview ─────────────────────────────────────────────────────────────────


@dataclass
class StackChange:
    pair_id: str
    relative_path: str
    target_stack_id: Optional[str]
    target_stack_name: Optional[str]
    target_path: str
    repo_url: Optional[str]
    branch: Optional[str]
    base_commit: Optional[str]
    create: bool
    hunk_ids: list[str]
    hunks: list[dict]
    files: dict[str, Optional[str]]          # repo-relative → new text (None = delete)
    before: dict[str, Optional[str]]         # repo-relative → old text
    source_path: str
    source_ws: Optional[Workspace] = None


@dataclass
class Preview:
    link: EnvLink
    selection: Selection
    changes: list[StackChange] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    changed_hunks: list[str] = field(default_factory=list)
    stale: bool = False
    commit_message: str = ""

    def groups(self) -> dict[tuple[str, str], list[StackChange]]:
        g: dict[tuple[str, str], list[StackChange]] = defaultdict(list)
        for c in self.changes:
            g[(c.repo_url or "", c.branch or "")].append(c)
        return g

    def to_dict(self) -> dict:
        import difflib

        out_changes = []
        for c in self.changes:
            files = []
            for path, new in sorted(c.files.items()):
                old = c.before.get(path)
                unified = "".join(difflib.unified_diff(
                    (old or "").splitlines(keepends=True), (new or "").splitlines(keepends=True),
                    fromfile=f"a/{path}" if old is not None else "/dev/null",
                    tofile=f"b/{path}" if new is not None else "/dev/null",
                ))
                status_ = "added" if old is None else ("removed" if new is None else "modified")
                files.append({"path": path, "status": status_, "unified": unified[:200_000]})
            out_changes.append({
                "pair_id": c.pair_id, "relative_path": c.relative_path,
                "target_stack_id": c.target_stack_id, "target_stack_name": c.target_stack_name,
                "target_path": c.target_path, "repo_url": c.repo_url, "branch": c.branch,
                "base_commit": c.base_commit, "create": c.create, "hunks": c.hunks, "files": files,
            })
        return {
            "direction": self.selection.direction,
            "changes": out_changes,
            "affected_stacks": len(self.changes),
            "commits": [
                {"repo_url": r, "branch": b, "stacks": len(cs)} for (r, b), cs in self.groups().items()
            ],
            "blockers": self.blockers,
            "warnings": self.warnings,
            "changed_hunks": self.changed_hunks,
            "stale": self.stale,
            "commit_message": self.commit_message,
            "selection_hash": self.selection.hash(self.link.id),
        }


def _hunk_brief(h: dict) -> dict:
    return {k: h.get(k) for k in ("id", "file", "key", "kind", "category", "classification",
                                  "source_value", "target_value", "group")}


async def _next_number(db: AsyncSession) -> int:
    return int((await db.execute(select(func.max(Promotion.number)))).scalar() or 0) + 1


def _default_message(link: EnvLink, pv: Preview, number: Optional[int]) -> str:
    src_node = link.target_node if pv.selection.direction == "reverse" else link.source_node
    tgt_node = link.source_node if pv.selection.direction == "reverse" else link.target_node
    if len(pv.changes) == 1:
        c = pv.changes[0]
        src, tgt = c.source_path, c.target_path
    else:
        src, tgt = src_node["path"], tgt_node["path"]
    ref = f"#{number}" if number is not None else "#<n>"
    lines = [f"promote({link.name}): {src} → {tgt} [promotion {ref}]", ""]
    for c in pv.changes:
        if c.create:
            lines.append(f"- create {c.target_path} from {c.source_path}")
            continue
        for h in c.hunks:
            if h.get("level") == "file":
                lines.append(f"- {c.relative_path or '.'}: {h['kind']} file {h['file']}")
            else:
                lines.append(f"- {c.relative_path or '.'}: {h['kind']} {h['key']} ({h['file']})")
    return "\n".join(lines).rstrip() + "\n"


async def _active_promotion_on(db: AsyncSession, stack_id: str, exclude: Optional[str] = None) -> Optional[Promotion]:
    q = (
        select(Promotion)
        .join(PromotionRun, PromotionRun.promotion_id == Promotion.id)
        .where(PromotionRun.target_stack_id == stack_id, Promotion.status.in_(ACTIVE_STATUSES))
    )
    for p in (await db.execute(q)).scalars():
        if p.id != exclude:
            return p
    return None


async def _run_in_progress(db: AsyncSession, stack_id: str) -> bool:
    r = await db.execute(
        select(Run.id).where(Run.workspace_id == stack_id, Run.status.notin_(list(TERMINAL_RUN))).limit(1)
    )
    return r.first() is not None


async def build_preview(
    db: AsyncSession, link: EnvLink, body: dict, user: User, *, stored_hunks_check: bool = True
) -> Preview:
    sel = Selection.from_body(body)
    pv = Preview(link=link, selection=sel)
    if not sel.pairs:
        pv.blockers.append("Nothing selected")
        return pv
    if sel.direction == "reverse":
        pv.warnings.append("You are promoting in reverse: target → source.")
        if not sel.confirm_reverse:
            pv.blockers.append("Reverse direction must be confirmed")

    bu = await db.get(BusinessUnit, link.business_unit_id)
    wcfg = await git_write_config.load(db, bu.slug)
    if not wcfg.enabled:
        pv.blockers.append("Git write access is disabled for this Business Unit (Settings → GitHub → Git write access)")
    elif not wcfg.token:
        pv.blockers.append("No git write token for this Business Unit (Settings → GitHub → Git write access)")

    # Pairs must be fresh (a workspace may have been imported/untracked).
    stacks, _names = await env_link_service._bu_stacks(db, link.business_unit_id)
    await env_link_service.reconcile_pairs(db, link, stacks)
    engine = get_engine(link.engine or "terraform")
    protected_rules = link.protected_rules or {}

    for ps in sel.pairs:
        pair = await db.get(EnvPair, ps.pair_id)
        if pair is None or pair.link_id != link.id:
            pv.blockers.append(f"Pair {ps.pair_id} is not part of this link any more")
            continue
        label = pair.relative_path or "."
        from_id, to_id, _fn, _tn = cmp.sides_for(pair, link, sel.direction)
        if from_id is None:
            pv.blockers.append(f"{label}: nothing to promote from — the stack only exists on the other side")
            continue
        src, tgt = await cmp.build_sides(db, link, pair, sel.direction, force=True, with_state=False)
        if src.error:
            pv.blockers.append(f"{label}: {src.error}")
            continue

        if to_id is None:
            # ── create in target ──
            if not ps.create_in_target:
                pv.blockers.append(f"{label}: missing in target — tick 'Create in target' to create it")
                continue
            if sel.direction == "reverse":
                pv.blockers.append(f"{label}: create-in-target is only available in the link's direction")
                continue
            if ps.target_branch:
                tgt.branch = ps.target_branch
            if not tgt.repo_url or not tgt.branch:
                pv.blockers.append(f"{label}: can't tell which repo/branch the target side lives in")
                continue
            acct_ok = tgt.account_id and (await db.execute(
                select(AwsAccount.id).where(AwsAccount.business_unit_id == link.business_unit_id,
                                            AwsAccount.account_id == tgt.account_id)
            )).first()
            if not acct_ok:
                # Same rule as a repo import (which never requires it): the stack
                # is registered anyway, but its runs get no account credentials.
                pv.warnings.append(
                    f"{label}: target AWS account {tgt.account_id or '?'} is not registered in this BU — "
                    "the new stack's runs will have no account credentials until it is"
                )
            creds = await cmp.read_creds(db, tgt.repo_url, bu.slug)
            try:
                base = await asyncio.to_thread(git_repo.fetch_branch, tgt.repo_url, tgt.branch, creds, True)
                existing = await asyncio.to_thread(git_repo.read_dir, tgt.repo_url, base, tgt.path)
            except git_repo.GitError as e:
                pv.blockers.append(f"{label}: {e}")
                continue
            if existing:
                pv.blockers.append(
                    f"{label}: {tgt.path} already exists on {tgt.branch} — import it as a stack instead"
                )
            prev = cmp.create_preview(link, sel.direction, src, tgt)
            files: dict[str, Optional[str]] = {}
            for f in prev["files"]:
                if f["text"] is None:
                    pv.blockers.append(f"{label}: {f['path']} is binary/oversized and can't be copied")
                    continue
                files[f"{tgt.path}/{f['path']}"] = f["text"]
            pv.warnings.extend(f"{label}: {w}" for w in prev["warnings"])
            if not files:
                pv.blockers.append(f"{label}: source stack has no files")
            pv.changes.append(StackChange(
                pair_id=pair.id, relative_path=label, target_stack_id=None, target_stack_name=None,
                target_path=tgt.path, repo_url=tgt.repo_url, branch=tgt.branch, base_commit=base,
                create=True, hunk_ids=[], hunks=[], files=files, before={}, source_path=src.path,
                source_ws=src.ws,
            ))
            continue

        # ── hunks into an existing target ──
        if ps.create_in_target:
            pv.blockers.append(f"{label}: already exists in target — nothing to create")
        if tgt.error:
            pv.blockers.append(f"{label}: {tgt.error}")
            continue
        # Stack-level blockers first: they explain *why* a selection may also
        # look stale (an active promotion has already committed it).
        active = await _active_promotion_on(db, tgt.ws.id)
        if active is not None:
            pv.blockers.append(f"{label}: promotion #{active.number} is still active on the target stack")
        if await _run_in_progress(db, tgt.ws.id):
            pv.blockers.append(f"{label}: the target stack has a run in progress")
        if not ps.hunk_ids:
            pv.blockers.append(f"{label}: no changes selected")
            continue
        diff = engine.config_diff(src.files, tgt.files, protected_rules).to_dict()
        by_id = {h["id"]: h for h in diff["hunks"]}
        missing = [h for h in ps.hunk_ids if h not in by_id]
        if missing:
            pv.changed_hunks.extend(missing)
            pv.blockers.append(
                f"{label}: {len(missing)} selected change(s) no longer match the current branches — "
                "recompare and review the selection"
            )
            continue
        if stored_hunks_check:
            stored = await cmp.get_result(db, pair.id, sel.direction)
            if stored is not None and stored.cache_key != cmp.cache_key(link, sel.direction, src, tgt):
                # Commits moved since the user looked; the hunks still exist so
                # the selection is valid, but say so.
                pv.stale = True
        # Grouped hunks travel together.
        groups = {by_id[h]["group"] for h in ps.hunk_ids if by_id[h].get("group")}
        chosen = [h for h in diff["hunks"] if h["id"] in ps.hunk_ids or (h.get("group") and h["group"] in groups)]
        for h in chosen:
            if h["classification"] == "backend":
                pv.blockers.append(f"{label}: {h['key']} is backend/remote-state config and can never be promoted")
            elif not h["applicable"]:
                pv.blockers.append(f"{label}: {h['key']} can't be applied automatically ({h.get('not_applicable_reason')})")
            elif h["classification"] == "protected":
                reason = sel.overrides.get(h["id"], "")
                if not reason:
                    pv.blockers.append(f"{label}: {h['key']} is protected — give a reason to include it")
                else:
                    carried = [a for a in (src.account_id,) if a and a in (h.get("source_value") or "")]
                    if carried or "arn:" in (h.get("source_value") or ""):
                        pv.warnings.append(
                            f"{label}: {h['key']} carries a source-environment value "
                            f"({'account ' + carried[0] if carried else 'an ARN'}) into the target"
                        )
        new_files, errors = engine.apply_hunks(src.files, tgt.files, [h["id"] for h in chosen], protected_rules)
        pv.blockers.extend(f"{label}: {e}" for e in errors)
        repo_files = {f"{tgt.path}/{rel}": txt for rel, txt in new_files.items()}
        before = {f"{tgt.path}/{rel}": tgt.files.get(rel) for rel in new_files}
        if tgt.ws.drift_status == "drifted":
            pv.warnings.append(f"{label}: the target has unresolved drift")
        if src.branch and tgt.branch and src.branch != tgt.branch:
            pv.warnings.append(f"{label}: source is on '{src.branch}', target on '{tgt.branch}'")
        pv.changes.append(StackChange(
            pair_id=pair.id, relative_path=label, target_stack_id=tgt.ws.id, target_stack_name=tgt.ws.name,
            target_path=tgt.path, repo_url=tgt.repo_url, branch=tgt.branch, base_commit=tgt.commit,
            create=False, hunk_ids=[h["id"] for h in chosen], hunks=[_hunk_brief(h) for h in chosen],
            files=repo_files, before=before, source_path=src.path, source_ws=src.ws,
        ))

    if pv.stale:
        pv.warnings.append("The compare was stale — both sides were re-read at their current commits")
    if len(pv.changes) > MANY_STACKS_WARNING:
        pv.warnings.append(f"This promotion affects {len(pv.changes)} stacks")
    for c in pv.changes:
        if not c.repo_url or not (c.repo_url.startswith("https://") or c.repo_url.startswith("http://")
                                  or c.repo_url.startswith("file://")):
            pv.blockers.append(f"{c.relative_path}: only https repositories can be written to")
    pv.commit_message = body.get("commit_message") or _default_message(link, pv, None)
    # De-duplicate while keeping order.
    pv.blockers = list(dict.fromkeys(pv.blockers))
    pv.warnings = list(dict.fromkeys(pv.warnings))
    return pv


# ─── create ──────────────────────────────────────────────────────────────────


def _trailer(message: str, promotion: Promotion) -> str:
    msg = message.rstrip() + "\n"
    return f"{msg}\n{PROMOTION_TRAILER}: {promotion.id}\n"


async def _audit(db: AsyncSession, user_id: Optional[str], action: str, p: Promotion, details: dict,
                 workspace_id: Optional[str] = None) -> None:
    row = AuditLog(
        user_id=user_id, action=action, resource_type="promotion", resource_id=p.id,
        workspace_id=workspace_id,
        details={"business_unit_id": p.business_unit_id, "link_id": p.link_id, "number": p.number, **details},
    )
    db.add(row)
    await stamp(db, row)


async def _lock_stacks(db: AsyncSession, stack_ids: list[str]) -> None:
    """Serialise promotions per target stack (Postgres advisory xact locks)."""
    if db.bind.dialect.name != "postgresql":
        return
    from sqlalchemy import text

    for sid in sorted(set(stack_ids)):
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"promotion:{sid}"})


async def create_promotion(db: AsyncSession, link: EnvLink, body: dict, user: User) -> tuple[Promotion, bool]:
    """Returns (promotion, created). `created=False` = idempotent replay."""
    sel = Selection.from_body(body)
    sel_hash = sel.hash(link.id)
    since = datetime.now(timezone.utc) - IDEMPOTENCY_WINDOW
    dup = (
        await db.execute(
            select(Promotion).where(
                Promotion.link_id == link.id, Promotion.selection_hash == sel_hash,
                # Only an in-flight twin is a double-click; retrying after a
                # failure must create a new promotion.
                Promotion.status.in_(ACTIVE_STATUSES), Promotion.created_at >= since,
                Promotion.kind == "promote",
            )
        )
    ).scalars().first()
    if dup is not None:
        return dup, False

    pv = await build_preview(db, link, body, user)
    if pv.blockers:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={"message": "Promotion is blocked", "blockers": pv.blockers})

    await _lock_stacks(db, [c.target_stack_id for c in pv.changes if c.target_stack_id])
    for c in pv.changes:
        if c.target_stack_id:
            active = await _active_promotion_on(db, c.target_stack_id)
            if active is not None:
                raise HTTPException(status_code=409, detail={
                    "message": "Promotion is blocked",
                    "blockers": [f"{c.relative_path}: promotion #{active.number} is still active on the target stack"],
                })

    promo = None
    for _attempt in range(3):
        number = await _next_number(db)
        promo = Promotion(
            number=number, link_id=link.id, business_unit_id=link.business_unit_id, kind="promote",
            direction=sel.direction, initiated_by=user.id, reason=(body.get("reason") or None),
            selection=sel.canonical(), selection_hash=sel_hash, status="committing",
        )
        promo.commit_message = (
            body.get("commit_message") or _default_message(link, pv, number)
        ).replace("[promotion #<n>]", f"[promotion #{number}]")
        db.add(promo)
        try:
            await db.flush()
            break
        except IntegrityError:
            await db.rollback()
            promo = None
    if promo is None:
        raise HTTPException(status_code=503, detail="Could not allocate a promotion number — retry")
    for c in pv.changes:
        if c.target_stack_id:
            db.add(PromotionRun(promotion_id=promo.id, pair_id=c.pair_id, target_stack_id=c.target_stack_id))
    await _audit(db, user.id, "promotion.create", promo, {
        "direction": sel.direction,
        "pairs": [{"pair_id": c.pair_id, "path": c.relative_path, "create": c.create,
                   "hunks": [h["key"] for h in c.hunks]} for c in pv.changes],
        "protected_overrides": [{"hunk_id": h, "reason": r} for h, r in sel.overrides.items()],
        "reason": promo.reason,
    })
    await db.commit()

    await _commit_and_run(db, link, promo, pv, user)
    return promo, True


async def _author(user: User) -> git_repo.Identity:
    name = (getattr(user, "display_name", None) or "").strip() or user.email.split("@")[0]
    return git_repo.Identity(name=name, email=user.email)


async def _push_group(
    db: AsyncSession, link: EnvLink, promo: Promotion, pv: Preview, repo_url: str, branch: str,
    changes: list[StackChange], user: User, wcfg: git_write_config.WriteConfig,
) -> dict:
    files: dict[str, Optional[str]] = {}
    for c in changes:
        files.update(c.files)
    message = _trailer(promo.commit_message or "", promo)
    author = await _author(user)
    committer = git_repo.Identity(wcfg.bot_name, wcfg.bot_email)
    base = changes[0].base_commit
    try:
        base_sha, sha = await asyncio.to_thread(
            git_repo.commit_files, repo_url, branch, wcfg.creds(), files, message, author, committer, base
        )
    except git_repo.NonFastForward:
        # Branch moved: re-read, re-apply the SAME hunks to the new HEAD, retry once.
        logger.info("promotion #%s: %s@%s moved, re-syncing", promo.number, repo_url, branch)
        git_repo.forget_fetch(repo_url, branch)
        body = {
            **promo.selection,
            "pairs": [p for p in promo.selection["pairs"] if p["pair_id"] in {c.pair_id for c in changes}],
            "confirm_reverse": True,
        }
        pv2 = await build_preview(db, link, body, user, stored_hunks_check=False)
        # The promotion itself is now "active" on these stacks — that blocker is expected.
        real = [b for b in pv2.blockers if f"promotion #{promo.number}" not in b]
        if real:
            raise git_repo.GitError("The branch moved and the selection no longer applies: " + "; ".join(real))
        files = {}
        for c in pv2.changes:
            files.update(c.files)
        base_sha, sha = await asyncio.to_thread(
            git_repo.commit_files, repo_url, branch, wcfg.creds(), files, message, author, committer,
            pv2.changes[0].base_commit,
        )
    git_repo.forget_fetch(repo_url, branch)
    return {
        "repo_url": repo_url, "branch": branch, "base_sha": base_sha, "sha": sha,
        "files": sorted(files), "pair_ids": [c.pair_id for c in changes],
        "stack_ids": [c.target_stack_id for c in changes if c.target_stack_id],
    }


async def _register_stack(db: AsyncSession, link: EnvLink, c: StackChange) -> Workspace:
    cand = _classify(c.target_path)
    src = c.source_ws
    env = cand.suggested_environment if cand else (src.environment if src else "dev")
    ws = Workspace(
        business_unit_id=link.business_unit_id,
        name=cand.name if cand else c.target_path.rsplit("/", 1)[-1],
        environment=env,
        aws_account_id=cand.aws_account_id if cand else (src.aws_account_id if src else "global"),
        region=cand.region if cand else (src.region if src else "us-east-1"),
        repo_url=c.repo_url,
        tf_working_dir=c.target_path,
        state_key=cand.state_key if cand else None,
        repo_ref=c.branch or "main",
        kind="terraform",
        path_status="ok",
    )
    db.add(ws)
    await db.flush()
    return ws


async def _commit_and_run(db: AsyncSession, link: EnvLink, promo: Promotion, pv: Preview, user: User) -> None:
    bu = await db.get(BusinessUnit, link.business_unit_id)
    wcfg = await git_write_config.load(db, bu.slug)
    commits: list[dict] = []
    errors: list[str] = []
    for (repo_url, branch), changes in pv.groups().items():
        try:
            commits.append(await _push_group(db, link, promo, pv, repo_url, branch, changes, user, wcfg))
        except git_repo.GitError as e:
            errors.append(f"{git_repo._redact_url(repo_url)} @ {branch}: {e}")
    promo.commits = commits
    promo.error = "\n".join(errors) or None
    promo.updated_at = datetime.now(timezone.utc)
    pushed_pairs = {pid for cm in commits for pid in cm["pair_ids"]}
    if not commits:
        promo.status = "commit_failed"
        await _audit(db, user.id, "promotion.status", promo, {"status": "commit_failed", "error": promo.error})
        await db.commit()
        return

    # Runs for every pushed stack (created stacks registered first).
    existing_rows = {
        r.target_stack_id: r
        for r in (await db.execute(select(PromotionRun).where(PromotionRun.promotion_id == promo.id))).scalars()
    }
    for c in pv.changes:
        if c.pair_id not in pushed_pairs:
            if c.target_stack_id and c.target_stack_id in existing_rows:
                await db.delete(existing_rows[c.target_stack_id])
            continue
        if c.create:
            ws = await _register_stack(db, link, c)
            pr = PromotionRun(promotion_id=promo.id, pair_id=c.pair_id, target_stack_id=ws.id, created_stack=True)
            db.add(pr)
            await _audit(db, user.id, "promotion.create_stack", promo,
                         {"path": c.target_path, "branch": c.branch}, workspace_id=ws.id)
        else:
            ws = await db.get(Workspace, c.target_stack_id)
            pr = existing_rows[c.target_stack_id]
        run = await run_service.create_run(
            db, ws, command="apply", triggered_by=user.id, branch=ws.repo_ref, promotion_id=promo.id
        )
        pr.run_id = run.id
    promo.status = "running"
    await _audit(db, user.id, "promotion.status", promo, {
        "status": "running",
        "commits": [{"repo": git_repo._redact_url(c["repo_url"]), "branch": c["branch"],
                     "base_sha": c["base_sha"], "sha": c["sha"]} for c in commits],
        "error": promo.error,
    })
    await env_link_service.reconcile_pairs(db, link, (await env_link_service._bu_stacks(db, link.business_unit_id))[0])
    await db.commit()


# ─── status ──────────────────────────────────────────────────────────────────


def derive_status(promo: Promotion, rows: list[tuple[PromotionRun, Optional[Run]]]) -> str:
    if promo.status in ("pending", "committing", "commit_failed"):
        return promo.status
    runs = [r for _, r in rows if r is not None]
    if not runs:
        return promo.status
    statuses = [r.status.value if hasattr(r.status, "value") else str(r.status) for r in runs]
    if any(s == "awaiting_approval" for s in statuses):
        return "awaiting_approval"
    if any(s == "applying" for s in statuses):
        return "applying"
    if any(s not in TERMINAL_RUN for s in statuses):
        return "running"
    applied = [pr for pr, r in rows if r is not None and _s(r) == "applied"]
    failed = [r for _, r in rows if r is not None and _s(r) == "failed"]
    cancelled = [r for _, r in rows if r is not None and _s(r) == "cancelled"]
    if len(applied) == len(runs):
        return "succeeded" if all(pr.verified_at is not None for pr in applied) else "verifying"
    if not applied:
        return "rejected" if cancelled and not failed else "failed"
    if any(pr.verified_at is None for pr in applied):
        return "verifying"
    return "partially_succeeded"


def _s(run: Run) -> str:
    return run.status.value if hasattr(run.status, "value") else str(run.status)


async def _rows(db: AsyncSession, promo: Promotion) -> list[tuple[PromotionRun, Optional[Run]]]:
    prs = list((await db.execute(select(PromotionRun).where(PromotionRun.promotion_id == promo.id))).scalars())
    out = []
    for pr in prs:
        out.append((pr, await db.get(Run, pr.run_id) if pr.run_id else None))
    return out


async def refresh_status(db: AsyncSession, promo: Promotion) -> str:
    """Recompute the derived status; persist + audit a change. Caller commits."""
    new = derive_status(promo, await _rows(db, promo))
    if new != promo.status:
        old = promo.status
        promo.status = new
        promo.updated_at = datetime.now(timezone.utc)
        await _audit(db, None, "promotion.status", promo, {"status": new, "previous": old})
    return new


async def on_run_changed(db: AsyncSession, run: Run) -> None:
    """Hook from patch_run (post-commit, own session). Applied → queue a verify."""
    if not run.promotion_id:
        return
    promo = await db.get(Promotion, run.promotion_id)
    if promo is None:
        return
    await cmp.invalidate_stacks(db, [run.workspace_id])
    if _s(run) == "applied":
        pr = (await db.execute(select(PromotionRun).where(PromotionRun.run_id == run.id))).scalar_one_or_none()
        if pr is not None and pr.verified_at is None:
            from app.services import bg_worker

            await bg_worker.enqueue(db, "env_verify", {"promotion_run_id": pr.id}, dedupe_key=f"verify:{pr.id}")
    await refresh_status(db, promo)
    await db.commit()


async def verify(db: AsyncSession, promotion_run_id: str) -> None:
    """Recompare the pair a promotion run touched; record what's left."""
    pr = await db.get(PromotionRun, promotion_run_id)
    if pr is None or pr.verified_at is not None:
        return
    promo = await db.get(Promotion, pr.promotion_id)
    link = await db.get(EnvLink, promo.link_id)
    stacks, _ = await env_link_service._bu_stacks(db, link.business_unit_id)
    await env_link_service.reconcile_pairs(db, link, stacks)
    col = EnvPair.source_stack_id if promo.direction == "reverse" else EnvPair.target_stack_id
    pair = (await db.execute(select(EnvPair).where(EnvPair.link_id == link.id, col == pr.target_stack_id))).scalars().first()
    residual: dict = {"in_sync": False}
    if pair is not None:
        pr.pair_id = pair.id
        pair.cache_key = None
        result = await cmp.compute(db, pair, promo.direction, force=True)
        summ = ((result.config_diff or {}).get("summary") or {})
        hunks = (result.config_diff or {}).get("hunks") or []
        promotable = [h for h in hunks if h["classification"] == "promotable"]
        residual = {
            "in_sync": not promotable and not result.error,
            "promotable": len(promotable),
            "protected": summ.get("protected_count", 0),
            "keys": [h["key"] for h in promotable[:20]],
            "error": result.error,
        }
    pr.residual = residual
    pr.verified_at = datetime.now(timezone.utc)
    await refresh_status(db, promo)
    await db.commit()


# ─── revert ──────────────────────────────────────────────────────────────────


async def revert(db: AsyncSession, promo: Promotion, user: User) -> Promotion:
    if promo.kind != "promote":
        raise HTTPException(status_code=409, detail="Only a promotion can be reverted")
    if not promo.commits:
        raise HTTPException(status_code=409, detail="Nothing was committed — nothing to revert")
    await refresh_status(db, promo)
    rows = await _rows(db, promo)
    if promo.status in ("pending", "committing") or any(r is not None and _s(r) not in TERMINAL_RUN for _, r in rows):
        raise HTTPException(status_code=409, detail="Wait for the promotion's runs to finish before reverting")
    if any(pr.created_stack for pr, _ in rows):
        raise HTTPException(
            status_code=409,
            detail="This promotion created stacks — revert can't remove them safely. "
                   "Destroy the created stacks from the Dashboard instead.",
        )
    already = (await db.execute(select(Promotion).where(
        Promotion.reverts_promotion_id == promo.id, Promotion.status != "commit_failed"))).scalars().first()
    if already is not None:
        raise HTTPException(status_code=409, detail=f"Already reverted by promotion #{already.number}")
    stack_ids = sorted({pr.target_stack_id for pr, _ in rows})
    for sid in stack_ids:
        if await _run_in_progress(db, sid):
            raise HTTPException(status_code=409, detail="A target stack has a run in progress")
        act = await _active_promotion_on(db, sid, exclude=promo.id)
        if act is not None:
            raise HTTPException(status_code=409, detail=f"Promotion #{act.number} is active on a target stack")
    link = await db.get(EnvLink, promo.link_id)
    bu = await db.get(BusinessUnit, link.business_unit_id)
    wcfg = await git_write_config.load(db, bu.slug)
    if not wcfg.enabled or not wcfg.token:
        raise HTTPException(status_code=409, detail="Git write access is not configured for this Business Unit")

    rev = None
    for _attempt in range(3):
        number = await _next_number(db)
        rev = Promotion(
            number=number, link_id=link.id, business_unit_id=link.business_unit_id, kind="revert",
            reverts_promotion_id=promo.id, direction=promo.direction, initiated_by=user.id,
            reason=f"Revert of promotion #{promo.number}", selection=promo.selection,
            selection_hash=hashlib.sha256(f"revert:{promo.id}".encode()).hexdigest(),
            status="committing",
            commit_message=f"revert({link.name}): promotion #{promo.number} [promotion #{number}]\n",
        )
        db.add(rev)
        try:
            await db.flush()
            break
        except IntegrityError:
            await db.rollback()
            rev = None
    if rev is None:
        raise HTTPException(status_code=503, detail="Could not allocate a promotion number — retry")
    for sid in stack_ids:
        db.add(PromotionRun(promotion_id=rev.id, target_stack_id=sid))
    await _audit(db, user.id, "promotion.revert", rev, {"reverts": promo.id, "reverts_number": promo.number})
    await db.commit()

    commits, errors = [], []
    author = await _author(user)
    committer = git_repo.Identity(wcfg.bot_name, wcfg.bot_email)
    for c in promo.commits:
        try:
            base, sha = await asyncio.to_thread(
                git_repo.revert_commits, c["repo_url"], c["branch"], wcfg.creds(), [c["sha"]],
                _trailer(rev.commit_message, rev), author, committer,
            )
            git_repo.forget_fetch(c["repo_url"], c["branch"])
            commits.append({**c, "base_sha": base, "sha": sha, "reverts": c["sha"]})
        except git_repo.GitError as e:
            errors.append(f"{git_repo._redact_url(c['repo_url'])} @ {c['branch']}: {e}")
    rev.commits = commits
    rev.error = "\n".join(errors) or None
    rev.updated_at = datetime.now(timezone.utc)
    if not commits:
        rev.status = "commit_failed"
        await _audit(db, user.id, "promotion.status", rev, {"status": "commit_failed", "error": rev.error})
        await db.commit()
        return rev
    reverted = {sid for c in commits for sid in c.get("stack_ids", [])}
    for pr in (await db.execute(select(PromotionRun).where(PromotionRun.promotion_id == rev.id))).scalars():
        if pr.target_stack_id not in reverted:
            await db.delete(pr)
            continue
        ws = await db.get(Workspace, pr.target_stack_id)
        run = await run_service.create_run(db, ws, command="apply", triggered_by=user.id,
                                           branch=ws.repo_ref, promotion_id=rev.id)
        pr.run_id = run.id
    rev.status = "running"
    await _audit(db, user.id, "promotion.status", rev, {"status": "running"})
    await db.commit()
    return rev


# ─── read models ─────────────────────────────────────────────────────────────

_STAGE_STEPS = {
    "checkov": "Checkov Security Scan",
    "plan": "Terraform Plan",
    "opa": "OPA Policy Check",
    "cost": "Cost Estimation",
    "approval": "Awaiting Approval",
    "apply": "Terraform Apply",
}


def _json(raw) -> Optional[dict]:
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def _cost(summary: Optional[dict]) -> Optional[str]:
    if not summary:
        return None
    diff = summary.get("diffTotalMonthlyCost")
    cur = summary.get("currency") or "USD"
    return f"{diff} {cur}/mo" if diff not in (None, "") else None


async def _stages(db: AsyncSession, pr: PromotionRun, run: Optional[Run], promo: Promotion) -> list[dict]:
    committed = promo.status not in ("pending", "committing", "commit_failed")
    stages = [{"key": "committed", "label": "Committed", "status": "success" if committed else
               ("failed" if promo.status == "commit_failed" else "pending"), "detail": None}]
    steps: dict[str, RunStep] = {}
    if run is not None:
        for s in (await db.execute(select(RunStep).where(RunStep.run_id == run.id))).scalars():
            steps[s.name] = s
    approver = None
    if run is not None:
        a = (await db.execute(
            select(AuditLog, User.email).join(User, User.id == AuditLog.user_id, isouter=True)
            .where(AuditLog.resource_id == run.id, AuditLog.action.in_(("approve", "reject")))
            .order_by(AuditLog.created_at.desc())
        )).first()
        if a is not None:
            approver = {"action": a[0].action, "by": a[1]}
    for key, name in _STAGE_STEPS.items():
        st = steps.get(name)
        status_ = (st.status.value if hasattr(st.status, "value") else st.status) if st else "pending"
        detail = None
        sj = _json(st.summary_json) if st is not None else None
        if key == "plan" and sj:
            detail = f"+{sj.get('add', 0)} ~{sj.get('change', 0)} −{sj.get('destroy', 0)}"
        elif key == "cost":
            detail = _cost(sj)
        elif key == "approval" and approver:
            detail = f"{'approved' if approver['action'] == 'approve' else 'rejected'} by {approver['by'] or 'system'}"
            status_ = "success" if approver["action"] == "approve" else "failed"
        elif key == "opa" and run is not None:
            detail = run.policy_status if run.policy_status != "not_run" else None
        if run is not None and _s(run) == "cancelled" and status_ in ("pending", "running"):
            status_ = "skipped"
        stages.append({"key": key, "label": {"checkov": "Checkov", "plan": "Plan", "opa": "OPA", "cost": "Cost",
                                             "approval": "Approval", "apply": "Apply"}[key],
                       "status": status_, "detail": detail})
    ver = "pending"
    if pr.verified_at is not None:
        ver = "success" if (pr.residual or {}).get("in_sync") else "warning"
    stages.append({"key": "verified", "label": "Verified", "status": ver,
                   "detail": None if pr.residual is None else
                   ("in sync" if pr.residual.get("in_sync") else f"{pr.residual.get('promotable', 0)} difference(s) left")})
    return stages


async def to_dict(db: AsyncSession, promo: Promotion, *, detail: bool = False) -> dict:
    await refresh_status(db, promo)
    user = await db.get(User, promo.initiated_by)
    link = await db.get(EnvLink, promo.link_id)
    out = {
        "id": promo.id, "number": promo.number, "link_id": promo.link_id,
        "link_name": link.name if link else None, "kind": promo.kind,
        "reverts_promotion_id": promo.reverts_promotion_id, "direction": promo.direction,
        "initiated_by": promo.initiated_by, "initiated_by_email": user.email if user else None,
        "reason": promo.reason, "status": promo.status, "error": promo.error,
        "commit_message": promo.commit_message,
        "commits": [{**{k: v for k, v in c.items() if k != "repo_url"},
                     "repo_url": git_repo._redact_url(c["repo_url"]),
                     "web_url": _commit_web_url(c["repo_url"], c["sha"])} for c in (promo.commits or [])],
        "created_at": promo.created_at, "updated_at": promo.updated_at,
    }
    if detail:
        rows = await _rows(db, promo)
        stacks = []
        for pr, run in rows:
            ws = await db.get(Workspace, pr.target_stack_id)
            stacks.append({
                "promotion_run_id": pr.id, "pair_id": pr.pair_id, "target_stack_id": pr.target_stack_id,
                "target_stack_name": ws.name if ws else None,
                "target_path": ws.tf_working_dir if ws else None,
                "created_stack": pr.created_stack, "run_id": pr.run_id,
                "run_status": _s(run) if run else None,
                "residual": pr.residual, "verified_at": pr.verified_at,
                "stages": await _stages(db, pr, run, promo),
            })
        out["stacks"] = stacks
        out["selection"] = promo.selection
        reverted_by = (await db.execute(select(Promotion).where(
            Promotion.reverts_promotion_id == promo.id))).scalars().first()
        out["reverted_by"] = {"id": reverted_by.id, "number": reverted_by.number} if reverted_by else None
    return out


def _commit_web_url(repo_url: str, sha: str) -> Optional[str]:
    """https://host/owner/repo/commit/<sha> for https forges (GitHub, Gitea, Forgejo)."""
    import urllib.parse

    p = urllib.parse.urlparse(repo_url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    path = p.path.removesuffix(".git").rstrip("/")
    port = f":{p.port}" if p.port else ""
    return f"{p.scheme}://{p.hostname}{port}{path}/commit/{sha}"
