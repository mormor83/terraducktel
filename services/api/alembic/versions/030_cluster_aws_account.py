"""k8s_clusters.aws_account_id — AWS creds for EKS `aws eks get-token` auth.

EKS kubeconfigs authenticate via an exec credential plugin that shells out to
`aws eks get-token`, which needs AWS credentials. Linking a cluster to an
onboarded AWS account lets the /test endpoint and helm runs export that
account's creds so the plugin can mint a token. Null for clusters whose
kubeconfig carries a static token/cert.

Revision ID: 030_cluster_aws_account
Revises: 029_k8s_clusters
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "030_cluster_aws_account"
down_revision = "029_k8s_clusters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "k8s_clusters",
        sa.Column("aws_account_id", sa.String(12), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("k8s_clusters", "aws_account_id")
