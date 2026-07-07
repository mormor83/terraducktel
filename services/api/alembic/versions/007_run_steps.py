"""Run steps for per-phase status and duration

Revision ID: 007_run_steps
Revises: 006_aws_accounts
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa

revision = "007_run_steps"
down_revision = "006_aws_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_steps",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "running", "success", "failed", "skipped",
                name="stepstatus",
                native_enum=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("summary_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_steps_run_id", "run_steps", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_run_steps_run_id", table_name="run_steps")
    op.drop_table("run_steps")
