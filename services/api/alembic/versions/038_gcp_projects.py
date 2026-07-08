"""gcp_projects table — GCP as a first-class provider.

Mirrors the shape of aws_accounts / azure_subscriptions (per-BU uniqueness,
encrypted credential) so the codepaths stay parallel. Workspaces gain an
optional FK to this table in migration 039; when set, the executor exports
the linked project's service-account key for terraform's `google` provider,
and a workspace may store its state in this project's GCS bucket.

Revision ID: 038_gcp_projects
Revises: 037_audit_hmac_chain
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa

revision = "038_gcp_projects"
down_revision = "037_audit_hmac_chain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gcp_projects",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("business_unit_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("client_email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_region", sa.String(length=50), nullable=False, server_default="us-central1"),
        sa.Column("state_bucket", sa.String(length=255), nullable=True),
        sa.Column("state_prefix", sa.String(length=255), nullable=True),
        sa.Column("service_account_json_encrypted", sa.Text(), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_unit_id",
            "project_id",
            name="uq_gcp_projects_bu_project",
        ),
    )


def downgrade() -> None:
    op.drop_table("gcp_projects")
