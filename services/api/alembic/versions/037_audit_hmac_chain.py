"""Audit chain: keyed HMAC in the app; DB stops recomputing entry_hash.

Migration 015 gave the DB trigger `audit_logs_chain_guard` an UNKEYED SHA-256
recompute of `entry_hash`, and claimed "a rogue psql session also can't tamper."
That was false: anyone with DB access could recompute the exact same unkeyed
digest over the (public) row fields and rewrite the whole chain undetectably.

The app now computes `entry_hash` as a **keyed HMAC-SHA256** whose key is derived
from `CREDENTIAL_ENCRYPTION_KEY` (see `app.services.audit_chain`) — a secret the
database role does not hold. This migration therefore replaces the trigger so it
NO LONGER recomputes `entry_hash` (the DB can't, without the key) while KEEPING:
  - the prev_hash chain-linkage check (structural, key-independent), and
  - the append-only UPDATE/DELETE blocks from 015.

Net effect: a rogue psql can still INSERT a row, but cannot produce a valid
`entry_hash` without the app key, so `verify_chain()` (app-side, keyed) detects
any forgery; and existing rows still cannot be UPDATE/DELETE'd.

Note: rows stamped before this migration used the old unkeyed SHA-256 and will
show as "broken" under the new keyed verification — expected; the cryptographic
guarantee covers rows written from here forward.

Revision ID: 037_audit_hmac_chain
Revises: 036_policies
Create Date: 2026-07-07
"""
from alembic import op

revision = "037_audit_hmac_chain"
down_revision = "036_policies"
branch_labels = None
depends_on = None


# prev_hash linkage only — no entry_hash recompute (the DB has no HMAC key).
_CHAIN_GUARD_LINKAGE_ONLY = r"""
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

    -- entry_hash is a keyed HMAC computed in the application; the DB cannot
    -- (and must not) recompute it. Authenticity is verified app-side by
    -- verify_chain(). Append-only is still enforced by the UPDATE/DELETE
    -- triggers created in migration 015.
    RETURN NEW;
END;
$$;
"""

# Restore the original 015 body (unkeyed entry_hash recompute) on downgrade.
_CHAIN_GUARD_ORIGINAL = r"""
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
        RAISE EXCEPTION 'audit_logs: prev_hash chain broken (got %, expected %)',
            NEW.prev_hash, expected_prev USING ERRCODE = 'check_violation';
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
        RAISE EXCEPTION 'audit_logs: entry_hash mismatch (got %, expected %)',
            NEW.entry_hash, expected_entry USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.execute(_CHAIN_GUARD_LINKAGE_ONLY)


def downgrade() -> None:
    op.execute(_CHAIN_GUARD_ORIGINAL)
