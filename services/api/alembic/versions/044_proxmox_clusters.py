"""proxmox_clusters table + workspaces.proxmox_cluster_id FK.

Proxmox VE as a first-class provider, mirroring gcp_projects (038) and the
workspace FK from 039. One revision because the FK is meaningless without
the table and vice versa.

Revision ID: 044_proxmox_clusters
Revises: 043_workspace_tags
Create Date: 2026-09-12
"""
from alembic import op
import sqlalchemy as sa

revision = "044_proxmox_clusters"
down_revision = "043_workspace_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proxmox_clusters",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("business_unit_id", sa.String(), nullable=False),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("api_token_id", sa.String(length=255), nullable=False),
        sa.Column("api_token_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("ssh_username", sa.String(length=64), nullable=True),
        sa.Column("ssh_private_key_encrypted", sa.Text(), nullable=True),
        sa.Column("tls_insecure", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ca_cert_pem", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_unit_id", "slug", name="uq_proxmox_clusters_bu_slug"),
    )
    op.add_column("workspaces", sa.Column("proxmox_cluster_id", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_workspaces_proxmox_cluster",
        "workspaces",
        "proxmox_clusters",
        ["proxmox_cluster_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_workspaces_proxmox_cluster", "workspaces", type_="foreignkey")
    op.drop_column("workspaces", "proxmox_cluster_id")
    op.drop_table("proxmox_clusters")
