"""drift_reports retention + bounded latest-per-workspace reads.

Background (2026-09-15, prod): every detector scan INSERTed one drift_reports
row per workspace and nothing ever deleted them. The table reached 1,078,822
rows / 121 GB (~1 GB/day, ~5,440 rows per workspace) on a 148 GB volume with a
200 GB autoscale ceiling, and `_latest_reports_by_workspace` loaded EVERY row
for the requested workspaces just to pick the newest. These tests pin the two
fixes: a batched keep-newest-N pruner, and a query that is O(workspaces), not
O(history). Both are exercised on the SQLite test dialect, so the SQL they use
must stay portable (window functions, IN-subquery — no DISTINCT ON).
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, inspect, select

from app.models.business_unit import BusinessUnit
from app.models.drift_report import DriftReport
from app.models.workspace import Workspace
from app.routers.drift import _latest_reports_by_workspace
from app.services import run_worker
from tests.test_run_worker import _SessionFactoryFromSession


async def _make_workspace(session) -> Workspace:
    bu = BusinessUnit(id=str(uuid.uuid4()), slug=f"bu-{uuid.uuid4().hex[:8]}", name="Test BU")
    session.add(bu)
    await session.flush()
    ws = Workspace(
        id=str(uuid.uuid4()),
        business_unit_id=bu.id,
        name=f"ws-{uuid.uuid4().hex[:6]}",
        aws_account_id="123456789012",
        environment="dev",
        region="us-east-1",
        repo_ref="main",
    )
    session.add(ws)
    await session.flush()
    return ws


async def _seed_reports(session, ws: Workspace, n: int, *, start: datetime) -> list[str]:
    """Insert `n` reports one minute apart, oldest first. Returns ids oldest→newest.
    `untracked_count` == index so a test can tell which rows survived."""
    ids: list[str] = []
    for i in range(n):
        rid = str(uuid.uuid4())
        session.add(
            DriftReport(
                id=rid,
                workspace_id=ws.id,
                has_drift=i % 2 == 1,
                summary=f"report {i}",
                untracked_count=i,
                resources=[{"address": f"aws_thing.r{i}", "type": "aws_thing"}],
                detected_at=start + timedelta(minutes=i),
            )
        )
        ids.append(rid)
    await session.commit()
    return ids


async def _count(session, ws_id: str) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(DriftReport).where(DriftReport.workspace_id == ws_id)
        )
    ).scalar_one()


T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


# ─── _prune_drift_reports ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prune_keeps_newest_n_per_workspace(db_session):
    """Two workspaces, 10 reports each, keep=3 → exactly the 3 newest survive
    in EACH workspace; the partition is per workspace, not global."""
    factory = _SessionFactoryFromSession(db_session)
    a = await _make_workspace(db_session)
    b = await _make_workspace(db_session)
    a_ids = await _seed_reports(db_session, a, 10, start=T0)
    b_ids = await _seed_reports(db_session, b, 10, start=T0 + timedelta(days=1))

    deleted = await run_worker._prune_drift_reports(factory, keep=3, batch=1000)
    assert deleted == 14  # (10 - 3) * 2

    assert await _count(db_session, a.id) == 3
    assert await _count(db_session, b.id) == 3
    survivors = set(
        (await db_session.execute(select(DriftReport.id))).scalars().all()
    )
    assert survivors == set(a_ids[-3:]) | set(b_ids[-3:])


@pytest.mark.asyncio
async def test_prune_is_bounded_by_batch_and_drains_on_repeat(db_session):
    """One call deletes at most `batch` rows (one bounded transaction); calling
    until it returns < batch drains the backlog — the loop's contract."""
    factory = _SessionFactoryFromSession(db_session)
    ws = await _make_workspace(db_session)
    await _seed_reports(db_session, ws, 12, start=T0)

    first = await run_worker._prune_drift_reports(factory, keep=2, batch=4)
    assert first == 4
    assert await _count(db_session, ws.id) == 8

    second = await run_worker._prune_drift_reports(factory, keep=2, batch=4)
    third = await run_worker._prune_drift_reports(factory, keep=2, batch=4)
    assert (first, second, third) == (4, 4, 2)
    assert await _count(db_session, ws.id) == 2

    assert await run_worker._prune_drift_reports(factory, keep=2, batch=4) == 0


@pytest.mark.asyncio
async def test_prune_never_deletes_the_newest_report(db_session):
    """keep=0 is clamped to 1: retention must never leave a workspace with no
    report at all, or drift_status has nothing to stand on."""
    factory = _SessionFactoryFromSession(db_session)
    ws = await _make_workspace(db_session)
    ids = await _seed_reports(db_session, ws, 5, start=T0)

    await run_worker._prune_drift_reports(factory, keep=0, batch=1000)

    remaining = (await db_session.execute(select(DriftReport.id))).scalars().all()
    assert remaining == [ids[-1]]


@pytest.mark.asyncio
async def test_prune_noop_when_under_threshold(db_session):
    factory = _SessionFactoryFromSession(db_session)
    ws = await _make_workspace(db_session)
    await _seed_reports(db_session, ws, 2, start=T0)

    assert await run_worker._prune_drift_reports(factory, keep=3, batch=1000) == 0
    assert await _count(db_session, ws.id) == 2


# ─── _latest_reports_by_workspace ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_latest_returns_exactly_one_newest_row_per_workspace(db_session):
    a = await _make_workspace(db_session)
    b = await _make_workspace(db_session)
    a_ids = await _seed_reports(db_session, a, 6, start=T0)
    b_ids = await _seed_reports(db_session, b, 3, start=T0)

    latest = await _latest_reports_by_workspace(db_session, [a.id, b.id])

    assert set(latest) == {a.id, b.id}
    assert latest[a.id].id == a_ids[-1] and latest[a.id].untracked_count == 5
    assert latest[b.id].id == b_ids[-1] and latest[b.id].untracked_count == 2


@pytest.mark.asyncio
async def test_latest_omits_workspaces_with_no_reports_and_handles_empty(db_session):
    a = await _make_workspace(db_session)
    empty = await _make_workspace(db_session)
    await _seed_reports(db_session, a, 1, start=T0)

    latest = await _latest_reports_by_workspace(db_session, [a.id, empty.id])
    assert set(latest) == {a.id}
    assert await _latest_reports_by_workspace(db_session, []) == {}


@pytest.mark.asyncio
async def test_latest_without_resources_still_carries_counts(db_session):
    """The summary path defers the per-resource JSON (the bulk of each row).
    Counts and status must be intact, and `resources` must be genuinely NOT
    loaded — that is the whole point. Note: touching a deferred column on an
    async session raises MissingGreenlet (no implicit lazy loads in asyncio),
    which is exactly why the summary route must never reference it; the
    end-to-end `/summary` test in test_drift_detection.py guards that."""
    ws = await _make_workspace(db_session)
    ids = await _seed_reports(db_session, ws, 4, start=T0)

    latest = await _latest_reports_by_workspace(db_session, [ws.id], with_resources=False)
    rep = latest[ws.id]
    assert rep.id == ids[-1]
    assert rep.untracked_count == 3
    assert rep.has_drift is True
    assert "resources" in inspect(rep).unloaded, "resources should be deferred, not fetched"

    # And the default (detail path) does load it.
    full = await _latest_reports_by_workspace(db_session, [ws.id])
    assert "resources" not in inspect(full[ws.id]).unloaded
    assert full[ws.id].resources[0]["address"] == "aws_thing.r3"
