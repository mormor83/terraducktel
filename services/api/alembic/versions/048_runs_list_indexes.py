"""index runs for the paged Runs list

`GET /runs/page` orders by `created_at desc` with LIMIT/OFFSET and filters by
`workspace_id` / `status`; without these every page was a full scan + sort of
`runs`.

Revision ID: 048_runs_list_indexes
Revises: 047_proxmox_clusters
"""
from alembic import op

revision = "048_runs_list_indexes"
down_revision = "047_proxmox_clusters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_runs_created_at", "runs", ["created_at"])
    op.create_index("ix_runs_workspace_id_created_at", "runs", ["workspace_id", "created_at"])
    op.create_index("ix_runs_status", "runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_index("ix_runs_workspace_id_created_at", table_name="runs")
    op.drop_index("ix_runs_created_at", table_name="runs")
