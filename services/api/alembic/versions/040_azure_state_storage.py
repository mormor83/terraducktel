"""azure_subscriptions: Azure Blob state-backend fields.

Adds the storage account + container that hold Terraform state for
workspaces flagged `state_backend=azureblob`. Both nullable — a
provider-only subscription (state still in S3) leaves them unset. The
existing SP creds authenticate to Blob via AAD (grant the SP "Storage
Blob Data Contributor"); no new secret is stored.

Revision ID: 040_azure_state_storage
Revises: 039_cloud_workspace_columns
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa

revision = "040_azure_state_storage"
down_revision = "039_cloud_workspace_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "azure_subscriptions",
        sa.Column("state_storage_account", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "azure_subscriptions",
        sa.Column("state_container", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("azure_subscriptions", "state_container")
    op.drop_column("azure_subscriptions", "state_storage_account")
