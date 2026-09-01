"""workspace key/value tags

Adds `workspaces.tags` as a JSON object, e.g. {"team": "payments"}.

A JSON column rather than a `workspace_tags` join table: at this fleet size
(low hundreds per BU) a dict scan gives what an index would, and it keeps tags
atomic with the row they describe — no partial write, no orphan cleanup when a
workspace is deleted, which is a class of bug this schema already had elsewhere.
If a deployment outgrows holding a BU's workspaces in memory, the upgrade path
is a real table behind the same API shape.

Nullable with no default: an untagged workspace is NULL, which reads the same
as {} everywhere through `workspace_tags.matches()`.

Revision ID: 043_workspace_tags
Revises: 042_api_key_rotation_overlap
"""
import sqlalchemy as sa
from alembic import op

revision = "043_workspace_tags"
down_revision = "042_api_key_rotation_overlap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("tags", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspaces", "tags")
