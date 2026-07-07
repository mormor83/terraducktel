"""Drift reports and workspace drift_status

Revision ID: 005_drift
Revises: 004_audit_log
Create Date: 2026-03-21
"""
from alembic import op
import sqlalchemy as sa

revision = "005_drift"
down_revision = "004_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("drift_status", sa.String(length=20), server_default="unknown", nullable=False),
    )
    op.create_table(
        "drift_reports",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("has_drift", sa.Boolean(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("plan_output", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("drift_reports")
    op.drop_column("workspaces", "drift_status")
