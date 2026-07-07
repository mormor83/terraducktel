"""azure_subscriptions table — multi-cloud groundwork.

Mirrors the shape of aws_accounts (per-BU uniqueness, encrypted client
secret) so the codepaths stay parallel. Workspaces gain an optional FK
to this table in migration 026 — null means AWS-only.

Revision ID: 025_azure_subscriptions
Revises: 024_user_presence
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa

revision = "025_azure_subscriptions"
down_revision = "024_user_presence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "azure_subscriptions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("business_unit_id", sa.String(), nullable=False),
        sa.Column("subscription_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_location", sa.String(length=50), nullable=False, server_default="eastus"),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_unit_id",
            "subscription_id",
            name="uq_azure_subscriptions_bu_sub",
        ),
    )


def downgrade() -> None:
    op.drop_table("azure_subscriptions")
