"""Refresh the cloud_assets inventory from a detector drift report.

Each scan re-reports the full asset set for one workspace (managed: codified /
drifted / ghost) plus the live resources found in that account/region that are
in no state (unmanaged). We replace this workspace's prior rows and prune stale
account-scoped unmanaged rows, then insert the fresh set — keyed by
(business_unit_id, asset_id) so re-scans converge instead of duplicating.

Limitation: unmanaged detection is per-workspace-account. A resource managed by
a *sibling* workspace in the same account would otherwise look unmanaged here;
we exclude any asset already codified/drifted/ghost elsewhere in the BU. Across
scan passes this converges; within a single pass ordering can briefly mislabel.
"""
from __future__ import annotations

import fnmatch
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cloud_asset import CloudAsset, MANAGED_STATES
from app.models.inventory_ignore_rule import InventoryIgnoreRule

logger = logging.getLogger(__name__)


def _matches_any_rule(asset_id: str, asset_type: str, rules) -> bool:
    """True if the asset matches any ignore rule (arn_glob / asset_type)."""
    for r in rules:
        if r.match_type == "arn_glob" and fnmatch.fnmatch(asset_id or "", r.pattern):
            return True
        if r.match_type == "asset_type" and (asset_type or "") == r.pattern:
            return True
    return False


async def reapply_ignore_rules(db: AsyncSession, bu_id: str) -> int:
    """Reclassify existing non-managed assets to `ignored` per the BU's current
    rules. Called after a rule is added so it takes effect without waiting for
    the next collector scan. Returns the number of rows changed. Caller commits.
    """
    rules = (
        await db.execute(
            select(InventoryIgnoreRule).where(InventoryIgnoreRule.business_unit_id == bu_id)
        )
    ).scalars().all()
    if not rules:
        return 0
    rows = (
        await db.execute(
            select(CloudAsset).where(
                CloudAsset.business_unit_id == bu_id,
                CloudAsset.iac_status.not_in([*MANAGED_STATES, "ignored"]),
            )
        )
    ).scalars().all()
    changed = 0
    for row in rows:
        if _matches_any_rule(row.asset_id, row.asset_type, rules):
            row.iac_status = "ignored"
            changed += 1
    return changed


async def refresh_workspace_assets(db: AsyncSession, workspace, assets) -> None:
    """Replace `workspace`'s inventory rows with the freshly reported `assets`.

    `assets` is a list of AssetIn (pydantic). Commits are the caller's job.
    """
    bu = workspace.business_unit_id
    wid = workspace.id

    # Active ignore rules for this BU — a matching non-managed asset becomes
    # `ignored` at ingest (so it's out of "unmanaged" + the codification base).
    rules = (
        await db.execute(
            select(InventoryIgnoreRule).where(InventoryIgnoreRule.business_unit_id == bu)
        )
    ).scalars().all()

    # 1. Clear this workspace's prior managed/ghost rows.
    await db.execute(delete(CloudAsset).where(CloudAsset.workspace_id == wid))

    # 2. Prune stale account-scoped (non-managed) rows for the scanned accounts —
    #    unmanaged AND service_managed are re-reported fresh each scan.
    unmanaged_accounts = {
        a.account_id for a in assets if a.iac_status not in MANAGED_STATES and a.account_id
    }
    for acct in unmanaged_accounts:
        await db.execute(
            delete(CloudAsset).where(
                CloudAsset.workspace_id.is_(None),
                CloudAsset.business_unit_id == bu,
                CloudAsset.account_id == acct,
            )
        )

    # 3. Assets managed by a *sibling* workspace must not be re-flagged unmanaged.
    managed_elsewhere = set(
        (
            await db.execute(
                select(CloudAsset.asset_id).where(
                    CloudAsset.business_unit_id == bu,
                    CloudAsset.iac_status.in_(MANAGED_STATES),
                )
            )
        ).scalars().all()
    )

    # 4. De-dup the payload and decide what to insert.
    to_insert = []
    seen: set[str] = set()
    for a in assets:
        if not a.asset_id or a.asset_id in seen:
            continue
        is_managed = a.iac_status in MANAGED_STATES
        if a.asset_id in managed_elsewhere:
            # Already recorded as managed by a sibling workspace. For a
            # non-managed live hit that just means it isn't rogue. For a managed
            # asset it means two workspaces share an asset_id — inserting it
            # would violate the (business_unit_id, asset_id) unique key and 500
            # the whole report, silently dropping every other asset in this
            # workspace. Skip it so one duplicate can't sink the batch. (Root
            # cause was non-unique provider ids like random_password's "none",
            # now also filtered at the detector.)
            continue
        seen.add(a.asset_id)
        to_insert.append(a)

    # 5. Drop any surviving rows that collide on (bu, asset_id) — except those
    #    legitimately owned by a sibling as managed (left untouched).
    collide_ids = {a.asset_id for a in to_insert} - managed_elsewhere
    if collide_ids:
        await db.execute(
            delete(CloudAsset).where(
                CloudAsset.business_unit_id == bu,
                CloudAsset.asset_id.in_(collide_ids),
            )
        )

    for a in to_insert:
        is_managed = a.iac_status in MANAGED_STATES
        status = a.iac_status
        summary = a.drift_summary or None
        # A non-managed asset matching an ignore rule is suppressed → `ignored`.
        if not is_managed and _matches_any_rule(a.asset_id, a.asset_type, rules):
            status = "ignored"
            summary = summary or "matched an inventory ignore rule"
        db.add(
            CloudAsset(
                business_unit_id=bu,
                workspace_id=wid if is_managed else None,
                asset_id=a.asset_id,
                address=a.address or None,
                asset_type=a.asset_type,
                provider=a.provider,
                region=a.region,
                account_id=a.account_id,
                iac_status=status,
                drift_summary=summary,
            )
        )
