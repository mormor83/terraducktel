"""workspaces: gcp_project_id FK + state_backend discriminator.

Two related columns land together (one revision → one head):

  - `gcp_project_id` — optional FK to gcp_projects (mirror of
    azure_subscription_id in migration 026). Null = not a GCP workspace.
  - `state_backend` — which object store holds this workspace's Terraform
    state: "s3" (default), "azureblob", or "gcs". A `server_default="s3"`
    backfills every existing row so behaviour is unchanged for them.

Revision ID: 039_cloud_workspace_columns
Revises: 038_gcp_projects
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa

revision = "039_cloud_workspace_columns"
down_revision = "038_gcp_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("gcp_project_id", sa.String(), nullable=True),
    )
    op.create_foreign_key(
        "fk_workspaces_gcp_project",
        "workspaces",
        "gcp_projects",
        ["gcp_project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "workspaces",
        sa.Column(
            "state_backend",
            sa.String(length=20),
            nullable=False,
            server_default="s3",
        ),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "state_backend")
    op.drop_constraint("fk_workspaces_gcp_project", "workspaces", type_="foreignkey")
    op.drop_column("workspaces", "gcp_project_id")
