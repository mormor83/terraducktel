import hashlib
from typing import Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.state_lock import StateLockEntry


def _lock_key(workspace_id: str) -> int:
    """Convert workspace_id to a deterministic positive bigint advisory lock key."""
    digest = hashlib.sha256(workspace_id.encode()).digest()
    return int.from_bytes(digest[:8], "big") % (2**63 - 1)


async def _is_postgres(session: AsyncSession) -> bool:
    """Detect if the underlying database is PostgreSQL.

    Phase-4: narrow exception. A transient DB error must not silently flip the
    service to in-process locks for the rest of the session — losing the
    distributed lock guarantee. Only swallow the well-known SQLite case where
    `SELECT version()` returns a non-PostgreSQL string; everything else surfaces.
    """
    from sqlalchemy.exc import OperationalError, ProgrammingError

    try:
        result = await session.execute(text("SELECT version()"))
        version_str = result.scalar() or ""
        return "postgresql" in version_str.lower() or "postgis" in version_str.lower()
    except (OperationalError, ProgrammingError) as e:
        import logging
        logging.getLogger(__name__).warning(
            "Could not verify PostgreSQL for advisory locks (falling back to SQLite mode): %s", e
        )
        return False


class StateLockService:
    """Manages distributed state locks for terraform workspaces.

    The `state_lock_entry` row is the source of truth — `acquire_lock` inserts
    a row, `release_lock` deletes it. On PostgreSQL, a transaction-scoped
    advisory lock (`pg_advisory_xact_lock`) serializes the read-modify-write
    around the row so concurrent acquires/releases on the same workspace can't
    race. The advisory lock auto-releases on commit, so the FastAPI connection
    pool can hand different connections to subsequent requests without breaking
    correctness (the previous design used `pg_try_advisory_lock`, which is
    session-scoped, and produced spurious 409s when LOCK and UNLOCK arrived on
    different pooled connections).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._pg: Optional[bool] = None

    async def _use_postgres(self) -> bool:
        if self._pg is None:
            self._pg = await _is_postgres(self._session)
        return self._pg

    async def _xact_serialize(self, workspace_id: str) -> None:
        if await self._use_postgres():
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _lock_key(workspace_id)},
            )

    async def acquire_lock(
        self, workspace_id: str, lock_id: str
    ) -> Tuple[bool, Optional[StateLockEntry]]:
        """Attempt to acquire an exclusive lock for a workspace.

        Returns (True, None) on success, or (False, existing_entry) if another
        run already holds the lock — the caller can surface the holder in the
        409 response so terraform names it in its error output.
        """
        await self._xact_serialize(workspace_id)
        existing = await self._session.get(StateLockEntry, workspace_id)
        if existing is not None:
            await self._session.commit()
            return False, existing
        entry = StateLockEntry(
            workspace_id=workspace_id,
            run_id=lock_id,
            lock_key=_lock_key(workspace_id),
        )
        self._session.add(entry)
        await self._session.commit()
        return True, None

    async def release_lock(
        self, workspace_id: str, lock_id: Optional[str] = None
    ) -> Tuple[bool, Optional[StateLockEntry]]:
        """Release the lock for a workspace.

        Idempotent on missing row (returns True). When `lock_id` is provided,
        only deletes when it matches the holder; mismatched lock IDs return
        (False, existing_entry). Pass `lock_id=None` for the force-unlock path
        (reaper / operator escape hatch).
        """
        await self._xact_serialize(workspace_id)
        existing = await self._session.get(StateLockEntry, workspace_id)
        if existing is None:
            await self._session.commit()
            return True, None
        if lock_id is not None and existing.run_id != lock_id:
            holder = existing
            await self._session.commit()
            return False, holder
        await self._session.delete(existing)
        await self._session.commit()
        return True, None


async def release_workspace_lock(session: AsyncSession, workspace_id: str) -> bool:
    """Force-release any held lock for a workspace.

    Used by the reaper (when a run job is detected as stale) and by the
    operator force-unlock endpoint. Returns True iff a row was actually
    present and removed (so the reaper can log whether real cleanup happened).
    """
    existing = await session.get(StateLockEntry, workspace_id)
    if existing is None:
        return False
    svc = StateLockService(session)
    await svc.release_lock(workspace_id, lock_id=None)
    return True
