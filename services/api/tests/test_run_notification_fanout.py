"""Both notification channels fire on a run transition, independently.

A Telegram outage must not suppress the Slack message, and vice versa —
they are separate integrations and a team that configured both is relying
on each of them.

`runs.py` imports the senders inside the function body, so these tests patch
them on `app.services.notification_service`, not on the router module.
"""
import uuid

import pytest

from app.models.business_unit import DEFAULT_BU_ID
from app.services import notification_service as ns

# asyncio_mode = "auto" in pyproject.toml, so no asyncio mark is needed.
pytestmark = pytest.mark.usefixtures("default_aws_account")


async def _make_run(factory, status):
    from app.models.run import Run, RunStatus
    from app.models.workspace import Workspace

    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID,
            id=ws_id,
            name=f"fanout-{ws_id[:8]}",
            repo_url="https://example.com/repo.git",
            tf_working_dir=".",
            aws_account_id="123456789012",
            environment="dev",
        ))
        session.add(Run(
            id=run_id, workspace_id=ws_id, command="plan",
            status=status, plan_output="example plan",
        ))
        await session.commit()
    return run_id


@pytest.fixture
def spy_senders(monkeypatch):
    """Replace both channels' senders with recorders."""
    calls = {"slack": [], "telegram": []}

    def _rec(channel, kind):
        async def _fn(session, **kw):
            calls[channel].append(kind)
        return _fn

    for kind in ("run_auto_approved", "run_awaiting_approval", "run_failed"):
        monkeypatch.setattr(ns, f"send_slack_{kind}", _rec("slack", kind))
        monkeypatch.setattr(ns, f"send_telegram_{kind}", _rec("telegram", kind))
    return calls


async def test_awaiting_approval_reaches_both_channels(
    auth_client, operator_token, _setup_db, spy_senders
):
    from app.models.run import RunStatus

    run_id = await _make_run(_setup_db, RunStatus.PLANNED)
    r = await auth_client.patch(
        f"/api/v1/runs/{run_id}",
        json={"status": "awaiting_approval"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert r.status_code == 200
    assert spy_senders["slack"] == ["run_awaiting_approval"]
    assert spy_senders["telegram"] == ["run_awaiting_approval"]


async def test_failed_reaches_both_channels(
    auth_client, operator_token, _setup_db, spy_senders
):
    from app.models.run import RunStatus

    run_id = await _make_run(_setup_db, RunStatus.RUNNING)
    r = await auth_client.patch(
        f"/api/v1/runs/{run_id}",
        json={"status": "failed"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert r.status_code == 200
    assert spy_senders["slack"] == ["run_failed"]
    assert spy_senders["telegram"] == ["run_failed"]


async def test_telegram_failure_does_not_suppress_slack(
    auth_client, operator_token, _setup_db, monkeypatch
):
    """The whole point of separate try/except blocks per channel.

    Note the inherent limit of this direction: because `senders` dispatches
    Slack before Telegram for every kind, `slack_calls == [run_id]` alone
    would also pass under a single `try` wrapping both calls, or even if the
    Telegram dispatch were deleted outright. Recording the Telegram attempt
    *before* raising — and asserting on it too — at least catches the
    "Telegram dispatch removed" regression. Proving the two are in genuinely
    separate try/except blocks (not just "Telegram happens to run second and
    its failure doesn't matter because nothing after it depends on it") is
    what `test_slack_failure_does_not_suppress_telegram` below is for: since
    Slack runs first, that test fails under a shared `try` (the exception
    from Slack would prevent Telegram from ever running).
    """
    from app.models.run import RunStatus

    slack_calls = []
    telegram_attempted = []

    async def _slack(session, **kw):
        slack_calls.append(kw["run_id"])

    async def _telegram_boom(session, **kw):
        telegram_attempted.append(kw["run_id"])
        raise RuntimeError("telegram exploded")

    monkeypatch.setattr(ns, "send_slack_run_failed", _slack)
    monkeypatch.setattr(ns, "send_telegram_run_failed", _telegram_boom)

    run_id = await _make_run(_setup_db, RunStatus.RUNNING)
    r = await auth_client.patch(
        f"/api/v1/runs/{run_id}",
        json={"status": "failed"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert r.status_code == 200
    assert slack_calls == [run_id]
    assert telegram_attempted == [run_id]


async def test_slack_failure_does_not_suppress_telegram(
    auth_client, operator_token, _setup_db, monkeypatch
):
    from app.models.run import RunStatus

    telegram_calls = []

    async def _slack_boom(session, **kw):
        raise RuntimeError("slack exploded")

    async def _telegram(session, **kw):
        telegram_calls.append(kw["run_id"])

    monkeypatch.setattr(ns, "send_slack_run_failed", _slack_boom)
    monkeypatch.setattr(ns, "send_telegram_run_failed", _telegram)

    run_id = await _make_run(_setup_db, RunStatus.RUNNING)
    r = await auth_client.patch(
        f"/api/v1/runs/{run_id}",
        json={"status": "failed"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert r.status_code == 200
    assert telegram_calls == [run_id]


async def test_internal_drift_report_reaches_both_channels(
    auth_client, _setup_db, monkeypatch
):
    """The /internal/ router authenticates with TERRADUCKTEL_INTERNAL_TOKEN,
    NOT a user JWT — `tests/conftest.py` sets it to the constant below, and
    `DriftReportIn` requires `workspace_id` in the body to match the path."""
    from app.models.workspace import Workspace

    calls = []

    async def _slack(session, **kw):
        calls.append("slack")

    async def _telegram(session, **kw):
        calls.append("telegram")

    monkeypatch.setattr(ns, "send_slack_drift_detected", _slack)
    monkeypatch.setattr(ns, "send_telegram_drift_detected", _telegram)

    ws_id = str(uuid.uuid4())
    async with _setup_db() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID, id=ws_id, name=f"drift-{ws_id[:8]}",
            repo_url="https://example.com/repo.git", tf_working_dir=".",
            aws_account_id="123456789012", environment="dev",
        ))
        await session.commit()

    r = await auth_client.post(
        f"/api/v1/internal/drift/{ws_id}/report",
        json={"workspace_id": ws_id, "has_drift": True, "summary": "1 changed"},
        headers={
            "X-Terraducktel-Internal-Token": "test-internal-token-do-not-use-in-prod"
        },
    )
    assert r.status_code == 200
    assert sorted(calls) == ["slack", "telegram"]


async def _post_drift_report(auth_client, _setup_db, ws_id=None):
    from app.models.workspace import Workspace

    ws_id = ws_id or str(uuid.uuid4())
    async with _setup_db() as session:
        session.add(Workspace(
            business_unit_id=DEFAULT_BU_ID, id=ws_id, name=f"drift-{ws_id[:8]}",
            repo_url="https://example.com/repo.git", tf_working_dir=".",
            aws_account_id="123456789012", environment="dev",
        ))
        await session.commit()

    return await auth_client.post(
        f"/api/v1/internal/drift/{ws_id}/report",
        json={"workspace_id": ws_id, "has_drift": True, "summary": "1 changed"},
        headers={
            "X-Terraducktel-Internal-Token": "test-internal-token-do-not-use-in-prod"
        },
    )


async def test_internal_drift_telegram_failure_does_not_suppress_slack(
    auth_client, _setup_db, monkeypatch
):
    """Mirrors test_telegram_failure_does_not_suppress_slack for the drift
    path in internal.py — also the regression test for the Finding-1 rollback
    fix: a DB-level failure in one channel must not leave the shared session
    dirty in a way that blocks (or falsely fails) the other channel."""
    slack_calls = []
    telegram_attempted = []

    async def _slack(session, **kw):
        slack_calls.append("slack")

    async def _telegram_boom(session, **kw):
        telegram_attempted.append("telegram")
        raise RuntimeError("telegram exploded")

    monkeypatch.setattr(ns, "send_slack_drift_detected", _slack)
    monkeypatch.setattr(ns, "send_telegram_drift_detected", _telegram_boom)

    r = await _post_drift_report(auth_client, _setup_db)
    assert r.status_code == 200
    assert slack_calls == ["slack"]
    assert telegram_attempted == ["telegram"]


async def test_internal_drift_slack_failure_does_not_suppress_telegram(
    auth_client, _setup_db, monkeypatch
):
    """Mirrors test_slack_failure_does_not_suppress_telegram for the drift
    path: Slack is dispatched first, so this is the direction that actually
    proves the two channels run under separate try/except blocks rather than
    one shared block."""
    telegram_calls = []

    async def _slack_boom(session, **kw):
        raise RuntimeError("slack exploded")

    async def _telegram(session, **kw):
        telegram_calls.append("telegram")

    monkeypatch.setattr(ns, "send_slack_drift_detected", _slack_boom)
    monkeypatch.setattr(ns, "send_telegram_drift_detected", _telegram)

    r = await _post_drift_report(auth_client, _setup_db)
    assert r.status_code == 200
    assert telegram_calls == ["telegram"]
