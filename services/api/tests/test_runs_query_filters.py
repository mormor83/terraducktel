"""Server-side narrowing for `GET /runs` and `GET /runs/{id}/steps`.

These exist so a CLI can ask "the last N failed runs for this workspace", and
poll a long apply's timeline, without pulling every run in the BU (or every
step's multi-megabyte `output`) on each request.
"""
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("default_aws_account")

from app.models.business_unit import DEFAULT_BU_ID


async def _seed(factory, *, statuses):
    """Create two workspaces; put `statuses` on runs of the first, one on the second.

    Returns (ws_a, ws_b). Runs are inserted oldest-first so `created_at desc`
    ordering is observable.
    """
    from app.models.run import Run, RunStatus
    from app.models.workspace import Workspace

    ws_a, ws_b = str(uuid.uuid4()), str(uuid.uuid4())
    async with factory() as session:
        for ws_id, name in ((ws_a, "vpc"), (ws_b, "rds")):
            session.add(Workspace(
                business_unit_id=DEFAULT_BU_ID,
                id=ws_id, name=name,
                aws_account_id="123456789012", region="us-east-1",
                environment="dev",
                tf_working_dir=f"account-123/us-east-1/{name}",
                repo_url="https://example.com/x.git",
            ))
        for st in statuses:
            session.add(Run(
                id=str(uuid.uuid4()), workspace_id=ws_a,
                command="plan", status=RunStatus(st),
            ))
        session.add(Run(
            id=str(uuid.uuid4()), workspace_id=ws_b,
            command="apply", status=RunStatus.APPLIED,
        ))
        await session.commit()
    return ws_a, ws_b


def _h(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_no_params_still_returns_everything(auth_client, admin_token, _setup_db):
    """Default is unbounded — the UI's Runs page buckets the full list itself."""
    await _seed(_setup_db, statuses=["applied", "failed", "planned"])
    resp = await auth_client.get("/api/v1/runs", headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 4


@pytest.mark.asyncio
async def test_filter_by_workspace_id(auth_client, admin_token, _setup_db):
    ws_a, ws_b = await _seed(_setup_db, statuses=["applied", "failed"])
    resp = await auth_client.get(
        f"/api/v1/runs?workspace_id={ws_a}", headers=_h(admin_token)
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 2
    assert {r["workspace_id"] for r in rows} == {ws_a}


@pytest.mark.asyncio
async def test_filter_by_single_status(auth_client, admin_token, _setup_db):
    await _seed(_setup_db, statuses=["applied", "failed", "planned"])
    resp = await auth_client.get("/api/v1/runs?status=failed", headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_filter_by_comma_separated_statuses(auth_client, admin_token, _setup_db):
    """`?status=failed,cancelled` — "show me the terminal-bad runs" in one call."""
    await _seed(_setup_db, statuses=["applied", "failed", "cancelled", "planned"])
    resp = await auth_client.get(
        "/api/v1/runs?status=failed,cancelled", headers=_h(admin_token)
    )
    assert resp.status_code == 200, resp.text
    assert {r["status"] for r in resp.json()} == {"failed", "cancelled"}


@pytest.mark.asyncio
async def test_unknown_status_is_a_400_listing_valid_values(
    auth_client, admin_token, _setup_db
):
    """A CLI typo must fail loudly, not silently return an empty list."""
    await _seed(_setup_db, statuses=["applied"])
    resp = await auth_client.get("/api/v1/runs?status=erroed", headers=_h(admin_token))
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "erroed" in detail
    assert "awaiting_approval" in detail  # the valid-values list is included


@pytest.mark.asyncio
async def test_limit_caps_and_keeps_newest_first(auth_client, admin_token, _setup_db):
    await _seed(_setup_db, statuses=["applied", "failed", "planned"])
    unbounded = await auth_client.get("/api/v1/runs", headers=_h(admin_token))
    resp = await auth_client.get("/api/v1/runs?limit=2", headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 2
    assert [r["id"] for r in rows] == [r["id"] for r in unbounded.json()[:2]]


@pytest.mark.asyncio
async def test_limit_is_range_checked(auth_client, admin_token, _setup_db):
    await _seed(_setup_db, statuses=["applied"])
    for bad in ("0", "-1", "1001"):
        resp = await auth_client.get(
            f"/api/v1/runs?limit={bad}", headers=_h(admin_token)
        )
        assert resp.status_code == 422, f"limit={bad} -> {resp.status_code}"


@pytest.mark.asyncio
async def test_filters_compose(auth_client, admin_token, _setup_db):
    ws_a, _ = await _seed(_setup_db, statuses=["failed", "failed", "applied"])
    resp = await auth_client.get(
        f"/api/v1/runs?workspace_id={ws_a}&status=failed&limit=1",
        headers=_h(admin_token),
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["workspace_id"] == ws_a
    assert rows[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_workspace_filter_cannot_peek_across_bus(
    auth_client, viewer_token, _setup_db
):
    """`workspace_id` narrows *within* the BU scope; it never widens it."""
    from app.models.business_unit import BusinessUnit
    from app.models.run import Run, RunStatus
    from app.models.workspace import Workspace

    other_bu = str(uuid.uuid4())
    foreign_ws = str(uuid.uuid4())
    factory = _setup_db
    async with factory() as session:
        session.add(BusinessUnit(id=other_bu, slug="other", name="Other"))
        session.add(Workspace(
            business_unit_id=other_bu, id=foreign_ws, name="secret",
            aws_account_id="123456789012", region="us-east-1", environment="prod",
            tf_working_dir="a/b/secret", repo_url="https://example.com/y.git",
        ))
        session.add(Run(
            id=str(uuid.uuid4()), workspace_id=foreign_ws,
            command="apply", status=RunStatus.APPLIED,
        ))
        await session.commit()

    resp = await auth_client.get(
        f"/api/v1/runs?workspace_id={foreign_ws}",
        headers={**_h(viewer_token), "X-Business-Unit": "default"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


# ─── steps: since + include_output ─────────────────────────────────────────


async def _seed_run_with_steps(factory):
    from app.models.run import Run, RunStatus
    from app.models.run_step import RunStep, StepStatus
    from app.models.workspace import Workspace

    ws_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID, id=ws_id, name="vpc",
            aws_account_id="123456789012", region="us-east-1", environment="dev",
            tf_working_dir="account-123/us-east-1/vpc",
            repo_url="https://example.com/x.git",
        ))
        session.add(Run(
            id=run_id, workspace_id=ws_id, command="apply",
            status=RunStatus.APPLYING,
        ))
        for pos, (name, st) in enumerate([
            ("Git Clone", StepStatus.SUCCESS),
            ("Terraform Init", StepStatus.SUCCESS),
            ("Terraform Plan", StepStatus.RUNNING),
            ("Terraform Apply", StepStatus.PENDING),
        ]):
            session.add(RunStep(
                id=str(uuid.uuid4()), run_id=run_id, position=pos,
                name=name, status=st, output=f"{name} log blob",
            ))
        await session.commit()
    return run_id


@pytest.mark.asyncio
async def test_steps_default_unchanged(auth_client, admin_token, _setup_db):
    """No params → full timeline with output, exactly as the executor expects."""
    run_id = await _seed_run_with_steps(_setup_db)
    resp = await auth_client.get(
        f"/api/v1/runs/{run_id}/steps", headers=_h(admin_token)
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["position"] for r in rows] == [0, 1, 2, 3]
    assert all(r["output"] for r in rows)


@pytest.mark.asyncio
async def test_steps_since_drops_the_finished_prefix(
    auth_client, admin_token, _setup_db
):
    run_id = await _seed_run_with_steps(_setup_db)
    resp = await auth_client.get(
        f"/api/v1/runs/{run_id}/steps?since=2", headers=_h(admin_token)
    )
    assert resp.status_code == 200, resp.text
    assert [r["position"] for r in resp.json()] == [2, 3]


@pytest.mark.asyncio
async def test_steps_since_past_the_end_is_empty_not_an_error(
    auth_client, admin_token, _setup_db
):
    run_id = await _seed_run_with_steps(_setup_db)
    resp = await auth_client.get(
        f"/api/v1/runs/{run_id}/steps?since=99", headers=_h(admin_token)
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_steps_include_output_false_omits_blobs_only(
    auth_client, admin_token, _setup_db
):
    """The cheap timeline poll: statuses kept, `output` dropped."""
    run_id = await _seed_run_with_steps(_setup_db)
    resp = await auth_client.get(
        f"/api/v1/runs/{run_id}/steps?include_output=false", headers=_h(admin_token)
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 4
    assert all(r["output"] is None for r in rows)
    assert [r["status"] for r in rows] == ["success", "success", "running", "pending"]
    assert all(r["name"] for r in rows)


@pytest.mark.asyncio
async def test_steps_negative_since_is_rejected(auth_client, admin_token, _setup_db):
    run_id = await _seed_run_with_steps(_setup_db)
    resp = await auth_client.get(
        f"/api/v1/runs/{run_id}/steps?since=-1", headers=_h(admin_token)
    )
    assert resp.status_code == 422
