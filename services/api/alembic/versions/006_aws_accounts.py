"""AWS accounts table

Revision ID: 006_aws_accounts
Revises: 005_drift
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa

revision = "006_aws_accounts"
down_revision = "005_drift"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "aws_accounts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state_bucket", sa.String(length=255), nullable=False),
        sa.Column("state_bucket_region", sa.String(length=50), nullable=False, server_default="us-east-1"),
        sa.Column("default_region", sa.String(length=50), nullable=False, server_default="us-east-1"),
        sa.Column("access_key_id_encrypted", sa.Text(), nullable=False),
        sa.Column("secret_access_key_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", name="uq_aws_accounts_account_id"),
    )


def downgrade() -> None:
    op.drop_table("aws_accounts")
