"""Audit chain: relax DB-side content hashing to chain-link only.

The 015 trigger tried to recompute entry_hash inside Postgres, but Postgres
`jsonb::text` and Python's `json.dumps` disagree on whitespace, so legitimate
inserts from Python kept tripping the trigger. This migration keeps the
chain-LINK enforcement (prev_hash must match the latest row's entry_hash) and
the UPDATE/DELETE block, but drops the content recomputation. Content
verification is done in Python by `/api/v1/audit/verify`, which walks the
chain end-to-end. Net guarantees:

  - Append-only at the DB level (UPDATE/DELETE rejected).
  - Insertion order is cryptographically anchored — re-ordering or skipping
    any row breaks the chain.
  - Content↔hash mismatch is caught by the verifier endpoint.

Revision ID: 016_audit_chain_link_only
Revises: 015_audit_hash_chain
Create Date: 2026-05-11
"""
from alembic import op

revision = "016_audit_chain_link_only"
down_revision = "015_audit_hash_chain"
branch_labels = None
depends_on = None


_CHAIN_GUARD_SQL = r"""
CREATE OR REPLACE FUNCTION audit_logs_chain_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    expected_prev text;
BEGIN
    SELECT entry_hash INTO expected_prev
    FROM audit_logs
    ORDER BY created_at DESC, id DESC
    LIMIT 1;
    expected_prev := coalesce(expected_prev, '');

    IF NEW.prev_hash IS DISTINCT FROM expected_prev THEN
        RAISE EXCEPTION 'audit_logs: prev_hash chain broken (got %, expected %)',
            NEW.prev_hash, expected_prev USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.entry_hash IS NULL OR length(NEW.entry_hash) <> 64 THEN
        RAISE EXCEPTION 'audit_logs: entry_hash must be a 64-char hex digest'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;
"""

# The earlier strict trigger — used by downgrade().
_STRICT_GUARD_SQL = r"""
CREATE OR REPLACE FUNCTION audit_logs_chain_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    expected_prev text;
    expected_entry text;
BEGIN
    SELECT entry_hash INTO expected_prev
    FROM audit_logs
    ORDER BY created_at DESC, id DESC
    LIMIT 1;
    expected_prev := coalesce(expected_prev, '');

    IF NEW.prev_hash IS DISTINCT FROM expected_prev THEN
        RAISE EXCEPTION 'audit_logs: prev_hash chain broken' USING ERRCODE = 'check_violation';
    END IF;

    expected_entry := encode(
        digest(
            NEW.prev_hash ||
            audit_logs_canonical(NEW.id, NEW.user_id, NEW.action, NEW.resource_type,
                                  NEW.resource_id, NEW.workspace_id,
                                  NEW.details::jsonb, NEW.created_at),
            'sha256'
        ),
        'hex'
    );

    IF NEW.entry_hash IS DISTINCT FROM expected_entry THEN
        RAISE EXCEPTION 'audit_logs: entry_hash mismatch' USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.execute(_CHAIN_GUARD_SQL)


def downgrade() -> None:
    op.execute(_STRICT_GUARD_SQL)
