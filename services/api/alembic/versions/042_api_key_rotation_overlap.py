"""api_key rotation with an overlap window

Adds the two columns a rotation needs to leave an audit trail:

  rotated_at        — when this key was superseded. Distinguishes an expiry set
                      by a rotation from one an admin chose, so the UI can say
                      "retiring in 6h" rather than just "expires".
  superseded_by_id  — the key that replaced it. ON DELETE SET NULL, because
                      deleting the successor must not erase the predecessor's
                      history.

No data migration: existing keys have never been rotated, so NULL is correct
for both. Nothing about the auth path changes — the overlap is enforced by the
existing `expires_at` check in api_key_service.is_active().

Revision ID: 042_api_key_rotation_overlap
Revises: 041_account_color
"""
import sqlalchemy as sa
from alembic import op

revision = "042_api_key_rotation_overlap"
down_revision = "041_account_color"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "api_keys",
        sa.Column("superseded_by_id", sa.String(), nullable=True),
    )
    op.create_foreign_key(
        "fk_api_keys_superseded_by_id",
        "api_keys",
        "api_keys",
        ["superseded_by_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_api_keys_superseded_by_id", "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "superseded_by_id")
    op.drop_column("api_keys", "rotated_at")
