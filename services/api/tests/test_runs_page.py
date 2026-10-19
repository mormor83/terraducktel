"""`GET /runs/page` — the Runs page's paged list.

The page used to pull every run in the BU (with megabytes of plan text each)
and bucket it client-side; this endpoint pages server-side and returns the
total + per-status counts the UI needs for its pager and status pills.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.usefixtures("default_aws_account")

from app.models.business_unit import DEFAULT_BU_ID


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _seed(factory, statuses, *, ws_name="vpc", plan_output=None):
    """One workspace with a run per status, newest LAST in `statuses`.

    Returns (ws_id, run_ids newest-first).
    """
    from app.models.run import Run, RunStatus
    from app.models.workspace import Workspace

    ws_id = str(uuid.uuid4())
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ids = []
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID, id=ws_id, name=ws_name,
            aws_account_id="123456789012", region="us-east-1", environment="dev",
            tf_working_dir=f"account-123/us-east-1/{ws_name}",
            repo_url="https://example.com/x.git",
        ))
        for i, st in enumerate(statuses):
            rid = str(uuid.uuid4())
            ids.append(rid)
            session.add(Run(
                id=rid, workspace_id=ws_id, command="plan",
                status=RunStatus(st), created_at=t0 + timedelta(minutes=i),
                plan_output=plan_output,
            ))
        await session.commit()
    return ws_id, list(reversed(ids))


@pytest.mark.asyncio
async def test_pages_newest_first_with_total(auth_client, admin_token, _setup_db):
    _, ids = await _seed(_setup_db, ["applied"] * 7)
    seen = []
    for offset in (0, 3, 6):
        resp = await auth_client.get(
            f"/api/v1/runs/page?offset={offset}&limit=3", headers=_h(admin_token)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 7
        assert body["offset"] == offset and body["limit"] == 3
        seen += [r["id"] for r in body["items"]]
    assert seen == ids


@pytest.mark.asyncio
async def test_items_omit_plan_output(auth_client, admin_token, _setup_db):
    await _seed(_setup_db, ["planned"], plan_output="x" * 10_000)
    body = (await auth_client.get("/api/v1/runs/page", headers=_h(admin_token))).json()
    assert len(body["items"]) == 1
    assert "plan_output" not in body["items"][0]


@pytest.mark.asyncio
async def test_status_filter_narrows_items_but_not_counts(
    auth_client, admin_token, _setup_db
):
    await _seed(_setup_db, ["applied", "failed", "failed", "cancelled"])
    resp = await auth_client.get(
        "/api/v1/runs/page?status=failed,cancelled", headers=_h(admin_token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert {r["status"] for r in body["items"]} == {"failed", "cancelled"}
    # Pills still show what every status would yield.
    assert body["status_counts"] == {"applied": 1, "failed": 2, "cancelled": 1}


@pytest.mark.asyncio
async def test_unknown_status_is_400(auth_client, admin_token, _setup_db):
    resp = await auth_client.get("/api/v1/runs/page?status=nope", headers=_h(admin_token))
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_search_matches_workspace_name_and_user_email(
    auth_client, admin_token, _setup_db
):
    from app.models.run import Run
    from app.models.user import User
    from sqlalchemy import select

    ws_vpc, _ = await _seed(_setup_db, ["applied", "failed"], ws_name="vpc")
    _, rds_ids = await _seed(_setup_db, ["applied"], ws_name="rds")

    resp = await auth_client.get("/api/v1/runs/page?q=VPC", headers=_h(admin_token))
    body = resp.json()
    assert body["total"] == 2
    assert {r["workspace_id"] for r in body["items"]} == {ws_vpc}

    async with _setup_db() as session:
        admin = (await session.execute(
            select(User).where(User.email == "admin@test.com")
        )).scalar_one()
        run = await session.get(Run, rds_ids[0])
        run.triggered_by = admin.id
        await session.commit()

    resp = await auth_client.get(
        "/api/v1/runs/page?q=admin@test", headers=_h(admin_token)
    )
    assert [r["id"] for r in resp.json()["items"]] == [rds_ids[0]]


@pytest.mark.asyncio
async def test_search_treats_wildcards_literally(auth_client, admin_token, _setup_db):
    await _seed(_setup_db, ["applied"], ws_name="vpc")
    resp = await auth_client.get("/api/v1/runs/page?q=%25", headers=_h(admin_token))
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_limit_is_range_checked(auth_client, admin_token, _setup_db):
    for bad in ("limit=0", "limit=201", "offset=-1"):
        resp = await auth_client.get(f"/api/v1/runs/page?{bad}", headers=_h(admin_token))
        assert resp.status_code == 422, f"{bad} -> {resp.status_code}"


@pytest.mark.asyncio
async def test_bu_scoping(auth_client, viewer_token, _setup_db):
    from app.models.business_unit import BusinessUnit
    from app.models.run import Run, RunStatus
    from app.models.workspace import Workspace

    await _seed(_setup_db, ["applied"])
    other_bu, foreign_ws = str(uuid.uuid4()), str(uuid.uuid4())
    async with _setup_db() as session:
        session.add(BusinessUnit(id=other_bu, slug="other", name="Other"))
        session.add(Workspace(
            business_unit_id=other_bu, id=foreign_ws, name="secret",
            aws_account_id="123456789012", region="us-east-1", environment="prod",
            tf_working_dir="a/b/secret", repo_url="https://example.com/y.git",
        ))
        session.add(Run(
            id=str(uuid.uuid4()), workspace_id=foreign_ws,
            command="apply", status=RunStatus.FAILED,
        ))
        await session.commit()

    resp = await auth_client.get(
        "/api/v1/runs/page?q=secret",
        headers={**_h(viewer_token), "X-Business-Unit": "default"},
    )
    assert resp.json()["total"] == 0
    resp = await auth_client.get(
        "/api/v1/runs/page",
        headers={**_h(viewer_token), "X-Business-Unit": "default"},
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["status_counts"] == {"applied": 1}
