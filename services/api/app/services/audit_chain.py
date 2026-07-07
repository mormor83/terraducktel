"""Helpers for the audit-log hash chain.

The chain's authenticity is enforced in the APPLICATION with a **keyed HMAC**. Each row's `entry_hash = HMAC-SHA256(k, prev_hash || canonical_row)`
where `k` is derived from `CREDENTIAL_ENCRYPTION_KEY` — a secret the database
role does NOT possess. The DB triggers (migration 015, amended by the audit-HMAC
migration) still enforce append-only (no UPDATE/DELETE) and prev_hash chain
linkage, but the DB can no longer recompute `entry_hash` (it lacks the key), so
a rogue `psql` session that inserts or rewrites rows cannot forge a valid chain
— `verify_chain()` detects it. (The earlier design used an unkeyed SHA-256 that
anyone with DB access could recompute, so its "a rogue psql can't tamper" claim
was false.)

Canonical row format (kept byte-identical to `audit_logs_canonical()` in the
SQL migration so the prev_hash linkage the trigger checks stays consistent):

    id|user_id_or_empty|action|resource_type|resource_id|
    workspace_id_or_empty|details_json_or_null|created_at_iso_utc

`details` is serialised with sort_keys=True so dict-ordering can't break the
hash. `created_at` is rendered as `YYYY-MM-DDTHH:MM:SS.ffffffZ` to match
Postgres's `YYYY-MM-DD"T"HH24:MI:SS.US"Z"` format.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


@lru_cache(maxsize=1)
def _hmac_key() -> bytes:
    """Derive the audit-chain HMAC key from CREDENTIAL_ENCRYPTION_KEY.

    Domain-separated via HKDF `info` from the Fernet/session derivations so the
    audit key is independent of the credential-encryption and session keys.
    Cached because the process-level key never changes at runtime.
    """
    from app.auth.encryption_key import get_credential_encryption_key

    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"terraducktel-audit-v1",
        info=b"audit-chain-hmac",
    ).derive(get_credential_encryption_key())


def _details_to_canonical(details: Any) -> str:
    """Match Postgres jsonb::text — which renders NULL as the literal 'null'."""
    if details is None:
        return "null"
    return json.dumps(details, sort_keys=True, separators=(",", ":"))


def _ts_to_canonical(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    # 6 digits of microsecond + "Z"
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def canonical_row(row: AuditLog) -> str:
    return "|".join(
        [
            row.id,
            row.user_id or "",
            row.action,
            row.resource_type,
            row.resource_id,
            row.workspace_id or "",
            _details_to_canonical(row.details),
            _ts_to_canonical(row.created_at),
        ]
    )


def compute_entry_hash(prev_hash: str, row: AuditLog) -> str:
    payload = (prev_hash + canonical_row(row)).encode()
    return hmac.new(_hmac_key(), payload, hashlib.sha256).hexdigest()


async def latest_hash(session: AsyncSession) -> str:
    """Return the entry_hash of the most recent audit row, or '' if table empty.

    `no_autoflush` so the SELECT doesn't trigger SQLAlchemy to flush any
    just-added-but-not-yet-stamped row first — that row would then become
    "the latest entry" with empty hashes and break the chain. Previously
    stamped rows in the same request are already flushed (see `stamp()`'s
    trailing flush) so they're visible without autoflush.
    """
    with session.no_autoflush:
        result = await session.execute(
            select(AuditLog.entry_hash)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
    row = result.scalar_one_or_none()
    return row or ""


async def stamp(session: AsyncSession, row: AuditLog) -> None:
    """Populate id + created_at + prev_hash + entry_hash on a row before INSERT.

    Caller pattern is `session.add(row); await stamp(session, row)`. We do
    *not* let SQLAlchemy's defaults set `id` / `created_at` at INSERT time
    because the canonical form includes both — the digest must be computed
    over the exact values that will be stored.

    To make this safe for chained calls (`stamp(r1); stamp(r2)` in the same
    request), we explicitly flush at the end so that the row enters the DB
    with its hashes already populated. Otherwise the next `latest_hash()`
    would either (a) trigger an autoflush of the unstamped row, breaking the
    PG trigger, or (b) miss it entirely and chain off the previous run.
    """
    if row.id is None:
        row.id = str(uuid.uuid4())
    if row.created_at is None:
        row.created_at = datetime.now(timezone.utc)
    row.prev_hash = await latest_hash(session)
    row.entry_hash = compute_entry_hash(row.prev_hash, row)
    # Materialise so subsequent stamp() calls in the same request can chain
    # off this row. Commit still happens at the request boundary.
    await session.flush()


async def verify_chain(session: AsyncSession, limit: int | None = None) -> dict:
    """Walk the chain and return a summary.

    {
        "ok": bool,
        "total": int,
        "broken_at": [<row_id>, ...],   # first few mismatches if any
    }

    O(n) over the table — fine for thousands of rows, paginate for prod size.
    """
    q = select(AuditLog).order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
    if limit is not None:
        q = q.limit(limit)
    result = await session.execute(q)
    prev = ""
    total = 0
    broken: list[str] = []
    for row in result.scalars():
        expected = compute_entry_hash(prev, row)
        if row.prev_hash != prev or row.entry_hash != expected:
            broken.append(row.id)
            if len(broken) >= 5:
                return {"ok": False, "total": total + 1, "broken_at": broken}
        prev = row.entry_hash
        total += 1
    return {"ok": not broken, "total": total, "broken_at": broken}
