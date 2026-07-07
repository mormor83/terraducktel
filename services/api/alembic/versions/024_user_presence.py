"""user_presence table for top-bar avatar stack.

One row per user; latest heartbeat overwrites in place. Tiny and hot —
gets read on every page load and written every 30s per active tab.

Revision ID: 024_user_presence
Revises: 023_ws_state_aws_account
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa

revision = "024_user_presence"
down_revision = "023_ws_state_aws_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_presence",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("bu_slug", sa.String(length=64), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "ix_user_presence_last_seen_at",
        "user_presence",
        ["last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_presence_last_seen_at", table_name="user_presence")
    op.drop_table("user_presence")
