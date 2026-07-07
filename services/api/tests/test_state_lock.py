import pytest


class TestStateLock:
    @pytest.mark.asyncio
    async def test_acquire_lock_succeeds(self, db_session):
        from app.services.state_service import StateLockService
        svc = StateLockService(db_session)
        acquired, holder = await svc.acquire_lock("workspace-abc", "run-001")
        assert acquired is True
        assert holder is None

    @pytest.mark.asyncio
    async def test_acquire_same_lock_twice_fails(self, db_session):
        from app.services.state_service import StateLockService
        svc = StateLockService(db_session)
        acquired1, _ = await svc.acquire_lock("workspace-abc", "run-001")
        acquired2, holder = await svc.acquire_lock("workspace-abc", "run-002")
        assert acquired1 is True
        assert acquired2 is False
        assert holder is not None
        assert holder.run_id == "run-001"

    @pytest.mark.asyncio
    async def test_release_lock_allows_reacquire(self, db_session):
        from app.services.state_service import StateLockService
        svc = StateLockService(db_session)
        await svc.acquire_lock("workspace-xyz", "run-001")
        released, _ = await svc.release_lock("workspace-xyz", lock_id="run-001")
        assert released is True
        acquired, _ = await svc.acquire_lock("workspace-xyz", "run-002")
        assert acquired is True

    @pytest.mark.asyncio
    async def test_release_is_idempotent_when_not_held(self, db_session):
        """Regression: terraform's UNLOCK on a never-acquired (or
        already-released) workspace must NOT 409. This was the source of the
        scary 'Error releasing the state lock' messages users were seeing."""
        from app.services.state_service import StateLockService
        svc = StateLockService(db_session)
        released, holder = await svc.release_lock("workspace-noop", lock_id="run-x")
        assert released is True
        assert holder is None

    @pytest.mark.asyncio
    async def test_release_with_mismatched_lock_id_is_rejected(self, db_session):
        from app.services.state_service import StateLockService
        svc = StateLockService(db_session)
        await svc.acquire_lock("workspace-abc", "run-001")
        released, holder = await svc.release_lock("workspace-abc", lock_id="run-002")
        assert released is False
        assert holder is not None
        assert holder.run_id == "run-001"

    @pytest.mark.asyncio
    async def test_force_release_clears_row(self, db_session):
        """Operator force-unlock (and the reaper) calls release_lock with
        lock_id=None — it must clear the row even if the caller doesn't know
        the holder's ID."""
        from app.services.state_service import (
            StateLockService,
            release_workspace_lock,
        )
        svc = StateLockService(db_session)
        await svc.acquire_lock("workspace-stuck", "run-old")
        cleared = await release_workspace_lock(db_session, "workspace-stuck")
        assert cleared is True
        # Subsequent force-release on the now-empty workspace returns False
        # (nothing to clear) but does not raise.
        again = await release_workspace_lock(db_session, "workspace-stuck")
        assert again is False

    @pytest.mark.asyncio
    async def test_release_works_across_sessions(self, _setup_db):
        """Regression for the 409 bug: under the FastAPI connection pool, the
        LOCK and UNLOCK HTTP requests can land on different DB sessions. The
        old implementation used session-scoped `pg_try_advisory_lock`, so the
        release session's `pg_advisory_unlock` returned False and the API
        replied 409 even though the run had completed cleanly.

        With the row-based design, an UNLOCK on a different session sees the
        same `state_lock_entry` row and succeeds.
        """
        from app.services.state_service import StateLockService

        factory = _setup_db

        # session A acquires
        async with factory() as session_a:
            svc_a = StateLockService(session_a)
            acquired, _ = await svc_a.acquire_lock("workspace-pool", "run-001")
            assert acquired is True

        # session B (a different pooled connection) releases
        async with factory() as session_b:
            svc_b = StateLockService(session_b)
            released, _ = await svc_b.release_lock(
                "workspace-pool", lock_id="run-001"
            )
            assert released is True
