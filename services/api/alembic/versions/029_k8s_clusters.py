"""k8s_clusters — Kubernetes clusters for Helm workspaces.

New table holding one row per onboarded K8s cluster: name (unique per BU),
optional API server URL + default namespace, and the kubeconfig encrypted at
rest (Fernet/HKDF over CREDENTIAL_ENCRYPTION_KEY, same scheme as aws_accounts).
Helm workspaces reference a cluster via workspaces.cluster_id (added in 028).

Revision ID: 029_k8s_clusters
Revises: 028_workspace_kind
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "029_k8s_clusters"
down_revision = "028_workspace_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "k8s_clusters",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("business_unit_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("server_url", sa.Text(), nullable=True),
        sa.Column(
            "default_namespace",
            sa.String(120),
            nullable=True,
            server_default="default",
        ),
        sa.Column("kubeconfig_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("business_unit_id", "name", name="uq_k8s_clusters_bu_name"),
    )


def downgrade() -> None:
    op.drop_table("k8s_clusters")
