"""Add workspaces, runs, and run_artifacts tables

Revision ID: 002
Revises: 001
Create Date: 2026-03-19

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("aws_account_id", sa.String(12), nullable=False),
        sa.Column("environment", sa.String(50), nullable=False),
        sa.Column("region", sa.String(50), nullable=False, server_default="us-east-1"),
        sa.Column("repo_url", sa.Text(), nullable=True),
        sa.Column("tf_working_dir", sa.String(500), nullable=False, server_default="."),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("triggered_by", sa.String(), nullable=True),
        sa.Column("command", sa.String(20), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "planning",
                "planned",
                "awaiting_approval",
                "applying",
                "applied",
                "failed",
                "cancelled",
                name="runstatus",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("plan_output", sa.Text(), nullable=True),
        sa.Column("error_output", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "run_artifacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("artifact_type", sa.String(50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("run_artifacts")
    op.drop_table("runs")
    op.drop_table("workspaces")
    op.execute("DROP TYPE IF EXISTS runstatus")
