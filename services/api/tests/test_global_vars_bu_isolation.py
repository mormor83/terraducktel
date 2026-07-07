"""Global variables are scoped per Business Unit — they must NOT leak across BUs.

Regression test for the bug where `global_variables` had no `business_unit_id`
and a globally-unique key, so one BU's globals were listed by — and injected
into the runs of — every other BU.
"""
from __future__ import annotations

import uuid

import pytest

from app.schemas.variable import VariableCreate


def _v(key: str, value: str, *, is_secret: bool = False) -> VariableCreate:
    return VariableCreate(key=key, value=value, is_secret=is_secret, is_hcl=False, description=None)


BU_A = "11111111-1111-1111-1111-111111111111"
BU_B = "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_globals_scoped_and_same_key_allowed_across_bus(_setup_db):
    from app.services import variable_service as varsvc

    factory = _setup_db
    async with factory() as session:
        # Same key in two BUs — must be allowed (unique is (bu, key), not key).
        a = await varsvc.create_global(session, _v("region", "us-east-1"), BU_A)
        await varsvc.create_global(session, _v("region", "eu-west-1"), BU_B)
        await varsvc.create_global(session, _v("a_only", "1"), BU_A)
        await session.commit()
        a_id = a.id

    async with factory() as session:
        a_rows = {r.key for r in await varsvc.list_globals(session, BU_A)}
        b_rows = {r.key for r in await varsvc.list_globals(session, BU_B)}
        assert a_rows == {"region", "a_only"}      # BU A sees only its own
        assert b_rows == {"region"}                # BU B does NOT see a_only

        # Cross-BU fetch-by-id is blocked (can't reach BU A's row from BU B).
        assert await varsvc.get_global_by_id(session, a_id, business_unit_id=BU_B) is None
        assert await varsvc.get_global_by_id(session, a_id, business_unit_id=BU_A) is not None


@pytest.mark.asyncio
async def test_merged_for_run_only_includes_own_bu_globals(_setup_db):
    """A run in BU A must merge BU A's global, never BU B's same-key value."""
    from app.models.run import Run, RunStatus
    from app.models.workspace import Workspace
    from app.services import variable_service as varsvc

    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    factory = _setup_db
    async with factory() as session:
        await varsvc.create_global(session, _v("region", "us-east-1"), BU_A)
        await varsvc.create_global(session, _v("region", "eu-west-1"), BU_B)
        session.add(Workspace(
            id=ws_id, name="iso", business_unit_id=BU_A,
            repo_url="https://example.com/r.git", tf_working_dir=".",
            aws_account_id="123456789012", environment="dev",
        ))
        session.add(Run(id=run_id, workspace_id=ws_id, command="plan", status=RunStatus.PENDING))
        await session.commit()

    async with factory() as session:
        run = await session.get(Run, run_id)
        merged = await varsvc.get_merged_for_run(session, ws_id, run)
        assert merged["region"].value == "us-east-1"   # BU A's, not BU B's eu-west-1
