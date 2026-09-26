"""drift_reports: indexes for the gauge, latest-per-workspace reads and retention

`drift_reports` had only its primary key. Three hot paths needed more, and —
measured, not assumed — they need TWO different indexes:

1. `run_worker.gauges_loop` runs `SELECT min(detected_at) FROM drift_reports`
   every 15 s for `tdt_drift_age_seconds_gauge`. In prod on 2026-09-15 that was
   a parallel sequential scan of the whole heap: 1.08M rows, 251 MB, 32,121
   buffers, ~170 ms on two workers, 5,760 times a day — pinning roughly half of
   a t4g.small's shared_buffers to one table around the clock.
   A composite `(workspace_id, detected_at)` btree does NOT help here: it is
   ordered by workspace_id first, so the global minimum of detected_at is not
   at either end of it. Verified on PostgreSQL 18 with 100k rows: with only the
   composite index the planner still chose a Seq Scan (1,235 buffers); with a
   plain `(detected_at)` index it became an InitPlan/Limit over an Index Only
   Scan touching 3 buffers. Hence `ix_drift_reports_detected_at`.

2. `drift.py:_latest_reports_by_workspace` — newest report per workspace via a
   `max(detected_at) GROUP BY workspace_id` subquery with a `workspace_id IN`
   predicate — and

3. `run_worker._prune_drift_reports` — `row_number() OVER (PARTITION BY
   workspace_id ORDER BY detected_at DESC)` —
   are both served by `ix_drift_reports_workspace_detected_at`
   `(workspace_id, detected_at DESC)`: a range scan per workspace, and the
   window's partition+order for free (93 buffers to rank 5,000 victims on the
   same 100k-row fixture). When the IN list is every workspace (superadmin
   `all`), the planner may prefer a heap scan of the two narrow columns — that
   is fine: the point of the rewrite is never touching the TOASTed `resources`
   column for a million rows, not the last few hundred buffers.

Both built CONCURRENTLY on PostgreSQL so a prod deploy does not take an
exclusive lock on a 121 GB (TOAST-inclusive) table. CONCURRENTLY cannot run
inside a transaction and env.py wraps migrations in one, hence
`autocommit_block()`. SQLite (the unit-test dialect) gets plain indexes.

Also runs `ANALYZE drift_reports` once: as of 2026-09-15 prod had NEVER been
analyzed or vacuumed (`pg_stat_user_tables` reported 1,670 rows against a real
count of 1,078,822), so the planner was estimating this table blind. The table
is insert-only and PG's insert-triggered autovacuum had not fired.

Revision ID: 044_drift_reports_index
Revises: 043_workspace_tags
"""
import sqlalchemy as sa
from alembic import op

revision = "044_drift_reports_index"
down_revision = "043_workspace_tags"
branch_labels = None
depends_on = None

IDX_WS_DETECTED = "ix_drift_reports_workspace_detected_at"  # per-workspace latest + retention
IDX_DETECTED = "ix_drift_reports_detected_at"  # global min() for the 15 s gauge


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # CONCURRENTLY must run outside the migration transaction.
        with op.get_context().autocommit_block():
            op.execute(
                sa.text(
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {IDX_WS_DETECTED} "
                    "ON drift_reports (workspace_id, detected_at DESC)"
                )
            )
            op.execute(
                sa.text(
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {IDX_DETECTED} "
                    "ON drift_reports (detected_at)"
                )
            )
            op.execute(sa.text("ANALYZE drift_reports"))
    else:
        op.create_index(
            IDX_WS_DETECTED, "drift_reports", ["workspace_id", sa.text("detected_at DESC")]
        )
        op.create_index(IDX_DETECTED, "drift_reports", ["detected_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {IDX_DETECTED}"))
            op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {IDX_WS_DETECTED}"))
    else:
        op.drop_index(IDX_DETECTED, table_name="drift_reports")
        op.drop_index(IDX_WS_DETECTED, table_name="drift_reports")
