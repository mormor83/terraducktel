"""Run reviewer for 4-eyes branches.

Adds:
  - runs.reviewer_id : user_id chosen at trigger time on 4-eyes-gated branches
                       (currently `dev`). Only this user (or an admin override)
                       can approve the run. NULL on self-approve branches.

The column is nullable because existing rows (and runs on non-gated branches)
have no reviewer. A partial index on (reviewer_id) speeds up the "approvals
assigned to me" query the UI will issue.

Revision ID: 013_run_reviewer
Revises: 012_branch_webhook
Create Date: 2026-05-11
"""
from alembic import op
import sqlalchemy as sa

revision = "013_run_reviewer"
down_revision = "012_branch_webhook"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "reviewer_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_runs_reviewer_id",
        "runs",
        ["reviewer_id"],
        postgresql_where=sa.text("reviewer_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_runs_reviewer_id", table_name="runs")
    op.drop_column("runs", "reviewer_id")
