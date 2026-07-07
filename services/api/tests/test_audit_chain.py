"""Regression tests for the audit-log hash chain (task #10).

The DB trigger that physically blocks UPDATE/DELETE is Postgres-only and is
verified by the live smoke check (see CLAUDE.md). These tests cover the
Python-side guarantees: canonical serialisation is deterministic, prev_hash
threads correctly through the chain, and `verify_chain` catches tampering.
"""
from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone

from app.models.audit_log import AuditLog
from app.services.audit_chain import (
    canonical_row,
    compute_entry_hash,
    latest_hash,
    stamp,
    verify_chain,
)


def _make(*, action="login", details=None, ts=None, uid=None, ws=None):
    return AuditLog(
        id=f"id-{action}",
        user_id=uid,
        action=action,
        resource_type="user",
        resource_id="rid",
        workspace_id=ws,
        details=details,
        created_at=ts or datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc),
    )


class TestCanonicalRow:
    def test_includes_every_field_in_fixed_order(self):
        row = _make(action="approve", uid="u1", ws="w1", details={"x": 1})
        s = canonical_row(row)
        # Hard-coded order so any reshuffle of the canonicaliser shows up.
        assert s == (
            "id-approve|u1|approve|user|rid|w1|"
            + '{"x":1}'
            + "|2026-05-11T12:00:00.000000Z"
        )

    def test_nullable_fields_render_as_empty_string(self):
        row = _make(uid=None, ws=None, details=None)
        s = canonical_row(row)
        assert "||" in s, "user_id and workspace_id should render as '' between pipes"
        assert "|null|" in s, "details=None must render as the literal 'null' to match jsonb::text"

    def test_dict_order_does_not_change_hash(self):
        a = _make(details={"alpha": 1, "beta": 2})
        b = _make(details={"beta": 2, "alpha": 1})
        # sort_keys=True is the property under test — any regression here breaks
        # the chain for everyone sharing the table.
        assert canonical_row(a) == canonical_row(b)


class TestComputeEntryHash:
    def test_deterministic(self):
        row = _make()
        assert compute_entry_hash("", row) == compute_entry_hash("", row)

    def test_prev_hash_affects_output(self):
        row = _make()
        h0 = compute_entry_hash("", row)
        h1 = compute_entry_hash("a" * 64, row)
        assert h0 != h1, "prev_hash MUST contribute to the digest"

    def test_returns_64_char_hex(self):
        h = compute_entry_hash("", _make())
        assert len(h) == 64
        int(h, 16)  # raises if non-hex


@pytest.mark.asyncio
async def test_latest_hash_empty_table_returns_empty_string(db_session):
    assert await latest_hash(db_session) == ""


@pytest.mark.asyncio
async def test_stamp_threads_chain_across_rows(db_session):
    r1 = AuditLog(action="login", resource_type="user", resource_id="u1")
    db_session.add(r1)
    await stamp(db_session, r1)
    await db_session.flush()
    assert r1.prev_hash == ""
    assert len(r1.entry_hash) == 64

    r2 = AuditLog(action="approve", resource_type="run", resource_id="r1")
    db_session.add(r2)
    await stamp(db_session, r2)
    await db_session.flush()
    # r2 must chain off r1
    assert r2.prev_hash == r1.entry_hash
    assert r2.entry_hash != r1.entry_hash


@pytest.mark.asyncio
async def test_verify_chain_clean(db_session):
    r1 = AuditLog(action="a1", resource_type="user", resource_id="x")
    db_session.add(r1)
    await stamp(db_session, r1)
    r2 = AuditLog(action="a2", resource_type="user", resource_id="x")
    db_session.add(r2)
    await stamp(db_session, r2)
    await db_session.commit()

    result = await verify_chain(db_session)
    assert result == {"ok": True, "total": 2, "broken_at": []}


@pytest.mark.asyncio
async def test_verify_chain_detects_content_tamper(db_session):
    r = AuditLog(action="legit", resource_type="user", resource_id="x")
    db_session.add(r)
    await stamp(db_session, r)
    await db_session.commit()

    # Simulate someone (or a tampered dump) editing action without updating
    # entry_hash. The verifier must catch it.
    r.action = "TAMPERED"
    await db_session.commit()

    result = await verify_chain(db_session)
    assert result["ok"] is False
    assert r.id in result["broken_at"]


@pytest.mark.asyncio
async def test_verify_chain_detects_reordered_rows(db_session):
    r1 = AuditLog(action="first", resource_type="user", resource_id="x")
    db_session.add(r1)
    await stamp(db_session, r1)
    r2 = AuditLog(action="second", resource_type="user", resource_id="x")
    db_session.add(r2)
    await stamp(db_session, r2)
    await db_session.commit()

    # Swap their prev_hashes — chain still has the same content but order is
    # broken. Verifier walks oldest-first and should flag both rows.
    r1.prev_hash, r2.prev_hash = r2.prev_hash, r1.prev_hash
    await db_session.commit()

    result = await verify_chain(db_session)
    assert result["ok"] is False
    assert len(result["broken_at"]) >= 1
