"""Branch tracking + per-workspace webhook toggle.

Adds:
  - workspaces.repo_ref         : tracked git ref/branch (default 'main').
  - workspaces.webhook_enabled  : per-workspace toggle for GitHub push triggers.
  - runs.branch                 : populated at trigger time from the workspace
                                  ref so the dashboard can show last-run-on-branch
                                  and approval policy can scope by branch.

Revision ID: 012_branch_webhook
Revises: 011_tfplan_blob
Create Date: 2026-05-04
"""
from alembic import op
import sqlalchemy as sa

revision = "012_branch_webhook"
down_revision = "011_tfplan_blob"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("repo_ref", sa.String(255), nullable=False, server_default="main"),
    )
    op.add_column(
        "workspaces",
        sa.Column(
            "webhook_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("runs", sa.Column("branch", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "branch")
    op.drop_column("workspaces", "webhook_enabled")
    op.drop_column("workspaces", "repo_ref")
