"""workspaces.azure_subscription_id — optional FK to azure_subscriptions.

Null means the workspace is AWS-only (the existing behaviour). When set,
the executor exports ARM_* env vars from the linked subscription so
terraform's `azurerm` provider can authenticate. State backend is still
S3 (driven by aws_account_id / state_aws_account_id) — multi-cloud state
backends are not in scope for this migration.

Revision ID: 026_workspace_azure_subscription
Revises: 025_azure_subscriptions
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa

revision = "026_workspace_azure_subscription"
down_revision = "025_azure_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("azure_subscription_id", sa.String(), nullable=True),
    )
    op.create_foreign_key(
        "fk_workspaces_azure_subscription",
        "workspaces",
        "azure_subscriptions",
        ["azure_subscription_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_workspaces_azure_subscription", "workspaces", type_="foreignkey"
    )
    op.drop_column("workspaces", "azure_subscription_id")
