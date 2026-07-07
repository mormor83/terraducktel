"""cloud_assets — Firefly-style cloud asset inventory.

One row per discovered cloud resource, classified by IaC status (codified /
drifted / ghost / unmanaged / ignored / undetermined). Refreshed by the
drift-detector on every scan and aggregated by the Inventory dashboard into a
codification percentage + per-state counts. Purely additive.

Revision ID: 034_cloud_assets
Revises: 033_drift_breakdown
Create Date: 2026-06-08
"""
from alembic import op
import sqlalchemy as sa

revision = "034_cloud_assets"
down_revision = "033_drift_breakdown"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cloud_assets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "business_unit_id",
            sa.String(),
            sa.ForeignKey("business_units.id"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.String(),
            sa.ForeignKey("workspaces.id"),
            nullable=True,
        ),
        sa.Column("asset_id", sa.String(), nullable=False),
        sa.Column("address", sa.String(), nullable=True),
        sa.Column("asset_type", sa.String(), nullable=False, server_default=""),
        sa.Column("provider", sa.String(), nullable=False, server_default="aws"),
        sa.Column("region", sa.String(), nullable=False, server_default=""),
        sa.Column("account_id", sa.String(), nullable=False, server_default=""),
        sa.Column("iac_status", sa.String(length=20), nullable=False, server_default="undetermined"),
        sa.Column("drift_summary", sa.Text(), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("business_unit_id", "asset_id", name="uq_cloud_assets_bu_asset"),
    )
    op.create_index("ix_cloud_assets_bu", "cloud_assets", ["business_unit_id"])
    op.create_index("ix_cloud_assets_workspace", "cloud_assets", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_cloud_assets_workspace", table_name="cloud_assets")
    op.drop_index("ix_cloud_assets_bu", table_name="cloud_assets")
    op.drop_table("cloud_assets")
