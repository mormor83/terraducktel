"""api_keys — scoped, long-lived credentials for automation.

Adds the `api_keys` table backing admin-minted API keys (see
app/models/api_key.py). A key authenticates as its owning user but carries a
narrower permission rider: a single BU, a capability tier (read|plan|apply),
and an optional workspace allowlist. Purely additive — no existing table or
auth behavior changes.

Revision ID: 032_api_keys
Revises: 031_global_vars_bu_scope
Create Date: 2026-06-04
"""
from alembic import op
import sqlalchemy as sa

revision = "032_api_keys"
down_revision = "031_global_vars_bu_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("token_prefix", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "business_unit_id",
            sa.String(),
            sa.ForeignKey("business_units.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "capability", sa.String(length=16), nullable=False, server_default="read"
        ),
        sa.Column("workspace_ids", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_api_keys_token_hash"),
    )
    op.create_index("ix_api_keys_bu", "api_keys", ["business_unit_id"])
    op.create_index("ix_api_keys_user", "api_keys", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_user", table_name="api_keys")
    op.drop_index("ix_api_keys_bu", table_name="api_keys")
    op.drop_table("api_keys")
