"""workspaces.kind + workspaces.cluster_id — Helm/K8s support.

Adds the workspace `kind` discriminator ({"terraform","helm"}, default
"terraform") plus an optional `cluster_id` pointing at the target K8s cluster
for helm workspaces. `kind` uses a server_default so existing rows backfill to
"terraform" and the platform behaves identically until a helm workspace is
created.

Revision ID: 028_workspace_kind
Revises: 027_changelog_entries
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "028_workspace_kind"
down_revision = "027_changelog_entries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "kind",
            sa.String(20),
            nullable=False,
            server_default="terraform",
        ),
    )
    op.add_column(
        "workspaces",
        sa.Column("cluster_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "cluster_id")
    op.drop_column("workspaces", "kind")
