"""Init config and state lock tables

Revision ID: 001
Revises:
Create Date: 2026-03-19

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # config table
    op.create_table(
        "config",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(36), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )

    # config_history table
    op.create_table(
        "config_history",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.String(36), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # state_lock_entry table
    op.create_table(
        "state_lock_entry",
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lock_key", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id"),
    )


def downgrade() -> None:
    op.drop_table("state_lock_entry")
    op.drop_table("config_history")
    op.drop_table("config")
