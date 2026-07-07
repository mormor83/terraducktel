#!/usr/bin/env python3
"""Re-stamp every audit_logs row with Python-canonical hashes.

The 015 migration backfilled hashes using a Postgres-side canonical form, but
production hashing is Python-side (see app/services/audit_chain.py) and the
two whitespace conventions for jsonb differ. Run this once after upgrading to
016 to bring every existing row into the Python-canonical chain. Idempotent.

Usage:
  DATABASE_URL=... python scripts/restamp_audit_chain.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _details_canonical(details) -> str:
    if details is None:
        return "null"
    if isinstance(details, str):
        # Postgres returned jsonb as a parsed dict already; if it's a string
        # (e.g. SQLite path), try to parse so we canonicalise the same way.
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            pass
    return json.dumps(details, sort_keys=True, separators=(",", ":"))


def _ts_canonical(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _entry_hash(prev: str, row: dict) -> str:
    canonical = "|".join(
        [
            row["id"],
            row["user_id"] or "",
            row["action"],
            row["resource_type"],
            row["resource_id"],
            row["workspace_id"] or "",
            _details_canonical(row["details"]),
            _ts_canonical(row["created_at"]),
        ]
    )
    return hashlib.sha256((prev + canonical).encode()).hexdigest()


async def restamp() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL required", file=sys.stderr)
        return 1

    engine = create_async_engine(url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        await session.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER ALL"))
        try:
            result = await session.execute(
                text(
                    "SELECT id, user_id, action, resource_type, resource_id, "
                    "workspace_id, details, created_at FROM audit_logs "
                    "ORDER BY created_at ASC, id ASC"
                )
            )
            prev = ""
            n = 0
            for mapping in result.mappings():
                row = dict(mapping)
                ent = _entry_hash(prev, row)
                await session.execute(
                    text(
                        "UPDATE audit_logs SET prev_hash = :p, entry_hash = :e WHERE id = :i"
                    ),
                    {"p": prev, "e": ent, "i": row["id"]},
                )
                prev = ent
                n += 1
            await session.commit()
            print(f"restamped {n} row(s)")
        finally:
            await session.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER ALL"))
            await session.commit()

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(restamp()))
