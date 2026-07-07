"""drift_breakdown — classify drift into the 4 canonical categories.

Adds per-category counts and a per-resource detail blob to `drift_reports` so
the new per-BU Drift dashboard can break drift into Modified (attribute),
Untracked (ghost), Deleted (orphaned), and State-Config Mismatch — and drill
into exactly which resources are affected. Purely additive; the legacy
has_drift / summary / plan_output columns are untouched.

Revision ID: 033_drift_breakdown
Revises: 032_api_keys
Create Date: 2026-06-08
"""
from alembic import op
import sqlalchemy as sa

revision = "033_drift_breakdown"
down_revision = "032_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "drift_reports",
        sa.Column("modified_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "drift_reports",
        sa.Column("untracked_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "drift_reports",
        sa.Column("deleted_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "drift_reports",
        sa.Column("mismatch_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "drift_reports",
        sa.Column("resources", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("drift_reports", "resources")
    op.drop_column("drift_reports", "mismatch_count")
    op.drop_column("drift_reports", "deleted_count")
    op.drop_column("drift_reports", "untracked_count")
    op.drop_column("drift_reports", "modified_count")
