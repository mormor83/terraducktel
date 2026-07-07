"""PG-backed job queue for executor launches.

Decouples `POST /workspaces/{id}/runs` (which now returns instantly) from the
actual `docker run` of the executor. A worker loop in the API claims jobs via
SELECT … FOR UPDATE SKIP LOCKED, runs the executor, and updates state. Stale
runs (no heartbeat for 90s) are reaped and their advisory locks released.

Revision ID: 014_run_jobs_queue
Revises: 013_run_reviewer
Create Date: 2026-05-11
"""
from alembic import op
import sqlalchemy as sa

revision = "014_run_jobs_queue"
down_revision = "013_run_reviewer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "state",
            sa.Enum("queued", "picked", "done", "failed", name="runjobstate"),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("phase", sa.String(20), nullable=False, server_default="plan"),
        sa.Column("picked_by", sa.String(255), nullable=True),
        sa.Column("picked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # The worker's hot query: oldest queued first. Partial index so we don't
    # carry every terminal job in the index forever.
    op.create_index(
        "ix_run_jobs_queued",
        "run_jobs",
        ["created_at"],
        postgresql_where=sa.text("state = 'queued'"),
    )
    # The reaper's hot query: picked jobs ordered by heartbeat for staleness.
    op.create_index(
        "ix_run_jobs_picked_heartbeat",
        "run_jobs",
        ["heartbeat_at"],
        postgresql_where=sa.text("state = 'picked'"),
    )


def downgrade() -> None:
    op.drop_index("ix_run_jobs_picked_heartbeat", table_name="run_jobs")
    op.drop_index("ix_run_jobs_queued", table_name="run_jobs")
    op.drop_table("run_jobs")
    op.execute("DROP TYPE IF EXISTS runjobstate")
