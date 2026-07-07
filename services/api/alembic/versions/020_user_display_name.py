"""users.display_name — derived from OIDC name claim at login.

Adds an optional `display_name` column to the users table. OIDC sign-in
fills it from the `name` claim (falling back to given/family or
preferred_username if `name` is absent). Local users leave it NULL and
the UI prettifies the email local part as a fallback.

Revision ID: 020_user_display_name
Revises: 019_workspace_state_key
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa

revision = "020_user_display_name"
down_revision = "019_workspace_state_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("display_name", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "display_name")
