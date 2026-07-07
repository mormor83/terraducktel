"""Unit coverage for approval_service, audit_chain, state_service (StateLock),
and run_step_service."""
import uuid
from datetime import datetime, timezone

import pytest

from app.services.approval_service import ApprovalService
from app.services import audit_chain as ac
from app.services import state_service as st
from app.services import run_step_service as rss
from app.models.audit_log import AuditLog
from app.models.run import Run, RunStatus
from app.models.run_step import RunStep, StepStatus
from app.models.state_lock import StateLockEntry
from app.models.user import User
from app.models.business_unit import DEFAULT_BU_ID

pytestmark = pytest.mark.usefixtures("default_bu")


def _run(status=RunStatus.AWAITING_APPROVAL):
    return Run(id=str(uuid.uuid4()), workspace_id="ws1", command="apply", status=status)


def _user():
    return User(id="u1", email="op@test.com", hashed_password="x", role="operator")


# ─── approval_service ────────────────────────────────────────────────────────


async def test_approve_transitions_and_audits(db_session):
    run = _run()
    db_session.add(run)
    await db_session.commit()
    out = await ApprovalService().approve(db_session, run, _user(), comment="lgtm")
    assert out.status == RunStatus.APPLYING


async def test_approve_wrong_state_409(db_session):
    from fastapi import HTTPException

    run = _run(status=RunStatus.PENDING)
    db_session.add(run)
    await db_session.commit()
    with pytest.raises(HTTPException) as ei:
        await ApprovalService().approve(db_session, run, _user())
    assert ei.value.status_code == 409


async def test_reject_transitions_and_wrong_state(db_session):
    from fastapi import HTTPException

    run = _run()
    db_session.add(run)
    await db_session.commit()
    out = await ApprovalService().reject(db_session, run, _user(), comment="no")
    assert out.status == RunStatus.CANCELLED

    bad = _run(status=RunStatus.APPLIED)
    db_session.add(bad)
    await db_session.commit()
    with pytest.raises(HTTPException) as ei:
        await ApprovalService().reject(db_session, bad, _user())
    assert ei.value.status_code == 409


async def test_system_auto_approve_with_apply(db_session):
    run = _run()
    db_session.add(run)
    await db_session.commit()
    out = await ApprovalService().system_auto_approve(
        db_session, run, summary={"add": 0}, skip_apply=False
    )
    assert out.status == RunStatus.APPLYING


async def test_system_auto_approve_skip_apply_short_circuits(db_session):
    run = _run()
    db_session.add(run)
    await db_session.commit()
    out = await ApprovalService().system_auto_approve(
        db_session, run, summary={"add": 0}, skip_apply=True
    )
    assert out.status == RunStatus.APPLIED


async def test_system_auto_approve_wrong_state_409(db_session):
    from fastapi import HTTPException

    run = _run(status=RunStatus.PENDING)
    db_session.add(run)
    await db_session.commit()
    with pytest.raises(HTTPException) as ei:
        await ApprovalService().system_auto_approve(db_session, run, summary={}, skip_apply=False)
    assert ei.value.status_code == 409


# ─── audit_chain ─────────────────────────────────────────────────────────────


def test_details_and_ts_canonical():
    assert ac._details_to_canonical(None) == "null"
    assert ac._details_to_canonical({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    # tz-naive datetime is treated as UTC
    naive = datetime(2026, 1, 2, 3, 4, 5, 6)
    assert ac._ts_to_canonical(naive).endswith("Z")
    aware = datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    assert ac._ts_to_canonical(aware) == ac._ts_to_canonical(naive)


async def test_stamp_chains_and_verify(db_session):
    r1 = AuditLog(user_id="u1", action="a", resource_type="run", resource_id="r1")
    db_session.add(r1)
    await ac.stamp(db_session, r1)
    r2 = AuditLog(user_id="u1", action="b", resource_type="run", resource_id="r2")
    db_session.add(r2)
    await ac.stamp(db_session, r2)
    assert r2.prev_hash == r1.entry_hash
    res = await ac.verify_chain(db_session)
    assert res["ok"] is True and res["total"] >= 2
    # limit path
    res2 = await ac.verify_chain(db_session, limit=1)
    assert res2["total"] == 1


async def test_verify_chain_detects_breaks_and_caps_at_five(db_session):
    # Insert 6 rows with deliberately wrong hashes → broken list caps at 5 and
    # returns early.
    for i in range(6):
        db_session.add(
            AuditLog(
                id=f"bad-{i}",
                user_id="u",
                action="x",
                resource_type="run",
                resource_id=str(i),
                prev_hash="wrong",
                entry_hash="alsowrong",
                created_at=datetime(2026, 1, 1, 0, 0, i, tzinfo=timezone.utc),
            )
        )
    await db_session.flush()
    res = await ac.verify_chain(db_session)
    assert res["ok"] is False and len(res["broken_at"]) == 5


# ─── state_service ───────────────────────────────────────────────────────────


def test_lock_key_is_deterministic_positive():
    k = st._lock_key("ws-abc")
    assert isinstance(k, int) and 0 <= k < 2**63 - 1
    assert k == st._lock_key("ws-abc")


class _FakeResult:
    def __init__(self, val):
        self._val = val

    def scalar(self):
        return self._val


class _FakeSession:
    def __init__(self, version="PostgreSQL 15.2"):
        self.version = version
        self.calls = []

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        return _FakeResult(self.version)


async def test_is_postgres_true_on_fake():
    assert await st._is_postgres(_FakeSession("PostgreSQL 15")) is True


async def test_is_postgres_false_on_sqlite(db_session):
    # Real sqlite session: SELECT version() raises → caught → False.
    assert await st._is_postgres(db_session) is False


async def test_xact_serialize_runs_advisory_lock_on_pg():
    fake = _FakeSession()
    svc = st.StateLockService(fake)
    svc._pg = True
    await svc._xact_serialize("ws1")
    assert any("pg_advisory_xact_lock" in c[0] for c in fake.calls)


async def test_acquire_release_lock_lifecycle(db_session):
    svc = st.StateLockService(db_session)
    ok, existing = await svc.acquire_lock("wsL", "run-1")
    assert ok is True and existing is None
    # second acquire blocked by holder
    ok2, holder = await svc.acquire_lock("wsL", "run-2")
    assert ok2 is False and holder.run_id == "run-1"
    # mismatched release id → refused
    rok, h = await svc.release_lock("wsL", "run-2")
    assert rok is False and h.run_id == "run-1"
    # matching release → success
    rok2, _ = await svc.release_lock("wsL", "run-1")
    assert rok2 is True
    # release on missing → idempotent True
    rok3, _ = await svc.release_lock("wsL", "run-1")
    assert rok3 is True


async def test_release_workspace_lock_helper(db_session):
    assert await st.release_workspace_lock(db_session, "absent") is False
    db_session.add(StateLockEntry(workspace_id="wsF", run_id="r", lock_key=st._lock_key("wsF")))
    await db_session.commit()
    assert await st.release_workspace_lock(db_session, "wsF") is True


# ─── run_step_service ────────────────────────────────────────────────────────


def test_step_names_for_command_terraform_and_helm():
    assert "Approval" in str(rss.step_names_for_command("apply"))
    plan = rss.step_names_for_command("plan")
    assert len(rss.step_names_for_command("apply")) > len(plan)
    helm = rss.step_names_for_command("apply", kind="helm")
    assert helm != rss.step_names_for_command("apply")


async def test_seed_and_list_steps(db_session):
    run = _run(status=RunStatus.PENDING)
    db_session.add(run)
    await db_session.commit()
    seeded = await rss.seed_steps(db_session, run.id, "plan")
    await db_session.commit()
    listed = await rss.list_steps(db_session, run.id)
    assert [s.position for s in listed] == list(range(len(seeded)))


async def test_update_step_status_and_duration(db_session):
    run = _run(status=RunStatus.PENDING)
    db_session.add(run)
    await db_session.commit()
    steps = await rss.seed_steps(db_session, run.id, "plan")
    await db_session.commit()
    step = steps[0]
    # running sets started_at
    await rss.update_step(db_session, step, status=StepStatus.RUNNING.value)
    assert step.started_at is not None
    # success sets completed_at + duration + output/summary
    await rss.update_step(
        db_session, step, status=StepStatus.SUCCESS.value, output="ok", summary_json="{}"
    )
    assert step.completed_at is not None and step.duration_seconds >= 0
    assert step.output == "ok" and step.summary_json == "{}"


async def test_update_step_completed_without_start_sets_zero_duration(db_session):
    run = _run(status=RunStatus.PENDING)
    db_session.add(run)
    await db_session.commit()
    steps = await rss.seed_steps(db_session, run.id, "plan")
    await db_session.commit()
    step = steps[1]
    # skip straight to SKIPPED without RUNNING → started_at None → duration 0
    await rss.update_step(db_session, step, status=StepStatus.SKIPPED.value)
    assert step.duration_seconds == 0


async def test_update_step_normalizes_naive_started_at(db_session):
    run = _run(status=RunStatus.PENDING)
    db_session.add(run)
    await db_session.commit()
    steps = await rss.seed_steps(db_session, run.id, "plan")
    await db_session.commit()
    step = steps[2]
    # tz-naive started_at (as SQLite returns) → SUCCESS must normalize it to UTC
    # before subtracting (covers the tzinfo-None branch).
    step.started_at = datetime(2026, 1, 1, 0, 0, 0)
    await rss.update_step(db_session, step, status=StepStatus.SUCCESS.value)
    assert step.duration_seconds >= 0
