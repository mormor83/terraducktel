"""workspaces.state_aws_account_id — decouple state-backend creds from
the resource-owning account.

`aws_account_id` always meant "the AWS account this workspace manages
resources in" — also used by the dashboard for tree grouping. For
non-AWS workspaces we now use sentinel `"global"` so they group
outside the AWS account tree.

But the executor was also using `aws_account_id` to look up which
AwsAccount row's per-account creds to inject as `AWS_ACCESS_KEY_ID`.
For a non-AWS workspace whose terraform state lives in an AWS S3
bucket (e.g. Cloudflare module with `backend "s3" {}` pointing at
home_dev's bucket), there's no single right answer:
  - aws_account_id="global"  → no creds, S3 backend gets a 403.
  - aws_account_id="222222222222" (home_dev) → creds work, but the
    workspace gets shoved under home_dev's tree group, hiding the
    fact that it's non-AWS.

This column decouples the two. Null = same as aws_account_id (no
change for existing workspaces). Non-null = use THIS account's creds
for the state backend, regardless of where the workspace sits in the
tree.

Revision ID: 023_ws_state_aws_account
Revises: 022_workspace_path_status
Create Date: 2026-05-24
"""
from alembic import op
import sqlalchemy as sa

revision = "023_ws_state_aws_account"
down_revision = "022_workspace_path_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("state_aws_account_id", sa.String(12), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "state_aws_account_id")
