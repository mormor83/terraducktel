"""workspaces.path_status + path_status_checked_at.

Marks each workspace as `ok` (path still exists at its tracked ref) or
`orphaned` (path is missing — repo folder was deleted or renamed and the
workspace can never be planned/applied/destroyed in the normal flow).
A background loop (added in this PR) refreshes the status periodically;
operators can also force a recheck via POST /v1/workspaces/{id}/sync.

`unknown` is the initial value for existing rows so the very first sync
cycle is required to label them — we don't want to claim "ok" for rows
we haven't verified yet.

Revision ID: 022_workspace_path_status
Revises: 021_run_auto_approve
Create Date: 2026-05-24
"""
from alembic import op
import sqlalchemy as sa

revision = "022_workspace_path_status"
down_revision = "021_run_auto_approve"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "path_status",
            sa.String(20),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "workspaces",
        sa.Column(
            "path_status_checked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Constrain values so a typo elsewhere can't leak through. CHECK (not
    # ENUM) so adding a future state ('moved', 'private', etc.) is one
    # migration, not a global rebuild.
    op.create_check_constraint(
        "workspaces_path_status_check",
        "workspaces",
        "path_status IN ('ok', 'orphaned', 'unknown')",
    )


def downgrade() -> None:
    op.drop_constraint("workspaces_path_status_check", "workspaces", type_="check")
    op.drop_column("workspaces", "path_status_checked_at")
    op.drop_column("workspaces", "path_status")
