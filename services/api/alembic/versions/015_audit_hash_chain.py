"""Immutable audit log — hash chain + reject UPDATE/DELETE.

Adds:
  - audit_logs.prev_hash, audit_logs.entry_hash (both NOT NULL, default '').
  - DB trigger `audit_logs_chain_guard` that on INSERT:
        * Verifies prev_hash matches the latest row's entry_hash
          (or '' if the table is empty).
        * Recomputes entry_hash from the canonical row content and checks the
          caller's value.
  - DB trigger `audit_logs_immutable_update` that aborts any UPDATE.
  - DB trigger `audit_logs_immutable_delete` that aborts any DELETE.

The Python side is responsible for setting prev_hash + entry_hash before
INSERT; the triggers are the belt to the application's braces, so a rogue
psql session also can't tamper.

Backfill: existing rows get a freshly-computed chain in a single transaction
so the trigger's "prev_hash must match latest" rule is satisfied from the
first new INSERT onward.

Revision ID: 015_audit_hash_chain
Revises: 014_run_jobs_queue
Create Date: 2026-05-11
"""
from alembic import op
import sqlalchemy as sa

revision = "015_audit_hash_chain"
down_revision = "014_run_jobs_queue"
branch_labels = None
depends_on = None


# Canonical row serialisation used by both the trigger and the Python helper.
# Order matters: any change to this format invalidates every stored hash.
# Keep this in sync with `app.services.audit_chain.canonical_row()`.
_CANONICAL_FN_SQL = r"""
CREATE OR REPLACE FUNCTION audit_logs_canonical(
    p_id text, p_user_id text, p_action text, p_resource_type text,
    p_resource_id text, p_workspace_id text, p_details jsonb, p_created_at timestamptz
) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT concat_ws(
        '|',
        p_id,
        coalesce(p_user_id, ''),
        p_action,
        p_resource_type,
        p_resource_id,
        coalesce(p_workspace_id, ''),
        coalesce(p_details::text, 'null'),
        to_char(p_created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
    );
$$;
"""

_CHAIN_GUARD_SQL = r"""
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

_IMMUTABLE_SQL = r"""
CREATE OR REPLACE FUNCTION audit_logs_block_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only; % is not permitted', TG_OP
        USING ERRCODE = 'feature_not_supported';
END;
$$;
"""


def upgrade() -> None:
    # pgcrypto provides digest() — should be available on Postgres 13+ by default
    # but enabling it explicitly avoids surprises on minimal images.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.add_column(
        "audit_logs",
        sa.Column("prev_hash", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "audit_logs",
        sa.Column("entry_hash", sa.String(64), nullable=False, server_default=""),
    )

    op.execute(_CANONICAL_FN_SQL)
    op.execute(_CHAIN_GUARD_SQL)
    op.execute(_IMMUTABLE_SQL)

    # Backfill existing rows in oldest-first order so the chain is well-formed
    # before the trigger goes live. We hash a Postgres-side canonical form here
    # purely to populate the chain; Python-side verification uses its own
    # canonicalisation, so pre-015 rows will show as "broken" in verify until
    # the operator runs a re-stamp pass (or accepts that history before this
    # migration isn't covered by the cryptographic guarantee).
    op.execute(
        r"""
        DO $$
        DECLARE
            r RECORD;
            prev TEXT := '';
            ent  TEXT;
        BEGIN
            FOR r IN SELECT * FROM audit_logs ORDER BY created_at ASC, id ASC LOOP
                ent := encode(
                    digest(
                        prev ||
                        audit_logs_canonical(r.id, r.user_id, r.action, r.resource_type,
                                              r.resource_id, r.workspace_id,
                                              r.details::jsonb, r.created_at),
                        'sha256'
                    ),
                    'hex'
                );
                UPDATE audit_logs SET prev_hash = prev, entry_hash = ent WHERE id = r.id;
                prev := ent;
            END LOOP;
        END$$;
        """
    )

    op.execute(
        "CREATE TRIGGER audit_logs_chain_guard "
        "BEFORE INSERT ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION audit_logs_chain_guard();"
    )
    op.execute(
        "CREATE TRIGGER audit_logs_immutable_update "
        "BEFORE UPDATE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION audit_logs_block_change();"
    )
    op.execute(
        "CREATE TRIGGER audit_logs_immutable_delete "
        "BEFORE DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION audit_logs_block_change();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_immutable_delete ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_immutable_update ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_chain_guard ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_block_change()")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_chain_guard()")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_canonical(text,text,text,text,text,text,jsonb,timestamptz)")
    op.drop_column("audit_logs", "entry_hash")
    op.drop_column("audit_logs", "prev_hash")
