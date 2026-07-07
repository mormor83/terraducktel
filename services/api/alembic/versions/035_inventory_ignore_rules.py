"""inventory_ignore_rules — per-BU suppression rules for the cloud inventory.

A live resource matching a rule is reclassified to `ignored` at ingest. Purely
additive. (The new `service_managed` IaC status is just a string value in the
existing cloud_assets.iac_status column — no schema change needed for it.)

Revision ID: 035_inventory_ignore_rules
Revises: 034_cloud_assets
Create Date: 2026-06-12
"""
from alembic import op
import sqlalchemy as sa

revision = "035_inventory_ignore_rules"
down_revision = "034_cloud_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_ignore_rules",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "business_unit_id",
            sa.String(),
            sa.ForeignKey("business_units.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("match_type", sa.String(length=20), nullable=False),
        sa.Column("pattern", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_inventory_ignore_rules_bu", "inventory_ignore_rules", ["business_unit_id"])


def downgrade() -> None:
    op.drop_index("ix_inventory_ignore_rules_bu", table_name="inventory_ignore_rules")
    op.drop_table("inventory_ignore_rules")
